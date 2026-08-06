"""FotMob 历史缺口比赛 ↔ NowGoal titan_id 身份解析(纯函数,不发网络请求、
不碰数据库)。

与 backend/ingest/kbisai_match_resolution.py / entity_resolution.py 是两个
独立实现,并列存在,互不修改、互不调用。之所以需要第三套实现:那两个模块
100% 依赖精确 kickoff(kbisai_match_resolution 要求 ``FotMobMatchRef.kickoff_at_utc``
非空且按 ±1800s/±60s 窗口过滤候选)。而本模块要解析的 2,269 场历史缺口比赛
``kickoff_at_utc`` 全部为 NULL、``kickoff_precision='date_only'``——候选会被
那两个模块的过滤器全部滤空,是结构性不可用,不是调参能救的。

可用信号换成了五层证据(按强度降序):
1. 联赛硬过滤(``FOTMOB_TO_NOWGOAL_LEAGUE``)
2. 自然日 ±N 天硬过滤(NowGoal 是北京时,FotMob 只有自然日,不假装有精确时刻)
3. 球队 ID 词典(``build_team_id_dictionary``,方向从最终比分重推,不信任何
   ``is_swapped`` 字段——旧仓的 is_swapped 已被证明不可信,见 docs/current-state.md
   P0 记录)
4. 最终比分精确相等(旧仓完全没用过这个信号)
5. 英文队名归一化相似度(词典外球队兜底)

fail-closed:仅当"恰好一个"候选同时满足方向证据与比分证据、且其 titan_id
未被本批次内其它比赛占用,才判定为匹配;0 个候选、≥2 个候选、titan_id 冲突
都不猜,原样进 review。``home_away_inverted``(即 direction == "inverted")
一律降级为 needs_review——本仓已核实的真实 inverted 基线为 0,判成反转更可能
是匹配错了,而不是当前这场比赛真的方向颠倒。
"""

from __future__ import annotations

import dataclasses
import difflib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence

FOTMOB_TO_NOWGOAL_LEAGUE = {47: 36, 87: 31, 54: 8, 55: 34, 53: 11}

STATUS_AUTO_OK = "auto_ok"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_NO_CANDIDATE = "no_candidate"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_TITAN_CONFLICT = "titan_conflict"

DIRECT = "direct"
INVERTED = "inverted"

_KICKOFF_SENTINEL_SUFFIX = "23:59"


@dataclass(frozen=True)
class NowGoalCandidate:
    """一个 NowGoal 历史候选比赛(调用方已按联赛/赛季从 archive 或 fixtures 抽出)。"""

    titan_id: str
    nowgoal_league_id: int
    kickoff_local: str | None  # 北京时 "YYYY-MM-DD HH:MM",来源未知时为 None
    home_team_id: int | None  # NowGoal 自己的球队 id 命名空间
    away_team_id: int | None
    home_team_name: str | None
    away_team_name: str | None
    home_score: int | None
    away_score: int | None


@dataclass(frozen=True)
class GapMatch:
    """待解析的 FotMob 历史缺口比赛(来自 Phase 0 gaps.jsonl 的一行)。"""

    match_id: int
    league_id: int  # FotMob 联赛 id
    date: str  # FotMob 自然日 "YYYY-MM-DD"(UTC,无精确时刻)
    home_team_id: int | None  # FotMob 自己的球队 id 命名空间
    away_team_id: int | None
    home_team_name: str | None
    away_team_name: str | None
    home_score: int | None
    away_score: int | None


@dataclass(frozen=True)
class TrainingPair:
    """一条已确认的历史配对,用于学习球队 id 词典。方向从比分重推,``is_swapped``
    等旧仓字段一律不读——调用方不应该把它们传进来。"""

    ng_home_id: int | None
    ng_away_id: int | None
    ng_home_score: int | None
    ng_away_score: int | None
    fm_home_id: int | None
    fm_away_id: int | None
    fm_home_score: int | None
    fm_away_score: int | None


@dataclass(frozen=True)
class ResolutionResult:
    match_id: int
    status: str
    titan_id: str | None = None
    direction: str | None = None  # "direct" / "inverted" / None
    evidence_kind: str | None = None  # "id" / "name" / None
    evidence_strength: float | None = None
    detail: str = ""
    matched_titan_kickoff_local: str | None = None
    """胜出候选自己的北京时间开球时刻(原样透传,未做时区换算)。下游要精确
    kickoff_utc(比如跳过 archive_season 重新发现、直接按 titan_id 抓历史赔率时
    做 pre-match/in-play 切分)时用这个字段,而不是 GapMatch.date(FotMob 自然日,
    没有精确时刻,±2 天窗口只用来做候选粗筛,不能冒充开球时刻——CLAUDE.md §6.2.1)。"""

    @property
    def home_away_inverted(self) -> int:
        return 1 if self.direction == INVERTED else 0


def kickoff_precision(kickoff_local: str | None) -> str:
    """``23:59`` 是来源的"时间未知"占位值(实测 2020/21 archive 1,826 行里
    78 行如此,分布 0~31 场不等,聚集度证明是兜底值而非真实开球时刻)。
    只影响下游要不要把这场标成 provider_unknown,不参与本模块的候选过滤——
    候选过滤只看日期部分,占位时间不影响日期。"""
    if not kickoff_local:
        return "unknown"
    if kickoff_local.strip().endswith(_KICKOFF_SENTINEL_SUFFIX):
        return "provider_unknown"
    return "reported"


def _parse_ymd(s: str | None) -> date | None:
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def within_date_window(fotmob_date: str, kickoff_local: str | None, window_days: int = 2) -> bool | None:
    """True/False 表示可判定;None 表示两侧日期有一侧不可解析,调用方必须把
    这种情况当作"排除"处理(fail-closed),不能当作命中。"""
    fm = _parse_ymd(fotmob_date)
    ng = _parse_ymd(kickoff_local)
    if fm is None or ng is None:
        return None
    return abs((fm - ng).days) <= window_days


def parse_score(raw: str | None) -> tuple[int, int] | None:
    """NowGoal/miaomiaodi 的比分记法 "H-A"。不可解析(空、点球大战附注等)返回 None。"""
    if not raw:
        return None
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(raw))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def team_direction_from_score(
    ng_home_score: int | None, ng_away_score: int | None,
    fm_home_score: int | None, fm_away_score: int | None,
) -> str | None:
    """比分是否对称复现,用来独立判定方向。任一侧比分缺失,或比分平局对称
    (如 1-1 vs 1-1,两个方向都能"巧合"吻合)时返回 None——平局场次的方向
    无法仅靠比分判定,必须靠其它证据。"""
    if None in (ng_home_score, ng_away_score, fm_home_score, fm_away_score):
        return None
    direct = ng_home_score == fm_home_score and ng_away_score == fm_away_score
    inverted = ng_home_score == fm_away_score and ng_away_score == fm_home_score
    if direct and not inverted:
        return DIRECT
    if inverted and not direct:
        return INVERTED
    return None


def build_team_id_dictionary(
    training_pairs: Iterable[TrainingPair],
    *, min_votes: int = 10, min_margin_ratio: float = 5.0,
) -> dict[int, int]:
    """从已确认配对学 nowgoal_team_id -> fotmob_team_id。多数票 + 最少票数 +
    领先倍数三重门槛,达不到门槛的 nowgoal_team_id 不进词典(宁缺毋滥,交给
    队名相似度兜底)——门槛本身来自比分重推的方向,不信任何 is_swapped 字段。"""
    votes: dict[int, Counter] = defaultdict(Counter)
    for p in training_pairs:
        direction = team_direction_from_score(p.ng_home_score, p.ng_away_score, p.fm_home_score, p.fm_away_score)
        if direction is None:
            continue
        if direction == DIRECT:
            id_pairs = [(p.ng_home_id, p.fm_home_id), (p.ng_away_id, p.fm_away_id)]
        else:
            id_pairs = [(p.ng_home_id, p.fm_away_id), (p.ng_away_id, p.fm_home_id)]
        for ng_id, fm_id in id_pairs:
            if ng_id is None or fm_id is None:
                continue
            votes[ng_id][fm_id] += 1

    result: dict[int, int] = {}
    for ng_id, counter in votes.items():
        ranked = counter.most_common()
        top_fm, top_n = ranked[0]
        if top_n < min_votes:
            continue
        runner_up_n = ranked[1][1] if len(ranked) > 1 else 0
        if runner_up_n > 0 and (top_n / runner_up_n) < min_margin_ratio:
            continue
        result[ng_id] = top_fm
    return result


def _normalize_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def _direction_evidence(
    candidate: NowGoalCandidate,
    match: GapMatch,
    team_id_dict: Mapping[int, int],
    name_threshold: float,
) -> tuple[str | None, str | None, float | None]:
    """返回 (direction, evidence_kind, strength)。evidence_kind='id' 要求两队
    id 都在词典里且方向自洽;词典给出方向但两边都对不上时视为该候选不合格
    (不是自动降级去试队名——词典既然覆盖了这两支球队,矛盾就是真的矛盾)。"""
    ng_h = team_id_dict.get(candidate.home_team_id) if candidate.home_team_id is not None else None
    ng_a = team_id_dict.get(candidate.away_team_id) if candidate.away_team_id is not None else None
    if ng_h is not None and ng_a is not None:
        if ng_h == match.home_team_id and ng_a == match.away_team_id:
            return DIRECT, "id", 1.0
        if ng_h == match.away_team_id and ng_a == match.home_team_id:
            return INVERTED, "id", 1.0
        return None, None, None

    if not (candidate.home_team_name and candidate.away_team_name and match.home_team_name and match.away_team_name):
        return None, None, None

    direct_score = min(
        name_similarity(candidate.home_team_name, match.home_team_name),
        name_similarity(candidate.away_team_name, match.away_team_name),
    )
    inverted_score = min(
        name_similarity(candidate.home_team_name, match.away_team_name),
        name_similarity(candidate.away_team_name, match.home_team_name),
    )
    if direct_score >= name_threshold and direct_score >= inverted_score:
        return DIRECT, "name", direct_score
    if inverted_score >= name_threshold:
        return INVERTED, "name", inverted_score
    return None, None, None


def _score_matches(candidate: NowGoalCandidate, match: GapMatch, direction: str) -> bool:
    if None in (candidate.home_score, candidate.away_score, match.home_score, match.away_score):
        return False
    if direction == DIRECT:
        return candidate.home_score == match.home_score and candidate.away_score == match.away_score
    return candidate.home_score == match.away_score and candidate.away_score == match.home_score


def resolve_gap_match(
    match: GapMatch,
    candidates: Sequence[NowGoalCandidate],
    team_id_dict: Mapping[int, int],
    *,
    date_window_days: int = 2,
    name_threshold: float = 0.6,
) -> ResolutionResult:
    ng_league = FOTMOB_TO_NOWGOAL_LEAGUE.get(match.league_id)
    survivors: list[tuple[NowGoalCandidate, str, str, float]] = []

    for c in candidates:
        if ng_league is not None and c.nowgoal_league_id != ng_league:
            continue
        if within_date_window(match.date, c.kickoff_local, date_window_days) is not True:
            continue
        direction, kind, strength = _direction_evidence(c, match, team_id_dict, name_threshold)
        if direction is None:
            continue
        if not _score_matches(c, match, direction):
            continue
        survivors.append((c, direction, kind, strength))

    if not survivors:
        return ResolutionResult(match.match_id, STATUS_NO_CANDIDATE, detail="no_surviving_candidate")
    if len(survivors) > 1:
        return ResolutionResult(
            match.match_id, STATUS_AMBIGUOUS,
            detail=f"{len(survivors)}_candidates_survive_direction_and_score_evidence",
        )

    c, direction, kind, strength = survivors[0]
    if direction == INVERTED:
        status, detail = STATUS_NEEDS_REVIEW, "inverted_direction_zero_prior_baseline"
    elif kind == "id":
        status, detail = STATUS_AUTO_OK, "team_id_dictionary"
    elif kind == "name" and strength >= 0.999:
        status, detail = STATUS_AUTO_OK, "exact_name_match"
    else:
        status, detail = STATUS_NEEDS_REVIEW, f"name_similarity={strength:.3f}"

    return ResolutionResult(
        match_id=match.match_id, status=status, titan_id=c.titan_id,
        direction=direction, evidence_kind=kind, evidence_strength=strength, detail=detail,
        matched_titan_kickoff_local=c.kickoff_local,
    )


def resolve_batch(
    matches: Sequence[GapMatch],
    candidates_by_match_id: Mapping[int, Sequence[NowGoalCandidate]],
    team_id_dict: Mapping[int, int],
    *,
    date_window_days: int = 2,
    name_threshold: float = 0.6,
) -> list[ResolutionResult]:
    """批量解析 + titan_id 跨比赛冲突检测。单场解析(resolve_gap_match)天然看
    不到"这个 titan_id 是否也被另一场比赛选中"——冲突检测必须在整批结果出来
    之后做第二遍扫描,任何 titan_id 被 ≥2 场比赛同时命中,全部降级为
    titan_conflict、原样进 review,不猜哪一场对。"""
    results = [
        resolve_gap_match(
            m, candidates_by_match_id.get(m.match_id, ()), team_id_dict,
            date_window_days=date_window_days, name_threshold=name_threshold,
        )
        for m in matches
    ]

    owners: dict[str, list[int]] = defaultdict(list)
    for r in results:
        if r.titan_id is not None and r.status in (STATUS_AUTO_OK, STATUS_NEEDS_REVIEW):
            owners[r.titan_id].append(r.match_id)
    conflicted = {t for t, ms in owners.items() if len(ms) > 1}
    if not conflicted:
        return results

    out = []
    for r in results:
        if r.titan_id in conflicted:
            out.append(
                dataclasses.replace(
                    r, status=STATUS_TITAN_CONFLICT,
                    detail=f"titan_id_claimed_by_{len(owners[r.titan_id])}_matches",
                )
            )
        else:
            out.append(r)
    return out
