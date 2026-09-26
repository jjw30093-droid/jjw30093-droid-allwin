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
        # 2026-08-25 起 dim_match 触发器校验 Season 与 (League_ID, Date) 一致:
        # "2026" 这种自然年标签配英超(47,跨年制)是触发器要拒绝的错标,
        # 换成真实的自然年联赛挪超(59)——本组测试关心的是"--season 精确
        # 匹配、不做前缀匹配"这个过滤语义,不关心具体是哪个联赛。
        (1, "2026", 59, "2026-08-01", "Finish", None, None, None),        # 目标
        (2, "2026", 59, "2026-08-02", "Finish", "Arena", None, None),     # 已有场馆 → 跳过
        (3, "2026", 59, "2026-08-03", "NotStarted", None, None, None),    # 未完赛 → 跳过
        (4, "2026/2027", 55, "2026-08-04", "Finish", None, None, None),   # 其它赛季
        (5, "2026", 59, "2026-08-05", "Finish", None, "#ff0000", None),   # 已有配色 → 跳过
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


# ── --missing-colors(2026-09-26,第四批):按四个配色列任一为空选场 ──


@pytest.fixture
def seeded_colors(data_dir):
    conn = connect_rw("core")
    # (id, venue, hl, hd, al, ad, status)
    rows = [
        (11, None, None, None, None, None, "Finish"),          # 全空 → 选
        (12, "Arena", None, None, None, None, "Finish"),       # 有场馆但缺配色 → 默认判据漏掉、本参数选中
        (13, "Arena", "#111111", "#222222", "#333333", "#444444", "Finish"),  # 四色齐全 → 不选
        (14, "Arena", "#111111", "#222222", "#333333", None, "Finish"),       # 缺一个 → 选
        (15, None, None, None, None, None, "NotStarted"),      # 未完赛 → 默认不选
    ]
    for mid, venue, hl, hd, al, ad, status in rows:
        conn.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, status,"
            " Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name, Venue_Name,"
            " Home_Team_Color_Light, Home_Team_Color_Dark, Away_Team_Color_Light, Away_Team_Color_Dark)"
            " VALUES (?, '2026', 59, '2026-08-01', ?, 1, 2, 'H', 'A', ?, ?, ?, ?, ?)",
            (mid, status, venue, hl, hd, al, ad),
        )
    conn.commit()
    yield conn
    conn.close()


def test_missing_colors_selects_any_null_color_column(seeded_colors):
    ids = sorted(t["Match_ID"] for t in _select_targets(_args(season="2026", missing_colors=True)))
    assert ids == [11, 12, 14]  # 13(四色齐全)与 15(未完赛)不选


def test_default_criterion_misses_rows_that_have_venue_but_no_colors(seeded_colors):
    # 对照:默认判据(三样全空)漏掉 12、14——这正是新增本参数的原因
    ids = sorted(t["Match_ID"] for t in _select_targets(_args(season="2026")))
    assert ids == [11]


def test_missing_colors_still_respects_status_and_scope_filters(seeded_colors):
    ids = sorted(
        t["Match_ID"]
        for t in _select_targets(_args(season="2026", missing_colors=True, finished_only=False))
    )
    assert ids == [11, 12, 14, 15]
    assert _select_targets(_args(season="2099", missing_colors=True)) == []
    assert _select_targets(_args(season="2026", league=47, missing_colors=True)) == []


def test_missing_colors_not_widened_by_include_filled(seeded_colors):
    # --include-filled(only_missing=False)与 --missing-colors 同用:仍只选配色缺失的
    ids = sorted(
        t["Match_ID"]
        for t in _select_targets(_args(season="2026", missing_colors=True, only_missing=False))
    )
    assert ids == [11, 12, 14]


def test_missing_colors_dry_run_via_main(seeded_colors, capsys):
    rc = main(["--season", "2026", "--missing-colors"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "目标场次: 3" in out and "dry-run" in out
