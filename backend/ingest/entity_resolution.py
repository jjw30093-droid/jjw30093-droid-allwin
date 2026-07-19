"""实体解析:NowGoal 队名/比赛 → FotMob canonical ID(写 odds.db,core 只读)。

- seed_team_aliases:从 allwin.db(core,只读)的 dim_match 队名 + dim_team_i18n
  中英文名,种 dim_team_alias(canonical_team_id = FotMob Team_ID)。幂等。
- resolve_match:按别名对 NowGoal 日程行做主客队匹配(正/反两个方向),
  给 confidence 与 home_away_inverted,写 dim_match_xref:
  高置信(≥0.9 且方向唯一)→ review_status='auto_ok',否则 'needs_review';
  绝不静默 verified=1(verified 只能由管理员 confirm 置位)。
- dim_match 只有日期没有开球时间 → kickoff_diff_seconds 记 NULL,不编造。
"""

import sqlite3
from datetime import date as date_cls
from datetime import timedelta

from backend.db.connections import tx
from backend.db.util import utc_now_iso

PROVIDER = "nowgoal"
EPL_LEAGUE_ID = 47
AUTO_OK_THRESHOLD = 0.9


def _norm(name: str | None) -> str:
    """别名归一:小写 + 压缩空白(中文名不受影响)。"""
    if not name:
        return ""
    return " ".join(str(name).lower().split())


def seed_team_aliases(conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection) -> int:
    """从 core 种别名(INSERT OR IGNORE 幂等),返回本次新增条数。"""
    aliases: set[tuple[int, str, str]] = set()

    for home_col, away_col in (("Home_Team_ID", "Home_Team_Name"), ("Away_Team_ID", "Away_Team_Name")):
        for row in conn_core.execute(
            f"SELECT DISTINCT {home_col} AS tid, {away_col} AS name FROM dim_match"
            " WHERE League_ID=? AND tid IS NOT NULL AND name IS NOT NULL",
            (EPL_LEAGUE_ID,),
        ):
            alias = _norm(row["name"])
            if alias:
                aliases.add((int(row["tid"]), alias, "dim_match"))

    for row in conn_core.execute(
        "SELECT Team_ID, name_en, name_zh FROM dim_team_i18n WHERE Team_ID IS NOT NULL"
    ):
        for name in (row["name_en"], row["name_zh"]):
            alias = _norm(name)
            if alias:
                aliases.add((int(row["Team_ID"]), alias, "dim_team_i18n"))

    now = utc_now_iso()
    added = 0
    with tx(conn_odds):
        for team_id, alias, source in sorted(aliases):
            cur = conn_odds.execute(
                "INSERT OR IGNORE INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                " VALUES (?, ?, ?, ?)",
                (team_id, alias, source, now),
            )
            added += cur.rowcount
    return added


def _alias_team_ids(conn_odds: sqlite3.Connection, name: str) -> set[int]:
    alias = _norm(name)
    if not alias:
        return set()
    return {
        int(r[0])
        for r in conn_odds.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias=?", (alias,)
        )
    }


def _candidate_matches(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection, date_str: str
) -> list[sqlite3.Row]:
    """候选:core dim_match 同日期 ±1 天(NowGoal 时区偏移)且未被本 provider 映射。"""
    day = date_cls.fromisoformat(date_str)
    lo = (day - timedelta(days=1)).isoformat()
    hi = (day + timedelta(days=1)).isoformat()
    mapped = {
        int(r[0])
        for r in conn_odds.execute(
            "SELECT fotmob_match_id FROM dim_match_xref WHERE provider=?", (PROVIDER,)
        )
    }
    rows = conn_core.execute(
        """SELECT Match_ID, Date, Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name
           FROM dim_match WHERE Date BETWEEN ? AND ?""",
        (lo, hi),
    ).fetchall()
    return [r for r in rows if int(r["Match_ID"]) not in mapped]


def resolve_match(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection, schedule_row: dict
) -> dict:
    """解析一行 NowGoal 日程 → dim_match_xref。

    schedule_row 需含 titan_id / home_name / away_name / date(YYYY-MM-DD,查询日)。
    返回 {resolved, created, xref_id, fotmob_match_id, confidence,
          home_away_inverted, review_status}。无任何候选得分时不写 xref。
    """
    titan_id = str(schedule_row["titan_id"])

    existing = conn_odds.execute(
        "SELECT * FROM dim_match_xref WHERE provider=? AND provider_match_id=?",
        (PROVIDER, titan_id),
    ).fetchone()
    if existing is not None:
        return {
            "resolved": True,
            "created": False,
            "xref_id": existing["id"],
            "fotmob_match_id": existing["fotmob_match_id"],
            "confidence": existing["confidence"],
            "home_away_inverted": existing["home_away_inverted"],
            "review_status": existing["review_status"],
        }

    home_ids = _alias_team_ids(conn_odds, schedule_row["home_name"])
    away_ids = _alias_team_ids(conn_odds, schedule_row["away_name"])

    # (score, match_id, inverted) 全部得分组合;每边命中记 0.5
    scored: list[tuple[float, int, int]] = []
    for cand in _candidate_matches(conn_odds, conn_core, schedule_row["date"]):
        h, a = int(cand["Home_Team_ID"]), int(cand["Away_Team_ID"])
        fwd = 0.5 * (h in home_ids) + 0.5 * (a in away_ids)
        inv = 0.5 * (h in away_ids) + 0.5 * (a in home_ids)
        if fwd > 0:
            scored.append((fwd, int(cand["Match_ID"]), 0))
        if inv > 0:
            scored.append((inv, int(cand["Match_ID"]), 1))

    if not scored:
        return {
            "resolved": False,
            "created": False,
            "xref_id": None,
            "fotmob_match_id": None,
            "confidence": 0.0,
            "home_away_inverted": 0,
            "review_status": None,
        }

    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    best_score, best_match_id, best_inverted = scored[0]
    ties = [t for t in scored if t[0] == best_score]
    unambiguous = len(ties) == 1

    if best_score >= AUTO_OK_THRESHOLD and unambiguous:
        review_status = "auto_ok"
    else:
        review_status = "needs_review"

    now = utc_now_iso()
    with tx(conn_odds):
        # UNIQUE(provider, fotmob_match_id):该 FotMob 场次已被其他 titan_id 占用时降级人工
        occupied = conn_odds.execute(
            "SELECT id FROM dim_match_xref WHERE provider=? AND fotmob_match_id=?",
            (PROVIDER, best_match_id),
        ).fetchone()
        if occupied is not None:
            return {
                "resolved": False,
                "created": False,
                "xref_id": None,
                "fotmob_match_id": best_match_id,
                "confidence": best_score,
                "home_away_inverted": best_inverted,
                "review_status": "conflict_existing_xref",
            }
        cur = conn_odds.execute(
            """INSERT INTO dim_match_xref
               (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                confidence, verified, method, kickoff_diff_seconds, review_status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, 'auto', NULL, ?, ?, ?)""",
            (best_match_id, PROVIDER, titan_id, best_inverted, best_score, review_status, now, now),
        )
        xref_id = cur.lastrowid

    return {
        "resolved": True,
        "created": True,
        "xref_id": xref_id,
        "fotmob_match_id": best_match_id,
        "confidence": best_score,
        "home_away_inverted": best_inverted,
        "review_status": review_status,
    }
