"""backend.cli.fix_legacy_odds_float_noise:一次性清洗浮点 ULP 噪声的 CLI。

真实事故(2026-08-21):bronze_legacy_odds_summary 88,457 行里有 1,910 行
在写入源头就带了 IEEE754 ULP 噪声(如 1.93 存成 1.9300000000000002)——
本地库已用这个 CLI 实际清洗过一遍(见 fix_legacy_odds_float_noise.py
docstring),这里钉住 CLI 本身的行为:dry-run 不改数据、live 精确清洗、
备份文件真实生成、二次运行幂等(0 行可清)。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import backend.cli.fix_legacy_odds_float_noise as cli

_ODDS_SCHEMA = Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds"


@pytest.fixture()
def odds_db(tmp_path: Path) -> Path:
    db = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db))
    for mig in ("0001_init.sql", "0004_legacy_odds_summary.sql", "0005_legacy_source_jka.sql"):
        conn.executescript((_ODDS_SCHEMA / mig).read_text())
    conn.execute(
        """INSERT INTO bronze_legacy_odds_summary
             (fotmob_match_id, source, provider, market, period, line,
              home_or_over, draw, away_or_under, orientation_fixed,
              source_file, ingested_at)
           VALUES
             (1, 'football_uk_jka', 'Bet365', 'ah', 'initial', -0.25,
              1.9300000000000002, NULL, 1.88, 0, 't', 'x'),
             (2, 'football_uk_jka', 'Bet365', '1x2', 'latest', NULL,
              2.05, 3.4, 3.8, 0, 't', 'x')"""
    )
    conn.commit()
    conn.close()
    return db


def test_dry_run_finds_bad_rows_without_modifying(odds_db: Path, capsys) -> None:
    assert cli.main(["--dry-run", "--db-path", str(odds_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"mode": "DRY_RUN", "corrupted_rows": 1, "by_source": {"football_uk_jka": 1}}

    conn = sqlite3.connect(str(odds_db))
    v = conn.execute("SELECT home_or_over FROM bronze_legacy_odds_summary WHERE id=1").fetchone()[0]
    assert v == 1.9300000000000002    # dry-run 绝不改数据
    conn.close()


def test_live_cleans_bad_row_and_leaves_clean_row_untouched(odds_db: Path, capsys) -> None:
    assert cli.main(["--live", "--db-path", str(odds_db), "--skip-backup"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows_updated"] == 1
    assert out["remaining_corrupted_rows"] == 0
    assert out["integrity_check"] == "ok"

    conn = sqlite3.connect(str(odds_db))
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, home_or_over FROM bronze_legacy_odds_summary")}
    assert rows[1] == 1.93
    assert rows[2] == 2.05            # 本来就干净的行数值不变
    conn.close()


def test_live_creates_backup_file(odds_db: Path, capsys) -> None:
    cli.main(["--live", "--db-path", str(odds_db)])
    out = json.loads(capsys.readouterr().out)
    backup_path = Path(out["backup_path"])
    assert backup_path.exists()
    assert backup_path.parent == odds_db.parent


def test_live_idempotent_second_run_finds_nothing(odds_db: Path, capsys) -> None:
    cli.main(["--live", "--db-path", str(odds_db), "--skip-backup"])
    capsys.readouterr()
    assert cli.main(["--live", "--db-path", str(odds_db), "--skip-backup"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows_updated"] == 0
