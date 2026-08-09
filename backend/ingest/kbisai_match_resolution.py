"""kbisai matchId ↔ FotMob Match_ID 身份解析(纯函数,不发网络请求、不碰数据库)。

与 backend/ingest/entity_resolution.py(nowgoal 专用,``PROVIDER = "nowgoal"``
模块常量、六道 auto_ok 门禁)是两个独立实现——本模块不复用、不修改后者。
kbisai 可用的识别信号完全不同:

- kbisai 的 kickoff 已经是来源自己声明的真实 UTC(``normalize_match_list`` 已
  归一),实测与 FotMob 的精确 kickoff 完全一致(diff=0,挪超样例
  matchId 4467576 ↔ FotMob Match_ID 5104970)。比赛身份主要靠 kickoff 精确
  匹配,不需要像 nowgoal 那样做模糊队名匹配来定位比赛;
- 队名(kbisai 返回简体中文)只用来做两件独立的事:
    (a) 同一时刻多场比赛时的消歧(FotMob 英超赛程常见:同一轮多场
        14:00Z 开球);
    (b) 独立确认主客方向没有被来源标反;
  这两件事都需要 ``dim_team_alias`` 里已经有该联赛球队的中文别名——目前只有
  英超 26/27(20/20 支球队)覆盖,挪超/瑞典超没有(0/16、0/16)。没有别名
  数据的联赛,一旦出现需要消歧的矛盾场景,一律 fail-closed,不猜。

CLAUDE.md §6.1:"自动映射必须校验开球时间、主客方向和球队实体;低置信度进入
人工审核,不得静默写成已验证"——本模块的 review_status 判定就是这条纪律的
直接实现:队名双向确认成功才给 'auto_ok',否则给 'needs_review'(仍然写入,
但不会被 `review_status IN ('auto_ok','confirmed')` 过滤的读路径拿到,等于
默认不被公开展示,直到人工确认)。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from backend.db.util import parse_strict_utc

# 与 entity_resolution.KICKOFF_TOLERANCE_SECONDS 用同一个值,两个 provider
# 的容差心智模型保持一致,但这是两个独立的常量(不是同一个符号)。
KICKOFF_TOLERANCE_SECONDS = 1800
# 单一候选、无别名数据可交叉核实时,只有开球时刻差在这个窗口内才敢自动采信——
# 比 entity_resolution 的 ±1800s 收紧很多,因为这里没有第二个信号兜底。
HIGH_CONFIDENCE_KICKOFF_SECONDS = 60

STATUS_MATCHED = "matched"
STATUS_NO_CANDIDATE = "no_candidate"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_KICKOFF_OUT_OF_TOLERANCE = "kickoff_out_of_tolerance"


@dataclass(frozen=True)
class FotMobMatchRef:
    """待映射的 FotMob 比赛(调用方从 dim_match 读出,必须已有精确 kickoff)。"""

    fotmob_match_id: int
    kickoff_at_utc: str
    home_team_id: int
    away_team_id: int


@dataclass(frozen=True)
class KbisaiCandidate:
    """一个 kbisai 候选比赛(normalize_match_list 输出的一行,调用方已经按
    competitionId 筛过——本模块不做联赛级过滤)。"""

    provider_match_id: str
    kickoff_at_utc: str
    home_team_name: str
    away_team_name: str


@dataclass(frozen=True)
class ResolutionResult:
    status: str
    reason: str
    fotmob_match_id: int
    provider_match_id: Optional[str] = None
    kickoff_diff_seconds: Optional[int] = None
    confidence: float = 0.0
    home_away_inverted: int = 0
    review_status: str = "needs_review"
    method: str = "auto"
    orientation_verified: bool = False

    @property
    def matched(self) -> bool:
        return self.status == STATUS_MATCHED


def _kickoff_diff_seconds(a: str, b: str) -> Optional[int]:
    dt_a = parse_strict_utc(a)
    dt_b = parse_strict_utc(b)
    if dt_a is None or dt_b is None:
        return None
    return int(abs((dt_a - dt_b).total_seconds()))


def _names_match(
    candidate: KbisaiCandidate,
    home_aliases: frozenset[str],
    away_aliases: frozenset[str],
) -> Optional[bool]:
    """返回 True=正常主客对应,False=主客颠倒,None=两个方向都对不上。"""
    direct = candidate.home_team_name in home_aliases and candidate.away_team_name in away_aliases
    inverted = candidate.home_team_name in away_aliases and candidate.away_team_name in home_aliases
    if direct and not inverted:
        return True
    if inverted and not direct:
        return False
    return None  # 都不匹配,或(理论上不该发生)两个方向同时匹配——一律视为无法判定


def resolve_kbisai_match(
    fotmob_match: FotMobMatchRef,
    candidates: list[KbisaiCandidate],
    *,
    fotmob_home_aliases: Optional[frozenset[str]] = None,
    fotmob_away_aliases: Optional[frozenset[str]] = None,
) -> ResolutionResult:
    """在(调用方已按 competitionId 筛过的)候选里,为一场 FotMob 比赛找 kbisai 映射。

    fotmob_home_aliases / fotmob_away_aliases:该 FotMob 球队在
    dim_team_alias 里的中文别名集合(调用方查库传入)。任一方为空/None 都
    视为"这个联赛没有别名数据,无法做队名验证"——不是"没查到就当没有别名"
    的静默降级,是调用方必须显式传入空集合或 None 来表达这一点。
    """
    has_alias_data = bool(fotmob_home_aliases) and bool(fotmob_away_aliases)

    scored: list[tuple[int, KbisaiCandidate]] = []
    for c in candidates:
        diff = _kickoff_diff_seconds(fotmob_match.kickoff_at_utc, c.kickoff_at_utc)
        if diff is not None and diff <= KICKOFF_TOLERANCE_SECONDS:
            scored.append((diff, c))
    scored.sort(key=lambda t: t[0])

    if not scored:
        return ResolutionResult(
            status=STATUS_NO_CANDIDATE,
            reason=f"no kbisai candidate within {KICKOFF_TOLERANCE_SECONDS}s of kickoff",
            fotmob_match_id=fotmob_match.fotmob_match_id,
        )

    if len(scored) > 1:
        if not has_alias_data:
            return ResolutionResult(
                status=STATUS_AMBIGUOUS,
                reason=(
                    f"{len(scored)} candidates within kickoff tolerance and no alias "
                    "data available to disambiguate"
                ),
                fotmob_match_id=fotmob_match.fotmob_match_id,
            )
        name_matches = [
            (diff, c, orientation)
            for diff, c in scored
            for orientation in [_names_match(c, fotmob_home_aliases, fotmob_away_aliases)]
            if orientation is not None
        ]
        if len(name_matches) != 1:
            return ResolutionResult(
                status=STATUS_AMBIGUOUS,
                reason=(
                    f"{len(name_matches)} of {len(scored)} same-window candidates matched "
                    "by team name (need exactly 1)"
                ),
                fotmob_match_id=fotmob_match.fotmob_match_id,
            )
        diff, best, direct = name_matches[0]
        return ResolutionResult(
            status=STATUS_MATCHED,
            reason="unique kickoff+team-name match among multiple same-window candidates",
            fotmob_match_id=fotmob_match.fotmob_match_id,
            provider_match_id=best.provider_match_id,
            kickoff_diff_seconds=diff,
            confidence=0.95,
            home_away_inverted=0 if direct else 1,
            review_status="auto_ok",
            orientation_verified=True,
        )

    # 恰好一个候选。
    best_diff, best = scored[0]
    if has_alias_data:
        orientation = _names_match(best, fotmob_home_aliases, fotmob_away_aliases)
        if orientation is None:
            return ResolutionResult(
                status=STATUS_AMBIGUOUS,
                reason=(
                    "kickoff matched a single candidate but team names disagree with "
                    "alias data in both directions"
                ),
                fotmob_match_id=fotmob_match.fotmob_match_id,
            )
        confidence = 0.95 if best_diff <= HIGH_CONFIDENCE_KICKOFF_SECONDS else 0.85
        return ResolutionResult(
            status=STATUS_MATCHED,
            reason="kickoff matched and team-name orientation confirmed",
            fotmob_match_id=fotmob_match.fotmob_match_id,
            provider_match_id=best.provider_match_id,
            kickoff_diff_seconds=best_diff,
            confidence=confidence,
            home_away_inverted=0 if orientation else 1,
            review_status="auto_ok",
            orientation_verified=True,
        )

    if best_diff > HIGH_CONFIDENCE_KICKOFF_SECONDS:
        return ResolutionResult(
            status=STATUS_KICKOFF_OUT_OF_TOLERANCE,
            reason=(
                f"single candidate is {best_diff}s away — above the "
                f"{HIGH_CONFIDENCE_KICKOFF_SECONDS}s high-confidence window and no alias "
                "data to corroborate identity"
            ),
            fotmob_match_id=fotmob_match.fotmob_match_id,
        )

    return ResolutionResult(
        status=STATUS_MATCHED,
        reason=(
            "unique kickoff match within high-confidence window; no alias data available "
            "to independently verify home/away orientation"
        ),
        fotmob_match_id=fotmob_match.fotmob_match_id,
        provider_match_id=best.provider_match_id,
        kickoff_diff_seconds=best_diff,
        confidence=0.7,
        home_away_inverted=0,
        review_status="needs_review",
        orientation_verified=False,
    )


def write_kbisai_xref(conn, result: ResolutionResult, *, now_iso: str) -> str:
    """把一个 matched 的 ResolutionResult 写进 dim_match_xref(provider='kbisai')。

    幂等:UNIQUE (provider, provider_match_id) 与 (provider, fotmob_match_id) 任一
    冲突时,若已有行的关键字段(provider_match_id/home_away_inverted/
    review_status)与本次结果完全一致 → 视为重复调用,返回 'unchanged'(不 UPDATE,
    这张表不是 append-only 但也不做静默覆盖——同 CLAUDE.md §6.1 "既有 verified/
    manual 行绝不覆盖" 的精神,自动流程不该在冲突时自说自话地改写)。
    若已有行与本次结果不一致 → 是需要人工介入的真实冲突,原样抛出
    sqlite3.IntegrityError,不静默吞掉。

    调用方必须先确认 result.matched 为真;对未匹配结果调用是编程错误。
    """
    if not result.matched:
        raise ValueError(f"cannot write xref for an unresolved match: status={result.status}")
    try:
        conn.execute(
            """INSERT INTO dim_match_xref
               (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                confidence, verified, method, kickoff_diff_seconds, review_status,
                created_at, updated_at)
               VALUES (?, 'kbisai', ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (
                result.fotmob_match_id, result.provider_match_id, result.home_away_inverted,
                result.confidence, result.method, result.kickoff_diff_seconds,
                result.review_status, now_iso, now_iso,
            ),
        )
        return "inserted"
    except sqlite3.IntegrityError:
        existing = conn.execute(
            """SELECT provider_match_id, home_away_inverted, review_status
               FROM dim_match_xref WHERE provider='kbisai' AND fotmob_match_id=?""",
            (result.fotmob_match_id,),
        ).fetchone()
        if (
            existing is not None
            and existing["provider_match_id"] == result.provider_match_id
            and existing["home_away_inverted"] == result.home_away_inverted
            and existing["review_status"] == result.review_status
        ):
            return "unchanged"
        raise
