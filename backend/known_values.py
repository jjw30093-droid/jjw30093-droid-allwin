"""封闭词表登记(2026-08-25,向 FotMob 学习的数据层收口之一)。

背景:仓库里 enum 型 TEXT 列的"已知取值"此前只活在注释和前端 *_ZH 翻译表里,
从不校验——生产实测的后果:`fact_match_events.event_type` 实际 9 种而
schema.py 注释写 5 种(VAR/MissedPenalty/Comment/PenaltyShootout 共 3,290 行
悄悄到达无人知晓);`fact_league_table.table_type` 实际 21 种而文档写 5 种
(分组制联赛写成 `all:K-League 1 Final Group A`,任何 `table_type='all'` 的
查询静默丢掉整个分组联赛);同一个加时段枚举在两张表里两种拼写。

FotMob 的对应纪律(APK 反编译实证):每个从 wire 解析的枚举都带 `Unknown`
兜底成员——新值**不崩、但被看见**。我们的 DTO 层已经有"不崩"的一半
(schemas.py:851 明确用 str 不用 Literal);这里补"被看见"的另一半:
质量门 G13(pipeline_gates)对照本登记表扫描,登记外的值出现即告警。

⚠️ 本表是"已知值"登记,不是白名单强制——出现新值只告警不拒写(拒写会把
来源新增枚举变成采集事故,方向反了)。登记的口径 = 生产实测出现过的值 ∪
FotMob APK 枚举里已知存在的值。与 backend/fotmob_client.py 的 STATUS_*
写侧常量的一致性由 tests/backend/test_known_values.py 交叉钉住(不做运行期
import,避免质量门背上 curl_cffi 依赖)。
"""

from __future__ import annotations

# dim_match.status —— 写侧唯一出口是 fotmob_client.derive_match_status
# (封闭四值);测试与 fotmob_client.STATUS_* 常量交叉校验。
DIM_MATCH_STATUS = frozenset({"Finish", "NotStarted", "InPlay", "Cancelled"})

# dim_match.kickoff_precision(migrations/core/0002 的三值约定)
KICKOFF_PRECISION = frozenset({"exact", "date_only", "unknown"})

# fact_shotmap(生产 364,017 行实测 + APK 枚举补全)
SHOT_OUTCOME = frozenset({"Goal", "AttemptSaved", "Miss", "Post"})
SHOT_SITUATION = frozenset({
    "RegularPlay", "FromCorner", "SetPiece", "FastBreak", "FreeKick",
    "ThrowInSetPiece", "Penalty", "IndividualPlay",
    "DirectFreekick",  # APK ShotSituation 枚举有、生产暂未出现
})
SHOT_TYPE = frozenset({
    "RightFoot", "LeftFoot", "Header", "OtherBodyParts",
    "HeaderAttempt",  # APK ShotType 枚举的拼法,生产用 Header;两者都登记
})
# 加时段拼写统一为 APK MatchPeriod 枚举拼法(migrations/core/0012 迁移
# fact_team_match_stats 的旧拼写 FirstExtraHalf/SecondExtraHalf)
SHOTMAP_PERIOD = frozenset({
    "FirstHalf", "SecondHalf", "FirstHalfExtra", "SecondHalfExtra",
    "PenaltyShootout",
})
TEAM_STATS_PERIOD = frozenset({
    "All", "FirstHalf", "SecondHalf", "FirstHalfExtra", "SecondHalfExtra",
})

# fact_match_events(生产 277,570 行实测 9 种——不是 schema.py 旧注释的 5 种)
EVENT_TYPE = frozenset({
    "Goal", "Card", "Substitution", "AddedTime", "Half",
    "VAR", "MissedPenalty", "Comment", "PenaltyShootout",
})
CARD_TYPE = frozenset({"Yellow", "Red", "YellowRed"})

# fact_league_table.table_type:五个基础档 + 分组赛制的 "档:组名" 复合形式
# (生产实测 21 种值里 16 种是复合;这不是脏数据,是分组联赛的真实结构)
TABLE_TYPE_BASES = frozenset({"all", "home", "away", "form", "xg"})


def table_type_is_known(value: str) -> bool:
    base = value.split(":", 1)[0]
    return base in TABLE_TYPE_BASES


# fact_team_match_stats.extra_json 里"已知存在但尚未投影进任何读路径"的键。
# 语义:我们知道它们在、并且是有意暂不投影(不是没发现)。质量门 G14 的
# 告警口径 = extra_json 键 ∉ (match_report.TEAM_STAT_KEYS ∪ 本集合)——
# 正是这套机制此前缺失,让球队级 physical_metrics_* 在库里躺了数月无人知晓。
TEAM_EXTRA_JSON_KNOWN_UNPROJECTED = frozenset({
    # 球队级体能五键(仅英超少量场次有;投影计划见射门/跑动那一轮的方案)
    "physical_metrics_distance_covered", "physical_metrics_walking",
    "physical_metrics_running", "physical_metrics_sprinting",
    "physical_metrics_number_of_sprints",
    # 来源 UI 分组表头伪键,恒为 null(match_report.py 头部注释的既有认定)
    "shots", "defense", "duels", "discipline", "physical_metrics",
    # 进攻区域三键(采集方案已批,落库后转正进 TEAM_STAT_KEYS)
    "attacking_zone_left", "attacking_zone_center", "attacking_zone_right",
})

# G13 扫描清单:(表, 列, 校验器)。校验器返回 True=已知。
# 表/列在测试库可能不存在(migration 骨架不含全部表)——质量门按
# OperationalError 如实 skip 该项,不误报。
def _in(vocab):
    return lambda v: v in vocab


ENUM_REGISTRY: tuple[tuple[str, str, object], ...] = (
    ("dim_match", "status", _in(DIM_MATCH_STATUS)),
    ("dim_match", "kickoff_precision", _in(KICKOFF_PRECISION)),
    ("fact_shotmap", "Outcome", _in(SHOT_OUTCOME)),
    ("fact_shotmap", "Situation", _in(SHOT_SITUATION)),
    ("fact_shotmap", "Shot_Type", _in(SHOT_TYPE)),
    ("fact_shotmap", "Period", _in(SHOTMAP_PERIOD)),
    ("fact_team_match_stats", "Period", _in(TEAM_STATS_PERIOD)),
    ("fact_match_events", "event_type", _in(EVENT_TYPE)),
    ("fact_match_events", "card_type", _in(CARD_TYPE)),
    ("fact_league_table", "table_type", table_type_is_known),
)
