"""odds.db Silver 派生:FINAL(精确 kickoff 前最后一条有效赛前快照)判定。

2026-09-13:本文件原本还有 build_odds_moves/build_event_moves/
build_cooccurrence 三个函数,构建"时间共现"(「关键变化」)所需的
silver_odds_moves/silver_event_moves/gold_move_cooccurrence 三张表——
该功能已整体下架(站长明确决定,产品设计里不应该有这条),三个函数、
它们专属的助手函数、以及这三张表本身(backend/migrations/odds/
0012_drop_cooccurrence_tables.sql)一并移除。final_pre_match_snapshot()
不属于那个功能,读的是 bronze_ng_odds_snap 而非上述三张表,原样保留。
"""

import sqlite3

from backend.db.util import normalize_exact_kickoff


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
