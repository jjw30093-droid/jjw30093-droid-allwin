"""「每日精选」只读查询(权限投影在此层完成,后端是权限真源 CLAUDE.md §8.3)。

可见性(用户已确认):
- reco:daily(付费):published/settled/voided 单,slip_date 近 30 天窗口;
- reco:track_record(登录基线):**只含 settled/voided**(全部历史)——
  未结算的 published 单绝不出现在战绩面,否则等价于把付费赛前内容送给全体登录用户;
- draft 任何非 admin 面都不出现;
- 聚合口径:命中率 = win/(win+lose),push 不计分母;净单位 = Σ(return_units-1)
  只对 settled;作废单单列计数,不进任何分母也不消失。
"""

import sqlite3
from datetime import timedelta

from backend.db.util import utc_now

RECO_DAILY_WINDOW_DAYS = 30


def _legs_by_slip(conn, slip_ids: list[str]) -> dict[str, list[dict]]:
    if not slip_ids:
        return {}
    placeholders = ",".join("?" for _ in slip_ids)
    rows = conn.execute(
        f"SELECT * FROM reco_legs WHERE slip_id IN ({placeholders}) ORDER BY sort_order",
        slip_ids,
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["slip_id"], []).append({
            "id": r["id"],
            "match_id": r["match_id"],
            "match_desc": r["match_desc"],
            "market": r["market"],
            "selection": r["selection"],
            "odds": r["odds"],
            "result": r["result"],
        })
    return out


def _slip_dto(row: sqlite3.Row, legs: list[dict]) -> dict:
    return {
        "id": row["id"],
        "slip_date": row["slip_date"],
        "title": row["title"],
        "note": row["note"],
        "combo_type": row["combo_type"],
        "status": row["status"],
        "result": row["result"],
        "return_units": row["return_units"],
        "published_at": row["published_at"],
        "settled_at": row["settled_at"],
        "edit_count": row["edit_count"],
        "last_edited_at": row["updated_at"],
        "legs": legs,
    }


def daily_slips(conn: sqlite3.Connection) -> list[dict]:
    """付费面:近 30 天(按 slip_date)的非 draft 单,新日期在前。"""
    cutoff = (utc_now() - timedelta(days=RECO_DAILY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM reco_slips WHERE status != 'draft' AND slip_date >= ?"
        " ORDER BY slip_date DESC, published_at DESC",
        (cutoff,),
    ).fetchall()
    legs = _legs_by_slip(conn, [r["id"] for r in rows])
    return [_slip_dto(r, legs.get(r["id"], [])) for r in rows]


def track_record_slips(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> tuple[int, list[dict]]:
    """登录面:settled/voided 全历史归档(命中/未中/走水/作废全展示,不挑选不隐藏)。"""
    total = conn.execute(
        "SELECT COUNT(*) FROM reco_slips WHERE status IN ('settled','voided')"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM reco_slips WHERE status IN ('settled','voided')"
        " ORDER BY slip_date DESC, settled_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    legs = _legs_by_slip(conn, [r["id"] for r in rows])
    return total, [_slip_dto(r, legs.get(r["id"], [])) for r in rows]


def track_record_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END)                    AS settled,
             SUM(CASE WHEN status='settled' AND result='win'  THEN 1 ELSE 0 END)  AS wins,
             SUM(CASE WHEN status='settled' AND result='lose' THEN 1 ELSE 0 END)  AS loses,
             SUM(CASE WHEN status='settled' AND result='push' THEN 1 ELSE 0 END)  AS pushes,
             SUM(CASE WHEN status='voided' THEN 1 ELSE 0 END)                     AS voided,
             SUM(CASE WHEN status='settled' THEN return_units - 1 ELSE 0 END)     AS net_units
           FROM reco_slips""",
    ).fetchone()
    settled = row["settled"] or 0
    wins = row["wins"] or 0
    loses = row["loses"] or 0
    decided = wins + loses
    return {
        "settled_count": settled,
        "win_count": wins,
        "lose_count": loses,
        "push_count": row["pushes"] or 0,
        "voided_count": row["voided"] or 0,
        "hit_rate": round(wins / decided, 4) if decided else None,   # push 不计分母
        "net_units": round(row["net_units"] or 0.0, 4),
    }


def published_match_ids(conn: sqlite3.Connection) -> set[int]:
    """当前处于 published(赛前有效)状态的推荐单覆盖的比赛 id 集合。

    只暴露"存在性"(某场比赛有已发布推荐),不含方向/赔率/标题等任何内容
    ——2026-08-11 站长授权:推荐存在性属公开运营信息,可向匿名展示,
    用于比赛卡/详情页的"推荐已发布/待发布"状态。settled/voided 不算
    (那是历史,不是赛前状态);draft 永不外泄。
    """
    rows = conn.execute(
        """SELECT DISTINCT l.match_id
           FROM reco_legs l JOIN reco_slips s ON s.id = l.slip_id
           WHERE s.status = 'published' AND l.match_id IS NOT NULL"""
    ).fetchall()
    return {int(r[0]) for r in rows}


def public_overview(conn: sqlite3.Connection) -> dict:
    """匿名可见的聚合面(首页「今日精选/推荐战绩摘要」模块)。

    只下发计数与已结算聚合,绝不包含任何单据内容(标题/场次/方向/赔率),
    未结算 published 单只贡献"今天已发布 N 场"的计数与最近发布时间——
    这是运营层面主动公开的发布状态,不泄漏付费赛果。
    slip_date 是站长录入的自然日(北京时间语境),"今天"按 Asia/Shanghai 判定。
    """
    from zoneinfo import ZoneInfo

    today = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    today_row = conn.execute(
        """SELECT COUNT(*) AS n, MAX(published_at) AS latest
           FROM reco_slips WHERE slip_date=? AND status != 'draft'""",
        (today,),
    ).fetchone()

    cutoff = (utc_now() - timedelta(days=RECO_DAILY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END)                    AS settled,
             SUM(CASE WHEN status='settled' AND result='win'  THEN 1 ELSE 0 END)  AS wins,
             SUM(CASE WHEN status='settled' AND result='lose' THEN 1 ELSE 0 END)  AS loses,
             SUM(CASE WHEN status='settled' AND result='push' THEN 1 ELSE 0 END)  AS pushes,
             SUM(CASE WHEN status='voided' THEN 1 ELSE 0 END)                     AS voided,
             SUM(CASE WHEN status='settled' THEN return_units - 1 ELSE 0 END)     AS net_units
           FROM reco_slips WHERE slip_date >= ?""",
        (cutoff,),
    ).fetchone()
    settled = row["settled"] or 0
    wins = row["wins"] or 0
    loses = row["loses"] or 0
    decided = wins + loses
    return {
        "today_date": today,
        "today_published_count": today_row["n"] or 0,
        "today_latest_published_at": today_row["latest"],
        "window_days": RECO_DAILY_WINDOW_DAYS,
        "settled_count": settled,
        "win_count": wins,
        "lose_count": loses,
        "push_count": row["pushes"] or 0,
        "voided_count": row["voided"] or 0,
        "hit_rate": round(wins / decided, 4) if decided else None,
        "net_units": round(row["net_units"] or 0.0, 4),
        "published_match_ids": sorted(published_match_ids(conn)),
    }


def admin_slips(conn: sqlite3.Connection, limit: int = 100, offset: int = 0) -> tuple[int, list[dict]]:
    """admin 面:全状态含 draft。"""
    total = conn.execute("SELECT COUNT(*) FROM reco_slips").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM reco_slips ORDER BY slip_date DESC, created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    legs = _legs_by_slip(conn, [r["id"] for r in rows])
    return total, [_slip_dto(r, legs.get(r["id"], [])) for r in rows]


def admin_match_candidates(
    conn_core: sqlite3.Connection, *, query: str | None, limit: int
) -> list[dict]:
    """admin 录入每日精选用的比赛候选(替代自由文本描述,减少手打描述与真实
    比赛对不上的风险)。admin 不受 entitlement 门禁约束,全部联赛可见;只看
    未开赛比赛,按开球时间由近到远排序——与站内比赛卡片同一份数据源
    (backend.queries.matches.list_matches),不重新定义一套比赛列表逻辑。
    """
    from backend.queries.leagues import LEAGUE_META
    from backend.queries.matches import list_matches

    result = list_matches(
        conn_core, set(LEAGUE_META.keys()), status="upcoming", query=query, limit=limit,
    )
    out = []
    for m in result["matches"]:
        meta = LEAGUE_META.get(m["league_id"])
        out.append({
            "match_id": m["match_id"],
            "league_id": m["league_id"],
            "league_name": meta["name_zh"] if meta else str(m["league_id"]),
            "home_name": m["home"]["name"],
            "away_name": m["away"]["name"],
            "kickoff_at_utc": m["kickoff_at_utc"],
            "status": m["status"],
        })
    return out
