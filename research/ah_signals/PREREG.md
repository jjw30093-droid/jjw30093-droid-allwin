# PREREG — 五大联赛 AH 赢盘信号研究(预注册)

状态:**草案,待站长审核**。审过之前不跑 Phase 2 的字段核验与持续性检验;审过之后本文件
只允许追加"修订记录",不允许改写已注册的假设与检验集合。

日期:2026-09-28。代码:`research/ah_signals/`。数据访问只读(`mode=ro`),中间产物在
仓库外 `~/research_out/ah_signals/`。

---

## 0. 已锁定口径(Phase 0/1 确认,不在本文件重开)

| 项 | 口径 |
|---|---|
| 样本 | 五大联赛(47/53/54/55/87),`Season ∈ {2025/2026, 2026/2027}`,`status='Finish'`,精确开球;不引入任何更早赛季 |
| 映射 | `dim_match_xref` `review_status ∈ {auto_ok, confirmed}`;8 场 needs_review 全研究剔除 → 1994 场 |
| 主口径公司 | Crown(`company_id=3`);Bet365(8/281)只做收盘线稳健性;Macauslot(1/80)只在 Phase 4 公司间分歧、且只比较 ±15 分钟共同窗口 |
| 收盘线(主) | `observed_at ≤ kickoff−10min` 的最后一条 Crown AH snap;距 kickoff >120min 记缺失(25/26 缺 0,26/27 缺 5)→ **目标表 N=1989** |
| 收盘线(敏感性) | 开球前真实最后一条(25/26 有 5.4% 场次与主定义不同,26/27 0%) |
| 开盘线 | payload `initial`(两季 100% 恒定,Phase 1b 验证);**只用开盘值本身,不构造任何依赖开盘时刻的特征** |
| T-24h 线 | `observed_at ≤ kickoff−24h` 的最后一条;首条晚于 kickoff−24h 记缺失(25/26 缺 0,26/27 缺 8) |
| 符号 | 库内 `line>0` = 主让;`margin = (home−away) − line`;`settle ∈ {−1,−0.5,0,0.5,1}`(与 `reco_settlement_math` 1989 场 0 不一致) |
| Bet365 异常 | 5868027 Malaga vs Deportivo 只从 Bet365 稳健性剔除(`BET365_EXCLUDE_MIDS`) |
| 升班马 | 25/26 常量 14 队(全部精确匹配);26/27 按样本内推导(英超 Hull/Coventry/Ipswich,法甲 Le Mans/Troyes,德甲 Elversberg/Paderborn/Schalke,意甲 Monza/Venezia/Frosinone,西甲 Racing Santander/Deportivo/Málaga) |
| 1x2 | 不跨公司拼接;Phase 4 三方交叉只在 Bet365 内部;Crown 只做 AH/OU |

---

## 1. 主假设与目标

**H0(主)**:在控制 Crown 收盘线之后,任何赛前可得的球队/球员滚动特征对 `margin`
均无增量预测力。

**主目标变量**:`margin`(连续)。**次目标**:`settle`、按真实收盘水位的平注 ROI
(只做描述,不作为检验统计量)。

**主检验形式(Phase 3)**:每个特征单独一条 OLS
`margin ~ closing_line + feature_diff`,HC1 稳健标准误,报告系数、95% CI、p 值、
标准化效应(每 1 SD 特征对应的 margin 变化,单位:球)。特征一律取 **主队 − 客队**
的差值(`feature_diff`),这样一条回归同时覆盖"主队强/客队弱"两个方向。

---

## 2. 样本与冷启动(Phase 2 字段核验时报告精确 N)

- 滚动特征要求该队在样本内至少有 3 场 **开球时间严格早于本场** 的已完赛比赛,否则
  该特征缺失(不插补)。
- 25/26 每个联赛前 3 轮(`Match_Round ≤ 3`)不进入特征检验(只在 Phase 1 基线里)。
- 26/27 滚动窗口可跨季承接 25/26;26/27 升班马前 3 场缺失。
- 特征检验样本估算(以 Phase 2 实际输出为准):25/26 ≈ 1752 − 141(前 3 轮)≈ 1611;
  26/27 ≈ 237 − 升班马前 3 场(≤ 14 队 × 3,去重后按实际)≈ 195~210;**合计 ≈ 1800**。
- 所有分组结果:N<100 标"样本不足,不可靠"。

---

## 3. 特征定义

### 3.1 窗口(主/敏感性)

- **主窗口:8 场 EWMA**。取该队最近 8 场(开球严格早于本场)的统计值,权重
  `w_k = 0.5^(k/4)`(k=0 最近一场,半衰期 4 场),归一化后加权平均;不足 8 场但 ≥3 场时按
  已有场次归一化。
- **敏感性:5 场 / 10 场**同一权重公式(半衰期分别 2.5 / 5 场)。**只报这两档**,不搜索
  其他窗口。
- 主客拆分(只用主队主场/客队客场的历史)**不做**——样本减半、且不在注册集合内。

### 3.2 对手调整(敏感性版本,与主版本成对报告,不进 FDR)

`stat_adj_i = stat_for_i − EWMA(对手在本场之前的 stat_against)`,再对 `stat_adj` 做同样
的 8 场 EWMA。对手的 `stat_against` 同样要求 ≥3 场。主版本 = 未调整差值。

### 3.3 仅上半场版本(敏感性,不进 FDR)

`fact_team_match_stats.Period='FirstHalf'` 的同名统计,按同一窗口计算,只对 F1–F7
(队级统计类)做。目的:检验"上半场表现"是否比全场更少受比分状态(game state)影响。

### 3.4 确认性特征集合(进 FDR,共 12 个,≤15)

编号按先验优先级;`for/against` 指本队/对手在该场的值;差值 = 主队 EWMA − 客队 EWMA。
"方向"是控制收盘线后系数的预期符号(用于报告是否与先验一致,**不做单边检验**)。

| # | 特征 | 定义(单场值) | 来源字段 | 预期方向 | 先验理由 |
|---|---|---|---|---|---|
| F1 | npxGD | `expected_goals_non_penalty(for) − (against)` | team extra_json | + | 基线:基础实力,市场可能未充分吸收 |
| F2 | xGOT 差 | `expected_goals_on_target(for) − (against)` | team extra_json | + | 射正质量比 xG 更接近真实进球 |
| F3 | 进攻运气残差 | `Goals(for) − expected_goals(for)` | team Goals + extra_json | **−** | 市场按结果定价,超额进球应均值回归 |
| F4 | 防守运气残差 | `Goals(against) − expected_goals(against)` | 同上 | **+** | 对称:超额失球的队被低估 |
| F5 | 大机会差 | `big_chance(for) − (against)` | team extra_json | + | 高质量机会创造 |
| F6 | 禁区内触球差 | `touches_opp_box(for) − (against)` | team extra_json | + | 领地控制,比控球率更贴近威胁 |
| F7 | 禁区内射门差 | `shots_inside_box(for) − (against)` | team extra_json | + | 射门位置质量 |
| F8 | 控球率 | `BallPossesion(for)` | team extra_json | 0(无先验) | 常被市场高估,作为对照 |
| F9 | 首发平均评分 | 该场本队 11 名首发 `rating` 均值 | `fact_match_lineup.rating`(is_starter=1) | + | 球员层面的表现质量 |
| F10 | 首发市值合计 | 该场本队 11 名首发 `market_value` 之和(log) | `fact_match_lineup.market_value` | + | 阵容资源(可能已被市场充分定价 → 用作"已定价"对照) |
| F11 | 阵容稳定性 | 该场首发 11 人与本队上一场首发的 Jaccard 相似度 | `fact_match_lineup` | + | 轮换/伤病扰动 |
| F12 | 休息天数差 | 本场 kickoff − 本队上一场(样本内联赛)kickoff,天;取 `min(x, 14)` 后主减客 | `dim_match.kickoff_at_utc` | + | 疲劳;**局限:杯赛/欧战不在样本内,只能度量联赛间隔**,在报告中明示 |

注:F9–F11 用的是 **历史比赛** 的首发(开球前已知),不是本场首发;本场赛前阵容
(kickoff−60min)属于 Phase 4 的独立分析。F3/F4 的 `expected_goals` 用含点球版本,
因为 `Goals` 含点球。

### 3.5 探索性集合(不进 FDR,报告时标注"探索性")

- **Phase 1 已观察**的分组(意甲主/客、`|line|` 深度、升班马、赛季阶段):只能作为
  "探索性 / Phase 1 已观察"列出,**不得作为确认性检验**。
- F1–F12 的 5/10 场窗口、对手调整版、仅上半场版:敏感性。
- 市场类特征(Phase 4):开盘→收盘线移动、T-24h→收盘移动、水位移动、Crown↔Bet365 收盘
  线分歧、AH-OU Poisson 一致性、赛前阵容(kickoff−60min)与预期首发的偏离、CLV。这些在
  Phase 4 单独预注册假设后才跑,本文件先只登记名单。

---

## 4. 多重比较与判定

- 确认性检验 = F1–F12 主版本(8 场窗口、未调整、Crown 收盘线),共 **12 个 p 值**,
  Benjamini–Hochberg **q = 0.10**。
- 通过 BH 的特征还必须同时满足(否则只报"统计显著但不稳健"):
  1. 5 场与 10 场窗口下系数同号;
  2. 对手调整版同号;
  3. Bet365 收盘线(剔除 5868027)下同号;
  4. 五个联赛中至少 4 个同号(各联赛 N≈340–440,不要求各自显著);
  5. 25/26 上下半季(`Match_Round ≤ 19` / `> 19`)同号。
- 任一特征通过全部条件才进入 Phase 5 的多变量模型;没有特征通过时,Phase 5 只跑
  "收盘线 + 全部 F1–F12 的 ridge"作为"整体是否有增量"的单一检验,报告 walk-forward
  的 out-of-sample R² 相对纯收盘线基线的增量与 95% CI。
- 报告用语:只写数字、N、CI、p 与 q;不用"表现优异"等形容词。

---

## 5. 功效分析(Phase 2 用实际 N 与实际 SD 重算并贴 stdout)

`margin` 在 Phase 1 的分布 p5/p95 = −2.5/+2.5,估 SD ≈ 1.55。控制收盘线后的 OLS 中,
单个标准化特征在 n 个样本、双侧 α 下 80% 功效可检出的偏相关约
`r_min ≈ (z_{α/2} + z_{0.80}) / √n`:

| n | α=0.05 | α≈0.0083(BH 12 个假设最严格档) |
|---|---|---|
| 1600 | 0.070 | 0.086 |
| 1800 | 0.066 | 0.081 |

换算成 margin:每 1 SD 特征 ≈ **0.10–0.13 球**。也就是说,比这更小的真实效应本研究
结构上检不出;Phase 3 的报告要把"未拒绝 H0"与"效应上限(CI 上界)"一起给出,不写
"无信号"以外的过度结论。

---

## 6. 字段核验(Phase 2 第一步,先跑先贴)

### 6.1 NULL 语义三分法

对 §3.4 用到的每个 `extra_json` key,在 Period='All' 与 'FirstHalf' 上分别统计
**(a) key 缺失 / (b) 值为 null / (c) 值为 0** 的行数与占比,分季分联赛。规则:

- (a) 与 (b) 一律记缺失,不当 0;
- (c) 只有在该 key 属于"计数类且 FotMob 会显式给 0"时才当 0,否则也标出来供审;
- 某 key 在某联赛缺失率 >5% → 该特征在该联赛标"覆盖不足",并在 §4 条件 4 中不计
  该联赛。

对 `fact_match_lineup.rating` / `market_value`:统计 NULL 占比(按首发)。

### 6.2 球员汇总 vs 球队交叉核对

同一 (Match_ID, Team_ID) 上,`Σ 球员 expected_goals` vs 队级 `expected_goals`、
`Σ 球员 accurate_passes` vs 队级 `accurate_passes`、`Σ 球员 goals` vs `Goals`:报告
相对误差分位数与 |误差|>10% 的场次占比。若 >2% 场次不一致,该来源的 NULL=0 假设
在本研究中不成立,F9–F11 之外不再引入任何球员级汇总特征。

### 6.3 水位合理性(数据清洗)

所有用到的 AH snap:`home+away ∉ [1.60, 2.10]` 或单边 `<0.30` 记异常,分公司分季
报告条数;异常 snap 不参与收盘/开盘/T-24h 取点(取该时点之前最后一条正常 snap),
被剔除后导致收盘线缺失的场次数单独报告。

### 6.4 持续性(split-half)

每个特征在每个 (team, season) 内按 **奇数轮 / 偶数轮** 拆两半,各自均值,跨
team-season 求 Pearson r(25/26 全部;26/27 单独,预计 N 小标不可靠)。r<0.3 的特征
标"低持续性"(仍进 FDR,不删除,报告时并列)。

### 6.5 截断测试(防泄漏,Phase 2 末尾贴原始输出)

随机取 3 场(seed 固定),把该场及之后所有比赛从输入中删掉后重算前一场的全部特征,
必须与全量计算逐字段完全相等(浮点 `==`,不设容差)。任何一项不等即停。

---

## 7. 各阶段停点

Phase 2:本文件审过 → 跑 §6 → 贴 stdout → STOP。Phase 3/4/5/6 按任务书,每阶段
末尾 `git status`、只提交任务文件、`wip:` 前缀。

## 8. 修订记录

- 2026-09-28 初稿(待审)。
