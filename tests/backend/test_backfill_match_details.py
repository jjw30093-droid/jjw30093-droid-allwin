"""backfill_match_details CLI 的选场与断点续跑判据(离线,零网络)。

真实抓取路径(fetch_match_payload → extract_prematch_details →
_write_match_details)的写库部分已由
tests/backend/test_poll_fotmob_snapshots_match_details.py 用离线 fixture
覆盖(同一个 _write_match_details);本文件只测本 CLI 自己的逻辑:
- --only-missing(默认)只选目标列全空的行——这是断点续跑的全部机制;
- 判空判据用本次事故真正丢失的三类代表列(场馆/配色/裁判统计),不用
  Referee/Temperature 这类 70%/16% 已有值的老基线列(用它们会把绝大多数
  历史场次误判为"已回填");
- --finished-only(默认)只选 Finish;
- dry-run(缺省)不发任何请求。
"""

import pytest

from backend.cli.backfill_match_details import _select_targets, main
from backend.db.connections import connect_rw


class _Args:
    season = None
    league = None
    date_from = None
    date_to = None
    limit = None
    finished_only = True
    only_missing = True


def _args(**overrides):
    a = _Args()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


@pytest.fixture
def seeded(data_dir):
    conn = connect_rw("core")
    rows = [
        # (id, season, league, date, status, venue, color, ref_stats)
        (1, "2026", 47, "2026-08-01", "Finish", None, None, None),        # 目标
        (2, "2026", 47, "2026-08-02", "Finish", "Arena", None, None),     # 已有场馆 → 跳过
        (3, "2026", 47, "2026-08-03", "NotStarted", None, None, None),    # 未完赛 → 跳过
        (4, "2026/2027", 55, "2026-08-04", "Finish", None, None, None),   # 其它赛季
        (5, "2026", 47, "2026-08-05", "Finish", None, "#ff0000", None),   # 已有配色 → 跳过
    ]
    for mid, season, league, date, status, venue, color, stats in rows:
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status,"
            " Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name,"
            " Venue_Name, Home_Team_Color_Light, Referee_Stats_Json)"
            " VALUES (?, ?, ?, ?, ?, 1, 2, 'H', 'A', ?, ?, ?)",
            (mid, season, league, date, status, venue, color, stats),
        )
    conn.commit()
    yield conn
    conn.close()


def test_only_missing_skips_partially_filled_rows(seeded):
    ids = [t["Match_ID"] for t in _select_targets(_args(season="2026"))]
    assert ids == [1]  # 2(有场馆)/3(未完赛)/5(有配色)都不选


def test_season_filter_is_exact_match_not_prefix(seeded):
    ids = [t["Match_ID"] for t in _select_targets(_args(season="2026/2027"))]
    assert ids == [4]


def test_include_filled_selects_everything_finished(seeded):
    ids = sorted(
        t["Match_ID"] for t in _select_targets(_args(season="2026", only_missing=False))
    )
    assert ids == [1, 2, 5]


def test_all_status_includes_not_started(seeded):
    ids = sorted(
        t["Match_ID"] for t in _select_targets(_args(season="2026", finished_only=False))
    )
    assert ids == [1, 3]


def test_dry_run_makes_no_requests_and_exits_zero(seeded, capsys):
    # 缺省不带 --commit:只打印,不 import 抓取路径、不发请求(发了会因
    # 无代理凭证抛错,退出码非 0——这条断言同时覆盖"真没发")。
    rc = main(["--season", "2026"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "目标场次: 1" in out
    assert "dry-run" in out
