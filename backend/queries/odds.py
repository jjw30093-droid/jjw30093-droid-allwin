"""odds.db 只读查询与 payload 形状归一(供 API 路由、bundle、silver 派生共用)。

背景(2026-08-06 审计 B2):bronze_ng_odds_snap.payload_json 存在两种真实形状——
- 嵌套 {"initial": {...}, "latest": {...}}:线上实时轮询链路
  (backend/ingest/odds_snapshots.py)写入,库中 65 行;
- 扁平 {"home":..,"draw":..,"away":..} / {"home":..,"line":..,"away":..} /
  {"over":..,"line":..,"under":..}:历史回填 CLI
  (backend/cli/ingest_nowgoal_historical_odds.py)写入,库中 734,812 行。

payload_json 在 SQL(裸 TEXT)/Pydantic(payload: dict)/生成 TS(宽 dict)三层
都没有形状契约,两个写入方各带一个只认自己形状的 silver 构建器,前端只认嵌套——
于是 2,152 场"完整走势"的赔率表对 Premium 渲染成整片 "—",且无一处报错。

修复方向是**读侧归一化**(写侧迁移会打破钉死嵌套形状的既有测试;
/odds 端点必须保持透传语义,同样有测试钉死)。本模块是归一化的唯一实现。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.commands.reco_odds_contract import MARKET_ODDS_FORMAT
from backend.models.research.market_baseline import (
    MIN_ODDS,
    OVERROUND_BOUNDS,
    proportional_devig,
)

# Bet365 在 bronze_ng_odds_snap 里有两个 company_id:'8' 是 2026-08-04 起的
# 实时轮询(唯一覆盖未来比赛的),'281' 是 2026-04-06 就停更的历史回填
# (对未来比赛贡献 0 场,但作为兜底不删)。同场两者都有时优先取 '8'。
_WIN_PROB_COMPANY_PRIORITY = ("8", "281")

def normalize_odds_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把两种真实 payload 形状归一为扁平字段组。

    嵌套形状取 latest,latest 缺失(None/空)时退回 initial;
    本就扁平的原样返回。输出恒为
    {home,draw,away} / {home,line,away} / {over,line,under} 之一。
    """
    if not isinstance(payload, dict):
        return {}
    if "latest" in payload or "initial" in payload:
        latest = payload.get("latest")
        if isinstance(latest, dict) and latest:
            return latest
        initial = payload.get("initial")
        if isinstance(initial, dict) and initial:
            return initial
        return {}
    return payload


def legacy_summary_points(
    conn_odds: sqlite3.Connection, fotmob_match_id: int
) -> list[dict[str, Any]]:
    """旧项目两点摘要(bronze_legacy_odds_summary)的读取。

    数据已在入库时归一为 canonical 方向(方向修正见
    backend/cli/ingest_legacy_odds.py),无逐条观测时间戳(§6.2:不伪装)。
    恒返回 initial+latest 两点(2026-08-16 起除"每日精选"外全站比赛内容
    与登录/付费彻底解耦,不再按 odds:history_full 投影裁剪成只给 latest)。

    routes_public 的 /odds 端点与 studio/bundle 共用本函数,保证两处
    对同一场比赛看到完全相同的点集。表不存在(旧测试库)时返回空列表。
    """
    sql = (
        "SELECT market, period, source, provider, line,"
        " home_or_over, draw, away_or_under"
        " FROM bronze_legacy_odds_summary WHERE fotmob_match_id=?"
        " ORDER BY market, source, period"
    )
    try:
        rows = conn_odds.execute(sql, (fotmob_match_id,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def odds_coverage_sets(conn_odds: sqlite3.Connection) -> tuple[set[int], set[int]]:
    """(完整时间线比赛集合, 两点摘要比赛集合)——每请求算一次,不逐行查。

    列表层用它给每场比赛标 odds_coverage_tier:
    full_timeline 优先于 open_close_only(同场两者皆有时按更高档展示)。
    """
    try:
        full = {
            int(row[0])
            for row in conn_odds.execute(
                """SELECT DISTINCT x.fotmob_match_id
                     FROM dim_match_xref x
                     JOIN bronze_ng_odds_snap b
                       ON b.provider_match_id=x.provider_match_id
                    WHERE x.provider='nowgoal'
                      AND x.review_status IN ('auto_ok','confirmed')"""
            )
        }
    except sqlite3.OperationalError:
        full = set()
    try:
        legacy = {
            int(row[0])
            for row in conn_odds.execute(
                "SELECT DISTINCT fotmob_match_id FROM bronze_legacy_odds_summary"
            )
        }
    except sqlite3.OperationalError:
        legacy = set()
    return full, legacy


def odds_coverage_for_match(conn_odds: sqlite3.Connection, match_id: int) -> tuple[bool, bool]:
    """odds_coverage_sets 的单场版:(该场是否有完整时间线, 该场是否有旧两点摘要)。

    2026-08-19 性能修复:GET /matches/{id} 只需要标注**这一场**比赛的覆盖档位,
    却在调 odds_coverage_sets() 时被迫先算出全站约 2,291 场比赛的两个集合再
    做 `match_id in ...` 判断——遍历 737K 条索引项只为了扔掉其中 2,290 场的
    结果。这里复用同一张覆盖索引 idx_ng_snap_series(provider_match_id, ...)
    直接按 provider_match_id 探测,SQLite 走 SEARCH 而不是 SCAN(EXPLAIN QUERY
    PLAN 已核实),生产实测从 209ms(与 odds_last_observed_for_match 合计)
    降到 0.01ms 量级。

    与 odds_coverage_sets 逐位等价(同一 JOIN 条件、同一 review_status 门槛),
    由 tests/backend/test_odds_single_match_scoped.py 钉住;列表端点仍然用
    整表版(那里"一次算全部、避免 N+1"是正确优化,不受本函数影响)。
    """
    try:
        full = conn_odds.execute(
            """SELECT 1
                 FROM dim_match_xref x
                 JOIN bronze_ng_odds_snap b
                   ON b.provider_match_id=x.provider_match_id
                WHERE x.provider='nowgoal'
                  AND x.review_status IN ('auto_ok','confirmed')
                  AND x.fotmob_match_id=?
                LIMIT 1""",
            (match_id,),
        ).fetchone() is not None
    except sqlite3.OperationalError:
        full = False
    try:
        legacy = conn_odds.execute(
            "SELECT 1 FROM bronze_legacy_odds_summary WHERE fotmob_match_id=? LIMIT 1",
            (match_id,),
        ).fetchone() is not None
    except sqlite3.OperationalError:
        legacy = False
    return full, legacy


def odds_last_observed_for_match(conn_odds: sqlite3.Connection, match_id: int) -> str | None:
    """odds_last_observed_by_match 的单场版:该场 full_timeline 赔率最后一次
    真实 observed_at,没有快照时如实 None(不是抛异常,也不编造时间)。

    2026-08-19 性能修复:同 odds_coverage_for_match 的理由——单场详情端点
    不该为了一场比赛的时间戳去算全站 GROUP BY。与整表版同一 JOIN 条件、
    同一 review_status 门槛,由 tests/backend/test_odds_single_match_scoped.py
    钉住等价性。
    """
    try:
        row = conn_odds.execute(
            """SELECT MAX(b.observed_at)
                 FROM dim_match_xref x
                 JOIN bronze_ng_odds_snap b
                   ON b.provider_match_id=x.provider_match_id
                WHERE x.provider='nowgoal'
                  AND x.review_status IN ('auto_ok','confirmed')
                  AND x.fotmob_match_id=?""",
            (match_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    return str(row[0])


def odds_last_observed_by_match(
    conn_odds: sqlite3.Connection, match_ids: set[int] | None = None
) -> dict[int, str]:
    """{fotmob_match_id: 该场 NowGoal 完整时间线赔率最后一次真实 observed_at}。

    只覆盖 full_timeline(auto_ok/confirmed 的 NowGoal 快照,与 odds_coverage_sets
    同一 JOIN 条件、同一 review_status 门槛);legacy(旧资产两点摘要)是一次性
    历史导入,ingested_at 不代表"持续观测的新鲜度",不在这里伪造一个假的新鲜度
    信号——legacy/none 档位的比赛这个函数里没有 key,调用方据此把它们的新鲜度
    状态判成 UNAVAILABLE,而不是编造一个数字。

    一次 GROUP BY 查询拿到所有比赛的 MAX(observed_at),每请求调一次,不逐场
    比赛单独查(N+1)。

    match_ids(2026-08-19 性能修复):可选收窄——省略/None 时行为与之前逐字节
    相同(全站 737K 条索引项的 GROUP BY,生产实测 142ms);列表端点在算完这一
    页真正返回哪些比赛后,把这批 id 传进来,只放大对这一页有用的结果集,
    不改变返回值的形状或语义,只改变覆盖范围。空集合直接返回 {}(不发查询)。
    """
    if match_ids is not None and not match_ids:
        return {}
    scope_clause = ""
    params: list = []
    if match_ids is not None:
        placeholders = ",".join("?" for _ in match_ids)
        scope_clause = f" AND x.fotmob_match_id IN ({placeholders})"
        params = sorted(match_ids)
    try:
        rows = conn_odds.execute(
            f"""SELECT x.fotmob_match_id, MAX(b.observed_at)
                 FROM dim_match_xref x
                 JOIN bronze_ng_odds_snap b
                   ON b.provider_match_id=x.provider_match_id
                WHERE x.provider='nowgoal'
                  AND x.review_status IN ('auto_ok','confirmed')
                  {scope_clause}
                GROUP BY x.fotmob_match_id""",
            params,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {int(row[0]): str(row[1]) for row in rows}


# 数值来源:backend/cli/ops_check.py 的 SOURCE_STALE_HOURS=6(数据源多久没更新
# 算异常的既有阈值,docs/current-state.md §36.4 明确记录过"没有编造任意阈值"——
# 这里复用同一个已有出处的数字,不另外发明一个。backend/queries 是查询层,不
# 反向依赖 backend/cli(CLI 层),因此复制常量值而不 import,避免引入不必要的
# 层间耦合。
ODDS_FRESHNESS_STALE_HOURS = 6


def classify_odds_freshness(last_observed_at: str | None, *, now: datetime | None = None) -> str:
    """把"最后观测时间 + 当前时间"分类为仓库统一的 FRESH/STALE/UNAVAILABLE 三态
    (与 backend/content_status.py::project_freshness、routes_public.py 的
    sync_state 同一套值)。

    None(从未有过 full_timeline 观测)-> "UNAVAILABLE";否则按
    (now - last_observed_at) 是否超过 ODDS_FRESHNESS_STALE_HOURS 分 FRESH/STALE。
    """
    if not last_observed_at:
        return "UNAVAILABLE"
    try:
        observed = datetime.fromisoformat(str(last_observed_at).replace("Z", "+00:00"))
    except ValueError:
        return "UNAVAILABLE"
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    else:
        observed = observed.astimezone(timezone.utc)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current - observed > timedelta(hours=ODDS_FRESHNESS_STALE_HOURS):
        return "STALE"
    return "FRESH"


def latest_1x2_by_match(
    conn_odds: sqlite3.Connection,
    match_ids: set[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """所有比赛的最新 Bet365 1x2 去水概率——一次查询,不逐场请求。

    首页/列表页的胜平负概率条共用本函数(见 CLAUDE.md 首页改版计划):字段
    只在这一处产出,routes_public.py 的 list_matches 与 /matches/{id} 都从
    这里取,不允许出现第二套各自实现的去水逻辑。

    口径(与 backend/models/research/market_baseline.py 的研究基线同一套
    去水公式与拒绝规则,但覆盖范围不同——那边只服务 5 大联赛已完赛比赛做
    回测,这里服务全部联赛的当前比赛,且必须包含 company_id '8' 实时轮询):
    - market='1x2' AND market_phase='pre_match';
    - xref 只认 review_status ∈ (auto_ok, confirmed),无法安全映射的 fail closed;
    - 每场每公司取 observed_at 最新一条(并列取 id 最大);
    - 公司优先级 8 > 281(见模块顶部注释);
    - 比例去水 p_i = (1/odds_i) / Σ_j(1/odds_j);
    - 任一赔率缺失/≤1.01,或 overround 不在 [1.01, 1.35] → 该场剔除,不补 0、不猜。
    2026-08-16 起恒返回最新快照,不再对匿名/无 odds:history_full 权益的请求
    施加 1 小时延迟(除"每日精选"外全站比赛内容与登录/付费彻底解耦)。

    返回的每场只有一个 observed_at——三段概率条底部必须显示这个时间,
    不得把它当成"实时"文案(数据可能是几小时甚至更久前的快照,§6.2 不伪装)。

    match_ids(2026-08-19 性能修复):可选收窄——省略/None 时行为与之前逐字节
    相同(全站取回全部 38,888 行并逐行 json.loads,生产实测 104ms)。列表端点
    对非 boost=free_predicted 的常见路径,在算完这一页真正返回哪些比赛后
    传入这批 id;boost=free_predicted 明确要求"完整候选窗口内确定性地找",
    该路径必须继续传 None。空集合直接返回 {}(不发查询)。
    """
    if match_ids is not None and not match_ids:
        return {}
    scope_clause = ""
    scope_params: list = []
    if match_ids is not None:
        scope_placeholders = ",".join("?" for _ in match_ids)
        scope_clause = f" AND x.fotmob_match_id IN ({scope_placeholders})"
        scope_params = sorted(match_ids)
    placeholders = ",".join("?" for _ in _WIN_PROB_COMPANY_PRIORITY)
    try:
        rows = conn_odds.execute(
            f"""SELECT b.id, x.fotmob_match_id, b.company_id, b.payload_json, b.observed_at
                  FROM bronze_ng_odds_snap b
                  JOIN dim_match_xref x
                    ON x.provider_match_id = b.provider_match_id AND x.provider='nowgoal'
                 WHERE b.market='1x2' AND b.market_phase='pre_match'
                   AND b.company_id IN ({placeholders})
                   AND x.review_status IN ('auto_ok','confirmed')
                   {scope_clause}""",
            list(_WIN_PROB_COMPANY_PRIORITY) + scope_params,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    best: dict[tuple[int, str], tuple[str, int, dict]] = {}
    for r in rows:
        obs = str(r["observed_at"])
        fid = int(r["fotmob_match_id"])
        cid = str(r["company_id"])
        key = (fid, cid)
        cur = best.get(key)
        cand = (obs, int(r["id"]))
        if cur is None or cand > (cur[0], cur[1]):
            try:
                payload = json.loads(r["payload_json"])
            except (TypeError, ValueError):
                continue
            best[key] = (obs, int(r["id"]), payload)

    fids = {fid for fid, _cid in best}
    out: dict[int, dict[str, Any]] = {}
    for fid in fids:
        chosen = None
        for cid in _WIN_PROB_COMPANY_PRIORITY:
            chosen = best.get((fid, cid))
            if chosen is not None:
                break
        if chosen is None:
            continue
        obs, _id, payload = chosen
        flat = normalize_odds_payload(payload)
        h, d, a = flat.get("home"), flat.get("draw"), flat.get("away")
        if not all(isinstance(v, (int, float)) and v > MIN_ODDS for v in (h, d, a)):
            continue
        p_home, p_draw, p_away, overround = proportional_devig(h, d, a)
        if not (OVERROUND_BOUNDS[0] <= overround <= OVERROUND_BOUNDS[1]):
            continue
        out[fid] = {
            "p_home": round(p_home, 4),
            "p_draw": round(p_draw, 4),
            "p_away": round(p_away, 4),
            "observed_at": obs,
        }
    return out


# 与 _WIN_PROB_COMPANY_PRIORITY 同一优先级(Bet365 实时轮询优先于历史回填)。
_REAL_LINE_COMPANY_PRIORITY = ("8", "281")


def latest_market_line(
    conn_odds: sqlite3.Connection, fotmob_match_id: int, market: str
) -> dict[str, Any] | None:
    """单场单市场(market='ou' 大小球 / 'corners_ou' 角球大小)最新真实盘口线。

    只在真的抓到盘口时返回非 None,由调用方(market_cards.py)决定"没有就退回
    统计参考线",本函数不做兜底、不猜。company_id 优先级同
    latest_1x2_by_match(Bet365 实时轮询 '8' 优先于历史回填 '281')。
    """
    placeholders = ",".join("?" for _ in _REAL_LINE_COMPANY_PRIORITY)
    try:
        rows = conn_odds.execute(
            f"""SELECT b.id, b.company_id, b.payload_json, b.observed_at
                  FROM bronze_ng_odds_snap b
                  JOIN dim_match_xref x
                    ON x.provider_match_id = b.provider_match_id AND x.provider='nowgoal'
                 WHERE x.fotmob_match_id=? AND b.market=?
                   AND b.company_id IN ({placeholders})
                   AND x.review_status IN ('auto_ok','confirmed')""",
            (fotmob_match_id, market, *_REAL_LINE_COMPANY_PRIORITY),
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    best: dict[str, tuple[str, int, dict]] = {}
    for r in rows:
        cid = str(r["company_id"])
        cand_key = (str(r["observed_at"]), int(r["id"]))
        cur = best.get(cid)
        if cur is None or cand_key > (cur[0], cur[1]):
            try:
                payload = json.loads(r["payload_json"])
            except (TypeError, ValueError):
                continue
            best[cid] = (cand_key[0], cand_key[1], payload)

    for cid in _REAL_LINE_COMPANY_PRIORITY:
        chosen = best.get(cid)
        if chosen is None:
            continue
        obs, _id, payload = chosen
        flat = normalize_odds_payload(payload)
        line = flat.get("line")
        if not isinstance(line, (int, float)):
            continue
        return {"line": float(line), "company_id": cid, "observed_at": obs}
    return None


# admin「每日精选」录入用(2026-08-14):与其让 admin 手打赔率数字,不如直接从
# 已经抓到的真实盘口里选——只覆盖三个"选项一句话能说清楚"的市场,AH 的让球
# 方向(主队让/受让)容易记反且历史上出过反向 bug(见 nowgoal.py 主客反转归一
# 注释),不纳入自动选项;这三个市场之外或没有真实数据时,admin 仍手动填写。
_OPTION_MARKETS = ("1x2", "ou", "corners_ou")
_OPTION_MARKET_LABEL_ZH = {"1x2": "胜平负", "ou": "大小球", "corners_ou": "角球大小"}


def _market_option_selections(
    market: str, flat: dict[str, Any]
) -> list[tuple[str, float, str, float | None]]:
    """单个市场的扁平赔率 → (选项文案, 赔率, 程序可读 side, line) 列表;
    任一必需字段缺失/非数值则整市场跳过。

    side 是给 reco_legs.side 用的机器可读选边(home/draw/away/over/under)——
    admin 录入每日精选选中某个选项后,结算判定要用这个,不能反过来解析
    "主胜"/"大2.5" 这类中文展示文案。
    """
    if market == "1x2":
        h, d, a = flat.get("home"), flat.get("draw"), flat.get("away")
        if not all(isinstance(v, (int, float)) for v in (h, d, a)):
            return []
        return [
            ("主胜", float(h), "home", None),
            ("平局", float(d), "draw", None),
            ("客胜", float(a), "away", None),
        ]
    if market in ("ou", "corners_ou"):
        line, over, under = flat.get("line"), flat.get("over"), flat.get("under")
        if not all(isinstance(v, (int, float)) for v in (line, over, under)):
            return []
        unit = "角球" if market == "corners_ou" else ""
        return [
            (f"大{unit}{line}", float(over), "over", float(line)),
            (f"小{unit}{line}", float(under), "under", float(line)),
        ]
    return []


def raw_market_options(
    conn_odds: sqlite3.Connection, fotmob_match_id: int
) -> list[dict[str, Any]]:
    """单场三个市场(1x2/大小球/角球大小)的最新真实原始赔率选项。

    不去水、不算胜率——原样透出该公司最新一口价与公司名(admin 需要的是
    "这个数字来自哪家公司的真实报价",不是研究用的隐含概率)。公司优先级同
    latest_1x2_by_match(Bet365 实时轮询 '8' 优先于历史回填 '281')。没有真实
    数据时返回空列表,由调用方(admin 端点)决定退回手动输入,不在这里编造。
    """
    placeholders_m = ",".join("?" for _ in _OPTION_MARKETS)
    placeholders_c = ",".join("?" for _ in _WIN_PROB_COMPANY_PRIORITY)
    try:
        rows = conn_odds.execute(
            f"""SELECT b.id, b.market, b.company_id, b.company_name, b.payload_json, b.observed_at
                  FROM bronze_ng_odds_snap b
                  JOIN dim_match_xref x
                    ON x.provider_match_id = b.provider_match_id AND x.provider='nowgoal'
                 WHERE x.fotmob_match_id=? AND b.market IN ({placeholders_m})
                   AND b.company_id IN ({placeholders_c})
                   AND x.review_status IN ('auto_ok','confirmed')""",
            (fotmob_match_id, *_OPTION_MARKETS, *_WIN_PROB_COMPANY_PRIORITY),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    best: dict[tuple[str, str], tuple[str, int, dict, str]] = {}
    for r in rows:
        key = (str(r["market"]), str(r["company_id"]))
        cand_key = (str(r["observed_at"]), int(r["id"]))
        cur = best.get(key)
        if cur is None or cand_key > (cur[0], cur[1]):
            try:
                payload = json.loads(r["payload_json"])
            except (TypeError, ValueError):
                continue
            best[key] = (cand_key[0], cand_key[1], payload, str(r["company_name"]))

    options: list[dict[str, Any]] = []
    for market in _OPTION_MARKETS:
        chosen = None
        for cid in _WIN_PROB_COMPANY_PRIORITY:
            chosen = best.get((market, cid))
            if chosen is not None:
                break
        if chosen is None:
            continue
        obs, snap_id, payload, company_name = chosen
        # cid 是上面 break 时命中的公司 id(_WIN_PROB_COMPANY_PRIORITY 遍历顺序),
        # 循环变量在 break 后仍保留该值,直接复用,不用再反查一次。
        flat = normalize_odds_payload(payload)
        # 新鲜度(2026-08-16):复用既有 classify_odds_freshness/
        # ODDS_FRESHNESS_STALE_HOURS(不新发明阈值),与其它新鲜度展示位
        # (MatchRow.tsx 等)统一使用同一套 FRESH/STALE/UNAVAILABLE 三态词汇,
        # 而不是另造一个 is_stale 布尔——这里的 observed_at 恒非空(真实
        # 报价快照才会进 options),UNAVAILABLE 分支理论上不会命中,但沿用
        # 三态而非砍成二态,是为了让前端只需认识一套新鲜度词汇。
        freshness = classify_odds_freshness(obs)
        for selection, odds, side, line in _market_option_selections(market, flat):
            options.append({
                "market": market,
                "market_label": _OPTION_MARKET_LABEL_ZH[market],
                "selection": selection,
                "odds": round(odds, 2),
                "company_name": company_name,
                "observed_at": obs,
                "snapshot_id": snap_id,
                "company_id": cid,
                "line": line,
                "side": side,
                "odds_format": MARKET_ODDS_FORMAT[market],
                "freshness": freshness,
            })
    return options
