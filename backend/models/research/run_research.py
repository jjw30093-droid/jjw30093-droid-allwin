"""run_research.py — season-forward 多模型×市场配对研究(只读,确定性)。

预先指定协议(在看到任何 test 结果之前固定,本 docstring 即协议记录):

- Folds(season-forward,禁随机划分):
    F1 train=[2020/21]                test=2021/22
    F2 train=[2020/21..2021/22]       test=2022/23
    F3 train=[..2022/23]              test=2023/24
    F4 train=[..2023/24]              test=2024/25
    F5 train=[..2024/25]              test=2025/26
- 每 fold 内:train 按 kickoff 时序切最后 20% 作校准段(calibration split);
  imputer/scaler/模型只在前 80% 拟合,isotonic 只在校准段拟合,test 只评估。
- 模型:freq(分联赛频率)/ dc(rolling-xG Dixon-Coles,分联赛基准)/
  lr(多项逻辑回归,pooled+league one-hot)/ hgb(HistGradientBoosting,pooled)/
  lr_market(特征+市场 logit)/ market_*(纯市场基线,不称自有模型)。
- 市场配对:所有"模型 vs 市场"比较都在完全相同的 paired sample 上;
  paired ΔRPS 用配对 bootstrap(n=2000,seed=20260807)给 95% CI。
- 指标:Accuracy/RPS/Brier/LogLoss/校准表(等宽10桶)/n,分联赛+pooled。
- 消融(lr 与 hgb):form(goals)/xg/shots/venue/rest/history,逐 fold ΔRPS;
  "稳定"=方向跨 fold 一致。**协议偏离(2026-08-07 对抗复核发现并如实记录)**:
  本节最初预登记的第 6 组是 league(联赛 one-hot),实现改成了 history
  (n_matches/n_prior_season)且从未消融过 league(add_league_onehot 在全部
  lr 变体中恒定存在)——league 消融未执行,不是"结果不利被隐藏",是协议
  文档与实现出现漂移,此处如实登记、不补跑(避免扩大本轮范围)。
  另:24 个 l5 口径的 shots/possession/n_matches 特征从未进入任何消融组
  (只消融了 l10 口径),"shots 组稳定增益"结论只覆盖已入组的 l10 特征。

2026-08-08 J1/K1/澳超接入(窄幅扩展,与上面五大联赛协议并存、互不污染):

- 本模块所有依赖 `LEAGUES` 模块常量的函数(`add_league_onehot` /
  `fit_predict_freq` / `fit_predict_dc` / `fit_predict_sklearn` /
  `run_full_study` 的 per_league 分解)现在都接受显式 `leagues=` 参数,
  默认值仍是原 `LEAGUES = (47,53,54,55,87)`,不传参数时行为逐位不变。
  **fail-closed**:数据行的 `league_id` 不在传入的 `leagues` 集合中时报错,
  不再静默按参照类(原 `LEAGUES[0]`=47)编码或按均匀分布回退 ——
  这是被对抗复核实测确认的真 P0(J/K/A 曾被静默算成英超)。
- `SEASONS` 赛季字符串折叠只对五大联赛(跨年赛季格式统一)有效;
  J1 的已完赛赛季是裸年份 `"2024"/"2025"/"2026"`,与 K1 相同,
  完全不在 `SEASONS` 里,若沿用字符串折叠会静默丢弃这些比赛(真 P0,已实测
  约 1,540 场消失)。新增 `build_folds_by_time()` 按 `kickoff_at_utc`
  绝对时间做 rolling-origin 折叠,供八联赛研究使用;五大联赛的 F1-F5
  仍用原 `build_folds()`(赛季字符串),两套折叠互不替代、分开报告。
- `_market_tables()` 的 summary 来源优先级原来只含三个五大联赛专用来源,
  J/K/A 的真实来源(`football_uk_jka`/`nowgoal_archive_refetch`)完全不在
  里面,导致这三个联赛的市场配对样本静默归零(真 P0,已实测)。改为参数化
  来源列表;调用方必须在配对样本为 0 但预期该联赛应有市场数据时 fail-closed
  报错,不得静默跳过。
- `time_split()` 的全局时间切分在联赛赛历错位时会让各联赛的训练/校准配额
  严重失衡(实测一个典型池化折:澳超 51% 的训练数据落进校准段,英超只有
  18.9%)。新增 `time_split_stratified()` 按联赛分层切分。
"""

from __future__ import annotations

import json
from collections import defaultdict

import numpy as np

SEASONS = ["2020/2021", "2021/2022", "2022/2023", "2023/2024", "2024/2025", "2025/2026"]
LEAGUES = (47, 53, 54, 55, 87)
ALL_EIGHT_LEAGUES = (47, 53, 54, 55, 87, 223, 9080, 113)
SEED = 20260807
BOOT_N = 2000


class UnknownLeagueError(ValueError):
    """数据中出现了不在配置的 leagues 集合中的 league_id——fail-closed,
    绝不静默落到 one-hot 参照类或均匀分布(2026-08-08 对抗复核确认的真 P0)。"""

FEATURE_GROUPS = {
    "xg": ["home_xg_for_l5", "home_xg_for_l10", "home_xg_against_l5", "home_xg_against_l10",
            "away_xg_for_l5", "away_xg_for_l10", "away_xg_against_l5", "away_xg_against_l10"],
    "form": ["home_goals_for_l5", "home_goals_for_l10", "home_goals_against_l5",
              "home_goals_against_l10", "away_goals_for_l5", "away_goals_for_l10",
              "away_goals_against_l5", "away_goals_against_l10"],
    "shots": ["home_shots_for_l10", "home_shots_on_target_for_l10", "home_possession_l10",
               "away_shots_for_l10", "away_shots_on_target_for_l10", "away_possession_l10"],
    "venue": ["home_xg_for_home_l5", "home_xg_for_home_l10",
               "away_xg_for_away_l5", "away_xg_for_away_l10"],
    "rest": ["home_rest_days", "away_rest_days"],
    "history": ["home_n_matches_l10", "away_n_matches_l10",
                 "home_n_prior_season", "away_n_prior_season"],
}
ALL_FEATURES = [f for group in FEATURE_GROUPS.values() for f in group]


# ── 指标(与 backend/eval 同口径) ─────────────────────────


def rps(p: np.ndarray, y: np.ndarray) -> float:
    cp1, cp2 = p[:, 0], p[:, 0] + p[:, 1]
    ca1, ca2 = (y == 0).astype(float), (y != 2).astype(float)
    return float(np.mean(0.5 * ((cp1 - ca1) ** 2 + (cp2 - ca2) ** 2)))


def rps_per_match(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    cp1, cp2 = p[:, 0], p[:, 0] + p[:, 1]
    ca1, ca2 = (y == 0).astype(float), (y != 2).astype(float)
    return 0.5 * ((cp1 - ca1) ** 2 + (cp2 - ca2) ** 2)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    onehot = np.eye(3)[y]
    return float(np.mean(np.sum((p - onehot) ** 2, axis=1)))


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    pt = np.clip(p[np.arange(len(y)), y], 1e-15, 1)
    return float(np.mean(-np.log(pt)))


def accuracy(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.argmax(p, axis=1) == y))


def calibration_buckets(p_home: np.ndarray, y_home: np.ndarray, n_bins: int = 10) -> list:
    out = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        mask = (p_home >= lo) & (p_home < hi) if b < n_bins - 1 else (p_home >= lo) & (p_home <= hi)
        if mask.sum() == 0:
            continue
        out.append({"bin": f"[{lo:.1f},{hi:.1f})", "avg_pred": round(float(p_home[mask].mean()), 4),
                    "freq": round(float(y_home[mask].mean()), 4), "n": int(mask.sum())})
    return out


def metric_block(p: np.ndarray, y: np.ndarray) -> dict:
    return {
        "n": int(len(y)),
        "accuracy": round(accuracy(p, y), 4),
        "rps": round(rps(p, y), 4),
        "brier": round(brier(p, y), 4),
        "log_loss": round(logloss(p, y), 4),
    }


# ── 数据准备 ─────────────────────────────────────────────


def rows_to_matrix(rows: list[dict], features: list[str]):
    X = np.array([[r["features"].get(f) if r["features"].get(f) is not None else np.nan
                   for f in features] for r in rows], dtype=float)
    y = np.array([0 if r["target_home_goals"] > r["target_away_goals"]
                  else 1 if r["target_home_goals"] == r["target_away_goals"] else 2
                  for r in rows])
    leagues = np.array([r["league_id"] for r in rows])
    return X, y, leagues


def add_league_onehot(X: np.ndarray, leagues: np.ndarray, all_leagues=LEAGUES) -> np.ndarray:
    """联赛 one-hot(参照格 = all_leagues[0],其余各一列)。

    fail-closed:`leagues` 数组里出现不在 `all_leagues` 配置集合中的 league_id
    时报错——2026-08-08 对抗复核实测确认,旧实现会把这类行的 one-hot 全部
    编码成 [0,0,...,0],与参照格(原来恒为 47/英超)完全无法区分,是真 P0。
    """
    unknown = set(np.unique(leagues)) - set(all_leagues)
    if unknown:
        raise UnknownLeagueError(
            f"league_id {sorted(unknown)} 不在配置的 leagues={all_leagues} 中,"
            "拒绝静默编码为参照类"
        )
    cols = [(leagues == lid).astype(float).reshape(-1, 1) for lid in all_leagues[1:]]
    return np.hstack([X] + cols)


def time_split(rows: list[dict], frac: float = 0.8):
    rows_sorted = sorted(rows, key=lambda r: (r["kickoff"], r["match_id"]))
    k = int(len(rows_sorted) * frac)
    return rows_sorted[:k], rows_sorted[k:]


def time_split_stratified(rows: list[dict], frac: float = 0.8):
    """按联赛分层的时间切分——P0-7(2026-08-08 对抗复核实测确认的真 P0):
    全局时间切分在联赛赛历错位时会让各联赛的训练/校准配额严重失衡
    (实测一个典型池化折:澳超 51% 的训练数据落进校准段,英超只有 18.9%,
    "池化在澳超上更好"可能完全是这个切分规则的副产品,和建模无关)。

    每个联赛内部各自按时间切最后 frac 比例做校准段,再合并。
    """
    by_league: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_league[r["league_id"]].append(r)
    train, calib = [], []
    for lid, lrows in by_league.items():
        lsorted = sorted(lrows, key=lambda r: (r["kickoff"], r["match_id"]))
        k = int(len(lsorted) * frac)
        train.extend(lsorted[:k])
        calib.extend(lsorted[k:])
    train.sort(key=lambda r: (r["kickoff"], r["match_id"]))
    calib.sort(key=lambda r: (r["kickoff"], r["match_id"]))
    return train, calib


def per_league_split_counts(train: list[dict], calib: list[dict], test: list[dict]) -> dict:
    """每折每联赛的 fit_n/calib_n/test_n——P0-7 要求配额失衡必须可见,写进产物。"""
    out: dict[str, dict[str, int]] = {}
    for label, rows in (("train", train), ("calib", calib), ("test", test)):
        for r in rows:
            key = str(r["league_id"])
            out.setdefault(key, {"train": 0, "calib": 0, "test": 0})
            out[key][label] += 1
    return out


# ── 模型 ─────────────────────────────────────────────────


def fit_predict_freq(train_rows, test_rows, leagues=LEAGUES):
    """分联赛频率基线。fail-closed:test 行的 league_id 不在配置的 `leagues`
    集合中直接报错(不得静默落到参照类)。该联赛在配置内但训练集里恰好还
    没有任何比赛(合法情形,例如某联赛历史最早的几折)时,才允许回退到
    均匀分布 [1/3,1/3,1/3] 这个显式的"无信息先验"。"""
    rates = {}
    for lid in leagues:
        ys = [0 if r["target_home_goals"] > r["target_away_goals"]
              else 1 if r["target_home_goals"] == r["target_away_goals"] else 2
              for r in train_rows if r["league_id"] == lid]
        if ys:
            arr = np.array(ys)
            rates[lid] = np.array([np.mean(arr == k) for k in (0, 1, 2)])
    overall = np.array([1 / 3, 1 / 3, 1 / 3])
    out = []
    for r in test_rows:
        if r["league_id"] not in leagues:
            raise UnknownLeagueError(
                f"league_id {r['league_id']} 不在配置的 leagues={leagues} 中"
            )
        out.append(rates.get(r["league_id"], overall))
    return np.array(out)


def _dc_prob_matrix(lam, mu, rho, n=10):
    from scipy.stats import poisson

    hp, ap = poisson.pmf(np.arange(n + 1), lam), poisson.pmf(np.arange(n + 1), mu)
    m = np.outer(hp, ap)
    m[0, 0] *= 1 - lam * mu * rho
    m[0, 1] *= 1 + lam * rho
    m[1, 0] *= 1 + mu * rho
    m[1, 1] *= 1 - rho
    m /= m.sum()
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def fit_predict_dc(train_rows, test_rows, calib_rows, leagues=LEAGUES):
    """分联赛基准的 rolling-xG DC + one-vs-rest isotonic(校准段拟合)。
    fail-closed:test 行的 league_id 不在配置的 `leagues` 集合中直接报错。"""
    from scipy.optimize import minimize_scalar
    from sklearn.isotonic import IsotonicRegression

    unknown_test = {r["league_id"] for r in test_rows} - set(leagues)
    if unknown_test:
        raise UnknownLeagueError(
            f"league_id {sorted(unknown_test)} 不在配置的 leagues={leagues} 中"
        )

    base = {}
    for lid in leagues:
        tr = [r for r in train_rows if r["league_id"] == lid]
        if not tr:
            continue
        hg = np.array([r["target_home_goals"] for r in tr], dtype=float)
        ag = np.array([r["target_away_goals"] for r in tr], dtype=float)
        xs = [v for r in tr for v in (r["features"].get("home_xg_for_l10"),
                                       r["features"].get("away_xg_for_l10")) if v is not None]
        xa = [v for r in tr for v in (r["features"].get("home_xg_against_l10"),
                                       r["features"].get("away_xg_against_l10")) if v is not None]
        base[lid] = {"mu_h": hg.mean(), "mu_a": ag.mean(),
                     "avg_for": np.mean(xs) if xs else 1.3,
                     "avg_against": np.mean(xa) if xa else 1.3}

    def lam_for(r):
        b = base.get(r["league_id"])
        f = r["features"]
        if b is None:
            return 1.4, 1.2
        lh = (b["mu_h"] * (f["home_xg_for_l10"] / b["avg_for"]) * (f["away_xg_against_l10"] / b["avg_against"])
              if f.get("home_xg_for_l10") is not None and f.get("away_xg_against_l10") is not None else b["mu_h"])
        la = (b["mu_a"] * (f["away_xg_for_l10"] / b["avg_for"]) * (f["home_xg_against_l10"] / b["avg_against"])
              if f.get("away_xg_for_l10") is not None and f.get("home_xg_against_l10") is not None else b["mu_a"])
        return lh, la

    lam_tr = np.array([lam_for(r) for r in train_rows])
    hg = np.array([r["target_home_goals"] for r in train_rows])
    ag = np.array([r["target_away_goals"] for r in train_rows])
    mask = (hg <= 1) & (ag <= 1)

    def nll(rho):
        t = np.ones(mask.sum())
        x, yv = hg[mask], ag[mask]
        l, m_ = lam_tr[mask, 0], lam_tr[mask, 1]
        t = np.where((x == 0) & (yv == 0), 1 - l * m_ * rho, t)
        t = np.where((x == 0) & (yv == 1), 1 + l * rho, t)
        t = np.where((x == 1) & (yv == 0), 1 + m_ * rho, t)
        t = np.where((x == 1) & (yv == 1), 1 - rho, t)
        if np.any(t <= 0):
            return 1e10
        return -np.sum(np.log(t))

    rho = float(minimize_scalar(nll, bounds=(-0.2, 0.1), method="bounded").x)

    def raw_probs(rows):
        return np.array([_dc_prob_matrix(*lam_for(r), rho) for r in rows])

    p_cal_raw = raw_probs(calib_rows)
    y_cal = np.array([0 if r["target_home_goals"] > r["target_away_goals"]
                      else 1 if r["target_home_goals"] == r["target_away_goals"] else 2
                      for r in calib_rows])
    isos = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(p_cal_raw[:, k], (y_cal == k).astype(int))
        isos.append(iso)

    p_test_raw = raw_probs(test_rows)
    p = np.stack([isos[k].predict(p_test_raw[:, k]) for k in range(3)], axis=1)
    p = np.clip(p, 1e-6, None)
    return p / p.sum(axis=1, keepdims=True), rho


def fit_predict_sklearn(train_rows, calib_rows, test_rows, kind: str,
                        features: list[str], market: dict | None = None,
                        leagues=LEAGUES, calibration: str = "isotonic"):
    """lr / hgb(+可选市场 logit 特征);市场特征缺失的行 fail-closed 由调用方
    先做 paired 过滤,这里 assert 保护。

    `leagues`:传给 `add_league_onehot` 的联赛集合(fail-closed,见其文档)。
    `calibration`:"isotonic"(默认,五大联赛冻结路径不变)或 "platt"
    (2026-08-08 新增,per-class sigmoid,参数量远小于 isotonic,小样本
    联赛校准集上更稳——见 run_multileague_research 的 B3 校准对照臂)。
    """
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.preprocessing import StandardScaler

    def matrix(rows):
        X, y, lg = rows_to_matrix(rows, features)
        X = add_league_onehot(X, lg, all_leagues=leagues)
        if market is not None:
            mk = np.array([market[r["match_id"]] for r in rows])
            logit = np.log(np.clip(mk, 1e-6, 1 - 1e-6))
            X = np.hstack([X, logit])
        return X, y

    Xtr, ytr = matrix(train_rows)
    Xc, yc = matrix(calib_rows)
    Xte, yte = matrix(test_rows)

    def _full3(proba: np.ndarray, classes: np.ndarray) -> np.ndarray:
        # 训练段偶然缺类时 predict_proba 列数 < 3;按 classes_ 映射回固定 3 列
        out = np.zeros((len(proba), 3))
        for j, cls in enumerate(classes):
            out[:, int(cls)] = proba[:, j]
        return out

    if kind == "lr":
        imp = SimpleImputer(strategy="median").fit(Xtr)
        sc = StandardScaler().fit(imp.transform(Xtr))
        model = LogisticRegression(max_iter=2000, C=1.0, random_state=SEED)
        model.fit(sc.transform(imp.transform(Xtr)), ytr)
        def predict(X):
            return _full3(model.predict_proba(sc.transform(imp.transform(X))), model.classes_)
    else:
        model = HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.06, max_depth=4, random_state=SEED,
            early_stopping=False)
        model.fit(Xtr, ytr)
        def predict(X):
            return _full3(model.predict_proba(X), model.classes_)

    p_cal = predict(Xc)
    p_test_raw = predict(Xte)
    if calibration == "temperature_per_league":
        cal_leagues = np.array([r["league_id"] for r in calib_rows])
        test_leagues = np.array([r["league_id"] for r in test_rows])
        p, _ = apply_per_league_temperature(p_cal, yc, cal_leagues, p_test_raw, test_leagues)
    else:
        p = _apply_ovr_calibration(p_cal, yc, p_test_raw, method=calibration)
    return p, model


def _fit_temperature(p_raw: np.ndarray, y: np.ndarray, t_bounds=(0.2, 5.0)) -> float:
    """拟合单一温度标量 T,使 softmax(log(p_raw)/T) 对 y 的负对数似然最小。
    T>1 让分布更平滑(降低置信度),T<1 让分布更尖锐。只有 1 个自由参数,
    小样本(几十场)也能稳定估计——这正是它被选为 per-league 校准方案的原因
    (对照:isotonic 是非参数、自由度随样本增长,在 100-200 场校准集上会过拟合)。
    """
    from scipy.optimize import minimize_scalar

    logp = np.log(np.clip(p_raw, 1e-9, 1.0))

    def nll(T):
        scaled = logp / T
        scaled = scaled - scaled.max(axis=1, keepdims=True)
        ex = np.exp(scaled)
        probs = ex / ex.sum(axis=1, keepdims=True)
        pt = np.clip(probs[np.arange(len(y)), y], 1e-12, 1.0)
        return float(-np.sum(np.log(pt)))

    res = minimize_scalar(nll, bounds=t_bounds, method="bounded")
    return float(res.x)


def _apply_temperature(p_raw: np.ndarray, T: float) -> np.ndarray:
    logp = np.log(np.clip(p_raw, 1e-9, 1.0))
    scaled = logp / T
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    ex = np.exp(scaled)
    return ex / ex.sum(axis=1, keepdims=True)


def apply_per_league_temperature(p_cal_raw: np.ndarray, y_cal: np.ndarray, cal_leagues: np.ndarray,
                                 p_target_raw: np.ndarray, target_leagues: np.ndarray,
                                 min_league_n: int = 20, t_bounds=(0.2, 5.0)) -> tuple[np.ndarray, dict]:
    """每联赛一个标量温度 T_lid(2026-08-08 新增,task #20)——最终综合复核
    确认的推荐架构:J1/K1/澳超"近期表现→结果"的标准化斜率只有五大联赛的
    约 40-61%,本质是尺度问题,每联赛 1 个温度参数就足够吸收,不需要
    league×feature 交互块(那需要 J1 804/K1 500/澳超 438 行去估约 768 个
    系数,代价与诊断出的问题完全不成比例)。

    某联赛校准段样本数 < `min_league_n` 时退回用全部联赛合并拟合的温度
    (池化 fallback),避免用几场比赛的噪声拟合出一个不稳定的 per-league T。
    返回 (校准后概率, {league_id: T})。
    """
    global_T = _fit_temperature(p_cal_raw, y_cal, t_bounds)
    cal_leagues = np.asarray(cal_leagues)
    T_by_league: dict = {}
    for lid in np.unique(cal_leagues):
        mask = cal_leagues == lid
        if mask.sum() >= min_league_n and len(np.unique(y_cal[mask])) >= 2:
            T_by_league[lid] = _fit_temperature(p_cal_raw[mask], y_cal[mask], t_bounds)
        else:
            T_by_league[lid] = global_T

    target_leagues = np.asarray(target_leagues)
    out = np.zeros_like(p_target_raw)
    for lid in np.unique(target_leagues):
        T = T_by_league.get(lid, global_T)
        mask = target_leagues == lid
        out[mask] = _apply_temperature(p_target_raw[mask], T)
    return out, {str(k): round(v, 4) for k, v in T_by_league.items()}


def _apply_ovr_calibration(p_cal_raw: np.ndarray, y_cal: np.ndarray,
                           p_target_raw: np.ndarray, method: str = "isotonic") -> np.ndarray:
    """One-vs-rest 三类校准,拟合于校准段、应用于目标段,归一化后返回。

    `method="isotonic"`:非参数保序回归(五大联赛冻结路径的默认值,不变)。
    `method="platt"`:per-class sigmoid(`sklearn.linear_model.LogisticRegression`
    对单变量 raw 概率的 logit 做一维逻辑回归),每类只有 2 个参数——
    2026-08-08 新增,给小样本联赛校准集用(见 B3 对照臂与
    docs/audits 里"isotonic 在 100-200 场校准集上过拟合"的诊断)。
    (`method="temperature_per_league"` 不走这个函数,见
    `apply_per_league_temperature`——它需要联赛标签,接口形状不同。)
    """
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    if method not in ("isotonic", "platt"):
        raise ValueError(f"unknown calibration method: {method!r}")

    calibrated = np.zeros_like(p_target_raw)
    for k in range(3):
        yk = (y_cal == k).astype(int)
        if method == "isotonic":
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(p_cal_raw[:, k], yk)
            calibrated[:, k] = iso.predict(p_target_raw[:, k])
        else:
            x = np.clip(p_cal_raw[:, k], 1e-6, 1 - 1e-6).reshape(-1, 1)
            logit = np.log(x / (1 - x))
            platt = LogisticRegression(max_iter=1000)
            if len(np.unique(yk)) < 2:
                # 校准段该类别只有一种标签(极小样本联赛可能发生):
                # 无法拟合 sigmoid,退化为常数预测(该类频率)
                calibrated[:, k] = float(yk.mean()) if len(yk) else 0.5
                continue
            platt.fit(logit, yk)
            xt = np.clip(p_target_raw[:, k], 1e-6, 1 - 1e-6).reshape(-1, 1)
            logit_t = np.log(xt / (1 - xt))
            calibrated[:, k] = platt.predict_proba(logit_t)[:, 1]

    calibrated = np.clip(calibrated, 1e-6, None)
    return calibrated / calibrated.sum(axis=1, keepdims=True)


def paired_bootstrap_dRPS(pa: np.ndarray, pb: np.ndarray, y: np.ndarray,
                          n_boot: int = BOOT_N, seed: int = SEED) -> dict:
    """ΔRPS = RPS(a) − RPS(b);负值 = a 优。配对 bootstrap 95% CI。"""
    da = rps_per_match(pa, y)
    db = rps_per_match(pb, y)
    diff = da - db
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(diff), size=(n_boot, len(diff)))
    boots = diff[idx].mean(axis=1)
    return {
        "mean_dRPS": round(float(diff.mean()), 5),
        "ci95": [round(float(np.quantile(boots, 0.025)), 5),
                  round(float(np.quantile(boots, 0.975)), 5)],
        "n": int(len(diff)),
    }


# ── 端到端编排(2026-08-07 从 runtime 脚本收口进永久模块) ──────────────


def y_of(rs: list[dict]) -> np.ndarray:
    return np.array([0 if r["target_home_goals"] > r["target_away_goals"]
                     else 1 if r["target_home_goals"] == r["target_away_goals"] else 2
                     for r in rs])


def build_folds(seasons=SEASONS) -> list[dict]:
    return [{"fold": f"F{i}", "train_seasons": seasons[:i], "test_season": seasons[i]}
            for i in range(1, len(seasons))]


# 五大联赛专用的 summary 来源优先级(冻结,原有行为不变)
DEFAULT_SUMMARY_SOURCES = ("asset_a_json", "asset_b_footballdata", "asset_b_nowgoal")
# J/K/A 的真实 summary 来源(2026-08-08 实测:football_uk_jka 覆盖 1,778 场,
# nowgoal_archive_refetch 覆盖 239 场,与五大联赛专用来源完全不重叠)
JKA_SUMMARY_SOURCES = ("football_uk_jka", "nowgoal_archive_refetch")
ALL_SUMMARY_SOURCES = DEFAULT_SUMMARY_SOURCES + JKA_SUMMARY_SOURCES


def build_folds_by_time(rows: list[dict], origins: list[str], test_days: int = 183) -> list[dict]:
    """按 kickoff_at_utc 绝对时间的 rolling-origin 折叠(2026-08-08 新增,
    P0-2/P0-6:替代按 Season 字符串折叠——J1 的已完赛赛季是裸年份
    "2024"/"2025"/"2026",完全不在 SEASONS 里,按字符串折叠会静默丢弃这些
    比赛;更严重的是四套赛历下"同一赛季标签"根本不是同一时间区间,按赛季
    字符串折叠会让训练集实际覆盖测试期(真实时间泄漏,不是丢样本那么简单)。

    `origins`:ISO 日期字符串列表(升序),每个 origin 定义一折:
    train = kickoff < origin,test = origin <= kickoff < origin + test_days 天。
    fail-closed:每折都断言 max(train.kickoff) < min(test.kickoff)。
    """
    from datetime import datetime, timedelta, timezone

    def _parse(s: str):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    folds = []
    for i, origin in enumerate(origins, start=1):
        origin_dt = _parse(origin if "T" in origin else origin + "T00:00:00Z")
        end_dt = origin_dt + timedelta(days=test_days)
        train = [r for r in rows if _parse(r["kickoff"]) < origin_dt]
        test = sorted(
            [r for r in rows if origin_dt <= _parse(r["kickoff"]) < end_dt],
            key=lambda r: (r["kickoff"], r["match_id"]),
        )
        if train and test:
            max_train_kickoff = max(r["kickoff"] for r in train)
            min_test_kickoff = min(r["kickoff"] for r in test)
            if not (_parse(max_train_kickoff) < _parse(min_test_kickoff)):
                raise ValueError(
                    f"fold F{i}(origin={origin})出现时间泄漏:"
                    f"max(train.kickoff)={max_train_kickoff} 不早于 "
                    f"min(test.kickoff)={min_test_kickoff}"
                )
        folds.append({"fold": f"T{i}", "origin": origin, "train_rows": train, "test_rows": test})
    return folds


def _market_tables(rows: list[dict], full: dict, summary_sources: dict,
                   source_priority: tuple[str, ...] = DEFAULT_SUMMARY_SOURCES) -> tuple[dict, dict]:
    """从 market_baseline 的 per_company/per_source 结构中取 (match_id -> (ph,pd,pa))。
    summary 合并规则(预先指定,不平均):按 `source_priority` 顺序,先到先得,
    其余来源只补缺。

    `source_priority` 默认只含五大联赛专用的三个来源(冻结,原行为不变)。
    调用方处理 J/K/A 数据时必须显式传入包含 JKA_SUMMARY_SOURCES 的优先级
    列表——2026-08-08 对抗复核实测确认:旧的硬编码优先级元组完全不含
    J/K/A 的真实来源,会让这三个联赛的市场配对样本静默归零而不报错(真 P0)。
    """
    mk_full = {c: {int(k): tuple(v["p"]) for k, v in d.items()} for c, d in full.items()}
    mk_sum: dict[int, tuple] = {}
    for src in source_priority:
        for mid, v in summary_sources.get(src, {}).items():
            mk_sum.setdefault(int(mid), tuple(v["p"]))
    return mk_full, mk_sum


def assert_market_coverage(rows: list[dict], mk_sum_combined: dict, leagues,
                           min_paired: int = 1) -> dict:
    """P0-4 fail-closed 检查:调用方在跑真正的研究之前,对每个配置的联赛
    确认市场配对样本数 >= min_paired,不满足则报错退出(不得静默产出
    "样本不足,不出指标"这种看似正常、实为配置错误的结果)。

    返回每联赛的配对计数,供调用方写进产物 manifest 存档。这个函数不对
    "该联赛真的没有市场数据"(如未来赛程、尚未采集)和"配置写错了"两种
    情况做区分——调用方必须先用 data_coverage 的真实覆盖率确认该联赛
    应该有市场数据,再调用本函数;对已知没有市场数据的联赛不要传进来。
    """
    counts: dict[str, int] = {}
    for lid in leagues:
        n = sum(1 for r in rows if r["league_id"] == lid and r["match_id"] in mk_sum_combined)
        counts[str(lid)] = n
        if n < min_paired:
            raise ValueError(
                f"league_id={lid} 的 summary_latest 市场配对样本数={n} < {min_paired}——"
                "fail-closed:可能是 source_priority 配置遗漏了该联赛的真实来源"
                "(参见 _market_tables 文档),而不是该联赛真的没有市场数据。"
            )
    return counts


def run_full_study(rows: list[dict], market_full: dict, market_summary_sources: dict,
                   seasons=SEASONS, folds: list[dict] | None = None, leagues=LEAGUES,
                   source_priority: tuple[str, ...] = DEFAULT_SUMMARY_SOURCES,
                   calibration: str = "isotonic",
                   use_stratified_calib: bool = False) -> dict:
    """整套折研究(模型训练+市场配对+消融),纯函数、确定性、只依赖入参。

    返回 {"fold_metrics": [...], "paired_market_metrics": [...],
          "feature_ablation": {...}}。不做任何 I/O。

    默认参数(`folds=None`,内部用 `seasons` 走 `build_folds()` 按赛季字符串
    折叠)与 2026-08-07 之前的行为逐位不变——这是五大联赛冻结对照必须保持
    可复现的路径。

    `folds`(2026-08-08 新增):显式传入按 `build_folds_by_time()` 构建的
    折叠列表(`[{"fold": tag, "train_rows": [...], "test_rows": [...]}]`),
    用于 J/K/A 八联赛研究(按 kickoff_at_utc 绝对时间折叠,不依赖 Season
    字符串)。传入时忽略 `seasons` 参数。
    `leagues`/`source_priority`/`calibration`/`use_stratified_calib`:
    见对应子函数文档,均为 fail-closed 或向后兼容默认值。
    """
    mk_full, mk_sum_combined = _market_tables(rows, market_full, market_summary_sources,
                                              source_priority=source_priority)
    if folds is None:
        folds = build_folds(seasons)
        season_mode = True
    else:
        season_mode = False

    fold_metrics: list[dict] = []
    paired_out: list[dict] = []
    preds_store: dict[tuple[str, str], dict[int, np.ndarray]] = {}

    for spec in folds:
        ftag = spec["fold"]
        if season_mode:
            train_all = [r for r in rows if r["season"] in spec["train_seasons"]]
            test = sorted([r for r in rows if r["season"] == spec["test_season"]],
                          key=lambda r: (r["kickoff"], r["match_id"]))
        else:
            train_all = spec["train_rows"]
            test = spec["test_rows"]
        splitter = time_split_stratified if use_stratified_calib else time_split
        train, calib = splitter(train_all, 0.8)
        y_test = y_of(test)

        entry = {"fold": ftag, "train_n": len(train), "calib_n": len(calib),
                 "test_n": len(test), "models": {},
                 "per_league_split_counts": per_league_split_counts(train, calib, test)}
        if season_mode:
            entry["test_season"] = spec["test_season"]
        else:
            entry["origin"] = spec["origin"]

        p = fit_predict_freq(train_all, test, leagues=leagues)
        entry["models"]["freq"] = metric_block(p, y_test)
        preds_store[(ftag, "freq")] = {r["match_id"]: p[i] for i, r in enumerate(test)}

        p, rho = fit_predict_dc(train, test, calib, leagues=leagues)
        entry["models"]["dc"] = metric_block(p, y_test) | {"rho": round(rho, 6)}
        preds_store[(ftag, "dc")] = {r["match_id"]: p[i] for i, r in enumerate(test)}

        for kind in ("lr", "hgb"):
            p, _ = fit_predict_sklearn(train, calib, test, kind, ALL_FEATURES,
                                       leagues=leagues, calibration=calibration)
            entry["models"][kind] = metric_block(p, y_test)
            preds_store[(ftag, kind)] = {r["match_id"]: p[i] for i, r in enumerate(test)}

        for label, table in [("market_pinnacle", mk_full.get("177", {})),
                              ("market_bet365", mk_full.get("281", {})),
                              ("market_macauslot", mk_full.get("80", {})),
                              ("market_summary_latest", mk_sum_combined)]:
            sub = [(i, r) for i, r in enumerate(test) if r["match_id"] in table]
            if len(sub) < 30:
                entry["models"][label] = {"n": len(sub), "note": "样本不足,不出指标"}
                continue
            pm = np.array([table[r["match_id"]] for _, r in sub])
            ym = y_test[[i for i, _ in sub]]
            entry["models"][label] = metric_block(pm, ym)

        # summary_latest 配对:dc/hgb/lr 用已训好的全量模型(preds_store),
        # 只需 paired>=100,不依赖训练集市场覆盖——它们与 lr_market/
        # lr_features_only(需要在 tr_p/ca_p 上重新训练,故需覆盖门槛)是
        # 两类不同的计算,不得共用同一道 gate(2026-08-07 对抗复核发现:
        # 旧实现把两者嵌在同一个 if 里,F1 因 tr_p=0 把 dc/hgb 配对也一并
        # 跳过,尽管这两个比较本可算出;修复后 F1 单独出现 summary_latest
        # 条目,数值对模型更不利,证明此前是遗漏而非美化)。
        paired = [r for r in test if r["match_id"] in mk_sum_combined]
        if len(paired) >= 100:
            yp = y_of(paired)
            p_mo = np.array([mk_sum_combined[r["match_id"]] for r in paired])
            entry["models"]["market_only_paired"] = metric_block(p_mo, yp)
            for m in ("dc", "lr", "hgb"):
                pm = np.array([preds_store[(ftag, m)][r["match_id"]] for r in paired])
                paired_out.append({
                    "fold": ftag, "vs": "summary_latest", "n": len(paired),
                    f"{m}_vs_market": paired_bootstrap_dRPS(pm, p_mo, yp),
                })

            tr_p = [r for r in train if r["match_id"] in mk_sum_combined]
            ca_p = [r for r in calib if r["match_id"] in mk_sum_combined]
            if len(tr_p) >= 200 and len(ca_p) >= 50:
                p_fm, _ = fit_predict_sklearn(tr_p, ca_p, paired, "lr", ALL_FEATURES,
                                              market=mk_sum_combined,
                                              leagues=leagues, calibration=calibration)
                p_fo, _ = fit_predict_sklearn(tr_p, ca_p, paired, "lr", ALL_FEATURES,
                                              leagues=leagues, calibration=calibration)
                entry["models"]["lr_market_paired"] = metric_block(p_fm, yp)
                entry["models"]["lr_features_only_paired"] = metric_block(p_fo, yp)
                paired_out.append({
                    "fold": ftag, "vs": "summary_latest", "n": len(paired),
                    "lr_features_vs_market": paired_bootstrap_dRPS(p_fo, p_mo, yp),
                    "lr_plus_market_vs_market": paired_bootstrap_dRPS(p_fm, p_mo, yp),
                })

        paired_pin = [r for r in test if r["match_id"] in mk_full.get("177", {})]
        if len(paired_pin) >= 100:
            yp = y_of(paired_pin)
            p_mo = np.array([mk_full["177"][r["match_id"]] for r in paired_pin])
            for m in ("dc", "lr", "hgb"):
                pm = np.array([preds_store[(ftag, m)][r["match_id"]] for r in paired_pin])
                paired_out.append({"fold": ftag, "vs": "pinnacle_archive_closing",
                                   "n": len(paired_pin),
                                   f"{m}_vs_market": paired_bootstrap_dRPS(pm, p_mo, yp)})

        entry["per_league"] = {}
        for lid in leagues:
            idx = [i for i, r in enumerate(test) if r["league_id"] == lid]
            if not idx:
                continue
            entry["per_league"][str(lid)] = {
                m: metric_block(np.array([preds_store[(ftag, m)][test[i]["match_id"]] for i in idx]),
                                y_test[idx])
                for m in ("freq", "dc", "lr", "hgb")
            }

        p_hgb = np.array([preds_store[(ftag, "hgb")][r["match_id"]] for r in test])
        entry["calibration_hgb_home"] = calibration_buckets(p_hgb[:, 0], (y_test == 0).astype(int))
        fold_metrics.append(entry)

    ablation: dict[str, list] = defaultdict(list)
    for spec in folds:
        ftag = spec["fold"]
        if season_mode:
            train_all = [r for r in rows if r["season"] in spec["train_seasons"]]
            test = sorted([r for r in rows if r["season"] == spec["test_season"]],
                          key=lambda r: (r["kickoff"], r["match_id"]))
        else:
            train_all = spec["train_rows"]
            test = spec["test_rows"]
        splitter = time_split_stratified if use_stratified_calib else time_split
        train, calib = splitter(train_all, 0.8)
        y_test = y_of(test)
        base_rps = {}
        for kind in ("lr", "hgb"):
            p, _ = fit_predict_sklearn(train, calib, test, kind, ALL_FEATURES,
                                       leagues=leagues, calibration=calibration)
            base_rps[kind] = rps(p, y_test)
        for group in FEATURE_GROUPS:
            feats = [f for g, fl in FEATURE_GROUPS.items() if g != group for f in fl]
            for kind in ("lr", "hgb"):
                p, _ = fit_predict_sklearn(train, calib, test, kind, feats,
                                           leagues=leagues, calibration=calibration)
                ablation[f"{kind}:drop_{group}"].append(
                    {"fold": ftag, "delta_rps": round(rps(p, y_test) - base_rps[kind], 5)})

    abl_out = {}
    for key, entries in sorted(ablation.items()):
        deltas = [e["delta_rps"] for e in entries]
        abl_out[key] = {
            "per_fold": entries,
            "mean_delta_rps": round(float(np.mean(deltas)), 5),
            "sign_stable": bool(all(d > 0 for d in deltas) or all(d < 0 for d in deltas)),
        }

    return {
        "fold_metrics": fold_metrics,
        "paired_market_metrics": paired_out,
        "feature_ablation": abl_out,
    }
