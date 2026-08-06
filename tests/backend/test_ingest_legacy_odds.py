"""bronze_legacy_odds_summary 入库 CLI 的方向性与幂等测试。

方向性修正的实证依据(2026-08-06,真实比分,详见 docs/current-state.md):
- Asset A 反转记录(match_name 与 dim_match 主客相反,1,587/8,088=19.6%):
  AH 已是 FotMob 方向(as-is 87.9% 命中悬殊比分赢家),
  1x2 是 match_name 方向(交换后 87.2% 命中)→ 只交换 1x2;
- Asset B footballdata source 的 AH 线:(line>0)==(主队赢) 实测仅 24.5%
  → 取反;nowgoal source 72.3% → 不动。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.cli.ingest_legacy_odds import (
    asset_a_rows,
    asset_b_rows,
    insert_rows,
    load_asset_a_records,
    resolve_orientation,
)

_ODDS_SCHEMA = (
    Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds"
)


@pytest.fixture()
def odds_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db_path))
    for mig in ("0001_init.sql", "0004_legacy_odds_summary.sql"):
        conn.executescript((_ODDS_SCHEMA / mig).read_text())
    conn.commit()
    conn.close()
    return db_path


def _rec(match_name: str, *, x2_latest=None, ah_latest=None, ou_latest=None) -> dict:
    odds: dict = {"provider": "Bet365"}
    if ah_latest:
        odds["asian_handicap"] = {"latest": ah_latest}
    if ou_latest:
        odds["over_under"] = {"latest": ou_latest}
    if x2_latest:
        odds["european_1x2"] = {"latest": x2_latest}
    return {"fotmob_id": "1", "match_name": match_name, "odds": odds}


# ── 方向解析 ──


def test_orientation_normal_inverted_unresolved() -> None:
    rec = _rec("Alpha vs Beta")
    assert resolve_orientation(rec, "Alpha", "Beta") is False
    assert resolve_orientation(rec, "Beta", "Alpha") is True
    assert resolve_orientation(rec, "Gamma", "Delta") is None
    assert resolve_orientation({"match_name": "no separator"}, "A", "B") is None


# ── Asset A 修正规则 ──


def test_asset_a_normal_record_passthrough() -> None:
    rows = asset_a_rows(
        _rec(
            "H vs A",
            x2_latest={"home": "1.44", "draw": "4.75", "away": "6.50"},
            ah_latest={"over_or_home": "0.99", "line": "1.25", "under_or_away": "0.91"},
            ou_latest={"over_or_home": "1.04", "line": "2.5", "under_or_away": "0.86"},
        ),
        inverted=False,
    )
    by = {r["market"]: r for r in rows}
    assert by["1x2"]["home_or_over"] == 1.44 and by["1x2"]["away_or_under"] == 6.5
    assert by["1x2"]["orientation_fixed"] == 0
    assert by["ah"]["line"] == 1.25 and by["ah"]["home_or_over"] == 0.99
    assert by["ou"]["line"] == 2.5 and by["ou"]["home_or_over"] == 1.04


def test_asset_a_inverted_swaps_only_1x2() -> None:
    """实证规则:反转时 1x2 交换,AH/OU 原样(AH 已是 FotMob 方向)。"""
    rows = asset_a_rows(
        _rec(
            "Marseille vs Montpellier",   # dim_match 实际是 Montpellier 主场
            x2_latest={"home": "2.05", "draw": "3.5", "away": "3.60"},
            ah_latest={"over_or_home": "0.80", "line": "-0.25", "under_or_away": "1.05"},
            ou_latest={"over_or_home": "1.0", "line": "2.75", "under_or_away": "0.85"},
        ),
        inverted=True,
    )
    by = {r["market"]: r for r in rows}
    # 1x2 交换后:canonical 主队(Montpellier)赔率 3.60,客队(Marseille)2.05
    assert by["1x2"]["home_or_over"] == 3.6 and by["1x2"]["away_or_under"] == 2.05
    assert by["1x2"]["orientation_fixed"] == 1
    # AH 原样:负线=客队 Marseille 热门,与 1x2 交换后一致
    assert by["ah"]["line"] == -0.25 and by["ah"]["orientation_fixed"] == 0
    # OU 原样
    assert by["ou"]["line"] == 2.75 and by["ou"]["orientation_fixed"] == 0


def test_asset_a_empty_slots_skipped() -> None:
    rows = asset_a_rows(
        _rec("H vs A", ah_latest={"over_or_home": "", "line": "", "under_or_away": ""}),
        inverted=False,
    )
    assert rows == []


def test_load_asset_a_dedupes_keeping_most_complete(tmp_path: Path) -> None:
    full = _rec("H vs A", x2_latest={"home": "1.5", "draw": "4", "away": "6"},
                ah_latest={"over_or_home": "0.9", "line": "1", "under_or_away": "0.9"})
    partial = _rec("H vs A", x2_latest={"home": "1.6", "draw": "4", "away": "5.5"})
    (tmp_path / "match_odds_data_2122.json").write_text(json.dumps([partial]))
    (tmp_path / "match_odds_data_2223.json").write_text(json.dumps([full]))
    (tmp_path / "match_odds_data.json.bak.pre_cleanup").write_text("not json")  # 必须被跳过
    best, total = load_asset_a_records(tmp_path)
    assert total == 2
    rec, src = best["1"]
    assert src == "match_odds_data_2223.json"    # 六槽更完整者胜出
    assert "asian_handicap" in rec["odds"]


# ── Asset B 修正规则 ──


def _b_db(tmp_path: Path, rows: list[tuple]) -> Path:
    p = tmp_path / "football_uk.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE silver_match_odds (match_id INTEGER, market TEXT, period TEXT,"
        " source TEXT, provider TEXT, raw_line REAL, raw_home REAL, raw_draw REAL, raw_away REAL)"
    )
    conn.executemany("INSERT INTO silver_match_odds VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return p


def test_asset_b_footballdata_ah_line_negated(tmp_path: Path) -> None:
    db = _b_db(tmp_path, [
        (100, "ah", "closing", "footballdata", "Bet365", 1.5, 0.9, None, 0.94),
        (100, "ah", "closing", "nowgoal", "Bet365", -1.5, 0.9, None, 0.94),
    ])
    rows, skipped = asset_b_rows(db, {100})
    by_src = {r["source"]: r for r in rows}
    assert by_src["asset_b_footballdata"]["line"] == -1.5     # 取反
    assert by_src["asset_b_footballdata"]["orientation_fixed"] == 1
    assert by_src["asset_b_nowgoal"]["line"] == -1.5          # 原样
    assert by_src["asset_b_nowgoal"]["orientation_fixed"] == 0


def test_asset_b_ou_null_line_skipped_not_zeroed(tmp_path: Path) -> None:
    db = _b_db(tmp_path, [
        (100, "ou", "closing", "footballdata", "Bet365", None, 0.9, None, 0.9),
        (100, "ou", "opening", "footballdata", "Bet365", 2.5, 0.9, None, 0.9),
    ])
    rows, skipped = asset_b_rows(db, {100})
    assert skipped["ou_null_line"] == 1
    assert len(rows) == 1 and rows[0]["line"] == 2.5 and rows[0]["period"] == "initial"


def test_asset_b_period_mapping_and_unknown_match_filtered(tmp_path: Path) -> None:
    db = _b_db(tmp_path, [
        (100, "1x2", "opening", "footballdata", "Bet365", None, 2.0, 3.4, 3.8),
        (999, "1x2", "closing", "footballdata", "Bet365", None, 2.0, 3.4, 3.8),
    ])
    rows, skipped = asset_b_rows(db, {100})
    assert len(rows) == 1
    assert rows[0]["period"] == "initial" and rows[0]["draw"] == 3.4 and rows[0]["line"] is None
    assert skipped["not_in_dim_match"] == 1


# ── 入库幂等 ──


def test_insert_rows_idempotent(odds_db: Path) -> None:
    conn = sqlite3.connect(str(odds_db))
    row = {
        "fotmob_match_id": 100, "source": "asset_a_json", "provider": "Bet365",
        "market": "ah", "period": "latest", "line": 1.0,
        "home_or_over": 0.95, "draw": None, "away_or_under": 0.95,
        "orientation_fixed": 0, "source_file": "match_odds_data_2122.json",
    }
    assert insert_rows(conn, [row]) == 1
    assert insert_rows(conn, [row]) == 0   # UNIQUE + OR IGNORE
    assert conn.execute("SELECT COUNT(*) FROM bronze_legacy_odds_summary").fetchone()[0] == 1
    conn.close()


def test_insert_rows_bad_enum_dropped_not_inserted(odds_db: Path) -> None:
    """INSERT OR IGNORE 会吞掉 CHECK 违规(SQLite 语义):坏枚举行不落库、
    返回计数 0——LIVE 输出的 rows_ignored_existing 会暴露这类批量异常。"""
    conn = sqlite3.connect(str(odds_db))
    bad = {
        "fotmob_match_id": 100, "source": "somewhere_else", "provider": "X",
        "market": "ah", "period": "latest", "line": 1.0,
        "home_or_over": 0.9, "draw": None, "away_or_under": 0.9,
        "orientation_fixed": 0, "source_file": None,
    }
    assert insert_rows(conn, [bad]) == 0
    assert conn.execute("SELECT COUNT(*) FROM bronze_legacy_odds_summary").fetchone()[0] == 0
    conn.close()
