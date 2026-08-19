"""赛前市场卡:结论(数据倾向 + 历史命中率)+ 折叠归因明细。

结论只来自 backend/eval/calibrate_markets.py 离线回测写入的
platform.db market_calibration 表——本模块不重新计算任何统计意义上的
"信号",只做查询和拼装。预估值(predictor)的算法必须与
calibrate_markets.py::build_predictions 完全一致(两队各自近 WINDOW 场
历史均值之和),否则标定用的定义和线上展示的定义不一致,命中率查表
失去意义(§文件头见 calibrate_markets.py 顶部说明)。

措辞纪律:只写"数据倾向""历史该档命中率",不写"推荐""必胜""稳赚"。
外样本不单调的 (market, line) 组合,signal_grade 是 NULL——查询层原样
透传 None,不在这一层"补"出一个方向性结论。

2026-08-19「数据倾向」填充(生产实测 ok 54% / insufficient_sample 25% /
no_calibration 21%,见 docs/current-state.md):在不碰 predictor 定义、不碰
结论区判定字段(B6)的前提下,把已经算出来但此前被丢弃的数据点亮——
market.lines 全量标定结果(``lines[]``)、calibrated_at、no_calibration 的
原因细分,以及折叠归因区补充的高阶指标(``_EXTRA_DRIVER_KEYS_BY_MARKET``,
零新查询,复用 team_recent_profile 已经算出的全部 TEAM_STAT_KEYS)。
"""

from __future__ import annotations

import sqlite3

from backend.eval.calibrate_markets import ALL_LEAGUES_SENTINEL, MARKETS
from backend.queries.odds import latest_market_line
from backend.queries.team_form import team_recent_profile

WINDOW = 10  # 必须与 calibrate_markets.py 的默认 window 一致

# 市场卡 key → bronze_ng_odds_snap.market 的真实盘口来源(2026-08-14 真实
# 探测,见 backend/migrations/odds/0008_corners_market.sql 与
# backend/providers/nowgoal.py 的角球盘注释)。yellow_cards(罚牌)没有
# 对应真实市场——NowGoal 完全不提供黄牌/罚牌盘口,该市场卡永远只能用
# 统计参考线,不在这个映射里,line_source 恒为 "statistical"。
_REAL_MARKET_BY_CARD_KEY = {
    "goals": "ou",
    "corners": "corners_ou",
}


def _bucket_lookup(
    conn_platform: sqlite3.Connection, market: str, league_id: int, line: float, predictor: float
) -> dict | None:
    """优先本联赛标定,没有则退回跨联赛合并(league_id=0)。"""
    for lid in (league_id, ALL_LEAGUES_SENTINEL):
        rows = conn_platform.execute(
            """SELECT bucket_index, bucket_lower, bucket_upper, hit_rate,
                      sample_size, signal_grade, calibrated_at
                 FROM market_calibration
                WHERE market=? AND league_id=? AND line=?
                ORDER BY bucket_index""",
            (market, lid, line),
        ).fetchall()
        if not rows:
            continue
        for r in rows:
            lower, upper = r["bucket_lower"], r["bucket_upper"]
            if (lower is None or predictor > lower) and (upper is None or predictor <= upper):
                return {
                    "bucket_index": r["bucket_index"],
                    "hit_rate": r["hit_rate"],
                    "sample_size": r["sample_size"],
                    "signal_grade": r["signal_grade"],
                    "calibration_scope": "league" if lid != ALL_LEAGUES_SENTINEL else "all_leagues",
                    "calibrated_at": r["calibrated_at"],
                }
        # 预估值落在最后一档边界之外(理论上不该发生,浮点误差兜底)
        last = rows[-1]
        return {
            "bucket_index": last["bucket_index"],
            "hit_rate": last["hit_rate"],
            "sample_size": last["sample_size"],
            "signal_grade": last["signal_grade"],
            "calibration_scope": "league" if lid != ALL_LEAGUES_SENTINEL else "all_leagues",
            "calibrated_at": last["calibrated_at"],
        }
    return None


def _line_calibration(
    conn_platform: sqlite3.Connection, market_key: str, league_id: int, ln: float, default_line: float,
    estimate: float,
) -> dict:
    """折叠区"本市场各盘口线的历史回测"一行:对 market.lines 里的某一条线
    单独查一次标定档位,与卡片顶层判定完全独立(B6:不影响、不参与
    data_quality/signal_grade/lean 的计算)。

    B2 落到数据层:signal_grade 为 None(该线从未标定过,或标定了但外样本
    不单调)时,hit_rate/sample_size 在这里就置 None——数字根本不下发到
    浏览器,不是"下发了但前端别渲染"。
    """
    b = _bucket_lookup(conn_platform, market_key, league_id, ln, estimate)
    graded = bool(b and b["signal_grade"])
    return {
        "line": ln,
        "is_default": ln == default_line,
        "signal_grade": b["signal_grade"] if b else None,
        "hit_rate": round(b["hit_rate"], 4) if graded else None,
        "sample_size": b["sample_size"] if graded else None,
    }


# Fix 3(数据倾向卡片填充):team_recent_profile 每次请求都算出全部
# TEAM_STAT_KEYS(37 个),折叠归因区此前只展示 calibrate_markets.py 里的
# predictor_key + driver_keys 这一小部分。按市场相关性补充几个已经算好但
# 被丢弃的高阶指标——纯展示层追加,同一份 profile 内已有,零新查询,不修改
# calibrate_markets.py(B1/本次不改清单)。accurate_crosses(角球)已经在
# calibrate_markets.py 的 driver_keys 里,这里不重复添加。
_EXTRA_DRIVER_KEYS_BY_MARKET: dict[str, list[str]] = {
    "goals": ["expected_goals_on_target", "big_chance_missed", "shots_inside_box"],
    "yellow_cards": ["tackles", "duel_won"],
    "corners": ["shots_outside_box", "shot_blocks"],
}


def _driver_keys(market) -> list[str]:
    return [market.predictor_key, *market.driver_keys, *_EXTRA_DRIVER_KEYS_BY_MARKET.get(market.key, [])]


def _driver_row(profile: dict, key: str) -> dict:
    m = profile["metrics"].get(key, {"for": {"avg": None, "n": 0}, "against": {"avg": None, "n": 0}})
    return {"key": key, "for": m["for"], "against": m["against"]}


def match_market_cards(
    conn_core: sqlite3.Connection,
    conn_platform: sqlite3.Connection,
    conn_odds: sqlite3.Connection,
    *,
    fotmob_match_id: int,
    home_id: int,
    away_id: int,
    league_id: int,
    before_date: str,
) -> list[dict]:
    """两队都不够历史(matches_considered=0)时,仍然为每个市场返回一张
    data_quality='no_history' 的卡——前端据此渲染"该联赛历史数据补采中",
    不是把整个市场卡列表砍掉,页面结构保持稳定。

    盘口线来源(line_source):goals(大小球)/corners(角球)在这场比赛真的
    抓到 NowGoal 实时盘口(latest_market_line)时,直接用真实盘口线(而不是
    统计参考线)——calibration 表按 (market, league_id, line) 精确匹配,
    真实线命中已标定档位就显示真实命中率,没命中就诚实降级
    data_quality='no_calibration'(不为了凑一个能打星的档位悄悄换回统计线)。
    yellow_cards(罚牌)在 NowGoal 上完全没有对应市场,line_source 恒为
    "statistical"。
    """
    home = team_recent_profile(
        conn_core, home_id, before_date=before_date, n=WINDOW, scope="same_league", league_id=league_id
    )
    away = team_recent_profile(
        conn_core, away_id, before_date=before_date, n=WINDOW, scope="same_league", league_id=league_id
    )

    cards: list[dict] = []
    for market in MARKETS.values():
        h = home["metrics"].get(market.predictor_key, {"for": {"avg": None, "n": 0}})
        a = away["metrics"].get(market.predictor_key, {"for": {"avg": None, "n": 0}})
        h_avg, a_avg = h["for"]["avg"], a["for"]["avg"]

        if home["matches_considered"] == 0 and away["matches_considered"] == 0:
            data_quality = "no_history"
        elif h_avg is None or a_avg is None:
            data_quality = "insufficient_sample"
        else:
            data_quality = "ok"

        real_market = _REAL_MARKET_BY_CARD_KEY.get(market.key)
        real_line = latest_market_line(conn_odds, fotmob_match_id, real_market) if real_market else None
        line = real_line["line"] if real_line else market.default_line
        line_source = "market" if real_line else "statistical"

        driver_keys = _driver_keys(market)
        card: dict = {
            "market": market.key,
            "label": market.label,
            "line": line,
            "line_source": line_source,
            "estimate": None,
            "bucket_index": None,
            "hit_rate": None,
            "sample_size": None,
            "signal_grade": None,
            "lean": None,               # "over" / "under",hit_rate 相对 0.5 的方向
            "calibration_scope": None,
            "data_quality": data_quality,
            "driver_factors": [_driver_row(home, k) for k in driver_keys],
            "driver_factors_away": [_driver_row(away, k) for k in driver_keys],
            "lines": [],
            "calibrated_at": None,
            "no_calibration_reason": None,
        }

        if data_quality == "ok":
            estimate = round(h_avg + a_avg, 2)
            card["estimate"] = estimate

            # Fix 1:该市场全部已标定线(market.lines,不止默认线)各自查一遍
            # 档位——与下面结论区的判定完全独立的第二组查询,不影响、不参与
            # data_quality/signal_grade/lean 的计算(B6:不换线)。
            card["lines"] = [
                _line_calibration(conn_platform, market.key, league_id, ln, line, estimate)
                for ln in market.lines
            ]

            bucket = _bucket_lookup(conn_platform, market.key, league_id, line, estimate)
            if bucket is not None:
                card.update(
                    bucket_index=bucket["bucket_index"],
                    hit_rate=round(bucket["hit_rate"], 4),
                    sample_size=bucket["sample_size"],
                    signal_grade=bucket["signal_grade"],
                    calibration_scope=bucket["calibration_scope"],
                    lean=("over" if bucket["hit_rate"] > 0.5 else "under") if bucket["signal_grade"] else None,
                    calibrated_at=bucket["calibrated_at"],
                )
            else:
                card["data_quality"] = "no_calibration"
                # Fix 6:线本身不在 market.lines(已标定线集合)里——多见于
                # 真实盘口线是整数线(10.0/9.0/…)——与"线在集合内、但这次真的
                # 查不到任何标定行"(理论上不该发生,保留原通用文案)是两种
                # 不同的诚实降级,前端文案不应该混为一谈。
                card["no_calibration_reason"] = (
                    "line_not_calibrated" if line not in market.lines else "line_unresolved"
                )

        cards.append(card)
    return cards
