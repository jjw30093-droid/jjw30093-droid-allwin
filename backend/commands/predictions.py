"""预测登记簿命令层(CLAUDE.md §9)。

状态机:draft → published → locked(is_official=1)→ [retracted]
        任何导入且无法证明赛前生成的历史 → legacy_unverified(永不 official)

公开样本口径不变量(CLAUDE.md §9.1):快照一旦 official+locked+赛前发布,永久属于
公开正式样本集合;retract 与 supersede 只是标注,不改变公开资格与评估分母。
supersede 只能追加新版本且只允许在开球前创建;历史已锁定样本不受新规追溯影响。

内容可编辑(migration 0007,用户明确要求):`edit_snapshot` 允许对任意状态、任意
时刻(含已锁定/已开球/已结算)的快照直接修正概率等字段——DB 层此前拦截 UPDATE 的
触发器已经移除,`edit_snapshot` 是唯一负责在编辑时写 `prediction_snapshot_edits`
留痕的地方,不能绕过它直接对表 UPDATE。物理删除依旧被 `trg_pred_snap_no_delete`
拦截,未受本次改动影响。

kickoff 口径(CLAUDE.md §6.2.1):正式预测的赛前判定必须使用精确 UTC 开球时刻。
kickoff_at_utc 缺失或只有日期(不含 'T' 时间部分)的快照禁止 publish/lock——
缺少精确开球时间的比赛不得生成新的正式赛前预测(见 docs/prediction-integrity.md)。
"""

import json
import sqlite3

from backend.db.util import (
    new_uuid,
    normalize_exact_kickoff,
    parse_strict_utc,
    sha256_hex,
    utc_now,
    utc_now_iso,
)
from backend.prediction_scope import official_sample_where

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
    applicable_league_ids: list[int] | None = None,
) -> str:
    row = conn.execute("SELECT id FROM model_versions WHERE id=?", (model_id,)).fetchone()
    if row:
        return model_id
    conn.execute(
        "INSERT INTO model_versions (id, algorithm, description, params_json, trained_at, train_range, metrics_json, applicable_league_ids, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (model_id, algorithm, description, json.dumps(params or {}, ensure_ascii=False),
         trained_at, train_range, json.dumps(metrics or {}, ensure_ascii=False),
         json.dumps(applicable_league_ids, ensure_ascii=False) if applicable_league_ids is not None else None,
         utc_now_iso()),
    )
    return model_id


def _require_model_applicable_to_league(
    conn: sqlite3.Connection, model_version_id: str, league_id: int | None, action: str
) -> None:
    """模型适用范围保护(CLAUDE.md §9;瑞典超接入新增,最小侵入)。

    仅当快照在 register 时显式冻结了 league_id 才触发校验——历史/测试调用方
    不传 league_id 时行为完全不变,不会因为这项新校验导致既有 publish/lock 回归。
    一旦提供 league_id,模型必须在 applicable_league_ids 显式声明包含该联赛,
    否则拒绝(含模型从未声明任何适用范围的情况,不隐式放行)。
    """
    if league_id is None:
        return
    row = conn.execute(
        "SELECT applicable_league_ids FROM model_versions WHERE id=?", (model_version_id,)
    ).fetchone()
    applicable = None
    if row is not None and _row_get(row, "applicable_league_ids"):
        try:
            applicable = set(json.loads(row["applicable_league_ids"]))
        except (ValueError, TypeError):
            applicable = None
    if not applicable or league_id not in applicable:
        raise PredictionError(
            "model_unvalidated_for_league",
            f"模型 {model_version_id} 未声明适用于联赛 {league_id},禁止{action}"
            "(缺少适用范围声明的模型不得成为该联赛的正式预测)",
        )


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
    kickoff_precision: str = "unknown",
    kickoff_source: str | None = None,
    league_id: int | None = None,
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
    """登记一条预测快照,冻结赛前判断所用的开球精度(kickoff_precision)与来源(kickoff_source)。

    provenance 在此冻结:此后 publish/lock 的精确性资格判断读取快照上冻结的值,
    不再依赖字符串形状。默认 unknown/None(保守:不可 publish/lock),调用方须显式
    传入可证明的 exact + source 才能进入正式赛前流程。

    league_id 同级冻结(默认 None = 跳过模型适用范围校验,保持历史调用方零回归)。
    显式传入后,publish/lock 会要求 model_version_id 在 applicable_league_ids
    中声明包含该联赛,否则拒绝(见 _require_model_applicable_to_league)。

    写入前统一规范化(唯一真源 normalize_exact_kickoff):
      - kickoff_precision != 'exact':kickoff_at_utc 强制写 NULL——date_only/unknown
        的新记录不得保留伪精确时间(即便调用方误传了一个形似精确的字符串);
      - kickoff_precision == 'exact' 且验证通过:写入规范化后的 UTC 'YYYY-MM-DDTHH:MM:SSZ'
        (即使传入的是 +08:00/-05:00 等带偏移格式,也统一存储为 Z),使后续 track record/
        evaluation/manifest 的时间比较不必依赖混合时区字符串字典序;
      - kickoff_precision == 'exact' 但验证不通过(非法时间/naive/缺来源):原样保留调用方
        传入的值,交由 publish/lock 的 `_require_precise_kickoff` 给出精确诊断原因
        (imprecise_kickoff / naive_kickoff / no_kickoff_source),不在此处静默吞掉错误。
    """
    if abs(home_win + draw + away_win - 1.0) >= 0.001:
        raise PredictionError("bad_probabilities", "三项概率之和必须为 1(容差 0.001)")
    if status == "legacy_unverified" and is_official:
        raise PredictionError("bad_status", "legacy_unverified 不能标记 official")
    if kickoff_precision not in ("exact", "date_only", "unknown"):
        raise PredictionError("bad_precision", f"非法 kickoff_precision:{kickoff_precision}")
    if kickoff_precision == "exact":
        normalized = normalize_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source)
        if normalized is not None:
            kickoff_at_utc = normalized
    else:
        kickoff_at_utc = None
    generated_at = generated_at or utc_now_iso()
    snap_id = new_uuid()
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, run_id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, league_id,
            model_version_id, generated_at,
            input_cutoff_at, input_snapshot_hash, prediction_hash,
            home_win, draw, away_win, expected_home_goals, expected_away_goals,
            confidence, visibility, status, is_official, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snap_id, run_id, match_id, kickoff_at_utc, kickoff_precision, kickoff_source, league_id,
         model_version_id, generated_at,
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


def _row_get(row, key, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _require_pre_kickoff(row, action: str) -> None:
    """当前时间必须严格早于精确开球时刻(用 tz-aware 解析比较,不做裸字符串比较)。"""
    parsed = parse_strict_utc(_row_get(row, "kickoff_at_utc"))
    if parsed is None:
        raise PredictionError("no_kickoff", f"缺少可解析的精确开球时间,无法{action}")
    if utc_now() >= parsed:
        raise PredictionError("post_kickoff", f"已过开球时间,禁止{action}(开球后生成/发布的预测不进入正式赛前统计)")


def _require_precise_kickoff(row, action: str) -> None:
    """CLAUDE.md §6.2.1:publish/lock 必须基于精确、可追溯来源的 UTC 开球时刻。

    资格由快照冻结的 provenance 决定(不再看字符串是否含 'T'):
      - kickoff_precision 必须为 exact;
      - kickoff_at_utc 必须能严格解析为 tz-aware UTC(拒绝 naive/非法/纯日期);
      - kickoff_source 必须非空(可追溯)。
    date_only / unknown / 缺来源 / 午夜占位 / naive datetime 一律拒绝。
    仅约束新的 publish/lock;历史已锁定样本由触发器保证不可改写。
    """
    from backend.db.util import _has_explicit_tz

    ko = _row_get(row, "kickoff_at_utc")
    precision = _row_get(row, "kickoff_precision")
    source = _row_get(row, "kickoff_source")
    if ko is None or parse_strict_utc(ko) is None:
        raise PredictionError(
            "imprecise_kickoff",
            f"缺少可严格解析的精确开球时刻(kickoff_at_utc={ko!r}),禁止{action}",
        )
    if isinstance(ko, str) and not _has_explicit_tz(ko):
        raise PredictionError(
            "naive_kickoff",
            f"开球时间缺少显式时区(naive datetime {ko!r}),禁止{action}",
        )
    if precision != "exact":
        raise PredictionError(
            "imprecise_kickoff",
            f"开球时间精度为 {precision!r}(非 exact),禁止{action}"
            "(缺少精确开球时间的比赛不得生成正式赛前预测)",
        )
    if not source or not str(source).strip():
        raise PredictionError(
            "no_kickoff_source",
            f"精确开球时间缺少可追溯来源(kickoff_source),禁止{action}",
        )
    # 汇总兜底:综合判定一致(唯一真源 normalize_exact_kickoff)
    if normalize_exact_kickoff(ko, precision, source) is None:
        raise PredictionError("imprecise_kickoff", f"开球时间来源不满足精确资格,禁止{action}")


def publish_snapshot(conn: sqlite3.Connection, snapshot_id: str, actor: str | None) -> None:
    row = _get_snapshot(conn, snapshot_id)
    if row["status"] != "draft":
        raise PredictionError("bad_state", f"仅 draft 可发布,当前 {row['status']}")
    _require_precise_kickoff(row, "发布")
    _require_pre_kickoff(row, "发布")
    _require_model_applicable_to_league(conn, row["model_version_id"], _row_get(row, "league_id"), "发布")
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
    _require_precise_kickoff(row, "锁定")
    _require_pre_kickoff(row, "锁定")
    _require_model_applicable_to_league(conn, row["model_version_id"], _row_get(row, "league_id"), "锁定")
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
    """修正:追加新版本并链接旧版本(旧版本保留,superseded_by/status 不受锁定触发器限制)。

    只允许开球前创建修正版(CLAUDE.md §9.1):开球后禁止 supersede,防止赛后用
    "修正"改写公开账本。被取代的旧版本永不退出公开正式样本集合与评估分母。
    """
    old = _get_snapshot(conn, old_id)
    _require_pre_kickoff(old, "创建修正版(supersede)")
    # 冻结的 provenance 一并继承到新版本:supersede 不能借"修正"洗白精度/来源/联赛范围,
    # 新版本要成为 official 仍须在 publish/lock 通过同一套精确性与模型适用范围门禁。
    new_id = register_snapshot(
        conn,
        match_id=old["match_id"],
        kickoff_at_utc=old["kickoff_at_utc"],
        kickoff_precision=_row_get(old, "kickoff_precision", "unknown"),
        kickoff_source=_row_get(old, "kickoff_source"),
        league_id=_row_get(old, "league_id"),
        model_version_id=new_kwargs.pop("model_version_id", old["model_version_id"]),
        **new_kwargs,
    )
    conn.execute("UPDATE prediction_snapshots SET superseded_by=? WHERE id=?", (new_id, old_id))
    write_audit(conn, "prediction.supersede", actor,
                target_type="prediction_snapshot", target_id=old_id, detail={"new_id": new_id})
    return new_id


def edit_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: str,
    actor: str | None,
    *,
    reason: str,
    home_win: float | None = None,
    draw: float | None = None,
    away_win: float | None = None,
    expected_home_goals: float | None = None,
    expected_away_goals: float | None = None,
    confidence: str | None = None,
) -> dict:
    """直接修正一条已存在的快照——任意状态、任意时刻(含已锁定/已开球/已结算)。

    用户明确要求"随时可以更改":本函数不做 draft/published/locked 状态门禁,也不
    做开球前/后门禁(那些是 publish_snapshot/lock_snapshot/supersede_snapshot 各自
    的门禁,服务于"新建/发布/锁定"这几个不同的动作,与"修正已存在的内容"是两件事)。

    只更新调用方显式传入(非 None)的字段。若三项概率里任一被传入,会与快照当前
    (未被传入的)另外两项合并后重新校验"和为 1(容差 0.001)"——不允许改出一个
    不合法的概率分布。

    每次真正产生变化的编辑都会在 `prediction_snapshot_edits`(append-only,见
    migration 0007)追加一行,记录改动前/后的值、操作者、原因、时间;`reason` 必须
    非空。若调用方传入的字段值与当前值完全相同(no-op),仍要求提供非空 reason,
    但不写历史行、不递增 edit_count——没有实质变化就不该产生一条"改了什么"的记录。

    返回 {"edit_id": str|None, "changed_fields": [...], "edit_count": int}。
    """
    if not reason or not str(reason).strip():
        raise PredictionError("bad_reason", "修正原因不能为空")
    row = _get_snapshot(conn, snapshot_id)

    candidates = {
        "home_win": home_win, "draw": draw, "away_win": away_win,
        "expected_home_goals": expected_home_goals, "expected_away_goals": expected_away_goals,
        "confidence": confidence,
    }
    changed = {
        k: v for k, v in candidates.items()
        if v is not None and v != _row_get(row, k)
    }
    current_edit_count = _row_get(row, "edit_count", 0) or 0
    if not changed:
        return {"edit_id": None, "changed_fields": [], "edit_count": current_edit_count}

    if any(k in changed for k in ("home_win", "draw", "away_win")):
        merged_home = changed.get("home_win", row["home_win"])
        merged_draw = changed.get("draw", row["draw"])
        merged_away = changed.get("away_win", row["away_win"])
        if abs(merged_home + merged_draw + merged_away - 1.0) >= 0.001:
            raise PredictionError("bad_probabilities", "三项概率之和必须为 1(容差 0.001)")

    before = {k: _row_get(row, k) for k in changed}
    edit_id = new_uuid()
    now = utc_now_iso()

    conn.execute(
        """INSERT INTO prediction_snapshot_edits
           (id, snapshot_id, actor_user_id, reason, before_json, after_json, edited_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (edit_id, snapshot_id, actor, reason,
         json.dumps(before, ensure_ascii=False, sort_keys=True),
         json.dumps(changed, ensure_ascii=False, sort_keys=True), now),
    )

    new_hash = prediction_hash_of(
        row["match_id"], row["model_version_id"],
        (
            changed.get("home_win", row["home_win"]),
            changed.get("draw", row["draw"]),
            changed.get("away_win", row["away_win"]),
        ),
        row["generated_at"],
    )
    set_clauses = ", ".join(f"{k}=?" for k in changed)
    conn.execute(
        f"UPDATE prediction_snapshots SET {set_clauses}, prediction_hash=?, "
        f"last_edited_at=?, edit_count=edit_count+1 WHERE id=?",
        (*changed.values(), new_hash, now, snapshot_id),
    )
    write_audit(
        conn, "prediction.edit", actor,
        target_type="prediction_snapshot", target_id=snapshot_id,
        detail={"reason": reason, "before": before, "after": changed},
    )

    return {
        "edit_id": edit_id,
        "changed_fields": sorted(changed.keys()),
        "edit_count": current_edit_count + 1,
    }


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
    """当日(UTC,按 published_at 日期)全部**正式合格**快照的稳定 hash 清单。

    资格判据 = backend.prediction_scope.official_sample_where(与 track record /
    evaluation 共用唯一真源):official ∧ 曾锁定 ∧ 有精确开球 ∧ 发布严格早于开球
    (julianday 比较,混合时区偏移同样正确)。历史上 manifest 只查 is_official,
    会把开球后发布 / 未锁定的 official 也纳入,导致与 track record 集合不一致——已收口。

    覆盖修正链全部正式版本:被取代(superseded_by 非空)与已撤回的正式快照
    同样入清单,不只最新版;entries 只含锁定即不可变的实质字段,因此赛后
    retract/supersede 不会改变已有 manifest 内容或 hash。
    幂等:内容不变则不新增;变化则 version+1 追加(不覆盖旧版)。
    """
    rows = conn.execute(
        f"""SELECT id, match_id, model_version_id, generated_at, published_at, locked_at,
                   input_snapshot_hash, prediction_hash, home_win, draw, away_win
            FROM prediction_snapshots
            WHERE {official_sample_where("")}
              AND substr(published_at, 1, 10)=?
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
