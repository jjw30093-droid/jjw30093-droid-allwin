"""预测登记簿命令层(CLAUDE.md §9:不可覆盖账本)。

状态机:draft → published → locked(is_official=1)→ [retracted]
        任何导入且无法证明赛前生成的历史 → legacy_unverified(永不 official)
锁定与官方化的实质字段由 DB 触发器再兜底一层(migration 0001)。

kickoff 口径:dim_match.Date 只有日期,kickoff_at_utc 取 <date>T00:00:00Z 的
保守下界——发布/锁定必须早于比赛日 00:00 UTC 才算赛前(宁严勿松,见
docs/prediction-integrity.md)。
"""

import json
import sqlite3

from backend.db.util import new_uuid, sha256_hex, utc_now_iso

from .audit import write_audit


class PredictionError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def get_or_create_model_version(
    conn: sqlite3.Connection,
    model_id: str,
    algorithm: str,
    description: str = "",
    params: dict | None = None,
    trained_at: str | None = None,
    train_range: str | None = None,
    metrics: dict | None = None,
) -> str:
    row = conn.execute("SELECT id FROM model_versions WHERE id=?", (model_id,)).fetchone()
    if row:
        return model_id
    conn.execute(
        "INSERT INTO model_versions (id, algorithm, description, params_json, trained_at, train_range, metrics_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (model_id, algorithm, description, json.dumps(params or {}, ensure_ascii=False),
         trained_at, train_range, json.dumps(metrics or {}, ensure_ascii=False), utc_now_iso()),
    )
    return model_id


def start_run(
    conn: sqlite3.Connection,
    model_version_id: str,
    triggered_by: str = "manual",
    input_cutoff_at: str | None = None,
    notes: str = "",
) -> str:
    run_id = new_uuid()
    now = utc_now_iso()
    conn.execute(
        "INSERT INTO prediction_runs (id, model_version_id, triggered_by, input_cutoff_at, started_at, status, notes, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
        (run_id, model_version_id, triggered_by, input_cutoff_at, now, notes, now),
    )
    return run_id


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, matches_count: int) -> None:
    conn.execute(
        "UPDATE prediction_runs SET finished_at=?, status=?, matches_count=? WHERE id=?",
        (utc_now_iso(), status, matches_count, run_id),
    )


def prediction_hash_of(match_id: int, model_version_id: str, probs: tuple, generated_at: str) -> str:
    canonical = json.dumps(
        {
            "match_id": match_id,
            "model_version_id": model_version_id,
            "home_win": round(probs[0], 6),
            "draw": round(probs[1], 6),
            "away_win": round(probs[2], 6),
            "generated_at": generated_at,
        },
        sort_keys=True,
    )
    return sha256_hex(canonical)


def register_snapshot(
    conn: sqlite3.Connection,
    *,
    match_id: int,
    kickoff_at_utc: str | None,
    model_version_id: str,
    home_win: float,
    draw: float,
    away_win: float,
    generated_at: str | None = None,
    run_id: str | None = None,
    input_cutoff_at: str | None = None,
    input_snapshot_hash: str | None = None,
    expected_home_goals: float | None = None,
    expected_away_goals: float | None = None,
    confidence: str | None = None,
    status: str = "draft",
    visibility: str = "public",
    is_official: int = 0,
) -> str:
    if abs(home_win + draw + away_win - 1.0) >= 0.001:
        raise PredictionError("bad_probabilities", "三项概率之和必须为 1(容差 0.001)")
    if status == "legacy_unverified" and is_official:
        raise PredictionError("bad_status", "legacy_unverified 不能标记 official")
    generated_at = generated_at or utc_now_iso()
    snap_id = new_uuid()
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, run_id, match_id, kickoff_at_utc, model_version_id, generated_at,
            input_cutoff_at, input_snapshot_hash, prediction_hash,
            home_win, draw, away_win, expected_home_goals, expected_away_goals,
            confidence, visibility, status, is_official, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snap_id, run_id, match_id, kickoff_at_utc, model_version_id, generated_at,
         input_cutoff_at, input_snapshot_hash,
         prediction_hash_of(match_id, model_version_id, (home_win, draw, away_win), generated_at),
         home_win, draw, away_win, expected_home_goals, expected_away_goals,
         confidence, visibility, status, is_official, utc_now_iso()),
    )
    return snap_id


def _get_snapshot(conn, snapshot_id: str):
    row = conn.execute("SELECT * FROM prediction_snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if row is None:
        raise PredictionError("not_found", "预测快照不存在")
    return row


def _require_pre_kickoff(row, action: str) -> None:
    now = utc_now_iso()
    if row["kickoff_at_utc"] is None:
        raise PredictionError("no_kickoff", f"缺少开球时间,无法{action}")
    if now >= row["kickoff_at_utc"]:
        raise PredictionError("post_kickoff", f"已过开球时间,禁止{action}(开球后生成/发布的预测不进入正式赛前统计)")


def publish_snapshot(conn: sqlite3.Connection, snapshot_id: str, actor: str | None) -> None:
    row = _get_snapshot(conn, snapshot_id)
    if row["status"] != "draft":
        raise PredictionError("bad_state", f"仅 draft 可发布,当前 {row['status']}")
    _require_pre_kickoff(row, "发布")
    conn.execute(
        "UPDATE prediction_snapshots SET status='published', published_at=? WHERE id=?",
        (utc_now_iso(), snapshot_id),
    )
    write_audit(conn, "prediction.publish", actor, target_type="prediction_snapshot", target_id=snapshot_id)


def lock_snapshot(conn: sqlite3.Connection, snapshot_id: str, actor: str | None) -> None:
    """锁定并官方化:此后 DB 触发器保证实质字段不可改、不可删。"""
    row = _get_snapshot(conn, snapshot_id)
    if row["status"] != "published":
        raise PredictionError("bad_state", f"仅 published 可锁定,当前 {row['status']}")
    _require_pre_kickoff(row, "锁定")
    conn.execute(
        "UPDATE prediction_snapshots SET status='locked', locked_at=?, is_official=1 WHERE id=?",
        (utc_now_iso(), snapshot_id),
    )
    write_audit(conn, "prediction.lock", actor, target_type="prediction_snapshot", target_id=snapshot_id)


def retract_snapshot(conn: sqlite3.Connection, snapshot_id: str, actor: str | None, reason: str) -> None:
    """撤回 = 状态标记,不物理删除;官方样本的撤回在 track record 透明展示。"""
    row = _get_snapshot(conn, snapshot_id)
    if row["status"] == "retracted":
        return
    conn.execute("UPDATE prediction_snapshots SET status='retracted' WHERE id=?", (snapshot_id,))
    write_audit(conn, "prediction.retract", actor,
                target_type="prediction_snapshot", target_id=snapshot_id, detail={"reason": reason})


def supersede_snapshot(conn: sqlite3.Connection, old_id: str, actor: str | None, **new_kwargs) -> str:
    """修正:追加新版本并链接旧版本(旧版本保留,superseded_by/status 不受锁定触发器限制)。"""
    old = _get_snapshot(conn, old_id)
    new_id = register_snapshot(
        conn,
        match_id=old["match_id"],
        kickoff_at_utc=old["kickoff_at_utc"],
        model_version_id=new_kwargs.pop("model_version_id", old["model_version_id"]),
        **new_kwargs,
    )
    conn.execute("UPDATE prediction_snapshots SET superseded_by=? WHERE id=?", (new_id, old_id))
    write_audit(conn, "prediction.supersede", actor,
                target_type="prediction_snapshot", target_id=old_id, detail={"new_id": new_id})
    return new_id


# ── 赛后结算 ───────────────────────────────────────────────

def settle_outcomes(conn_platform: sqlite3.Connection, conn_core: sqlite3.Connection) -> int:
    """为已完赛且有登记快照的比赛写入 prediction_outcomes(幂等)。"""
    match_ids = [
        r[0]
        for r in conn_platform.execute(
            """SELECT DISTINCT s.match_id FROM prediction_snapshots s
               LEFT JOIN prediction_outcomes o ON o.match_id = s.match_id
               WHERE o.match_id IS NULL"""
        )
    ]
    settled = 0
    now = utc_now_iso()
    for mid in match_ids:
        row = conn_core.execute(
            "SELECT home_score, away_score, Date, status FROM dim_match WHERE Match_ID=?", (mid,)
        ).fetchone()
        if row is None or row["status"] != "Finish":
            continue
        h, a = row["home_score"], row["away_score"]
        if h is None or a is None:
            continue
        outcome = "home" if h > a else ("away" if a > h else "draw")
        conn_platform.execute(
            "INSERT OR IGNORE INTO prediction_outcomes (match_id, home_goals, away_goals, outcome, finished_at, settled_at, source)"
            " VALUES (?, ?, ?, ?, ?, ?, 'fotmob')",
            (mid, h, a, outcome, f"{row['Date']}T00:00:00Z", now),
        )
        settled += 1
    return settled


# ── 每日 manifest ─────────────────────────────────────────

def build_daily_manifest(conn: sqlite3.Connection, manifest_date: str) -> dict:
    """当日(UTC,按 published_at 日期)全部 official 快照的稳定 hash 清单。

    幂等:内容不变则不新增;变化则 version+1 追加(不覆盖旧版)。
    """
    rows = conn.execute(
        """SELECT id, match_id, model_version_id, generated_at, published_at, locked_at,
                  input_snapshot_hash, prediction_hash, home_win, draw, away_win
           FROM prediction_snapshots
           WHERE is_official=1 AND published_at IS NOT NULL AND substr(published_at, 1, 10)=?
           ORDER BY id""",
        (manifest_date,),
    ).fetchall()
    entries = [dict(r) for r in rows]
    manifest_json = json.dumps(
        {"date": manifest_date, "entries": entries}, sort_keys=True, ensure_ascii=False
    )
    manifest_hash = sha256_hex(manifest_json)

    latest = conn.execute(
        "SELECT version, manifest_hash FROM prediction_manifests WHERE manifest_date=?"
        " ORDER BY version DESC LIMIT 1",
        (manifest_date,),
    ).fetchone()
    if latest and latest["manifest_hash"] == manifest_hash:
        return {"manifest_date": manifest_date, "version": latest["version"],
                "manifest_hash": manifest_hash, "changed": False, "entries": len(entries)}
    version = (latest["version"] + 1) if latest else 1
    conn.execute(
        "INSERT INTO prediction_manifests (id, manifest_date, version, manifest_json, manifest_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (new_uuid(), manifest_date, version, manifest_json, manifest_hash, utc_now_iso()),
    )
    return {"manifest_date": manifest_date, "version": version,
            "manifest_hash": manifest_hash, "changed": True, "entries": len(entries)}
