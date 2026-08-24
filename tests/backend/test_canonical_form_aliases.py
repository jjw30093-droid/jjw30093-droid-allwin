"""跨源品牌书写差异归一化别名(2026-08-24,修复 36 场比赛零赔率事故的系统性部分)。

NowGoal 的队名书写与 FotMob 不一致("AC Milan" vs "Milan"、"Groningen" vs
"FC Groningen"),而 auto_ok 要求两边都精确命中别名表,seed_team_aliases 又只从
FotMob 侧播种——纯品牌书写差异会导致 auto_ok 永远够不到,卡进 needs_review 后
(此前)永不重新评估。本测试钉住:归一化规则的实测边界(23 个真实失败队名对修好、
10 个不可约的绝不误判为相等)、撞名 fail-closed、不改 _norm 语义、幂等。
"""

import pytest

from backend.db.connections import connect_rw
from backend.ingest.entity_resolution import (
    _canonical_form,
    _norm,
    seed_canonical_form_aliases,
)


@pytest.fixture
def odds(data_dir):
    from backend.db import migrate
    migrate.apply_all("odds", quiet=True)


# (FotMob 写法, NowGoal 写法) —— 2026-08-24 生产库真实卡死队名对,逐条用真实
# NowGoal schedule 接口核实过配对本身正确,只是书写差异导致别名精确匹配落空。
FIXED_PAIRS = [
    ("Roma", "AS Roma"),
    ("Jeju SK", "Jeju SK FC"),
    ("Gwangju FC", "Gwangju Football Club"),
    ("Barcelona", "FC Barcelona"),
    ("FC Groningen", "Groningen"),
    ("Milan", "AC Milan"),
    ("Paris Saint-Germain", "Paris Saint Germain (PSG)"),
    ("Gimcheon Sangmu", "Gimcheon Sangmu FC"),
    ("Elversberg", "SV Elversberg"),
    ("Mainz 05", "FSV Mainz 05"),
    ("Paderborn", "SC Paderborn 07"),
    ("Queens Park Rangers", "Queens Park Rangers (QPR)"),
    ("Excelsior", "Excelsior SBV"),
    ("Arouca", "FC Arouca"),
    ("Auxerre", "AJ Auxerre"),
    ("Jeonbuk Hyundai Motors FC", "Jeonbuk Hyundai Motors"),
    ("Brighton & Hove Albion", "Brighton Hove Albion"),
    ("Telstar", "SC Telstar"),
    ("Ajax", "AFC Ajax"),
    ("Tromsø", "Tromso IL"),
    ("Cambuur", "SC Cambuur"),
    ("Famalicao", "FC Famalicao"),
    ("Sunderland", "Sunderland A.F.C"),
]

# 不可约:一边带限定词/昵称、另一边没有,任何安全算法都不该把它们判成相等
# ——这些交给 backend/ingest/provider_alias_overrides.py 人工表。
IRREDUCIBLE_PAIRS = [
    ("Daejeon Hana Citizen", "Daejeon Citizen"),
    ("Athletic Club", "Athletic Bilbao"),
    ("West Bromwich Albion", "West Bromwich(WBA)"),
    ("Wolverhampton Wanderers", "Wolves"),
    ("Academico Viseu", "Viseu"),
    ("Brest", "Stade Brestois"),
    ("Nacional", "Nacional da Madeira"),
    ("FC Twente", "FC Twente Enschede"),
    ("Inter", "Inter Milan"),
    ("Internacional", "Internacional RS"),
]


class TestCanonicalForm:
    @pytest.mark.parametrize("fotmob_name,nowgoal_name", FIXED_PAIRS)
    def test_fixes_real_stuck_pairs(self, fotmob_name, nowgoal_name):
        assert _norm(fotmob_name) != _norm(nowgoal_name), "该对不该已经被 _norm 命中"
        assert _canonical_form(fotmob_name) == _canonical_form(nowgoal_name)
        assert _canonical_form(fotmob_name) != ""

    @pytest.mark.parametrize("fotmob_name,nowgoal_name", IRREDUCIBLE_PAIRS)
    def test_does_not_overreach_on_irreducible_pairs(self, fotmob_name, nowgoal_name):
        """防止归一化规则以后被改激进,把真正不同的限定词/昵称误判成同一支队。"""
        assert _canonical_form(fotmob_name) != _canonical_form(nowgoal_name)

    def test_no_diacritic_no_affix_is_noop(self):
        assert _canonical_form("Arsenal") == "arsenal"

    def test_empty_input(self):
        assert _canonical_form(None) == ""
        assert _canonical_form("") == ""

    def test_pure_affix_name_falls_back_not_empty(self):
        """词缀剥完导致整串清空的极端情况(罕见)→ 回退未剥离形式,不产出空别名。"""
        assert _canonical_form("FC") == "fc"


class TestSeedCanonicalFormAliases:
    def test_seed_adds_canonical_form_alias(self, odds):
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (8564, 'ac milan', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        out = seed_canonical_form_aliases(conn)
        assert out["added"] == 1
        got = {r[0] for r in conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='milan'")}
        conn.close()
        assert got == {8564}

    def test_seed_strips_affix_from_fotmob_name(self, odds):
        """FotMob 侧带词缀、NowGoal 侧不带(如 "FC Groningen" vs "Groningen")——
        播种侧生成剥离后的短形式,让 NowGoal 的精确查询直接命中。"""
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (8674, 'fc groningen', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        seed_canonical_form_aliases(conn)
        got = {r[0] for r in conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='groningen'")}
        conn.close()
        assert got == {8674}

    def test_collision_with_existing_alias_rejected(self, odds):
        """"fc foo" 归一化成 "foo",但 "foo" 已作为另一支球队的原始别名存在——
        撞名,不得覆盖已有行、也不得新增歧义行。"""
        conn = connect_rw("odds")
        conn.executemany(
            "INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
            " VALUES (?, ?, 'dim_match', '2026-01-01T00:00:00Z')",
            [(111, "fc foo"), (222, "foo")])
        conn.commit()
        out = seed_canonical_form_aliases(conn)
        rows = conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='foo'"
        ).fetchall()
        conn.close()
        assert out["added"] == 0
        assert [r[0] for r in rows] == [222]  # 仍只有球队 222 原有的那一行

    def test_collision_across_two_new_forms_rejected(self, odds):
        """两支不同球队的原始别名归一化到同一个新形式 → 整串拒绝。"""
        conn = connect_rw("odds")
        conn.executemany(
            "INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
            " VALUES (?, ?, 'dim_match', '2026-01-01T00:00:00Z')",
            [(111, "fc foo"), (222, "ac foo")])
        conn.commit()
        out = seed_canonical_form_aliases(conn)
        n = conn.execute("SELECT COUNT(*) FROM dim_team_alias WHERE alias='foo'").fetchone()[0]
        conn.close()
        assert out["added"] == 0
        assert ("foo", [111, 222]) in out["rejected"]
        assert n == 0

    def test_idempotent(self, odds):
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (8564, 'ac milan', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        seed_canonical_form_aliases(conn)
        out2 = seed_canonical_form_aliases(conn)
        conn.close()
        assert out2["added"] == 0

    def test_does_not_touch_noop_aliases(self, odds):
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (100, 'arsenal', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        out = seed_canonical_form_aliases(conn)
        n = conn.execute("SELECT COUNT(*) FROM dim_team_alias WHERE canonical_team_id=100").fetchone()[0]
        conn.close()
        assert out["added"] == 0 and n == 1
