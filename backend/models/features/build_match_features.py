"""
build_match_features.py — 从 Bronze(dim_match / fact_team_match_stats)算出
逐场赛前特征表 int_match_features(ROADMAP.md Phase 1.M.1)。

只建特征、不训练模型(1.M.2 才训练)。

口径(详见 schema.py 里 INT_MATCH_FEATURES_COLUMNS 前的注释):
    - 只用 dim_match.status = 'Finish' 的场次。
    - 每支球队按 (match_date, match_id) 正序排列后,对每场比赛的 rolling
      特征用 shift(1) 先把当场本身移出窗口,再取窗口大小 5/10 的均值——
      确保某场比赛的特征只包含"该场之前"的历史,不泄露当场结果。
    - rolling 允许跨赛季取最近 N 场,不按赛季边界截断(简化选择,非遗漏)。
    - *_n_matches_* 记录该 rolling 实际用了几场历史(不足 N 场时 < N),
      供下游判断这行特征的可信度。
    - xg_against(该队被对手打出的预期进球)取"同一场比赛里对方球队的
      expected_goals"，不是本队字段的变体。

幂等策略:
    每次全量重算、DELETE 全表再整体 INSERT——rolling 依赖每支球队的完整
    历史(且允许跨赛季),没法像 Silver 那样只按单个 (League_ID, Season)
    增量重建,所以退化为整表幂等重建,可反复重跑。

用法:
    python backend/models/features/build_match_features.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from db import get_connection
from db.util import utc_now_iso
from schema import FEATURE_ROLLING_STATS, FEATURE_WINDOWS, INT_MATCH_FEATURES_COLUMNS, _quote

DECAY_RATE = 0.0015


def _load_raw(conn, league_id: int) -> tuple:
    # League_ID 谓词是硬防线,不是可选项(2026-08-07 审计):此前无谓词版本
    # 依赖"只有 EPL 有 Period='All' stats"这个隐式前提兜底,J1/K1/澳超与
    # 五大联赛全量接入后该前提已失效——今天无谓词重跑会把 8 个联赛 12,785
    # 场混进同一张特征表,rolling 与联赛基准全部被污染,且悄悄改变
    # dc-baseline-1.M.2 已发布指标的输入。与 predict_wdl_future.py 的
    # League 谓词防线(见其 docstring)同一条纪律。
    matches = pd.read_sql_query(
        """
        SELECT Match_ID AS match_id, Date AS match_date, Season AS season,
               League_ID AS league_id, Home_Team_ID AS home_team_id,
               Away_Team_ID AS away_team_id, home_score, away_score
        FROM dim_match
        WHERE status = 'Finish' AND League_ID = ?
        """,
        conn,
        params=(league_id,),
    )

    team_stats = pd.read_sql_query(
        """
        SELECT fts.Match_ID AS match_id, fts.Team_ID AS team_id, fts.extra_json
        FROM fact_team_match_stats fts
        JOIN dim_match dm ON fts.Match_ID = dm.Match_ID
        WHERE fts.Period = 'All' AND dm.status = 'Finish' AND dm.League_ID = ?
        """,
        conn,
        params=(league_id,),
    )
    return matches, team_stats


def _extract_stats(team_stats: pd.DataFrame) -> pd.DataFrame:
    parsed = team_stats["extra_json"].apply(json.loads)
    team_stats = team_stats.copy()
    team_stats["expected_goals"] = parsed.apply(lambda d: d.get("expected_goals"))
    team_stats["total_shots"] = parsed.apply(lambda d: d.get("total_shots"))
    team_stats["ShotsOnTarget"] = parsed.apply(lambda d: d.get("ShotsOnTarget"))
    team_stats["BallPossesion"] = parsed.apply(lambda d: d.get("BallPossesion"))
    return team_stats.drop(columns=["extra_json"])


def _build_team_match_long(matches: pd.DataFrame, team_stats: pd.DataFrame) -> pd.DataFrame:
    """把 (match, team) 两条 fact_team_match_stats 行互相对齐,拼出对手的
    expected_goals(= 本队的 xg_against),再挂上 dim_match 的比分/日期/主客场
    上下文,产出一张"每队每场一行"的长表。"""
    merged = team_stats.merge(
        team_stats, on="match_id", suffixes=("", "_opp")
    )
    merged = merged[merged["team_id"] != merged["team_id_opp"]].drop_duplicates(
        subset=["match_id", "team_id"]
    )

    df = merged.merge(matches, on="match_id")
    df["is_home"] = df["team_id"] == df["home_team_id"]
    df["xg_for"] = df["expected_goals"]
    df["xg_against"] = df["expected_goals_opp"]
    df["goals_for"] = np.where(df["is_home"], df["home_score"], df["away_score"])
    df["goals_against"] = np.where(df["is_home"], df["away_score"], df["home_score"])
    df["shots_for"] = df["total_shots"]
    df["shots_on_target_for"] = df["ShotsOnTarget"]
    df["possession"] = df["BallPossesion"]

    cols = [
        "match_id", "team_id", "match_date", "season", "league_id", "is_home",
    ] + FEATURE_ROLLING_STATS
    return df[cols].sort_values(["team_id", "match_date", "match_id"]).reset_index(drop=True)


def _rolling_features(df: pd.DataFrame) -> pd.DataFrame:
    """对已按 (team_id, match_date, match_id) 排好序的长表,逐队算
    shift(1) + rolling(窗口 5/10, min_periods=1) 的均值,以及该 rolling
    实际用了几场(cumcount 截断到窗口大小,不用再单独 rolling().count())。"""
    df = df.copy()
    grouped = df.groupby("team_id", group_keys=False)
    prior_count = grouped.cumcount()

    for stat in FEATURE_ROLLING_STATS:
        shifted = grouped[stat].shift(1)
        for w in FEATURE_WINDOWS:
            df[f"{stat}_l{w}"] = (
                shifted.groupby(df["team_id"])
                .rolling(window=w, min_periods=1)
                .mean()
                .reset_index(level=0, drop=True)
            )
    for w in FEATURE_WINDOWS:
        df[f"n_matches_l{w}"] = prior_count.clip(upper=w)
    return df


def _side_frame(rolled: pd.DataFrame, venue_suffix: str = "") -> pd.DataFrame:
    keep = ["match_id", "team_id"]
    rename = {}
    for stat in FEATURE_ROLLING_STATS:
        for w in FEATURE_WINDOWS:
            src = f"{stat}_l{w}"
            keep.append(src)
            rename[src] = f"{stat}{venue_suffix}_l{w}"
    for w in FEATURE_WINDOWS:
        src = f"n_matches_l{w}"
        keep.append(src)
        rename[src] = f"n_matches{venue_suffix}_l{w}"
    return rolled[keep].rename(columns=rename)


def build_features_df(matches: pd.DataFrame, team_stats: pd.DataFrame) -> pd.DataFrame:
    team_stats = _extract_stats(team_stats)
    long_df = _build_team_match_long(matches, team_stats)

    overall_rolled = _rolling_features(long_df)
    home_venue_rolled = _rolling_features(long_df[long_df["is_home"]])
    away_venue_rolled = _rolling_features(long_df[~long_df["is_home"]])

    home_overall = _side_frame(overall_rolled[overall_rolled["is_home"]]).rename(
        columns=lambda c: f"home_{c}" if c not in ("match_id", "team_id") else c
    )
    away_overall = _side_frame(overall_rolled[~overall_rolled["is_home"]]).rename(
        columns=lambda c: f"away_{c}" if c not in ("match_id", "team_id") else c
    )
    home_venue = _side_frame(home_venue_rolled, venue_suffix="_home").rename(
        columns=lambda c: f"home_{c}" if c not in ("match_id", "team_id") else c
    )
    away_venue = _side_frame(away_venue_rolled, venue_suffix="_away").rename(
        columns=lambda c: f"away_{c}" if c not in ("match_id", "team_id") else c
    )

    home_features = home_overall.merge(home_venue, on=["match_id", "team_id"])
    away_features = away_overall.merge(away_venue, on=["match_id", "team_id"])

    out = matches.merge(
        home_features, left_on=["match_id", "home_team_id"], right_on=["match_id", "team_id"]
    ).drop(columns=["team_id"])
    out = out.merge(
        away_features, left_on=["match_id", "away_team_id"], right_on=["match_id", "team_id"]
    ).drop(columns=["team_id"])

    for stat in ("xg_for", "goals_for"):
        for w in FEATURE_WINDOWS:
            out[f"{stat}_diff_l{w}"] = out[f"home_{stat}_l{w}"] - out[f"away_{stat}_l{w}"]

    out["target_home_goals"] = out["home_score"]
    out["target_away_goals"] = out["away_score"]

    match_date = pd.to_datetime(out["match_date"])
    latest = match_date.max()
    days_from_latest = (latest - match_date).dt.days
    out["sample_weight"] = np.exp(-DECAY_RATE * days_from_latest)

    return out


def write_features(conn, df: pd.DataFrame, league_id: int) -> int:
    col_names = [name for name, _ in INT_MATCH_FEATURES_COLUMNS if name != "updated_at"]
    df = df[col_names].astype(object).where(pd.notnull(df[col_names]), None)

    # 只清重建目标联赛的行:无谓词 DELETE 会把其它联赛已构建的特征连带清空
    # (与 predict_wdl_future.py 已修复、test_predict_wdl_future.py 回归保护的
    # gold DELETE 缺陷同型)。
    conn.execute("DELETE FROM int_match_features WHERE league_id = ?", (league_id,))
    placeholders = ", ".join("?" for _ in col_names)
    cols_sql = ", ".join(_quote(n) for n in col_names)
    conn.executemany(
        f"INSERT INTO int_match_features ({cols_sql}, updated_at) "
        f"VALUES ({placeholders}, ?)",
        [tuple(row) + (utc_now_iso(),) for row in df.itertuples(index=False, name=None)],
    )
    conn.commit()
    return len(df)


def build_match_features(league_id: int = 47) -> None:
    """默认仍为 EPL(47)——dc-baseline-1.M.2 的训练输入范围;其它联赛需显式传参。"""
    conn = get_connection()
    try:
        matches, team_stats = _load_raw(conn, league_id)
        print(f"dim_match 完赛场次(League {league_id}): {len(matches)}")
        print(f"fact_team_match_stats(Period=All)行数: {len(team_stats)}")

        features_df = build_features_df(matches, team_stats)
        n = write_features(conn, features_df, league_id)
        print(f"int_match_features 写入行数: {n}")
    finally:
        conn.close()
    print("=== int_match_features 构建完成 ===")


if __name__ == "__main__":
    build_match_features()
