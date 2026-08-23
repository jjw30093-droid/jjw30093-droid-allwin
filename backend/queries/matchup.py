"""图4·本场攻防对位(PREMATCH_MOBILE_DATA_VISUALIZATION_V2 Phase 2.4,最高优先级)。

回答「这队最擅长的进攻方式,对手扛不扛得住」——运动战 / 反击 / 定位球
(含角球)/ 禁区内射门,四条交叉:己方在该类型的场均射门与 xG(进攻),
对手在该类型让出的场均射门与 xG(防守,同一批比赛对手 join,同
`defensive_pressure.py` 的写法但按 `fact_shotmap.Situation` 再切一刀)。

前三类来自 `fact_shotmap.Situation`;禁区内射门刻意**不用射门坐标现算
"禁区"几何**(没有验证过 X/Y 坐标系与真实球场禁区的换算关系,贸然按坐标
框定禁区会引入未经验证的几何假设),改用已经在 Phase 0 基线里验证过
100% 覆盖的 `fact_team_match_stats.extra_json.shots_inside_box` 字段——
数据源自己已经做好了"是否禁区内"的判定,不需要本站重新猜坐标系。这一行
因此没有对应的 xG 拆分(该字段本身不带 xG),前端展示时如实留空,不编造。

诚实纪律:某类型射门 xG 覆盖不完整时该行 xG 记 None + complete=False,
不拿"有 xG 的那部分"冒充整类合计(同 Phase 0.2 的教训)。

验收返工二(独立复核第二轮,两个 P1):
1. 运动战/反击/定位球的"关键对位"判定只能用 xG,禁区内射门只能用射门
   次数——不能靠前端 `own_xg_pg ?? own_shots_pg` 这种隐式 fallback 猜
   单位,那在 xG 缺失(`own_xg_complete=False`)时会把次数当 xG 用,
   跟 xG 口径的基准比出一个假结论(真实复现:比赛 5868022 / 球队 10205,
   运动战 own_shots_pg=6.1、own_xg_pg=None、own_baseline_avg=0.696,
   6.1 > 0.696 恒成立)。改为每个 situation 显式声明
   `comparison_metric`("xg" 或 "shots"),外加不含糊的
   `own_comparison_value`/`conceded_comparison_value`/
   `own_baseline_value`/`conceded_baseline_value`/`comparison_complete`
   ——不再用单一的 `baseline_available` 布尔糊住"己方基准"和"对手基准"
   是否各自真的齐全这两件事。
2. 联赛基准必须 tier-conditioned:目标是 `venue_full` 只能用
   `venue_full` 参考队算基准,目标是 `venue_partial` 只能用
   `venue_partial` 参考队,不能把两档混进同一个分布(两档虽然都算"该队
   自己有真实同场景历史",但样本量本身不同,混算仍然不纯)。目标是
   `mixed`/`unavailable` 时完全不生成基准,不调用
   `league_situation_baseline()`。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from .venue_baseline import _league_team_ids
from .window import DEFAULT_MAX_N, DEFAULT_MIN_N, _lookback_floor, venue_window

_BASELINE_ELIGIBLE_TIERS = ("venue_full", "venue_partial")

_SITUATION_GROUPS: list[tuple[str, str, list[str]]] = [
    ("regular_play", "运动战", ["RegularPlay"]),
    ("fast_break", "反击", ["FastBreak"]),
    ("set_piece", "定位球(含角球)", ["SetPiece", "FromCorner"]),
]

_ALL_SITUATION_KEYS = [key for key, _, _ in _SITUATION_GROUPS] + ["box_shots"]

# 验收返工二:关键对位排序此前直接拿不同 Situation 的原始 xG 相加——运动战
# 天然射门多、xG 基数大,和"这类进攻是不是本场真正的关键对位"是两回事。
# 改为同联赛、同主客场景、同 Situation 各自的联赛基准均值,样本队数低于
# 这个下限时该 Situation 的基准记 None(不编造一个基于极小样本的基准),
# 同 `venue_baseline.MIN_LEAGUE_SAMPLE` 取值一致。
MIN_BASELINE_SAMPLE = 5


def _shots_xg(rows: list[sqlite3.Row], all_match_ids: list[int]) -> tuple[int, float | None, bool, int]:
    """rows 每行须含 Match_ID、xG 两列(该情境类型下己方或对手的全部射门)。
    all_match_ids 是候选窗口的全部比赛 id(含这类射门次数为 0 的比赛)。

    诚实纪律(Phase 0.2 教训:分子分母必须来自同一批场次)+ 2026-08-23
    站长决定:单场比赛里哪怕只有 1 脚射门缺 xG,也把这场比赛的这类射门
    整场从 xG 统计里剔除(不是只丢那一脚"部分已知"凑合计)——分子(xG
    合计)与分母(参与统计的场次)必须配对来自同一批"这类射门全部有 xG"
    的干净比赛。射门次数(shots)不受影响,仍按全量候选场次统计(次数
    不依赖 xG 是否存在,这条口径不变)。

    返回:射门总数(全量口径,给 shots_pg 用)、干净场次的 xG 合计(或
    None)、是否有干净场次可以展示、干净场次数(调用方用这个数字而不是
    窗口原始场次做 xG 的分母——分子分母必须配对,不能用全量场次除一个
    去污染后的合计)。
    """
    shots = len(rows)
    if shots == 0:
        # 这类射门在候选窗口里一次都没发生过,维持原有语义:不展示数字
        # (不是"0.00"——没有观测到不等于观测到了 0 次)。
        return 0, None, False, 0
    by_match: dict[int, list[float | None]] = {}
    for r in rows:
        by_match.setdefault(int(r["Match_ID"]), []).append(r["xG"])
    contaminated = {mid for mid, xgs in by_match.items() if any(x is None for x in xgs)}
    clean_n = sum(1 for mid in all_match_ids if mid not in contaminated)
    if clean_n == 0:
        return shots, None, False, 0
    clean_xg = sum(x for mid, xgs in by_match.items() if mid not in contaminated for x in xgs)
    return shots, clean_xg, True, clean_n


def _own_situation_rows(
    conn: sqlite3.Connection, match_ids: list[int], team_id: int, situations: list[str],
) -> list[sqlite3.Row]:
    if not match_ids:
        return []
    mid_ph = ",".join("?" for _ in match_ids)
    sit_ph = ",".join("?" for _ in situations)
    return conn.execute(
        f"""SELECT Match_ID, xG FROM fact_shotmap
             WHERE Team_ID=? AND Match_ID IN ({mid_ph}) AND Situation IN ({sit_ph})""",
        [team_id, *match_ids, *situations],
    ).fetchall()


def _conceded_situation_rows(
    conn: sqlite3.Connection, match_ids: list[int], team_id: int, situations: list[str],
) -> list[sqlite3.Row]:
    if not match_ids:
        return []
    mid_ph = ",".join("?" for _ in match_ids)
    sit_ph = ",".join("?" for _ in situations)
    return conn.execute(
        f"""SELECT f.Match_ID Match_ID, f.xG xG
              FROM dim_match m
              JOIN fact_shotmap f ON f.Match_ID=m.Match_ID
               AND f.Team_ID = (CASE WHEN m.Home_Team_ID=? THEN m.Away_Team_ID ELSE m.Home_Team_ID END)
             WHERE m.Match_ID IN ({mid_ph}) AND f.Situation IN ({sit_ph})""",
        [team_id, *match_ids, *situations],
    ).fetchall()


def _box_own(conn: sqlite3.Connection, match_ids: list[int], team_id: int) -> tuple[float | None, int]:
    if not match_ids:
        return None, 0
    ph = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""SELECT json_extract(extra_json, '$.shots_inside_box') v FROM fact_team_match_stats
             WHERE Team_ID=? AND Period='All' AND Match_ID IN ({ph})""",
        [team_id, *match_ids],
    ).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _box_conceded(conn: sqlite3.Connection, match_ids: list[int], team_id: int) -> tuple[float | None, int]:
    if not match_ids:
        return None, 0
    ph = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"""SELECT json_extract(t_opp.extra_json, '$.shots_inside_box') v
              FROM dim_match m
              JOIN fact_team_match_stats t_opp ON t_opp.Match_ID=m.Match_ID AND t_opp.Period='All'
               AND t_opp.Team_ID = (CASE WHEN m.Home_Team_ID=? THEN m.Away_Team_ID ELSE m.Home_Team_ID END)
             WHERE m.Match_ID IN ({ph})""",
        [team_id, *match_ids],
    ).fetchall()
    vals = [r["v"] for r in rows if r["v"] is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def league_situation_baseline(
    conn: sqlite3.Connection, league_id: int, before_boundary: str,
    *, is_home: bool, tier: str, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[str, dict[str, Any]]:
    """联赛在该主/客场景、该 `tier` 下,每个 situation 的己方产出、对手
    让出两个基准均值——只用 `venue_window()` 算出来的 tier **精确等于**
    传入 `tier` 的参考队(不是"在 venue_full/venue_partial 里就行":
    venue_full 目标只跟 venue_full 参考队比,venue_partial 目标只跟
    venue_partial 参考队比,两档不得混进同一个分布——验收返工二明确否决
    了此前"只要不是 mixed/unavailable 就算兼容"的宽松判定)。调用方必须
    保证 `tier` 是 `venue_full` 或 `venue_partial`;传其他值时视为无参考
    队(样本恒为 0),不建议这样调用,调用方(`team_matchup_profile`)
    自己在 tier 不是这两档时直接跳过整个基准计算,不会走到这里。

    样本队数低于 `MIN_BASELINE_SAMPLE` 时该项基准记 None,不编造一个基于
    极小样本的数字。

    运动战/反击/定位球三类用 xG(该队该 Situation 全部射门都带 xG 才计入
    样本,不用不完整的部分 xG 拉低基准);禁区内射门没有 xG,用射门次数。
    这个函数本身较贵(遍历联赛全部球队各跑一遍窗口 + shotmap 查询),是
    本轮已知的 P2 性能债,不在这次返工范围内优化——真实测量值见
    `docs/current-state.md` 性能记录章节。
    """
    floor_ = _lookback_floor(before_boundary)
    team_ids = _league_team_ids(conn, league_id, before_boundary, floor_, is_home=is_home)

    own_samples: dict[str, list[float]] = {k: [] for k in _ALL_SITUATION_KEYS}
    conceded_samples: dict[str, list[float]] = {k: [] for k in _ALL_SITUATION_KEYS}

    for tid in team_ids:
        w = venue_window(conn, tid, league_id, before_boundary, is_home=is_home, max_n=max_n, min_n=min_n)
        if w.tier != tier or not w.match_ids:
            continue
        ids = w.match_ids
        n = w.matches
        for key, _label, sits in _SITUATION_GROUPS:
            own_shots, own_xg, own_xg_complete, own_clean_n = _shots_xg(
                _own_situation_rows(conn, ids, tid, sits), ids
            )
            conc_shots, conc_xg, conc_xg_complete, conc_clean_n = _shots_xg(
                _conceded_situation_rows(conn, ids, tid, sits), ids
            )
            if own_xg_complete and own_clean_n > 0:
                own_samples[key].append(own_xg / own_clean_n)
            if conc_xg_complete and conc_clean_n > 0:
                conceded_samples[key].append(conc_xg / conc_clean_n)
        box_own_avg, _box_own_n = _box_own(conn, ids, tid)
        box_conc_avg, _box_conc_n = _box_conceded(conn, ids, tid)
        if box_own_avg is not None:
            own_samples["box_shots"].append(box_own_avg)
        if box_conc_avg is not None:
            conceded_samples["box_shots"].append(box_conc_avg)

    baseline: dict[str, dict[str, Any]] = {}
    for key in _ALL_SITUATION_KEYS:
        own_vals = own_samples[key]
        conc_vals = conceded_samples[key]
        baseline[key] = {
            "own_avg": (sum(own_vals) / len(own_vals)) if len(own_vals) >= MIN_BASELINE_SAMPLE else None,
            "conceded_avg": (sum(conc_vals) / len(conc_vals)) if len(conc_vals) >= MIN_BASELINE_SAMPLE else None,
            "own_sample": len(own_vals),
            "conceded_sample": len(conc_vals),
        }
    return baseline


Comparison = Literal["xg", "shots"]


def _comparison_fields(
    *, metric: Comparison,
    own_xg_pg: float | None, own_xg_complete: bool, own_shots_pg: float | None,
    conc_xg_pg: float | None, conc_xg_complete: bool, conc_shots_pg: float | None,
    baseline_entry: dict[str, Any],
) -> dict[str, Any]:
    """把"用哪个字段比较"这个判断从前端挪到后端——后端已经知道
    `regular_play`/`fast_break`/`set_piece` 恒用 xG、`box_shots` 恒用射门
    次数,不需要前端靠"xG 是不是 None"去猜,那猜法在 xG 不完整
    (`own_xg_complete=False` 但恰好该场次数据仍有射门记录)时会把次数误
    当 xG 用。`comparison_complete` 要求己方比较值、对手比较值、己方基准、
    对手基准全部非空才算真——任一缺失都不该参与"关键对位"判定。
    """
    if metric == "xg":
        own_value = own_xg_pg if own_xg_complete else None
        conc_value = conc_xg_pg if conc_xg_complete else None
    else:
        own_value = own_shots_pg
        conc_value = conc_shots_pg
    own_baseline = baseline_entry["own_avg"]
    conc_baseline = baseline_entry["conceded_avg"]
    own_baseline = round(own_baseline, 3) if own_baseline is not None else None
    conc_baseline = round(conc_baseline, 3) if conc_baseline is not None else None
    complete = (
        own_value is not None and conc_value is not None
        and own_baseline is not None and conc_baseline is not None
    )
    return {
        "comparison_metric": metric,
        "own_comparison_value": round(own_value, 3) if own_value is not None else None,
        "conceded_comparison_value": round(conc_value, 3) if conc_value is not None else None,
        "own_baseline_value": own_baseline,
        "conceded_baseline_value": conc_baseline,
        "comparison_complete": complete,
    }


_EMPTY_BASELINE_ENTRY: dict[str, Any] = {"own_avg": None, "conceded_avg": None, "own_sample": 0, "conceded_sample": 0}


def team_matchup_profile(
    conn: sqlite3.Connection, team_id: int, league_id: int, before_boundary: str,
    *, is_home: bool, max_n: int = DEFAULT_MAX_N, min_n: int = DEFAULT_MIN_N,
) -> dict[str, Any]:
    w = venue_window(conn, team_id, league_id, before_boundary, is_home=is_home, max_n=max_n, min_n=min_n)
    ids = w.match_ids
    n = w.matches
    # 目标自己是 mixed/unavailable 时完全不生成 Situation 基准——这两档
    # 口径本来就跟"纯同场景窗口"的联赛分布不可比,生成一个"看起来有效"
    # 的基准数字比诚实缺失更危险。
    if w.tier in _BASELINE_ELIGIBLE_TIERS:
        baseline = league_situation_baseline(
            conn, league_id, before_boundary, is_home=is_home, tier=w.tier, max_n=max_n, min_n=min_n,
        )
    else:
        baseline = {k: dict(_EMPTY_BASELINE_ENTRY) for k in _ALL_SITUATION_KEYS}

    def _pg(total: float | int | None) -> float | None:
        if total is None or n == 0:
            return None
        return round(total / n, 2)

    situations = []
    for key, label, sits in _SITUATION_GROUPS:
        own_shots, own_xg, own_xg_complete, own_clean_n = _shots_xg(
            _own_situation_rows(conn, ids, team_id, sits), ids
        )
        conc_shots, conc_xg, conc_xg_complete, conc_clean_n = _shots_xg(
            _conceded_situation_rows(conn, ids, team_id, sits), ids
        )
        own_shots_pg = _pg(own_shots) if n > 0 else None
        # xG 的分母是"干净场次数"(own_clean_n/conc_clean_n),不是窗口原始
        # 场次 n——2026-08-23 站长决定:单场里只要有 1 脚缺 xG 就把那场整场
        # 从 xG 统计里剔除,分子分母必须配对来自同一批干净场次,不能拿全量
        # 场次去除一个去污染后的合计(那等于变相把被剔除的场次按 0 计入)。
        own_xg_pg = round(own_xg / own_clean_n, 2) if own_xg_complete else None
        conc_shots_pg = _pg(conc_shots) if n > 0 else None
        conc_xg_pg = round(conc_xg / conc_clean_n, 2) if conc_xg_complete else None
        situations.append({
            "key": key,
            "label": label,
            "own_shots_pg": own_shots_pg,
            "own_xg_pg": own_xg_pg,
            "own_xg_complete": own_xg_complete,
            "own_xg_matches": own_clean_n if own_xg_complete else None,
            "conceded_shots_pg": conc_shots_pg,
            "conceded_xg_pg": conc_xg_pg,
            "conceded_xg_complete": conc_xg_complete,
            "conceded_xg_matches": conc_clean_n if conc_xg_complete else None,
            **_comparison_fields(
                metric="xg",
                own_xg_pg=own_xg_pg, own_xg_complete=own_xg_complete, own_shots_pg=own_shots_pg,
                conc_xg_pg=conc_xg_pg, conc_xg_complete=conc_xg_complete, conc_shots_pg=conc_shots_pg,
                baseline_entry=baseline[key],
            ),
        })

    box_own_avg, box_own_n = _box_own(conn, ids, team_id)
    box_conc_avg, box_conc_n = _box_conceded(conn, ids, team_id)
    box_own_shots_pg = round(box_own_avg, 2) if box_own_avg is not None else None
    box_conc_shots_pg = round(box_conc_avg, 2) if box_conc_avg is not None else None
    situations.append({
        "key": "box_shots",
        "label": "禁区内射门",
        "own_shots_pg": box_own_shots_pg,
        "own_xg_pg": None,
        "own_xg_complete": False,
        "own_xg_matches": None,
        "conceded_shots_pg": box_conc_shots_pg,
        "conceded_xg_pg": None,
        "conceded_xg_complete": False,
        "conceded_xg_matches": None,
        **_comparison_fields(
            metric="shots",
            own_xg_pg=None, own_xg_complete=False, own_shots_pg=box_own_shots_pg,
            conc_xg_pg=None, conc_xg_complete=False, conc_shots_pg=box_conc_shots_pg,
            baseline_entry=baseline["box_shots"],
        ),
    })

    return {
        "tier": w.tier,
        "matches": n,
        "label_zh": w.label_zh,
        "situations": situations,
    }
