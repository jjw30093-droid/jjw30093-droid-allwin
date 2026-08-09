"""P0 回归:predict_wdl_future 的 League_ID 谓词不能让别的联赛的正式预测消失/被顶替。

背景(本轮任务):load_future_fixtures 原来只按 Season='2026/2027' + status='NotStarted'
筛选,没有 League_ID 谓词——一旦 dim_match 里出现别的联赛(西甲/意甲/德甲/法甲)同赛季的
NotStarted 行,该脚本会把这些非英超比赛也当成预测目标,并且 write_predictions 里的
`DELETE FROM gold_wdl_predictions WHERE season=?` 同样没有联赛谓词,会把已经写入的其它
联赛记录一并删除。wdl_baseline_params.pkl 只用英超历史拟合,对其它联赛的"预测"没有
统计意义,一旦流入 prediction_register/import_gold_predictions 链路,按 CLAUDE.md §9.1
会永久进入公开预测账本、不可删除,所以这里必须在源头堵住。
"""

import importlib.util
import os

import pytest

from backend.db.connections import connect_rw
from backend.db.paths import db_path
from backend.init_db import init_db


def _load_module():
    """predict_wdl_future.py 是历史遗留的独立脚本风格(`from db import get_connection`,
    非 `backend.db`),用 importlib 按文件路径加载,不走包导入,避免污染 sys.modules 里的
    `backend.db`/`db` 命名空间。"""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(repo_root, "backend", "models", "predict_wdl_future.py")
    spec = importlib.util.spec_from_file_location("predict_wdl_future_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load_module()


@pytest.fixture
def core_schema(data_dir):
    """data_dir 只跑 migrate.apply_all,不建 gold_wdl_predictions(那是 init_db.py 的legacy
    bronze/silver/gold 表,历史上不走 migration runner)——这里补建,幂等(CREATE TABLE IF NOT EXISTS)。"""
    init_db(db_path("core"), quiet=True)
    return data_dir


def _insert_match(conn, match_id, league_id, season="2026/2027", status="NotStarted"):
    conn.execute(
        """
        INSERT INTO dim_match
            (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
             Home_Team_Name, Away_Team_Name, status, Match_Round,
             kickoff_at_utc, kickoff_precision, kickoff_source)
        VALUES (?, ?, ?, '2026-08-22', 100, 200, 'Home', 'Away', ?, '1',
                '2026-08-22T14:00:00Z', 'exact', 'fotmob:fixtures')
        """,
        (match_id, season, league_id, status),
    )


def _insert_gold_row(conn, match_id, league_id, season="2026/2027"):
    conn.execute(
        """
        INSERT INTO gold_wdl_predictions
            (match_id, league_id, season, lambda_home, lambda_away,
             lambda_home_is_fallback, lambda_away_is_fallback,
             p_home, p_draw, p_away, calibrated, confidence, reason, updated_at)
        VALUES (?, ?, ?, 1.2, 1.0, 0, 0, 0.4, 0.3, 0.3, 1, 'normal', NULL,
                datetime('now'))
        """,
        (match_id, league_id, season),
    )


def test_load_future_fixtures_scopes_to_league_id(data_dir, mod):
    conn = connect_rw("core")
    try:
        _insert_match(conn, match_id=1, league_id=47)   # 英超,应命中
        _insert_match(conn, match_id=2, league_id=87)   # 西甲,不应命中
        conn.commit()

        default_scoped = mod.load_future_fixtures(conn)
        assert set(default_scoped["match_id"]) == {1}

        explicit_other = mod.load_future_fixtures(conn, league_id=87)
        assert set(explicit_other["match_id"]) == {2}
    finally:
        conn.close()


def test_write_predictions_does_not_touch_other_leagues_gold_rows(core_schema, mod):
    conn = connect_rw("core")
    try:
        # 模拟"西甲已经有一条正式预测存在"(例如未来某天西甲模型上线后写入的)。
        _insert_gold_row(conn, match_id=2, league_id=87)
        conn.commit()

        import pandas as pd

        df = pd.DataFrame(
            [
                {
                    "match_id": 1, "league_id": 47, "season": "2026/2027",
                    "lambda_home": 1.5, "lambda_away": 1.1,
                    "lambda_home_is_fallback": 0, "lambda_away_is_fallback": 0,
                    "p_home": 0.5, "p_draw": 0.3, "p_away": 0.2,
                    "confidence": "normal", "reason": None,
                }
            ]
        )
        n = mod.write_predictions(conn, df, league_id=47)
        assert n == 1

        rows = conn.execute(
            "SELECT match_id, league_id FROM gold_wdl_predictions WHERE season='2026/2027' ORDER BY match_id"
        ).fetchall()
        assert {(r[0], r[1]) for r in rows} == {(1, 47), (2, 87)}
    finally:
        conn.close()


def test_write_predictions_overwrites_only_its_own_league(core_schema, mod):
    """同一联赛重跑要能覆盖旧结果(DELETE+INSERT 的存在意义),但不能波及别的联赛。"""
    conn = connect_rw("core")
    try:
        _insert_gold_row(conn, match_id=1, league_id=47)   # 旧的英超预测
        _insert_gold_row(conn, match_id=2, league_id=87)   # 西甲预测,必须存活
        conn.commit()

        import pandas as pd

        df = pd.DataFrame(
            [
                {
                    "match_id": 1, "league_id": 47, "season": "2026/2027",
                    "lambda_home": 2.0, "lambda_away": 0.9,
                    "lambda_home_is_fallback": 0, "lambda_away_is_fallback": 0,
                    "p_home": 0.6, "p_draw": 0.25, "p_away": 0.15,
                    "confidence": "normal", "reason": None,
                }
            ]
        )
        mod.write_predictions(conn, df, league_id=47)

        rows = conn.execute(
            "SELECT match_id, league_id, p_home FROM gold_wdl_predictions "
            "WHERE season='2026/2027' ORDER BY match_id"
        ).fetchall()
        assert len(rows) == 2
        by_match = {r[0]: (r[1], r[2]) for r in rows}
        assert by_match[1] == (47, 0.6)   # 英超那条已被新结果覆盖
        assert by_match[2] == (87, 0.4)   # 西甲那条(来自 _insert_gold_row,p_home=0.4)原封不动
    finally:
        conn.close()
