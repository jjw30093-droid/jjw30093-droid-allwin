"""backend/scheduler.py::step1_ingest_newly_finished 的候选判据(2026-08-24)。

起因:候选判据此前只认 status='NotStarted',赛程同步撞上正在进行的比赛会把
dim_match.status 写成 'InPlay',一旦变成 InPlay 就再也不是 'NotStarted',
永久漏检——生产实测 21 场比赛卡在 InPlay、开球早已过去数天,shots/
player_stats/momentum 全部零数据。改成与 poll_windows.league_stale_
unresolved_match_ids 同一判据(status != 'Finish' 且已过精确开球 + 2.5h)后,
本文件钉住:InPlay 也能被捞回来重新入库;仍在合理进行中的比赛不会被误抓;
候选池按 Season 收窄,不会把别的赛季同 League_ID 的滞留场次也混进来。

FotMobClient/ingest_match 全部打桩,不发真实网络请求。

**连接陷阱**(写这份测试时踩过,记录下来防止以后有人复制这个模式踩同一个坑):
scheduler.py 用的是 legacy `backend/db.py::get_connection()`,它读的
`backend/db/paths.py::DB_PATH` 是**导入时**算一次的模块级常量,不像
`connect_rw`/`connect_ro` 那样每次调用都现读 `ALLWIN_DATA_DIR`。同一个
pytest 进程内,`DB_PATH` 会冻结在"本进程第一次导入 backend.db.paths 那一刻"
的临时目录上——如果测试用 `connect_rw("core")` 写数据、却指望
`scheduler.step1_ingest_newly_finished()`(内部用 `get_connection()`)读到
同一份数据,两边实际可能读写的是两个不同的物理文件,现象是"候选池数量对得上,
但具体是哪场比赛对不上"这种诡异的跨测试污染。修复:测试用
`scheduler_module.get_connection()`(与被测代码同一个连接工厂)写测试数据,
不用 `connect_rw`。
"""

import sys
from pathlib import Path

import pytest

from tests.backend.coreseed import seed_core_schema

BACKEND_DIR = str(Path(__file__).resolve().parents[2] / "backend")


@pytest.fixture
def scheduler_module(data_dir):
    """按生产同款方式导入 backend/scheduler.py(它自己也会做同一个 sys.path
    hack,这里先做一遍确保 import 顺序稳定)。"""
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    import scheduler as scheduler_mod

    return scheduler_mod


def _seed(scheduler_module, match_id, league_id, season, status, kickoff_at_utc):
    """用被测代码自己的连接工厂写测试数据,见模块顶部"连接陷阱"说明——
    必须和 step1_ingest_newly_finished 内部读的是同一个物理文件。"""
    conn = scheduler_module.get_connection()
    seed_core_schema(conn)
    conn.execute("DELETE FROM dim_match")  # 每个测试独立,不依赖上一个测试留下的行
    conn.execute(
        "INSERT OR REPLACE INTO dim_match (Match_ID, Season, League_ID, Date, status,"
        " kickoff_at_utc, kickoff_precision, kickoff_source)"
        " VALUES (?, ?, ?, '2026-08-14', ?, ?, 'exact', 'fotmob')",
        (match_id, season, league_id, status, kickoff_at_utc),
    )
    conn.commit()
    conn.close()


class _FakeClient:
    """FotMobClient 的最小打桩:check_ip 只需非空 origin,league_matches 返回
    调用方在 fixture 里指定的固定 payload。"""

    def __init__(self, finished_ids):
        self._finished_ids = set(finished_ids)

    def check_ip(self):
        return {"origin": "203.0.113.1"}

    def league_matches(self, league_id, season):
        # 候选集合外的 id 也放几个进真实日程,证明"实时状态 finished"和
        # "在候选池里"必须同时满足,缺一不可。
        all_ids = self._finished_ids | {90001, 90002}
        return {
            "fixtures": {
                "allMatches": [
                    {"id": mid, "status": {"finished": mid in self._finished_ids}}
                    for mid in all_ids
                ]
            }
        }


def _patch(scheduler_module, monkeypatch, finished_ids, ingested, now_iso="2026-08-24T12:00:00Z"):
    monkeypatch.setattr(scheduler_module, "FotMobClient", lambda: _FakeClient(finished_ids))
    monkeypatch.setattr(
        scheduler_module,
        "ingest_match",
        lambda mid, league_id=None, season=None: ingested.append((mid, league_id, season)),
    )
    monkeypatch.setattr(scheduler_module, "utc_now_iso", lambda: now_iso)


class TestStaleReingestCandidatePool:
    def test_inplay_past_threshold_is_reingested(self, scheduler_module, monkeypatch):
        # kickoff 早已过去,状态卡在 InPlay(生产实测的真实故障形状)
        _seed(scheduler_module, 5795371, 47, "2026/2027", "InPlay", "2026-08-20T15:30:00Z")
        ingested = []
        _patch(scheduler_module, monkeypatch, finished_ids=[5795371], ingested=ingested)

        ok = scheduler_module.step1_ingest_newly_finished(47, "2026/2027")
        assert ok == [5795371]
        assert ingested == [(5795371, 47, "2026/2027")]

    def test_inplay_within_threshold_not_reingested(self, scheduler_module, monkeypatch):
        """真的还在进行中的比赛(开球才 1 小时)不该被当成滞留场次去重抓。"""
        _seed(scheduler_module, 5795372, 47, "2026/2027", "InPlay", "2026-08-24T11:00:00Z")
        ingested = []
        _patch(scheduler_module, monkeypatch, finished_ids=[5795372], ingested=ingested)

        ok = scheduler_module.step1_ingest_newly_finished(47, "2026/2027")
        assert ok == []
        assert ingested == []

    def test_still_not_finished_per_fotmob_not_reingested(self, scheduler_module, monkeypatch):
        """在候选池里(该结束却没结束),但 FotMob 实时状态仍未报 finished——
        不得当成"完赛"落库(数据源比赛真的还没结束,或还没更新)。"""
        _seed(scheduler_module, 5795373, 47, "2026/2027", "InPlay", "2026-08-20T15:30:00Z")
        ingested = []
        _patch(scheduler_module, monkeypatch, finished_ids=[], ingested=ingested)

        ok = scheduler_module.step1_ingest_newly_finished(47, "2026/2027")
        assert ok == []
        assert ingested == []

    def test_notstarted_still_works_unchanged(self, scheduler_module, monkeypatch):
        """回归:原有的 NotStarted→Finish 路径必须继续工作,不是被本次改动
        意外收窄了。"""
        _seed(scheduler_module, 5795374, 47, "2026/2027", "NotStarted", "2026-08-20T15:30:00Z")
        ingested = []
        _patch(scheduler_module, monkeypatch, finished_ids=[5795374], ingested=ingested)

        ok = scheduler_module.step1_ingest_newly_finished(47, "2026/2027")
        assert ok == [5795374]

    def test_stale_match_in_different_season_excluded(self, scheduler_module, monkeypatch):
        """league_stale_unresolved_match_ids 只按 League_ID 判定、不区分赛季;
        本函数必须自己再按 Season 收窄,不能把别的赛季的滞留场次用本次调用的
        season 字符串误标进 ingest_match(season 会覆盖 dim_match.Season)。"""
        # 同一 League_ID=47,但赛季是旧的 2025/2026,同样滞留 InPlay 多日
        _seed(scheduler_module, 5795375, 47, "2025/2026", "InPlay", "2026-08-20T15:30:00Z")
        ingested = []
        _patch(scheduler_module, monkeypatch, finished_ids=[5795375], ingested=ingested)

        # 本次调用要的是 2026/2027 赛季,5795375 是 2025/2026 的,不该被带上
        ok = scheduler_module.step1_ingest_newly_finished(47, "2026/2027")
        assert ok == []
        assert ingested == []
