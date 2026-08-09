"""J/K/A 旧库赔率导入 CLI(2026-08-07)的闸门、映射与幂等测试。

关键不变量(实证依据见 CLI docstring 与 runtime/research/jka-odds-ingest/):
- 本子集 AH 线**不取反**(与 Asset B footballdata 的取反刻意不同——同一张旧表
  两个真实来源:五大联赛=真 football-data.co.uk,J/K/A=NowGoal 数据换了标签);
- migration 0005 重建表必须保留既有行与旧 source 值;
- refetch 空哨兵(archive 收盘 odds 为 null)被闸门拒绝,只留真实行。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import backend.cli.ingest_jka_legacy_odds as cli

_ODDS_SCHEMA = Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds"


@pytest.fixture()
def odds_db(tmp_path: Path) -> Path:
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db))
    for mig in ("0001_init.sql", "0004_legacy_odds_summary.sql", "0005_legacy_source_jka.sql"):
        conn.executescript((_ODDS_SCHEMA / mig).read_text())
    conn.commit()
    conn.close()
    return db


# ── migration 0005 ──


def test_migration_preserves_existing_rows_and_old_sources(tmp_path: Path) -> None:
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db))
    conn.executescript((_ODDS_SCHEMA / "0001_init.sql").read_text())
    conn.executescript((_ODDS_SCHEMA / "0004_legacy_odds_summary.sql").read_text())
    conn.execute(
        """INSERT INTO bronze_legacy_odds_summary
             (fotmob_match_id, source, provider, market, period, line,
              home_or_over, draw, away_or_under, orientation_fixed, source_file, ingested_at)
           VALUES (1, 'asset_a_json', 'Bet365', '1x2', 'latest', NULL,
                   2.0, 3.4, 3.8, 1, 'old', '2026-08-06T00:00:00Z')""")
    conn.commit()
    conn.executescript((_ODDS_SCHEMA / "0005_legacy_source_jka.sql").read_text())
    row = conn.execute(
        "SELECT source, orientation_fixed, home_or_over FROM bronze_legacy_odds_summary").fetchone()
    assert row == ("asset_a_json", 1, 2.0)
    # 新 source 值可写入;未知值仍被 CHECK 拒绝
    conn.execute(
        """INSERT INTO bronze_legacy_odds_summary
             (fotmob_match_id, source, provider, market, period,
              home_or_over, away_or_under, ingested_at)
           VALUES (2, 'football_uk_jka', 'Bet365', 'ah', 'initial', 1.9, 1.9, 'x')""")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO bronze_legacy_odds_summary
                 (fotmob_match_id, source, provider, market, period,
                  home_or_over, away_or_under, ingested_at)
               VALUES (3, 'made_up_source', 'Bet365', 'ah', 'initial', 1.9, 1.9, 'x')""")
    conn.close()


# ── 行闸门 ──


def test_row_gate_bounds() -> None:
    assert cli._row_ok("1x2", 2.0, 3.4, 3.8, None)
    assert not cli._row_ok("1x2", 1.0, 3.4, 3.8, None)      # 赔率 <1.01
    assert not cli._row_ok("1x2", 5.0, 6.0, 7.0, None)      # overround <1
    assert cli._row_ok("ah", 1.9, None, 1.9, -0.25)
    assert not cli._row_ok("ah", 1.9, None, 1.9, None)      # 无线
    assert not cli._row_ok("ah", 1.9, None, 1.9, 0.3)       # 非 0.25 步进
    assert not cli._row_ok("ah", 1.9, None, 1.9, 4.0)       # 超界
    assert cli._row_ok("ou", 2.02, None, 1.82, 2.25)
    assert not cli._row_ok("ou", None, None, 1.82, 2.25)    # 空哨兵


# ── refetch 装载(空哨兵/缺市场诚实缺失) ──


def test_load_refetch_skips_null_sentinel(tmp_path: Path, monkeypatch) -> None:
    d = tmp_path / "refetch"
    d.mkdir()
    (d / "live_nowgoal_results.json").write_text(json.dumps({
        "111": {"titan_id": "9", "kickoff": "2026-04-01 18:00",
                "1x2": {"opening": {"home": 2.0, "draw": 3.4, "away": 3.8},
                        "closing": {"home": 2.1, "draw": 3.4, "away": 3.5}},
                "ou": {"opening": {"home_or_over": 1.9, "line": 2.5, "away_or_under": 1.9},
                       "closing": {"home_or_over": None, "line": 0.0, "away_or_under": None}}},
    }))
    (d / "adjudicate_results.json").write_text(json.dumps({
        "5139869": {"titan_id": "8", "ah": {"opening": {"home_or_over": 2.03, "line": 0.0, "away_or_under": 1.78}}},
        "4410075": {"titan_id": "7", "ah": {"opening": {"home_or_over": 1.85, "line": -0.25, "away_or_under": 2.0}}},
    }))
    (d / "gap_hits.json").write_text(json.dumps({"111": {}}))
    monkeypatch.setattr(cli, "REFETCH_DIR", d)
    rows, ids = cli.load_refetch_rows()
    assert ids == {111, 5139869, 4410075}
    r111 = {(r["market"], r["period"]) for r in rows[111]}
    assert ("ou", "latest") not in r111       # 空哨兵被拒
    assert ("ou", "initial") in r111 and ("1x2", "latest") in r111
    # AH 线原样透传,不取反
    ah = next(r for r in rows[4410075] if r["market"] == "ah")
    assert ah["line"] == -0.25


# ── 端到端:小型旧库 → 落库幂等 ──


@pytest.fixture()
def small_old_db(tmp_path: Path) -> Path:
    old = tmp_path / "football_uk.db"
    conn = sqlite3.connect(str(old))
    conn.executescript("""
        CREATE TABLE dim_match (Match_ID INT PRIMARY KEY, League_ID INT,
          Home_Team_Name TEXT, Away_Team_Name TEXT);
        CREATE TABLE silver_match_odds (match_id INT, market TEXT, period TEXT,
          source TEXT, raw_home REAL, raw_draw REAL, raw_away REAL, raw_line REAL);
        INSERT INTO dim_match VALUES (201, 223, 'Kashima Antlers', 'FC Tokyo');
        -- fd 全 6 行(AH 线 canonical 方向,主队热门 line>0)
        INSERT INTO silver_match_odds VALUES
          (201,'1x2','opening','footballdata',1.9,3.6,3.9,NULL),
          (201,'1x2','closing','footballdata',1.9,3.6,4.0,NULL),
          (201,'ah','opening','footballdata',1.9,NULL,1.95,0.5),
          (201,'ah','closing','footballdata',1.92,NULL,1.92,0.5),
          (201,'ou','opening','footballdata',1.83,NULL,2.03,2.5),
          (201,'ou','closing','footballdata',1.85,NULL,2.0,2.5),
          -- watcher 行必须被忽略(仲裁证明是盘中过时快照)
          (201,'1x2','closing','nowgoal',2.5,3.3,2.88,NULL);
    """)
    conn.commit()
    conn.close()
    return old


def test_live_ingest_idempotent(odds_db: Path, small_old_db: Path,
                                tmp_path: Path, monkeypatch, capsys) -> None:
    d = tmp_path / "refetch"
    d.mkdir()
    (d / "live_nowgoal_results.json").write_text("{}")
    (d / "adjudicate_results.json").write_text(json.dumps({"5139869": {}, "4410075": {}}))
    (d / "gap_hits.json").write_text("{}")
    monkeypatch.setattr(cli, "REFETCH_DIR", d)
    monkeypatch.setattr(cli, "OLD_DB", small_old_db)
    monkeypatch.setattr(cli, "CORE_DB", small_old_db)   # dim_match 同库即可
    monkeypatch.setattr(cli, "ODDS_DB", odds_db)

    assert cli.main(["--live", "--db-path", str(odds_db), "--skip-backup"]) == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["rows_inserted"] == 6 and out1["integrity_check"] == "ok"

    conn = sqlite3.connect(str(odds_db))
    rows = conn.execute(
        """SELECT market, period, line, home_or_over, away_or_under, source
           FROM bronze_legacy_odds_summary WHERE fotmob_match_id=201
           ORDER BY market, period""").fetchall()
    assert len(rows) == 6
    assert all(r[5] == "football_uk_jka" for r in rows)
    ah_latest = next(r for r in rows if r[0] == "ah" and r[1] == "latest")
    assert ah_latest[2] == 0.5                 # 线不取反
    x2_latest = next(r for r in rows if r[0] == "1x2" and r[1] == "latest")
    assert (x2_latest[3], x2_latest[4]) == (1.9, 4.0)   # fd 值,非 watcher 值
    conn.close()

    # 重跑幂等
    assert cli.main(["--live", "--db-path", str(odds_db), "--skip-backup"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["rows_inserted"] == 0
