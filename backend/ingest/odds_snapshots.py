"""odds.db 快照落库(hash-diff:payload 不变不重复落库)+ source_health 记录。

时间戳纪律(CLAUDE.md §6.2):
- source_updated_at:NowGoal / FotMob 均不声明来源更新时间 → 恒 NULL,不得用抓取时间伪装;
- observed_at:本系统首次观察到该内容的 UTC 时间(由调用方传入,同一轮统一);
- ingested_at:写库时间(本模块内取 utc_now_iso());
- poll_run_id:同轮采集标识。

可被 CLI(backend/cli/poll_nowgoal.py)与 worker 复用;失败只追加 source_health
记录,绝不覆盖/删除最后成功落库的数据(source_health 本身也是 append-only)。
"""

import json
import sqlite3

from backend.db.connections import tx
from backend.db.util import sha256_hex, utc_now_iso
from backend.providers.nowgoal import canonical_payload_json


def _last_hash(conn: sqlite3.Connection, sql: str, params: tuple) -> str | None:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def ingest_odds_records(
    conn_odds: sqlite3.Connection,
    provider_match_id: str,
    records: list[dict],
    observed_at: str,
    poll_run_id: str | None,
    market_phase: str = "unknown",
) -> dict:
    """把 canonical 赔率记录写入 bronze_ng_odds_snap(每 (market, company) 一条序列)。

    对每条记录:取同序列最近一条 payload_hash,不变则跳过,变了才 INSERT。
    records 应已按 dim_match_xref.home_away_inverted 做过 normalize_for_inversion。
    返回 {"inserted": n, "skipped": m}。
    """
    inserted = skipped = 0
    with tx(conn_odds):
        for record in records:
            payload = {"initial": record.get("initial"), "latest": record.get("latest")}
            payload_json = canonical_payload_json(payload)
            payload_hash = sha256_hex(payload_json)
            last = _last_hash(
                conn_odds,
                """SELECT payload_hash FROM bronze_ng_odds_snap
                   WHERE provider_match_id=? AND market=? AND company_id=?
                   ORDER BY observed_at DESC, id DESC LIMIT 1""",
                (provider_match_id, record["market"], record["company_id"]),
            )
            if last == payload_hash:
                skipped += 1
                continue
            conn_odds.execute(
                """INSERT INTO bronze_ng_odds_snap
                   (provider_match_id, market, company_id, company_name, market_phase,
                    payload_json, payload_hash, source_updated_at, observed_at,
                    ingested_at, poll_run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    provider_match_id,
                    record["market"],
                    record["company_id"],
                    record.get("company_name", ""),
                    market_phase,
                    payload_json,
                    payload_hash,
                    observed_at,
                    utc_now_iso(),
                    poll_run_id,
                ),
            )
            inserted += 1
    return {"inserted": inserted, "skipped": skipped}


def ingest_lineup_snapshot(
    conn_odds: sqlite3.Connection,
    fotmob_match_id: int,
    snapshot: dict,
    observed_at: str,
    poll_run_id: str | None,
) -> dict:
    """阵容快照落库(序列键:fotmob_match_id),同 hash-diff 模式。"""
    payload_json = canonical_payload_json(snapshot)
    payload_hash = sha256_hex(payload_json)
    with tx(conn_odds):
        last = _last_hash(
            conn_odds,
            """SELECT payload_hash FROM bronze_fm_lineup_snap
               WHERE fotmob_match_id=? ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (fotmob_match_id,),
        )
        if last == payload_hash:
            return {"inserted": 0, "skipped": 1}
        conn_odds.execute(
            """INSERT INTO bronze_fm_lineup_snap
               (fotmob_match_id, payload_json, payload_hash, source_updated_at,
                observed_at, ingested_at, poll_run_id)
               VALUES (?, ?, ?, NULL, ?, ?, ?)""",
            (fotmob_match_id, payload_json, payload_hash, observed_at, utc_now_iso(), poll_run_id),
        )
    return {"inserted": 1, "skipped": 0}


def ingest_sideline_snapshot(
    conn_odds: sqlite3.Connection,
    fotmob_match_id: int,
    fotmob_team_id: int | None,
    snapshot: dict,
    observed_at: str,
    poll_run_id: str | None,
) -> dict:
    """伤停快照落库(序列键:fotmob_match_id + fotmob_team_id),同 hash-diff 模式。"""
    payload_json = canonical_payload_json(snapshot)
    payload_hash = sha256_hex(payload_json)
    with tx(conn_odds):
        last = _last_hash(
            conn_odds,
            """SELECT payload_hash FROM bronze_fm_sideline_snap
               WHERE fotmob_match_id=? AND fotmob_team_id IS ?
               ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (fotmob_match_id, fotmob_team_id),
        )
        if last == payload_hash:
            return {"inserted": 0, "skipped": 1}
        conn_odds.execute(
            """INSERT INTO bronze_fm_sideline_snap
               (fotmob_match_id, fotmob_team_id, payload_json, payload_hash,
                source_updated_at, observed_at, ingested_at, poll_run_id)
               VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
            (
                fotmob_match_id,
                fotmob_team_id,
                payload_json,
                payload_hash,
                observed_at,
                utc_now_iso(),
                poll_run_id,
            ),
        )
    return {"inserted": 1, "skipped": 0}


def record_source_health(
    conn_odds: sqlite3.Connection,
    source: str,
    ok: bool,
    latency_ms: int | None = None,
    error_summary: str | None = None,
    meta: dict | None = None,
) -> None:
    """append-only 源健康记录;失败只追加记录,不触碰已落库快照。"""
    with tx(conn_odds):
        conn_odds.execute(
            """INSERT INTO source_health (source, checked_at, ok, latency_ms, error_summary, meta_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                source,
                utc_now_iso(),
                1 if ok else 0,
                latency_ms,
                error_summary,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
