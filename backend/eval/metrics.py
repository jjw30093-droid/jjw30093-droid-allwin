"""预测评估指标(纯函数,离线运行;不进在线请求)。

约定:outcome ∈ {'home','draw','away'},概率向量按 (home, draw, away) 排列。
RPS 定义与既有基线一致:sum_{k=1..K-1} (CDF_p - CDF_y)^2 / (K-1),K=3。
"""

import math

OUTCOME_INDEX = {"home": 0, "draw": 1, "away": 2}
_EPS = 1e-15


def _onehot(outcome: str) -> tuple:
    i = OUTCOME_INDEX[outcome]
    return tuple(1.0 if j == i else 0.0 for j in range(3))


def accuracy(samples: list[tuple[tuple, str]]) -> float:
    hit = 0
    for probs, outcome in samples:
        if max(range(3), key=lambda i: probs[i]) == OUTCOME_INDEX[outcome]:
            hit += 1
    return hit / len(samples)


def brier(samples: list[tuple[tuple, str]]) -> float:
    """多分类 Brier:mean(sum_k (p_k - y_k)^2)。"""
    total = 0.0
    for probs, outcome in samples:
        y = _onehot(outcome)
        total += sum((probs[k] - y[k]) ** 2 for k in range(3))
    return total / len(samples)


def log_loss(samples: list[tuple[tuple, str]]) -> float:
    total = 0.0
    for probs, outcome in samples:
        p = max(probs[OUTCOME_INDEX[outcome]], _EPS)
        total += -math.log(p)
    return total / len(samples)


def rps(samples: list[tuple[tuple, str]]) -> float:
    total = 0.0
    for probs, outcome in samples:
        y = _onehot(outcome)
        cp = cy = 0.0
        s = 0.0
        for k in range(2):          # K-1 = 2
            cp += probs[k]
            cy += y[k]
            s += (cp - cy) ** 2
        total += s / 2
    return total / len(samples)


def calibration_buckets(samples: list[tuple[tuple, str]], n_bins: int = 10) -> list[dict]:
    """one-vs-rest 校准表:每类 n_bins 桶,报 avg_pred vs 实际频率与样本数。"""
    out = []
    for cls, idx in OUTCOME_INDEX.items():
        bins = [{"n": 0, "pred_sum": 0.0, "hit": 0} for _ in range(n_bins)]
        for probs, outcome in samples:
            p = probs[idx]
            b = min(int(p * n_bins), n_bins - 1)
            bins[b]["n"] += 1
            bins[b]["pred_sum"] += p
            bins[b]["hit"] += 1 if outcome == cls else 0
        for i, b in enumerate(bins):
            if b["n"] == 0:
                continue
            out.append(
                {
                    "class": cls,
                    "bin_low": round(i / n_bins, 2),
                    "bin_high": round((i + 1) / n_bins, 2),
                    "n": b["n"],
                    "avg_pred": round(b["pred_sum"] / b["n"], 4),
                    "freq": round(b["hit"] / b["n"], 4),
                }
            )
    return out


def evaluate_all(samples: list[tuple[tuple, str]]) -> dict:
    if not samples:
        return {"sample_size": 0}
    return {
        "sample_size": len(samples),
        "accuracy": round(accuracy(samples), 6),
        "brier": round(brier(samples), 6),
        "log_loss": round(log_loss(samples), 6),
        "rps": round(rps(samples), 6),
        "calibration": calibration_buckets(samples),
    }
