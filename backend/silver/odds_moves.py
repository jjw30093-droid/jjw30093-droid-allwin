"""odds.db Silver/Gold 派生:变化点(moves)与时间共现(co-occurrence)。

- build_odds_moves:同序列相邻两条 bronze_ng_odds_snap 的 latest 组做字段级 diff;
- build_event_moves:阵容/伤停相邻快照 hash 变化 → silver_event_moves(带变化摘要);
- build_cooccurrence:同场比赛 |odds_move.moved_at - event_move.moved_at| ≤ 窗口
  → gold_move_cooccurrence。只表达时间共现,不声称因果(CLAUDE.md §6.4),
  命名与文案不得用因果词。

全部幂等:靠 UNIQUE 约束 + INSERT OR IGNORE,重跑不重复。
"""

import json
import sqlite3
from datetime import datetime, timezone

from backend.db.connections import tx
from backend.db.util import normalize_exact_kickoff, utc_now_iso
from backend.queries.odds import normalize_odds_payload

# 已确认(auto_ok/confirmed)映射才进 Silver;needs_review/rejected 不产出派生
_ACTIVE_XREF_STATUSES = ("auto_ok", "confirmed")


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _latest_fields(payload_json: str) -> dict:
    """取一条快照的可比较字段组(扁平)。

    bronze_ng_odds_snap 真实存在两种 payload 形状(嵌套=实时轮询,
    扁平=历史回填 CLI,后者 73 万行)。旧实现只认嵌套,对扁平恒返回 {},
    导致历史回填数据在 canonical Silver 重建下产出 0 个变化点——
    统一走 normalize_odds_payload 读侧归一(审计 B2)。
    """
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    fields = normalize_odds_payload(payload)
    return fields if isinstance(fields, dict) else {}


def build_odds_moves(conn_odds: sqlite3.Connection) -> int:
    """对每序列(provider_match_id, market, company_id)按 observed_at 相邻两条
    diff latest 组的字段级变化(1x2: home/draw/away,ah: home/line/away,
    ou: over/line/under)→ silver_odds_moves。返回新增行数。"""
    xref = {
        row["provider_match_id"]: int(row["fotmob_match_id"])
        for row in conn_odds.execute(
            "SELECT provider_match_id, fotmob_match_id FROM dim_match_xref"
            " WHERE provider='nowgoal' AND review_status IN (?, ?)",
            _ACTIVE_XREF_STATUSES,
        )
    }
    snaps = conn_odds.execute(
        """SELECT id, provider_match_id, market, company_id, payload_json, observed_at
           FROM bronze_ng_odds_snap
           ORDER BY provider_match_id, market, company_id, observed_at, id"""
    ).fetchall()

    now = utc_now_iso()
    inserted = 0
    with tx(conn_odds):
        prev_by_series: dict[tuple, sqlite3.Row] = {}
        for snap in snaps:
            series = (snap["provider_match_id"], snap["market"], snap["company_id"])
            prev = prev_by_series.get(series)
            prev_by_series[series] = snap
            if prev is None:
                continue
            fotmob_match_id = xref.get(snap["provider_match_id"])
            if fotmob_match_id is None:
                continue
            prev_fields = _latest_fields(prev["payload_json"])
            new_fields = _latest_fields(snap["payload_json"])
            for field in sorted(set(prev_fields) | set(new_fields)):
                old_v, new_v = prev_fields.get(field), new_fields.get(field)
                if old_v == new_v:
                    continue
                cur = conn_odds.execute(
                    """INSERT OR IGNORE INTO silver_odds_moves
                       (fotmob_match_id, provider, company_id, market, field,
                        prev_value, new_value, from_snapshot_id, to_snapshot_id,
                        moved_at, created_at)
                       VALUES (?, 'nowgoal', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        fotmob_match_id,
                        snap["company_id"],
                        snap["market"],
                        field,
                        None if old_v is None else str(old_v),
                        None if new_v is None else str(new_v),
                        prev["id"],
                        snap["id"],
                        snap["observed_at"],
                        now,
                    ),
                )
                inserted += cur.rowcount
    return inserted


def _lineup_change_summary(prev_payload: str, new_payload: str) -> dict:
    """阵容变化摘要:两侧阵型变化 + 首发进出名单(只描述差异,不解释原因)。"""
    try:
        prev, new = json.loads(prev_payload), json.loads(new_payload)
    except json.JSONDecodeError:
        return {"note": "payload 解析失败,仅记录 hash 变化"}
    summary: dict = {}
    for side in ("home", "away"):
        p_side, n_side = prev.get(side) or {}, new.get(side) or {}
        side_sum = {}
        if p_side.get("formation") != n_side.get("formation"):
            side_sum["formation"] = {
                "prev": p_side.get("formation"),
                "new": n_side.get("formation"),
            }
        p_starters = {str(x.get("id")): x.get("name") for x in p_side.get("starters") or []}
        n_starters = {str(x.get("id")): x.get("name") for x in n_side.get("starters") or []}
        added = sorted(set(n_starters) - set(p_starters))
        removed = sorted(set(p_starters) - set(n_starters))
        if added:
            side_sum["starters_added"] = [{"id": i, "name": n_starters[i]} for i in added]
        if removed:
            side_sum["starters_removed"] = [{"id": i, "name": p_starters[i]} for i in removed]
        if side_sum:
            summary[side] = side_sum
    return summary


def _sideline_change_summary(prev_payload: str, new_payload: str) -> dict:
    try:
        prev, new = json.loads(prev_payload), json.loads(new_payload)
    except json.JSONDecodeError:
        return {"note": "payload 解析失败,仅记录 hash 变化"}
    p_list = {str(x.get("id")): x.get("name") for x in prev.get("sidelined") or []}
    n_list = {str(x.get("id")): x.get("name") for x in new.get("sidelined") or []}
    added = sorted(set(n_list) - set(p_list))
    removed = sorted(set(p_list) - set(n_list))
    summary: dict = {"team_id": new.get("team_id")}
    if added:
        summary["sidelined_added"] = [{"id": i, "name": n_list[i]} for i in added]
    if removed:
        summary["sidelined_removed"] = [{"id": i, "name": p_list[i]} for i in removed]
    return summary


def _build_event_moves_for(
    conn_odds: sqlite3.Connection,
    rows: list[sqlite3.Row],
    series_key,
    event_type: str,
    summarize,
    now: str,
) -> int:
    inserted = 0
    prev_by_series: dict[tuple, sqlite3.Row] = {}
    for snap in rows:
        series = series_key(snap)
        prev = prev_by_series.get(series)
        prev_by_series[series] = snap
        if prev is None:
            continue
        if prev["payload_hash"] == snap["payload_hash"]:
            continue
        detail = summarize(prev["payload_json"], snap["payload_json"])
        cur = conn_odds.execute(
            """INSERT OR IGNORE INTO silver_event_moves
               (fotmob_match_id, event_type, detail_json, from_snapshot_id,
                to_snapshot_id, moved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snap["fotmob_match_id"],
                event_type,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                prev["id"],
                snap["id"],
                snap["observed_at"],
                now,
            ),
        )
        inserted += cur.rowcount
    return inserted


def build_event_moves(conn_odds: sqlite3.Connection) -> int:
    """阵容/伤停相邻快照 hash 变化 → silver_event_moves。返回新增行数。"""
    now = utc_now_iso()
    lineup_rows = conn_odds.execute(
        """SELECT id, fotmob_match_id, payload_json, payload_hash, observed_at
           FROM bronze_fm_lineup_snap ORDER BY fotmob_match_id, observed_at, id"""
    ).fetchall()
    sideline_rows = conn_odds.execute(
        """SELECT id, fotmob_match_id, fotmob_team_id, payload_json, payload_hash, observed_at
           FROM bronze_fm_sideline_snap ORDER BY fotmob_match_id, fotmob_team_id, observed_at, id"""
    ).fetchall()
    inserted = 0
    with tx(conn_odds):
        inserted += _build_event_moves_for(
            conn_odds,
            lineup_rows,
            lambda s: (s["fotmob_match_id"],),
            "lineup_change",
            _lineup_change_summary,
            now,
        )
        inserted += _build_event_moves_for(
            conn_odds,
            sideline_rows,
            lambda s: (s["fotmob_match_id"], s["fotmob_team_id"]),
            "sideline_change",
            _sideline_change_summary,
            now,
        )
    return inserted


def final_pre_match_snapshot(
    conn_odds: sqlite3.Connection,
    provider_match_id: str,
    market: str,
    company_id: str,
    kickoff_at_utc: str | None,
    kickoff_precision: str | None = None,
    kickoff_source: str | None = None,
) -> sqlite3.Row | None:
    """FINAL:精确 kickoff 前最后一条 market_phase='pre_match' 的有效快照。

    唯一真源 normalize_exact_kickoff——kickoff 缺失、precision 非 exact、缺来源、
    naive/非法时间一律不满足,返回 None,不得声称某条快照是收盘快照(CLAUDE.md §6.2.1)。
    明确排除 unknown 与 in_play 阶段的快照(CLAUDE.md §6.3)。
    """
    normalized = normalize_exact_kickoff(kickoff_at_utc, kickoff_precision, kickoff_source)
    if normalized is None:
        return None
    return conn_odds.execute(
        """SELECT * FROM bronze_ng_odds_snap
           WHERE provider_match_id=? AND market=? AND company_id=?
             AND market_phase='pre_match' AND observed_at < ?
           ORDER BY observed_at DESC, id DESC LIMIT 1""",
        (provider_match_id, market, company_id, normalized),
    ).fetchone()


def build_cooccurrence(conn_odds: sqlite3.Connection, window_seconds: int = 900) -> int:
    """同场比赛内 |odds_move.moved_at - event_move.moved_at| ≤ window_seconds 的
    (odds_move, event_move) 对 → gold_move_cooccurrence。只表达时间共现。返回新增行数。"""
    odds_moves = conn_odds.execute(
        "SELECT id, fotmob_match_id, moved_at FROM silver_odds_moves ORDER BY fotmob_match_id, id"
    ).fetchall()
    event_moves_by_match: dict[int, list[sqlite3.Row]] = {}
    for row in conn_odds.execute(
        "SELECT id, fotmob_match_id, moved_at FROM silver_event_moves ORDER BY fotmob_match_id, id"
    ):
        event_moves_by_match.setdefault(int(row["fotmob_match_id"]), []).append(row)

    now = utc_now_iso()
    inserted = 0
    with tx(conn_odds):
        for om in odds_moves:
            om_ts = _parse_ts(om["moved_at"])
            for em in event_moves_by_match.get(int(om["fotmob_match_id"]), []):
                delta = int((om_ts - _parse_ts(em["moved_at"])).total_seconds())
                if abs(delta) > window_seconds:
                    continue
                cur = conn_odds.execute(
                    """INSERT OR IGNORE INTO gold_move_cooccurrence
                       (fotmob_match_id, odds_move_id, event_move_id, window_seconds,
                        delta_seconds, computed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (om["fotmob_match_id"], om["id"], em["id"], window_seconds, delta, now),
                )
                inserted += cur.rowcount
    return inserted
