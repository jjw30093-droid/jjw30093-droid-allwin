"""backend/providers/nowgoal_archive.py 纯解析函数测试(2026-08-08 挪超/瑞典超
历史赔率回补新增)。全部离线、fixture 驱动,不发真实网络请求。

fixture 数据在 tests/fixtures/nowgoal_archive/,基于 2026-08-08 用真实
titan_id(2912730,Eliteserien)抓取的真实响应构造(数值取自真实抓包,详见
runtime/research/nordic-odds-probe/)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.providers.nowgoal_archive import (
    NowGoalArchiveTransport,
    SeasonIdentityError,
    archive_kickoff_to_utc,
    parse_archive_season,
    two_point_from_euro_history,
    two_point_from_mix_history,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nowgoal_archive"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestArchiveKickoffToUtc:
    """P0-3 坑:archive 的 kickoff 是北京墙上时间(UTC+8),唯一转换实现。
    2026-08-08 用 J1/K1 历史回填产物 × dim_match.kickoff_at_utc 实测 8 例,
    恒定 -8h,零反例——这里钉死其中一例作回归。"""

    def test_known_real_example(self):
        assert archive_kickoff_to_utc("2026-04-07 17:00") == "2026-04-07T09:00:00Z"

    def test_with_seconds(self):
        assert archive_kickoff_to_utc("2026-04-07 17:00:00") == "2026-04-07T09:00:00Z"

    def test_does_not_reproduce_the_no_conversion_bug(self):
        """反例保护:如果有人误把这一步删掉、直接透传北京时间当 UTC,
        会导致所有 kickoff_diff_seconds 恒为 -28800(见 P0-3),这里断言
        转换后的时刻确实不同于原始字符串直接拼 Z。"""
        raw = "2026-04-07 17:00"
        naive_wrong = raw.replace(" ", "T") + ":00Z"
        assert archive_kickoff_to_utc(raw) != naive_wrong


class TestParseArchiveSeason:
    def test_extracts_only_finished_matches_for_requested_league(self):
        rows = parse_archive_season(_load("archive_season_sample.json"), ng_league_id=22)
        ids = sorted(r.titan_id for r in rows)
        # 9999999 是别的联赛(26)的比赛,8888888 是未开赛(status=0),均应被排除
        assert ids == ["2912730", "2912731", "2912738"]

    def test_league_filter_excludes_other_leagues(self):
        rows = parse_archive_season(_load("archive_season_sample.json"), ng_league_id=22)
        assert all(r.ng_league_id == 22 for r in rows)

    def test_kickoff_converted_to_utc(self):
        rows = parse_archive_season(_load("archive_season_sample.json"), ng_league_id=22)
        target = next(r for r in rows if r.titan_id == "2912730")
        assert target.kickoff_utc == "2026-03-14T15:00:00Z"

    def test_score_parsed(self):
        rows = parse_archive_season(_load("archive_season_sample.json"), ng_league_id=22)
        target = next(r for r in rows if r.titan_id == "2912730")
        assert (target.home_score, target.away_score) == (2, 1)

    def test_sorted_by_kickoff(self):
        rows = parse_archive_season(_load("archive_season_sample.json"), ng_league_id=22)
        kickoffs = [r.kickoff_utc for r in rows]
        assert kickoffs == sorted(kickoffs)

    def test_missing_schedule_list_returns_empty(self):
        assert parse_archive_season({"LeagueInfo": [22, "x", "2026"]}, ng_league_id=22) == []


class TestTwoPointFromMixHistory:
    """严格赛前过滤:opening=最早赛前行,closing=最晚赛前行,赛后/占位行必须
    被丢弃而不是回退使用——两点摘要绝不能悄悄掺进赛中/赛后价格。"""

    def test_opening_and_closing_are_pre_match_only(self):
        payload = _load("mix_history_sample.json")["Data"]
        tp = two_point_from_mix_history(payload, "2026-03-14T15:00:00Z")
        assert tp["ah"]["opening"] == {"home": 0.93, "line": -0.75, "away": 0.88}
        assert tp["ah"]["closing"] == {"home": 0.9, "line": -0.75, "away": 0.9}
        assert tp["ou"]["opening"] == {"over": 0.88, "line": 3.25, "under": 0.93}
        assert tp["ou"]["closing"] == {"over": 0.8, "line": 2.5, "under": 1.0}

    def test_post_match_and_zero_placeholder_rows_excluded(self):
        """fixture 里 mt=1773504000(赛后)且 u=g=d=0 的行,两条独立理由都应
        排除它:既晚于 kickoff,数值本身也是占位符不是真实报价。"""
        payload = _load("mix_history_sample.json")["Data"]
        tp = two_point_from_mix_history(payload, "2026-03-14T15:00:00Z")
        # 若占位行被误用,opening/closing 会出现 home=0 这种不可能的赔率
        for market in ("ah", "ou"):
            for point in ("opening", "closing"):
                vals = tp[market][point]
                assert all(v != 0 for k, v in vals.items() if k in ("home", "away", "over", "under"))

    def test_zero_pre_match_rows_yields_none_not_fallback(self):
        """某市场赛前一行都没有时必须写 None,不得回退到赛中/赛后行。"""
        payload = {"ah": [{"odds": {"u": "1.9", "g": "0", "d": "1.9"}, "mt": 9999999999}], "ou": []}
        tp = two_point_from_mix_history(payload, "2026-03-14T15:00:00Z")
        assert tp["ah"] is None  # 唯一一行晚于 kickoff
        assert tp["ou"] is None  # 压根没有行


class TestTwoPointFromEuroHistory:
    def test_opening_and_closing(self):
        rows = _load("euro_history_sample.json")
        tp = two_point_from_euro_history(rows, "2026-03-14T15:00:00Z")
        assert tp["opening"] == {"home": 4.20, "draw": 4.00, "away": 1.70}
        assert tp["closing"] == {"home": 4.75, "draw": 3.80, "away": 1.70}

    def test_post_match_row_excluded(self):
        rows = _load("euro_history_sample.json")
        tp = two_point_from_euro_history(rows, "2026-03-14T15:00:00Z")
        # 第三行(16:00,赛后)的赔率 5.50 不应出现在 opening 或 closing 里
        assert tp["opening"]["home"] != 5.50
        assert tp["closing"]["home"] != 5.50

    def test_no_pre_match_rows_returns_none(self):
        rows = [{"HomeWin": "2.0", "Standoff": "3.0", "GuestWin": "3.5",
                "TimeShow": "2026,03,15,00,00,00"}]
        assert two_point_from_euro_history(rows, "2026-03-14T15:00:00Z") is None


class TestArchiveSeasonIdentityGate:
    """archive_season() 的自证身份门禁:LeagueInfo[0]/[2] 与请求参数不符必须
    拒绝、零写入(镜像 ingest_future_fixtures._verify_season_identity)。"""

    def test_mismatched_league_id_rejected(self, monkeypatch):
        transport = NowGoalArchiveTransport(proxy="")
        monkeypatch.setattr(transport, "_get", lambda *a, **k: json.dumps(
            {"LeagueInfo": [99, "Wrong League", "2026"]}))
        with pytest.raises(SeasonIdentityError, match="mismatch"):
            transport.archive_season(22, "2026")

    def test_mismatched_season_rejected(self, monkeypatch):
        transport = NowGoalArchiveTransport(proxy="")
        monkeypatch.setattr(transport, "_get", lambda *a, **k: json.dumps(
            {"LeagueInfo": [22, "Norway Eliteserien", "2025"]}))
        with pytest.raises(SeasonIdentityError, match="mismatch"):
            transport.archive_season(22, "2026")

    def test_matching_identity_passes(self, monkeypatch):
        transport = NowGoalArchiveTransport(proxy="")
        monkeypatch.setattr(transport, "_get", lambda *a, **k: json.dumps(
            {"LeagueInfo": [22, "Norway Eliteserien", "2026"], "ScheduleList": {}}))
        data = transport.archive_season(22, "2026")
        assert data["LeagueInfo"][0] == 22

    def test_non_json_response_rejected(self, monkeypatch):
        transport = NowGoalArchiveTransport(proxy="")
        monkeypatch.setattr(transport, "_get", lambda *a, **k: "<html>not json</html>")
        with pytest.raises(Exception):
            transport.archive_season(22, "2026")


class TestTransportDoesNotTouchProductionPath:
    """本模块必须完全独立于生产实时轮询用的 backend.providers.nowgoal._http_get——
    不 import 它、不 monkeypatch 它、不共享任何网络层状态。"""

    def test_offline_construction_bypasses_credential_resolution(self):
        # proxy="" 必须能直接构造,不触发 THORDATA_PROXY 环境变量解析
        transport = NowGoalArchiveTransport(proxy="")
        assert transport.proxies == {}

    def test_module_does_not_import_nowgoal_http_get(self):
        import backend.providers.nowgoal_archive as mod
        import backend.providers.nowgoal as nowgoal_mod
        # 只允许复用纯函数,不允许拿到 _http_get 这个网络函数本身
        assert not hasattr(mod, "_http_get")
        assert mod.looks_blocked is nowgoal_mod.looks_blocked
