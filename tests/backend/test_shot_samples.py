"""模拟器射门样本库(scripts/simulator/shot_samples.py):口径、分箱、编码、大小。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "simulator"


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ss():
    return _load("shot_samples")


def test_situation_mapping_matches_params_core(ss):
    assert ss.SITUATION_CHANNEL == _load("params_core").SITUATION_CHANNEL


def test_result_code(ss):
    assert ss.result_code("Goal", None) == ss.RESULT_GOAL
    assert ss.result_code("Post", 0) == ss.RESULT_POST
    assert ss.result_code("Miss", 0) == ss.RESULT_MISS
    assert ss.result_code("AttemptSaved", 1) == ss.RESULT_BLOCKED
    assert ss.result_code("AttemptSaved", 0) == ss.RESULT_SAVED
    # 扑救 / 封堵分不清的不要(否则扑救与封堵比例失真)
    assert ss.result_code("AttemptSaved", None) is None


def _db(tmp_path: Path, rows: list[tuple]) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "allwin.db")
    con.execute("CREATE TABLE dim_match (Match_ID INTEGER, League_ID INTEGER, Season TEXT)")
    con.execute(
        """CREATE TABLE fact_shotmap (Match_ID INTEGER, Shot_ID INTEGER, X_Coord REAL, Y_Coord REAL, xG REAL,
           Situation TEXT, Shot_Type TEXT, Outcome TEXT, Is_Blocked INTEGER, Is_Own_Goal INTEGER,
           Goal_Crossed_Y REAL, Goal_Crossed_Z REAL, Blocked_X REAL, Blocked_Y REAL)"""
    )
    con.executemany("INSERT INTO dim_match VALUES (?,?,?)", [(1, 47, "2026/2027"), (2, 42, "2026/2027"), (3, 47, "2023/2024")])
    con.executemany("INSERT INTO fact_shotmap VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return con


def _shot(match, sid, xg, situation="RegularPlay", outcome="Miss", blocked=0, own=0, x=95.0, y=30.0, gy=None, gz=None, bx=None, by=None):
    return (match, sid, x, y, xg, situation, "RightFoot", outcome, blocked, own, gy, gz, bx, by)


def test_load_shots_filters(ss, tmp_path):
    con = _db(tmp_path, [
        _shot(1, 1, 0.1),
        _shot(1, 2, None, outcome="Goal"),                 # xG 为空 = 乌龙球
        _shot(1, 3, 0.1, own=1, outcome="Goal"),           # 明确乌龙
        _shot(1, 4, 0.1, outcome="AttemptSaved", blocked=None),  # 扑救/封堵分不清
        _shot(1, 5, 0.1, situation="Unknown"),
        _shot(2, 6, 0.1),                                  # 非五大联赛
        _shot(3, 7, 0.1),                                  # 旧赛季
        _shot(1, 8, 0.1, outcome="AttemptSaved", blocked=1, bx=90.0, by=31.0),
    ])
    shots = ss.load_shots(con, (47, 87, 55, 54, 53), ("2025/2026", "2026/2027"))
    assert sorted(s["id"] for s in shots) == [1, 8]
    assert {s["id"]: s["result"] for s in shots} == {1: ss.RESULT_MISS, 8: ss.RESULT_BLOCKED}


def test_bin_index_boundaries(ss):
    edges = [0.0, 0.1, 0.2, 1.0]
    assert ss.bin_index(edges, 0.0) == 0
    assert ss.bin_index(edges, 0.0999) == 0
    assert ss.bin_index(edges, 0.1) == 1
    assert ss.bin_index(edges, 0.95) == 2
    assert ss.bin_index(edges, 1.4) == 2  # 超出上界落最后一箱


def test_encode_endpoints(ss):
    base = {"x": 95.04, "y": 30.06, "type": 1, "gy": None, "gz": None, "bx": None, "by": None}
    assert ss._encode({**base, "result": ss.RESULT_MISS}) == [95.0, 30.1, 1, 3]
    assert ss._encode({**base, "result": ss.RESULT_MISS, "gy": 38.123, "gz": 2.901}) == [95.0, 30.1, 1, 3, 38.12, 2.9]
    # 封堵:终点是封堵点,不是越线点
    assert ss._encode({**base, "result": ss.RESULT_BLOCKED, "gy": 34.0, "gz": 0.5, "bx": 97.24, "by": 31.66}) == [95.0, 30.1, 1, 2, 97.2, 31.7]


def test_build_prefers_endpoint_and_is_deterministic(ss):
    shots = []
    for i in range(400):
        shots.append({"id": i + 1, "x": 90.0, "y": 34.0, "xg": (i % 100) / 100, "channel": "open", "type": 0,
                      "result": ss.RESULT_MISS, "gy": 40.0 if i < 100 else None, "gz": 1.0 if i < 100 else None,
                      "bx": None, "by": None})
    shots.append({"id": 999, "x": 94.0, "y": 34.0, "xg": 0.79, "channel": "penalty", "type": 0, "result": ss.RESULT_SAVED,
                  "gy": 33.0, "gz": 0.4, "bx": None, "by": None})
    a = ss.build_shot_samples(shots, (47,), ("2026/2027",))
    b = ss.build_shot_samples(list(reversed(shots)), (47,), ("2026/2027",))
    assert a == b
    ch = a["channels"]["open"]
    assert len(ch["edges"]) == ss.N_BINS + 1 and ch["edges"][0] == 0.0 and ch["edges"][-1] == 1.0
    # 每箱 20 脚(5 个 xG 值 × 4)、每个 xG 值恰有 1 脚带终点 → 每箱 5 脚有终点,且全部入选
    assert all(sum(1 for s in b_ if len(s) == 6) == 5 for b_ in ch["bins"])
    assert a["channels"]["penalty"]["bins"][0] == [[94.0, 34.0, 0, 1, 33.0, 0.4]]
    assert a["channels"]["counter"]["bins"] == [[] for _ in range(ss.N_BINS)]


def test_library_size_budget(ss):
    # 满额样本库(每箱都塞满、每脚都带终点)也不超过 60KB
    full = []
    sid = 0
    for ch, k in ss.PER_BIN.items():
        for b in range(ss.N_BINS):
            for _ in range(k + 5):
                sid += 1
                full.append({"id": sid, "x": 101.37, "y": 33.91, "xg": (b + 0.5) / ss.N_BINS, "channel": ch, "type": 3,
                             "result": ss.RESULT_MISS, "gy": 37.91, "gz": 2.49, "bx": None, "by": None})
    for _ in range(ss.PENALTY_SAMPLES + 5):
        sid += 1
        full.append({"id": sid, "x": 94.0, "y": 34.0, "xg": 0.79, "channel": "penalty", "type": 0, "result": ss.RESULT_GOAL,
                     "gy": 36.52, "gz": 0.44, "bx": None, "by": None})
    lib = ss.build_shot_samples(full, (47, 87, 55, 54, 53), ("2025/2026", "2026/2027"))
    size = len(json.dumps(lib, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size <= 60_000, size
