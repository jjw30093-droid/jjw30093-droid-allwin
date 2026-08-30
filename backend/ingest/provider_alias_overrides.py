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
    (10273, "Atletico Paranaense",
     "NowGoal 拼写 Atletico(无 h),FotMob 拼写 Athletico(带 h)——单字母拼写差异,"
     "不是结构性词缀/变音符,_canonical_form 不猜拼写,人工核实收录"),

    # ── 2026-08-29 追加:巴甲(268)/葡超(61)州名后缀批次 ──────────────────
    # 触发原因:pipeline_gates 的 xref_unmapped_upcoming 连续 35 小时 CRITICAL
    # ——巴甲未来 72h 的 10 场里 5 场、葡超 1 场卡在 needs_review 采不到任何赔率。
    # 根因与上面 Internacional RS 完全同类:NowGoal 给巴西俱乐部名加州名/城市
    # 后缀,FotMob 用简称,双边队名对不上 → 只能模糊匹配 → 达不到
    # AUTO_OK_THRESHOLD=0.9 → needs_review → 该场不可轮询。
    #
    # 核实方式(与 2026-08-24 那批同一标准,不是从字面猜):调生产 NowGoal 日程
    # 接口取回原文队名,用 titan_id ↔ FotMob Match_ID 的开球时刻**逐分钟一致**
    # 加**对手方队名已能精确解析**双重锁定身份,再逐条确认是同一支球队。
    # 收录前用 _alias_team_ids() 实测确认每条当前确实解析为空集(∅):
    # Gremio (RS) 和 Sporting Braga 实测已能由 _canonical_form 解析,故不收录。
    (10272, "Atletico Mineiro",
     "NowGoal 用全称 Mineiro,FotMob 用缩写 Atletico-MG——MG 是米纳斯吉拉斯州缩写,"
     "展开成全称不是词缀规则能覆盖的"),
    (7733, "Vitoria BA",
     "NowGoal 带州名后缀 BA(巴伊亚州),FotMob 用简称 Vitoria——不能用通用规则去掉"
     "州名,否则会跟葡超 Vitoria de Guimaraes(7844)等同名球队撞名"),
    (9863, "Fluminense RJ",
     "NowGoal 带州名后缀 RJ(里约州),FotMob 用简称 Fluminense"),
    (9808, "Corinthians Paulista (SP)",
     "NowGoal 用全称加州名括注 Paulista (SP),FotMob 用简称 Corinthians"),
    (197693, "Chapecoense SC",
     "NowGoal 带州名后缀 SC(圣卡塔琳娜州),FotMob 用 Chapecoense AF——两边各带"
     "一个不同的后缀,去后缀规则两头都不安全"),
    (1626, "Remo Belem (PA)",
     "NowGoal 带城市+州名 Belem (PA),FotMob 用简称 Remo"),
    (9767, "Coritiba PR",
     "NowGoal 带州名后缀 PR(巴拉那州),FotMob 用简称 Coritiba"),
    (7844, "Vitoria Guimaraes",
     "NowGoal 省略介词 de,FotMob 用 Vitoria de Guimaraes——不能用通用规则去掉"
     "Guimaraes 当城市名,否则会跟巴甲 Vitoria(7733)撞名"),
]
