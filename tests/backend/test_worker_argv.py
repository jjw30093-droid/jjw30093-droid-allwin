"""Worker 任务 argv 接线回归测试。

2026-08-24 真实事故:--write-match-details 开关早已完整实现
(backend/cli/poll_fotmob_snapshots.py::_write_match_details),但
backend/worker/runner.py 注册 fotmob_snapshot 任务的 argv 漏了它——该任务
跑了 1.4 万+ 次,每轮抓回整份 pageProps 又当场丢掉场馆/天气/配色,
dim_match 16934 行里 Venue_*/Weather_Description 非空数为 0。

"实现了 CLI 开关但没在 runner argv 里接线"与"根本没实现"在生产上等价,
且没有任何常规测试能抓到(CLI 自身的测试全绿)。本文件是唯一防线:任何
新增采集开关都应在此追加一条 argv 断言(CLAUDE.md §6.3)。
"""

from backend.worker import runner


def test_fotmob_snapshot_argv_includes_write_match_details():
    argv = runner.REGISTRY["fotmob_snapshot"]["argv"]
    assert "--write-match-details" in argv, (
        "fotmob_snapshot 任务丢失 --write-match-details 开关:该任务将继续"
        "抓回整份 payload 又丢弃场馆/天气/球队配色(2026-08-24 事故复发)"
    )
    assert "--due" in argv


def test_fotmob_snapshot_argv_module_path():
    """开关必须挂在正确的模块上——防止有人把 argv 改成别的入口后
    上面那条断言仍然误绿。"""
    argv = runner.REGISTRY["fotmob_snapshot"]["argv"]
    assert "backend.cli.poll_fotmob_snapshots" in argv


def test_physical_stats_poll_argv_wired():
    """physical_stats_poll(体能统计迟到补采,独立 allwin-physical-stats.timer
    调度)必须指向正确的 CLI 模块并带 --due,同一纪律(CLAUDE.md §6.3)。"""
    argv = runner.REGISTRY["physical_stats_poll"]["argv"]
    assert "backend.cli.poll_physical_stats" in argv
    assert "--due" in argv


def test_standings_refresh_poll_argv_wired():
    """standings_refresh_poll(积分榜迟到刷新,独立 allwin-standings.timer
    调度)必须指向正确的 CLI 模块并带 --due——同一纪律(CLAUDE.md §6.3):
    "实现了 CLI 开关但没在 runner argv 里接线"与"根本没实现"在生产上等价。"""
    argv = runner.REGISTRY["standings_refresh_poll"]["argv"]
    assert "backend.cli.poll_standings" in argv
    assert "--due" in argv
