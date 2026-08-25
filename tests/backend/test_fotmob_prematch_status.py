"""赛前 status 判定回归(本轮任务 §1)。

背景:真实赛前 FotMob header.status 没有 `reason` 键(只有
cancelled/finished/halfs/started/timezone/utcTime),旧代码
`status_obj.get("reason", {}).get("short", "Unknown")` 因此对任何赛前比赛恒
返回 'Unknown',使该场比赛被 poll_windows.upcoming_precise_matches(过滤
status='NotStarted')永久排除出赔率轮询窗口。

集成级用例读取仓库里真实抓取的赛前 payload(tests/fixtures/fotmob/
prematch-5104961.json,裁掉了 translations/fallback/seo 等与解析无关的大字
段,保留 general/header/content 三个真正被解析的顶层键,内容未编造)。
枚举级用例(finished/cancelled/started)用符合已验证真实形状的合成
status_obj 字典单测 derive_match_status 的分支覆盖——FotMob 目前没有已完赛/
取消比赛的真实 payload 落在本仓库里,这里如实标注为合成输入,不冒充真实抓包。
"""

import importlib.util
import json
import os
import sys

import pytest

from backend.fotmob_client import (
    FotMobClient,
    derive_match_status,
    STATUS_CANCELLED,
    STATUS_FINISH,
    STATUS_IN_PLAY,
    STATUS_NOT_STARTED,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "fotmob")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _load_ingest_future_fixtures_module():
    """ingest_future_fixtures.py 是历史遗留的独立脚本风格(`from ingest_match import
    _upsert`/`from fotmob_client import ...`,非 `backend.xxx`),真实运行方式是
    `python backend/ingest/ingest_future_fixtures.py`——Python 会自动把脚本所在目录
    (backend/ingest/)加进 sys.path[0]。这里手动补齐同样的两个目录再按文件路径加载,
    复刻真实运行环境,而不是把它当包导入。"""
    for d in (os.path.join(REPO_ROOT, "backend", "ingest"), os.path.join(REPO_ROOT, "backend")):
        if d not in sys.path:
            sys.path.insert(0, d)
    path = os.path.join(REPO_ROOT, "backend", "ingest", "ingest_future_fixtures.py")
    spec = importlib.util.spec_from_file_location("ingest_future_fixtures_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client():
    return FotMobClient(proxy="")  # 离线解析,不发起任何网络请求


def test_real_prematch_payload_status_is_not_started(client):
    """真实赛前 payload(5104961,2026-08-01 开球)必须解析成 NotStarted,
    且不得丢失 kickoff 三列(TRAP A 的两个症状都要断言)。"""
    page_props = _load_fixture("prematch-5104961.json")
    assert page_props["header"]["status"] == {
        "cancelled": False,
        "finished": False,
        "halfs": {},
        "numberOfAwayRedCards": 0,
        "numberOfHomeRedCards": 0,
        "started": False,
        "timezone": "UTC",
        "utcTime": "2026-08-01T14:00:00.000Z",
    }
    assert "reason" not in page_props["header"]["status"]

    row = client.parse_match_dim(page_props, match_id=5104961, league_id=59)

    assert row["status"] == STATUS_NOT_STARTED
    assert row["kickoff_at_utc"] == "2026-08-01T14:00:00Z"
    assert row["kickoff_precision"] == "exact"
    assert row["kickoff_source"] == "fotmob:match_details"
    # 赛前数据能力实测(裁判/天气/场馆):同一份 payload 里都必须解析出来,
    # 不得因为改了 status 判定就顺带影响其它字段。
    assert row["Referee"] == "Mischa Kellerhals"
    assert row["Temperature"] == "18"
    assert row["Wind_Speed"] == "9"
    # 场馆(content.matchFacts.infoBox.Stadium)与天气文字描述(2026-08-20):
    # 此前完全未提取,现在补上——真实来源 description="Partly Cloudy/Wind"
    # 比 defaultTitle="Windy" 信息量大,优先取 description。
    assert row["Venue_Name"] == "Nye Fredrikstad Stadion"
    assert row["Venue_City"] == "Fredrikstad"
    assert row["Venue_Country"] == "Norway"
    assert row["Weather_Description"] == "Partly Cloudy/Wind"


@pytest.mark.parametrize(
    "status_obj,expected",
    [
        pytest.param(
            {"cancelled": False, "finished": False, "halfs": {}, "started": False,
             "numberOfAwayRedCards": 0, "numberOfHomeRedCards": 0,
             "timezone": "UTC", "utcTime": "2026-08-01T14:00:00.000Z"},
            STATUS_NOT_STARTED,
            id="prematch-real-shape-no-reason-key",
        ),
        pytest.param(
            {"cancelled": False, "finished": True, "started": True,
             "reason": {"short": "FT", "long": "Full Time"}, "scoreStr": "2 - 1"},
            STATUS_FINISH,
            id="finished-FT",
        ),
        pytest.param(
            # 点球告负判负胜(awarded win):finished=true 但没有常规比分文本。
            {"cancelled": False, "finished": True, "started": True,
             "reason": {"short": "AW", "long": "Awarded win"}},
            STATUS_FINISH,
            id="finished-awarded-win",
        ),
        pytest.param(
            {"cancelled": True, "finished": False, "started": False,
             "reason": {"short": "Can", "long": "Cancelled"}},
            STATUS_CANCELLED,
            id="cancelled",
        ),
        pytest.param(
            {"cancelled": False, "finished": False, "started": True,
             "reason": {"short": "HT", "long": "Half Time"}},
            STATUS_IN_PLAY,
            id="started-not-finished",
        ),
        pytest.param({}, STATUS_NOT_STARTED, id="empty-dict"),
        pytest.param(None, STATUS_NOT_STARTED, id="none"),
    ],
)
def test_derive_match_status_enumerated(status_obj, expected):
    assert derive_match_status(status_obj) == expected


@pytest.mark.parametrize(
    "status_obj",
    [
        {"cancelled": False, "finished": False, "started": False, "utcTime": "x"},
        {"finished": True, "reason": {"short": "FT"}},
        {"cancelled": True, "reason": {"short": "Can"}},
        {"started": True, "finished": False},
        {},
    ],
)
def test_ingest_future_fixtures_status_matches_match_details_status(status_obj):
    """两条 ingest 路径(赛程列表页 / match_details 详情页)必须对同一个 status
    对象形状给出完全一致的结果——这是 TRAP A 不再漂移的结构性保证,不是约定。
    (两边各自的 sys.path 技巧导致 fotmob_client 被加载成两个独立模块实例,
    因此这里比较的是行为等价,不是同一个函数对象。)"""
    iff = _load_ingest_future_fixtures_module()
    assert iff._status_from_fixture(status_obj) == derive_match_status(status_obj)


def test_derive_match_status_closed_vocabulary():
    """封闭四值——全站消费方(poll_windows/queries/matches 等)只认这四个,
    第五个值会让比赛对某个查询静默失踪,和历史 'Unknown' bug 是同一类缺陷。"""
    allowed = {STATUS_FINISH, STATUS_NOT_STARTED, STATUS_IN_PLAY, STATUS_CANCELLED}
    samples = [
        {"finished": True}, {"cancelled": True}, {"started": True}, {},
        {"finished": True, "cancelled": True},  # finished 优先级最高,不产生第五个值
        {"reason": {"short": "AET"}},  # 没有 finished/cancelled/started 布尔时不透传 reason
    ]
    for s in samples:
        assert derive_match_status(s) in allowed
