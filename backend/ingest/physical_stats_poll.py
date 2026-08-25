"""体能统计(physical_metrics_*)迟到补采的到期判断(纯函数)。

背景(站长已诊断,不在此重新推导):FotMob 部分体能统计
(`physical_metrics_distance_covered`/`_walking`/`_running`/`_sprinting`/
`_number_of_sprints`,落在 `fact_team_match_stats.extra_json` 里——这几个
key 不在 TEAM_STATS_CORE_COLUMNS 真列表里,见 backend/schema.py 的注释)
是异步计算的,经常在比赛 `status` 已经翻成 `Finish` 之后才真正出现最终值;
现有管道(`fotmob_incremental_multi` → `ingest_match()`)只在比赛刚解决的
那一刻抓一次,之后永不回访,于是这几个字段要么缺失,要么卡在早期的小
partial 值(4000-9500 米量级)而不是真实的队伍总值(约 95000-125000 米)。

本模块只负责"这场比赛现在是否到了该回查一次的时间点,是第几次检查"这一个
纯判断,不做任何 I/O、不碰数据库连接、不发网络请求——落库/请求/告警都在
调用方(backend/cli/poll_physical_stats.py)。

设计与 backend/ingest/postmatch_retry.py 同构但不同源:postmatch_retry 是
"同一个 stale 判据每 tick 都可能再次成立"的连续重试计数;这里是三个固定的
kickoff+N 小时检查点(6h/12h/24h),命中即查一次,查完这一枪不管有没有拿到
有效值都要往前推进 checks_done,第三次仍未拿到有效值才转入 exhausted——
与 poll_windows.py 的"持续分级节奏"也不同构(那里是永不停止的周期性轮询,
这里是有限次、达到即停的检查点),所以单独成一个文件而不是塞进
poll_windows.py 或 postmatch_retry.py 任何一个,避免把两种不同的语义硬拗
成同一套参数。

联赛范围刻意收窄、人工维护,不做自动探测(不含欧冠 42——虽然是热门联赛,
但当前 dim_match 里一行都没有,加入候选池也不会有真实效果,徒增无意义的
判断分支;需要新增联赛时手动往这个 frozenset 里加,不做"自动发现有比赛的
联赛就加进来"这种会不受控扩大抓取面的逻辑)。
"""

from dataclasses import dataclass
from datetime import datetime, timezone

# 站长指定,人工维护、刻意收窄——只有英超(League_ID=47)。新增联赛需要
# 站长明确批准后手动加入,不做自动探测。
PHYSICAL_STATS_LEAGUE_IDS: frozenset[int] = frozenset({47})

# 三个检查点:kickoff + 6h / +12h / +24h,固定三次,不多不少。
CHECKPOINT_HOURS: tuple[float, ...] = (6.0, 12.0, 24.0)
MAX_CHECKS = len(CHECKPOINT_HOURS)

# 候选池的开球时间上限(小时):超过这个窗口的比赛永远不再进入候选池,不管
# 有没有状态行。2026-08-25 真实生产事故:_candidate_rows() 最初只用状态行
# 是否存在筛候选(LEFT JOIN 两侧皆 NULL 即入选),而 due_checkpoint() 只判断
# "够没够 N 小时"这个下限、没有上限——首次上线时数据库里所有历史 Finish
# 英超比赛(2020 年至今)都满足"没有状态行",于是把六年前的比赛也当成刚
# 完赛的在处理,已经对 2020 赛季批量触发 ingest_match() 重抓,人工发现后
# 立即停服务、禁用定时器,处理了 42 场后止损(ingest_match 列作用域幂等
# 写入,未造成数据损坏,纯粹浪费代理请求)。24h(最后一个检查点)+24h
# 缓冲 = 48h:检查点 3 之后再晚到的 tick 不该把一场已经错过完整检查窗口的
# 比赛当成"刚发现的新比赛"从头查起。
CANDIDATE_WINDOW_HOURS = 48.0

# "有效/终值"判定阈值(米)。真实队伍总跑动距离约 95000-125000 米;早期
# partial 值只有 4000-9500 米量级,50000 是站长指定的、明显区分两者的分界。
VALID_DISTANCE_THRESHOLD_M = 50000.0


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass(frozen=True)
class CheckpointDecision:
    due: bool
    checkpoint: int | None   # 1/2/3,命中(或即将命中)的检查点序号;终态时为 None
    reason: str


_NOT_DUE_NO_KICKOFF = CheckpointDecision(False, None, "no_kickoff_at_utc")
_NOT_DUE_RESOLVED = CheckpointDecision(False, None, "already_resolved")
_NOT_DUE_EXHAUSTED = CheckpointDecision(False, None, "already_exhausted")


def within_candidate_window(kickoff_at_utc: str | None, now_iso: str) -> bool:
    """开球时间是否还落在候选窗口(CANDIDATE_WINDOW_HOURS)内——超窗的比赛
    永远不应该进入检查点判断,不管它有没有状态行、checks_done 是多少。
    这是"候选池"这一层的判断,`_candidate_rows()` 的 SQL 应该做等价的时间
    过滤(SQL 层先挡一道,避免把六年的历史比赛都拉进 Python 层再逐条丢弃);
    这里额外在 `due_checkpoint()` 内部也生效一次,双保险,防止未来任何新
    调用方绕过 SQL 过滤直接调纯函数。"""
    if not kickoff_at_utc:
        return False
    kickoff = _parse_iso(kickoff_at_utc)
    now = _parse_iso(now_iso)
    elapsed_hours = (now - kickoff).total_seconds() / 3600.0
    return 0 <= elapsed_hours <= CANDIDATE_WINDOW_HOURS


_NOT_DUE_TOO_OLD = CheckpointDecision(False, None, "kickoff_outside_candidate_window")


def due_checkpoint(
    kickoff_at_utc: str | None,
    checks_done: int,
    resolved: bool,
    exhausted: bool,
    now_iso: str,
) -> CheckpointDecision:
    """判定这场比赛现在是否到了该做体能统计回查的时间点。

    - `resolved`(已经拿到过有效值)或 `exhausted`(三次检查点都用完仍未拿到
      有效值,已经告警过)→ 永久不再 due,调用方应把这场比赛从候选池里剔除;
    - `checks_done` 是这场比赛已经真正执行过的检查次数(0..3);
    - 三个检查点严格按顺序推进,不允许跳过——`checks_done` 对应下一个要看
      的检查点下标(0 → 看 6h 点,1 → 看 12h 点,2 → 看 24h 点);
    - 到达 `checks_done >= MAX_CHECKS` 但既未 resolved 也未 exhausted 是一个
      过渡态(调用方应当在完成第 3 次检查后立即落 exhausted_at,不应该让
      这个状态在两次 tick 之间持续存在);本函数对这个过渡态本身也返回
      not due,防止在极端情况下(如告警失败但状态提交成功)被重复判定为
      "还要再查一次"。
    """
    if resolved:
        return _NOT_DUE_RESOLVED
    if exhausted:
        return _NOT_DUE_EXHAUSTED
    if not kickoff_at_utc:
        return _NOT_DUE_NO_KICKOFF
    if checks_done >= MAX_CHECKS:
        return CheckpointDecision(False, None, "checks_exhausted_pending_finalize")
    if not within_candidate_window(kickoff_at_utc, now_iso):
        return _NOT_DUE_TOO_OLD

    checkpoint = checks_done + 1  # 1-based,供人读 & 落库
    required_hours = CHECKPOINT_HOURS[checks_done]
    kickoff = _parse_iso(kickoff_at_utc)
    now = _parse_iso(now_iso)
    elapsed_hours = (now - kickoff).total_seconds() / 3600.0

    if elapsed_hours < required_hours:
        return CheckpointDecision(False, checkpoint, f"not_yet_due_checkpoint_{checkpoint}")
    return CheckpointDecision(True, checkpoint, f"due_checkpoint_{checkpoint}")


def is_valid_distance(home_distance: float | None, away_distance: float | None) -> bool:
    """"这场比赛的体能统计已经是终值"的定义:主客两队的
    physical_metrics_distance_covered 都必须各自达到阈值——只有一队达标
    不能代表整场数据已经收敛(可能只是其中一队恰好先算完)。缺失(None)
    一律视为无效,不当 0 处理(CLAUDE.md §11.3 的"缺失值不得静默填 0"同一
    纪律,虽然这里不是图表,但精神一致:不能把"没有数据"和"数据是 0"混为
    一谈)。
    """
    if home_distance is None or away_distance is None:
        return False
    return home_distance >= VALID_DISTANCE_THRESHOLD_M and away_distance >= VALID_DISTANCE_THRESHOLD_M
