"""research/ah_signals 公共常量与只读工具。

所有数据库访问必须经 `open_ro()`(sqlite3 URI `mode=ro`),零写入生产库。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

LEAGUES = (47, 53, 54, 55, 87)
LEAGUE_NAME = {47: "英超", 53: "法甲", 54: "德甲", 55: "意甲", 87: "西甲"}
SEASONS = ("2025/2026", "2026/2027")
MARKETS = ("ah", "ou", "1x2")

# company_id → 合并后的公司键。Macauslot 两个 id 合并;Bet365 的 '281' 是 1x2-only
# 的历史回填 id(见 backend/queries/odds.py 注释),同样合并进 Bet365 并在 Phase 0
# 报告里单独标出。Pinnacle('177')/Sbobet('31')按任务书禁用,不出现在这里。
COMPANIES = {
    "1": "Macauslot",
    "80": "Macauslot",
    "8": "Bet365",
    "281": "Bet365",
    "3": "Crown",
}
COMPANY_IDS = {
    "Macauslot": ("1", "80"),
    "Bet365": ("8", "281"),
    "Crown": ("3",),
}


def open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def quantiles(values, probs=(0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)) -> list[float]:
    """线性插值分位数;空列表返回 NaN。"""
    vals = sorted(values)
    if not vals:
        return [math.nan] * len(probs)
    out = []
    n = len(vals)
    for p in probs:
        k = (n - 1) * p
        f = int(math.floor(k))
        c = min(f + 1, n - 1)
        out.append(vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f))
    return out
