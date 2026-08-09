# 数据层规划(docs/data-plan.md)

> 数据层覆盖与排期的**单一真源**。`CLAUDE.md` §16 已登记本文件。

## 0. 本文件的职责与边界

三份文档三个不同的轴，**每个事实只有一个 owner，本文件引用而不复制**：

| 文档 | 轴 | 回答 |
|---|---|---|
| `docs/data-sources.md` | **来源** | 某个 provider(FotMob/NowGoal/Kbisai)能给什么、验证到什么程度 |
| `docs/current-state.md` | **时间** | 某一轮做了什么、真实命令输出是什么(追加式，不改旧节) |
| **本文件** | **联赛 × 数据层** | 现在哪些格子有数据、下一步按什么依赖顺序填 |

动态进度、行数快照、临时排期只写这里，不写 `CLAUDE.md`；具体某一轮的真实命令输出写 `docs/current-state.md`，不写在这里覆盖。

---

## 1. 现状一句话

产品骨架完整（前端五页、付费墙、预测账本、Studio）。截至 2026-08-05：五大联赛
26/27 赛季赛程（含精确 kickoff）已全部回填；挪超/瑞超 2026 赛季赛程已回填；
赔率层新增第二个 provider（kbisai，AES-256-CBC + Protobuf 双协议）并已对本周末
挪超/瑞超 + 英超第一轮共 25 场目标比赛完成真实身份解析（16/25 成功，9 场诚实
标记为暂不可解析——见 §7 本轮记录）与完整赔率变化序列采集（320 条真实变化点）。

2026-08-06 更新（历史赔率两轮回填 + 旧资产整合 + kickoff 回填，见
`docs/current-state.md` §25）：五大联赛 10,735 场已完赛比赛的赔率覆盖达到
**10,492 场 = 97.7%**（2,156 场完整时间线 + 8,336 场初盘/临场两点摘要）；
`silver_odds_moves` 首次产出真实数据（1,477,750 行,来自 NowGoal 历史回填的
734,812 条 bronze 快照）；已完赛比赛 `kickoff_at_utc` 从 0 行回填至全量
（`fotmob:leagues`,precision=exact）。"NowGoal 赔率层结构性空表"的说法自此作废。

2026-08-07 更新（J1/韩K联/澳超接入，见 `docs/current-state.md` §26/§27）：
三联赛 FotMob 全量 ingest 2,050 场（逐场明细+精确 kickoff+53 队中文名）并上线
网站（league:lottery 免费档）；历史赔率经全量复核后导入
`bronze_legacy_odds_summary` 10,856 行，已完赛 2,050 场中 88.3% 有 Bet365
初盘/临场两点（来源实证为 NowGoal，AH 线符号与 canonical 一致未取反）。

2026-08-07 二次更新（缺口补齐 + J1 新赛季，见 `docs/current-state.md`
§28）：旧库覆盖边界之外的 240 场用 NowGoal archive 实爬补齐 208 场（1,248
行，零失败），赔率覆盖升至 **2,018/2,050 = 98.4%**；剩余 32 场（澳超三季
季后赛 12 场 + J1 2026 冠军系列赛 20 场）是 archive 本身不收录附加赛的真实
来源边界。另：FotMob 已切换 J1 默认赛季为 `2026/2027`（跨年制过渡落地），
新赛季 380 场未开赛赛程已接入，首轮 2026-08-07 开赛。

2026-08-08 更新（挪超/瑞超已完赛比赛 + 历史赔率回补 + 32 队中文名双重
验证，见 `docs/current-state.md` §30）：上面 §2 矩阵记录的"59/67 全 0 完赛"
已作废——两联赛 2026 赛季已完赛比赛全量 ingest（挪超 123/123、瑞超
119/119，0 失败），`fact_league_table` 刷新为当前真实积分榜；32 支球队
中文名从 0/32 补齐到 32/32（`qwen_max_websearch_verified`，双重验证过程中
纠正了 2 处真实的翻译幻觉——`Start` 被误译成另一支球队 Stabæk 的中文名、
`Örgryte` 被误译成另一个城市/球队 Örebro 的中文名，均已用 WebSearch 独立
核实修正）。历史赔率：新建通用 `backend/cli/ingest_nowgoal_season_odds.py`
（NowGoal season-archive，curl_cffi + 住宅代理，独立于生产实时轮询路径），
挪超 123/123 场、瑞超 104/119 场解析为 `auto_ok` 并写入两点摘要
（`source=nowgoal_archive_refetch`，无需新迁移）；瑞超原有的 4 条
`needs_review` xref 已诚实修复 3 条（根因是队名别名不折叠变音符，不是
kickoff 判定问题），第 4 场留给生产轮询链路在临近开球时自然拾取。
`docs/current-state.md` §29 记录的"58/67 无历史 fact 表"（第 138 行 #1）
在 fact 表层面已解决；下方第 161 行"挪超/瑞超比赛级 fact 表回填路径"
同步关闭。**未接入定时增量**——`scheduler.step1_ingest_newly_finished`
仍硬编码 `(47,'2026/2027')`，两联赛后续新增的已完赛比赛不会被自动抓取。

---

## 2. 联赛 × 数据层覆盖矩阵

**as-of 2026-08-05T00:35Z**，由以下命令重新生成（`backend/cli/data_coverage.py` 尚不存在，此矩阵为手工查询产物，见下方"重新生成方式"）：

### 生产库 `data/allwin.db`

| 联赛 | 比赛(完赛/未赛) | 赛季数 | 26/27(或 2026)赛季精确kickoff | fact_league_table | fact_season_player_stats(赛季数) | silver 5 表 | i18n真实中文名/球队数 |
|---|---|---|---|---|---|---|---|
| 47 英超 | 2660 (2280/380) | 7 | 380/380 exact(`fotmob:fixtures`，上一轮回填，本轮未再次写入) | 600/6季 | 59139/6季 | 全 6 季 | 30/30 |
| 87 西甲 | 2660 (2280/**380**) | 7 | **380/380 exact**(本轮新增) | 600/6季 | 10822/**仅1季**(2025/26) | 全 6 季 | 0/31 |
| 55 意甲 | 2661 (2281/**380**) | 7 | **380/380 exact**(本轮新增) | 600/6季 | 10574/**仅1季** | 全 6 季 | 0/29 |
| 53 法甲 | 2364 (2058/**306**) | 7 | **306/306 exact**(本轮新增) | 550/6季 | 9178/**仅1季** | 全 6 季 | 0/28 |
| 54 德甲 | 2142 (1836/**306**) | 7 | **306/306 exact**(本轮新增) | 540/6季 | 9287/**仅1季** | 全 6 季 | 0/27 |
| 59 挪超 | 118 (0/118) | 1(`"2026"`) | 118/118 exact(`fotmob:fixtures`) | 80/1季 | 0 | 0 | 0/16 |
| **67 瑞超** | **121 (0/121)** | 1(`"2026"`，本轮首次接入) | **121/121 exact**(`fotmob:fixtures`，本轮新增) | 0(未回填) | 0 | 0 | 0/16 |
| **223 日职联** | **960 (960/0)** | 3(`2024/2025/2026`，2026-08-07 首次接入) | 960/960 exact(`fotmob:match_details`) | 280/3季(2026 为过渡期东西分组赛,只有 `all:组名` 行、无总表——赛制事实,非缺数) | 30399/3季 | 0 | **26/26** |
| **9080 韩K联** | **582 (582/0)** | 3(`2024/2025/2026`，同轮接入) | 582/582 exact(同上) | 240/3季(2024/2025 为常规+冠军/保级组复合结构,总表在 `all`、分组在 `all:组名`) | 18714/3季 | 0 | **14/14** |
| **113 澳超** | **508 (508/0)** | 3(`2023/2024`–`2025/2026`，同轮接入) | 508/508 exact(同上) | 185/3季 | 17743/3季 | 0 | **13/13** |

比赛级 fact 表（只对已完赛场次有意义）：47/53/54/55/87 各自与该联赛"完赛"行数基本一致；59/67 全 0（全部未开赛）。**本轮新增的 380(87)+380(55)+306(53)+306(54)+121(67) = 1493 行 upcoming 全部经 `backend/ingest/ingest_future_fixtures.py`（已加赛季身份校验，见 §7 本轮记录）写入，status 只出现 `NotStarted`，未出现 `Cancelled`/`InPlay`。**

`gold_wdl_predictions`(760) / `int_match_features`(2280) **只有 League 47**，其余联赛均 0——`backend/models/predict_wdl_future.py` 本轮已加 `League_ID` 谓词（见 §7 D-2026-08-04-3 关闭记录），不会因为 87/55/54/53 也出现 upcoming 行而误产生跨联赛"预测"。

### `data/platform.db` — 预测账本

`prediction_snapshots` 760 = 380 `draft` + 380 `legacy_unverified`，**`is_official=0` 且 `locked_at IS NULL` 的行数 = 760/760**。**生产环境目前没有一条正式(`is_official=1` 且已锁定)预测样本** —— 不得把这 760 读成"已有公开战绩"。`model_versions` 只有一条 `dc-baseline-1.M.2`，`applicable_league_ids=[47]`。

### `data/odds.db` — 赔率层（as-of 2026-08-06 历史回填 + 旧资产整合后）

| 表 | 行数 | 说明 |
|---|---|---|
| `dim_team_alias` | 225 | 178 五大联赛种子 + 16 挪超自动播种 + 9 手工播种 NowGoal 拼写 + 22（i18n 同步产生） |
| `dim_team_xref` | 6 | 生产 launchd `nowgoal_snapshot` 轮询在后台产生 |
| `dim_match_xref` | **2,181**(nowgoal 2,165 / kbisai 16) | 2,156 行为 NowGoal 历史回填实体解析产物(auto_ok,confidence 0.95/0.75 按证据类型) |
| `bronze_ng_odds_snap` | **734,871** | 734,812 行历史回填(2,156 场完整赛前时间线,中位数 326 观测点/场) + 生产轮询少量 |
| `bronze_fm_lineup_snap` | 7 | 周末挪超 7 场 |
| `bronze_fm_sideline_snap` | 14 | 同上 |
| `bronze_kbisai_odds_point` | 320 | kbisai 完整赔率变化序列 |
| **`bronze_legacy_odds_summary`** | **74,863** | 本轮新表(`odds/0004`):旧项目初盘/临场两点摘要,8,336 场(asset_a 7,938 / asset_b_footballdata 4,955 / asset_b_nowgoal 91),方向缺陷已在入库时修正(19.6% 的 1x2 主客反转、footballdata AH 线符号取反) |
| **`silver_odds_moves`** | **1,477,750** | **首次产出真实数据**(从历史回填 bronze 快照逐序列 diff 得出) |
| `silver_event_moves` / `gold_move_cooccurrence` | 0 | 历史回填只有赔率、无阵容/伤停快照(archive 端点不提供),结构性诚实为空;live 链路见 §4 |
| `poll_state` / `source_health` | 9 / 9 | 生产轮询后台产生 |

覆盖口径(五大联赛已完赛 10,735 场):完整时间线 2,156 + 两点摘要 8,336 =
**10,492 场(97.7%)**;仍无覆盖 243 场(113 场两侧来源均无 + 130 场旧资产
`match_name` 无法安全对齐主客方向,已写 review 文件待人工,绝不带病入库)。
API `/api/v1/matches/{id}/odds` 以 `coverage_tier` 区分 `full_timeline` /
`open_close_only`,前端对两点摘要只出表格不画走势图。

挪超 7 场周末比赛涉及的 14 支球队，其 FotMob 拼写与 NowGoal 真实拼写均已验证能解析到同一 `canonical_team_id`（见 §7 D-2026-08-04-1）——这是 `entity_resolution.py`(NowGoal 专用)`auto_ok` 六道门中的第一道。**kbisai 走的是完全独立的身份解析实现**（`backend/ingest/kbisai_match_resolution.py`，kickoff 精确匹配为主、CJK 队名为消歧/定向确认信号，不复用/不修改 `entity_resolution.py`）——见 §3。

### 沙箱 `runtime/data/*.db`（gitignored，与生产完全隔离）

as-of 2026-07-30T17:31Z（已落后现实 5 天，不代表当前状态）：59 → 240 场 season `"2026"`，全部 `kickoff_precision='exact'`／`kickoff_source='fotmob:league_or_team_fixture'`（**与生产 `fotmob:fixtures'` 是不同字符串，两边不可直接比对行数**）；`dim_team_i18n` 14 行，但只有 2 行是真中文名（8007 瓦勒伦加、8448 汉坎），其余 12 行 `name_zh = name_en` 回退值。`bronze_ng_odds_snap` 48 行（7 场 × 6 序列：Bet365+Sbobet × 1x2/ah/ou），`dim_match_xref` 7 行全 `confirmed`；`silver_odds_moves` 0（`build_odds_silver` 从未对该库跑过）。

### 重新生成方式

暂无自动化脚本。手工查询模板见 `docs/current-state.md` 本轮追加章节的命令记录；建模 `backend/cli/data_coverage.py`（参照只读的 `backend/cli/ops_check.py`）是登记在 §5 的一项待办，不阻塞本文件当前使用。

---

## 3. 验证状态表（CLAUDE.md §18 三分：代码实现 / 离线fixture验证 / 真实外部验证）

| 能力 | 代码实现 | 离线fixture | 真实外部 |
|---|---|---|---|
| FotMob 五大联赛历史 + 英超未来赛程回填 | ✅ | ✅ | ✅(库内真实行数) |
| FotMob 挪超(59)赛程 + 精确kickoff回填 | ✅ | — | **✅ 2026-08-04 本轮:118场真实ingest** |
| NowGoal type=6/14 端点解析 | ✅ | ✅ | ✅(2026-07-21 probe) |
| NowGoal 赔率二次变化快照(同一序列≥2观测) | ✅ | ✅ | ✅(match 2912857 Bet365 1x2: 2026-07-28→07-30 两次真实观测，见 `docs/data-sources.md` §7) |
| `silver_odds_moves` 由真实赔率变化产出 | ✅ | 未见断言 | **UNVERIFIED**(`build_odds_silver` 从未对含真实数据的库运行过——本项目历史上从未产出过一条 `silver_odds_moves`) |
| `gold_move_cooccurrence` | ✅ | ✅ | **结构性阻塞（原因已更新）**：`bronze_fm_lineup_snap`(7)/`bronze_fm_sideline_snap`(14) 本轮已有真实数据（2026-08-04 `poll_fotmob_snapshots --write-match-details`，见 §7），阻塞点变成 `silver_event_moves` 的构建从未对含真实数据的库运行过（仍是 0 行），不再是"没有 bronze 数据" |
| FotMob 赛前裁判/天气(赛前数据能力实测) | ✅(`derive_match_status` 修复 + `poll_fotmob_snapshots --write-match-details`) | ✅ | **✅ 2026-08-04 本轮**：挪超周末 7 场真实采集，Referee/Temperature/Wind_Speed 7/7/7 非空，status 全部保持 `NotStarted`(未被历史 bug 误判为 `Unknown`)，复跑一次 hash-diff 正确识别 0 新增 |
| kbisai comp/category 联赛 id 发现 | ✅ | ✅ | ✅ 2026-08-04 本轮真实请求：英超=82(三处路径一致)、瑞典超=184(与 8/8 场真实赛程 kickoff+队名完全对上，瑞典超甲=185/瑞典甲=186 均为不同联赛，已排除)；挪超=201(上一轮已验证) |
| kbisai futureMatch_b 赛程发现(含 +17~20 天窗口) | ✅ | ✅ | ✅ 2026-08-04 本轮：+3 天窗口(挪超/瑞超周末)与 +17~20 天窗口(英超第一轮，此前 UNVERIFIED)均真实验证可用；`matchDate` 分桶有重叠(同一场比赛可能出现在相邻两天的响应里)，调用方需按 `provider_match_id` 去重 |
| kbisai AES-256-CBC + Protobuf 双协议解密 | ✅(`cryptography`，无 openssl 子进程) | ✅(真实抓包重放) | ✅ 本轮：`cryptography` 库解密结果与探测阶段 openssl 子进程解密结果逐字节一致 |
| kbisai↔FotMob 身份解析 | ✅(`kbisai_match_resolution.py`，kickoff 精确匹配 + CJK 队名消歧，fail-closed) | ✅ | ✅ 本轮 25 场真实目标：16/25 成功(7 挪超 `needs_review`+7 英超`auto_ok`+2 瑞超`needs_review`)，9/25 诚实 fail-closed(6 瑞超同刻双场缺别名数据、3 英超别名字面量不匹配——非 bug，见 §7) |
| kbisai matchAllOdds 完整变化序列(初盘到现在) | ✅ | ✅(真实截断样本，保留同 changeTime 不同赔率、字节相同重复两种边界) | ✅ 本轮：320 条真实变化点(挪超7场+瑞超2场)，三目标公司(36\*/澳\*/平\*)每场每市场 100% 到位，复跑 0 新增(幂等) |
| kbisai `market_phase` 判定跨接口复用 `_STATUS_GROUPS` | ✅(`derive_market_phase`) | ✅ | **部分 UNVERIFIED**：`_STATUS_GROUPS` 枚举在 protobuf 比分端点上验证过，跨到 AES-JSON 的 matchAllOdds 端点复用未独立验证；本轮 320 条真实数据的 statusId×market_phase 交叉表 100% 落在 `{statusId=1, pre_match}`(全部赛前观测)，未覆盖 in_play/finished/other 分支 |
| kbisai 对英超第一轮(T-17~20d)的目标公司覆盖 | ✅ | — | **诚实负结果**：比赛本身可发现(10/10)，但 36\*/澳\*/平\* 三家目标公司均未发布该轮任何赔率(其它公司如 5/6/11/14/15/20 已有真实数据)，如实记 0 行，未扩大范围替换公司 |
| T-72h/15min + T-2h/5min 分级轮询节流 | ✅(`poll_windows.required_interval_seconds`) | ✅ | **UNVERIFIED，且本轮周末实测也无法验证**：`source_health`/`poll_state` schema 里没有 per-match 尝试历史，多场比赛同时处于不同档位时无法从现有表结构分离出单场节流间隔 |
| 挪超(59) NowGoal 实体解析 auto_ok | ✅ | 未见断言 | **部分**：别名门（第1道）已用真实捕获拼写验证通过（本轮，见§7）；其余5道门需真实 `--due` 窗口触发，尚未发生 |
| `--due` 72h窗口到期判定 | ✅ | ✅ | 本轮已验证"窗口未开时诚实返回0"（2026-08-04T00:45Z，`window_candidates=0`）；"窗口打开后触发真实采集"待 2026-08-04T17:00Z 后验证 |
| `market_phase`/FINAL 精确判定 | ✅ | ✅ | 待周末实测 |
| 挪超正式WDL模型预测 | ❌ | — | 不存在；`predict_wdl_future.py` 的 `FUTURE_SEASON="2026/2027"` 硬编码排除了裸年份赛季，且模型 `applicable_league_ids` 门禁锁定 `[47]` |

---

## 4. 结构性阻塞清单（有代码但跑不通，写清阻塞原因）

1. **挪超/瑞超历史比赛级 fact 表全 0**：`ingest_future_fixtures.py` 只写非 Finish 行，没有任何工具会在这些未来赛程"变成"已完赛后自动跑 `ingest_match`。需要挂到 `scheduler.step1_ingest_newly_finished`，但该函数目前硬编码 `(47, '2026/2027')`。
2. **`gold_move_cooccurrence` 结构性为 0**：见 §3（本轮已推进一步：bronze 层不再是 0，阻塞点收窄到 `silver_event_moves` 从未构建过）。
3. **11115 条历史 Finish 行没有精确 kickoff**：`ingest_future_fixtures.py` 显式跳过 Finish 行是真的；但 **`ingest_match.py` 会写 kickoff 三列这句话是本轮核实后发现的错误说法**——`fotmob_client.py::parse_match_dim` 本来就会从 `match_details()` 响应里解析并返回
   `kickoff_at_utc`/`kickoff_precision`/`kickoff_source`（`git show` 历史确认非本轮新增），`ingest_match.py::_upsert` 会把这三列写进 `dim_match`。也就是说**工具确实存在**：对历史 Finish 比赛重跑 `ingest_match.py`（每场 1 次 `match_details` 请求）就能回填精确 kickoff。真正的阻塞是**成本**（11115 场 ≈ 11115 次 FotMob 请求，需要住宅代理与较长时间）和**没有对应的批量入口脚本**，不是"没有任何工具能做这件事"。这行错误说法的历史来源未查明，本轮已订正。
4. **`_resolve_schedule_rows` 对 NowGoal 当日全量日程做跨联赛解析**：不按目标联赛过滤候选比赛，理论上一条无关比赛可能抢占 `UNIQUE(provider, fotmob_match_id)` 槽位并冻结（`needs_review` 永不重新评估）。本轮新增了 5 个联赛的真实比赛（87/55/54/53/67），此风险不再是"当前窗口内为零"，需要重新评估；本轮未修复，只更新风险等级。
5. **kbisai 与 NowGoal 完全独立的两套实体解析实现**：`entity_resolution.py`(NowGoal) 与 `kbisai_match_resolution.py`(kbisai) 没有共享代码，也没有共享的"联赛 competitionId 白名单"配置——目前挪超/瑞超/英超三个 kbisai competitionId(201/184/82) 是本轮验证后硬编码在一次性发现脚本产出里，不在任何生产配置文件中，下一次要采集新联赛需要重新走一遍 `backend/cli/kbisai_discover_competitions.py` 手工核实流程。
6. **kbisai 未接入 worker 自动链路**：`backend/cli/poll_kbisai_odds.py`/`kbisai_match_resolution.py` 都是可重复手动调用的 CLI，没有注册进 `backend/worker/runner.py` 的任务链或 systemd timer——本轮所有 kbisai 数据都是手动触发采集的，不会随时间自动保持新鲜。

---

## 5. 前向计划（依赖排序，替代 ROADMAP 旧 Phase 1-4）

按依赖顺序，不标时间估算：

1. ~~挪超(59)周末实测~~ **[已完成]**：生产库迁移 → 赛程回填 → 别名播种 → 窗口验证 → 真实赔率采集。见 `docs/current-state.md` 本轮追加章节。（`silver_odds_moves` 首次产出仍未发生，见 §4-2，移入下方 #5）
2. ~~新赔率源只读能力探测~~ **[已完成，走向生产接入]**：kbisai 已验证支持真正的完整变化时间序列（matchAllOdds，非"初盘/最新"两槽模型）+ 历史回填（验证到 2016 年，早期比赛粒度稀疏）。本轮已完成协议层（AES 解密 + Protobuf 复用）、schema（`odds/0003`）、身份解析、采集 CLI 四块并对 25 场真实目标比赛跑通。
3. ~~五大联赛下赛季赛程 + 精确kickoff回填~~ **[已完成]**：47(上一轮)/87/55/54/53(本轮) 全部 380/380/380/306/306 exact；59/67 2026 赛季 118/121 exact。
4. **扩大 kbisai↔FotMob 身份解析的可解析覆盖率**：本轮 25 场目标只解析出 16 场（见 §7），两类真实缺口——(a) `dim_team_alias` 每支球队目前只有 1 个中文别名，kbisai 有时用全称(如"曼彻斯特联")而非常见简称("曼联")，3 场英超因此判为歧义；(b) 瑞典超本周末有 3 对同一时刻开球的比赛，没有别名数据无法消歧。两者都不应该靠放宽匹配阈值/猜测名字解决，需要真实补充别名数据源（同一球队的多个真实中文名，非合成变体）。
5. `gold_move_cooccurrence` 解阻塞：`bronze_fm_lineup_snap`/`bronze_fm_sideline_snap` 本轮已有真实数据（7/14 行），下一步是真实跑通 `silver/build_event_moves` 并首次产出 `silver_odds_moves`/`silver_event_moves`。
6. 五大联赛历史赛季(2020/21–2024/25)球员榜回填（4 联赛 × 约 185 请求/联赛 ≈ 740 请求）。
7. 五大联赛西甲/意甲/法甲/德甲中文队名/球员名映射（目前 0 覆盖，本轮 kbisai 身份解析的英超命中率之所以能到 auto_ok 正是因为英超有这份数据——扩大到其它四个联赛能直接提高未来的 kbisai 匹配成功率）。
8. kbisai 接入 worker 自动链路：目前 `poll_kbisai_odds.py`/身份解析都是手动 CLI，未注册进 `backend/worker/runner.py` 或 systemd timer（见 §4-6）。
9. 英超第一轮补采：三个目标公司(36\*/澳\*/平\*)截至本轮尚未发布赔率（见 §3），需要在临近开球时重新跑一次 `poll_kbisai_odds.py` 确认是否已发布。
10. 挪超/瑞超比赛级 fact 表回填路径（依赖 #1 产生真实完赛比赛后设计）。
11. `core/0003` schedule-state v1 从"纯离线设计"转为生产采用（目前 7 张表在生产库已建好但 0 行，无任何生产代码路径写入）。
12. 非英超联赛 WDL 模型：依赖 #6/#10 的比赛级历史特征数据，且需要解除 `model_versions.applicable_league_ids` 门禁或训练新模型版本。
13. `content_status.json` v1-writer / v2-reader 结构不匹配缺陷修复（`backend/content_pipeline.py` 的 `_status_after_success` 写 flat v1，`backend/content_status.py` 的 `public_status_for_match` 读的是 v2 `matches` 映射）——怀疑与既存 Playwright `anonymous.spec.ts` 失败相关，未验证。
14. `backend/cli/data_coverage.py`：让 §2 矩阵可自动重新生成，而不是手工查询。
15. 前端展示 FotMob 赛前裁判/天气/伤停数据：本轮已验证 FotMob 能提供这些数据并采集入库（`dim_match.Referee/Temperature/Wind_Speed`、`bronze_fm_sideline_snap`），但目前没有任何页面组件读取展示它们（`grep` 确认，只有 `about-model` 静态文案提到"天气"字样）。

---

## 6. 生产库写入前置清单

任何触碰 `data/*.db` 的操作前：

1. `bash deploy/scripts/backup_sqlite.sh`（唯一正确工具——`.backup` API + integrity_check + sha256 + 原子发布；不要手写 `cp`/`sqlite3 .backup` 命令替代它）。
2. 确认待应用的 migration 文件已 `git commit`（防止应用后 `git clean` 造成 ledger 与文件不一致）。
3. `python -m backend.db.migrate --status` 确认迁移顺序与依赖（core 先于 platform；见 §7 本轮记录的具体原因）。
4. 迁移/写入后跑 `python -m backend.db.migrate --status`（应全部 `pending=none`）+ 三库 `PRAGMA integrity_check`。
5. `bash deploy/scripts/restore_verify.sh` 只在**迁移之后**的备份上跑（它断言 `pending == []`，对迁移前备份会正确失败）。

---

## 7. 已知缺陷 / 本轮记录登记

- **D-2026-08-04-1**：挪超(59) NowGoal 别名手工播种。14 支周末相关球队的 NowGoal 拼写（如 `Valerenga`/`Bodo Glimt`/`KFUM Oslo`/`Kristiansund BK`）取自 `runtime/artifacts/runs/eliteserien/*/nowgoal-schedule-*.raw.txt` 真实捕获产物（经 `backend.providers.nowgoal.parse_schedule` 解析，每个拼写 5-7 次独立观测），非猜测。9 条为新增（5 条与 FotMob 名字规范化后已重合，`resolve_entities` 自动种子已覆盖）。已用 `_alias_team_ids` 逐一验证 14 支球队的 FotMob 拼写与 NowGoal 拼写解析到同一 `canonical_team_id`，全部通过（0 失败）。
- **D-2026-08-04-2**（**已关闭，2026-08-05**）：英超 380 场 2026/2027 upcoming 行的 `kickoff_at_utc` 全 NULL。上一轮（"英超26/27精确开球时间重拓"）已修复——本轮核实 `SELECT kickoff_precision,COUNT(*) FROM dim_match WHERE League_ID=47 AND Season='2026/2027'` = `exact 380`，与已完赛历史行为一致，问题不再存在。
- **D-2026-08-04-3**（**已关闭，2026-08-05**）：`predict_wdl_future.py` 的 `load_future_fixtures`/`write_predictions` 缺 `League_ID` 谓词（§4-5 旧编号）。本轮修复：两处都加了 `league_id` 参数（默认 47），并加了三条回归测试（`tests/backend/test_predict_wdl_future.py`）覆盖"另一个联赛的 gold 行不会被误删/误生成"。这是本轮**最高优先级**的修复——不修的话，四个新联赛的 upcoming 行落地后，`predict_wdl_future.py` 会立刻把它们当成英超比赛出"预测"（模型基准参数只用英超历史拟合，对其它联赛的输出没有统计意义），一旦这些预测经 `prediction_register` 流程变成正式发布快照，按 CLAUDE.md §9.1 将永久进入公开预测账本、不可撤回。本机由于没有 systemd/cron（只有本地 launchd `poll_wrapper`，其 `JOBS` 不含 `model_predict`），实际暴露窗口为零，但代码层面的漏洞是真实的，已在四个新联赛赛程落地**之前**修复并验证。
- **D-2026-08-04-4**：`docs/current-state.md` §12 标题"瑞典超(Allsvenskan)正式接入"与实际不符——数据写入了 2026-07-21 的 `/tmp/allwin-allsvenskan-pilot-*` 目录，该目录现已不存在。**本轮瑞超已真实持久化接入生产库**（121 行 exact kickoff，见 §2），该历史记录里描述的"未接入"状态已成为过去式，正文本身不改（追加式文档纪律），此处更新登记状态。
- **D-2026-08-05-1**：`fotmob_client.py::parse_match_dim` 的赛前 `status` 判定 bug（真实存在，已修复）。真实赛前 `header.status` 没有 `reason` 键（只有 `cancelled/finished/halfs/started/timezone/utcTime`），旧代码 `status_obj.get("reason",{}).get("short","Unknown")` 因此对任何赛前比赛恒返回 `'Unknown'`，会让该场比赛被 `poll_windows.upcoming_precise_matches`（过滤 `status='NotStarted'`）永久排除出赔率轮询窗口。修复为 `derive_match_status()` 共享函数（布尔三元组 → 封闭四值），`ingest_future_fixtures.py::_status_from_fixture` 与 `parse_match_dim` 现在共用同一实现。已用 11 份仓库内真实赛前 payload + 17181 个真实 status 对象的统计验证（`Can`→`Cancelled` 70 例、其余 finished 类仍是 `Finish`）。
- **D-2026-08-05-2**：`ingest_future_fixtures.py` 原本没有赛季身份校验，若 FotMob 尚未发布某联赛的目标赛季，`league_matches()` 可能静默返回另一个（通常是已完结的）赛季数据，被当成目标赛季写进 `dim_match`。本轮照抄 `backfill_season_tables._verify_identity` 加了 `_verify_season_identity`（校验 `details.id`/`selectedSeason`），不一致抛 `SeasonIdentityError` 拒绝落库，不静默降级。
- **D-2026-08-05-3**（本轮诚实负结果，非缺陷）：kbisai 身份解析对本轮 25 场真实目标只解析出 16 场，9 场 fail-closed，原因**不是代码 bug**：(a) 瑞典超本周末 8 场比赛里有 3 对(6 场)在同一时刻开球（`2026-08-09T12:00Z`/`14:30Z`、`2026-08-10T17:00Z`各一对），`dim_team_alias` 对瑞超 0/16 覆盖，无法消歧；(b) 3 场英超比赛(赫尔城vs曼联、布伦特福德vs热刺、曼城vs伯恩茅斯)kbisai 用的是队伍全称（如"曼彻斯特联"/"曼彻斯特城"）而 `dim_team_alias` 只存了常见简称（"曼联"/"曼城"），单一别名字符串精确匹配对不上。两种情况匹配器都正确 fail-closed（不猜测），已记录进 §5-4 作为后续改进方向。
- **D-2026-08-05-4**（本轮诚实负结果，非缺陷）：英超第一轮(kickoff T-17~20d)10 场比赛均可通过 `futureMatch_b` 发现并成功身份解析，但三个目标公司(36\*/澳\*/平\*)在采集时点均未发布任何赔率（`fetch_match_all_odds` 返回的 15 家公司里不含这三家；其它公司如 5/6/11/14/15/20 已有真实数据）。如实记 0 行，未改用其它公司替代、未扩大采集范围。见 §5-9（需临近开球时重新采集确认）。

---

## 附:2026-08-07 覆盖数字以 data_coverage.py 重跑为准

本文件 §2 的覆盖矩阵是手工查询的历史快照,动态数字已落后(如五大联赛现为
每联赛 7 个赛季分区、J1 2026 完赛 200 场、EPL 2025/26 有 48 场无赔率)。
自本日起,任何覆盖数字以只读 CLI 重跑为准:

    python -m backend.cli.data_coverage --json coverage.json --md coverage.md

(deterministic,复跑 sha256 一致;联赛×赛季×公司三档赔率覆盖、特征与
正式预测覆盖全量输出;详见 docs/audits/multileague-point-in-time-model-v1.md §1。)

## 附二:2026-08-07 多联赛 PIT 建模研究(五大联赛)

`docs/audits/multileague-point-in-time-model-v1.md` 完成了五大联赛严格
point-in-time 数据集(10,734 场,dataset_hash
`172d4428455465ac77bff6d57fa45e170938aa08edca24d8ce49fbbbf7cda0c0`)、可复现
市场基线(Pinnacle/Bet365/Macauslot 分列 + 旧资产 summary_latest)、五折
season-forward 模型研究(freq/DC/LR/HGB)与对抗性复核。结论:LR 候选跨折
稳定优于频率与 DC 基线,但**全部候选在配对样本上均不优于市场基线**
(F2–F5 显著)。新增可重复入口 `backend/cli/run_multileague_research.py`
(三只读库 + output-dir 一条命令跑完全套研究产物)。判定:
`MULTI_LEAGUE_POINT_IN_TIME_MODEL_RESEARCH_COMPLETE`,
`READY_FOR_SHADOW_PREDICTION_DESIGN`(有条件,未开始 shadow 实现)。

2026-08-08 更新(J1/韩K联/澳超接入建模研究,见
`docs/audits/multileague-jka-integration-v1.md`):上面「J1/韩K联/澳超本轮
只报覆盖、未混训」的表述已过期。实测特征数据质量(精确开球率、xG 覆盖)
与五大联赛无法区分,原排除理由不成立。已扩展 `run_multileague_research.py`
支持 `--leagues all8`(八联赛 + 按 kickoff_at_utc 绝对时间折叠,与五大联赛
`--leagues big5` 赛季字符串折叠物理隔离)。真实结论:混训(pooled lr +
per-league 温度校准)相对「该联赛单独训练」在 J1、澳超上统计显著更优,
K1 方向一致但未显著;相对该联赛历史频率基线,三个联赛在当前样本量下
均未达统计显著(如实标注为功效不足,非阳性结论)。**未完成的关键前置项**:
五大联赛保护侧检验(池化后是否劣化)。判定范围与五大联赛研究相同,均
不涉及生产模型注册与预测上线。
