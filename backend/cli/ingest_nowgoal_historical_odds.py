"""把 NowGoal 历史赔率回填产物(JSONL,`runtime/research/` 下,不进 git)灌入
`data/odds.db`:`dim_match_xref` / `bronze_ng_odds_snap` / `silver_odds_moves`。

不写 `gold_move_cooccurrence`——这张表要求同一场比赛同时有 `silver_event_moves`
(阵容/伤停变化),而这批历史回填只抓了赔率(archive 端点没有历史阵容/伤停,
只有 2020-2023 这些旧赛季的比赛;阵容/伤停快照只对当前/未来比赛的 live 轮询
链路可用)。留空是诚实的,不是遗漏。

AH/OU 的 u/g/d 槽位语义(2026-08-06 用 6 场真实悬殊比分离线核对,覆盖主队
大胜和客队大胜两种方向,12 组公司观测全部一致,零反例):
    ah: u=主队赔率, g=盘口线(以客队角度报出——g<0 表示客队被让球 / 是热门,
        g>0 表示客队被受让 / 是冷门), d=客队赔率
    ou: u=大球赔率, g=总进球盘口线, d=小球赔率
    1x2: 抓取阶段已经落地成语义化字段(home_win/draw/away_win),这里直接透传。

核对证据(真实 titan_id / 真实比分 / 真实 g 符号):
    1904007 曼联 9-0 南安普顿      g=+1.25/+1.25  (客队大冷门: g>0 ✓)
    1894550 拜仁 8-0 沙尔克04      g=+2.5/+2.75   (客队大冷门: g>0 ✓)
    1915627 都灵 0-7 米兰         g=-0.75/-0.75  (客队是热门: g<0 ✓)
    1903924 水晶宫 0-7 利物浦      g=-0.75/-1     (客队是热门: g<0 ✓)
    2223107 维罗纳 0-6 国际米兰    g=-1/-0.75     (客队是热门: g<0 ✓)
    2213874 布莱顿 6-0 狼队       g=+1/+1        (客队冷门:   g>0 ✓)

用法:
    python -m backend.cli.ingest_nowgoal_historical_odds --dry-run
    python -m backend.cli.ingest_nowgoal_historical_odds --live

只处理 `phase == "PRE_MATCH"` 的快照(源数据里约 3% 是 IN_PLAY_EXCLUDED,
即赛中盘口,不属于赛前赔率时间线,直接跳过)。跳过 `_` 开头的目录(隔离区,
例如误写产物的隔离存档)。可重复运行:bronze 按
(provider_match_id, market, company_id, observed_at) 去重,silver 按
(to_snapshot_id, field) 唯一约束去重,xref 按 (provider, provider_match_id) upsert。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[2]
BACKFILL_ROOT = _REPO_ROOT / "runtime" / "research" / "nowgoal-top5-history-backfill-v1"
GAP_SHARD_DIR = _REPO_ROOT / "runtime" / "research" / "nowgoal-aws-gap-shards" / "a0ecdb219f764800a08b"
ODDS_DB_PATH = _REPO_ROOT / "data" / "odds.db"

# ah/ou 原始槽位 -> canonical 字段名(见模块 docstring 的核对证据)
_SLOT_MAP: dict[str, dict[str, str]] = {
    "ah": {"u": "home", "g": "line", "d": "away"},
    "ou": {"u": "over", "g": "line", "d": "under"},
}

_EVIDENCE_CONFIDENCE = {"id": 0.95, "name": 0.75}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_payload(row: dict[str, Any], home_away_inverted: bool) -> dict[str, Any] | None:
    """把 NowGoal 原始槽位转成"以 FotMob canonical home/away 为准"的字段。

    `home_away_inverted` 反映 NowGoal 自己的主客队分配是否与 FotMob canonical
    相反(dim_match_xref/resolution 阶段判定)。原始 u/g/d 槽位语义是相对
    NowGoal 自己的主客队(见模块 docstring 核对证据),不是相对 FotMob canonical——
    翻转时必须换边,规则与 backend/providers/nowgoal.py 里已有的实时轮询链路一致:
    1x2 交换 home/away;AH 交换双边并把盘口线取负(线原本是从 NowGoal 客队角度报出,
    翻转后 NowGoal 客队变成 canonical 主队,取负才能保持"线始终从 canonical 客队
    角度报出"这个不变量);OU 不换(总进球盘口和主客队身份无关,对称)。
    """
    market = row.get("market")
    values = row.get("values") or {}
    if market == "1x2":
        try:
            home_win = float(values["home_win"])
            away_win = float(values["away_win"])
            draw = float(values["draw"])
        except (KeyError, TypeError, ValueError):
            return None
        if home_away_inverted:
            home_win, away_win = away_win, home_win
        return {"home": home_win, "draw": draw, "away": away_win}
    if market in ("ah", "ou"):
        odds = values.get("odds") or {}
        slot_map = _SLOT_MAP[market]
        out: dict[str, Any] = {}
        for slot, field in slot_map.items():
            raw = odds.get(slot)
            if raw is None:
                return None
            try:
                out[field] = float(raw)
            except (TypeError, ValueError):
                return None
        if home_away_inverted and market == "ah":
            out["home"], out["away"] = out["away"], out["home"]
            out["line"] = -out["line"]
        return out
    return None


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def discover_shards() -> list[tuple[str, Path]]:
    shards = []
    if not BACKFILL_ROOT.exists():
        return shards
    for child in sorted(BACKFILL_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        progress_path = child / "progress.json"
        history_path = child / "normalized" / "odds-history.jsonl"
        if progress_path.exists() and history_path.exists():
            shards.append((child.name, child))
    return shards


def _load_completed_titan_ids(shard_dir: Path) -> set[str]:
    progress = json.loads((shard_dir / "progress.json").read_text())
    return {str(key).split(".")[-1] for key in progress.get("completed_match_keys", [])}


def _load_xref_rows(shard_name: str, completed_titan_ids: set[str]) -> list[dict[str, Any]]:
    shard_file = GAP_SHARD_DIR / f"{shard_name}.ready.jsonl"
    if not shard_file.exists():
        return []
    rows = []
    for line in shard_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        titan_id = str(row.get("titan_id"))
        if titan_id in completed_titan_ids and row.get("resolution_status") == "auto_ok":
            rows.append(row)
    return rows


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.backup-pre-nowgoal-historical-ingest-{stamp}")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return backup_path


def upsert_xref(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    now = _utc_now()
    count = 0
    for row in rows:
        confidence = _EVIDENCE_CONFIDENCE.get(row.get("resolution_evidence_kind"), 0.5)
        conn.execute(
            """
            INSERT INTO dim_match_xref
                (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                 confidence, verified, method, review_status, created_at, updated_at)
            VALUES (?, 'nowgoal', ?, ?, ?, 0, 'auto', 'auto_ok', ?, ?)
            ON CONFLICT(provider, provider_match_id) DO UPDATE SET
                fotmob_match_id=excluded.fotmob_match_id,
                home_away_inverted=excluded.home_away_inverted,
                confidence=excluded.confidence,
                review_status=excluded.review_status,
                updated_at=excluded.updated_at
            """,
            (
                int(row["match_id"]),
                str(row["titan_id"]),
                int(row.get("home_away_inverted") or 0),
                confidence,
                now,
                now,
            ),
        )
        count += 1
    return count


def _iter_pre_match_rows(history_path: Path) -> Iterable[dict[str, Any]]:
    with history_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("phase") == "PRE_MATCH":
                yield row


def ingest_bronze(
    conn: sqlite3.Connection,
    shard_name: str,
    history_path: Path,
    inverted_by_titan_id: dict[str, bool],
) -> tuple[int, int]:
    inserted = 0
    skipped_bad_payload = 0
    now = _utc_now()
    poll_run_id = f"historical-backfill-{shard_name}"
    for row in _iter_pre_match_rows(history_path):
        titan_id = str(row["titan_id"])
        payload = _canonical_payload(row, inverted_by_titan_id.get(titan_id, False))
        if payload is None:
            skipped_bad_payload += 1
            continue
        market = row["market"]
        company_id = str(row["company_id"])
        observed_at = row["observed_at"]
        existing = conn.execute(
            """
            SELECT 1 FROM bronze_ng_odds_snap
            WHERE provider_match_id=? AND market=? AND company_id=? AND observed_at=?
            """,
            (titan_id, market, company_id, observed_at),
        ).fetchone()
        if existing:
            continue
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO bronze_ng_odds_snap
                (provider_match_id, market, company_id, company_name, market_phase,
                 payload_json, payload_hash, source_updated_at, observed_at,
                 ingested_at, poll_run_id)
            VALUES (?, ?, ?, ?, 'pre_match', ?, ?, NULL, ?, ?, ?)
            """,
            (
                titan_id,
                market,
                company_id,
                row.get("company_name", ""),
                payload_json,
                _payload_hash(payload),
                observed_at,
                now,
                poll_run_id,
            ),
        )
        inserted += 1
    return inserted, skipped_bad_payload


def compute_silver_moves(conn: sqlite3.Connection, titan_ids: set[str]) -> int:
    if not titan_ids:
        return 0
    now = _utc_now()
    inserted = 0
    placeholders = ",".join("?" for _ in titan_ids)
    series_keys = conn.execute(
        f"""
        SELECT DISTINCT provider_match_id, market, company_id
        FROM bronze_ng_odds_snap
        WHERE provider_match_id IN ({placeholders})
        """,
        tuple(titan_ids),
    ).fetchall()
    for provider_match_id, market, company_id in series_keys:
        xref = conn.execute(
            "SELECT fotmob_match_id FROM dim_match_xref WHERE provider='nowgoal' AND provider_match_id=?",
            (provider_match_id,),
        ).fetchone()
        if xref is None:
            continue
        fotmob_match_id = xref[0]
        snaps = conn.execute(
            """
            SELECT id, payload_json, observed_at FROM bronze_ng_odds_snap
            WHERE provider_match_id=? AND market=? AND company_id=?
            ORDER BY observed_at ASC, id ASC
            """,
            (provider_match_id, market, company_id),
        ).fetchall()
        prev_id, prev_payload = None, None
        for snap_id, payload_json, observed_at in snaps:
            payload = json.loads(payload_json)
            if prev_payload is not None:
                for field, new_value in payload.items():
                    prev_value = prev_payload.get(field)
                    if prev_value != new_value:
                        cur = conn.execute(
                            """
                            INSERT OR IGNORE INTO silver_odds_moves
                                (fotmob_match_id, provider, company_id, market, field,
                                 prev_value, new_value, from_snapshot_id, to_snapshot_id,
                                 moved_at, created_at)
                            VALUES (?, 'nowgoal', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                fotmob_match_id,
                                company_id,
                                market,
                                field,
                                str(prev_value),
                                str(new_value),
                                prev_id,
                                snap_id,
                                observed_at,
                                now,
                            ),
                        )
                        inserted += cur.rowcount
            prev_id, prev_payload = snap_id, payload
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--skip-backup", action="store_true", help="仅供测试用临时库使用")
    parser.add_argument("--db-path", type=Path, default=ODDS_DB_PATH, help="仅供测试用临时库覆盖")
    args = parser.parse_args(argv)
    db_path = args.db_path

    shards = discover_shards()
    if not shards:
        print(json.dumps({"error": "no shards found", "root": str(BACKFILL_ROOT)}))
        return 1

    total_xref = total_bronze = total_bad_payload = total_silver = 0
    per_shard: list[dict[str, Any]] = []

    if args.dry_run:
        for shard_name, shard_dir in shards:
            completed = _load_completed_titan_ids(shard_dir)
            xref_rows = _load_xref_rows(shard_name, completed)
            history_count = sum(1 for _ in _iter_pre_match_rows(shard_dir / "normalized" / "odds-history.jsonl"))
            per_shard.append(
                {
                    "shard": shard_name,
                    "completed_matches": len(completed),
                    "xref_candidates": len(xref_rows),
                    "pre_match_snapshot_rows": history_count,
                }
            )
            total_xref += len(xref_rows)
            total_bronze += history_count
        print(
            json.dumps(
                {
                    "mode": "DRY_RUN",
                    "shard_count": len(shards),
                    "total_xref_candidates": total_xref,
                    "total_pre_match_snapshot_rows": total_bronze,
                    "shards": per_shard,
                },
                indent=2,
            )
        )
        return 0

    backup_path = None
    if not args.skip_backup:
        backup_path = backup_db(db_path)

    conn = _connect(db_path)
    try:
        for shard_name, shard_dir in shards:
            completed = _load_completed_titan_ids(shard_dir)
            xref_rows = _load_xref_rows(shard_name, completed)
            conn.execute("BEGIN")
            try:
                xref_n = upsert_xref(conn, xref_rows)
                inverted_by_titan_id = {
                    str(row["titan_id"]): bool(row.get("home_away_inverted"))
                    for row in xref_rows
                }
                bronze_n, bad_n = ingest_bronze(
                    conn, shard_name, shard_dir / "normalized" / "odds-history.jsonl", inverted_by_titan_id
                )
                silver_n = compute_silver_moves(conn, completed)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            total_xref += xref_n
            total_bronze += bronze_n
            total_bad_payload += bad_n
            total_silver += silver_n
            per_shard.append(
                {
                    "shard": shard_name,
                    "xref_upserted": xref_n,
                    "bronze_inserted": bronze_n,
                    "bad_payload_skipped": bad_n,
                    "silver_moves_inserted": silver_n,
                }
            )
            print(json.dumps({"event": "shard_done", **per_shard[-1]}), flush=True)
    finally:
        conn.close()

    summary = {
        "mode": "LIVE",
        "backup_path": str(backup_path) if backup_path else None,
        "shard_count": len(shards),
        "total_xref_upserted": total_xref,
        "total_bronze_inserted": total_bronze,
        "total_bad_payload_skipped": total_bad_payload,
        "total_silver_moves_inserted": total_silver,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
