# PREREG — 五大联赛 AH 赢盘信号研究(预注册)

状态:**第 2 稿已审定(2026-09-28)**。此后本文件只允许追加"修订记录",不允许改写已注册的
假设与检验集合。

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

**H0(主)**:在控制 Crown 收盘线之后,任何赛前可得的**非射门类**球队/球员滚动特征
(C 族)对 `margin` 均无增量预测力。射门/xG 类(B 族)只作对照基准,不构成研究结论。

**主目标变量**:`margin`(连续)。**次目标**:`settle`、按真实收盘水位的平注 ROI
(只做描述,不作为检验统计量)。

特征一律取 **主队 − 客队** 的差值(`feature_diff`),一条回归同时覆盖"主队强/客队弱"
两个方向。

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

`fact_team_match_stats.Period='FirstHalf'` 的同名统计,按同一窗口计算,只对能从
队级 `extra_json` 直接构造的特征(B1–B6、C1–C3、C5–C8、C12)做。目的:检验"上半场
表现"是否比全场更少受比分状态(game state)影响。

### 3.4 B 基线族(射门/xG,只作对照;独立 BH;**结果不计入研究结论**)

用途:Phase 3 报告里作为对照基准;Phase 5 作为协变量;§5 次检验的合成指标来源。

| # | 特征 | 定义(单场值,for=本队 against=对手) | 来源字段 | 预期方向 |
|---|---|---|---|---|
| B1 | npxGD | `expected_goals_non_penalty(for) − (against)` | team extra_json | + |
| B2 | xGOT 差 | `expected_goals_on_target(for) − (against)` | team extra_json | + |
| B3 | 进攻运气残差 | `Goals(for) − expected_goals(for)` | team Goals + extra_json | **−**(均值回归) |
| B4 | 防守运气残差 | `Goals(against) − expected_goals(against)` | 同上 | **+** |
| B5 | 大机会差 | `big_chance(for) − (against)` | team extra_json | + |
| B6 | 禁区内射门差 | `shots_inside_box(for) − (against)` | team extra_json | + |

B3/B4 的 `expected_goals` 用含点球版本,因为 `Goals` 含点球。

### 3.5 C 确认族(非射门,研究对象;独立 BH;≤14 个,当前 13 个)

"构造"一列写明所用字段;**标"代理"的表示与 Opta 标准定义不同**,报告里按此名称
而不是标准名称称呼。

| # | 特征 | 构造(单场值) | 来源字段 | 预期方向 | 先验理由 |
|---|---|---|---|---|---|
| C1 | field tilt(代理:前场传球占比) | `opp_half_passes(for) / (opp_half_passes(for) + opp_half_passes(against))`,分母明确为**双方 `opposition_half_passes` 之和** | team `opposition_half_passes` | + | 领地控制 |
| C2 | PPDA(代理) | 分子 = `passes(against) − opposition_half_passes(against)`(对手在本方半场的传球);分母 = `tackles(for) + interceptions(for) + fouls(for)`;分母为 0 记缺失 | team `passes`、`opposition_half_passes`、`matchstats.headers.tackles`、`interceptions`、`fouls` | **−**(值越小压迫越强) | 高位压迫强度;报告附 C2 与 C12 的相关系数,\|r\|>0.8 标注共线但不移出 |
| C3 | 禁区触球差 | `touches_opp_box(for) − (against)` | team `touches_opp_box` | + | 领地控制,比控球率更贴近威胁 |
| C4 | 推进传球差 | `Σ 出场球员 passes_into_final_third(for) − (against)` | player `passes_into_final_third`(球员级求和,间接验证见 §6.2) | + | 纵向推进能力 |
| C5 | 传中占比 | 球员表有传中总数列(`accurate_crosses_total`,迁移 0022)时:`Σ 球员 accurate_crosses_total / Σ 球员 accurate_passes_total`(传中总数 / 传球总数);该列不可用时沿用队级 `accurate_crosses / accurate_passes` 并标"代理" | player `accurate_crosses_total`、`accurate_passes_total`;回退 team `accurate_crosses`、`accurate_passes` | 双侧,不预设 | 风格变量 |
| C6 | 长传占比(代理) | `long_balls_accurate(for) / accurate_passes(for)` | team `long_balls_accurate`、`accurate_passes` | 双侧,不预设 | 风格变量 |
| C7 | 对抗成功率差 | `(ground_duels_won(for) + aerials_won(for)) / (ground_duels_won(for)+aerials_won(for)+ground_duels_won(against)+aerials_won(against))`,主减客 | team `ground_duels_won`、`aerials_won`(对抗零和,双方 won 之和即总数) | + | 身体对抗 |
| C8 | 角球差 | `corners(for) − (against)` | team `corners` | + | 施压频率 |
| C9 | 首发球员赛前滚动评分差 | 见下方 C9 定义 | `fact_match_lineup.rating`、`is_starter`、`sub_in/out_time`、`minutes_played` | + | 球员层面表现质量 |
| C10 | 阵容 Jaccard 稳定性差 | 本队上一场首发 11 人 vs 上上场首发 11 人的 Jaccard(两场均严格早于本场),取 8 场 EWMA;主减客 | `fact_match_lineup` | + | 轮换/伤病扰动 |
| C11 | 休息天数差 | 本场 kickoff − 本队上一场(样本内联赛)kickoff,天;`min(x, 14)`;主减客 | `dim_match.kickoff_at_utc` | + | 疲劳;**局限:杯赛/欧战不在样本内,只能度量联赛间隔** |
| C12 | 控球差(对照) | `BallPossesion(for)` 差 | team `BallPossesion` | 0(预期控制收盘线后无增量) | 常被市场高估 |
| C13 | 首发市值差(条件保留) | 本队上一场首发 11 人 `market_value` 之和取 log;主减客 | `fact_match_lineup.market_value` | + | 阵容资源;**保留前提见 §6.6** |

**C9 定义(写死)**:对本场 kickoff 严格早于本场的所有比赛,取每名球员的**赛后**
`rating`,按该场 `minutes_played` 加权做 8 场 EWMA(球员个人的近 8 场,不足 8 场但 ≥3
场按已有归一化,<3 场缺失),得到每名球员的"赛前滚动评分"。球队值 = 该队**上一场**
(赛前可得)首发 11 人赛前滚动评分的均值(评分缺失的球员不计,11 人中缺失 >3 人则该
场缺失)。**本场的 rating 与本场实际首发不得进入**;本场赛前阵容(kickoff−60min)归
Phase 4。
**C9 缺失规则(审定)**:首发 11 人中历史评分场次 <3 的球员不参与均值;有效球员
<8 人时该队该场 C9 记为缺失,不做插补;报告 C9 缺失率。

**去留规则(写死,§6.1/6.2 核验后执行,只看字段核验结果,不看与 margin 的关系)**:
C1、C2、C4、C5、C6、C7 若在 Phase 2 无法构造(字段不存在、分母语义不成立、球员汇总
交叉核对不通过),或所需任一字段 NULL 率(key 缺失 + 值 null)>5%,直接移出 C 族并记录
原因;移出后 BH 按剩余特征数计算。C13 按 §6.6 决定去留。

### 3.6 探索性集合(不进 FDR,报告时标注"探索性")

- **Phase 1 已观察**的分组(意甲主/客、`|line|` 深度、升班马、赛季阶段):只能作为
  "探索性 / Phase 1 已观察"列出,**不得作为确认性检验**。
- B/C 族的 5/10 场窗口、对手调整版、仅上半场版:敏感性。
- 市场类特征(Phase 4):开盘→收盘线移动、T-24h→收盘移动、水位移动、Crown↔Bet365 收盘
  线分歧、AH-OU Poisson 一致性、赛前阵容(kickoff−60min)与预期首发的偏离、CLV。这些在
  Phase 4 单独预注册假设后才跑,本文件先只登记名单。

---

## 4. 检验结构与多重比较

- **主检验**(每个特征一条):`margin ~ closing_line + feature_diff`,HC1 稳健标准误,
  报告系数、95% CI、双侧 p、标准化效应(每 1 SD 特征对应的 margin 变化,单位:球)。
  每个特征预设方向,**检验用双侧**,报告"方向与预期是否一致"。
- **次检验(仅 C 族)**:`margin ~ closing_line + B_composite + feature_diff`,报告
  "xG 之外的增量"。`B_composite` = B1–B6 各自在全样本标准化(z 分数)后**按先验方向
  对齐**(B3 取负号,其余取正号)的**等权平均**:
  `B_composite = (z(B1) + z(B2) − z(B3) + z(B4) + z(B5) + z(B6)) / 6`。权重现在写死,
  不按结果拟合;某项缺失时该场 `B_composite` 缺失。
- **多重比较**:B 族与 C 族**各自独立**做 Benjamini–Hochberg,q = 0.10,只对主检验
  的 p 值做;B 族通过与否只作对照,不写入结论;C 族的 BH 按 §3.5 去留规则后的实际
  特征数计算(≤13)。
- C 族通过 BH 的特征还必须同时满足(否则只报"统计显著但不稳健"):
  1. 5 场与 10 场窗口下系数同号;
  2. 对手调整版同号;
  3. Bet365 收盘线(剔除 5868027)下同号;
  4. 五个联赛中至少 4 个同号(各联赛 N≈340–440,不要求各自显著;§6.1 判"覆盖不足"
     的联赛不计入);
  5. 25/26 上下半季(`Match_Round ≤ 19` / `> 19`)同号。
- 任一 C 族特征通过全部条件才进入 Phase 5 的多变量模型;没有特征通过时,Phase 5 只跑
  "收盘线 + B_composite + 全部 C 族的 ridge"作为"整体是否有增量"的单一检验,报告
  walk-forward 的 out-of-sample R² 相对"收盘线 + B_composite"基线的增量与 95% CI。
- 报告用语:只写数字、N、CI、p 与 q;不用"表现优异"等形容词。

---

## 5. 功效分析(Phase 2 用实际 N 与实际 SD 重算并贴 stdout)

`margin` 在 Phase 1 的分布 p5/p95 = −2.5/+2.5,估 SD ≈ 1.55。控制收盘线后的 OLS 中,
单个标准化特征在 n 个样本、双侧 α 下 80% 功效可检出的偏相关约
`r_min ≈ (z_{α/2} + z_{0.80}) / √n`:

| n | α=0.05 | α≈0.0077(BH 13 个假设最严格档) |
|---|---|---|
| 1600 | 0.070 | 0.087 |
| 1800 | 0.066 | 0.082 |

换算成 margin:每 1 SD 特征 ≈ **0.10–0.13 球**。比这更小的真实效应本研究结构上
检不出;Phase 3 的报告要把"未拒绝 H0"与"效应上限(CI 上界)"一起给出,不写"无信号"
以外的过度结论。次检验(加 B_composite)的功效更低,同样报告 CI 上界。

---

## 6. 字段核验(Phase 2 第一步,先跑先贴)

### 6.1 NULL 语义三分法

对 §3.4/3.5 用到的每个 `extra_json` key,在 Period='All' 与 'FirstHalf' 上分别统计
**(a) key 缺失 / (b) 值为 null / (c) 值为 0** 的行数与占比,分季分联赛。规则:

- (a) 与 (b) 一律记缺失,不当 0;
- (c) 只有在该 key 属于"计数类且 FotMob 会显式给 0"时才当 0,否则也标出来供审;
- 某 key 在某联赛缺失率 >5% → 该特征在该联赛标"覆盖不足",并在 §4 条件 4 中不计
  该联赛;对 C1/C2/C4/C5/C6/C7,任一字段全样本缺失率 >5% → 按 §3.5 去留规则移出。

对 `fact_match_lineup.rating` / `market_value`、`fact_player_match_stats.minutes_played` /
`passes_into_final_third`:统计 NULL 占比(按首发 / 按出场)。

### 6.2 球员汇总 vs 球队交叉核对

同一 (Match_ID, Team_ID) 上,`Σ 球员 expected_goals` vs 队级 `expected_goals`、
`Σ 球员 accurate_passes` vs 队级 `accurate_passes`、`Σ 球员 goals` vs `Goals`:报告
相对误差分位数与 |误差|>10% 的场次占比。若 >2% 场次不一致,该来源的 NULL=0 假设
在本研究中不成立,C9 仍可用(rating 不是求和量)。
**C4 间接验证(审定)**:逐场对比 `Σ 球员 accurate_passes_total`(球员传球总数)与队级
`passes`,相对误差 >2% 的场次 C4 记为缺失;C4 缺失场次占比 >5% 时按 §3.5 去留规则
移出。另报告每队每场 `Σ minutes_played` 的分布(应接近 11×90 + 补时),用于检查是否
有球员行缺失。

### 6.3 水位合理性(数据清洗)

所有用到的 AH snap:`home+away ∉ [1.60, 2.10]` 或单边 `<0.30` 记异常,分公司分季
报告条数;异常 snap 不参与收盘/开盘/T-24h 取点(取该时点之前最后一条正常 snap),
被剔除后导致收盘线缺失的场次数单独报告。

### 6.4 持续性(split-half)

每个特征在每个 (team, season) 内按 **奇数轮 / 偶数轮** 拆两半,各自均值,跨
team-season 求 Pearson r(25/26 全部;26/27 单独,预计 N 小标不可靠)。r<0.3 的特征
标"低持续性"(仍进 FDR,不删除,报告时并列)。

### 6.5 截断测试(防泄漏,Phase 2 末尾贴原始输出)

**全部场次**:对每一场 i,把开球 ≥ i 的所有比赛从输入中删掉后重算第 i 场的全部特征
(B/C 族主版本 + 5/10 窗口 + 对手调整 + 上半场版),必须与全量计算逐字段**浮点严格
相等**(`==`,不设容差);任何一项不等即停并贴出不等的场次与字段。
若全量耗时过长(单次 >30 分钟),改为:**随机 300 场(seed 固定)+ 26/27 全部前 3 轮场次
+ 25/26 第 4 轮全部场次**(冷启动边界),并在输出里写明用了哪一档。

### 6.6 C13 `market_value` 时间语义核验

- 同一球员在 25/26 不同比赛的 `fact_match_lineup.market_value` 是否随时间变化:按
  球员统计 distinct 值个数,报告"恒定 / 变化"的球员占比与变化球员的前 10 例(球员、
  各值、对应比赛日期)。
- 25/26 的值与 26/27 同一球员的值是否一致:两季都有首发记录的球员中,值相同的占比。
- 判定:只有当值随比赛时间**在赛季内有变化**、且变化时点与比赛日期一致(即写入的是
  当场抓取时的估值,而非事后回填的当前值)时,才视为"赛前估值";**不能证明是赛前
  估值的(例如同一球员两季值全部相同、或赛季内恒定),删除 C13**,并记录核验结果。

---

## 7. 各阶段停点

Phase 2:本文件审过 → 跑 §6 → 贴 stdout → 按 §3.5/§6.6 更新去留并追加修订记录 → STOP。
Phase 3/4/5/6 按任务书,每阶段末尾 `git status`、只提交任务文件、`wip:` 前缀。

## 8. 修订记录

- 2026-09-28 初稿(待审)。
- 2026-09-28 第 2 稿审定修改(站长审定,直接生效):C1 分母写明为双方
  `opposition_half_passes` 之和;C2 分子改为对手在本方半场的传球(`passes −
  opposition_half_passes`),报告附 C2×C12 相关系数;C4 改为间接验证(球员传球总数 vs
  队级 passes,>2% 记缺失,缺失 >5% 移出)+ 报告 Σminutes_played 分布;C5 优先用球员
  表传中总数列,方向改双侧;C9 缺失规则(评分场次 <3 不参与,有效 <8 记缺失);C7 不改。
  Phase 2 范围:只做字段核验、NULL 三分法、交叉核对、market_value 时间语义、split-half、
  截断测试;不计算任何特征与 margin/settle/收盘线的关系。
- 2026-09-28 第 2 稿(按站长意见):拆 B 基线族 / C 确认族各自独立 BH;C 族改为非射门
  13 个特征并写死去留规则;C9 改为赛前滚动评分(仅严格更早比赛的赛后评分、分钟加权、
  上一场首发);C13 加 `market_value` 时间语义核验;增加次检验(`+ B_composite`,
  权重写死);截断测试改为全部场次(超时则 300 场 + 冷启动边界档)。
- 2026-09-28 Phase 2 字段核验结果(按 §3.5/§6.6 规则执行,只看字段核验,不看与目标的
  关系;原始输出 `~/research_out/ah_signals/phase2_stdout_part1.txt` + `_part2.txt`):
  - **C4 移出**:`fact_player_match_stats.accurate_passes_total` 在 25/26 99.9% NULL、
    26/27 8.7% NULL(列由迁移 0007 加入,存量未回填),C4 间接验证不一致队场 3543/3988
    (88.84%)>5%。另 §6.2 交叉核对:Σ球员 xG vs 队级 xG |err|>2% 占 13.04%、Σ球员
    goals vs 队级 Goals 3.86%,均 >2% → 球员级求和的 NULL=0 假设在本研究不成立,C9
    (非求和量)保留。
  - **C13 删除**:`fact_match_lineup.market_value` 赛季内 90.9% 球员有变化,但取值按
    "写入批次"而非按比赛日期分段(同一球员 08-16=A、08-23=B、08-31..12-21=A、12-27=B、
    12-30=A……,4 个月的比赛共用同一个值,相邻比赛日在两个值间来回跳),两季取值集合
    相同的球员仅 2/1697——写入的是抓取时刻的当前估值,不能证明是赛前估值。
  - **C5 用队级代理**(`accurate_crosses / accurate_passes`):球员表 `accurate_crosses_total`
    列存在(迁移 0022)但两季 100% NULL(未回填),按"无传中总数列"处理;方向双侧。
  - C1/C2/C6/C7 所需队级 key 缺失率全部 0.0%(Period=All;FirstHalf 仅 touches_opp_box
    有 16 行值 null,0.4%)→ 保留。C 族剩余 **12 个**(C1–C3、C5–C12)。
  - 队级 extra_json 无 key 缺失(a=0)、无 null(All),0 值为真实 0(big_chance 534 队场、
    xGOT 110 队场等);FirstHalf 的 Goals 列全 NULL(3988/3988),B3/B4 无上半场版本。
  - lineup:每队场首发恰 11 人(3988/3988);rating NULL 0.09%/0.04%;
    Σminutes_played 队场 p1=924、p50=990,<900 的队场 7 个。
  - split-half(25/26,N=96 team-season):B3 +0.060、B4 −0.217、C11 −0.487 标"低持续性"
    (仍进 FDR);其余 0.44~0.999。26/27 每队 ≤5 轮,奇/偶各 ≥5 场的队为 0,r 不可算,
    标"样本不足,不可靠"。
  - 截断测试:按比赛日分组全量档,1994 场 / 209 个比赛日 / 474,572 个字段,不等 0,
    用时 477s。
  - C2×C12(8 场窗口 diff)r=−0.257(N=1810),不共线。
  - 功效(实际 N=1810,margin SD=1.547):r_min(α=.05)=0.066≈0.102 球/SD;BH 最严档
    (0.10/12)0.082≈0.127 球/SD。
- 2026-09-28 **C4 验证字段更正**(站长审定;结论依据字段核验,未接触结果变量):
  此前用 `accurate_passes_total`(存量未回填)做行完整性验证是选错了字段。更正为
  (a)行完整性:Σ球员 `accurate_passes` vs 队级 `accurate_passes`,|err|>2% 的队场
  2/3988(0.05%),25/26 1/3504、26/27 1/484 → 通过;(b)`passes_into_final_third` 的
  NULL 语义按球员分组:minutes≥45 且 accurate_passes≥10 的球员 NULL 率 6.83%
  (2,804/41,066;25/26 6.84%、26/27 6.71%),其余球员 47.02% → 第一组 >5%,判定为
  缺数据(该组 NULL 行 accurate_passes p50=17、max=103,不是"出场少没这类传球"),
  **C4 维持移出**。`features.py` 已同步改为用 `accurate_passes` 做行完整性、NULL 按 0
  计,以便未来数据补全后可直接启用;截断测试对改后特征重新全量跑过(见 part3)。
  C 族最终 **11 个**(C1、C2、C3、C5、C6、C7、C8、C9、C10、C11、C12),BH 分母=11。
- 2026-09-28 **样本隔离**(站长补充进任务书):Phase 3 与 Phase 4 只用 25/26(冷启动
  剔除后:`Match_Round>3` 且有 Crown 收盘线,N=1608 候选);26/27 完全不参与任何检验、
  筛选或描述统计;26/27 只在 Phase 5 用冻结模型跑一次。按 25/26 实际 N 重算的功效:
  N=1607(C9 1577、C10 1559)、margin SD=1.539 → r_min(α=.05)=0.070≈0.108 球/SD,
  BH 最严档(0.10/11=0.0091)0.086≈0.132 球/SD。
- 2026-09-28 Phase 3 执行口径(站长):主口径 Crown 收盘线;Bet365 稳健性剔除
  `BET365_EXCLUDE_MIDS`;B 族(6)与 C 族(11)各自独立 BH q=0.10;每个特征报告系数、
  HC1 SE、t、p、q、与 closing_line 的相关系数、5 条稳健性条件逐条、方向是否与预期一致;
  C 族另报次检验(+B_composite);不显著特征按 95% CI 上界报告效应上限(球/SD);敏感性
  (5/10 场、对手调整、仅上半场)单独成表不进 FDR,B3/B4 的上半场版因 FirstHalf Goals
  全 NULL 标"不可算"。p 值用正态近似(df≈1600;服务器无 numpy/scipy,OLS+HC1 为纯
  Python 实现,已与 numpy 在合成数据上逐值核对一致)。
- 2026-09-28 Phase 3 结论确认(站长):C 族 11 个、B 族 6 个均未通过 BH;Phase 5 方案(收盘线 +
  B_composite + 全部 C 族 ridge,单一整体增量检验)批准,**ridge alpha 只能在每个训练窗口
  内部 CV 选择,不得全样本调参**;先不跑。B1/B2 系数为负(与先验相反)记入 REPORT 探索性
  部分,注明"不显著,不据此行动"。Phase 4 另行预注册于 `PREREG_phase4.md`。
