"""联赛积分榜(fact_league_table)迟到刷新的到期判断(纯函数)。

背景(站长已诊断,不在此重新推导):fact_league_table 只由
backend/ingest/ingest_league.py::ingest_season_tables() 写入,而该函数从未
被任何 worker 任务调度过——现有 7 个 timer 里没有一个会碰它。旧的手动 CLI
路径(_season_tables_done())只判断"这个 (League_ID, Season) 有没有过任何一
行",一旦赛季初始 skeleton(played=0)落库就永久短路跳过,导致积分榜停在
赛季初始状态,即使联赛已经踢完多轮(2026-08 英超 2026/2027 真实事故)。

站长明确决定的触发条件(与三次固定检查点的 physical_stats_poll 不同构,
不要混为一谈):"只要该联赛+赛季有比赛结束,过 6 小时,积分榜就要刷新一次"
——这是一个**基于最近一场完赛比赛开球时间的时间戳比较**,不是逐场重试、
也没有"有效/无效"数据判断(ingest_season_tables 是整体 pull-and-replace,
请求成功即视为结构上有效)。语义:

    due_at = 最近一场完赛比赛的 kickoff_at_utc + 6 小时
    due    = now >= due_at 且 (从未刷新过 或 上次刷新时间早于 due_at)

"上次刷新时间早于 due_at"这个条件是关键:它保证了"每有一场新比赛完赛,
due 的判定点就随之前移"——不是刷新一次就永久满足,而是要刷新到
"覆盖了最新一场完赛比赛"才算数。

2026-08-26 扩到全联赛(此前刻意收窄成只有英超一家,站长实测西甲/意甲/法甲等
12 个在营联赛积分榜同样冻结或滞后后明确要求扩容)。范围**不再是本模块维护
的另一份白名单**,而是直接派生自 backend/queries/leagues.py::LEAGUE_META
(仓库里"新增联赛只改这一处字典"的单一真源)——调用方(poll_standings.py)
遍历 LEAGUE_META,不在这里重复维护第二份联赛清单。没有任何完赛比赛的联赛
(如当前 dim_match 零行的欧冠/欧联/欧协)天然对这个判断是 inert,不需要
特殊处理。

每轮上限(MAX_REFRESHES_PER_RUN):12 个联赛可能在首轮同时到期,每个到期都要
向 FotMob 拉一次整季数据(HTTP + DELETE/INSERT),站长要求每轮最多真正刷新
5 个,其余顺延到下一轮(30 分钟一轮,约 1.5 小时排空一次性积压)。这个上限
只约束"真正执行刷新"这个昂贵操作,不约束"检查是否到期"这个廉价的纯 DB 读
——调用方按 due_at 最旧优先选出前 5 个执行,其余记 deferred 顺延,不静默
丢弃(CLAUDE.md 禁止静默截断)。

本模块只做这一个纯判断,不做任何 I/O、不碰数据库连接、不发网络请求——
落库/请求都在调用方(backend/cli/poll_standings.py)。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# 最近一场完赛比赛开球后多久应刷新一次积分榜(站长明确决定,不做成可配置项)。
REFRESH_DELAY_HOURS = 6.0

# 每轮最多真正刷新多少个联赛(站长明确决定,不做成可配置项)。只计真正执行
# HTTP+DELETE/INSERT 的联赛,不计"检查了但未到期/无比赛"的——见上方模块
# 说明。被这个上限挤掉的到期联赛在 poll_standings.py 里记 action="deferred",
# 顺延到下一轮,按 due_at 最旧优先,不会被持续饿死。
MAX_REFRESHES_PER_RUN = 5


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


@dataclass(frozen=True)
class RefreshDecision:
    due: bool
    due_at: str | None  # 计算出的到期时刻(ISO UTC),便于调用方记录/调试
    reason: str


_NOT_DUE_NO_FINISHED_MATCH = RefreshDecision(False, None, "no_finished_match")


def due_refresh(
    latest_finished_kickoff_at_utc: str | None,
    last_refreshed_at: str | None,
    now_iso: str,
) -> RefreshDecision:
    """判定某个 (League_ID, Season) 现在是否该刷新一次积分榜。

    - `latest_finished_kickoff_at_utc`:该联赛+赛季当前已知"已完赛"
      (status='Finish')比赛里最新一场的精确开球时间;没有任何完赛比赛时
      恒不 due(不存在需要刷新的理由);
    - `last_refreshed_at`:上一次真正成功调用 ingest_season_tables() 的
      时间;从未刷新过传 None;
    - 判定不依赖 `now_iso` 与开球时间的具体差值大小,只看是否跨过了
      `latest_finished_kickoff_at_utc + REFRESH_DELAY_HOURS` 这个时刻,
      且上次刷新是否已经覆盖了这个时刻。
    """
    if not latest_finished_kickoff_at_utc:
        return _NOT_DUE_NO_FINISHED_MATCH

    kickoff = _parse_iso(latest_finished_kickoff_at_utc)
    due_at = kickoff + timedelta(hours=REFRESH_DELAY_HOURS)
    due_at_iso = due_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    now = _parse_iso(now_iso)

    if now < due_at:
        return RefreshDecision(False, due_at_iso, "not_yet_due")

    if last_refreshed_at:
        last_refresh = _parse_iso(last_refreshed_at)
        if last_refresh >= due_at:
            return RefreshDecision(False, due_at_iso, "already_refreshed_since_due")

    return RefreshDecision(True, due_at_iso, "due")
