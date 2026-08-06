from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.cli.ingest_nowgoal_historical_odds import (
    _canonical_payload,
    _payload_hash,
    compute_silver_moves,
    discover_shards,
    ingest_bronze,
    upsert_xref,
)


# ── _canonical_payload: 核心语义正确性(见模块 docstring 的真实核对证据)──


def test_1x2_passthrough_non_inverted() -> None:
    row = {"market": "1x2", "values": {"home_win": "1.44", "draw": "4.75", "away_win": "6.50"}}
    assert _canonical_payload(row, False) == {"home": 1.44, "draw": 4.75, "away": 6.5}


def test_1x2_swaps_when_inverted() -> None:
    row = {"market": "1x2", "values": {"home_win": "1.44", "draw": "4.75", "away_win": "6.50"}}
    assert _canonical_payload(row, True) == {"home": 6.5, "draw": 4.75, "away": 1.44}


def test_ah_maps_u_home_d_away_g_line_non_inverted() -> None:
    # 真实数据:曼联(主)9-0 南安普顿(客),客队大冷门,g=+1.25 与验证结论一致
    row = {"market": "ah", "values": {"odds": {"d": "0.91", "g": "1.25", "u": "0.99"}}}
    assert _canonical_payload(row, False) == {"home": 0.99, "line": 1.25, "away": 0.91}


def test_ah_swaps_sides_and_negates_line_when_inverted() -> None:
    row = {"market": "ah", "values": {"odds": {"d": "0.91", "g": "1.25", "u": "0.99"}}}
    assert _canonical_payload(row, True) == {"home": 0.91, "line": -1.25, "away": 0.99}


def test_ou_maps_u_over_d_under_g_line() -> None:
    row = {"market": "ou", "values": {"odds": {"d": "0.86", "g": "2.5", "u": "1.04"}}}
    assert _canonical_payload(row, False) == {"over": 1.04, "line": 2.5, "under": 0.86}


def test_ou_unaffected_by_inversion() -> None:
    # OU 对称,不因主客队反转而换边——总进球盘口与主客队身份无关
    row = {"market": "ou", "values": {"odds": {"d": "0.86", "g": "2.5", "u": "1.04"}}}
    assert _canonical_payload(row, True) == _canonical_payload(row, False)


def test_missing_slot_returns_none() -> None:
    row = {"market": "ah", "values": {"odds": {"d": "0.91", "u": "0.99"}}}
    assert _canonical_payload(row, False) is None


def test_non_numeric_slot_returns_none() -> None:
    row = {"market": "ah", "values": {"odds": {"d": "n/a", "g": "1.25", "u": "0.99"}}}
    assert _canonical_payload(row, False) is None


def test_unknown_market_returns_none() -> None:
    assert _canonical_payload({"market": "corners", "values": {}}, False) is None


def test_payload_hash_stable_regardless_of_key_order() -> None:
    a = _payload_hash({"home": 1.0, "line": 0.5, "away": 2.0})
    b = _payload_hash({"away": 2.0, "line": 0.5, "home": 1.0})
    assert a == b


# ── discover_shards: 跳过隔离目录、要求完整产物 ──


def test_discover_shards_skips_underscore_prefixed(tmp_path: Path, monkeypatch) -> None:
    import backend.cli.ingest_nowgoal_historical_odds as mod

    root = tmp_path / "backfill-root"
    good = root / "premier_league-2020-2021"
    (good / "normalized").mkdir(parents=True)
    (good / "progress.json").write_text("{}")
    (good / "normalized" / "odds-history.jsonl").write_text("")

    quarantine = root / "_quarantine-something"
    (quarantine / "normalized").mkdir(parents=True)
    (quarantine / "progress.json").write_text("{}")
    (quarantine / "normalized" / "odds-history.jsonl").write_text("")

    incomplete = root / "la_liga-2020-2021"
    incomplete.mkdir(parents=True)
    (incomplete / "progress.json").write_text("{}")
    # 没有 normalized/odds-history.jsonl -> 不应该被发现

    monkeypatch.setattr(mod, "BACKFILL_ROOT", root)
    shards = discover_shards()
    names = [name for name, _ in shards]
    assert names == ["premier_league-2020-2021"]


# ── 端到端:真实 schema 上跑一遍最小 fixture,校验 xref/bronze/silver 落地正确 ──


_SCHEMA_SQL = (Path(__file__).resolve().parents[2] / "backend" / "migrations" / "odds" / "0001_init.sql").read_text()


@pytest.fixture()
def odds_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()
    return db_path


def test_ingest_bronze_and_silver_end_to_end(odds_db: Path) -> None:
    conn = sqlite3.connect(str(odds_db))
    conn.execute("PRAGMA foreign_keys = ON")

    xref_rows = [
        {
            "titan_id": "9001",
            "match_id": 555,
            "home_away_inverted": 0,
            "resolution_evidence_kind": "id",
        }
    ]
    xref_n = upsert_xref(conn, xref_rows)
    assert xref_n == 1

    history_path = odds_db.parent / "odds-history.jsonl"
    rows = [
        {
            "titan_id": "9001",
            "market": "ah",
            "company_id": "8",
            "company_name": "Bet365",
            "phase": "PRE_MATCH",
            "observed_at": "2021-02-02T19:00:00Z",
            "values": {"odds": {"d": "0.95", "g": "-0.5", "u": "0.95"}},
        },
        {
            "titan_id": "9001",
            "market": "ah",
            "company_id": "8",
            "company_name": "Bet365",
            "phase": "PRE_MATCH",
            "observed_at": "2021-02-02T19:30:00Z",
            "values": {"odds": {"d": "0.90", "g": "-0.5", "u": "1.00"}},
        },
        {
            # in-play 快照必须被跳过,不进 bronze
            "titan_id": "9001",
            "market": "ah",
            "company_id": "8",
            "company_name": "Bet365",
            "phase": "IN_PLAY_EXCLUDED",
            "observed_at": "2021-02-02T20:05:00Z",
            "values": {"odds": {"d": "0.80", "g": "-0.5", "u": "1.10"}},
        },
    ]
    history_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    inverted = {row["titan_id"]: bool(row["home_away_inverted"]) for row in xref_rows}
    bronze_n, bad_n = ingest_bronze(conn, "test-shard", history_path, inverted)
    assert bad_n == 0
    assert bronze_n == 2  # in-play 行被跳过

    phases = [r[0] for r in conn.execute("SELECT market_phase FROM bronze_ng_odds_snap").fetchall()]
    assert phases == ["pre_match", "pre_match"]

    silver_n = compute_silver_moves(conn, {"9001"})
    # 两次快照之间 home(u:0.95->1.00) 和 away(d:0.95->0.90) 都变了,line 没变
    fields_changed = {
        r[0] for r in conn.execute("SELECT field FROM silver_odds_moves").fetchall()
    }
    assert fields_changed == {"home", "away"}
    assert silver_n == 2

    fotmob_id = conn.execute(
        "SELECT fotmob_match_id FROM silver_odds_moves LIMIT 1"
    ).fetchone()[0]
    assert fotmob_id == 555

    conn.close()


def test_ingest_bronze_is_idempotent_on_rerun(odds_db: Path) -> None:
    conn = sqlite3.connect(str(odds_db))
    xref_rows = [{"titan_id": "9002", "match_id": 556, "home_away_inverted": 0, "resolution_evidence_kind": "name"}]
    upsert_xref(conn, xref_rows)

    history_path = odds_db.parent / "odds-history-2.jsonl"
    row = {
        "titan_id": "9002",
        "market": "1x2",
        "company_id": "281",
        "company_name": "bet365",
        "phase": "PRE_MATCH",
        "observed_at": "2021-02-02T19:00:00Z",
        "values": {"home_win": "1.50", "draw": "4.00", "away_win": "6.00"},
    }
    history_path.write_text(json.dumps(row) + "\n")
    inverted = {"9002": False}

    first_n, _ = ingest_bronze(conn, "shard", history_path, inverted)
    second_n, _ = ingest_bronze(conn, "shard", history_path, inverted)
    assert first_n == 1
    assert second_n == 0  # 同一个 (provider_match_id, market, company_id, observed_at) 不会重复插入

    total = conn.execute("SELECT COUNT(*) FROM bronze_ng_odds_snap").fetchone()[0]
    assert total == 1
    conn.close()


def test_upsert_xref_confidence_by_evidence_kind(odds_db: Path) -> None:
    conn = sqlite3.connect(str(odds_db))
    upsert_xref(
        conn,
        [
            {"titan_id": "1", "match_id": 100, "home_away_inverted": 0, "resolution_evidence_kind": "id"},
            {"titan_id": "2", "match_id": 101, "home_away_inverted": 0, "resolution_evidence_kind": "name"},
        ],
    )
    rows = dict(
        conn.execute("SELECT provider_match_id, confidence FROM dim_match_xref").fetchall()
    )
    assert rows["1"] == pytest.approx(0.95)
    assert rows["2"] == pytest.approx(0.75)
    conn.close()
