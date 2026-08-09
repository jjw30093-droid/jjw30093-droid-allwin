# 多联赛 point-in-time 建模与市场基线研究 v1

Date: 2026-08-07(初版);对抗性复核收口:2026-08-07
Task: MULTI_LEAGUE_POINT_IN_TIME_MODEL_AND_MARKET_BENCHMARK_V1
Runtime evidence: `runtime/research/multileague-model-v1/`(gitignored;
coverage.json / coverage.md / dataset_manifest.json / manifest.json /
fold_metrics.json / paired_market_metrics.json / feature_ablation.json /
dc_replication.json / market_baseline_report.json /
adversarial-{pit,calibration,paired,latest,ablation}.md / PROGRESS.md /
git-state.txt / doc-audit-*.md)

## 边界遵守

- 三个真实数据库全程 `mode=ro`;研究读 `/tmp/mlmodel-v1/` 的 SQLite Backup API
  一致性副本(integrity_check 全 ok);未写任何特征/预测/评估/migration 进真实库;
  未联网;未动 dirty worktree 的任何用户文件;未 commit;
  未注册/锁定任何正式预测;未改 `model_versions.applicable_league_ids`。
- 新增源码(永久,含测试):`backend/cli/data_coverage.py`、
  `backend/cli/run_multileague_research.py`(端到端 CLI,§9)、
  `backend/models/research/{pit_dataset,market_baseline,run_research}.py`、
  `tests/backend/test_{data_coverage,pit_dataset,market_baseline,run_research,build_features_league_isolation,run_multileague_research_cli}.py`;
  以及对 `build_match_features.py`/`build_wdl_baseline.py` 的三处最小修复
  (League 谓词,见 §3)。**后端测试最终 1,074 passed**(0 failed/skipped/xfailed,
  见 §10)。

## 0. 对抗性复核收口(2026-08-07,窄幅任务)

初版发布后按用户要求对六项主张(PIT/校准泄漏/配对一致性/latest≠close/
消融稳定/GO-NO_GO 可复现)做了独立对抗复核(6 个只读代理,1 个因平台
403 认证错误未完成——**不计为发现**,已用本人独立复算 + 新建可重复 CLI
替代其验证目标)。逐项结论(P0/P1/P2 分类)与全部原始复核记录见
`runtime/research/multileague-model-v1/adversarial-*.md`:

| # | 主张 | 判定 | 分类 | 处置 |
|---|---|---|---|---|
| 1 | PIT 无泄漏 | 时间泄漏**未被推翻**(0 反例);lineage_hash 覆盖不完整 | **真 P1** | 已修复(§2) |
| 2 | 校准无泄漏 | **未被推翻**(反证法+数值复算);3 处措辞/文档瑕疵 | P2 | 已在本文档/代码注释登记(§6.4) |
| 3 | 市场配对一致性 | 配对本身**未被推翻**(逐位复算);F1 summary 比较被错误门槛跳过 | **真 P1** | 已修复(§6.2) |
| 4 | summary_latest 未冒充 closing | 核心断言**未被推翻**;macauslot 标签过度延伸 | P2 | 已修正(§5) |
| 5 | 消融跨折稳定 | 数字**未被推翻**;稳定性判据本身证据薄弱、协议漂移 | P2 | 已补充合并显著性检验+登记(§6.3) |
| 6 | GO/NO_GO 可独立重算 | 子代理因平台故障未完成;本人建永久 CLI 直接解决 | — | 见 §9 |

未发现任何真 P0(无捏造数字、无真实数据泄漏、无"模型优于市场"的失实结论)。
两个真 P1 均已按"先 RED 测试后最小修复"流程闭合,详见对应章节。

## 1. 覆盖真源(阶段一)

`python -m backend.cli.data_coverage`(deterministic,复跑 sha256 一致:
`ecf4bbc0…78fd`):47 个联赛-赛季分区、15,156 场、完赛 12,785、五大联赛完赛
10,735(全部 exact kickoff)。赔率三档互斥统计与分公司/分来源明细见
coverage.json;公司标签(`281:bet 365` / `177:pinnacle` / `80:macauslot` /
live `8:Bet365`/`31:Sbobet`)与 legacy source 标签一律不合并。
docs/data-plan.md §2 手工矩阵的动态数字已落后(五大联赛现为 7 分区/联赛等),
data-plan 已追加"以本 CLI 重跑为准"的更正条目。

## 2. 严格 PIT 数据集(阶段二)

`backend/models/research/pit_dataset.py`:kickoff_at_utc 严格时刻序;同刻批次
互不可见;target 自排除;缺精确 kickoff fail-closed(实际 0 场);
Serie A 2022/23 保级附加赛(4185671)flag+默认排除;physical/xGOT 原始值/
shot_accuracy 按 TRUSTED_WITH_FILTERS 不取;每场带 input_cutoff_at 与
lineage hash。真实构建 10,734 行,复跑一致。9 条 PIT 不变量永久测试全过
(同刻不泄漏/同日按时刻/未来不进历史/跨季规则/fail-closed/确定性等)。

**2026-08-07 对抗复核发现并修复的真 P1**:独立对照实现(无 deque、每队
全量历史 + 严格 `kickoff <` 谓词)逐场逐特征复算,**0 处时间泄漏**——kickoff
严格序、同刻互不可见、target 自排除、venue/rest 只用更早比赛,全部无法
推翻。但 `lineage_hash` 只收集了 `overall`(每队 last-10 总口径)历史,
未收集 `venue`(主客场分口径)窗口的输入场——venue 窗口经常回溯到
overall-10 之外(真实数据 **10,181/10,734 行,94.8%** 受影响),这些比赛
是真实特征输入但未体现在溯源 hash 里(不是数值错误,是溯源记账不完整)。
已修复(`pit_dataset.py` 把 venue 窗口 match_id 并入 `lineage_ids`),RED
测试 `test_pit_dataset.py::TestLineageCompleteness`。修复前后**全部 10,734
行的特征值与目标值逐场零变化**(只有 lineage 记账变了),故此前跑出的
全部模型/市场/消融数字均不受影响、无需重算。

修复后 **dataset_hash = `172d4428455465ac77bff6d57fa45e170938aa08edca24d8ce49fbbbf7cda0c0`**
(旧值 `7eb86784e5056725…` 已废弃,任何引用旧值的产物均已重新生成)。

## 3. build_match_features 独立复核结论(阶段二)

- **(match_date, match_id) 排序**:当前数据无任何"同队同日两场"(SQL 全量
  验证空集),故为潜伏风险而非已触发缺陷;kickoff 纪律在研究构建器中落实。
- **真实 P0(已最小修复+永久反例测试)**:特征/训练脚本无 League 谓词,靠
  "只有 EPL 有 Period='All' stats"的隐式前提兜底,该前提已随 J1/K1/澳超与
  五大联赛全量接入失效——今天无谓词重跑会把 8 个联赛 12,785 场混入特征表、
  污染联赛基准,并把其它联赛同名赛季 gold 行连带删除。修复:
  `_load_raw/write_features/build_match_features(league_id)`、
  `load_features(WHERE imf.league_id=?)`、`write_predictions(DELETE … AND league_id=?)`,
  默认 47 保持既有行为;`test_build_features_league_isolation.py` 钉死。
- **已知未修(如实登记)**:`sample_weight` 锚点取全表最大日期(含 test 段之后),
  违反严格 PIT 且使完整路径重建不可复现——修改会使已发布 0.2143 失去可复现性,
  故生产脚本保持原样、研究管线不用该权重;正式建模若沿用需改为按 fold 的
  train 截止时刻锚定。isotonic 在 train 段 in-sample 拟合(非独立校准段)
  同样保留原样,研究管线采用 train 内时序 80/20 校准段。

## 4. Dixon-Coles 复现(问题 2)

**EXACT**。冻结 int_match_features 快照(2,280 行 EPL,2026-07-09)+ 同一
代码路径:ρ=-0.005274 ✓、test RPS(校准后)=0.2143 ✓、频率基线=0.2281 ✓
(dc_replication.json)。附注:走"重建特征表→训练"的完整路径不可复现,
原因即 §3 的锚点与多联赛混入问题——0.2143 应表述为"对 2026-07-09 冻结
特征快照可精确复现"。

## 5. 市场基线(阶段三)

口径六要素预先固化(公司集合/last-pre-kickoff 快照规则/proportional 去水/
缺失与 overround 剔除/不跨公司聚合/RPS 公式),7 条永久测试(赛前严格性/
in_play 拒入/未映射 fail-closed/公司不合并等)。真实构建:

| 基线 | 配对场次 | overround p50 | 所选快照距开球间隔(小时) | 时间证据分级 |
|---|---|---|---|---|
| pinnacle(177) | 2,155 | 1.0235 | p50=0.03 p90=0.15 p99=0.75 max=3.2 | **archive closing**(>24h 前的快照 0 场,确实贴近开球) |
| bet 365(281) | 2,153 | 1.0560 | p50=0.47 p90=2.6 p99=31.5 max=91.1 | 混合:>24h 前 28 场(1.3%) |
| macauslot(80) | 2,156 | 1.1066 | p50=1.93 p90=105.0 p99=148.5 max=463.9 | **不得称"closing"**:22.4%(482 场)在开球 24h 前、最长 19.3 天前,只是"赛前某个时点的最后一条快照" |
| summary_latest(3 来源分列) | 7,938/4,887/91 | — | 无观测时间戳 | **无时间戳,不称 closing** |

**2026-08-07 对抗复核纠正**:此前版本把三家公司的 last-pre-kickoff 快照
一律标注"archive closing",对 macauslot 是过度延伸——用真实快照-开球间隔
分布核实后,只有 pinnacle 的快照分布证明它确实贴近收盘;macauslot 的
"last snapshot before kickoff"规则选出的很多是赛前多日的孤立观测点。
§6.2 涉及"对收盘盘口"的表述只针对 Pinnacle,不适用 bet 365/macauslot。

## 6. 模型研究(阶段四)

协议预先指定:F1–F5 season-forward folds(test=2021/22…2025/26,每折
1,752–1,826 场);fold 内 train 时序 80/20 出校准段,scaler/imputer/模型只
拟合前 80%,isotonic 只拟合校准段;seed=20260807;配对 bootstrap n=2000。

### 6.1 总榜(RPS,越小越好)

| 折 | freq | dc | lr | hgb | market_summary | market_pinnacle |
|---|---|---|---|---|---|---|
| F1(21/22) | 0.2312 | 0.2159 | **0.2139** | 0.2156 | 0.1971 | 0.1893(n=205) |
| F2(22/23) | 0.2307 | 0.2099 | **0.2083** | 0.2153 | 0.1992 | 0.1922(n=206) |
| F3(23/24) | 0.2285 | 0.1999 | **0.1975** | 0.2050 | 0.1874 | — |
| F4(24/25) | 0.2330 | 0.2049 | **0.2042** | 0.2077 | 0.1937 | — |
| F5(25/26) | 0.2292 | 0.2117 | **0.2065** | 0.2140 | 0.1967 | — |

lr 为 5/5 折自有模型最优;dc 紧随;hgb 全折劣于 lr(样本量级下树模型无优势)。
分联赛 lr 五折均值:西甲 0.2004 / 意甲 0.2024 / 英超 0.2085 / 德甲 0.2092 /
法甲 0.2117。pooled(带联赛 one-hot)优于按联赛单独训练的稳定性,分联赛
指标见 fold_metrics.json per_league。

### 6.2 模型 vs 市场(完全相同配对样本,ΔRPS>0=模型更差)

**2026-08-07 对抗复核修正**:此前版本遗漏了 F1 的 summary_latest 配对比较
——旧编排代码把 dc/hgb 的市场配对错误嵌进了只有 `lr_market`(需要在配对
子集上重新训练)才需要的训练集覆盖门槛(`tr_p>=200`)里,F1 训练季
(2020/21)市场覆盖恰好为 0,导致 dc/hgb/lr 三个本可独立算出的比较被一并
跳过。已在永久编排模块(`backend/models/research/run_research.py::run_full_study`)
修复(dc/hgb/lr 只需 `paired>=100`,与需要重训练的 lr_market/lr_features
分离成两道独立门槛),RED 测试见 `test_run_research.py::TestRunFullStudyOrchestration`。
修复后**全部 29 个配对比较,26 个 CI 显著为正**(F1 pinnacle 3 个方向一致
但 CI 含 0,是唯一的非显著组;F1 新增的 3 个 summary_latest 比较**全部显著
且幅度是全表最大**:+0.018~+0.021——遗漏方向对模型更不利,不是选择性隐藏)。
修复前后,其余全部原有数值(freq/dc/lr/hgb 各折指标、market 各公司基线、
已存在的 22 个配对比较)逐位零变化,只新增了 7 个此前被错误跳过的比较。

- 全部 5 折、对全部市场基线,自有模型(dc/lr/hgb)ΔRPS 全为正(更差):
  对 summary_latest 差 +0.008~+0.021;对 Pinnacle archive-closing(仅
  F1/F2 有该基线的样本,F3–F5 无 full_timeline 覆盖)差 +0.012~+0.018,
  F1 三项 CI 含 0、F2 三项显著。
- lr+market 融合把差距缩到 +0.0025(F5)~+0.0097(F2),**仍未在任何折反超
  任何市场基线**。
- 结论表述(遵守 §2.1b):在本研究口径下,自有模型不优于市场基线;
  不得声称"模型优于收盘盘口"——且严格说该表述只对 Pinnacle 成立(§5 已
  纠正 macauslot 不构成"收盘"证据)。市场基线自此有可复现口径,UNVERIFIED
  状态在研究口径内解除(正式评估口径仍未运行,prediction_evaluations=0)。

### 6.3 变量消融(去组重训,ΔRPS>0=该组有增益)

**2026-08-07 对抗复核补充验证与措辞纠正**:原始数字(下表)逐位精确复现,
无编造。但"10 次方向一致"的表述会被误读为 10 次独立验证——实为 **5 个
独立测试季 × 2 个相关模型**,lr 与 hgb 在同一折共享同一 test 集,delta
高度相关。单折 delta 的 bootstrap 折内标准误为 0.0003–0.0020,+0.0005 级
的单折数字**大多不可独立辨识**(如 lr:drop_shots 只有 2/5 折 CI 排除 0)。
合并 5 折(互不相交的 test 集)重新检验后,shots 组在 lr(合并 +0.00187,
z=+3.51)与 hgb(+0.00230,z=+3.22)、venue 在 lr(+0.00097,z=+2.73)下
**真实显著**——结论没有被推翻,但证据强度来自合并检验,不是"逐折同号"
本身(venue-lr 的 5/5 同号里有一折仅 z=0.24,接近掷硬币)。

另两条如实登记、不改动结果:①本节最初预登记的第 6 消融组是 league(联赛
one-hot),实现从未消融过它(`add_league_onehot` 在全部 lr 变体中恒定
存在)、静默替换成了 history 组——是协议文档与实现的漂移,不是选择性
隐藏(history 组本身也如实报了"不稳定");②有 24 个 l5 口径的
shots/possession/n_matches 特征从未进入任何消融组(只消融了 l10 口径),
"shots 组稳定增益"结论只覆盖已入组的 12 个 l10/venue 特征,不代表全部
44 个数据集特征都已消融验证。

| 组 | lr meanΔ | hgb meanΔ | 单折是否稳定(旧口径) | 5 折合并显著性(新增) |
|---|---|---|---|---|
| **shots(射门/射正/控球,仅 l10)** | +0.0019 | +0.0023 | 双模型同号 | **双模型合并显著**(z=3.51/3.22) |
| venue(主客场分口径 xG) | +0.0010 | −0.0002 | 仅 lr 同号 | **仅 lr 合并显著**(z=2.73);hgb 不显著 |
| form(进球滚动) | +0.0003 | +0.0015 | 不同号 | hgb 合并 z=2.58(与 venue-lr 强度相当,但因不同号被原判据归为"不稳定") |
| xg(滚动 xG) | +0.0005 | −0.0002 | 不同号 | 不稳定(边际贡献被 form+shots 覆盖) |
| rest / history | ≈0 | ≈0 | 不稳定 |

注意:xg"不稳定"是**边际**结论(其它组在场时),不是"xG 无用"——dc 模型
只用 xG 也接近 lr,信息高度重叠。

### 6.4 校准无泄漏对抗复核(2026-08-07)

用反证法逐路径验证:同时跑"协议路径"(scaler/imputer/模型只拟合 train
前 80%,isotonic 只拟合校准段)与故意构造的"泄漏路径"(改用全部 train
含校准段拟合),F1/F5 的 `fold_metrics.json` 记录值**只与协议路径逐位一致**
——`fit_predict_dc` 的 rho(如 F1 −0.005274 vs 泄漏变体 −0.080641≠)、
`fit_predict_sklearn` 的 lr/hgb 均如此(hgb 泄漏变体因 isotonic in-sample
过拟合 RPS 剧烈跳到 0.3455,反证生产路径是干净的)。`time_split` 对乱序
输入实测仍按 kickoff 正确切分;isotonic `clip` 边界与三类退化(全 0)行的
归一化均无 NaN/越界。**核心断言未被推翻。**

三处如实登记的措辞/文档瑕疵(均不构成 test 泄漏):
1. `fit_predict_freq` 用的是完整 train(含校准段的 100%),不是本节协议
   写的"只在前 80% 拟合"——freq 本身无 isotonic/scaler,不存在"校准段
   既拟合又校准"的问题,且实测偏差方向让频率基线略强、使"lr 优于 freq"
   的结论更保守而非虚高,不影响任何方向性结论。
2. `run_research.py` 旧版 docstring 声称市场特征路径"有 assert 保护",
   实际函数体内无 assert,缺行时靠 `KeyError` 崩溃(仍是 fail-closed,
   只是文档描述与实现不符,已在模块内修正措辞)。
3. F1/F5 的 80/20 时序切点两侧存在极少数 kickoff 完全同刻的比赛,
   PIT 构建器本身保证同刻互不可见,不影响 test 泄漏结论。

## 7. 最终回答

1. **当前数据真正支持哪些联赛建模**:五大联赛(10,734 场 PIT 样本,零
   kickoff/统计缺失)。J1/K1/澳超只有覆盖统计(比赛级明细已入库但两点摘要
   赔率、无 int 特征历史),本轮不混训。
2. **DC 结果可复现吗**:可,精确复现(对冻结特征快照);完整重建路径不可
   复现,原因已定位并登记(§3/§4)。
3. **哪个候选最好**:多项逻辑回归(pooled+联赛 one-hot),5/5 折自有模型
   最优,RPS 0.1975–0.2139。
4. **是否稳定优于市场**:**否**。所有折、所有市场基线、配对样本上模型均
   更差(F2–F5 显著);加市场特征能逼近但不反超。
5. **哪些变量稳定增益**:shots 组(射门/射正/控球)双模型跨折稳定;venue
   在 lr 稳定;其余组边际不稳定。
6. **shadow prediction 资格**:见 §8。
7. **数据问题(会让正式模型不诚实的)**:sample_weight 未来锚点(§3);
   summary_latest 无时间戳(不得当收盘价,配对结论只能说"对旧资产临场摘要");
   full_timeline 仅覆盖 2020/21(全)+21/22、22/23 零星,"对 Pinnacle 收盘"
   的结论只有 F1/F2 两折小样本;xGOT/physical/半场拆分等按质量审计过滤;
   2026/27 起 J 联赛赛制切换的跨年样本尚无。

## 8. GO / NO_GO

- **MULTI_LEAGUE_POINT_IN_TIME_MODEL_RESEARCH_COMPLETE** — 严格时间验证、
  paired market benchmark、永久测试(五个研究测试文件 43 条 + 联赛隔离
  测试 4 条,本模块合计新增 47 条)、真实复算均已完成。
- **shadow prediction:有条件 GO(NO_GO on "优于市场"叙事)**。
  依据:lr 候选跨 5 折稳定优于频率基线与现行 DC 基线(F5 上 0.2065 vs
  0.2117),校准表健康;shadow 的意义是积累真实赛前样本以对照市场,而非
  声称跑赢市场。若走 shadow:用 `visibility='internal'`、永不 publish/lock
  的快照通道(现有规则天然不入公开样本),模型版本注册为新 id 并显式
  `applicable_league_ids=[47,53,54,55,87]`,权重锚点必须改为 train 截止时刻。
  **不输出 READY_FOR_OFFICIAL_PREDICTION。**

`READY_FOR_SHADOW_PREDICTION_DESIGN`(门槛:预先定义的多折校准+基线门槛
已过——lr 全折优于 freq/dc,校准桶偏差见 fold_metrics.json;市场门槛如实
未过并如实标注)。

**明确不输出**(本轮任务边界):`READY_FOR_SHADOW_PREDICTION_EXECUTION`、
`READY_FOR_OFFICIAL_PREDICTION`、`MODEL_BEATS_MARKET`。本轮只做研究收口,
未开始 shadow 实现,未扩展新模型。

**明确未做、登记为后续独立任务**(2026-08-07 收口指令末尾四项,与同一
指令"不得扩展模型、市场研究或 shadow implementation"直接冲突,本轮不
执行):修正旧 K1 lineup=null 解析;联赛集合与自然年/跨年赛季折叠规则
配置化;明确总决赛/季后赛是否进入目标样本;重新运行 J1/K1/澳超独立
指标与市场配对(不沿用五大联赛结论)。详见
`runtime/research/multileague-model-v1/PROGRESS.md`"明确未做"节。

## 9. 永久端到端研究入口(2026-08-07 窄幅收口新增)

`backend/cli/run_multileague_research.py`:一条命令从三个只读数据库完整
跑出全部产物(coverage → PIT dataset → market baseline → 五折模型研究 →
配对指标 → 消融 → manifest),不依赖任何预先存在的中间文件、不含仓库
绝对路径或固定 `/tmp` 目录:

```
python -m backend.cli.run_multileague_research \
    --core <core.db> --odds <odds.db> --platform <platform.db> \
    --output-dir <output-dir>
```

契约(12 条永久测试覆盖,`tests/backend/test_run_multileague_research_cli.py`):
只读打开三库;缺必要表 `ResearchInputError` fail closed、不产出任何文件;
写产物 manifest.json 最后一步原子写入(tmp+rename),中途异常时不出现
"看起来已完成"的 manifest(已注入异常验证:前置产物如实落盘,manifest 缺席);
产物 JSON 不含生成时间/主机名/绝对路径;相同输入原地重跑 artifact hash
逐位一致;不同绝对路径(同一份数据复制到两个不同 output-dir)业务 hash
相同;manifest 的 `dataset_hash` 直接来自 `pit_dataset.build_dataset`(即
§2 lineage 修复后的算法路径,非任何硬编码旧值);三个输入库读写前后
文件哈希与体积不变。

原 `runtime/research/multileague-model-v1/run_full_research.py`(gitignored,
此前硬编码仓库绝对路径与固定 `/tmp/mlmodel-v1`)已降级为薄包装,只转调
本 CLI 的 `main()`,不再含任何业务逻辑。

### 9.1 已有产物 output-dir 重跑失败的 manifest 混污(真 P1,2026-08-07 修复)

用户复核发现:对已经成功跑过一次的 output-dir 重跑,若第二轮在覆写部分
artifact 后失败,旧 `manifest.json` 此前会原样留在磁盘上——目录状态变成
"旧成功 manifest + 新一轮部分新 artifact + 本轮失败",manifest 不再如实
描述目录内容,违反"manifest = 完整成功信号"的契约。

安全契约选择方案 B(最小 fail-closed,未选 sibling-staging 的方案 A,
因为本 CLI 单次产物体积不大且不涉及并发发布场景,方案 B 已足够且改动
最小):完成三库必要表校验后、开始覆写任何 artifact 前,先原子撤销旧
`manifest.json`(`Path.unlink(missing_ok=True)`);之后无论在哪个阶段
失败,output-dir 在本轮成功之前必然没有 `manifest.json`,不会出现旧
manifest 与新部分 artifact 混合的状态;新 manifest 仍只在全部产物写完后
最后原子写入。明确不采用"清空整个 output-dir"的方式,避免误删该目录下
可能存在的、与本 CLI 无关的用户文件。

`TestExistingOutputRerunFailure`(3 条新增测试,含在上面 12 条中)覆盖:

- 早期阶段失败(`run_full_study` 注入异常)后旧 manifest 必须不存在;
- 后期阶段失败(`fold_metrics.json` 已成功写入之后、`paired_market_metrics.json`
  之前注入异常)后旧 manifest 仍必须不存在,证明该契约不只对"第一个
  写入点之前失败"成立;
- 连续两次成功重跑(无异常注入)必须仍能正常产出 manifest 且 artifact hash
  与首次一致(证明修复没有破坏正常重跑路径的等幂性)。

三条测试在修复前均以"旧 manifest 错误地继续存在"的方式确认 RED(2 个
新增反例 fail,1 个正常重跑用例 pass),修复后(`run()` 在表校验通过、
写任何 artifact 前立即 `unlink(missing_ok=True)` 旧 manifest)全部转 GREEN。

真实执行(从 fresh output-dir 对 `/tmp/mlmodel-v1/` 三库副本重跑)得到:

```
manifest.dataset_hash   = 172d4428455465ac77bff6d57fa45e170938aa08edca24d8ce49fbbbf7cda0c0
manifest.manifest_hash  = 5945f9adbb2805132a1b6d0a2d2926751165b0bc9bc16b0d34cc813ffddeb0e3
run_environment         = {python: 3.13.5, sklearn: 1.6.1}
```

**逐字段验证(非"直接宣称一致")**:CLI 产出的 `fold_metrics.json`/
`paired_market_metrics.json` 与本人在 CLI 建成前手工调用
`run_full_study()` 直接产出的版本**逐值相等**(Python `==`,非仅 hash 比对);
`feature_ablation.json` 与 lineage 修复前的原始产物**逐值相等**(消融不
涉及市场数据或 lineage,理应不变,验证成立);`fold_metrics.json` 里
freq/dc/lr/hgb/market_pinnacle/market_bet365/market_macauslot 七个字段
与 lineage 修复前的原始产物逐折逐字段比对 **0 处不一致**;新增内容仅为
§6.2 修复带来的 7 个此前被错误跳过的配对比较。产物已同步进
`runtime/research/multileague-model-v1/`(checked-in 快照,gitignored 目录
本身仍不进 git,但供下次会话续读)。

## 10. 最终验收(2026-08-07,含 manifest 重跑修复后的最终复算)

```
新 CLI 定向测试:  test_run_multileague_research_cli.py      12 passed
                  (含 TestExistingOutputRerunFailure 3 条新增)
研究模块测试:      test_run_research.py                       10 passed
                  test_pit_dataset.py                         10 passed
                  test_market_baseline.py                      7 passed
                  test_data_coverage.py                        4 passed
                  (五个研究测试文件合计)                       43 passed
联赛隔离测试:      test_build_features_league_isolation.py     4 passed
本模块合计新增测试(43 + 4):                                  47

backend 全量(标准运行):
  pytest tests/backend/ -q
  → 1,077 passed, 0 failed, 0 skipped, 0 xfailed in ~590s

backend 全量(-W default,Python 解释器级显式启用全部警告):
  python3 -W default -m pytest tests/backend/ -rA
  → 1,077 passed, 20 warnings in 831.06s (0:13:51)
  两次独立全量运行(标准 + -W default)均确认 1,077 passed 且无 failed/
  skipped/xfailed。标准运行不显式传 `-W default` 时不显示 warnings 计数
  行,不代表真实 warnings 数为 0——这正是本轮修复前的错误结论,已订正。
  -W default 明细抽样确认(10/20 条已核对文件与行号,均为同一类别):
  ResourceWarning: unclosed sqlite3 database/file(测试 fixture 未显式
  `.close()` 连接,由 GC 在不确定时机回收触发,和某个具体功能缺陷无关)——
  1 处出自 test_auth.py::TestLegacyGateRemoval::test_simulate_membership_param_is_dead,
  6 处出自 test_build_features_league_isolation.py::TestLeagueIsolation::test_load_raw_filters_by_league,
  3 处出自 test_predict_wdl_future.py::test_load_future_fixtures_scopes_to_league_id。
  其余约 10 处未逐条核对具体文件/行号(捕获时输出被截断,未重新跑第三次
  14 分钟全量以求完整枚举)——如实标注为**未完全逐条确认**,而非估算或
  默认视为同类别一并计入结论;两次独立全量运行的总数(20)本身是确认过的
  事实,不是估算值。

git diff --check:  exit 0(无冲突标记 / 无行尾空白问题)
```

三次全新对话时点的独立复核(用户方)与本文档记录的测试计数、
`172d4428…` dataset hash、P6 六项结论均已核对一致;本轮(manifest 重跑
修复)在此基础上新增 3 条 `TestExistingOutputRerunFailure` 测试,测试
总数由此前的 40(五研究文件)+ backend 1,074 更新为 43(五研究文件)+
backend 1,077,并以 `-W default` 复核了此前被错误省略的 warnings 计数。
