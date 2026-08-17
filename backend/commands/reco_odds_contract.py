"""「每日精选」赔率合约:港赔(HK odds)/十进制赔率的显式换算(CLAUDE.md §9.1)。

背景(2026-08-16,已用真实数据确认的财务结算 bug):reco_legs.odds 一直被
settle_slip() 当十进制赔率直接相乘,但 NowGoal 的 ou(大小球)/ah(让球)/
corners_ou(角球大小)三个市场给的是**港赔**,数值经常 <1(如 0.83、1.03),
和十进制赔率(必须 >1)是完全不同的数字含义。

真实样本证据(2026-08-16,data/odds.db 只读查询确认):
- market='1x2' payload,如 {"initial":{"away":3.0,"draw":3.6,"home":2.1},...}
  ——值全部 >1,是十进制赔率,不转换(backend/queries/odds.py 的
  latest_1x2_by_match() 已经在生产用这套值直接做比例去水,且有
  overround∈[1.01,1.35] 的校验,真实数据能通过,进一步证实是十进制)。
- market='ou' payload,如
  {"initial":{"line":3.0,"over":1.0,"under":0.8},
   "latest":{"line":2.75,"over":0.9,"under":0.9}}
  ——over/under 大量 <1(0.78~1.08 区间),标准港盘记法。
- market='ah' payload,如
  {"initial":{"away":0.95,"home":0.85,"line":0.25},
   "latest":{"away":1.03,"home":0.78,"line":0.25}}——同样港盘记法。
- market='corners_ou' payload 同 ou 记法,同样港盘。

标准港盘换算(单一真源,不在其它模块重复实现):
十进制赔率 = 港盘数值 + 1.0(港盘只表示纯利润倍数,不含本金;十进制赔率
含本金)。例如港盘 1.03 → 十进制 2.03(不是 1.03);港盘 0.83 → 十进制 1.83。
"""

from __future__ import annotations

# 市场 -> 该市场来源赔率的真实格式,由 provider parser(backend/providers/
# nowgoal.py)显式声明,不由数值大小猜——1x2 的十进制赔率理论上也可能出现
# <1 的畸变脏数据,格式判定不能靠"看这个数字长得像不像"。
MARKET_ODDS_FORMAT: dict[str, str] = {
    "1x2": "decimal",
    "ou": "hk",
    "ah": "hk",
    "corners_ou": "hk",
}

_VALID_FORMATS = {"decimal", "hk"}


def hk_to_decimal(hk_odds: float) -> float:
    """标准港盘转十进制:十进制 = 港盘 + 1.0。

    港盘(Hong Kong odds)只表示相对本金的纯利润倍数,不含本金本身;
    十进制赔率(decimal odds)含本金,是"总回报 = 本金 × 十进制赔率"里的
    那个乘数。二者相差恰好 1.0 个本金单位。
    """
    return hk_odds + 1.0


def to_canonical_decimal(source_odds: float, odds_format: str | None) -> float:
    """source_odds + 显式 odds_format -> 真正参与结算乘法的十进制赔率。

    odds_format 必须显式传入且只能是 'decimal' 或 'hk',不接受默认值/None——
    赔率格式来自 provider 的显式声明,不允许在这里"猜"一个可能算错的格式
    (CLAUDE.md:宁可 fail-closed,也不能猜一个可能算错的结果)。
    """
    if odds_format not in _VALID_FORMATS:
        raise ValueError(
            f"非法 odds_format: {odds_format!r}(必须是 {sorted(_VALID_FORMATS)} 之一,"
            " 不接受默认值/猜测)"
        )
    if odds_format == "hk":
        return hk_to_decimal(source_odds)
    return source_odds
