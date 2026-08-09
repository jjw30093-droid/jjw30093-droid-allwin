# 五大联赛历史核心数据质量审计报告(修复轮 · v3)

- 审计范围:`data/allwin.db`(生产核心库,只读快照)vs `data/verify_leagues.db`(法甲53/德甲54/意甲55/西甲87 六赛季独立抓取验证库,只读快照)
- 基准联赛:英超(League_ID=47)——仅作为"同源同赛季"参照系,不作为绝对真值
- 审计脚本:[`analysis/data_quality/audit_top5_historical_core.py`](../../analysis/data_quality/audit_top5_historical_core.py)(`audit_method_version=3`,`output_schema_version=3`)
- 自验证测试:[`analysis/data_quality/test_audit_fixture.py`](../../analysis/data_quality/test_audit_fixture.py)(**56 条** = v2 基线 45 条 + 本轮新增 11 条:7 条 position_id 适用性反例 + 1 条 dim_player 唯一计数跨赛季去重 + 3 条 schema contract/method version 契约)
- 输出目录:[`analysis/data_quality/output/`](../../analysis/data_quality/output/)
- 本轮为**只读审计 + 审计器窄收口修复**,未对任一源数据库做任何写操作(见文末"最终确认")

**背景**:上一轮(v1)报告曾声称"7 张表内容级已验证",但独立复核用注入实验证明 v1 的 merge-parity 判定逻辑存在 P0 缺陷,v2 已修复并经独立复核确认 `Underlying data: TRUSTED_WITH_FILTERS`、`Audit implementation: VALIDATED`,但 v2 报告遗留两处口径问题(`Report readiness: SHARE_WITH_CAVEATS`):① `position_id` 被无差别标 REQUIRED,导致 `standard_match` 部分赛季因"替补席未出场球员合法缺失该字段"被误判 NOT_READY;② xGOT 训练建议把"结构性零填充"错误限定为只发生在越靶射门,实际 on-target 射门同样受影响。**本轮(v3)只针对这两处 + dim_player 唯一计数补充 + 测试补强做窄收口,不重新触碰已验证的 merge-parity / readiness 引擎 / 条件字段分母 / 异常分区逻辑。**

---

## 三个 Verdict

| 维度 | 判定 |
|---|---|
| **Underlying data(底层数据)** | **TRUSTED_WITH_FILTERS** |
| **Audit implementation(审计器实现)** | **VALIDATED** |
| **Report readiness(本报告可用性)** | **READY_TO_SHARE** |

三者均只有在 P0/P1 全部修复、全部反例测试通过、真实数据库重新证明之后才能给出。`Underlying data`/`Audit implementation` 在 v2 已成立且本轮未改动其判定基础;`Report readiness` 本轮从 v2 的 `SHARE_WITH_CAVEATS` 升级为 `READY_TO_SHARE`——两处遗留 caveat(position_id 适用性、xGOT on-target 零填充措辞)已修复并重新生成产物验证,若修复后出现无法解释的新异常将改判 `DONE WITH CAVEATS`(见文末最终确认,本轮未出现此情况)。

---

## 开篇结论(≤15 行,10 问速答)

1. **verify_leagues 是否已完整合并进 allwin?** 是。7 张核心表全部给出内容级证据(不是行数比对):6 张按 Match 分区的表(`dim_match`+5 张事实表)在 24 个联赛×赛季分区上**全部 EXACT**(147/168);`dim_player`(全局表)3/24 EXACT、21/24 CONTENT_MISMATCH,逐条差异按分区计有 34 处 Unicode 重音规范化、18 处真实姓名形式变化(如昵称↔全名)——但这是**分区出现次数**,同一现实球员跨多个赛季会被计多次;去重到**唯一 Player_ID** 后实际只有 **20 名球员**发生过差异(13 名纯重音规范化、7 名含真实姓名形式变化),其中 14 名跨 ≥2 个分区重复出现,0 处缺失、0 处空白、0 处同一 ID 对应多个不兼容姓名(冲突)。两套口径均由脚本 `dim_player_partition_occurrences()`/`dim_player_name_diff_summary()` 直接计算并写入 `top5_quality_summary.json.dim_player_name_differences`,非人工誊抄。
2. **哪些表 EXACT/SEMANTICALLY_EQUAL?** `dim_match`/`fact_shotmap`/`fact_player_match_stats`/`fact_team_match_stats`/`fact_match_events`/`fact_match_lineup` 六表 24/24 EXACT(现在的 EXACT 判定**真正比较了全部共同列,含 extra_json 原文哈希与语义哈希**,已用 12 条 mutation 反例证明能检出内容篡改)。0 个分区落在 SEMANTICALLY_EQUAL/CONTENT_MISMATCH/MISSING_FROM_ALLWIN/EXTRA_IN_ALLWIN。
3. **schema 是否对齐?** 是。唯一差异是 `dim_match.kickoff_at_utc` 仅存在于 core(verify_leagues.db 生成早于精确开球时间改造,预期内差异)。
4. **字段填充率(按 League×Season×family 正确粒度)?** `shot_xg`(REQUIRED 最低覆盖率 95.8%-99.7%)、`goalkeeper_advanced`(95.3%-98.7%)全部 5 联赛全部 6 赛季 READY;`standard_match`(**65.0%-92.8%,精确最小值 65.0095%,Bundesliga 2022/2023**)、`defensive_duel_passing`(66.7%-74.7%)READY 或 READY_WITH_FILTERS,**0 个分区 NOT_READY**(v3 修复:`fact_match_lineup.position_id` 改为只对首发球员——`is_starter=1`——REQUIRED,替补席未出场球员的合法缺失不再计入分母,见第 5.6/8 节);`physical` 全 5 联赛全 6 赛季 REQUIRED 覆盖率 0%(NOT_READY,成因 UNVERIFIED,本轮不修改)。
5. **哪些联赛/赛季有明显高阶数据缺口?** 全部 5 联赛统一缺口(非四个合入联赛特有):半场拆分统计 2023/24 前只有 `Period='All'`;`physical_metrics_*` 全量 0%;xGOT 在 on-target 射门子集内"非空率"从 2024/25 起由约 55% 跳到约 99.5%,但**正例(>0)比例全程持平于约 54%-58%**——是来源在 2024/25 起对 on-target 射门做的**结构性零填充**(把此前的 NULL 编码成字面 0),不是新增测量能力(详见第 5.1 节,off-target 射门的 xGOT 则始终 NOT_APPLICABLE);替补席 `position_id`(`fact_match_lineup.position_id`)2024/25 起全联赛同步降到 0%,但首发球员的 `position_id` 全程 100%,`usual_position_id`/`usual_position` 全程 ~100% 可作跨赛季稳定替代(详见第 5.6 节,v3 已修正为不计入替补合法缺失)。
6. **跨联赛 xG 模型最早安全可用赛季?** 团队级/射门级核心 xG 字段(`fact_shotmap.xG`、team extra_json `expected_goals` 等)全部 6 赛季 REQUIRED 覆盖率 ≥95.8%,自 2020/21 起即可用;需要半场拆分的模型建议从 **2023/24** 起。**xGOT 训练规则(v3 修正)**:off-target 射门的 xGOT 始终 NOT_APPLICABLE;on-target 射门里 2024/25 起的字面 0 是结构性零填充,不能直接当测得值使用——若无法可靠区分"真实 0"与"填充 0",该字段应从跨断点统一模型中排除,或只用 `xGOT>0`/"是否可测"等稳定派生特征,不得笼统写"NULL 与 0 语义等价"(见第 5.1 节)。
7. **是否发现污染模型的异常或结构性偏差?** 无 CRITICAL。**4 项 HIGH 不是 4 个独立底层问题,而是 2 个真实联赛赛季问题分别在 core 和 verify 两个库里各出现一次**(Ligue1 2022/23、LaLiga 2020/21;core/verify 数字几乎相同,证明是源数据本身的既有问题,非本轮合并引入)。这 2 个分区各含 2 名孤儿**首发**球员,经查这 4 名球员均**无 `fact_player_match_stats` 行、无 `minutes_played`、无 `fact_match_events` 记录**——HIGH 是**参照完整性标准**下的严重度(球员名无法展示、阵容名册不完整),不代表这 4 名球员污染了当前任何球员统计/训练特征(因为根本没有对应的统计行可污染);对模型训练风险为 LOW,对阵容名册/展示完整性有真实影响。其余 30 个分区的孤儿全部是从未出场的替补(0 行有 `minutes_played>0`),已判 LOW。2 项 MEDIUM(events 时间线 1 处、lineup 换人时间 1 处)。
8. **是否有分区需要重新采集?** 不需要。已发现的缺口均可归因为历史来源能力限制(半场拆分、xGOT 扩展)——已用 4/4 场真实 FotMob 现网抓取独立确认:2021/22 旧场今天仍只返回 `Periods=['All']`、越靶射门 xGOT 仍为 NULL,2024/25 新场才有完整拆分与 0 填充,重采旧赛季恢复不了任何数据。`physical_metrics_*` 成因未证实(疑似解析器 key 映射问题,非"来源不提供"),本轮未修改 `backend/fotmob_client.py`,留作独立后续事项。
9. **训练时按字段/赛季过滤是否足够?** 是。按第 8 节三套训练配方(基于修复后的 `League×Season×family` 粒度 readiness)过滤后可用,无需重新采集。
10. **总体评级?** **TRUSTED_WITH_FILTERS**——合并内容一致(6/7 表 EXACT 有真实证据支撑)、无 CRITICAL 异常、审计器本身已通过 mutation 反例验证;训练前必须按 family/字段/赛季过滤,`physical` 排除,`shot_accuracy` 视为与 ShotsOnTarget 冗余,`position_id` 首发用具体槽位/替补用 `usual_position_id`,xGOT 按第 5.1 节规则处理 on-target 结构性零。

---

## 1. 数据集与粒度总览

四张验证库合并进 allwin 的联赛为 Ligue1(53)/Bundesliga(54)/SerieA(55)/LaLiga(87),各 6 个赛季(2020/2021–2025/2026)。加上基准 EPL(47),审计覆盖 5 联赛 × 6 赛季 = 30 个分区(`top5_partition_summary.csv` 30 行)。

真实候选键粒度(归一化组合键去重测量,非假设主键):

| 表 | 候选键 | core 重复组 | verify 重复组 |
|---|---|---:|---:|
| `dim_match` | Match_ID | 0 | 0 |
| `dim_player` | Player_ID | 0 | 0 |
| `fact_shotmap` | Match_ID+Player_ID+Team_ID+Minute+Period+X_Coord+Y_Coord+xG | 3 | 3 |
| `fact_player_match_stats` | Match_ID+Player_ID+Team_ID | 0 | 0 |
| `fact_team_match_stats` | Match_ID+Team_ID+Period | 0 | 0 |
| `fact_match_events` | Match_ID+event_index | 0 | 0 |
| `fact_match_lineup` | Match_ID+Team_ID+Player_ID | 0 | 0 |

`fact_shotmap` 的 3 组"重复"经全字段核对(补充 Outcome/Shot_Type/xGOT 后即可区分),均是**同球员同分钟同坐标的两次真实射门**(如射正被扑+回追补射:Match 3424138 = AttemptSaved/右脚 + Goal/左脚),不是数据重复;两库该 3 组完全一致,是源数据本身的候选键颗粒度问题,非合并引入,严重度 LOW,不计入 mismatch。

比赛数与真实球队数推导的双循环期望完全吻合(不硬编码 380/306):Bundesliga 全程 18 队/306 场、Ligue1 2023/24 起由 20 队降至 18 队/306 场,均正确识别为正常联赛规则。唯一超出双循环期望的分区是 **SerieA 2022/2023(381 场,期望 380,+1)**——已通过 `dim_match` 逐场核实,多出的 Match_ID=**4185671**(2023-06-11,Spezia vs Hellas Verona,`Match_Round='final'`,1–3)是真实的保级附加赛(相当于英超语境下的 play-off),Hellas Verona(9876)与 Spezia(9881)双方各踢 39 场,其余 18 队各 38 场,20 支球队数正确、无重复/伪造记录。训练用常规联赛数据集应**排除该附加赛**或单独标记为 playoff 样本。

---

## 2. 合并完整性(verify_leagues → allwin)

采用**修复后**的流式行哈希多重集合比对——`raw_hash_expr` 覆盖全部共同列(含 extra_json 原始文本),`sem_hash_expr` 另外对 extra_json 做 canonical JSON(`json.dumps(sort_keys=True)`)语义哈希;两条轨道均已通过 12 条 mutation 反例验证:

| 表 | EXACT | CONTENT_MISMATCH | MISSING_FROM_ALLWIN | EXTRA_IN_ALLWIN | SCHEMA_INCOMPATIBLE |
|---|---:|---:|---:|---:|---:|
| `dim_match` | 24/24 | 0 | 0 | 0 | 0 |
| `fact_shotmap` | 24/24 | 0 | 0 | 0 | 0 |
| `fact_player_match_stats` | 24/24 | 0 | 0 | 0 | 0 |
| `fact_team_match_stats` | 24/24 | 0 | 0 | 0 | 0 |
| `fact_match_events` | 24/24 | 0 | 0 | 0 | 0 |
| `fact_match_lineup` | 24/24 | 0 | 0 | 0 | 0 |
| `dim_player` | 3/24 | 21/24 | 0 | 0 | 0 |

**6/7 表(144/144 分区)内容级完全一致**——`dim_match` 的比较现在覆盖 Season/League_ID/Date/Home_Team_ID/Away_Team_ID/Home_Team_Name/Away_Team_Name/home_score/away_score/status/Referee/Match_Round/Temperature/Wind_Speed/Who_Lost_On_Penalties(共同列全集,`kickoff_at_utc` 是 core 独有列,已在 schema 差异里单独报告,不算进内容比较,也**不因此把整表判 SCHEMA_INCOMPATIBLE**);三张含 extra_json 的事实表(`fact_team_match_stats`/`fact_match_events`/`fact_match_lineup`)的 EXACT 结论现在真正意味着 extra_json 原文与语义都一致。

**这一结论已经用 12 条 mutation 反例证明审计器有能力检出破坏**(见 [test_audit_fixture.py](../../analysis/data_quality/test_audit_fixture.py) `test_mutation_01`–`12`):只改 `home_score`/`Home_Team_ID` 必判 CONTENT_MISMATCH;只改 `extra_json` 数值必判 CONTENT_MISMATCH;仅键顺序/空白变化必判 SEMANTICALLY_EQUAL(不得 EXACT);合法→非法 JSON、NULL→`{}`均必判 CONTENT_MISMATCH;干净数据必判 EXACT。

`dim_player` 是全局表(allwin 还含 EPL 等其它联赛球员),采用专门比对逻辑:先用四联赛事实表(`fact_match_lineup ∪ fact_player_match_stats`)实际引用的 Player_ID 集合限定范围,再剔除"verify 自己的事实表引用、但连 verify 自己 dim_player 都没有"的孤儿引用(源数据既有缺陷,单独统计,不计入合并遗漏)。21/24 分区判 CONTENT_MISMATCH,逐条差异分类(按分区计数,同一现实球员跨多个赛季会被计多次):

| 差异类型 | 出现次数(按分区) |
|---|---:|
| ACCENT_NORMALIZATION_ONLY(Unicode 重音规范化,如 "Luis Diaz"→"Luis Díaz") | 34 |
| TRULY_DIFFERENT_NAME(真实姓名形式变化,如 "Iyenoma Udogie"→"Destiny Udogie"、"Florentino Luis"→"Florentino") | 18 |
| CASE_OR_WHITESPACE_ONLY | 0 |
| WORD_ORDER_ONLY | 0 |
| missing_from_allwin | 0 |
| name_blank_in_allwin | 0 |

**"均为重音修复"是不准确的表述**——真实存在的姓名形式变化(昵称/全名互换等)占比不小,但全部 21 个分区都还是**同一个真实球员**,没有出现丢失、清空或跨球员冲突,数据完整性安全,只是 allwin 一侧的姓名文本更新更及时。

**唯一球员计数(v3 新增,避免"分区出现次数"被误读成不同球员数)**:上表的 34/18 是**按分区计的出现次数**——同一现实球员在多个赛季各出现一次差异会被计多次。去重到 Player_ID 后,`dim_player_name_diff_summary()` 独立计算得到:

| 口径 | 数量 |
|---|---:|
| 唯一 Player_ID 总数 | **20** |
| 其中仅 Unicode 重音/大小写/词序规范化(良性) | **13** |
| 其中含真实姓名形式变化 | **7** |
| 跨 ≥2 个分区重复出现 | **14** |
| 同一 Player_ID 对应多个不兼容 core 姓名(冲突) | **0** |
| missing_from_allwin / blank_in_allwin | **0 / 0** |

该结果与 `top5_quality_summary.json.dim_player_name_differences` 一致,由脚本对 `dim_player_parity_rows()` 已产出的分区级差异重新按 Player_ID 聚合得到,不是从复核报告誊抄的数字。

---

## 3. Schema 对齐

7 张表 schema 全部 `compatible=True`(无 verify 独有列、无类型不匹配、无列序差异)。唯一差异:`dim_match.kickoff_at_utc` 仅存在于 core(verify_leagues.db 生成早于 CLAUDE.md §6.2.1 精确开球时间改造)。这是预期内、有文档依据的差异,不是缺陷。

---

## 4. 联赛-赛季覆盖矩阵(`top5_table_coverage.csv`/`top5_partition_summary.csv`)

30 个分区(5 联赛 × 6 赛季)全部有数据,`match_count_vs_round_robin_delta` 除 SerieA 2022/2023(+1,已在第 1 节解释)外全部为 0。半场拆分能力(团队统计 `Period IN (FirstHalf,SecondHalf)`)的按季演进(以 `fact_team_match_stats` 每场行数中位数体现,6 行=含半场拆分,2 行=仅 All):

| 赛季 | p50 rows/match(5 联赛一致) |
|---|---|
| 2020/2021 – 2022/2023 | 2(仅 `Period='All'`,少量分区 p99 已达 6,说明该赛季中途开始零星提供) |
| 2023/2024 – 2025/2026 | 6(全量含半场拆分) |

该分界线**跨全部 5 联赛同步出现**(含从未经过合并的 EPL),证明是上游 FotMob 的统一能力升级,不是四个合入联赛的合并缺陷。

---

## 5. 关键字段缺失趋势(精确复算)

### 5.1 xGOT:on-target 结构性零填充(三层证据口径,v3.1 修正内部矛盾)

按正确的 on-target 分母(`Outcome IN ('Goal','AttemptSaved')`,已通过 `FieldSpec.applicable_sql` 修正)重新统计,5 联赛合并口径:

| 赛季 | on-target 总数 | NULL | =0 | >0 |
|---|---:|---:|---:|---:|
| 2020/2021 | 26,055 | 42.5% | 0.0%(1 行) | **57.5%** |
| 2023/2024 | 28,345 | 44.7% | 0.3% | **55.0%** |
| 2024/2025 | 27,264 | 0.5% | 44.8% | **54.7%** |
| 2025/2026 | 27,191 | 0.4% | 45.4% | **54.2%** |

**v2 曾把 2020/2021 那 1 行字面 0 直接称为"真实测量"/"真实 0",同时又说"无法逐行区分某个具体 0 是真实值还是填充值"——这两句话不能同时成立,是 v2 报告的内部矛盾。v3.1 改为三层证据口径,严格区分"已验证的事实"“可以做出的统计推断”与"不能声称的内容":**

**1)已验证的事实**(可从 CSV/SQL 直接复算,已用只读脚本核实):
- off-target 射门的 xGOT 不适用(NOT_APPLICABLE)。
- on-target 子集中,2024/25 起 NULL 大规模转为字面 0(NULL 占比从 42.5%-44.7% 降到 0.4%-0.5%,=0 占比从 0%-0.3% 升到 44.8%-45.4%)。
- `xGOT>0` 的比例在各赛季间大致稳定在 54%-58%,没有随这次切换发生明显变化。
- 2020/2021 的 on-target 子集中,26,055 行里已经存在 **1 行字面 xGOT=0**(该行本身早于 2024/25 断点,不属于本次讨论的"结构性零填充"批次)。

**2)可以做出的统计推断**(分布层面,不是逐行标签):
- 上述分布**强烈支持**"2024/25 起来源对 on-target 射门发生了 NULL→0 的编码制度切换"这一解释。
- **不能把覆盖率上升(NULL 占比下降)解释为新增测量能力**——因为 `xGOT>0` 的真实测量比例没有随之同步增长,只是 NULL 换了个字面值。

**3)不能声称的内容**(v2 的错误,本轮已删除):
- **不能说某一个具体的 0 已被逐行证明为"真实测量"**——2020/2021 那 1 行字面 0 的业务语义(它究竟是"真实的极小 xGOT"还是"某种早期填充/边界情况")本身**未被逐行验证**;它唯一能说明的是"不能仅凭'数值等于 0 + 属于哪个赛季'就把一行机械分类为填充值",不能反过来证明它一定是真实测量。
- **不能说所有 xGOT=0 都是填充值**(2024/25 前也存在极少量 0,不应被"0=填充"的规则一律清空或标记)。
- **不能说所有 NULL 与所有 0 完全等价**(NULL 与 0 是两种不同的原始存储状态,只是在 2024/25 断点前后发生了系统性的相互转换,不代表二者在任意上下文里可以互换)。

**跨断点训练规则**:2020/21 on-target 子集中已存在 1 条字面 0,但该行的业务语义未被逐行验证,因此它只能说明"不能仅凭数值 0 和赛季作绝对分类",不能证明该值一定是真实测量。2024/25 起 NULL 大规模转为 0、同时 `xGOT>0` 比例基本稳定,强烈支持来源发生了结构性零填充/编码制度切换。跨断点训练时,若无法可靠区分填充 0 与有效 0,应排除原始 xGOT 数值,或仅在经过单独业务验证后使用稳定派生特征。`xGOT>0` 可以保留为候选派生指标(其"是否为正"这一状态相对稳定),**但不构成"有预测价值"的证明**——是否真的对下游任务有用,需要经过独立的模型/业务验证后才能使用,不应把"分布稳定"和"有预测价值"混为一谈。

**现网独立复现确认**(第 9 节,4/4 场真实抓取):今天用现有 FotMob 客户端重新抓取 2021/22 的旧比赛,off-target 射门 xGOT 依然是 100% NULL——不是当年没抓到,是来源今天仍然不提供;重新采集恢复不了这类历史缺口。这一结论基于结构层面的比对(Periods 键集合、NULL/0 计数),同样不涉及对任何单条 xGOT 数值的逐行语义判定。

### 5.2 半场拆分:2023/24 起稳定,更早赛季不可回填

同上,`Periods` 键集合从 `['All']` 变为 `['All','FirstHalf','SecondHalf']` 的分界线在 2023/24,全 5 联赛同步。现网复现确认 2021/22 旧比赛今天仍只返回 `['All']`。

### 5.3 physical_metrics_*:全量 0%,成因 UNVERIFIED

全 5 联赛全 6 赛季,6 个 `physical_metrics_*` 字段共 1,968,498 行 applicable,**0 行 non-null**。

**成因未证实为"来源从不提供"**——`backend/fotmob_client.py:867-875` 显示解析器**确实尝试解析**这 6 个字段(`g("topSpeed")`、`g("distanceCovered")` 等),但只用了 camelCase 键名猜测,**没有像其它字段那样加下划线格式别名**(对照同文件 786-790 行对其它字段的下划线修复)。这是疑似解析器 key 映射缺陷,不是证实的来源缺陷。本轮**未修改** `backend/fotmob_client.py`(超出本轮授权边界),建议后续单独做一次 payload→parser 字段映射审计。在成因确认前,`physical` family 保持 NOT_READY,不建议纳入任何训练配方。

### 5.4 shot_accuracy:确认为冗余列,不是"单位未定"

全部 328,083 行里 125,064 行 shot_accuracy 非空,其中 **125,064/125,064(100.0%)与 ShotsOnTarget 逐行相等**,取值为 0-8 的整数,0 个落在 (0,1) 区间——它是**射正次数的冗余复制列**,不是命中比例。已加入永久回归检测(`player_stats.shot_accuracy_not_redundant`,只在真正出现分歧时报警,本轮 0 次触发)。训练配方应视其为与 ShotsOnTarget 等价,不需要额外单位换算,也不需要作为独立信息量字段纳入。

### 5.5 坐标契约:105×68 米制球场,不是 0-100 归一化

全部 269,071 行 `X_Coord∈[0.38,104.91]`、`Y_Coord∈[0.20,67.87]`,0 行落在 105×68 球场之外。解析器(`backend/fotmob_client.py:560-561`)原样存储 `s.get('x')/s.get('y')`,未做缩放。已把异常检测阈值改为真正的 105×68 边界(带 0.5 容差),而不是旧版本错误假设的 0-100 归一化。若使用归一化坐标特征,应除以 105/68,不是除以 100。

### 5.6 position_id:首发/替补拆分后是结构变化,不是全体数据缺口(v3 修正)

v2 曾把 `position_id` 的下降描述为"全联赛真实数据缺口",按全体行(不分首发/替补)统计确实呈现 2024/25 起降到约 55% 的模式。**但独立复核按 `is_starter` 拆分后发现这个描述过重**——真实情况是:

**`fact_match_lineup.position_id`**(EPL,全 5 联赛同构):

| 赛季 | 首发覆盖率 | 替补覆盖率 |
|---|---:|---:|
| 2020/2021 | **100.0%**(8360/8360) | 18.8%(1180/6283) |
| 2021/2022 | **100.0%** | 27.4% |
| 2022/2023 | **100.0%** | 33.1% |
| 2023/2024 | **100.0%** | 14.9% |
| 2024/2025 | **100.0%** | **0.0%**(0/6828) |
| 2025/2026 | **100.0%** | **0.0%** |

**首发球员的 position_id 全部 5 联赛全部 6 赛季恒为 100%**,下降**全部**发生在替补席——这是来源自 2024/25 起对替补停供该字段的**结构变化**,不是"数据缺口"(未上场替补本来就没有 on-pitch 阵型槽位,NULL 是合法值)。`fact_match_lineup.usual_position_id` 全名单(不分首发/替补)覆盖率 **99.98%**(451,079/451,161),不受此结构变化影响。

**`fact_player_match_stats.position_id`**(该表只含实际出场球员,minutes_played>0 的行数为 0 例外):即使限定到出场球员,覆盖率仍逐季真实下降(EPL:84.0%→85.3%→82.3%→77.4%→72.3%→72.7%);进一步按首发/替补拆分,**首发出场球员覆盖率全程 ~99.9%-100%**,下降集中在**替补里实际登场的球员**(EPL:18.6%→27.7%→32.9%→15.0%→**0.0%→0.0%**)。`fact_player_match_stats.usual_position` 在同样出场球员范围内保持 **99.92%-100%**,是稳定的跨赛季替代字段,但**语义与 position_id 不同**——`position_id` 是这场具体比赛的阵型槽位,`usual_position` 是球员的通常位置,不能声称二者完全等价。

**v3 修正**:
- `fact_match_lineup.position_id` 的 `applicable_sql` 改为 `is_starter=1`(只对首发球员判 REQUIRED);替补席 position_id 改为独立观察指标 `position_id_bench`(`applicable_sql=is_starter=0`,`applicability=OPTIONAL`),仍在 `top5_field_coverage.csv` 里如实报告其 0% 覆盖率,但不再拖累 `standard_match` 的 REQUIRED 最低覆盖率判断。
- `fact_match_lineup.usual_position_id` 继续对全名单保持 REQUIRED。
- `fact_player_match_stats.position_id` 从 REQUIRED 降为 OPTIONAL(即使限定到出场球员仍持续下降,成因未查,不应继续拖累 readiness);`fact_player_match_stats.usual_position` 继续保持 REQUIRED。
- **训练建议**:研究首发阵型/位置类特征时优先用 `fact_match_lineup.position_id`(首发覆盖率 100%);需要覆盖全部出场球员(含替补)的跨赛季稳定位置特征时,优先用 `usual_position`/`usual_position_id`,不要用逐场 `position_id` 混合首发替补一起训练。
- **根因仍 UNVERIFIED**:替补 position_id 与 `fact_player_match_stats.position_id` 为何逐年下降(是否为上游 API 字段迁移、还是解析器未跟上来源结构变化)本轮未深入代码定位,标记为独立后续调查项。

---

## 6. 异常与跨表一致性(`top5_anomalies.csv`,158 条,已按 League×Season 分区)

严重度分布:CRITICAL 0、HIGH 4、MEDIUM 2、LOW 152。全部异常携带 `league_id`/`season`(`dim_player_anomalies` 除外——`dim_player` 本身是全局表,没有联赛/赛季维度,保留全局输出)。

**HIGH(4 条,全部同一类别 `fact.orphan_player_id`)——是 2 个真实问题各在 core/verify 出现一次,不是 4 个独立问题**:

| 联赛 | 赛季 | 库 | 行占比 | 唯一球员占比 | 首发孤儿数 | 有出场分钟数据 |
|---|---|---|---:|---:|---:|---:|
| Ligue1 | 2022/2023 | core | 2.22% | 10.28%(71/691) | 2 | 0 |
| Ligue1 | 2022/2023 | verify | 2.25% | 10.42%(72/691) | 2 | 0 |
| LaLiga | 2020/2021 | core | 3.25% | 12.68%(89/702) | 2 | 0 |
| LaLiga | 2020/2021 | verify | 3.25% | 12.68%(89/702) | 2 | 0 |

同一分区在 core 和 verify 里各产出一条 Finding,是审计器"在两个库上各跑一遍同一套规则"的设计使然,**用于交叉验证问题是否由本轮合并引入**(core/verify 数字几乎相同 → 证明是源数据既有问题,与合并无关)——读者不应把"4 条 HIGH"理解为 4 个互相独立的底层缺陷,实质是 **2 个真实分区问题**。这 4 名孤儿首发球员经查**均无 `fact_player_match_stats` 行、`minutes_played` 或 `fact_match_events` 记录**——HIGH 严重度反映的是**参照完整性**标准(球员名无法展示、阵容名册不完整),不代表这些球员污染了任何统计/训练特征(因为压根没有可被污染的统计行)。对模型训练风险为 **LOW**,对阵容名册/展示完整性有真实但局部的影响。

**其余 30 个含孤儿 Player_ID 的分区已判 LOW**——经修复后的逐分区核实,那些分区里的孤儿全部是从未在 `fact_player_match_stats` 里出现 `minutes_played>0` 的替补名单,建模影响可忽略。全量 `fact_player_match_stats` 的 Player_ID 孤儿率为 **0/328,083(0.00%)**。

**MEDIUM(2 条)**:`fact_match_events` 完赛比赛零事件 1 场;`fact_match_lineup.sub_in_time` 晚于 `sub_out_time` 1 例。均为孤立个案,已给出代表性 Match_ID。

**LOW(152 条)**:主要是 `dim_match.kickoff_provenance_note`(30,精确开球时刻覆盖率,时间元数据限制非比赛数据错误)、`team_stats.team_xg_vs_shotmap_xg_delta`(30,球队级与射门级 xG 求和差值分布,p50 常年 ≈0.003)、`shotmap.per_match_extreme`(28,每场射门数极端值,供人工参考)、`lineup.captain_count_not_one`(14,队长数≠1,可能是臂章转移未捕获)。

### 6.1 乌龙球对账(逐场验证,不是同步性推断)

player-goals 路径(球员进球汇总 vs `dim_match` 比分)在 5 联赛 10,735 场完赛比赛里有 **876 场**不符;逐场验证"主队比分 = 主队球员进球 + 客队乌龙球"(且反向对称)后,**873 场(99.7%)被乌龙球精确解释**,3 场真实未解释:

- SerieA 2020/2021:Match_ID **3428775**
- LaLiga 2023/2024:Match_ID **4205412**
- Bundesliga 2024/2025:Match_ID **4534664**

这 3 场是真实的、独立的待查数据缺陷,不应笼统归入"乌龙球已完全解释"。

---

## 7. 与 EPL 的对照

EPL(47)未经过 verify_leagues 合并,是同源同赛季参照系。第 4-5 节的所有关键趋势(半场拆分分界线、xGOT NULL→0 切换、physical 全 0%、position_id 2024/25 起下降)在 EPL 与四个合入联赛之间**完全同步**,证明这些都是上游 FotMob 侧的统一现象,不是四个合入联赛特有的合并缺陷或采集质量差异。`shot_xg`/`goalkeeper_advanced` 两个 family 的 REQUIRED 覆盖率在 EPL 与四个合入联赛之间差距在 1-2 个百分点以内(见 `top5_field_coverage.csv` 的 `vs_benchmark_epl_same_season_delta_pct` 列)。

---

## 8. 模型可用性(`top5_feature_readiness.csv`,210 行 = 5 联赛 × 6 赛季 × 7 family)

修复后的 readiness 引擎按 League×Season×family 逐分区判定,`covered_match_count` 用每个 family 的锚点字段(如 `shot_xg`→`fact_shotmap.xG`,`standard_match`→`fact_match_lineup.is_starter`)在**同一** League×Season 下的覆盖率,不再跨赛季求和;readiness 判定只看 REQUIRED 字段(条件性/次要字段已标 CONDITIONAL/OPTIONAL,不拖累判断)。covered_match_count 反映的是"该 family 在这场比赛是否存在基础数据"(锚点字段口径),**不是"该 family 全部字段同时齐全的比赛数"**——字段级完整度仍须查 `minimum_required_field_coverage_pct` 和 `top5_field_coverage.csv`:

| feature_family | 判定模式(5 联赛 6 赛季,v3 数字) |
|---|---|
| `shot_xg` | 全部 30 分区 READY(REQUIRED 最低覆盖率 95.8%-99.7%) |
| `goalkeeper_advanced` | 全部 30 分区 READY(95.3%-98.7%) |
| `defensive_duel_passing` | 全部 30 分区 READY_WITH_FILTERS(66.7%-74.7% 区间,ground_duels_won/duel_won/duel_lost/passes_into_final_third 等中高覆盖字段决定下限) |
| `standard_match` | **v3 修正后 0 个分区 NOT_READY**(12 分区 READY、18 分区 READY_WITH_FILTERS,65.0%-92.8% 区间;`position_id` 已限定首发适用,不再因替补合法缺失被误判,见 §5.6) |
| `player_attacking_xg_xa` | 无 REQUIRED 字段(全部字段均为事件条件性,已用真实数据确认 51%-82% 触发率取决于球员是否射门/关键传球),以整体均值参考,29 分区 READY_WITH_FILTERS、1 分区(LaLiga 2020/21,59.2%)NOT_READY——与本轮修改无关的既有边界情况 |
| `context` | 同上,无统一锚点(动态发现的次要统计集合),以整体均值参考,全部 30 分区 READY_WITH_FILTERS |
| `physical` | 全部 30 分区 NOT_READY(0% 确认,成因 UNVERIFIED) |

**修正前后对比(standard_match)**:v2(position_id 全体行 REQUIRED)—— EPL/Ligue1 2024/25-2025/26、Bundesliga 2020/21+2021/22+2024/25+2025/26、SerieA 2023/24-2025/26、LaLiga 2020/21+2023/24-2025/26 共 **约半数分区 NOT_READY**(min_req 47%-58%,均由替补 position_id 拖累)。v3(position_id 限定首发)—— **全部 30 分区不再是 NOT_READY**,min_req 由其它真正 REQUIRED 字段决定(最低分区 Bundesliga 2022/2023,由 `fact_match_lineup.country_code` 覆盖率 65.0095% 决定;次低 Ligue1 2022/2023 为 65.0209%),分布为 65.0%-92.8%。此变化完全由 `top5_feature_readiness.csv` 重新生成后的真实数字驱动,不是先射箭再画靶。

**方法论边界(§六)**:`defensive_duel_passing`/`shot_xg` 等 family 里部分 REQUIRED/OPTIONAL 边界(如 `accurate_crosses`/`long_balls_accurate` 的取舍)同时参考了足球语义(是否所有球员都会发生该事件)与实测覆盖模式,不是纯语义推导——这一分类方式不改变本轮 merge parity 结论,但意味着 **family readiness 是训练数据筛选的入口关卡,不是对 family 内每个具体字段的自动批准**。若后续模型实际采用某个具体字段,应按该字段自己的 `top5_field_coverage.csv` 行(含 `applicability`/`non_null_rate_pct`/`vs_prev_season_same_league_delta_pct`)逐字段复核,不能仅因所属 family 判 READY 就默认该字段可直接入模。

### 三套训练数据集配方(基于修复后 readiness)

**配方 A:五联赛最大公共覆盖集**
- 联赛:全部 5 个;赛季:全部 6 个
- 字段族:`shot_xg` + `goalkeeper_advanced`(全程 READY)
- 强制过滤:排除 `physical`;`xGOT` 按第 5.1 节规则处理(off-target 恒 NOT_APPLICABLE,on-target 2024/25 起的字面 0 视为结构性填充、不当测得值;如无法可靠区分则只用 `xGOT>0` 派生特征,不直接喂数值)
- 偏差风险:半场拆分特征在 2023/24 前不可用,若混用需显式标记 `has_half_split` flag

**配方 B:高阶 xG 模型可用集**
- 联赛:全部 5 个;赛季:2023/24-2025/26(半场拆分 + xGOT 双重稳定窗口)
- 字段族:`shot_xg` + `defensive_duel_passing` + `standard_match` 的 REQUIRED 子集(`position_id` 若用于首发阵型特征可直接使用,首发覆盖率 100%;若需覆盖替补/全出场名单,改用 `usual_position`/`usual_position_id`,见 §5.6)
- 偏差风险:xGOT 数值层面(>0 比例)本身没有随窗口改变而提升,不要预期该窗口内特征"更准",只是"更完整"

**配方 C:单联赛最大字段集**
- 逐联赛选择该联赛 REQUIRED 覆盖率最高的赛季区间(`standard_match`/`defensive_duel_passing` 修正后已无 NOT_READY 分区,可参考 `top5_feature_readiness.csv` 的 `minimum_required_field_coverage_pct` 逐联赛逐赛季排序选取)
- 排除 `physical`、`shot_accuracy`(与 ShotsOnTarget 冗余,不提供额外信息量)
- 偏差风险:样本量小于配方 A/B,不建议用于跨联赛泛化评估

---

## 9. FotMob 现网独立复现(只读)

用现有 `backend/fotmob_client.py`(未修改)只读抓取 4 场真实比赛(2 场 2021/2022 + 2 场 2024/2025,跨 EPL/SerieA 两个联赛),只记录结构信号,未落盘完整 payload,未输出任何代理/凭证配置:

| Match_ID | 联赛 | 赛季 | Periods keys | off-target xGOT NULL | off-target xGOT =0 |
|---|---|---|---|---:|---:|
| 3609929 | EPL | 2021/2022 | `['All']` | 19/19 | 0/19 |
| 3656991 | SerieA | 2021/2022 | `['All']` | 7/7 | 0/7 |
| 4506263 | EPL | 2024/2025 | `['All','FirstHalf','SecondHalf']` | 0/11 | 11/11 |
| 4535244 | SerieA | 2024/2025 | `['All','FirstHalf','SecondHalf']` | 0/8 | 8/8 |

4/4 场抓取成功(首次尝试 SerieA 2024/2025 一度遇到瞬时 TLS 连接错误,重试即成功,不影响结论)。**结论确认**:2021/22 的旧比赛用现网今天重新抓取,依然只有 `Periods=['All']`、越靶射门 xGOT 依然 100% NULL——上游来源没有对旧赛季回填,重新采集这两类字段收益为零。`physical_metrics_*` 本轮**未**在现网复现范围内单独验证(超出本轮授权,成因保持 UNVERIFIED)。

---

## 10. 需要重新采集/修复的分区

**无**。已识别的全部缺口(半场拆分、xGOT 扩展)均已用现网复现证实为上游历史限制,不可通过重新采集恢复。`physical_metrics_*` 的成因未证实,但即使证实为解析器缺陷,修复方式也是"改代码重跑历史 payload 缓存或重新解析",而不是"重新抓取网络数据"——本轮不判定为需要重新采集。替补席 `position_id`/`fact_player_match_stats.position_id` 的下降(§5.6,首发不受影响)建议作为独立调查项,暂不判定需要重新采集或修复,因缺乏根因证据(可能是上游字段迁移,也可能是解析器未跟上来源结构变化)。

---

## 11. 建议的自动化数据质量闸门(仅提议,本轮不接入 CI)

**必须 100% 不变式**(违反即 CI 失败):
- 两个源库读取前后 SHA-256/size/mtime 不变(证明只读)
- `dim_match`/事实表主键候选组合无重复(0 容忍)
- 正式预测锁定后不可 UPDATE(见 CLAUDE.md §9.1,不属于本轮范围但同一原则)
- 免费 DTO 不含受限概率字段(见 CLAUDE.md §8.2,同上)

**允许小幅异常但需告警**(阈值示例,非机械规则):
- `fact.orphan_player_id` 唯一球员占比 > 20% 且含 ≥1 名首发/有出场分钟数据的孤儿 → 告警
- `team_stats.team_xg_vs_shotmap_xg_delta` p95 > 0.5 → 告警
- 任一 REQUIRED 字段单赛季环比下降 > 15 个百分点 → 告警,人工复核是否为采集回归(区分"REQUIRED 字段真实下降"与"该字段本身应改判 CONDITIONAL/OPTIONAL"——`fact_player_match_stats.position_id` 逐季下降正是本轮从 REQUIRED 改判 OPTIONAL 的真实案例)
- 任一 OPTIONAL 观察指标(如 `position_id_bench`)骤降至 0% 且此前非零 → 告警,可能是上游结构性字段停供(如本轮发现的替补 position_id 2024/25 起归零),需人工判断是否需要新增替代字段而非机械追修旧字段

**仅人工观察的漂移信号**:
- `context` family 新发现的 extra_json key(可能是上游新增统计维度)
- `physical_metrics_*` 若未来某天出现非零值(可能预示上游开放了该数据)

---

## 12. 假设、局限与未验证事项

- **`shot_accuracy` 的确切业务含义**:已确认数值上等于 ShotsOnTarget,但未在 FotMob 官方文档里找到该字段设计初衷的一手说明,标记 CONFIRMED_REDUNDANT_BUT_ORIGIN_UNVERIFIED。
- **`physical_metrics_*` 全 0% 的根因**:已证实"解析器确实尝试解析但只用了 camelCase 键名,没有下划线别名兜底",但未证实这就是唯一/确切原因,也未验证如果加上下划线别名是否真的能解析出数据——UNVERIFIED,本轮未修改 `backend/fotmob_client.py`。
- **替补席 `position_id`/`fact_player_match_stats.position_id` 逐季下降的根因**:UNVERIFIED。本轮已确认现象的精确边界(首发全程 100% 不受影响,下降只发生在替补/替补上场球员,`usual_position_id`/`usual_position` 全程稳定可替代),并已把该字段的 REQUIRED/OPTIONAL 分类改到与这一现象一致,但**尚未定位**是上游 API 结构变更、还是 `backend/fotmob_client.py` 解析器未跟上来源变化——本轮未修改该解析器,标记为独立后续调查项。
- **`xGOT` 结构性零填充的逐行区分**:UNVERIFIED。已从赛季断点统计层面证实 2024/25 起 on-target 射门发生 NULL→0 的转换、且真实测得(>0)比例未变,但**无法逐行证明某一具体 0 值是"真实极小 xGOT"还是"填充值"**——第 5.1 节给出的是统计层面的处理规则,不是逐行可验证的标签。
- **`shot_accuracy` 的确切业务含义**:已确认数值上等于 ShotsOnTarget,但未在 FotMob 官方文档里找到该字段设计初衷的一手说明,标记 CONFIRMED_REDUNDANT_BUT_ORIGIN_UNVERIFIED。
- **`physical_metrics_*` 全 0% 的根因**:已证实"解析器确实尝试解析但只用了 camelCase 键名,没有下划线别名兜底",但未证实这就是唯一/确切原因,也未验证如果加上下划线别名是否真的能解析出数据——UNVERIFIED,本轮未修改 `backend/fotmob_client.py`。
- **`dim_player` 姓名差异的完整原始来源**:已用 Unicode 规范化 + 词序比较分类出 accent/truly-different 两类,并已按唯一 Player_ID 去重(20 名,13 accent-only、7 truly-different),但未逐一联系数据来源核实"真实姓名变化"是否反映球员真实改名/转会俱乐部更新等具体业务事件。
- **`fact_shotmap` 3 组候选键碰撞**在 core 与 verify 里数值完全一致,已解释为真实的"射正被扑+回追补射"场景,但未逐场对照原始视频/官方数据源做最终确认。
- **`defensive_duel_passing`/`shot_xg` 等 family 内部分 REQUIRED/OPTIONAL 边界**(如 `accurate_crosses` vs `long_balls_accurate`)同时参考了足球语义与实测覆盖模式,不是纯粹的先验业务规则——不改变本轮 merge parity 结论,但意味着这些边界本身是一种审计设计选择,后续若有不同业务判断应可复核调整(见第 8 节"方法论边界")。
- **备份/恢复演练、生产环境冒烟**等 CLAUDE.md §14/§15 相关条款不在本轮范围内(本轮是纯离线只读数据质量审计,不涉及 Nginx/systemd/Cloudflare)。

---

## 最终确认(v3 窄收口轮)

**Verdict:DONE**

本轮范围严格限定为 v2 独立复核指出的两处 caveat + dim_player 唯一计数 + 测试补强,**未重新触碰 merge-parity 引擎、readiness 粒度框架、条件字段分母、异常分区逻辑**(这些模块本身在本轮前后逐字节相同,见下方"未变化的产物")。

### 修改文件

| 文件 | 本轮改动 |
|---|---|
| `analysis/data_quality/audit_top5_historical_core.py` | ① `_lineup_field_specs()`:`fact_match_lineup.position_id` 加 `applicable_sql="is_starter=1"`;新增观察指标 `position_id_bench`(`applicable_sql="is_starter=0"`,`applicability=OPTIONAL`);② `_player_field_specs()`:`fact_player_match_stats.position_id` 从 REQUIRED 改 OPTIONAL;③ 新增 `dim_player_partition_occurrences()`、`dim_player_name_diff_summary()` 并接入 `main()`,写入 `summary["dim_player_name_differences"]`;④ `audit_method_version`/`output_schema_version` 由 2 升至 3 |
| `analysis/data_quality/test_audit_fixture.py` | 新增 11 条测试(45→56):7 条 position_id 适用性反例、1 条 dim_player 唯一计数跨赛季去重、3 条 schema contract/method version 契约 |
| `analysis/data_quality/output/*.csv`+`*.json`(7 个) | 全部重新生成;其中 `top5_anomalies.csv`/`top5_partition_summary.csv`/`top5_table_coverage.csv`/`verify_merge_parity.csv` 4 个文件因本轮未改动其对应模块,**哈希与 v2 完全相同**;`top5_feature_readiness.csv`/`top5_field_coverage.csv`/`top5_quality_summary.json` 3 个文件因 position_id 分类调整与新增 JSON 字段而哈希变化 |
| 本报告 | xGOT 训练措辞、position_id 前后对比、dim_player 唯一计数、HIGH 严重度说明、方法论边界说明 |

**未修改**:`data/allwin.db`、`data/verify_leagues.db`、`backend/`(含 `backend/fotmob_client.py`)、`frontend/`、任何生产 migration、模型代码。

### position_id 修正前后 readiness 变化

| | v2(position_id 全体行 REQUIRED) | v3(position_id 限定 `is_starter=1` REQUIRED) |
|---|---|---|
| `standard_match` REQUIRED 字段数 | 19 | 18(`fact_player_match_stats.position_id` 移出) |
| `standard_match` NOT_READY 分区数(30 分区) | ~15(约半数,min_req 47%-58%) | **0** |
| `standard_match` min_req 分布 | 47.0%-92.8% | 65.0%-92.8% |
| `defensive_duel_passing`/`shot_xg`/`goalkeeper_advanced`/`context`/`player_attacking_xg_xa`/`physical` | 不受影响 | 不受影响(数字与 v2 一致或仅有无关的取整差异) |

详见第 5.6/8 节的完整分季数据。

### xGOT 最终训练规则(v3.1,三层证据口径)

- **已验证事实**:off-target 射门的 xGOT 不适用(NOT_APPLICABLE);on-target 子集里 2024/25 起 NULL 大规模转为字面 0;`xGOT>0` 比例各赛季间大致稳定在 54%-58%;2020/2021 on-target 子集(26,055 行)里已存在 1 行字面 0,早于本次讨论的断点。
- **分布层推断**(不是逐行标签验证):上述分布强烈支持"2024/25 起来源发生了 NULL→0 编码制度切换"这一解释;正例比例基本持平,与"真实测量能力未明显增加"的解释一致,并强烈支持编码制度发生变化——这是分布层面的推断,不是逐行标签验证。
- **不能声称的内容**:不能说某个具体的 0(含 2020/2021 那 1 行)已被逐行证明是"真实测量";不能说所有 xGOT=0 都是填充值;不能说 NULL 与 0 在任意上下文里完全等价。
- **训练规则**:若无法可靠区分填充 0 与有效 0,应从跨断点统一模型中排除原始 xGOT 数值,或仅在经过单独业务验证后使用 `xGOT>0` 等稳定派生特征——`xGOT>0` 的"稳定"不等于"有预测价值",是否真的对下游任务有用需经独立模型/业务验证。

### dim_player 分区次数与唯一计数(脚本直接计算,见 `top5_quality_summary.json.dim_player_name_differences`)

- 分区出现次数:accent-only 34、truly-different 18(同一现实球员跨赛季重复计数)。
- 唯一 Player_ID:总计 **20**(accent-only 13、含真实姓名变化 7),跨 ≥2 分区重复出现 **14** 名,冲突(同一 ID 对应多个不兼容姓名)**0**,missing **0**,blank **0**。

### 测试与确定性

- `.venv/bin/python -m pytest analysis/data_quality/test_audit_fixture.py -q` → **56 passed**(0 skip/xfail)。
- 新增 7 条 position_id 反例(starter 缺失拉低 REQUIRED 覆盖率、bench 缺失不拖累、bench 观察指标如实报告 0%、usual_position_id 全名单 REQUIRED、player_stats.position_id 不再是 REQUIRED、standard_match 不再因替补 position_id 误判 NOT_READY、真实 REQUIRED 缺口仍能正确降级)全部通过。
- 真实数据库连续跑 **3 次**(两次到独立 `/tmp` 目录 + 一次与已提交 `analysis/data_quality/output/` 比对),7 个输出文件三次逐字节相同。
- `python -m compileall analysis/` 通过;`git diff --check` 通过,无空白符错误。
- `merge parity` 仍为 **144/144 EXACT**(6 张分区表 ×24 分区),`dim_player` 仍 3/24 EXACT + 21/24 CONTENT_MISMATCH——本轮未改动该模块,数字与 v2 完全一致。
- `readiness` 仍为 **210 行**(5×6×7),唯一键(league_id+season+feature_family)无重复,`match_coverage_rate_pct` 全部在 [0,100]。
- `anomalies` 数量与 severity **与 v2 完全一致**(158 条,CRITICAL 0/HIGH 4/MEDIUM 2/LOW 152)——本轮未改动异常检测逻辑,唯一变化是报告措辞更清晰地说明了 4 条 HIGH 的真实含义(第 6 节)。

### 输出 method version 与 7 个文件哈希

`audit_method_version=3`,`output_schema_version=3`(见 `top5_quality_summary.json`)。

| 文件 | SHA-256(前 8 位…后 8 位) | 相对 v2 |
|---|---|---|
| `top5_anomalies.csv` | `a8e66661…32a0a0b6` | 相同 |
| `top5_feature_readiness.csv` | `1476da44…caf9878b` | **已变化**(position_id 分类调整) |
| `top5_field_coverage.csv` | `71f034a8…9b6751eb` | **已变化**(新增 `position_id_bench` 观察指标行、`position_id` 适用性调整) |
| `top5_partition_summary.csv` | `01a6cb47…e07879f8` | 相同 |
| `top5_quality_summary.json` | `8f21eda8…1c3495b8` | **已变化**(新增 `dim_player_name_differences`、method version 升至 3) |
| `top5_table_coverage.csv` | `5616259f…c3677ddf` | 相同 |
| `verify_merge_parity.csv` | `8ef02bf4…9a002bba` | 相同 |

完整 64 位哈希以 `shasum -a 256 analysis/data_quality/output/*` 重新计算结果为权威来源,此处仅截断展示避免排版换行错位。

### 源数据库前后完整性

| | SHA-256 | size | mtime | WAL/SHM |
|---|---|---|---|---|
| `allwin.db` 前 | `92a6a39c…00ab364e` | 406,073,344 | 2026-07-19T23:21:06 | 无(已 checkpoint) |
| `allwin.db` 后 | `92a6a39c…00ab364e`(相同) | 406,073,344 | 2026-07-19T23:21:06 | 无 |
| `verify_leagues.db` 前 | `603163b5…940a19c0` | 305,598,464 | 2026-07-11T16:46:03 | 无 |
| `verify_leagues.db` 后 | `603163b5…940a19c0`(相同) | 305,598,464 | 2026-07-11T16:46:03 | 无 |

### 其它确认

- **无数据库写入**:所有连接均为 `mode=ro&immutable=1`,所有注入实验只在 `/tmp` 副本上进行。
- **无 backend/frontend 修改**:`git status` 里这两个目录下的既有改动(与本轮无关的历史未提交工作)未被触碰。
- **无 commit/push/tag/deploy**。
- **dirty worktree 未清理**:会话开始时已存在的未提交改动原样保留。

**未验证事项汇总**(见第 12 节详述):`shot_accuracy` 字段设计初衷、`physical_metrics_*` 根因、替补 `position_id`/`fact_player_match_stats.position_id` 下降根因、xGOT 结构性零填充的逐行区分、`dim_player` 姓名差异的具体业务来源、`fact_shotmap` 3 组候选键碰撞的最终视频级确认、`defensive_duel_passing`/`shot_xg` 部分字段边界的业务复核。以上均已在正文和最终确认中明确标记 UNVERIFIED,未被笼统包装为已验证结论。本轮 position_id 分类修改后重新生成的 readiness 分布(0 个 `standard_match` NOT_READY)与预期完全一致、无法解释的新异常,故最终判定为 **DONE**,不降级为 `DONE WITH CAVEATS`。
