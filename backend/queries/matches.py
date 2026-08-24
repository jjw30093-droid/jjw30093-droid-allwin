"""allwin.db(core)只读查询:比赛列表/详情/近况/排名/赛程 + 中文名 JOIN。"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.media.team_crests import resolve_team_crest_url
from backend.queries.teams import (
    display_name_for_team,
    provider_name_for_team,
    team_display_for,
    team_display_map,
)


def team_i18n_map(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        team_id: row["name_zh"]
        for team_id, row in team_display_map(conn).items()
        if row.get("name_zh")
    }


def _team_ref(team_id, name_en, display) -> dict:
    return {
        "team_id": team_id,
        "name": display_name_for_team(
            team_id, provider_name=name_en, display=display
        ),
        "name_en": provider_name_for_team(
            team_id, provider_name=name_en, display=display
        ),
        "crest_url": resolve_team_crest_url("fotmob", team_id),
    }


def _row_to_summary(r, display) -> dict:
    return {
        "match_id": r["Match_ID"],
        "league_id": r["League_ID"],
        "season": r["Season"],
        "date_utc": r["Date"],
        # 精确开球时刻可空(§6.2.1);旧测试布景可能没有该列,容错取 None
        "kickoff_at_utc": r["kickoff_at_utc"] if "kickoff_at_utc" in r.keys() else None,
        "round": str(r["Match_Round"]) if r["Match_Round"] is not None else None,
        "status": r["status"],
        "home": _team_ref(r["Home_Team_ID"], r["Home_Team_Name"], display),
        "away": _team_ref(r["Away_Team_ID"], r["Away_Team_Name"], display),
        "home_score": r["home_score"],
        "away_score": r["away_score"],
}


def _int_or_none(value) -> int | None:
    """dim_match.Temperature/Wind_Speed 是 TEXT 列(采集端 str() 落库,§见
    fotmob_client.parse_match_dim);容错解析,解析不出来就是 None,不硬转崩溃。"""
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _venue_weather_referee(r) -> dict:
    """详情页专属字段(2026-08-20,MatchDetailSummary)。只在 match_by_id 合入,
    不进 _row_to_summary——列表端点不需要,也不该为每张卡片多带这几列。"""
    keys = r.keys()
    return {
        "referee": r["Referee"] if "Referee" in keys else None,
        "temperature_c": _int_or_none(r["Temperature"] if "Temperature" in keys else None),
        "wind_speed_kmh": _int_or_none(r["Wind_Speed"] if "Wind_Speed" in keys else None),
        "weather_description": r["Weather_Description"] if "Weather_Description" in keys else None,
        "venue_name": r["Venue_Name"] if "Venue_Name" in keys else None,
        "venue_city": r["Venue_City"] if "Venue_City" in keys else None,
        "venue_country": r["Venue_Country"] if "Venue_Country" in keys else None,
    }


def _color_pair(light, dark) -> dict | None:
    """两个都缺失时返回 None(不是"数据是 null/null"),供前端一次性判断
    "这场比赛完全没有配色数据" vs "有数据但某个模式缺失"。"""
    if light is None and dark is None:
        return None
    return {"light": light, "dark": dark}


def _team_colors(r) -> dict:
    """详情页图表配色(2026-08-24,MatchDetailSummary.home_team_color/
    away_team_color)。与 _venue_weather_referee 同一套"列可能不存在于旧测试
    布景"容错模式,只在 match_by_id 合入。"""
    keys = r.keys()

    def get(col):
        return r[col] if col in keys else None

    return {
        "home_team_color": _color_pair(
            get("Home_Team_Color_Light"), get("Home_Team_Color_Dark")
        ),
        "away_team_color": _color_pair(
            get("Away_Team_Color_Light"), get("Away_Team_Color_Dark")
        ),
    }


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# 自然日档(北京时间,UTC+8 无夏令时)相对今天的偏移。三段首尾严格相接。
_CALENDAR_DAY_OFFSETS = {"yesterday": -1, "today": 0, "tomorrow": 1}

# 滚动档天数。正数向未来 [now, now+N),负数向过去 [now-N, now)。
_ROLLING_WINDOW_DAYS = {"3d": 3, "7d": 7, "past3d": -3, "past7d": -7}


def _window_bounds(
    window: str | None, now: datetime
) -> tuple[str | None, str | None]:
    """把 window token 翻成半开区间 [start, end) 的 UTC ISO 字符串。

    两档语义(2026-08-19 增加向过去的窗口后,每档都是双向的):
    - 自然日档 yesterday/today/tomorrow:按**北京自然日**切,不是滚动 24h。
      这不是风格选择——生产实测同一时刻"按 UTC 滚动 24h"是 0 场、"按北京
      自然日"是 5 场,选错用户就看不到昨天的比赛。`today` 因此天然双向,
      「今天赛果」= window=today & status=finished,不需要另造 token。
    - 滚动档 3d/7d/past3d/past7d:以请求时刻 now 为锚,past{N}d = [now-N, now)
      与 {N}d = [now, now+N) 在 now 处严格互补。边界半开,所以开球时刻恰好
      等于 now 的比赛只属于未来窗,不会被两个窗口同时选中。

    注意上界随请求时刻移动,叠加 60 秒共享缓存后最多滞后 60 秒——文案不得
    声称赛果窗口"精确到当前时刻"。
    """
    if window in (None, "all"):
        return None, None
    current = now.astimezone(timezone.utc)
    day_offset = _CALENDAR_DAY_OFFSETS.get(window)
    if day_offset is not None:
        local = current.astimezone(ZoneInfo("Asia/Shanghai"))
        start_local = (local + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return _iso_utc(start_local), _iso_utc(start_local + timedelta(days=1))
    days = _ROLLING_WINDOW_DAYS.get(window)
    if days is None:
        raise ValueError("unsupported match window")
    if days < 0:
        return _iso_utc(current + timedelta(days=days)), _iso_utc(current)
    return _iso_utc(current), _iso_utc(current + timedelta(days=days))


def list_matches(
    conn: sqlite3.Connection,
    league_ids: set[int],
    date: str | None = None,
    status: str | None = None,       # upcoming / finished / None
    league_id: int | None = None,
    season: str | None = None,
    window: str | None = None,
    query: str | None = None,
    query_team_ids: set[int] | None = None,
    match_ids: set[int] | None = None,
    priority_match_ids: set[int] | None = None,
    top_priority_match_ids: set[int] | None = None,
    now: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if league_id is not None:
        league_ids = league_ids & {league_id}
    if not league_ids:
        return {"total": 0, "matches": []}
    ordered_league_ids = sorted(league_ids)
    placeholders = ",".join("?" for _ in ordered_league_ids)
    where = [f"League_ID IN ({placeholders})"]
    params: list = ordered_league_ids
    if date:
        where.append("Date = ?")
        params.append(date)
    if season is not None:
        # 赛季过滤必须发生在 SQL LIMIT 之前——曾在端点层做 Python 后筛,
        # 多赛季联赛(如五大联赛 6 季)下 LIMIT 先截走别的赛季,目标赛季 0 命中
        where.append("Season = ?")
        params.append(season)
    if status == "upcoming":
        where.append("status IN ('NotStarted', 'InPlay')")
    elif status == "finished":
        where.append("status = 'Finish'")
    start, end = _window_bounds(window, now or datetime.now(timezone.utc))
    if start is not None:
        where.append("julianday(kickoff_at_utc) >= julianday(?)")
        params.append(start)
    if end is not None:
        where.append("julianday(kickoff_at_utc) < julianday(?)")
        params.append(end)
    cleaned_query = query.strip() if isinstance(query, str) else ""
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        try:
            i18n_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(dim_team_i18n)").fetchall()
            }
        except sqlite3.OperationalError:
            i18n_columns = set()
        i18n_name_en = (
            "t.name_en LIKE ? COLLATE NOCASE OR " if "name_en" in i18n_columns else ""
        )
        alias_sql = ""
        alias_params: list[int] = []
        if query_team_ids:
            ordered_team_ids = sorted(query_team_ids)
            alias_placeholders = ",".join("?" for _ in ordered_team_ids)
            alias_sql = (
                f" OR Home_Team_ID IN ({alias_placeholders})"
                f" OR Away_Team_ID IN ({alias_placeholders})"
            )
            alias_params = ordered_team_ids * 2
        where.append(
            f"""(
              Home_Team_Name LIKE ? COLLATE NOCASE OR
              Away_Team_Name LIKE ? COLLATE NOCASE OR
              EXISTS (
                SELECT 1 FROM dim_team_i18n t
                 WHERE t.Team_ID IN (dim_match.Home_Team_ID, dim_match.Away_Team_ID)
                   AND (t.name_zh LIKE ? COLLATE NOCASE OR {i18n_name_en} 0)
              )
              {alias_sql}
            )"""
        )
        params.extend(
            [pattern, pattern, pattern]
            + ([pattern] if i18n_name_en else [])
            + alias_params
        )
    if match_ids is not None:
        if not match_ids:
            return {"total": 0, "matches": []}
        ordered_match_ids = sorted(match_ids)
        id_placeholders = ",".join("?" for _ in ordered_match_ids)
        where.append(f"Match_ID IN ({id_placeholders})")
        params.extend(ordered_match_ids)
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM dim_match WHERE {cond}", params).fetchone()[0]
    order_params: list[int] = []
    # 统一时间轴:COALESCE(kickoff_at_utc, Date) 而不是把"没有精确开球时间"的
    # 行整体压到最后再按 Match_ID 排——那样会让同一批比赛(如整赛季只有自然日
    # 粒度的英超 2026/2027)在多联赛混排列表里完全失去时间顺序,被排在任何有
    # 精确 kickoff 的联赛(哪怕是几个月后的比赛)后面(CLAUDE.md §6.2.1:不伪装
    # 精确时间,但也不能因为"不精确"就放弃参与时间排序——Date 本身是真实来源
    # 数据,只是粒度粗;北京时间是固定 UTC+8 无夏令时偏移,按 UTC 排序等价于
    # 按北京时间排序,不需要额外转换)。
    if status == "finished":
        # 赛果:最新在前。这条分支**刻意不建 priority CASE**——调用方
        # (routes_public.py 的 /api/v1/matches)每次都传 priority_match_ids,
        # 但把"免费且已发布概率"提到前面对赛程有意义、对赛果只会打乱时间倒序,
        # 用户翻赛果就是要按时间从近到远看。所以 boost=free_predicted 对
        # status=finished 无效是**设计如此**,不是漏了,别当成 bug 补上。
        order = "julianday(COALESCE(kickoff_at_utc, Date)) DESC, Match_ID DESC"
    else:
        # 两层优先级(2026-08-16 首页重点位确定性选场修复):
        # top_priority_match_ids 排在 priority_match_ids 之前——原先单一
        # CASE WHEN 只能表达"有 X 特征的比赛都排最前",当这批比赛数量超过
        # limit 时,里面更值得优先的一小撮(比如"免费且已发布概率")仍然可能
        # 被同一大类里开球更早的其它比赛挤出分页截断线之外。新增这一档让
        # 调用方可以标出"这几场无论如何都必须留在第一页"的更强子集,
        # 不影响只传 priority_match_ids 时的既有单层语义(向后兼容:
        # 不传 top_priority_match_ids 时生成的 SQL 与改动前逐字节相同)。
        tiers: list[tuple[str, list[int]]] = []
        if top_priority_match_ids:
            ids = sorted(top_priority_match_ids)
            placeholders = ",".join("?" for _ in ids)
            tiers.append((f"Match_ID IN ({placeholders})", ids))
        if priority_match_ids:
            ids = sorted(priority_match_ids)
            placeholders = ",".join("?" for _ in ids)
            tiers.append((f"Match_ID IN ({placeholders})", ids))
        priority_order = ""
        if tiers:
            when_clauses = [f"WHEN {cond} THEN {rank}" for rank, (cond, _) in enumerate(tiers)]
            priority_order = f"CASE {' '.join(when_clauses)} ELSE {len(tiers)} END, "
            for _, ids in tiers:
                order_params.extend(ids)
        order = (
            priority_order
            + "julianday(COALESCE(kickoff_at_utc, Date)) ASC, Match_ID ASC"
        )
    rows = conn.execute(
        f"SELECT * FROM dim_match WHERE {cond} ORDER BY {order} LIMIT ? OFFSET ?",
        params + order_params + [limit, offset],
    ).fetchall()
    # 2026-08-19 性能修复:team_display_map() 每次都全扫 dim_match(33,868 行)
    # 求 304 支球队的译名,只为了本页 ≤limit 场比赛里出现的球队——改成只查
    # 这一页真正出现过的 team_id(见 backend/queries/teams.py::team_display_for
    # 的等价性说明与 tests/backend/test_team_display_for.py)。
    page_team_ids = {r["Home_Team_ID"] for r in rows} | {r["Away_Team_ID"] for r in rows}
    display = team_display_for(conn, page_team_ids)
    return {"total": total, "matches": [_row_to_summary(r, display) for r in rows]}


def match_by_id(conn: sqlite3.Connection, match_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM dim_match WHERE Match_ID=?", (match_id,)).fetchone()
    if r is None:
        return None
    display = team_display_for(conn, {r["Home_Team_ID"], r["Away_Team_ID"]})
    return {**_row_to_summary(r, display), **_venue_weather_referee(r), **_team_colors(r)}


def recent_form(
    conn: sqlite3.Connection, team_id: int, before_date: str, limit: int = 5
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM dim_match
           WHERE status='Finish' AND Date < ? AND (Home_Team_ID=? OR Away_Team_ID=?)
           ORDER BY Date DESC LIMIT ?""",
        (before_date, team_id, team_id, limit),
    ).fetchall()
    # 只有对手需要译名(见下方 _team_ref(opp_id,...)),team_id 自己不出现在
    # 输出里——按真正返回的对手 id 收窄,不查全表。
    opponent_ids = {
        (r["Away_Team_ID"] if r["Home_Team_ID"] == team_id else r["Home_Team_ID"])
        for r in rows
    }
    display = team_display_for(conn, opponent_ids)
    out = []
    for r in rows:
        is_home = r["Home_Team_ID"] == team_id
        gf = r["home_score"] if is_home else r["away_score"]
        ga = r["away_score"] if is_home else r["home_score"]
        if gf is None or ga is None:
            continue
        opp_id = r["Away_Team_ID"] if is_home else r["Home_Team_ID"]
        opp_name = r["Away_Team_Name"] if is_home else r["Home_Team_Name"]
        out.append(
            {
                "match_id": r["Match_ID"],
                "date_utc": r["Date"],
                "opponent": _team_ref(opp_id, opp_name, display),
                "venue": "home" if is_home else "away",
                "goals_for": gf,
                "goals_against": ga,
                "result": "W" if gf > ga else ("L" if gf < ga else "D"),
            }
        )
    return out


def matches_with_shot_history(conn: sqlite3.Connection) -> set[int]:
    """双方球队都有历史射门数据的比赛 id 集合(赛前射门分布图能否画出来的判据)。

    赛前射门分布图画的是"两队各自最近 N 场的射门落点",所以它需要的不是这场比赛
    自己的射门数据(还没踢),而是**双方球队过去有没有被采集过射门**。

    实现上先取"有射门史的球队集合"(fact_shotmap 33 万行 → 223 支球队,很小),
    再要求主客两队都在集合内 —— 比逐场 EXISTS 子查询便宜得多。

    用途:首页重点比赛选场需要避开"点进去一片空白"的比赛(2026-08-12 实测:
    未来 7 天 77 场里有 24 场射门与赔率都没有,其中英冠/巴甲/葡超/荷甲四个
    本周赛程最多的联赛在 dim_match 里 0 场完赛、0 行射门,是纯赛程壳)。
    """
    try:
        teams = {int(r[0]) for r in conn.execute("SELECT DISTINCT Team_ID FROM fact_shotmap")}
    except sqlite3.OperationalError:
        return set()
    if not teams:
        return set()
    return {
        int(r[0])
        for r in conn.execute(
            "SELECT Match_ID, Home_Team_ID, Away_Team_ID FROM dim_match"
        )
        if r[1] in teams and r[2] in teams
    }


def seasons_of_league(conn: sqlite3.Connection, league_id: int) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT Season FROM dim_match WHERE League_ID=? ORDER BY Season", (league_id,)
        )
    ]


def default_fixture_season(conn: sqlite3.Connection, league_id: int, seasons: list[str]) -> str | None:
    """赛程默认赛季 = 最早一场未开赛比赛所在的赛季;没有未开赛比赛则回退最新赛季。

    不用 max(Season):英超 2026/2027 已有赛程但 2025/2026 才是"最新已完赛"——
    max(Season) 两种语境下答案不同,这里明确回答"用户现在最该看哪个赛季"。
    不用"最新一个有 NotStarted 的赛季":未来若同时存在两个都有未开赛比赛的
    赛季(如跨赛季衔接期),那个定义会挑出开赛更晚的一个,这里始终挑今晚的球。
    """
    row = conn.execute(
        """SELECT Season FROM dim_match
             WHERE League_ID=? AND status IN ('NotStarted','InPlay')
             ORDER BY COALESCE(kickoff_at_utc, Date) ASC, Match_ID ASC
             LIMIT 1""",
        (league_id,),
    ).fetchone()
    if row is not None:
        return row[0]
    return seasons[-1] if seasons else None


def _extra_num(raw, key: str):
    """从 fact_league_table.extra_json 取一个数值字段;缺失/畸形一律 None。

    绝不返回 0 兜底 —— 0 在"多拿/少拿分"语境下是有意义的真实取值
    (正好兑现),不能和"没有这个数据"混为一谈。
    """
    if not raw:
        return None
    try:
        value = json.loads(raw).get(key)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, (int, float)) else None


def standings(
    conn: sqlite3.Connection,
    league_id: int,
    season: str | None = None,
    table_type: str = "all",
) -> dict:
    """联赛积分榜。

    两个真实缺陷的修复(2026-08-12 实测确认,均导致页面渲染空表/全零表):

    1. **分组赛制的 table_type 带后缀**。此前硬编码 `table_type='all'`,而
       J1(223)2026 赛季在 fact_league_table 里根本没有 plain `all` 行,只有
       `all:100 Year Vision League East` / `...West` 各 10 行(两组赛制)。
       结果是 J1 积分榜页渲染出一张空表。改为:plain `all` 缺失时,回退到
       `all:` 前缀的全部分组行(按组名 + 位次排序,同页展示各组)。

    2. **`played` 全 0 的幽灵赛季**。英超(47)有一个 Season='2024' 共 60 行、
       `sum(played)=0` 的赛季(真实赛季是 '2024/2025',sum=2380)。它会进入
       available_seasons 供用户选择,选中后是一张全零表。改为:可选赛季只保留
       真正踢过球的赛季;若全部赛季都是 0(联赛刚接入),则如实保留原列表,
       不制造"这个联赛不存在"的假象。

    `table_type` 打开此前 100% 不可见的另外三档(2026-08-12):
    `home`(747 行)/ `away`(747)/ `form`(703)/ `xg`(695),合计 2,892 行
    —— 此前函数硬编码 'all',这些行进不了任何 API,更进不了页面。
    xg 档还带 x_points / x_position 与 extra_json 里预算好的 xPointsDiff,
    是"实际拿分 vs 按 xG 应得分"这类内容的唯一数据来源。
    """
    try:
        season_rows = conn.execute(
            """SELECT Season, SUM(COALESCE(played, 0)) AS total_played
                 FROM fact_league_table WHERE League_ID=?
                GROUP BY Season ORDER BY Season""",
            (league_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        season_rows = []
    if not season_rows:
        return {"season": season, "available_seasons": [], "rows": []}
    played_seasons = [r[0] for r in season_rows if (r[1] or 0) > 0]
    seasons = played_seasons or [r[0] for r in season_rows]
    if season is None or season not in seasons:
        season = seasons[-1]
    display = team_display_map(conn)

    def fetch(where_type: str, params: tuple) -> list:
        return conn.execute(
            f"""SELECT Team_ID, Team_Name, position, played, wins, draws, losses,
                       goals_for, goals_against, goal_diff, points, qual_color,
                       xg, xg_conceded, x_points, x_position, extra_json,
                       table_type
                  FROM fact_league_table
                 WHERE League_ID=? AND Season=? AND {where_type}
                 ORDER BY table_type, position""",
            params,
        ).fetchall()

    rows = fetch("table_type=?", (league_id, season, table_type))
    if not rows:
        # 分组赛制回退:'<type>:<组名>'(如 J1 的 'all:100 Year Vision League East')
        rows = fetch("table_type LIKE ?", (league_id, season, f"{table_type}:%"))
    return {
        "season": season,
        "available_seasons": seasons,
        "rows": [
            {k: r[k] for k in r.keys() if k not in ("table_type", "extra_json")}
            | {
                "team": _team_ref(r["Team_ID"], r["Team_Name"], display),
                # 分组赛制下告诉前端这一行属于哪个组;单表联赛为 None。
                "group_name": (
                    r["table_type"].split(":", 1)[1]
                    if ":" in str(r["table_type"])
                    else None
                ),
                # xg 档专有:实际积分 − 按 xG 应得积分(实测确认就是这个方向,
                # 例:阿森纳 85 实得 vs 77.8 应得 → +7.18)。非 xg 档为 None。
                "x_points_diff": _extra_num(r["extra_json"], "xPointsDiff"),
                "x_position_diff": _extra_num(r["extra_json"], "xPositionDiff"),
            }
            for r in rows
        ],
    }


def recent_shot_map_spec(
    conn: sqlite3.Connection,
    match_id: int,
    window: int = 5,
) -> dict | None:
    """Return an honest recent-match shot-map dataset for both target teams.

    The target match itself and later matches are never included.  ``same_league``
    and ``all_covered`` keep separate match-id sets so the browser can filter
    locally without pretending that our database covers every competition.
    Missing shot rows remain a coverage fact; they are not converted to zero
    shots.
    """
    if window < 1 or window > 10:
        raise ValueError("shot-map window must be between 1 and 10")
    try:
        target = conn.execute(
            """SELECT Match_ID, League_ID, Date, Home_Team_ID, Away_Team_ID,
                      Home_Team_Name, Away_Team_Name
                 FROM dim_match WHERE Match_ID=?""",
            (match_id,),
        ).fetchone()
        if target is None:
            return None
        conn.execute("SELECT 1 FROM fact_shotmap LIMIT 1")
    except sqlite3.OperationalError:
        return None

    display = team_display_for(conn, {target["Home_Team_ID"], target["Away_Team_ID"]})
    teams = [
        {
            "team_id": int(target["Home_Team_ID"]),
            "name": display_name_for_team(
                target["Home_Team_ID"],
                provider_name=target["Home_Team_Name"],
                display=display,
            ),
            "side": "home",
        },
        {
            "team_id": int(target["Away_Team_ID"]),
            "name": display_name_for_team(
                target["Away_Team_ID"],
                provider_name=target["Away_Team_Name"],
                display=display,
            ),
            "side": "away",
        },
    ]

    recent_sets: dict[str, dict[str, dict]] = {}
    all_match_ids: set[int] = set()
    for team in teams:
        by_scope: dict[str, dict] = {}
        for scope in ("same_league", "all_covered"):
            league_clause = "AND League_ID=?" if scope == "same_league" else ""
            params: list[int | str] = [
                target["Date"],
                target["Date"],
                match_id,
                team["team_id"],
                team["team_id"],
            ]
            if scope == "same_league":
                params.append(target["League_ID"])
            params.append(window)
            rows = conn.execute(
                f"""SELECT Match_ID
                      FROM dim_match
                     WHERE status='Finish'
                       AND (Date < ? OR (Date = ? AND Match_ID < ?))
                       AND (Home_Team_ID=? OR Away_Team_ID=?)
                       {league_clause}
                    ORDER BY Date DESC, Match_ID DESC
                     LIMIT ?""",
                params,
            ).fetchall()
            ids = [int(row["Match_ID"]) for row in rows]
            all_match_ids.update(ids)
            covered = 0
            if ids:
                placeholders = ",".join("?" for _ in ids)
                covered = int(
                    conn.execute(
                        f"""SELECT COUNT(DISTINCT Match_ID)
                              FROM fact_shotmap
                             WHERE Match_ID IN ({placeholders})""",
                        ids,
                    ).fetchone()[0]
                )
            by_scope[scope] = {
                "match_ids": ids,
                "matched_games": len(ids),
                "shot_covered_games": covered,
            }
        recent_sets[str(team["team_id"])] = by_scope

    matches: list[dict] = []
    shots: list[dict] = []
    if all_match_ids:
        ids = sorted(all_match_ids)
        placeholders = ",".join("?" for _ in ids)
        match_rows = conn.execute(
            f"""SELECT Match_ID, League_ID, Date, Home_Team_ID, Away_Team_ID,
                      Home_Team_Name, Away_Team_Name
                 FROM dim_match
                WHERE Match_ID IN ({placeholders})
                ORDER BY Date DESC, Match_ID DESC""",
            ids,
        ).fetchall()
        # team_display_for 是按需 scoped 查询,只覆盖点名传入的 team_id——
        # 上面只传了 target 两队,近 N 场里的对手球队(如 Urawa Red Diamonds)
        # 从未被点名,拿不到中文名,回退成 FotMob 英文名(比赛筛选栏"对
        # Urawa Red Diamonds")。这里补扫这批比赛涉及的全部球队,一次性
        # 把对手也纳入中文名映射。
        opponent_ids = {
            tid
            for row in match_rows
            for tid in (row["Home_Team_ID"], row["Away_Team_ID"])
        }
        if opponent_ids - display.keys():
            display = {**display, **team_display_for(conn, opponent_ids)}
        for row in match_rows:
            matches.append(
                {
                    "match_id": int(row["Match_ID"]),
                    "league_id": int(row["League_ID"]),
                    "date_utc": row["Date"],
                    "home": _team_ref(
                        row["Home_Team_ID"], row["Home_Team_Name"], display
                    ),
                    "away": _team_ref(
                        row["Away_Team_ID"], row["Away_Team_Name"], display
                    ),
                }
            )
        for index, row in enumerate(
            conn.execute(
                f"""SELECT Match_ID, Player_ID, Team_ID, Minute, Period,
                           X_Coord, Y_Coord, xG, xGOT, Situation, Outcome, Shot_Type,
                           Is_Blocked, Is_On_Target
                      FROM fact_shotmap
                     WHERE Match_ID IN ({placeholders})
                       AND X_Coord BETWEEN 0 AND 105
                       AND Y_Coord BETWEEN 0 AND 68
                     ORDER BY Match_ID, Minute, Team_ID, Player_ID, X_Coord, Y_Coord""",
                ids,
            )
        ):
            shots.append(
                {
                    "shot_id": f"{row['Match_ID']}:{index}",
                    "match_id": int(row["Match_ID"]),
                    "player_id": row["Player_ID"],
                    "team_id": int(row["Team_ID"]),
                    "minute": row["Minute"],
                    "period": row["Period"],
                    "x": row["X_Coord"],
                    "y": row["Y_Coord"],
                    "xg": row["xG"],
                    "xgot": row["xGOT"],
                    "situation": row["Situation"],
                    "outcome": row["Outcome"],
                    "shot_type": row["Shot_Type"],
                    # 2026-08-23 起才采集(见 backend/migrations/core/
                    # 0005_shotmap_raw_fields.sql),旧场次/未回填场次恒为
                    # None——前端必须区分 None(无精确口径)和 False。
                    "is_blocked": bool(row["Is_Blocked"]) if row["Is_Blocked"] is not None else None,
                    "is_on_target": bool(row["Is_On_Target"]) if row["Is_On_Target"] is not None else None,
                }
            )
    if not shots:
        return None
    # 官方口径射正/被封堵/总射门(fact_team_match_stats.extra_json)——单纯从
    # fact_shotmap 的 Outcome 数逐次射门算"射正"会把 AttemptSaved 里混进的
    # 被封堵射门也算作射正(实测 26,067 队场:场均 7.75 vs 官方 4.36,只有
    # 6.8% 完全吻合)。前端聚合汇总数字改用这里。2026-08-23 起已回填的场次
    # (shots[].is_blocked 非 None)可以在单次射门层面精确拆分"被扑出"/
    # "被封堵"(见 ShotMapExplorer.tsx outcomeLabel);未回填场次仍不假装
    # 能在单次射门层面区分,保留原始 Outcome 展示。
    official_stats: list[dict] = []
    if all_match_ids:
        for row in conn.execute(
            f"""SELECT Match_ID, Team_ID, extra_json
                  FROM fact_team_match_stats
                 WHERE Period='All' AND Match_ID IN ({placeholders})""",
            ids,
        ):
            extra = json.loads(row["extra_json"] or "{}")
            sot = extra.get("ShotsOnTarget")
            blocked = extra.get("blocked_shots")
            total = extra.get("total_shots")
            if sot is None and blocked is None and total is None:
                continue
            official_stats.append(
                {
                    "match_id": int(row["Match_ID"]),
                    "team_id": int(row["Team_ID"]),
                    "shots_on_target": sot,
                    "blocked_shots": blocked,
                    "total_shots": total,
                }
            )

    return {
        "pitch": {"length": 105, "width": 68},
        "coordinate_note": "FotMob normalized attacking direction",
        "window": window,
        "target_match_id": match_id,
        "teams": teams,
        "recent_sets": recent_sets,
        "matches": matches,
        "shots": shots,
        "official_stats": official_stats,
    }
