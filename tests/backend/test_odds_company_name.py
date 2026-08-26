"""公司显示名读侧归一(backend.queries.odds.canonical_company_name)。

背景(2026-08-26 真实数据):bronze_ng_odds_snap.company_name 直接来自来源抓取,
同一家博彩公司在生产库里既有多个 company_id 也有多种拼写——利物浦 vs 伯恩利
(match 3411527)一场里就同时出现了 id '281'/"bet 365" 与 id '8'/"Bet365"、
id '80'/"macauslot" 与 id '1'/"Macauslot"。旧前端直接展示原始 company_name,
于是同一页面里 "bet 365" 和 "Bet365"、"macauslot" 和 "Macauslot" 并存,看起来
像 bug。

归一放在读侧(不改存储,§17):按 company_id 映射到统一品牌名。这里钉死:
- 两个 Bet365 id 都归一到 "Bet365";
- 两个 Macauslot id 都归一到 "Macauslot";
- 未登记的 id 绝不被吞掉,回退到来源名、再回退到 id 本身(诚实,不假装知道)。
"""

from __future__ import annotations

from backend.queries.odds import canonical_company_name


def test_bet365_two_ids_normalize_to_one_name():
    # id '8' 实时轮询写作 "Bet365",id '281' 历史回填写作 "bet 365" —— 同一家。
    assert canonical_company_name("8", "Bet365") == "Bet365"
    assert canonical_company_name("281", "bet 365") == "Bet365"


def test_macauslot_two_ids_normalize_to_one_name():
    assert canonical_company_name("1", "Macauslot") == "Macauslot"
    assert canonical_company_name("80", "macauslot") == "Macauslot"


def test_known_singletons_use_clean_latin_brand():
    # 保留 Latin 品牌名,不转成 "皇冠"/"平博" 这类博彩行话(非博彩定位)。
    assert canonical_company_name("177", "pinnacle") == "Pinnacle"
    assert canonical_company_name("3", "Crown") == "Crown"
    assert canonical_company_name("31", "Sbobet") == "Sbobet"


def test_int_company_id_accepted():
    # 路由传进来的 company_id 可能是 int 或 str,两者等价。
    assert canonical_company_name(8, "Bet365") == "Bet365"


def test_unknown_id_falls_back_to_source_name_then_id():
    # 未登记来源:不吞掉,回退到来源名。
    assert canonical_company_name("999", "SomeBook") == "SomeBook"
    # 来源名也缺失:回退到 id,不返回空串。
    assert canonical_company_name("999", "") == "999"
    assert canonical_company_name("999", None) == "999"
    assert canonical_company_name("999", "   ") == "999"
