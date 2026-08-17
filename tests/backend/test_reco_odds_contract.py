"""赔率合约模块测试(backend/commands/reco_odds_contract.py)。

直接复现用户报告的真实 bug:NowGoal 的 ou/ah/corners_ou 三个市场给的是
**港赔(HK odds)**,数值经常 <1(如 0.83、1.03),旧代码把它当十进制赔率直接
相乘,几乎不产生利润(1.03 应该对应十进制 2.03,接近翻倍)。

真实样本证据(2026-08-16,data/odds.db 只读查询确认,详见交付报告):
- market='1x2' payload 值均 >1(如 home=2.1/2.08,draw=3.6/3.26,away=3.0/3.17)
  ——十进制赔率,不转换。
- market='ou'/'ah'/'corners_ou' payload 值大量 <1(如 over=1.0/0.9/0.84,
  under=0.8/0.88/0.91,ah home=0.85/0.78/1.08)——标准港盘记法
  (十进制 = 港盘 + 1.0)。
"""

import pytest

from backend.commands.reco_odds_contract import (
    MARKET_ODDS_FORMAT,
    hk_to_decimal,
    to_canonical_decimal,
)


class TestHkToDecimal:
    def test_standard_conversion(self):
        assert hk_to_decimal(1.03) == pytest.approx(2.03)
        assert hk_to_decimal(0.83) == pytest.approx(1.83)
        assert hk_to_decimal(1.00) == pytest.approx(2.00)
        assert hk_to_decimal(0.0) == pytest.approx(1.0)


class TestToCanonicalDecimal:
    """用户报告原文数字:港盘 1.03 现在被当十进制直接相乘(几乎不产生利润),
    真实应该是十进制 2.03(接近翻倍)。"""

    def test_hk_1_03_becomes_decimal_2_03_not_1_03(self):
        assert to_canonical_decimal(1.03, "hk") == pytest.approx(2.03)

    def test_hk_0_83_becomes_decimal_1_83(self):
        assert to_canonical_decimal(0.83, "hk") == pytest.approx(1.83)

    def test_hk_1_00_becomes_legal_decimal_2_00(self):
        """旧 CHECK (odds > 1.0) 会错误拒绝合法港盘 1.00;canonical 转换后
        变成合法十进制 2.00,能通过下游 >1.0 的安全校验。"""
        canonical = to_canonical_decimal(1.00, "hk")
        assert canonical == pytest.approx(2.00)
        assert canonical > 1.0

    def test_decimal_market_not_converted(self):
        """1x2 是十进制市场,原样透传,不做 +1 转换。"""
        assert to_canonical_decimal(2.1, "decimal") == pytest.approx(2.1)

    def test_unknown_format_rejected(self):
        with pytest.raises(ValueError):
            to_canonical_decimal(1.5, "unknown")

    def test_none_format_rejected(self):
        with pytest.raises(ValueError):
            to_canonical_decimal(1.5, None)


class TestMarketOddsFormatDeclaration:
    """市场 -> 格式必须由 provider parser 显式声明,不是数值大小猜测——
    与真实数据库样本(见模块 docstring)对齐。"""

    def test_declared_formats(self):
        assert MARKET_ODDS_FORMAT == {
            "1x2": "decimal",
            "ou": "hk",
            "ah": "hk",
            "corners_ou": "hk",
        }
