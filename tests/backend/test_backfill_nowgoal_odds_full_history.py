from __future__ import annotations

import backend.cli.backfill_nowgoal_odds_full_history as cli


class _StubTransport:
    """镜像 test_ingest_nowgoal_season_odds.py::TestFetchTwoPointRows._StubTransport,
    同一种离线打桩方式,按 cid 返回不同的固定 payload。"""

    def __init__(self, mix_by_cid: dict, euro_by_cid: dict):
        self._mix_by_cid = mix_by_cid
        self._euro_by_cid = euro_by_cid

    def mix_history(self, titan_id, cid="8"):
        return self._mix_by_cid.get(cid, {"ah": [], "ou": []})

    def euro_history(self, titan_id, cid="281"):
        return self._euro_by_cid.get(cid, [])


_MIX_BET365 = {
    "ah": [{"odds": {"u": "0.9", "g": "-0.5", "d": "0.9"}, "mt": 900},
           {"odds": {"u": "0.85", "g": "-0.5", "d": "0.95"}, "mt": 1000}],
    "ou": [{"odds": {"u": "0.9", "g": "2.5", "d": "0.9"}, "mt": 950}],
}
_EURO_BET365 = [{"HomeWin": "2.0", "Standoff": "3.2", "GuestWin": "3.8", "TimeShow": "1970,01,01,00,00,00"}]


class TestFetchAndBuildRecords:
    def test_covers_all_three_companies(self):
        transport = _StubTransport(
            mix_by_cid={"8": _MIX_BET365, "3": _MIX_BET365, "1": _MIX_BET365},
            euro_by_cid={"281": _EURO_BET365, "80": _EURO_BET365},
        )
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        assert set(out.keys()) == {"8", "3", "1"}
        assert len(out["8"]) > 0
        assert len(out["1"]) > 0

    def test_bet365_and_macauslot_include_1x2_crown_does_not(self):
        """cid=3(皇冠)的历史 1x2 cid 未知,euro_cid=None,应该跳过 1x2,
        不是抓取失败,是刻意不发这个请求(见模块 docstring 的已知空缺)。"""
        transport = _StubTransport(
            mix_by_cid={"8": _MIX_BET365, "3": _MIX_BET365, "1": _MIX_BET365},
            euro_by_cid={"281": _EURO_BET365, "80": _EURO_BET365},
        )
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        assert any(r["market"] == "1x2" for r in out["8"])
        assert any(r["market"] == "1x2" for r in out["1"])
        assert not any(r["market"] == "1x2" for r in out["3"])

    def test_ah_present_for_all_companies_when_mix_history_has_data(self):
        transport = _StubTransport(
            mix_by_cid={"8": _MIX_BET365, "3": _MIX_BET365, "1": _MIX_BET365},
            euro_by_cid={},
        )
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        for cid in ("8", "3", "1"):
            assert any(r["market"] == "ah" for r in out[cid]), f"company {cid} missing ah"

    def test_inverted_flag_propagates_to_ah_records(self):
        transport = _StubTransport(mix_by_cid={"8": _MIX_BET365}, euro_by_cid={})
        not_inv = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        inv = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=True)
        ah_not_inv = next(r for r in not_inv["8"] if r["market"] == "ah")
        ah_inv = next(r for r in inv["8"] if r["market"] == "ah")
        assert ah_not_inv["latest"]["line"] == -ah_inv["latest"]["line"]

    def test_no_pre_match_data_yields_empty_records_not_crash(self):
        transport = _StubTransport(mix_by_cid={}, euro_by_cid={})
        out = cli.fetch_and_build_records(transport, "titan-1", "2026-01-01T00:00:00Z", inverted=False)
        assert out == {"8": [], "3": [], "1": []}
