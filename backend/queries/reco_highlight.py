"""首页战绩 banner 的**择优**口径选择器(2026-09,经站长明确决定)。

⚠️ 本模块**故意**从数十个统计口径候选里挑一个最好看的展示。**这不是 bug,
不要"修复"成全样本。** 站长在被明确告知这与 CLAUDE.md §2.2/§8.1 冲突之后
重申坚持该方案,并同步删除了 CLAUDE.md 里对应的三行条款(:17/:37/:479)。
要改回全样本展示是产品决定,不是代码层能单方翻案的。

与记录面的分工(不要搞混):
- **记录面**(`/reco?tab=record`、`GET /api/v1/reco/track-record`)仍然是全样本、
  命中未中走水作废全展示。本模块**不碰**它们,也不改 `track_record_summary` /
  `public_overview` 的任何数字。
- **本模块**只服务首页 banner 这个引流位。

设计约定(站长选择不补文档条款,所以下面这条是本功能的设计决定而非硬性规则):
每个候选都必须同时携带原始计数(`win_count`/`decided_count` 等),让前端
"只渲染百分比、不渲染计数"在类型上就很别扭。理由是站长自己提的"实事求是"。

纯函数纪律:本模块所有判定函数**接收 now、不读时钟、不查库**,与
`backend/ingest/physical_stats_poll.py::within_candidate_window` 同一写法,
边界值因此可精确测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Mapping, Sequence

from .reco import hit_rate_from_counts

# ── 可调常量(站长可改;改动会直接改变首页展示的口径)──────────────
MIN_STREAK = 3                    # 低于此长度不作为连中候选(2 连是巧合)
HIT_RATE_THRESHOLD = 0.70         # 站长指定的"好看"线
DAY_WINDOWS = (30, 10, 5, 3)      # 天窗口,由宽到窄
COUNT_WINDOWS = (20, 10, 5)       # 场次窗口,由宽到窄
BOARDS = ("daily_pick", "daily_public")
BOARD_LABEL_ZH = {"daily_pick": "每日精选", "daily_public": "每日公推"}

# 连中中"算命中"的结果值。half_win **不在其中**——「近 N 单全中」这句话里的
# "全中"不能由半赢撑起来(slip 级 half_win 目前不可达,但 CHECK 允许,防御式处理)。
_STREAK_HIT = ("win",)
_STREAK_SKIP = ("push",)          # 跳过:不计长度也不断连(与"push 不计分母"一致)


@dataclass(frozen=True)
class SlipFact:
    """一张已结算/已作废的单,只保留判定需要的字段。"""

    id: str
    board: str
    combo_type: str               # single / parlay
    slip_date: str                # 北京自然日 YYYY-MM-DD
    published_at: str | None
    status: str                   # settled / voided
    result: str | None
    return_units: float | None
    market: str | None            # 仅单关有意义(单关 腿==单)
    league_id: int | None         # 仅单关有意义


@dataclass(frozen=True)
class Candidate:
    kind: Literal["rate", "parlay_return"]
    board: str
    window_kind: Literal["days", "count"]
    window_value: int
    segment_kind: Literal["overall", "market", "league", "league_market"]
    market: str | None
    league_id: int | None
    win: int
    lose: int
    half_win: int
    half_loss: int
    push: int
    decided: int
    hit_rate: float | None
    slip_count: int
    net_units: float | None       # 仅 parlay_return 用
    observed_from: str
    observed_to: str

    @property
    def key(self) -> str:
        """可复现的候选标识:截图质疑时能精确回溯当时选了哪个口径。"""
        seg = self.segment_kind
        if self.market:
            seg += f":{self.market}"
        if self.league_id is not None:
            seg += f":L{self.league_id}"
        return f"{self.board}|{self.window_kind}{self.window_value}|{seg}|{self.kind}"


def _parse_day(d: str) -> datetime | None:
    try:
        return datetime.strptime(d, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def ordered_slips(slips: Sequence[SlipFact], board: str) -> list[SlipFact]:
    """按 `published_at ASC, id ASC` 升序——**不用 settled_at,也不用 slip_date**。

    这是本模块最关键的单点决策:`settled_at` 会被重结算覆盖
    (`backend/commands/reco.py::settle_slip` 的 UPDATE 在 resettle 路径同样执行),
    若沿用战绩页那套 `slip_date DESC, settled_at DESC`,站长纠正一次 8 月的旧
    结算就会**静默改变首页那个"近 N 单全中"**。`published_at` 写一次永不改
    (`publish_slip` 的 UPDATE 带 `AND status='draft'` 谓词,settle/void/edit
    三条路径的 SQL 都不含该列),语义也正确:连中是"连续几次公开承诺都对了",
    发布时刻正是承诺时刻。

    `id` 做最终兜底,保证全序确定、可复现;`published_at` 为空时用 "" 参与排序
    (防御路径,生产实测零空值),不报错。
    """
    return sorted(
        (s for s in slips if s.board == board),
        key=lambda s: (s.published_at or "", s.id),
    )


@dataclass(frozen=True)
class Streak:
    length: int
    skipped_push: int
    skipped_void: int
    from_date: str
    to_date: str


def current_streak(slips: Sequence[SlipFact], board: str) -> Streak | None:
    """**当前**连中(从最新一张已结算单往回数),不是历史最佳连中。

    最新一单是 lose/half_loss/half_win → 连中 = 0(返回 None)。挑一段 8 月的
    历史连胜写成"近 5 单全中"是事实性谎言,不在"选择口径"的范围内。

    push 跳过(不计长度也不断连)、voided 跳过,两者都计数并要求调用方披露。
    """
    ordered = ordered_slips(slips, board)
    length = 0
    skipped_push = 0
    skipped_void = 0
    dates: list[str] = []
    # 尾随的 push/voided(还没遇到下一个命中就先记着)不算"连中期间跳过的"——
    # 它们在连中之外。只有当后面又数到一个命中时,才把它们计入披露数字。
    # 否则会把"5 连中之前恰好有一个走水"说成"连中其间有 1 单走水",描述失真。
    pending_push = 0
    pending_void = 0
    for s in reversed(ordered):                 # 从最新往回
        if s.status == "voided":
            pending_void += 1
            continue
        if s.status != "settled" or s.result is None:
            continue
        if s.result in _STREAK_SKIP:
            pending_push += 1
            continue
        if s.result in _STREAK_HIT:
            if length > 0:                      # 夹在两个命中之间才算"其间"
                skipped_push += pending_push
                skipped_void += pending_void
            pending_push = pending_void = 0
            length += 1
            dates.append(s.slip_date)
            continue
        break                                   # lose / half_loss / half_win 断连
    if length == 0:
        return None
    return Streak(
        length=length,
        skipped_push=skipped_push,
        skipped_void=skipped_void,
        from_date=min(dates),
        to_date=max(dates),
    )


def _window_slips(
    slips: Sequence[SlipFact], board: str, kind: str, value: int, now: datetime
) -> list[SlipFact]:
    """取窗口内的**已结算**单(voided 不进任何统计)。"""
    settled = [
        s for s in ordered_slips(slips, board)
        if s.status == "settled" and s.result is not None
    ]
    if kind == "days":
        cutoff = (now - timedelta(days=value)).strftime("%Y-%m-%d")
        return [s for s in settled if s.slip_date >= cutoff]
    # 场次窗口:取最近 value 单。样本不足 value 时**不生成候选**——
    # 用 6 单冒充"最近 10 单"是虚假标签。
    if len(settled) < value:
        return []
    return settled[-value:]


def _tally(rows: Sequence[SlipFact]) -> tuple[int, int, int, int, int]:
    win = sum(1 for r in rows if r.result == "win")
    lose = sum(1 for r in rows if r.result == "lose")
    half_win = sum(1 for r in rows if r.result == "half_win")
    half_loss = sum(1 for r in rows if r.result == "half_loss")
    push = sum(1 for r in rows if r.result == "push")
    return win, lose, half_win, half_loss, push


def _make_rate_candidate(
    rows: Sequence[SlipFact], *, board: str, window_kind: str, window_value: int,
    segment_kind: str, market: str | None, league_id: int | None,
) -> Candidate | None:
    if not rows:
        return None
    win, lose, half_win, half_loss, push = _tally(rows)
    decided = win + lose + half_win + half_loss
    if decided == 0:
        return None                             # 全走水:不是 0% 也不是 100%
    dates = [r.slip_date for r in rows]
    return Candidate(
        kind="rate", board=board,
        window_kind=window_kind, window_value=window_value,
        segment_kind=segment_kind, market=market, league_id=league_id,
        win=win, lose=lose, half_win=half_win, half_loss=half_loss, push=push,
        decided=decided,
        hit_rate=hit_rate_from_counts(win, lose, half_win, half_loss),
        slip_count=len(rows), net_units=None,
        observed_from=min(dates), observed_to=max(dates),
    )


def build_candidates(
    slips: Sequence[SlipFact], board: str, now: datetime,
    *, known_league_ids: Mapping[int, str] | None = None,
) -> list[Candidate]:
    """枚举该板块的全部候选。命中率类候选**只用单关**(站长决策:串子是串子,
    串子和单关分开算);串关另出一条"回报"候选,不算命中率。

    单关的腿==单,所以 market/league 归属唯一,不存在串关混 ah+ou 的歧义。
    """
    out: list[Candidate] = []
    windows = [("days", v) for v in DAY_WINDOWS] + [("count", v) for v in COUNT_WINDOWS]
    for wkind, wval in windows:
        rows = _window_slips(slips, board, wkind, wval, now)
        singles = [r for r in rows if r.combo_type == "single"]
        parlays = [r for r in rows if r.combo_type == "parlay"]

        c = _make_rate_candidate(
            singles, board=board, window_kind=wkind, window_value=wval,
            segment_kind="overall", market=None, league_id=None)
        if c:
            out.append(c)

        markets = {r.market for r in singles if r.market}
        for m in sorted(markets):
            c = _make_rate_candidate(
                [r for r in singles if r.market == m], board=board,
                window_kind=wkind, window_value=wval,
                segment_kind="market", market=m, league_id=None)
            if c:
                out.append(c)

        # 联赛维度:没有中文名的联赛整体丢弃——不能把内部 league_id 露给用户,
        # 也不做"其它联赛"聚合桶(那等于把多个不可比联赛混成一个好看数字)。
        leagues = {
            r.league_id for r in singles
            if r.league_id is not None
            and (known_league_ids is None or r.league_id in known_league_ids)
        }
        for lg in sorted(leagues):
            in_league = [r for r in singles if r.league_id == lg]
            c = _make_rate_candidate(
                in_league, board=board, window_kind=wkind, window_value=wval,
                segment_kind="league", market=None, league_id=lg)
            if c:
                out.append(c)
            for m in sorted({r.market for r in in_league if r.market}):
                c = _make_rate_candidate(
                    [r for r in in_league if r.market == m], board=board,
                    window_kind=wkind, window_value=wval,
                    segment_kind="league_market", market=m, league_id=lg)
                if c:
                    out.append(c)

        if parlays:
            net = sum((r.return_units or 0.0) for r in parlays) - len(parlays)
            pdates = [r.slip_date for r in parlays]
            out.append(Candidate(
                kind="parlay_return", board=board,
                window_kind=wkind, window_value=wval,
                segment_kind="overall", market=None, league_id=None,
                win=0, lose=0, half_win=0, half_loss=0, push=0, decided=0,
                hit_rate=None, slip_count=len(parlays), net_units=round(net, 4),
                observed_from=min(pdates), observed_to=max(pdates),
            ))
    return out


def _window_rank(c: Candidate) -> tuple[int, int]:
    # 天窗口优先于场次窗口:日历区间读者能自己去战绩页核对,"最近 N 单"核对不了。
    return (1 if c.window_kind == "days" else 0, c.window_value)


_SEGMENT_RANK = {"overall": 3, "market": 2, "league": 1, "league_market": 0}


def _rank_key(c: Candidate) -> tuple:
    """全序比较器。首位是命中率(站长口径:"最好看"=命中率),其余仅用于破平。

    破平方向刻意偏向**更少自由度、更难被挑选**的候选——按定义,平局时两者
    一样好看,那就选更经得起核对的那个。
    """
    return (
        c.hit_rate if c.hit_rate is not None else -1.0,
        c.decided,
        _window_rank(c),
        _SEGMENT_RANK.get(c.segment_kind, 0),
        c.key,                                  # 最终兜底,保证完全确定性
    )


@dataclass(frozen=True)
class Highlight:
    board: str
    kind: Literal["streak", "rate_qualified", "rate_best_effort", "parlay_return", "empty"]
    streak: Streak | None
    candidate: Candidate | None
    candidates_considered: int


def select_for_board(
    slips: Sequence[SlipFact], board: str, now: datetime,
    *, known_league_ids: Mapping[int, str] | None = None,
    min_streak: int = MIN_STREAK, threshold: float = HIT_RATE_THRESHOLD,
) -> Highlight:
    """单个板块的择优。阶梯:连中 → 命中率(阈值只决定 kind,不决定选谁)
    → argmax 兜底。阈值达标与否共用同一个 argmax,避免两套破平逻辑。"""
    streak = current_streak(slips, board)
    if streak is not None and streak.length >= min_streak:
        return Highlight(board, "streak", streak, None, 0)

    cands = build_candidates(slips, board, now, known_league_ids=known_league_ids)
    rate_cands = [c for c in cands if c.kind == "rate"]
    if not rate_cands:
        # 没有命中率候选时,串关回报仍可作为兜底展示
        parlay = [c for c in cands if c.kind == "parlay_return"]
        if parlay:
            best_p = max(parlay, key=lambda c: (c.net_units or 0.0, c.slip_count, c.key))
            return Highlight(board, "parlay_return", None, best_p, len(cands))
        return Highlight(board, "empty", None, None, 0)

    best = max(rate_cands, key=_rank_key)
    kind = (
        "rate_qualified"
        if best.hit_rate is not None and best.hit_rate >= threshold - 1e-9
        else "rate_best_effort"
    )
    return Highlight(board, kind, None, best, len(cands))


def _leagues_for_match_ids(conn_core, match_ids: set[int]) -> dict[int, int | None]:
    """批量取 dim_match.League_ID——严格镜像 reco.py::_kickoffs_for_match_ids
    的范式(整批取一列 + Python 层按 match_id 拼装;跨库不 ATTACH;
    conn_core 缺表时返回空 dict,调用方等价于"全部联赛未知",不报错)。

    不复用 queries/matches.py::list_matches:那个函数 limit 默认 50 会静默丢行、
    league_ids 是硬过滤,理由与 _kickoffs_for_match_ids 的既有注释完全相同。
    """
    import sqlite3 as _sqlite3

    if not match_ids:
        return {}
    placeholders = ",".join("?" for _ in match_ids)
    try:
        rows = conn_core.execute(
            f"SELECT Match_ID, League_ID FROM dim_match WHERE Match_ID IN ({placeholders})",
            tuple(match_ids),
        ).fetchall()
    except _sqlite3.OperationalError:
        return {}
    return {int(r["Match_ID"]): r["League_ID"] for r in rows}


def record_highlight_samples(conn, conn_core) -> list[SlipFact]:
    """取两个板块全部 settled/voided 单的最小事实集。**不在 SQL 里做任何择优**
    ——择优是纯函数的事(便于测试与复现)。

    market/league 只对单关有意义(单关 腿==单,归属唯一);串关不带这两个字段,
    因为"按单归属市场"在串关上无定义(站长决策:串子和单关分开算)。
    """
    slip_rows = conn.execute(
        "SELECT id, board, combo_type, slip_date, published_at, status, result,"
        " return_units FROM reco_slips"
        " WHERE status IN ('settled','voided')"
        " ORDER BY slip_date DESC, published_at DESC"
        " LIMIT 2000"                       # 载荷安全带,不是业务规则
    ).fetchall()
    if not slip_rows:
        return []

    single_ids = [r["id"] for r in slip_rows if r["combo_type"] == "single"]
    leg_by_slip: dict[str, tuple[str | None, int | None]] = {}
    if single_ids:
        placeholders = ",".join("?" for _ in single_ids)
        leg_rows = conn.execute(
            f"SELECT slip_id, match_id, TRIM(LOWER(market)) AS market"
            f"  FROM reco_legs WHERE slip_id IN ({placeholders})",
            single_ids,
        ).fetchall()
        leagues = _leagues_for_match_ids(
            conn_core, {r["match_id"] for r in leg_rows if r["match_id"] is not None}
        )
        for r in leg_rows:
            mid = r["match_id"]
            leg_by_slip[r["slip_id"]] = (
                r["market"], leagues.get(mid) if mid is not None else None
            )

    out: list[SlipFact] = []
    for r in slip_rows:
        market, league_id = leg_by_slip.get(r["id"], (None, None))
        out.append(SlipFact(
            id=r["id"], board=r["board"], combo_type=r["combo_type"],
            slip_date=r["slip_date"], published_at=r["published_at"],
            status=r["status"], result=r["result"],
            return_units=r["return_units"],
            market=market, league_id=league_id,
        ))
    return out


def select_highlights(
    slips: Sequence[SlipFact], now: datetime,
    *, known_league_ids: Mapping[int, str] | None = None,
) -> list[Highlight]:
    """两个板块**分别**出一条(站长决策:精选准就挂精选,公推准就挂公推),
    不跨板块比较——那是两个不可比的总体。零样本板块返回 kind='empty',
    由展示层决定是否渲染。"""
    return [
        select_for_board(slips, b, now, known_league_ids=known_league_ids)
        for b in BOARDS
    ]
