"""backend/metrics/registry.py 结构校验——不重新验证数据库覆盖率(那是
backend/scripts/recompute_metric_coverage.py 的职责),只保证每条指标定义
本身完整、语义合法,不会有"声明了但缺关键字段"的半成品条目流入图表层。
"""

from __future__ import annotations

from backend.metrics.registry import REGISTRY, MetricDef, get_metric, metrics_by_semantic
from backend.silver.ratio_metrics import RATIO_SPECS


def test_registry_non_empty():
    assert len(REGISTRY) > 0


def test_every_metric_key_matches_its_canonical_key():
    for key, m in REGISTRY.items():
        assert key == m.canonical_key, f"{key} 字典键与 canonical_key 不一致"


def test_every_metric_has_required_text_fields_non_empty():
    for key, m in REGISTRY.items():
        assert m.name_zh, f"{key} 缺中文名"
        assert m.explanation_zh, f"{key} 缺一句话解释"
        assert m.numerator, f"{key} 缺 numerator"
        assert m.unit, f"{key} 缺单位"
        assert m.missing_policy, f"{key} 缺缺失策略说明"
        assert m.coverage_note, f"{key} 缺覆盖率说明(不许凭空声明指标)"
        assert m.source_field, f"{key} 缺来源字段"
        assert m.methodology_version, f"{key} 缺方法论版本号"


def test_direction_is_one_of_allowed_values():
    allowed = {"higher_better", "lower_better", "style_only"}
    for key, m in REGISTRY.items():
        assert m.direction in allowed, f"{key} direction={m.direction} 不合法"


def test_semantic_is_one_of_four_allowed_classes():
    allowed = {"performance", "style", "outcome_variance", "unavailable"}
    for key, m in REGISTRY.items():
        assert m.semantic in allowed, f"{key} semantic={m.semantic} 不合法"


def test_style_direction_implies_style_semantic():
    """direction=style_only 的指标不能被标成 performance——那等于把"打法
    描述"包装成"优劣排名",违反措辞纪律(不代表强弱,只能写偏向/较多)。"""
    for key, m in REGISTRY.items():
        if m.direction == "style_only":
            assert m.semantic == "style", f"{key} 是 style_only 方向却标成 {m.semantic}"


def test_outcome_variance_metrics_have_short_window_caveat_in_explanation():
    """outcome_variance 类指标的解释文案必须能看出"这是短期窗口结果,不是
    稳定能力"这层意思——不能只写数值定义,不然前端展示时容易被误读成
    可持续的能力评价。"""
    for m in metrics_by_semantic("outcome_variance"):
        assert "短期" in m.explanation_zh or "不是" in m.explanation_zh, (
            f"{m.canonical_key} 的解释文案没有短期/非稳定能力的诚实提示"
        )


def test_ratio_metrics_declare_denominator_others_do_not():
    """numerator/denominator 都是比率型指标(如传球成功率)才应该有 denominator;
    直接计数/均值型指标(如射门次数)denominator 必须是 None,不能塞一个假分母。"""
    ratio_keys = {"pass_completion", "opp_half_pass_share", "situation_shots_for", "situation_shots_against"}
    for key, m in REGISTRY.items():
        if key in {"pass_completion", "opp_half_pass_share"}:
            assert m.denominator is not None, f"{key} 应该是比率型指标,denominator 不该是 None"


def test_get_metric_raises_for_unknown_key():
    try:
        get_metric("this_key_does_not_exist")
    except KeyError:
        pass
    else:
        raise AssertionError("get_metric 对未声明的 key 必须抛错,不能静默回退")


def test_field_tilt_naming_red_line():
    """opp_half_pass_share 的解释文案必须明确声明它不是 Opta/StatsBomb 的
    正式 Field Tilt,只是本站自己算的代理指标——这是方案 §三 图2 的命名红线,
    写进永久测试防止未来改文案时不小心删掉这句免责声明。"""
    m = get_metric("opp_half_pass_share")
    assert "Field Tilt" in m.explanation_zh
    assert "代理" in m.explanation_zh


def test_metric_def_is_frozen_dataclass_instance():
    for m in REGISTRY.values():
        assert isinstance(m, MetricDef)


def test_every_ratio_spec_is_registered_with_a_denominator():
    """球队象限图的比值型指标(backend/silver/ratio_metrics.py::RATIO_SPECS)
    必须先在这里登记才能在 silver 里算——这是本文件自称的目的("不把数据库
    字段直接推到页面"),这条测试是它第一次获得真正的运行时接线:没登记就
    不能进 RATIO_SPECS,denominator 缺失说明它其实不是比率型指标(该走
    columnMetric 而不是 ratio)。"""
    for spec in RATIO_SPECS:
        m = get_metric(spec.metric_key)  # 找不到直接抛 KeyError,不允许静默跳过
        assert m.denominator is not None, (
            f"{spec.metric_key} 在 RATIO_SPECS 里是比率型,但 registry 里 denominator 是 None"
        )


def test_def_action_density_never_claims_to_be_ppda():
    """防守动作密度是"每百次对手传球"的代理指标,没有动作坐标、无法限定
    逼抢区域,铁桶阵与高位压迫会拿到同样的分数——绝不能"自称"PPDA(照抄
    test_field_tilt_naming_red_line 的模式)。文案里出现"不是 PPDA"这种
    否定式免责声明是允许的、甚至是要求的——红线是"敢不敢自称",不是
    "敢不敢提及"这四个字母。"""
    for m in REGISTRY.values():
        if "防守动作密度" in m.name_zh or "def_action_density" in m.canonical_key:
            assert "PPDA" not in m.name_zh
            if "PPDA" in m.explanation_zh:
                assert "不是 PPDA" in m.explanation_zh or "不是PPDA" in m.explanation_zh
