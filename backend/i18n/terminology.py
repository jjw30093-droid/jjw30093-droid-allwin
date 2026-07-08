"""
terminology.py — 足球领域术语中文词典(位置/事件/统计字段标签)。

和球队/球员姓名不同,这些是通用足球术语,不存在"音译"问题,直接按中文体育媒体
惯用表达手写,不走翻译 API。命名贴合 schema.py 里对应表的原始字段/取值,
Silver 聚合层和前端取中文标签时直接查这几个字典,不用每层重复定义。

覆盖范围:高频会展示给用户的字段;冷门/内部字段(如 physical_metrics_* 这类
FotMob 尚未稳定采集、真实球队/联赛也几乎不显示的指标)未收录,用到时再补,
不提前为"可能用不上的字段"造词典条目。
"""

# ── fact_match_events.event_type ──
EVENT_TYPE_ZH = {
    "Goal": "进球",
    "Card": "红黄牌",
    "Substitution": "换人",
    "AddedTime": "补时提示",
    "Half": "中场/终场",
    "MissedPenalty": "点球未进",
    "VAR": "VAR 判罚",
}

# ── fact_match_events.card_type ──
CARD_TYPE_ZH = {
    "Yellow": "黄牌",
    "Red": "红牌",
    "YellowRed": "两黄变红",
}

# ── fact_shotmap.Outcome ──
SHOT_OUTCOME_ZH = {
    "Goal": "破门",
    "AttemptSaved": "射正被扑",
    "Miss": "射偏",
    "Post": "击中门柱/横梁",
    "BlockedShot": "射门被封堵",
}

# ── fact_shotmap.Situation ──
SHOT_SITUATION_ZH = {
    "RegularPlay": "运动战",
    "FromCorner": "角球",
    "SetPiece": "定位球",
    "FreeKick": "任意球",
    "Penalty": "点球",
    "FastBreak": "反击",
    "ThrowInSetPiece": "界外球战术",
}

# ── fact_shotmap.Shot_Type ──
SHOT_TYPE_ZH = {
    "LeftFoot": "左脚",
    "RightFoot": "右脚",
    "Head": "头球",
    "Header": "头球",
    "OtherBodyPart": "其他部位",
    "OtherBodyParts": "其他部位",
}

# ── fact_match_lineup.is_starter / is_captain 等布尔字段的展示标签 ──
LINEUP_FLAG_ZH = {
    "is_starter": "首发",
    "is_bench": "替补",
    "is_captain": "队长",
}

# ── fact_league_table.table_type(积分榜档位) ──
TABLE_TYPE_ZH = {
    "all": "总",
    "home": "主场",
    "away": "客场",
    "form": "近期",
    "xg": "预期进球(xG)",
}

# ── 常用统计字段标签(fact_player_match_stats / fact_team_match_stats / fact_season_player_stats 高频展示项) ──
STAT_LABEL_ZH = {
    "goals": "进球",
    "assists": "助攻",
    "rating": "评分",
    "rating_title": "评分",
    "minutes_played": "出场时间",
    "expected_goals": "预期进球(xG)",
    "expected_assists": "预期助攻(xA)",
    "xg_and_xa": "xG+xA",
    "ShotsOnTarget": "射正",
    "ShotsOffTarget": "射偏",
    "total_shots": "射门次数",
    "shot_accuracy": "射门精度",
    "blocked_shots": "封堵射门",
    "big_chance_missed_title": "错失良机",
    "big_chance_created_team_title": "创造良机",
    "accurate_passes": "传球成功",
    "chances_created": "创造机会",
    "passes_into_final_third": "威胁传球",
    "accurate_crosses": "传中成功",
    "long_balls_accurate": "长传成功",
    "touches": "触球次数",
    "dispossessed": "被断球",
    "dribbles_succeeded": "成功过人",
    "clearances": "解围",
    "interceptions": "拦截",
    "recoveries": "反抢/回追",
    "aerials_won": "争顶成功",
    "duel_won": "对抗成功",
    "duel_lost": "对抗失败",
    "fouls": "犯规",
    "was_fouled": "被侵犯",
    "corners": "角球",
    "Offsides": "越位",
    "owngoal": "乌龙球",
    "saves": "扑救",
    "goals_conceded": "失球",
    "goals_prevented": "扑救预期失球差(goals prevented)",
    "clean_sheet": "零封",
    "yellow_card": "黄牌",
    "red_card": "红牌",
    "BallPossesion": "控球率",
}


def get_zh(dictionary: dict, key: str, fallback: str = None) -> str:
    """统一查词典的小工具:查不到就返回 fallback(默认原始 key,方便定位漏词条)。"""
    return dictionary.get(key, fallback if fallback is not None else key)
