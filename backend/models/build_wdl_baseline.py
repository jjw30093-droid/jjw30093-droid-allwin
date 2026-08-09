"""
build_wdl_baseline.py — 极简 baseline WDL 概率卡(ROADMAP.md Phase 1.M.2,路线 1)。

不训练树模型/神经网络,纯统计方法:
    1. 用 rolling xG(l10)估进攻强度 × 防守弱度 × 联赛基准 -> λ_home / λ_away
       (经典 Dixon-Coles 式拆解,但强度直接取自 int_match_features 已算好的
       rolling xG,不额外做队伍参数 MLE)。
    2. 独立 Poisson 比分矩阵 + Dixon-Coles ρ 低比分修正。
    3. 从修正后的矩阵求 P(主胜/平/客胜),做 isotonic 校准。
    4. walk-forward 评估:train=2020/2021~2024/2025,test=2025/2026。
       所有"拟合"(联赛基准 μ、全联盟 rolling 均值、ρ、isotonic 校准器)
       只用 train 段;test 段只用来算指标,不参与任何拟合。

只建模型 + 评估,不碰赔率、不做前端。test 段的最终结果写入 gold_wdl_predictions。

用法:
    python backend/models/build_wdl_baseline.py
"""

import os
import pickle
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import poisson
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from schema import GOLD_WDL_PREDICTIONS_COLUMNS, _quote

TRAIN_SEASONS = ["2020/2021", "2021/2022", "2022/2023", "2023/2024", "2024/2025"]

# dc-baseline-1.M.2 的训练范围是且仅是 EPL(model_versions.applicable_league_ids
# =[47])。season 字符串在五大联赛间同名("2025/2026"),特征表如今可含多联赛,
# 无联赛谓词的 train/test 划分会把其它联赛场次静默卷进来,联赛基准与 ρ 全部
# 漂移(2026-08-07 审计缺陷 #4)。
LEAGUE_ID = 47

# 固化后的模型参数(ρ / isotonic 校准器 / 联赛基准)存这里,供 C2(对未来赛程
# 出预测)加载复用,不重新拟合。模型文件不进 git(见 .gitignore 的 *.pkl)。
ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
ARTIFACTS_PATH = os.path.join(ARTIFACTS_DIR, "wdl_baseline_params.pkl")


def save_artifacts(baselines: dict, rho: float, calibrators: dict) -> None:
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    with open(ARTIFACTS_PATH, "wb") as f:
        pickle.dump(
            {
                "train_seasons": TRAIN_SEASONS,
                "baselines": baselines,
                "rho": rho,
                "calibrators": calibrators,
            },
            f,
        )
    print(f"\n模型参数已固化: {ARTIFACTS_PATH}")


def load_artifacts() -> dict:
    with open(ARTIFACTS_PATH, "rb") as f:
        return pickle.load(f)
TEST_SEASON = "2025/2026"
N_GOALS = 10  # 比分矩阵覆盖 0..10 球,数据集实际最大进球数为 9,足够
RHO_BOUNDS = (-0.2, 0.1)


# ── Step 1: rolling xG -> λ ──────────────────────────────────────────
def load_features(conn) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT imf.match_id, imf.league_id, imf.season, imf.match_date,
               imf.home_team_id, imf.away_team_id,
               imf.home_xg_for_l10, imf.away_xg_against_l10,
               imf.away_xg_for_l10, imf.home_xg_against_l10,
               imf.target_home_goals, imf.target_away_goals, imf.sample_weight,
               dm.Home_Team_Name AS home_team_name, dm.Away_Team_Name AS away_team_name
        FROM int_match_features imf
        JOIN dim_match dm ON imf.match_id = dm.Match_ID
        WHERE imf.league_id = ?
        """,
        conn,
        params=(LEAGUE_ID,),
    )
    return df


def compute_league_baselines(train_df: pd.DataFrame) -> dict:
    mu_home = train_df["target_home_goals"].mean()
    mu_away = train_df["target_away_goals"].mean()
    # "全联盟场均 xg_for/xg_against":把 train 段主客两侧的 rolling xG 合并成一个池子取均值，
    # 代表一支"平均球队"的 rolling 攻防强度基准，用来把每队的 rolling xG 归一化成相对强度。
    league_avg_xg_for = pd.concat(
        [train_df["home_xg_for_l10"], train_df["away_xg_for_l10"]]
    ).mean()
    league_avg_xg_against = pd.concat(
        [train_df["home_xg_against_l10"], train_df["away_xg_against_l10"]]
    ).mean()
    return {
        "mu_home": mu_home,
        "mu_away": mu_away,
        "league_avg_xg_for": league_avg_xg_for,
        "league_avg_xg_against": league_avg_xg_against,
    }


def compute_lambda(df: pd.DataFrame, baselines: dict) -> pd.DataFrame:
    df = df.copy()
    mu_home, mu_away = baselines["mu_home"], baselines["mu_away"]
    avg_for, avg_against = baselines["league_avg_xg_for"], baselines["league_avg_xg_against"]

    att_home = df["home_xg_for_l10"] / avg_for
    def_away = df["away_xg_against_l10"] / avg_against
    att_away = df["away_xg_for_l10"] / avg_for
    def_home = df["home_xg_against_l10"] / avg_against

    lambda_home_model = mu_home * att_home * def_away
    lambda_away_model = mu_away * att_away * def_home

    home_fallback = df["home_xg_for_l10"].isna() | df["away_xg_against_l10"].isna()
    away_fallback = df["away_xg_for_l10"].isna() | df["home_xg_against_l10"].isna()

    df["lambda_home"] = np.where(home_fallback, mu_home, lambda_home_model)
    df["lambda_away"] = np.where(away_fallback, mu_away, lambda_away_model)
    df["lambda_home_is_fallback"] = home_fallback.astype(int)
    df["lambda_away_is_fallback"] = away_fallback.astype(int)
    return df


# ── Step 2: Dixon-Coles ρ + 比分矩阵 ──────────────────────────────────
def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def fit_rho(train_df: pd.DataFrame) -> float:
    x = train_df["target_home_goals"].to_numpy()
    y = train_df["target_away_goals"].to_numpy()
    lam = train_df["lambda_home"].to_numpy()
    mu = train_df["lambda_away"].to_numpy()
    w = train_df["sample_weight"].to_numpy()

    # tau 只在 (0,0)/(0,1)/(1,0)/(1,1) 不等于 1,只有这些场次的对数似然
    # 依赖 rho,其余场次的贡献是常数,优化时可以忽略(不影响 argmax)。
    mask = (x <= 1) & (y <= 1)
    x, y, lam, mu, w = x[mask], y[mask], lam[mask], mu[mask], w[mask]

    def neg_log_lik(rho: float) -> float:
        taus = np.array([_tau(xi, yi, li, mi, rho) for xi, yi, li, mi in zip(x, y, lam, mu)])
        if np.any(taus <= 0):
            return 1e10
        return -np.sum(w * np.log(taus))

    result = minimize_scalar(neg_log_lik, bounds=RHO_BOUNDS, method="bounded")
    return float(result.x)


def build_score_matrix(lam: float, mu: float, rho: float, n: int = N_GOALS) -> np.ndarray:
    home_pmf = poisson.pmf(np.arange(n + 1), lam)
    away_pmf = poisson.pmf(np.arange(n + 1), mu)
    matrix = np.outer(home_pmf, away_pmf)

    matrix[0, 0] *= _tau(0, 0, lam, mu, rho)
    matrix[0, 1] *= _tau(0, 1, lam, mu, rho)
    matrix[1, 0] *= _tau(1, 0, lam, mu, rho)
    matrix[1, 1] *= _tau(1, 1, lam, mu, rho)

    matrix = matrix / matrix.sum()
    return matrix


# ── Step 3: 派生 WDL ─────────────────────────────────────────────────
def matrix_to_wdl(matrix: np.ndarray) -> tuple:
    p_home = np.tril(matrix, -1).sum()
    p_draw = np.trace(matrix)
    p_away = np.triu(matrix, 1).sum()
    return p_home, p_draw, p_away


def compute_raw_probs(df: pd.DataFrame, rho: float) -> pd.DataFrame:
    df = df.copy()
    p_home_list, p_draw_list, p_away_list = [], [], []
    for lam, mu in zip(df["lambda_home"], df["lambda_away"]):
        matrix = build_score_matrix(lam, mu, rho)
        ph, pd_, pa = matrix_to_wdl(matrix)
        p_home_list.append(ph)
        p_draw_list.append(pd_)
        p_away_list.append(pa)
    df["p_home_raw"] = p_home_list
    df["p_draw_raw"] = p_draw_list
    df["p_away_raw"] = p_away_list
    return df


def fit_isotonic_calibrators(train_df: pd.DataFrame) -> dict:
    """三分类用 one-vs-rest 分别做 isotonic:对每个类别把"模型输出的该类概率"
    映射到"该类真实发生频率",三个类别各训一个单变量 isotonic 回归器。
    校准后三个概率不再天然归一(各自独立映射),应用时要重新归一化。"""
    outcomes = np.where(
        train_df["target_home_goals"] > train_df["target_away_goals"], "home",
        np.where(train_df["target_home_goals"] == train_df["target_away_goals"], "draw", "away"),
    )
    calibrators = {}
    for cls in ("home", "draw", "away"):
        y = (outcomes == cls).astype(int)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(train_df[f"p_{cls}_raw"], y, sample_weight=train_df["sample_weight"])
        calibrators[cls] = iso
    return calibrators


def apply_calibration(df: pd.DataFrame, calibrators: dict) -> pd.DataFrame:
    df = df.copy()
    for cls in ("home", "draw", "away"):
        df[f"p_{cls}_cal_raw"] = calibrators[cls].predict(df[f"p_{cls}_raw"])
    total = df["p_home_cal_raw"] + df["p_draw_cal_raw"] + df["p_away_cal_raw"]
    df["p_home"] = df["p_home_cal_raw"] / total
    df["p_draw"] = df["p_draw_cal_raw"] / total
    df["p_away"] = df["p_away_cal_raw"] / total
    return df


# ── Step 4: 评估指标 ──────────────────────────────────────────────────
def rps(p_home: np.ndarray, p_draw: np.ndarray, p_away: np.ndarray, actual: np.ndarray) -> float:
    """actual: 0=home win, 1=draw, 2=away win。三分类 RPS,除以 (m-1)=2。"""
    cum_pred_1 = p_home
    cum_pred_2 = p_home + p_draw
    cum_actual_1 = (actual == 0).astype(float)
    cum_actual_2 = (actual != 2).astype(float)
    rps_per_match = 0.5 * ((cum_pred_1 - cum_actual_1) ** 2 + (cum_pred_2 - cum_actual_2) ** 2)
    return float(rps_per_match.mean())


def brier(p_home: np.ndarray, p_draw: np.ndarray, p_away: np.ndarray, actual: np.ndarray) -> float:
    onehot = np.zeros((len(actual), 3))
    onehot[np.arange(len(actual)), actual] = 1
    preds = np.stack([p_home, p_draw, p_away], axis=1)
    return float(np.mean(np.sum((preds - onehot) ** 2, axis=1)))


def calibration_table(p_home: np.ndarray, actual_home_win: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    bins = pd.qcut(p_home, n_bins, duplicates="drop")
    tmp = pd.DataFrame({"bin": bins, "p_home": p_home, "actual": actual_home_win})
    grouped = tmp.groupby("bin", observed=True).agg(
        pred_mean=("p_home", "mean"), actual_freq=("actual", "mean"), n=("actual", "size")
    )
    return grouped.reset_index(drop=True)


def write_predictions(conn, test_df: pd.DataFrame) -> int:
    out = test_df[
        [
            "match_id", "league_id", "season", "lambda_home", "lambda_away",
            "lambda_home_is_fallback", "lambda_away_is_fallback",
            "p_home", "p_draw", "p_away",
        ]
    ].copy()
    out["calibrated"] = 1
    # confidence/reason 是 C2(未来赛程预测)才有的概念,test 段(已完赛的
    # walk-forward 评估行)固定为 NULL。
    out["confidence"] = None
    out["reason"] = None

    col_names = [name for name, _ in GOLD_WDL_PREDICTIONS_COLUMNS if name != "updated_at"]
    out = out[col_names].astype(object).where(pd.notnull(out[col_names]), None)

    # league_id 谓词与 predict_wdl_future.py 的已修复缺陷同型:无谓词会把其它
    # 联赛同名赛季的 gold 行连带删除(test_predict_wdl_future.py 论证的风险)。
    conn.execute(
        "DELETE FROM gold_wdl_predictions WHERE season = ? AND league_id = ?",
        (TEST_SEASON, LEAGUE_ID),
    )
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
    conn = get_connection()
    try:
        df = load_features(conn)
        train_df = df[df["season"].isin(TRAIN_SEASONS)].copy()
        test_df = df[df["season"] == TEST_SEASON].copy()
        print(f"train 段场次: {len(train_df)}({TRAIN_SEASONS[0]}~{TRAIN_SEASONS[-1]})")
        print(f"test  段场次: {len(test_df)}({TEST_SEASON})")

        # Step 1: λ
        baselines = compute_league_baselines(train_df)
        print()
        print("=== 联赛基准(仅用 train 段算)===")
        print(f"mu_home(train 场均主队进球) = {baselines['mu_home']:.4f}")
        print(f"mu_away(train 场均客队进球) = {baselines['mu_away']:.4f}")
        print(f"league_avg_xg_for(train 全联盟场均 xg_for, l10)     = {baselines['league_avg_xg_for']:.4f}")
        print(f"league_avg_xg_against(train 全联盟场均 xg_against, l10) = {baselines['league_avg_xg_against']:.4f}")

        train_df = compute_lambda(train_df, baselines)
        test_df = compute_lambda(test_df, baselines)
        print()
        print(f"train 段 λ_home 兜底(rolling 缺失)场次: {train_df['lambda_home_is_fallback'].sum()}")
        print(f"train 段 λ_away 兜底(rolling 缺失)场次: {train_df['lambda_away_is_fallback'].sum()}")
        print(f"test  段 λ_home 兜底(rolling 缺失)场次: {test_df['lambda_home_is_fallback'].sum()}")
        print(f"test  段 λ_away 兜底(rolling 缺失)场次: {test_df['lambda_away_is_fallback'].sum()}")

        # Step 2: Dixon-Coles rho(只用 train 段拟合)
        rho = fit_rho(train_df)
        print()
        print(f"=== Dixon-Coles rho(train 段 MLE 拟合,约束区间 {RHO_BOUNDS}) ===")
        print(f"rho = {rho:.6f}")

        # Step 3: 比分矩阵 -> 原始 WDL 概率
        train_df = compute_raw_probs(train_df, rho)
        test_df = compute_raw_probs(test_df, rho)

        # isotonic 校准器只用 train 段拟合
        calibrators = fit_isotonic_calibrators(train_df)
        test_df = apply_calibration(test_df, calibrators)
        train_df = apply_calibration(train_df, calibrators)  # 仅用于对照展示,不参与拟合

        # 固化本次拟合的参数(联赛基准 μ / rho / isotonic 校准器),供 C2
        # (对未来赛程出预测)加载复用,不重新拟合。
        save_artifacts(baselines, rho, calibrators)

        # Step 4: walk-forward 评估(全部在 test 段)
        test_actual = np.where(
            test_df["target_home_goals"] > test_df["target_away_goals"], 0,
            np.where(test_df["target_home_goals"] == test_df["target_away_goals"], 1, 2),
        )

        rps_raw = rps(test_df["p_home_raw"].to_numpy(), test_df["p_draw_raw"].to_numpy(),
                      test_df["p_away_raw"].to_numpy(), test_actual)
        rps_cal = rps(test_df["p_home"].to_numpy(), test_df["p_draw"].to_numpy(),
                      test_df["p_away"].to_numpy(), test_actual)
        brier_raw = brier(test_df["p_home_raw"].to_numpy(), test_df["p_draw_raw"].to_numpy(),
                          test_df["p_away_raw"].to_numpy(), test_actual)
        brier_cal = brier(test_df["p_home"].to_numpy(), test_df["p_draw"].to_numpy(),
                          test_df["p_away"].to_numpy(), test_actual)
        logloss_raw = log_loss(
            test_actual, test_df[["p_home_raw", "p_draw_raw", "p_away_raw"]].to_numpy(), labels=[0, 1, 2]
        )
        logloss_cal = log_loss(
            test_actual, test_df[["p_home", "p_draw", "p_away"]].to_numpy(), labels=[0, 1, 2]
        )

        # baseline: 永远输出 train 段的联赛平均 [主胜率, 平率, 客胜率]
        train_actual = np.where(
            train_df["target_home_goals"] > train_df["target_away_goals"], 0,
            np.where(train_df["target_home_goals"] == train_df["target_away_goals"], 1, 2),
        )
        base_home_rate = float(np.mean(train_actual == 0))
        base_draw_rate = float(np.mean(train_actual == 1))
        base_away_rate = float(np.mean(train_actual == 2))
        n_test = len(test_df)
        base_p_home = np.full(n_test, base_home_rate)
        base_p_draw = np.full(n_test, base_draw_rate)
        base_p_away = np.full(n_test, base_away_rate)
        rps_baseline = rps(base_p_home, base_p_draw, base_p_away, test_actual)
        brier_baseline = brier(base_p_home, base_p_draw, base_p_away, test_actual)
        logloss_baseline = log_loss(
            test_actual, np.stack([base_p_home, base_p_draw, base_p_away], axis=1), labels=[0, 1, 2]
        )

        print()
        print("=== test 段(2025/2026)基准(train 段主胜/平/客胜频率)===")
        print(f"baseline [主胜率, 平率, 客胜率] = [{base_home_rate:.4f}, {base_draw_rate:.4f}, {base_away_rate:.4f}]")

        print()
        print("=== test 段指标:模型(校准前/后) vs baseline ===")
        print(f"{'指标':<10}{'baseline':>12}{'模型(校准前)':>16}{'模型(校准后)':>16}")
        print(f"{'RPS':<10}{rps_baseline:>12.4f}{rps_raw:>16.4f}{rps_cal:>16.4f}")
        print(f"{'Brier':<10}{brier_baseline:>12.4f}{brier_raw:>16.4f}{brier_cal:>16.4f}")
        print(f"{'LogLoss':<10}{logloss_baseline:>12.4f}{logloss_raw:>16.4f}{logloss_cal:>16.4f}")

        print()
        print("=== 校准曲线(test 段,按预测主胜概率 p_home 分 10 档,校准后)===")
        cal_table = calibration_table(test_df["p_home"].to_numpy(), (test_actual == 0).astype(int))
        print(f"{'预测均值':>10}{'实际频率':>10}{'样本数':>8}")
        for _, row in cal_table.iterrows():
            print(f"{row['pred_mean']:>10.4f}{row['actual_freq']:>10.4f}{int(row['n']):>8}")

        print()
        print("=== 校准曲线(test 段,校准前 raw p_home,对照)===")
        cal_table_raw = calibration_table(test_df["p_home_raw"].to_numpy(), (test_actual == 0).astype(int))
        print(f"{'预测均值':>10}{'实际频率':>10}{'样本数':>8}")
        for _, row in cal_table_raw.iterrows():
            print(f"{row['pred_mean']:>10.4f}{row['actual_freq']:>10.4f}{int(row['n']):>8}")

        # Step 5: 落库(只存 test 段)+ 样例
        n_written = write_predictions(conn, test_df)
        print()
        print(f"gold_wdl_predictions 写入行数: {n_written}")

        print()
        print("=== 样例预测(test 段随机 5 场,校准后概率)===")
        sample = test_df.sample(n=5, random_state=42)
        for _, row in sample.iterrows():
            print(
                f"{row['home_team_name']:<20} vs {row['away_team_name']:<20} "
                f"λ_home={row['lambda_home']:.2f} λ_away={row['lambda_away']:.2f}  "
                f"P(H/D/A)=({row['p_home']:.3f}/{row['p_draw']:.3f}/{row['p_away']:.3f})  "
                f"实际比分 {int(row['target_home_goals'])}-{int(row['target_away_goals'])}"
            )
    finally:
        conn.close()
    print()
    print("=== build_wdl_baseline 完成(停下等待人工核对指标)===")


if __name__ == "__main__":
    main()
