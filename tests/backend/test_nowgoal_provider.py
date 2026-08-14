"""P0.7 测试:NowGoal / FotMob Provider Adapter 纯解析函数。

fixture 数据放 tests/fixtures/nowgoal/。type=6(日程)与 type=14(赔率,
odds_sample_real_shape.json)结构均已在 2026-07-21 瑞典超接入实验中用真实
titan_id 交叉验证过(见 docs/data-sources.md);odds_sample.json/poll_fixture.json
是仍受支持的旧构造形状(companies 直接列表 + name 字段),parse_odds() 对两种
形状都兼容。公司覆盖范围(除 Bet365/Sbobet 外)与历史回填仍 UNVERIFIED。
"""

import json
from pathlib import Path

import pytest

from backend.db.util import sha256_hex
from backend.providers import fotmob_snapshots, nowgoal

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nowgoal"


def _schedule_text() -> str:
    return (FIXTURES / "schedule_sample.txt").read_text(encoding="utf-8")


def _odds_payload() -> dict:
    return json.loads((FIXTURES / "odds_sample.json").read_text(encoding="utf-8"))


# ── 日程解析 ─────────────────────────────────────────────────────────────


class TestParseSchedule:
    def test_parses_match_lines_only(self):
        rows = nowgoal.parse_schedule(_schedule_text())
        # A[4](缺客队)与 A[5](主队名为空)剔除;L[/V=/B[ 行忽略
        assert [r["titan_id"] for r in rows] == ["2549883", "2549884", "2549885"]

    def test_field_extraction(self):
        rows = nowgoal.parse_schedule(_schedule_text())
        first = rows[0]
        assert first["home_name"] == "Arsenal"
        assert first["away_name"] == "Chelsea"
        # fixture A[1] 的 JS Date 元组 '2026,6,19,19,30,00'(月份 0 基)→ 2026-07-19 19:30:00
        assert first["kickoff"] == "2026-07-19 19:30:00"
        assert first["raw_line"].startswith("A[1]=")

    def test_chinese_team_name(self):
        rows = nowgoal.parse_schedule(_schedule_text())
        assert rows[2]["home_name"] == "阿森纳"

    def test_empty_input(self):
        assert nowgoal.parse_schedule("") == []
        assert nowgoal.parse_schedule("garbage without quotes") == []


# ── 赔率解析 ─────────────────────────────────────────────────────────────


class TestParseOdds:
    def test_target_cids_selected(self):
        # 目标三家 = Bet365(8)/澳门(1)/皇冠(3);fixture 含 8/1/3 → 全选
        records = nowgoal.parse_odds(_odds_payload())
        cids = {r["company_id"] for r in records}
        assert cids == {"8", "1", "3"}
        markets_8 = sorted(r["market"] for r in records if r["company_id"] == "8")
        assert markets_8 == ["1x2", "ah", "ou"]

    def test_field_mapping_and_values(self):
        records = nowgoal.parse_odds(_odds_payload())
        r_1x2 = next(r for r in records if r["company_id"] == "8" and r["market"] == "1x2")
        assert r_1x2["initial"] == {"home": 2.10, "draw": 3.40, "away": 3.50}
        assert r_1x2["latest"] == {"home": 2.05, "draw": 3.40, "away": 3.60}
        r_ah = next(r for r in records if r["company_id"] == "8" and r["market"] == "ah")
        assert r_ah["initial"] == {"home": 0.95, "line": -0.5, "away": 0.93}
        r_ou = next(r for r in records if r["company_id"] == "8" and r["market"] == "ou")
        assert r_ou["latest"] == {"over": 0.85, "line": 2.75, "under": 1.01}

    def test_empty_groups_dropped(self):
        records = nowgoal.parse_odds(_odds_payload())
        # cid=1(澳门,目标公司):euro 最新组全空 → latest=None 但记录保留;
        # ah 两组全空 → 整行剔除
        rec_1 = [r for r in records if r["company_id"] == "1"]
        assert [r["market"] for r in rec_1] == ["1x2"]
        assert rec_1[0]["initial"] == {"home": 2.15, "draw": 3.35, "away": 3.45}
        assert rec_1[0]["latest"] is None

    def test_no_fallback_when_targets_absent(self):
        """目标公司都缺席 → 返回空,**不回退**拿别家冒充(用户要求'没有就不抓')。"""
        # 只留一个非目标公司(cid=50,1xBet)
        payload = {"companies": [
            {"cid": "50", "cn": "1xBet",
             "euro": {"f": {"u": "2.0", "g": "3.0", "d": "3.5"},
                      "l": {"u": "2.0", "g": "3.0", "d": "3.5"}}}]}
        records = nowgoal.parse_odds(payload)
        assert records == []   # 绝不冒充

    def test_no_companies(self):
        assert nowgoal.parse_odds({}) == []
        assert nowgoal.parse_odds({"companies": []}) == []


class TestParseOddsRealShape:
    """真实响应结构回归测试(2026-07-21,瑞典超接入实验中 titan_id=2912218 真实
    probe 首次验证 type=14):顶层是 {ErrCode, Data:{mixodds:[...]}, MatchState},
    不是旧项目审计构造假设的 {companies:[...]}；公司名字段是 'cn' 不是 'name'。

    两个真实 bug(此前完全 UNVERIFIED,首次真实探测才发现):
      1. _iter_companies 原来只会把 Data 当成"字典转列表"(list(d.values())),
         取不到 Data.mixodds 这一层,导致真实响应下 parse_odds 恒返回空列表；
      2. _company_records 原来只认 'name'/'company' 字段,真实响应用 'cn',
         导致 company_name 恒为空字符串。
    fixture 数值本身是脱敏占位,不是真实盘口——只用于锁定字段名与嵌套层级。
    """

    def _payload(self) -> dict:
        return json.loads((FIXTURES / "odds_sample_real_shape.json").read_text(encoding="utf-8"))

    def test_reaches_mixodds_nested_under_data(self):
        records = nowgoal.parse_odds(self._payload())
        cids = {r["company_id"] for r in records}
        # 真实存档含 8(Bet365)/31(Sbobet)/50(1xBet);目标三家=8/1/3,
        # 只有 8 命中(1/3 不在此存档),31/50 非目标不选
        assert cids == {"8"}
        assert records, "真实结构下 parse_odds 不应返回空列表(回归此前 0 条的 bug)"

    def test_company_name_from_cn_field(self):
        # 目标三家=8/1/3;真实存档只有 8 命中(31/50 非目标)。cn 字段提取的回归点在 8。
        records = nowgoal.parse_odds(self._payload())
        names = {r["company_id"]: r["company_name"] for r in records}
        assert names["8"] == "Bet365"

    def test_field_values_correct_under_real_shape(self):
        records = nowgoal.parse_odds(self._payload())
        r_1x2 = next(r for r in records if r["company_id"] == "8" and r["market"] == "1x2")
        assert r_1x2["initial"] == {"home": 2.10, "draw": 3.40, "away": 3.50}
        assert r_1x2["latest"] == {"home": 2.05, "draw": 3.40, "away": 3.60}

    def test_legacy_companies_shape_still_supported(self):
        """companies 直接列表 + name 字段(旧项目审计构造)不能因这次修复回归。"""
        records = nowgoal.parse_odds(_odds_payload())
        assert {r["company_id"] for r in records} == {"8", "1", "3"}


# ── 主客反转 ─────────────────────────────────────────────────────────────


class TestInversion:
    def _record(self, market, initial, latest):
        return {
            "market": market, "company_id": "8", "company_name": "Bet365",
            "initial": initial, "latest": latest,
        }

    def test_1x2_swaps_home_away(self):
        rec = self._record("1x2", {"home": 2.0, "draw": 3.0, "away": 4.0},
                           {"home": 1.9, "draw": 3.1, "away": 4.2})
        out = nowgoal.normalize_for_inversion(rec, inverted=True)
        assert out["initial"] == {"home": 4.0, "draw": 3.0, "away": 2.0}
        assert out["latest"] == {"home": 4.2, "draw": 3.1, "away": 1.9}

    def test_ah_swaps_sides_and_negates_line(self):
        rec = self._record("ah", {"home": 0.95, "line": -0.5, "away": 0.93},
                           {"home": 0.90, "line": 0.25, "away": 0.98})
        out = nowgoal.normalize_for_inversion(rec, inverted=True)
        assert out["initial"] == {"home": 0.93, "line": 0.5, "away": 0.95}
        assert out["latest"] == {"home": 0.98, "line": -0.25, "away": 0.90}

    def test_ou_unchanged(self):
        rec = self._record("ou", {"over": 0.9, "line": 2.5, "under": 0.96}, None)
        out = nowgoal.normalize_for_inversion(rec, inverted=True)
        assert out["initial"] == {"over": 0.9, "line": 2.5, "under": 0.96}
        assert out["latest"] is None

    def test_not_inverted_returns_independent_copy(self):
        rec = self._record("1x2", {"home": 2.0, "draw": 3.0, "away": 4.0}, None)
        out = nowgoal.normalize_for_inversion(rec, inverted=False)
        assert out == rec
        out["initial"]["home"] = 999
        assert rec["initial"]["home"] == 2.0


# ── canonical JSON / hash 与 WAF 检测 ────────────────────────────────────


class TestCanonicalAndWaf:
    def test_canonical_json_stable_across_key_order(self):
        a = {"latest": {"home": 2.0, "away": 3.0}, "initial": None}
        b = {"initial": None, "latest": {"away": 3.0, "home": 2.0}}
        assert nowgoal.canonical_payload_json(a) == nowgoal.canonical_payload_json(b)
        assert sha256_hex(nowgoal.canonical_payload_json(a)) == sha256_hex(
            nowgoal.canonical_payload_json(b)
        )

    def test_looks_blocked(self):
        assert nowgoal.looks_blocked("<title>Just a moment...</title>")
        assert nowgoal.looks_blocked("checking your browser - Cloudflare")
        assert not nowgoal.looks_blocked("V=1;\nA[1]=[123,'a',36,'b'];")
        assert not nowgoal.looks_blocked("")


# ── FotMob 快照提取 ──────────────────────────────────────────────────────


def _fm_payload():
    return {
        "content": {
            "lineup": {
                "homeTeam": {
                    "id": 9825,
                    "formation": "4-3-3",
                    "starters": [{"id": 20, "name": "B"}, {"id": 10, "name": "A"}],
                    "subs": [{"id": 30, "name": "C"}],
                    "unavailable": [
                        {"id": 90, "name": "Injured One",
                         "unavailability": {"type": "injury", "expectedReturn": "2026-08-01"}},
                    ],
                },
                "awayTeam": {
                    "id": 8455,
                    "formation": "4-4-2",
                    "starters": [{"id": 40, "name": "D"}],
                    "subs": [],
                    "unavailable": [],
                },
            }
        }
    }


class TestFotmobSnapshots:
    def test_lineup_minimal_subset_sorted(self):
        snap = fotmob_snapshots.extract_lineup_snapshot(_fm_payload())
        assert snap["home"]["team_id"] == 9825
        assert snap["home"]["formation"] == "4-3-3"
        assert [p["id"] for p in snap["home"]["starters"]] == [10, 20]
        assert snap["away"]["subs"] == []

    def test_lineup_hash_stable_regardless_of_player_order(self):
        p1, p2 = _fm_payload(), _fm_payload()
        p2["content"]["lineup"]["homeTeam"]["starters"].reverse()
        s1 = fotmob_snapshots.extract_lineup_snapshot(p1)
        s2 = fotmob_snapshots.extract_lineup_snapshot(p2)
        assert nowgoal.canonical_payload_json(s1) == nowgoal.canonical_payload_json(s2)

    def test_lineup_missing_gives_empty_sides(self):
        snap = fotmob_snapshots.extract_lineup_snapshot({"content": {}})
        assert snap["home"]["team_id"] is None
        assert snap["home"]["starters"] == []

    def test_sideline_extraction(self):
        snap = fotmob_snapshots.extract_sideline_snapshot(_fm_payload(), 9825)
        assert snap["team_id"] == 9825
        assert snap["sidelined"] == [
            {"id": 90, "name": "Injured One", "reason": "injury",
             "expected_return": "2026-08-01"}
        ]

    def test_sideline_empty_for_clean_team_and_unknown_team(self):
        assert fotmob_snapshots.extract_sideline_snapshot(_fm_payload(), 8455)["sidelined"] == []
        assert fotmob_snapshots.extract_sideline_snapshot(_fm_payload(), 12345)["sidelined"] == []

    def test_fetch_requires_proxy_env(self, monkeypatch):
        monkeypatch.delenv("THORDATA_PROXY", raising=False)
        # FotMobClient 以 ``from dotenv import load_dotenv`` 持有模块别名；
        # 必须 patch 实际调用点，避免全量测试前序加载 .env 后污染本用例。
        monkeypatch.setattr(
            "backend.fotmob_client.load_dotenv",
            lambda *a, **k: None,
        )
        with pytest.raises(RuntimeError, match="THORDATA_PROXY"):
            fotmob_snapshots.fetch_match_payload(4193490)
