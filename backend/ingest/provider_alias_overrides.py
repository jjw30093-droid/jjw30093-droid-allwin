"""人工核实过的跨源球队别名(2026-08-24)。

覆盖 `entity_resolution._canonical_form` 明确判定为不可约的队名差异——每一条
都是"一边带限定词/昵称、另一边没有"的写法差异,通用归一化规则去掉这类限定词
会有真实撞名风险(比如去掉"Milan"会把国际米兰和 AC 米兰混在一起;去掉城市名
会把巴西 Internacional 和其它国家同名球队混在一起),只能人工逐条核实后收进
这张表,不能靠算法猜。

2026-08-24 核实方式:直接调用生产 NowGoal 日程接口按开球时间比对 FotMob 真实
场次,确认每一条都是同一支球队的不同书写,不是别的队——不是从队名字面猜的。

签入 git、可 review、可在任意环境幂等重建(seed_manual_alias_overrides),
不是散落在某次运维会话里、其他人无从复现的一次性 UPDATE。

格式:(canonical_team_id, provider_spelling, 说明)。
canonical_team_id 是 FotMob Team_ID;provider_spelling 是 NowGoal 实际发送的
队名原文(未归一化,种别名时会走标准 _norm)。
"""

MANUAL_OVERRIDES: list[tuple[int, str, str]] = [
    (133900, "Daejeon Citizen",
     "NowGoal 用旧称 Daejeon Citizen,FotMob 用新称 Daejeon Hana Citizen(2026 赛季更名)"),
    (8315, "Athletic Bilbao",
     "NowGoal 用惯用地名 Athletic Bilbao,FotMob 用官方名 Athletic Club"),
    (8659, "West Bromwich(WBA)",
     "NowGoal 缩写括注 WBA,FotMob 用全名 West Bromwich Albion"),
    (8602, "Wolves",
     "NowGoal 用绰号 Wolves,FotMob 用全名 Wolverhampton Wanderers"),
    (1786, "Viseu",
     "NowGoal 省略 Academico 前缀,FotMob 用全名 Academico Viseu"),
    (8521, "Stade Brestois",
     "NowGoal 用官方全名 Stade Brestois,FotMob 用地名 Brest"),
    (10214, "Nacional da Madeira",
     "NowGoal 带地域后缀 da Madeira,FotMob 用简称 Nacional"),
    (8611, "FC Twente Enschede",
     "NowGoal 带城市后缀 Enschede,FotMob 用简称 FC Twente"),
    (8636, "Inter Milan",
     "NowGoal 带城市后缀 Milan,FotMob 用简称 Inter——不能用通用规则去掉城市名,"
     "否则会跟 AC Milan(canonical_team_id=8564)撞名"),
    (8702, "Internacional RS",
     "NowGoal 带州名后缀 RS,FotMob 用简称 Internacional——不能用通用规则去掉州名,"
     "否则会跟哥伦比亚/厄瓜多尔等地同名球队撞名"),
]
