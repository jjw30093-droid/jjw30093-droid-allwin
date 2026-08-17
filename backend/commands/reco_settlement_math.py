"""「每日精选」推荐单单腿结算判定——纯函数(CLAUDE.md §9.1 适用范围修订:
本模块只服务人工推荐板块的自动结算候选计算,不改动模型预测登记簿的任何锁定/
留痕规则)。

设计原则(2026-08-16):这是公开战绩页命中/未中判定的唯一算术真源,算错方向
或算错四分之一盘口拆分是 P0 数据诚实性问题。因此:

- 不查数据库、不做网络请求、无副作用——所有输入都是调用方已经查好的具体数值,
  便于用手算过的真实数字做单元测试锁定行为;
- 任何"数据不足"或"符号约定未经证实"的情况一律抛 `SettlementUnresolvable`,
  由调用方(下一阶段的 reco_auto_settle 任务)捕获后诚实记录"这条腿跳过自动
  结算,原因是 X",绝不吞掉异常猜一个结果;
- 只覆盖 `entry_type='provenance_bound'` 且 `match_id` 非空的腿(`legacy_manual`
  天然没有可靠 market/line/side,调用方按"没有 match_id/line 全部为 None 时
  必然落入 SettlementUnresolvable"的方式自然排除,本模块不需要专门识别
  entry_type)。

── 让球盘(ah)符号约定:已验证,line>0 = 主队让球(主队是热门) ──────────────

`line` 字段来自 `bronze_ng_odds_snap`(经 `backend/providers/nowgoal.py`
`_FIELD_MAP["ah"] = {"u": "home", "g": "line", "d": "away"}` 解析,`reco_legs.line`
直接透传自该字段,未做任何符号变换),符号约定此前在本模块新增之前**没有专门
针对这张实时表的文档**,因此本次改动用两条独立证据链交叉验证,而不是凭经验假设:

1. **本次新增的真实数据交叉验证**(2026-08-16,`data/odds.db` + `data/allwin.db`
   只读查询,脚本见任务交付记录,未提交仓库):
   - 用同一场比赛的 `market='1x2'` 赔率(home 十进制赔率 < away 十进制赔率 ⇒
     主队是独立信号源判定的"热门")与该场 `market='ah'` 的 `line` 符号做交叉对照;
   - 两轮独立取样(同公司精确配对 48 组/23 场;任意公司每场取一条 24 场)
     **全部 100% 一致**:`line>0` 时主队恒是 1x2 热门,`line<0` 时客队恒是热门,
     零反例(如 Bodø/Glimt 客场对 Vålerenga,line=-1.25,1x2 客队热门,真实
     结果 1-2 客队小胜——热门让 1.25 球只赢 1 球,不完全符合,是正常的"热门
     未打出盘口"场景,不是符号错误);
   - 市场效率残差检验(`margin - line` vs `margin + line` 在 48 个样本上的
     标准差 1.55 vs 1.91)与"|line|≥1.0 时,用 `margin-line` 公式两边覆盖率
     接近 50/50(7:8)"均支持 `adjusted_home_margin = margin - line` 是正确公式,
     不是反过来。
2. **仓库里已经存在、独立于本次改动的三处历史文档**(不同 CLI 脚本,不同批次
   人工核实,方法与本次新增验证相互独立):
   - `backend/cli/ingest_legacy_odds.py`(2026-08-06):
     "统一后的 canonical 约定(与 `bronze_ng_odds_snap` 一致):
     ah line>0 = 主队让球(主队是热门)"——**明确声明与本表(`bronze_ng_odds_snap`)
     同一套约定**;
   - `backend/cli/ingest_jka_legacy_odds.py`(2026-08-07):"AH 线符号 = 本表
     canonical(line>0=主队让球):同场同期 1x2 热门方向交叉验证 **2,834 组
     agree / 1 组 borderline**"——与本次方法相同(用 1x2 热门方向反推 AH 符号),
     样本量比本次更大,结论一致;
   - `backend/cli/ingest_nowgoal_historical_odds.py`(2026-08-06):列出 6 场真实
     悬殊比分核对(曼联 9-0 南安普顿 g=+1.25、拜仁 8-0 沙尔克 g=+2.5、
     都灵 0-7 米兰 g=-0.75、水晶宫 0-7 利物浦 g=-0.75、维罗纳 0-6 国际米兰
     g=-1、布莱顿 6-0 狼队 g=+1),g 符号与真实大比分获胜方(热门)方向
     逐场一致,零反例。

结算公式(以主队视角的净胜球调整值判定):

    adjusted_home_margin = (home_score - away_score) - line

    side='home' 在 adjusted_home_margin > 0 时赢盘(需要克服 line 代表的让球数);
    side='away' 在 adjusted_home_margin < 0 时赢盘;
    整数盘 adjusted_home_margin == 0 时走水(push)。

半线(line 以 .5 结尾)不可能走水;四分之一线(line 以 .25/.75 结尾)拆成相邻
两个半仓(line-0.25 与 line+0.25,恰好一个整数、一个半数)分别判定再合并,
合并规则与 `backend/commands/reco.py::_leg_return_multiplier` 的既有乘数定义
完全自洽(half_win 乘数 (odds+1)/2 = win 与 push 乘数的算术平均;half_loss
乘数 0.5 = lose(0) 与 push(1.0) 乘数的算术平均)——本模块只产出"半仓构成"这个
事实判断本身,不重复实现乘数计算。

若证据不足以支持某个市场/字段组合,一律抛 `SettlementUnresolvable`,不猜测。
"""

from __future__ import annotations

import math

_RESULTS = frozenset({"win", "lose", "push", "half_win", "half_loss"})


class SettlementUnresolvable(Exception):
    """这条腿现在不能自动判定:数据不足、市场/选边不支持,或(理论上)符号约定
    未经证实。调用方必须 fail-closed——跳过这条腿的自动结算,不得吞掉异常后
    猜一个可能算错的 win/lose/push/half_win/half_loss。"""


def resolve_leg_result(
    market: str,
    line: float | None,
    side: str,
    *,
    home_score: int,
    away_score: int,
    home_corners: float | None = None,
    away_corners: float | None = None,
) -> str:
    """给定一场真实已完赛比赛的比分(+可选角球数)与推荐单某条腿的
    market/line/side,判定这条腿的结算结果。

    纯函数:不查数据库、无副作用。调用方负责只在"正式完赛、可结算"
    (status=='Finish' 且 home_score/away_score 均非 NULL)时调用本函数——
    本函数本身也做一次防御性 None 校验,但不负责判断比赛是否已经完赛。

    返回值 ∈ {'win','lose','push','half_win','half_loss'}。
    数据不足/市场或选边不支持/符号约定未证实时抛 `SettlementUnresolvable`。
    """
    if home_score is None or away_score is None:
        raise SettlementUnresolvable("home_score/away_score 缺失,无法判定")

    if market == "1x2":
        return _resolve_1x2(side, home_score, away_score)
    if market == "ou":
        if line is None:
            raise SettlementUnresolvable("ou 市场缺少 line,无法判定")
        total = home_score + away_score
        return _resolve_over_under(total, line, side)
    if market == "corners_ou":
        if line is None:
            raise SettlementUnresolvable("corners_ou 市场缺少 line,无法判定")
        if home_corners is None or away_corners is None:
            raise SettlementUnresolvable(
                "corners_ou 市场缺少角球统计(home_corners/away_corners),无法判定"
                "(不得拿 0 顶替缺失的统计数据)"
            )
        total = home_corners + away_corners
        return _resolve_over_under(total, line, side)
    if market == "ah":
        if line is None:
            raise SettlementUnresolvable("ah 市场缺少 line,无法判定")
        margin = home_score - away_score
        return _resolve_ah(margin, line, side)
    raise SettlementUnresolvable(f"不支持自动结算的市场: {market!r}")


# ── 1x2:胜平负,无 push/半仓 ────────────────────────────────────────────

_1X2_SIDES = frozenset({"home", "draw", "away"})


def _resolve_1x2(side: str, home_score: int, away_score: int) -> str:
    if side not in _1X2_SIDES:
        raise SettlementUnresolvable(f"1x2 非法 side: {side!r}(必须 home/draw/away)")
    if home_score > away_score:
        outcome = "home"
    elif away_score > home_score:
        outcome = "away"
    else:
        outcome = "draw"
    return "win" if side == outcome else "lose"


# ── ou / corners_ou:大小球,line 与"总量"比较 ──────────────────────────

_OU_HIGHER_WINS = {"over": True, "under": False}


def _resolve_over_under(total: float, line: float, side: str) -> str:
    if side not in _OU_HIGHER_WINS:
        raise SettlementUnresolvable(
            f"ou/corners_ou 非法 side: {side!r}(必须 over/under)"
        )
    return _line_result(total, line, _OU_HIGHER_WINS[side])


# ── ah:让球盘,line 与"主队净胜球"比较(符号约定见模块 docstring) ──────

_AH_HIGHER_WINS = {"home": True, "away": False}


def _resolve_ah(margin: float, line: float, side: str) -> str:
    if side not in _AH_HIGHER_WINS:
        raise SettlementUnresolvable(f"ah 非法 side: {side!r}(必须 home/away)")
    return _line_result(margin, line, _AH_HIGHER_WINS[side])


# ── 共享的"value vs line"判定 + 四分之一盘口拆分 ──────────────────────
#
# ou 的 value=总进球(或总角球)、ah 的 value=主队净胜球,两个市场的比较逻辑
# 结构完全相同:higher_side_wins=True 的一边在 value>line 时赢(ou 的 over、
# ah 的 home),higher_side_wins=False 的一边在 value<line 时赢(ou 的 under、
# ah 的 away),value==line 时走水。整线/半线/四分之一线三种粒度共用同一套
# 拆分与合并规则,不为 ou/ah 各写一份、避免两处慢慢漂移出不一致的结果。

_QUARTER_STEP = 0.25


def _line_result(value: float, line: float, higher_side_wins: bool) -> str:
    quarter_units = line / _QUARTER_STEP
    if not math.isclose(quarter_units, round(quarter_units), abs_tol=1e-6):
        raise SettlementUnresolvable(
            f"line={line} 不是 0.25 的整数倍,不是合法的亚洲盘口线,拒绝猜测判定"
        )
    granularity = round(quarter_units) % 4
    if granularity in (0, 2):
        # 整数盘(0)或半球盘(2 → .50):单次判定即可,push 只在整数盘可能出现。
        return _eval_single(value, line, higher_side_wins)

    # 四分之一盘(granularity 1 或 3,即 .25/.75):标准亚洲盘做法——拆成相邻
    # 两个半仓(一个整数、一个半数),各半仓独立判定,再按下方 _combine_quarter
    # 合并。
    low = line - _QUARTER_STEP
    high = line + _QUARTER_STEP
    r_low = _eval_single(value, low, higher_side_wins)
    r_high = _eval_single(value, high, higher_side_wins)
    return _combine_quarter(r_low, r_high)


def _eval_single(value: float, line: float, higher_side_wins: bool) -> str:
    if math.isclose(value, line, abs_tol=1e-9):
        return "push"
    return "win" if (value > line) == higher_side_wins else "lose"


def _combine_quarter(r_low: str, r_high: str) -> str:
    """两个半仓(各占一半本金)各自 win/lose/push 的结果 → 合并成整条腿的
    win/half_win/push/half_loss/lose。

    与 `reco.py::_leg_return_multiplier` 的乘数定义完全自洽:
    half_win 乘数 = (win 乘数 + push 乘数) / 2,half_loss 乘数 =
    (lose 乘数 + push 乘数) / 2——本函数只是这个"算术平均"关系在离散标签
    层面的对应表达。

    真正的四分之一盘(拆分出的两个半仓恰好相差 0.5,一个整数一个半数)理论上
    不可能同时出现 (win, lose) 这种"两个半仓完全反向"的组合——那只会在拆分
    出的两个 line 之间存在第三个可能取值时发生,而这要求 value 本身既不是
    整数也不是半数(例如角球总数出现异常的小数)。这种情况视为数据异常,
    拒绝猜测,交由调用方按"数据不足"处理。
    """
    if r_low == "win" and r_high == "win":
        return "win"
    if r_low == "lose" and r_high == "lose":
        return "lose"
    if r_low == "push" and r_high == "push":
        return "push"
    if {r_low, r_high} == {"win", "push"}:
        return "half_win"
    if {r_low, r_high} == {"lose", "push"}:
        return "half_loss"
    raise SettlementUnresolvable(
        f"四分之一盘口拆分结果异常(low={r_low}, high={r_high}):两个半仓完全"
        " 反向,通常意味着 value 不在整数/半数网格上(如角球总数出现非常规"
        " 小数),拒绝猜测判定"
    )
