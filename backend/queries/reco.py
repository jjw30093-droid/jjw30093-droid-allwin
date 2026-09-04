"""「每日精选」只读查询(权限投影在此层完成,后端是权限真源 CLAUDE.md §8.3)。

可见性(2026-08-16 产品权限口径修正,经用户批准——CLAUDE.md §8 三段可见性
修订;取代旧的"reco:daily 付费全局布尔权益"):
- 每日精选是全站唯一需要 admin 授权的内容,按"用户 + 单条 slip"授予
  (backend/commands/reco_access.py),不再是"持有 daily_picks 订阅即可看
  近 30 天全部推荐单";
- 列表面 daily_slips():近 30 天(slip_date)published/settled/voided 单的
  存在性对任何已登录用户可见,内容(标题/摘要/腿/思路说明)只对当前用户
  持有 active reco_access_grants 的 slip 下发,未授权 slip 只给"存在性 +
  状态"的中性投影(access_required=True);
- 正文面 daily_slip_detail() + /reco/daily/{slip_id} 路由层的 has_access
  判定:未登录 401,已登录未授权 403(不含任何正文字段),已授权 200;
- reco:track_record 战绩归档(结算/作废归档,全部历史)已改为匿名可见
  ——命中/未中/走水全展示,是站点自身的历史运营记录,与
  backend/queries/track_record.py 里模型预测的公开战绩端点同一先例,不
  属于"每日精选"按场授权的约束范围;
- draft 任何非 admin 面都不出现;
- 聚合口径(2026-08-16 扩展四分之一盘口 half_win/half_loss):push 不计分母;
  half_win/half_loss 计入分母(已判定,只是不是整仓赢/输),half_win 按 0.5
  个命中计权、half_loss 不贡献命中权重——命中率 = (win + 0.5*half_win) /
  (win + lose + half_win + half_loss)。两者都必须在 *_count 里独立可见,
  不得被三值硬编码的统计口径吞掉(CLAUDE.md 严禁选择性丢失公开战绩记录),
  也不得被误算成整赢或整输;净单位 = Σ(return_units-1) 只对 settled;
  作废单单列计数,不进任何分母也不消失。
"""

import json
import sqlite3
from datetime import timedelta

from backend.commands.reco_settlement_math import SettlementUnresolvable, resolve_leg_result
from backend.db.util import utc_now
from backend.media.team_crests import resolve_team_crest_url
from backend.queries.leagues import LEAGUE_META
from backend.queries.teams import display_name_for_team, team_display_map

RECO_DAILY_WINDOW_DAYS = 30

# 每日公推(board='daily_public',2026-09 新增,经用户批准):与「每日精选」
# 并列的完全公开板块,不需要登录/授权。公推是引流面,窗口比精选(30天)
# 短——近 7 天足够展示连续性,一个常量,随时可调。
RECO_PUBLIC_WINDOW_DAYS = 7
_RESULT_BREAKDOWN_SQL = """
             SUM(CASE WHEN status='settled' AND result='win'        THEN 1 ELSE 0 END) AS wins,
             SUM(CASE WHEN status='settled' AND result='lose'       THEN 1 ELSE 0 END) AS loses,
             SUM(CASE WHEN status='settled' AND result='push'       THEN 1 ELSE 0 END) AS pushes,
             SUM(CASE WHEN status='settled' AND result='half_win'   THEN 1 ELSE 0 END) AS half_wins,
             SUM(CASE WHEN status='settled' AND result='half_loss'  THEN 1 ELSE 0 END) AS half_losses,
"""


def hit_rate_from_counts(
    win: int, lose: int, half_win: int, half_loss: int
) -> float | None:
    """命中率的唯一实现:`(win + 0.5*half_win) / (win + lose + half_win + half_loss)`。
    push 不计分母;分母为 0 时返回 None(不是 0.0,也不是 1.0)。

    2026-09 从 _summarize_result_row 提取成公开函数,供
    backend/queries/reco_highlight.py(首页战绩 banner 的择优口径)复用——
    命中率算式只能有一份实现,否则两处口径会各自漂移。提取是纯搬迁,
    _summarize_result_row 的输出逐字节不变。
    """
    decided = win + lose + half_win + half_loss
    if not decided:
        return None
    return round((win + 0.5 * half_win) / decided, 4)


def _summarize_result_row(row: sqlite3.Row, *, settled: int, voided: int, net_units: float) -> dict:
    """把一行聚合 SQL 的原始计数,转成对外 DTO 形状(供 track_record_summary
    与 public_overview 共用,避免半赢/半输两个新枚举值在两处各写一套、
    其中一处漏写的情况)。"""
    wins = row["wins"] or 0
    loses = row["loses"] or 0
    half_wins = row["half_wins"] or 0
    half_losses = row["half_losses"] or 0
    return {
        "settled_count": settled,
        "win_count": wins,
        "lose_count": loses,
        "push_count": row["pushes"] or 0,
        "half_win_count": half_wins,
        "half_loss_count": half_losses,
        "voided_count": voided,
        "hit_rate": hit_rate_from_counts(wins, loses, half_wins, half_losses),
        "net_units": round(net_units, 4),
    }


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


def daily_slips(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """列表面(2026-08-16 起按"用户 + 单条 slip"授权,取代旧的全局
    reco:daily 布尔权益门禁;CLAUDE.md §8 三段可见性修订):近 30 天(按
    slip_date)非 draft 单,新日期在前——存在性 + 状态对任何已登录用户全部
    可见,但只有当前用户对该 slip 持有 active reco_access_grants 授权时才
    给完整投影,否则只给"存在性 + 状态"的中性投影
    (access_required=True),标题/摘要/腿的市场选择赔率/思路说明整体不
    出现(不是置 null——受限字段物理不下发,与 CLAUDE.md §8.2 匿名概率边界
    同一纪律)。"""
    cutoff = (utc_now() - timedelta(days=RECO_DAILY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM reco_slips WHERE status != 'draft' AND board='daily_pick'"
        " AND slip_date >= ? ORDER BY slip_date DESC, published_at DESC",
        (cutoff,),
    ).fetchall()
    granted_slip_ids = {
        r[0] for r in conn.execute(
            "SELECT slip_id FROM reco_access_grants WHERE user_id=? AND status='active'",
            (user_id,),
        ).fetchall()
    }
    legs = _legs_by_slip(conn, [r["id"] for r in rows if r["id"] in granted_slip_ids])
    out = []
    for r in rows:
        if r["id"] in granted_slip_ids:
            out.append({**_slip_dto(r, legs.get(r["id"], [])), "access_required": False})
        else:
            out.append({
                "id": r["id"], "slip_date": r["slip_date"], "status": r["status"],
                "access_required": True,
            })
    return out


def daily_slip_detail(conn: sqlite3.Connection, slip_id: str) -> dict | None:
    """/reco/daily/{slip_id} 正文访问用:非 draft 单的完整投影。draft 视为
    不存在(404)——与列表面/战绩面"draft 永不外泄"的既有规则一致。授权
    判定不在这里做(见 backend.commands.reco_access.has_access),这里只
    负责"这张单是否存在、内容长什么样",调用方(路由层)先查存在性再查
    授权,顺序保证未授权用户拿到的是明确的 403 而不是被"不存在"掩盖。"""
    row = conn.execute(
        "SELECT * FROM reco_slips WHERE id=? AND status != 'draft' AND board='daily_pick'",
        (slip_id,),
    ).fetchone()
    if row is None:
        return None
    legs = _legs_by_slip(conn, [row["id"]])
    return _slip_dto(row, legs.get(row["id"], []))


def track_record_slips(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> tuple[int, list[dict]]:
    """登录面:settled/voided 全历史归档(命中/未中/走水/作废全展示,不挑选
    不隐藏)。只取 board='daily_pick'——每日公推(board='daily_public')战绩
    本期不做,绝不能静默混进这份已公开的精选战绩数字里(2026-09)。"""
    total = conn.execute(
        "SELECT COUNT(*) FROM reco_slips WHERE status IN ('settled','voided')"
        " AND board='daily_pick'"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM reco_slips WHERE status IN ('settled','voided')"
        " AND board='daily_pick' ORDER BY slip_date DESC, settled_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    legs = _legs_by_slip(conn, [r["id"] for r in rows])
    return total, [_slip_dto(r, legs.get(r["id"], [])) for r in rows]


def track_record_summary(conn: sqlite3.Connection) -> dict:
    """同 track_record_slips():只聚合 board='daily_pick',公推不计入精选
    已公开的命中率/盈亏单位(2026-09)。"""
    # 反向指针(2026-09):首页战绩 banner 的**择优**口径在
    # backend/queries/reco_highlight.py。本函数永远全样本,
    # 不得为了让 banner 好看在这里加任何过滤。
    row = conn.execute(
        f"""SELECT
             SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END)                    AS settled,
{_RESULT_BREAKDOWN_SQL}
             SUM(CASE WHEN status='voided' THEN 1 ELSE 0 END)                     AS voided,
             SUM(CASE WHEN status='settled' THEN return_units - 1 ELSE 0 END)     AS net_units
           FROM reco_slips WHERE board='daily_pick'""",
    ).fetchone()
    return _summarize_result_row(
        row, settled=row["settled"] or 0, voided=row["voided"] or 0,
        net_units=row["net_units"] or 0.0,
    )


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
    只统计 board='daily_pick'——这是「每日精选」模块的摘要,每日公推
    (board='daily_public')有自己独立的公开列表,不共用这份聚合数字,
    避免两个板块的样本混在一起(2026-09)。
    """
    # 反向指针(2026-09):首页战绩 banner 的**择优**口径在
    # backend/queries/reco_highlight.py。本函数永远全样本,
    # 不得为了让 banner 好看在这里加任何过滤。
    from zoneinfo import ZoneInfo

    today = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    today_row = conn.execute(
        """SELECT COUNT(*) AS n, MAX(published_at) AS latest
           FROM reco_slips WHERE slip_date=? AND status != 'draft' AND board='daily_pick'""",
        (today,),
    ).fetchone()

    cutoff = (utc_now() - timedelta(days=RECO_DAILY_WINDOW_DAYS)).strftime("%Y-%m-%d")
    row = conn.execute(
        f"""SELECT
             SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END)                    AS settled,
{_RESULT_BREAKDOWN_SQL}
             SUM(CASE WHEN status='voided' THEN 1 ELSE 0 END)                     AS voided,
             SUM(CASE WHEN status='settled' THEN return_units - 1 ELSE 0 END)     AS net_units
           FROM reco_slips WHERE board='daily_pick' AND slip_date >= ?""",
        (cutoff,),
    ).fetchone()
    summary = _summarize_result_row(
        row, settled=row["settled"] or 0, voided=row["voided"] or 0,
        net_units=row["net_units"] or 0.0,
    )
    return {
        "today_date": today,
        "today_published_count": today_row["n"] or 0,
        "today_latest_published_at": today_row["latest"],
        "window_days": RECO_DAILY_WINDOW_DAYS,
        **summary,
        "published_match_ids": sorted(published_match_ids(conn)),
    }


def public_slips(conn: sqlite3.Connection) -> list[dict]:
    """每日公推(board='daily_public')的完全公开列表(2026-09 新增):近
    RECO_PUBLIC_WINDOW_DAYS 天非 draft 单,全部完整正文,不做任何按身份/按
    授权的投影裁剪——这是这个板块与「每日精选」daily_slips() 的核心区别:
    没有 access_required 这一步,响应对匿名和登录用户完全一致。结构上是
    daily_slips() 去掉 reco_access_grants 联查后的形态,共用同一套
    _slip_dto/_legs_by_slip,不重新发明投影逻辑。voided 单同样展示(与精选
    "作废不消失"同一纪律)。draft 仍不出现。
    """
    cutoff = (utc_now() - timedelta(days=RECO_PUBLIC_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM reco_slips WHERE board='daily_public' AND status != 'draft'"
        " AND slip_date >= ? ORDER BY slip_date DESC, published_at DESC",
        (cutoff,),
    ).fetchall()
    legs = _legs_by_slip(conn, [r["id"] for r in rows])
    return [_slip_dto(r, legs.get(r["id"], [])) for r in rows]


# ── 首页公推 banner 数据面(2026-09)──────────────────────────────────
#
# 窗口只是安全带,不是主判据:「开球 +2 小时撤下」由前端按各自当前时间精确
# 判定(见下方 public_current_slips 的 docstring),这里只保证载荷有界、且
# 不会有一张永远不结算的老单在首页挂死。
RECO_PUBLIC_CURRENT_WINDOW_DAYS = 2
# 「比赛开球 2 小时后从首页撤下」这条产品规则的单一真源,随响应下发给前端,
# 避免前后端各写一个 2。
RECO_PUBLIC_HIDE_AFTER_KICKOFF_HOURS = 2.0


def _banner_team_ref(team_id, provider_name, display) -> dict | None:
    """banner 的球队投影:中文名 + 同源队徽地址。

    与 queries/matches.py::_team_ref 同一套解析(display_name_for_team +
    resolve_team_crest_url),但**不下发 name_en**——banner 上没有位置展示
    英文名,下发了也只会变成前端要额外丢弃的字段。

    crest_url 为 None 是合法且常见的状态(媒体管线还没采到这支球队的队徽):
    前端 TeamBadge 在 crestUrl 缺失时渲染两字缩写兜底,不是错误态。
    """
    if team_id is None:
        return None
    tid = int(team_id)
    return {
        "team_id": tid,
        "name": display_name_for_team(tid, provider_name=provider_name, display=display),
        "crest_url": resolve_team_crest_url("fotmob", tid),
    }


def _banner_match_facts_for_ids(
    conn_core: sqlite3.Connection, match_ids: set[int]
) -> dict[int, dict]:
    """批量取首页 banner 展示所需的 dim_match 事实:精确开球时刻、联赛、主客队。

    整批取而不是逐条腿取(避免 N+1);跨库不 ATTACH,Python 层按 match_id 拼装
    ——与 reco_highlight.py::_league_ids_for_match_ids 同一范式。

    刻意**不复用** queries/matches.py::list_matches:那个函数 limit 默认 50
    (按 id 精确查却带分页 LIMIT,漏传就静默丢腿),而且 league_ids 是硬过滤
    (不在 LEAGUE_META 的联赛直接返回空行)——banner 的联赛徽缺了应该只是
    不画徽标,不能把整条腿弄丢。这里对未登记联赛只让 league_name_zh 为
    None,腿本身照常下发。

    kickoff_at_utc 可空是合法状态(§6.2.1:来源只给自然日时该列必须 NULL,
    不得补成当天 00:00),本函数如实返回 None,由调用方决定怎么处理,绝不
    用 Date 列顶替。conn_core 缺表或缺列(旧测试布景)时返回空 dict,调用方
    等价于"全部比赛事实未知",不报错——banner 退回只用 match_desc 文本渲染。
    """
    if not match_ids:
        return {}
    placeholders = ",".join("?" for _ in match_ids)
    try:
        rows = conn_core.execute(
            f"SELECT Match_ID, kickoff_at_utc, League_ID,"
            f" Home_Team_ID, Home_Team_Name, Away_Team_ID, Away_Team_Name"
            f" FROM dim_match WHERE Match_ID IN ({placeholders})",
            tuple(match_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    try:
        display = team_display_map(conn_core)
    except sqlite3.OperationalError:
        display = {}
    out: dict[int, dict] = {}
    for r in rows:
        league_id = r["League_ID"]
        meta = LEAGUE_META.get(int(league_id)) if league_id is not None else None
        out[int(r["Match_ID"])] = {
            "kickoff_at_utc": r["kickoff_at_utc"],
            "league_id": int(league_id) if league_id is not None else None,
            "league_name_zh": meta["name_zh"] if meta else None,
            "home": _banner_team_ref(r["Home_Team_ID"], r["Home_Team_Name"], display),
            "away": _banner_team_ref(r["Away_Team_ID"], r["Away_Team_Name"], display),
        }
    return out


def _public_current_legs_by_slip(
    conn: sqlite3.Connection, conn_core: sqlite3.Connection, slip_ids: list[str]
) -> dict[str, list[dict]]:
    """首页 banner 专用腿投影。刻意**不复用** _legs_by_slip:那个函数
    SELECT * 并把 odds/result 带进 DTO,而 banner 的硬性要求是不展示赔率。
    这里在 SQL 就不 SELECT odds,值从头到尾不进本进程——不依赖 Pydantic
    response_model 丢弃多余键这种"查了再丢"的兜底。

    ORDER BY sort_order 与 _legs_by_slip 一致:串关腿的展示顺序是站长录入顺序。
    """
    if not slip_ids:
        return {}
    placeholders = ",".join("?" for _ in slip_ids)
    rows = conn.execute(
        f"SELECT id, slip_id, match_id, match_desc, market, selection"
        f"  FROM reco_legs WHERE slip_id IN ({placeholders}) ORDER BY sort_order",
        slip_ids,
    ).fetchall()
    facts = _banner_match_facts_for_ids(
        conn_core, {r["match_id"] for r in rows if r["match_id"] is not None}
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        # 取不到比赛事实(legacy_manual 腿没有 match_id、或 dim_match 里没有
        # 这一行)时全部字段为 None,前端退回只渲染 match_desc 文本——不因为
        # 缺队徽就把腿藏起来。
        f = facts.get(r["match_id"]) if r["match_id"] is not None else None
        out.setdefault(r["slip_id"], []).append({
            "id": r["id"],
            "match_id": r["match_id"],
            "match_desc": r["match_desc"],
            "market": r["market"],
            "selection": r["selection"],
            "kickoff_at_utc": f["kickoff_at_utc"] if f else None,
            "league_id": f["league_id"] if f else None,
            "league_name_zh": f["league_name_zh"] if f else None,
            "home": f["home"] if f else None,
            "away": f["away"] if f else None,
        })
    return out


def public_current_slips(
    conn: sqlite3.Connection, conn_core: sqlite3.Connection
) -> list[dict]:
    """首页 banner 数据面:board='daily_public' 且 status='published' 的单,
    带每条腿的精确开球时刻。

    与 public_slips() 的三点区别,每一点都对应一条产品决策:
    - 只要 status='published':settled/voided 立刻不再出现(站长决策)。
      voided 单在 /reco 页仍然可见——那边"作废不消失"的记录面纪律不受影响,
      banner 是引流位不是记录面。
    - 窗口 2 天而不是 7 天(见 RECO_PUBLIC_CURRENT_WINDOW_DAYS)。
    - 不下发 odds/result(见 _public_current_legs_by_slip)。

    **本函数不做"开球 +2 小时是否已过"的判定**,只如实下发 kickoff 事实。
    该判定会被 CDN(s-maxage=60)与首页 ISR(revalidate 60)共同缓存,服务端
    算出来的结果随缓存变陈旧、且所有访客共享同一份陈旧判定;由各客户端按
    自己的当前时间判定才永远正确(frontend/lib/reco-banner.ts::
    visiblePublicPicks)。这与 backend/ingest/physical_stats_poll.py::
    within_candidate_window(接收 now 参数、不自己读时钟)是同一思路。

    slip_date 是站长录入的**北京自然日**(同 public_overview 的口径),所以
    cutoff 必须按北京时间算——public_slips 的 7 天窗口下那 8 小时偏差无所谓,
    2 天窗口下会真实影响边界。
    """
    from zoneinfo import ZoneInfo

    beijing_now = utc_now().astimezone(ZoneInfo("Asia/Shanghai"))
    cutoff = (
        beijing_now - timedelta(days=RECO_PUBLIC_CURRENT_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT id, slip_date, title, combo_type, published_at FROM reco_slips"
        " WHERE board='daily_public' AND status='published' AND slip_date >= ?"
        " ORDER BY slip_date DESC, published_at DESC",
        (cutoff,),
    ).fetchall()
    legs = _public_current_legs_by_slip(conn, conn_core, [r["id"] for r in rows])
    return [{
        "id": r["id"],
        "slip_date": r["slip_date"],
        "title": r["title"],
        "combo_type": r["combo_type"],
        "published_at": r["published_at"],
        "legs": legs.get(r["id"], []),
    } for r in rows]


def cross_board_market_conflicts(
    conn: sqlite3.Connection,
    *,
    board: str,
    legs: list[dict],
    exclude_slip_id: str | None = None,
) -> list[dict]:
    """同一场比赛的同一盘口是否已在**另一个**板块被非 voided 单占用
    (2026-09,站长决策:只提醒,不拦截)。

    判定粒度是 (match_id, market),不含 line/side/selection——同一盘口在两个
    板块给出相反方向的推荐(如精选"大2.5"、公推"小2.5")是最需要提醒站长的
    情形,按更细粒度判定会放过这种情况。market 两边做 TRIM(LOWER(...))归一,
    因为 legacy_manual 草稿允许自由文本,大小写/空白差异不应造成漏判。

    match_id 为空的腿(站外赛事,只有 match_desc 文本)跳过,无法判定是否
    同场。voided 单不占用盘口——作废等于内容已撤回。

    只返回提醒信息(不抛异常),调用方(commands/reco.py)决定如何展示;不
    在这里做拦截,保持"只提醒不拦截"的产品决策清晰体现在这一层。
    """
    other_board = "daily_public" if board == "daily_pick" else "daily_pick"
    warnings: list[dict] = []
    for leg in legs:
        match_id = leg.get("match_id")
        market = leg.get("market")
        if match_id is None or not market:
            continue
        params: list = [match_id, market, other_board]
        exclude_sql = ""
        if exclude_slip_id is not None:
            exclude_sql = " AND s.id != ?"
            params.append(exclude_slip_id)
        row = conn.execute(
            f"""SELECT s.id, s.title, s.slip_date, s.status FROM reco_legs l
                JOIN reco_slips s ON s.id = l.slip_id
                WHERE l.match_id = ? AND TRIM(LOWER(l.market)) = TRIM(LOWER(?))
                  AND s.board = ? AND s.status != 'voided'{exclude_sql}
                ORDER BY s.slip_date DESC LIMIT 1""",
            params,
        ).fetchone()
        if row is not None:
            warnings.append({
                "match_id": match_id,
                "market": market,
                "conflicting_slip_id": row["id"],
                "conflicting_slip_title": row["title"],
                "conflicting_slip_date": row["slip_date"],
                "other_board": other_board,
            })
    return warnings


def slip_member_preview(conn: sqlite3.Connection, slip_id: str) -> dict | None:
    """admin 用的"会员视角预览":这张单如果是 published 状态,一个真实会员在
    /reco/daily 会看到的内容形状。不重新发明会员端字段投影——直接复用
    daily_slips()/track_record_slips() 共用的 _slip_dto/_legs_by_slip,只是
    换成读取这一张(可能还是 draft)特定 slip_id,不受 status/30 天窗口限制
    (admin 需要在发布前看预览)。不存在时返回 None,由路由层转 404。
    """
    row = conn.execute("SELECT * FROM reco_slips WHERE id=?", (slip_id,)).fetchone()
    if row is None:
        return None
    legs = _legs_by_slip(conn, [row["id"]])
    return _slip_dto(row, legs.get(row["id"], []))


# ── admin 面(全状态含 draft;比会员 DTO 多 entry_type/结算依据比分与角球/
#    待确认标记等运营信息)────────────────────────────────────────────

def _match_facts_for_ids(
    conn_core: sqlite3.Connection, match_ids: set[int], corner_match_ids: set[int]
) -> dict[int, dict]:
    """批量取 dim_match 状态/比分,(仅需要角球的 match_id)再批量取
    fact_team_match_stats 双方角球——与 backend/commands/reco_auto_settle.py::
    _match_facts 同一个查询口径(Period='All' 的 extra_json->corners),这里
    按整页(而不是逐条腿)批量取,避免 N+1。conn_core 缺表(旧测试布景)时
    直接返回空,由调用方把每条腿当"比赛数据缺失"处理,不报错。
    """
    if not match_ids:
        return {}
    placeholders = ",".join("?" for _ in match_ids)
    try:
        rows = conn_core.execute(
            f"SELECT Match_ID, status, home_score, away_score, Home_Team_ID, Away_Team_ID"
            f" FROM dim_match WHERE Match_ID IN ({placeholders})",
            tuple(match_ids),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    facts: dict[int, dict] = {}
    for r in rows:
        facts[int(r["Match_ID"])] = {
            "status": r["status"], "home_score": r["home_score"], "away_score": r["away_score"],
            "home_team_id": r["Home_Team_ID"], "away_team_id": r["Away_Team_ID"],
            "home_corners": None, "away_corners": None,
        }
    need_ids = corner_match_ids & set(facts.keys())
    if need_ids:
        placeholders2 = ",".join("?" for _ in need_ids)
        try:
            stat_rows = conn_core.execute(
                f"SELECT Match_ID, Team_ID, extra_json FROM fact_team_match_stats"
                f" WHERE Match_ID IN ({placeholders2}) AND Period='All'",
                tuple(need_ids),
            ).fetchall()
        except sqlite3.OperationalError:
            stat_rows = []
        for s in stat_rows:
            f = facts.get(int(s["Match_ID"]))
            if f is None:
                continue
            try:
                extra = json.loads(s["extra_json"] or "{}")
            except ValueError:
                extra = {}
            corners = extra.get("corners")
            corners = float(corners) if corners is not None else None
            if s["Team_ID"] == f["home_team_id"]:
                f["home_corners"] = corners
            elif s["Team_ID"] == f["away_team_id"]:
                f["away_corners"] = corners
    return facts


def _match_finished(facts: dict | None) -> bool:
    return bool(
        facts and facts["status"] == "Finish"
        and facts["home_score"] is not None and facts["away_score"] is not None
    )


def _needs_review_reason(leg_row: sqlite3.Row, facts: dict | None) -> str:
    """低成本"待确认"原因(2026-08-16):直接复用 resolve_leg_result 这个纯函数
    (与 reco_auto_settle 同一个判定实现),不重新发明一套原因分类。只在
    needs_review=True 时调用一次,不为了给理由额外做多余查询。"""
    if leg_row["entry_type"] != "provenance_bound":
        return "该腿为 legacy_manual(缺乏真实盘口溯源),自动结算任务不处理此类腿,需人工结算"
    if facts is None:
        return "比赛数据缺失,无法判定"
    try:
        resolve_leg_result(
            leg_row["market"], leg_row["line"], leg_row["side"],
            home_score=facts["home_score"], away_score=facts["away_score"],
            home_corners=facts["home_corners"], away_corners=facts["away_corners"],
        )
    except SettlementUnresolvable as exc:
        return str(exc)
    # resolve_leg_result 能算出结果,说明只是还没轮到下一次自动结算任务处理——
    # 这里刻意不回传算出的具体 win/lose,避免让这段展示文案看起来像"已经有
    # 确定结果只是没告诉你",实际上这仅仅是"待处理",不是"已判定但隐藏"。
    return "比赛已完赛,等待下一轮自动结算任务处理或人工结算"


def _admin_legs_by_slip(
    conn: sqlite3.Connection, conn_core: sqlite3.Connection, slip_rows: list[sqlite3.Row]
) -> dict[str, list[dict]]:
    slip_ids = [r["id"] for r in slip_rows]
    if not slip_ids:
        return {}
    status_by_slip = {r["id"]: r["status"] for r in slip_rows}
    placeholders = ",".join("?" for _ in slip_ids)
    leg_rows = conn.execute(
        f"SELECT * FROM reco_legs WHERE slip_id IN ({placeholders}) ORDER BY sort_order",
        slip_ids,
    ).fetchall()

    match_ids = {r["match_id"] for r in leg_rows if r["match_id"] is not None}
    corner_match_ids = {
        r["match_id"] for r in leg_rows
        if r["match_id"] is not None and r["market"] == "corners_ou"
    }
    facts_by_match = _match_facts_for_ids(conn_core, match_ids, corner_match_ids)

    out: dict[str, list[dict]] = {}
    for r in leg_rows:
        facts = facts_by_match.get(r["match_id"]) if r["match_id"] is not None else None
        finished = _match_finished(facts)

        match_result = None
        corners = None
        needs_review = False
        needs_review_reason = None

        if r["result"] is not None:
            # 只对已结算腿现算比分/角球——未结算腿不查,避免徒增开销
            # (§任务要求)。
            if finished:
                match_result = {"home_score": facts["home_score"], "away_score": facts["away_score"]}
                if (
                    r["market"] == "corners_ou"
                    and facts["home_corners"] is not None
                    and facts["away_corners"] is not None
                ):
                    corners = {"home": facts["home_corners"], "away": facts["away_corners"]}
        elif status_by_slip.get(r["slip_id"]) == "published" and finished:
            # 待确认:published 单里比赛已正式完赛但 result 仍是 NULL——只读
            # 标记,绝不在这里写任何 result(§不得自动判输)。
            needs_review = True
            needs_review_reason = _needs_review_reason(r, facts)

        out.setdefault(r["slip_id"], []).append({
            "id": r["id"],
            "match_id": r["match_id"],
            "match_desc": r["match_desc"],
            "market": r["market"],
            "selection": r["selection"],
            "odds": r["odds"],
            "result": r["result"],
            "entry_type": r["entry_type"],
            "match_result": match_result,
            "corners": corners,
            "needs_review": needs_review,
            "needs_review_reason": needs_review_reason,
        })
    return out


def _admin_slip_dto(row: sqlite3.Row, legs: list[dict]) -> dict:
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
        "settle_source": row["settle_source"],
        "edit_count": row["edit_count"],
        "last_edited_at": row["updated_at"],
        "board": row["board"],
        "legs": legs,
    }


def admin_slips(
    conn: sqlite3.Connection,
    conn_core: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    board: str = "",
) -> tuple[int, list[dict]]:
    """admin 面:全状态含 draft;可选 status/date_from/date_to(按 slip_date)/
    board 筛选,与既有 limit/offset 配合。total 是筛选后的计数,不是全库
    总数。board 不传即两个板块都返回(admin 需要一眼看到全貌)。"""
    where = []
    params: list = []
    if status:
        where.append("status = ?")
        params.append(status)
    if date_from:
        where.append("slip_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("slip_date <= ?")
        params.append(date_to)
    if board:
        where.append("board = ?")
        params.append(board)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    total = conn.execute(f"SELECT COUNT(*) FROM reco_slips{where_sql}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM reco_slips{where_sql} ORDER BY slip_date DESC, created_at DESC"
        " LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    legs = _admin_legs_by_slip(conn, conn_core, rows)
    return total, [_admin_slip_dto(r, legs.get(r["id"], [])) for r in rows]


ADMIN_MATCH_CANDIDATES_DEFAULT_WINDOW = "7d"


def admin_match_candidates(
    conn_core: sqlite3.Connection, *, query: str | None, limit: int,
    window: str | None = ADMIN_MATCH_CANDIDATES_DEFAULT_WINDOW,
) -> list[dict]:
    """admin 录入每日精选用的比赛候选(替代自由文本描述,减少手打描述与真实
    比赛对不上的风险)。admin 不受 entitlement 门禁约束,全部联赛可见;只看
    未开赛比赛,按开球时间由近到远排序——与站内比赛卡片同一份数据源
    (backend.queries.matches.list_matches),不重新定义一套比赛列表逻辑。

    window(2026-08-16):默认 7 天,语义与解析完全复用 list_matches() 已有的
    window 参数(today/tomorrow/3d/7d/all,见 backend/queries/matches.py::
    _window_bounds),不发明新的窗口表示法。默认收窄搜索范围,是因为多数
    每日精选选的都是近几天的比赛;传 window='all'(或 today/tomorrow/3d)
    可以显式放宽/收紧到不同范围。没有精确 kickoff_at_utc 的比赛在非 'all'
    窗口下会被排除(list_matches 的既有行为,§6.2.1:不得把缺失的精确时间
    伪装成"在窗口内"),这类比赛仍可通过 window='all' 搜到。
    """
    from backend.queries.leagues import LEAGUE_META
    from backend.queries.matches import list_matches

    result = list_matches(
        conn_core, set(LEAGUE_META.keys()), status="upcoming", query=query, limit=limit,
        window=window,
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
