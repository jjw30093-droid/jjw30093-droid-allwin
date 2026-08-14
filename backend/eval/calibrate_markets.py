"""赛前市场离线标定(CLI,离线运行,绝不进 API 请求路径——CLAUDE.md §5.3)。

用法::

    python -m backend.eval.calibrate_markets                  # 全部已定义市场,跨联赛合并
    python -m backend.eval.calibrate_markets --market yellow_cards
    python -m backend.eval.calibrate_markets --league-id 47   # 只标定单个联赛

## 为什么要分档,而不是直接预测一个数字

单场足球本质上几乎不可预测:用两队近 10 场历史做 14 个因子的多元线性回归,
时间序 80/20 切分后,对"永远猜训练集均值"这个基准的 RMSE 改善只有
0.5%-2.9%(本模块的兄弟实验,记录在 docs/current-state.md)。但 R² 低不等于
没有信号——按预估值分 5 档后,顶档与底档在真实历史里的过线率可以有 8-29
个百分点的差距,且这个差距在时间序外样本上依然单调。这才是"数据倾向"
类意见唯一站得住脚的形式。

## 方法论(不可偏离,偏离等于编造信号)

1. 预估值 = 两队各自近 window 场"自己"的历史均值之和(不是"两队合计"的
   历史均值,是"各自的历史"相加)——预测的是"这两支球队组合在一起,历史上
   会打出多大的量"。
2. 目标值(actual)= 该场真实发生的合计(通常与预估值同一个统计口径;
   大小球例外,预估值用两队近期 xG 历史,目标值用真实进球数——预测机会
   质量,验证真实结果)。
3. **严格按时间顺序**滚动:第 k 场比赛的预估值只能用第 k 场之前的历史,
   绝不使用同场或未来数据(look-ahead bias 是这类回测最容易犯、最难发现
   的错误)。
4. 前 80%(按时间)划为 calibration 集合,只用它确定 5 档的分位数边界;
   后 20% 划为 validation 集合,分档命中率只在这个**从未参与定档**的
   集合上计算。
5. **外样本不单调就不给意见**:validation 集合上顶档命中率必须真实高于
   底档,且允许至多 1 处相邻档位逆序(允许噪音,不允许趋势整体反向)。
   不满足条件的 (market, league_id, line) 组合,signal_grade 写 NULL,
   前端据此只展示数据面板,不显示"倾向"文案。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field

from backend.db.connections import connect_ro, connect_rw, tx
from backend.db.util import utc_now_iso

BUCKET_COUNT = 5
MIN_BUCKET_SAMPLE = 20  # 验证集里某档样本太少,命中率噪音太大,不可信
ALL_LEAGUES_SENTINEL = 0  # 与 migration 里的哨兵值一致,league_id=0 表示跨联赛合并


@dataclass(frozen=True)
class MarketDef:
    key: str
    label: str
    # 预估值用的历史统计口径(fact_team_match_stats.extra_json 的 key,
    # 与 backend/queries/match_report.py::TEAM_STAT_KEYS 的 DTO 名一致——
    # 市场卡在线上用 team_recent_profile() 算预估值时必须读同一个 key,
    # 否则标定用的定义和线上展示的定义不一致,命中率查表失去意义)。
    predictor_key: str
    # 目标值口径:"extra_json:<key>" 或特殊值 "goals_column"(真实进球数,
    # 与 xG 预估值分离——大小球市场预测机会质量,验证真实结果)。
    actual_key: str
    lines: list[float] = field(default_factory=list)
    # 市场卡结论区默认展示的盘口线(必须是 lines 之一);折叠区展示 lines 全部。
    default_line: float = 0.0
    # 折叠归因区的补充指标(team_recent_profile 的 DTO key),不含 predictor_key
    # 本身(那个已经是结论区的主指标,折叠区不重复)。
    driver_keys: list[str] = field(default_factory=list)


MARKETS: dict[str, MarketDef] = {
    "yellow_cards": MarketDef(
        key="yellow_cards", label="罚牌", predictor_key="yellow_cards",
        actual_key="extra_json:yellow_cards", lines=[2.5, 3.5, 4.5], default_line=3.5,
        driver_keys=["fouls", "red_cards"],
    ),
    "goals": MarketDef(
        key="goals", label="大小球", predictor_key="expected_goals",
        actual_key="goals_column", lines=[2.5, 3.5], default_line=2.5,
        driver_keys=["shots_on_target", "big_chance"],
    ),
    "corners": MarketDef(
        # 9.5 是竞彩最常见的角球线,选它当默认线——即使某次标定跑出来这条线
        # 外样本不单调(实测发生过),查询层也必须诚实展示"这条线暂无信号",
        # 不能为了凑一个能打星的默认线换成竞彩不常用的线。
        key="corners", label="角球", predictor_key="corners",
        actual_key="extra_json:corners", lines=[8.5, 9.5, 10.5], default_line=9.5,
        driver_keys=["touches_opp_box", "accurate_crosses", "blocked_shots"],
    ),
}


def _extract(extra_json: str | None, key: str) -> float | None:
    if not extra_json:
        return None
    try:
        val = json.loads(extra_json).get(key)
    except (json.JSONDecodeError, AttributeError):
        return None
    return float(val) if isinstance(val, (int, float)) else None


def _load_rows(conn_core: sqlite3.Connection, league_id: int | None) -> list[sqlite3.Row]:
    league_clause = "AND m.League_ID=?" if league_id else ""
    params: list[int] = [league_id] if league_id else []
    return conn_core.execute(
        f"""SELECT m.Date, s.Match_ID, s.Team_ID, s.Goals, s.extra_json
              FROM fact_team_match_stats s
              JOIN dim_match m ON m.Match_ID = s.Match_ID
             WHERE s.Period='All' AND m.status IN ('Finish','Finished')
               {league_clause}
             ORDER BY m.Date, s.Match_ID""",
        params,
    ).fetchall()


@dataclass
class PredictionRow:
    date: str
    match_id: int
    predictor: float
    actual: float


def build_predictions(
    conn_core: sqlite3.Connection, market: MarketDef, *, window: int = 10, league_id: int | None = None,
) -> list[PredictionRow]:
    """严格时间序滚动:第 k 场只用第 k 场之前每支球队各自的历史均值。"""
    rows = _load_rows(conn_core, league_id)
    by_match: dict[int, dict[int, sqlite3.Row]] = {}
    for r in rows:
        by_match.setdefault(int(r["Match_ID"]), {})[int(r["Team_ID"])] = r

    # 按比赛日期 + Match_ID 排序,保证同一天多场时顺序稳定可复现
    matches = sorted(
        ((v[next(iter(v))]["Date"], mid, v) for mid, v in by_match.items() if len(v) == 2),
        key=lambda x: (x[0], x[1]),
    )

    history: dict[int, list[float]] = {}
    out: list[PredictionRow] = []
    for date, match_id, teams in matches:
        team_ids = list(teams.keys())
        hists = [history.get(t, []) for t in team_ids]
        if all(len(h) >= 3 for h in hists):
            predictor = sum(sum(h[-window:]) / len(h[-window:]) for h in hists)
            if market.actual_key == "goals_column":
                actual = sum(float(teams[t]["Goals"] or 0) for t in team_ids)
            else:
                _, key = market.actual_key.split(":", 1)
                vals = [_extract(teams[t]["extra_json"], key) for t in team_ids]
                actual = sum(v for v in vals if v is not None) if all(v is not None for v in vals) else None
            if actual is not None:
                out.append(PredictionRow(date=date, match_id=match_id, predictor=predictor, actual=actual))
        for t in team_ids:
            v = _extract(teams[t]["extra_json"], market.predictor_key)
            if v is not None:
                history.setdefault(t, []).append(v)
    return out


def _bucket_boundaries(values: list[float], k: int = BUCKET_COUNT) -> list[float]:
    s = sorted(values)
    n = len(s)
    return [s[min(n - 1, int(n * i / k))] for i in range(1, k)]


def _bucket_of(value: float, boundaries: list[float]) -> int:
    for i, b in enumerate(boundaries):
        if value <= b:
            return i
    return len(boundaries)


@dataclass
class BucketResult:
    bucket_index: int
    lower: float | None
    upper: float | None
    hit_rate: float
    sample_size: int


@dataclass
class CalibrationResult:
    market: str
    league_id: int
    line: float
    buckets: list[BucketResult]
    spread_pp: float
    monotonic: bool
    signal_grade: str | None
    train_size: int


def _grade(spread_pp: float, monotonic: bool) -> str | None:
    if not monotonic:
        return None
    if spread_pp >= 25:
        return "★★★"
    if spread_pp >= 12:
        return "★★"
    if spread_pp >= 5:
        return "★"
    return None


def _is_monotonic(hit_rates: list[float], *, max_inversions: int = 1) -> bool:
    inversions = sum(
        1 for a, b in zip(hit_rates, hit_rates[1:]) if b < a
    )
    return inversions <= max_inversions


def calibrate_one(preds: list[PredictionRow], market: str, league_id: int, line: float) -> CalibrationResult | None:
    """80/20 时间序切分:前 80% 定档边界,后 20% 算命中率(never 用同一批
    数据既定档又算命中率——那会把噪音也算成信号)。"""
    if len(preds) < 100:
        return None
    ordered = sorted(preds, key=lambda p: (p.date, p.match_id))
    split = int(len(ordered) * 0.8)
    train, valid = ordered[:split], ordered[split:]
    boundaries = _bucket_boundaries([p.predictor for p in train])

    by_bucket: dict[int, list[PredictionRow]] = {i: [] for i in range(BUCKET_COUNT)}
    for p in valid:
        by_bucket[_bucket_of(p.predictor, boundaries)].append(p)

    buckets: list[BucketResult] = []
    for i in range(BUCKET_COUNT):
        rows = by_bucket[i]
        hit_rate = sum(1 for p in rows if p.actual > line) / len(rows) if rows else 0.0
        lower = boundaries[i - 1] if i > 0 else None
        upper = boundaries[i] if i < len(boundaries) else None
        buckets.append(BucketResult(i, lower, upper, hit_rate, len(rows)))

    if any(b.sample_size < MIN_BUCKET_SAMPLE for b in buckets):
        # 样本量不够的档位命中率不可信——整组标定作废,不半吊子输出。
        monotonic = False
        spread_pp = 0.0
    else:
        rates = [b.hit_rate for b in buckets]
        spread_pp = round((max(rates) - min(rates)) * 100, 1)
        monotonic = _is_monotonic(rates)

    return CalibrationResult(
        market=market, league_id=league_id, line=line, buckets=buckets,
        spread_pp=spread_pp, monotonic=monotonic,
        signal_grade=_grade(spread_pp, monotonic), train_size=len(train),
    )


def persist(conn_platform: sqlite3.Connection, results: list[CalibrationResult]) -> None:
    now = utc_now_iso()
    with tx(conn_platform):
        for r in results:
            conn_platform.execute(
                "DELETE FROM market_calibration WHERE market=? AND league_id=? AND line=?",
                (r.market, r.league_id, r.line),
            )
            for b in r.buckets:
                conn_platform.execute(
                    """INSERT INTO market_calibration
                       (market, league_id, line, bucket_index, bucket_lower, bucket_upper,
                        hit_rate, sample_size, signal_grade, spread_pp, monotonic,
                        train_size, calibrated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r.market, r.league_id, r.line, b.bucket_index, b.lower, b.upper,
                     b.hit_rate, b.sample_size, r.signal_grade, r.spread_pp, int(r.monotonic),
                     r.train_size, now),
                )


def run(market_keys: list[str], *, league_id: int | None = None, window: int = 10) -> list[CalibrationResult]:
    conn_core = connect_ro("core")
    results: list[CalibrationResult] = []
    for key in market_keys:
        market = MARKETS[key]
        preds = build_predictions(conn_core, market, window=window, league_id=league_id)
        for line in market.lines:
            r = calibrate_one(preds, key, league_id or ALL_LEAGUES_SENTINEL, line)
            if r is not None:
                results.append(r)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赛前市场离线标定")
    ap.add_argument("--market", choices=sorted(MARKETS), default=None,
                     help="只标定单个市场,默认全部")
    ap.add_argument("--league-id", type=int, default=None,
                     help="只用该联赛数据标定,默认跨联赛合并")
    ap.add_argument("--window", type=int, default=10, help="近 N 场历史窗口")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果,不写库")
    args = ap.parse_args(argv)

    keys = [args.market] if args.market else sorted(MARKETS)
    results = run(keys, league_id=args.league_id, window=args.window)

    for r in results:
        m = MARKETS[r.market]
        status = r.signal_grade or ("不单调,不给意见" if not r.monotonic else "样本不足")
        print(f"{m.label} 线={r.line}: spread={r.spread_pp}pp grade={status} "
              f"(train={r.train_size}, valid_total={sum(b.sample_size for b in r.buckets)})")

    if not args.dry_run:
        conn_platform = connect_rw("platform")
        persist(conn_platform, results)
        print(f"写入 market_calibration:{sum(len(r.buckets) for r in results)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
