"""ingest_future_fixtures.py 的赛季身份校验回归(本轮任务 §2)。

背景:若 FotMob 尚未发布某联赛的目标赛季,`league_matches(league_id, season)`
可能忽略 `season` 参数、静默返回另一个(通常是已完结)赛季的数据。不校验的话,
这些行会被 write_rows 当成目标赛季写进 dim_match——例如把已完赛的整季错误标
成 Season='2026/2027',这正是 docs/data-plan.md 记录的跨赛季污染风险。
"""

import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_module():
    """同 test_fotmob_prematch_status.py 的加载方式:复刻
    `python backend/ingest/ingest_future_fixtures.py` 真实运行时的 sys.path。"""
    for d in (os.path.join(REPO_ROOT, "backend", "ingest"), os.path.join(REPO_ROOT, "backend")):
        if d not in sys.path:
            sys.path.insert(0, d)
    path = os.path.join(REPO_ROOT, "backend", "ingest", "ingest_future_fixtures.py")
    spec = importlib.util.spec_from_file_location("iff_identity_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load_module()


class _FakeClient:
    """替身 FotMobClient:只实现 fetch_fixture_rows 用到的 league_matches()。"""

    def __init__(self, response):
        self._response = response
        self.calls = 0

    def league_matches(self, league_id, season):
        self.calls += 1
        return self._response


def test_matching_identity_passes(mod):
    response = {
        "details": {"id": 87, "selectedSeason": "2026/2027"},
        "fixtures": {"allMatches": []},
    }
    client = _FakeClient(response)
    rows = mod.fetch_fixture_rows(client, league_id=87, season="2026/2027")
    assert rows == []
    assert client.calls == 1


def test_league_id_mismatch_rejects_and_raises(mod):
    """来源返回了别的联赛的数据(id 不匹配)——必须拒绝,不能静默写入。"""
    response = {
        "details": {"id": 47, "selectedSeason": "2026/2027"},  # 请求 87,来源返回了 47
        "fixtures": {"allMatches": [{"id": 1, "status": {}, "home": {}, "away": {}}]},
    }
    client = _FakeClient(response)
    with pytest.raises(mod.SeasonIdentityError, match="details.id=47"):
        mod.fetch_fixture_rows(client, league_id=87, season="2026/2027")


def test_season_mismatch_rejects_and_raises(mod):
    """来源静默返回了另一个赛季(常见于目标赛季尚未发布)——必须拒绝,不能把
    已完赛的旧赛季误标成目标赛季写进 dim_match。"""
    response = {
        "details": {"id": 53, "selectedSeason": "2025/2026"},  # 请求 2026/2027
        "fixtures": {"allMatches": [{"id": 1, "status": {"finished": True}, "home": {}, "away": {}}]},
    }
    client = _FakeClient(response)
    with pytest.raises(mod.SeasonIdentityError, match="selectedSeason='2025/2026'"):
        mod.fetch_fixture_rows(client, league_id=53, season="2026/2027")


def test_unparseable_details_id_rejects(mod):
    response = {"details": {"id": None}, "fixtures": {"allMatches": []}}
    client = _FakeClient(response)
    with pytest.raises(mod.SeasonIdentityError, match="无法解析"):
        mod.fetch_fixture_rows(client, league_id=53, season="2026/2027")


def test_missing_details_key_rejects(mod):
    """响应里完全没有 details(结构性漂移)也要 fail-closed,不能把
    details.get('id') 报出的 None!=league_id 之外的路径悄悄放过。"""
    response = {"fixtures": {"allMatches": []}}
    client = _FakeClient(response)
    with pytest.raises(mod.SeasonIdentityError):
        mod.fetch_fixture_rows(client, league_id=53, season="2026/2027")
