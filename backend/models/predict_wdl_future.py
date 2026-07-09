"""
predict_wdl_future.py — 对 26/27 英超未踢比赛出 WDL 概率预测(ROADMAP.md Phase C2)。

复用 1.M.2(build_wdl_baseline.py)固化好的模型参数——联赛基准 μ、Dixon-Coles
ρ、isotonic 校准器,全部从 backend/models/artifacts/wdl_baseline_params.pkl
加载,不重新拟合、不用 2026/2027 数据训练任何参数。

rolling 特征口径:
    26/27 还没开踢,没有"该场之前"这个概念——每支球队的 rolling xG 就是
    "截至当前所有已完赛历史(含 2025/2026 赛季末)算出的最近 N 场均值",
    同一支球队在 26/27 的全部场次共用同一份快照(和 1.M.1 的 lag 逻辑一致:
    允许跨赛季取最近 N 场;区别只是这里没有"当场"需要 shift 排除,因为
    这场比赛还没发生,直接取最近 N 场真实历史即可)。
    升班马(Hull City=8667 / Coventry City=8669)在 bronze 里没有任何英超
    历史,快照是 NULL,走 compute_lambda() 已有的 λ=μ 兜底逻辑,不是本脚本
    新写的分支。

只对 dim_match 里 Season='2026/2027' 且 status='NotStarted' 的场次出预测,
不碰任何已完赛数据,也不给这些场次填任何比分/xG。

用法:
    python backend/models/predict_wdl_future.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from schema import GOLD_WDL_PREDICTIONS_COLUMNS, FEATURE_ROLLING_STATS, _quote
from models.build_wdl_baseline import (
    ARTIFACTS_PATH,
    load_artifacts,
    compute_lambda,
    compute_raw_probs,
    apply_calibration,
)
from models.features.build_match_features import _load_raw, _extract_stats, _build_team_match_long

FUTURE_SEASON = "2026/2027"
SNAPSHOT_WINDOWS = [5, 10]


def load_future_fixtures(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT Match_ID AS match_id, League_ID AS league_id, Season AS season,
               Match_Round AS match_round, Date AS match_date,
               Home_Team_ID AS home_team_id, Away_Team_ID AS away_team_id,
               Home_Team_Name AS home_team_name, Away_Team_Name AS away_team_name
        FROM dim_match
        WHERE Season = ? AND status = 'NotStarted'
        """,
        conn,
        params=(FUTURE_SEASON,),
    )


def compute_team_snapshots(conn, team_ids_needed: set) -> pd.DataFrame:
    """每支球队"当前"的整体 rolling xG 快照:直接取最近 N 场已完赛历史的均值
    (不做 shift——这里没有"当场"要排除,要预测的比赛本身还没发生)。"""
    matches, team_stats_raw = _load_raw(conn)
    team_stats = _extract_stats(team_stats_raw)
    long_df = _build_team_match_long(matches, team_stats)
    long_df = long_df.sort_values(["team_id", "match_date", "match_id"])

    snapshots = {}
    for team_id, g in long_df.groupby("team_id"):
        n_hist = len(g)
        row = {"team_id": team_id}
        for w in SNAPSHOT_WINDOWS:
            tail = g.tail(w)
            row[f"n_matches_l{w}"] = min(n_hist, w)
            for stat in FEATURE_ROLLING_STATS:
                row[f"{stat}_l{w}"] = tail[stat].mean() if len(tail) else np.nan
        snapshots[team_id] = row

    # 需要预测的球队里,bronze 完全没有历史的(升班马):补一行全 NULL、
    # n_matches=0,让 compute_lambda() 的兜底逻辑接管,不特殊处理。
    for team_id in team_ids_needed:
        if team_id not in snapshots:
            row = {"team_id": team_id}
            for w in SNAPSHOT_WINDOWS:
                row[f"n_matches_l{w}"] = 0
                for stat in FEATURE_ROLLING_STATS:
                    row[f"{stat}_l{w}"] = np.nan
            snapshots[team_id] = row

    return pd.DataFrame(snapshots.values())


def attach_snapshots(fixtures: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    snap_cols = [f"{stat}_l{w}" for stat in FEATURE_ROLLING_STATS for w in SNAPSHOT_WINDOWS]
    snap_cols += [f"n_matches_l{w}" for w in SNAPSHOT_WINDOWS]

    home_snap = snapshots[["team_id"] + snap_cols].rename(
        columns={c: f"home_{c}" for c in snap_cols}
    )
    away_snap = snapshots[["team_id"] + snap_cols].rename(
        columns={c: f"away_{c}" for c in snap_cols}
    )

    df = fixtures.merge(home_snap, left_on="home_team_id", right_on="team_id").drop(columns="team_id")
    df = df.merge(away_snap, left_on="away_team_id", right_on="team_id").drop(columns="team_id")
    return df


def compute_confidence(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    insufficient_mask = (df["home_n_matches_l5"] < 5) | (df["away_n_matches_l5"] < 5)
    promoted_mask = (df["lambda_home_is_fallback"] == 1) | (df["lambda_away_is_fallback"] == 1)

    df["confidence"] = "normal"
    df["reason"] = None
    df.loc[insufficient_mask, "confidence"] = "low"
    df.loc[insufficient_mask, "reason"] = "insufficient_history"
    # promoted_no_history 集合⊆insufficient_history(n_matches=0 必然 <5),
    # 最后覆盖成更具体的原因。
    df.loc[promoted_mask, "confidence"] = "low"
    df.loc[promoted_mask, "reason"] = "promoted_no_history"
    return df


def write_predictions(conn, df: pd.DataFrame) -> int:
    out = df[
        [
            "match_id", "league_id", "season", "lambda_home", "lambda_away",
            "lambda_home_is_fallback", "lambda_away_is_fallback",
            "p_home", "p_draw", "p_away", "confidence", "reason",
        ]
    ].copy()
    out["calibrated"] = 1

    col_names = [name for name, _ in GOLD_WDL_PREDICTIONS_COLUMNS if name != "updated_at"]
    out = out[col_names].astype(object).where(pd.notnull(out[col_names]), None)

    conn.execute("DELETE FROM gold_wdl_predictions WHERE season = ?", (FUTURE_SEASON,))
    placeholders = ", ".join("?" for _ in col_names)
    cols_sql = ", ".join(_quote(n) for n in col_names)
    conn.executemany(
        f"INSERT INTO gold_wdl_predictions ({cols_sql}, updated_at) "
        f"VALUES ({placeholders}, datetime('now'))",
        [tuple(row) for row in out.itertuples(index=False, name=None)],
    )
    conn.commit()
    return len(out)


def main() -> None:
    # ── 第 0 步: 加载(不重新拟合)1.M.2 固化的模型参数 ──────────────────
    artifacts = load_artifacts()
    baselines = artifacts["baselines"]
    rho = artifacts["rho"]
    calibrators = artifacts["calibrators"]
    print(f"模型参数加载自: {ARTIFACTS_PATH}")
    print(f"  train_seasons(拟合时用的赛季) = {artifacts['train_seasons']}")
    print(f"  mu_home = {baselines['mu_home']:.4f}, mu_away = {baselines['mu_away']:.4f}")
    print(f"  league_avg_xg_for = {baselines['league_avg_xg_for']:.4f}, "
          f"league_avg_xg_against = {baselines['league_avg_xg_against']:.4f}")
    print(f"  rho(加载,未重新拟合) = {rho:.6f}")
    print("  未用 2026/2027 任何数据拟合上述参数。")

    conn = get_connection()
    try:
        fixtures = load_future_fixtures(conn)
        print(f"\ndim_match 里 Season={FUTURE_SEASON} 且 status='NotStarted' 的场次: {len(fixtures)}")

        team_ids_needed = set(fixtures["home_team_id"]) | set(fixtures["away_team_id"])
        snapshots = compute_team_snapshots(conn, team_ids_needed)

        promoted = snapshots[snapshots["n_matches_l10"] == 0]
        if len(promoted):
            print(f"\nbronze 无历史(n_matches=0,走兜底)的球队快照:")
            print(promoted[["team_id", "n_matches_l5", "n_matches_l10"]].to_string(index=False))

        df = attach_snapshots(fixtures, snapshots)
        df = compute_lambda(df, baselines)
        df = compute_raw_probs(df, rho)
        df = apply_calibration(df, calibrators)
        df = compute_confidence(df)

        n_written = write_predictions(conn, df)
        print(f"\ngold_wdl_predictions 写入行数(Season={FUTURE_SEASON}): {n_written}")

        print(f"\n=== confidence 分布 ===")
        print(df["confidence"].value_counts().to_string())
        print(f"\n=== reason 分布 ===")
        print(df["reason"].value_counts(dropna=False).to_string())
        print(f"\nλ 兜底(promoted_no_history 的另一种视角)场次: "
              f"home={int(df['lambda_home_is_fallback'].sum())}, "
              f"away={int(df['lambda_away_is_fallback'].sum())}")

        print(f"\n=== 样例预测(按轮次+对阵排序,前 10 场)===")
        df["_round_int"] = df["match_round"].astype(int)
        sample = df.sort_values(["_round_int", "match_id"]).head(10)
        for _, row in sample.iterrows():
            print(
                f"R{row['match_round']:>2}  {row['home_team_name']:<20} vs {row['away_team_name']:<20} "
                f"λ_home={row['lambda_home']:.2f} λ_away={row['lambda_away']:.2f}  "
                f"P(H/D/A)=({row['p_home']:.3f}/{row['p_draw']:.3f}/{row['p_away']:.3f})  "
                f"confidence={row['confidence']}({row['reason']})"
            )

        # ── 确认历史 test 段(2025/2026)没被动过 ─────────────────────────
        test_count = conn.execute(
            "SELECT COUNT(*) FROM gold_wdl_predictions WHERE season = '2025/2026'"
        ).fetchone()[0]
        print(f"\ngold_wdl_predictions 里 season='2025/2026'(test 段)行数(应仍是 380): {test_count}")
    finally:
        conn.close()

    print("\n=== predict_wdl_future 完成(停下等待人工核对预测)===")


if __name__ == "__main__":
    main()
