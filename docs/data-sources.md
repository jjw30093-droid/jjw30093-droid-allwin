# 数据源(docs/data-sources.md)

> 依据真实代码撰写:`backend/providers/nowgoal.py`、`backend/providers/fotmob_snapshots.py`、
> `backend/ingest/odds_snapshots.py`、`backend/cli/poll_nowgoal.py`、
> `tests/fixtures/nowgoal/`(2026-07-19 核对)；
> `backend/providers/kbisai_odds.py`、`backend/ingest/kbisai_match_resolution.py`、
> `backend/ingest/kbisai_odds_snapshots.py`、`backend/cli/poll_kbisai_odds.py`、
> `tests/fixtures/kbisai/`(2026-08-04/05 核对)。
> 纪律:能力必须与验证状态一起写;不可验证一律标 **UNVERIFIED**,不得夸大。

## 0. 春秋直播公开实时比分（Kbisai）

2026-07-31 对官网匿名 Web 客户端进行最小契约验证，确认：

- `POST /api/v1/football/realtimeMatch_b` 返回
  `application/x-protobuf` 的全量足球比赛快照；
- `wss://kbisailive.com/ws/match` 接受公开 Web 握手，订阅消息 type=10；
- 比分变动与状态变动消息类型分别为 type=12 / type=13；
- REST `MatchListResp` 含赛事、球队、比赛、比分、状态及开球时间；
- 无需登录、Cookie、账号 token、验证码或付费权限。

实现位于 `backend/providers/kbisai_live_scores.py`。它不会读取 `.env` 或
ThorData，也不会调用主播、聊天室、赔率、推荐和账户接口。传输固定
`trust_env=False`，不会继承本机代理环境。

时间与状态语义：

- `matchTime` 是 Unix 秒，归一为 UTC `kickoff_at_utc`；
- football `statusId=1` 为 `NOT_STARTED`，2–7 为 `IN_PLAY`，8 为
  `FINISHED`，9–13 为 `OTHER`；
- `homeScore[0]` / `awayScore[0]` 是公开前端实际展示的当前总比分；
- 压缩整数数组第 10 项在真实进行中样本中是 epoch-like 值，不是分钟。
  当前只输出 `provider_clock_reference` 并标
  `clock_semantics=UNVERIFIED`，禁止写成“第 N 分钟”；
- **本节范围限定于 `realtimeMatch_b`/`futureMatch_b` 两个比分/赛程端点**：
  来源没有可验证的更新时间字段，`source_updated_at=NULL`；
  本系统首次看到内容的时间写入 `observed_at`。**该结论不适用于 §0.1 的
  `matchAllOdds` 赔率端点**——那个端点的每个变化点自带来源声明的
  `changeTime`（Unix 秒），是真实的来源更新时间，参见 §0.1。

该实现兼容当次公开 Web 客户端，不代表来源承诺稳定、正式或可商业再分发的
API。生产上线前仍须确认来源条款、频率、缓存、署名和展示许可。来源改变协议
或要求登录时必须 fail closed，不得通过窃取 Cookie 或模拟账号继续调用。

真实样本与测试证据见
`docs/audits/kbisai-live-score-provider-v1.md`；运行方法见
`docs/operations/kbisai-live-scores.md`。

## 0.1 春秋直播公开赔率（Kbisai Odds，2026-08-04/05 本轮新增）

与 §0 的实时比分是同一站点、不同的一组接口。实现位于
`backend/providers/kbisai_odds.py`；协议边界同 §0（只用匿名公开接口，不读
Cookie/账号/`.env`/代理凭证，`trust_env=False`）。

### 端点与真实验证结果

| 端点 | 方法 | 用途 | 验证状态 |
|---|---|---|---|
| `POST /api/v1/football/comp/category` | body `{"sportType":"football"}` | 联赛分类树，用于发现某联赛的 kbisai competitionId | **已验证**：真实响应含 7 个顶层分类、1216 个节点；英超="英超"→**82**（"热门"/"欧洲"/"英格兰"三处路径一致）、挪超="挪超"→**201**（上一轮验证）、瑞典超="瑞典超"→**184**（本轮用真实赛程交叉核实，见下） |
| `POST /api/v1/football/futureMatch_b` | body `{"matchDate":"YYYY-MM-DD"}` | 某天全量赛程发现，返回 Protobuf（复用 §0 的 `MatchListResp` 解码器） | **已验证**，且验证范围超出原计划：不仅 `matchDate` 为 T+3 天内可用（上一轮已验证），**T+17~20 天同样可用**（本轮对英超第一轮 2026-08-21~24 的真实请求，10/10 场全部发现）。⚠️ `matchDate` 分桶**有重叠**：同一场比赛可能同时出现在相邻两天的响应里（实测 2026-08-21 的比赛也出现在 2026-08-22 的响应里），调用方必须按 `provider_match_id` 去重，不能假设每天的响应互斥 |
| `POST /api/v1/football/matchAllOdds` | body `{"matchId":N,"oddsType":"eu"\|"asia"\|"bs"}` | 某场比赛某个市场的**完整变化序列**（从初盘到当前，非"初盘/最新"两槽模型） | **已验证**：`{"<companyId>":{"statusMatchOdds":[{oddsInfo,changeTime,goingTime,score,statusId}...]}}`，新到旧排序。本轮 320 条真实变化点（挪超 7 场 + 瑞典超 2 场 × 3 目标公司 × 3 市场）逐点校验：单场单市场单公司变化点数在 1～22 之间（真实分布，非固定值），无编造均匀值 |
| `GET /api/v1/common/nm/allCompany` | **仅 GET**（POST 返回 `code:9999` 服务器未知错误，2026-08-04 实测确认） | 公司 id → 展示名映射 | **已验证**：真实注册表含约 15 家公司，用户点名的三家目标公司确认为 `{"2":"36*","7":"澳*","22":"平*"}` |

### 加密与市场语义

- 三个 AES 端点返回 `{"code":0,"msg":"...","data":"<base64 密文>"}`，
  `code!=0` 一律判定为协议错误（如 allCompany 误用 POST 返回的
  `code:9999`）；解密用 **AES-256-CBC + ZeroPadding**（不是 PKCS7，去除
  末尾零字节而非按 PKCS7 规则去除），密钥/IV 是站点自己公开 JS bundle
  里的静态常量（非账号凭证，任何访客浏览器都能看到），硬编码为模块常量，
  **不放 `.env`**；用 `cryptography` 库实现（`requirements.txt` 已加，替代探测阶段
  的 openssl 子进程 workaround，本轮验证两者对同一份真实密文解密结果逐字节一致）；
- `oddsInfo` 三个来源市场 → 本站 canonical 市场：`eu`→`1x2`
  `[home,draw,away,closed]`；`asia`→`ah` `[home_water,盘口线,away_water,closed]`；
  `bs`→`ou` `[over_water,盘口线,under_water,closed]`——**AH/OU 的盘口线固定在
  下标 1**，是这两个市场唯一带盘口的字段来源，已作为 `bronze_kbisai_odds_point`
  的一等列（不是塞进 JSON 里的次要字段），schema 用 CHECK 强制 1x2 必须
  NULL、ah/ou 必须非 NULL；
- `market_phase` 判定复用 `kbisai_live_scores._STATUS_GROUPS`
  （1=NOT_STARTED，2-7=IN_PLAY，8=FINISHED，9-13=OTHER），**该枚举是在
  §0 的 protobuf 端点上验证的，跨到本节的 AES-JSON `matchAllOdds` 端点复用
  未独立验证，标记 UNVERIFIED**；本轮 320 条真实数据的 statusId 全部是
  1（NOT_STARTED，赛前观测），未覆盖 in_play/finished 分支的真实交叉验证；
- `changeTime`（Unix 秒）→ `source_updated_at`（UTC ISO）是来源自己声明的
  更新时间——见 §0 顶部的作用域限定，这是唯一一个 kbisai 接口提供真实
  `source_updated_at` 的地方。

### 身份解析（与 NowGoal 完全独立的实现）

`backend/ingest/kbisai_match_resolution.py`，不复用/不修改
`entity_resolution.py`（NowGoal 专用）。kbisai 的 kickoff 是来源自己声明的
精确 UTC，身份解析以 kickoff 精确匹配为主，不需要像 NowGoal 那样靠队名
模糊匹配定位比赛；队名（kbisai 返回简体中文）只用于同刻多场时的消歧和
主客方向的独立确认——这两件事都需要 `dim_team_alias` 里已有该联赛的中文
别名（本轮实测：英超 26/27 全部 20/20 支球队覆盖，挪超/瑞典超 0/16）。

本轮对 25 场真实目标比赛（挪超周末 7 + 瑞典超周末 8 + 英超第一轮 10）跑通：
**16/25 成功**（挪超 7 场 `needs_review`——无别名数据独立确认主客方向；
瑞典超 2 场 `needs_review`；英超 7 场 `auto_ok`——别名数据双向确认）。
**9/25 诚实 fail-closed，均非代码缺陷**：瑞典超本周末有 3 对（6 场）比赛
同一时刻开球且无别名数据消歧；英超 3 场（赫尔城vs曼联、布伦特福德vs热刺、
曼城vs伯恩茅斯）因 kbisai 用队伍全称（"曼彻斯特联"/"曼彻斯特城"）而
`dim_team_alias` 只存了常见简称（"曼联"/"曼城"），单一别名字符串精确匹配
对不上——两种情况匹配器都正确拒绝写入，未做任何"猜最相似名字"的模糊匹配。

### 落库

新表 `bronze_kbisai_odds_point`（`backend/migrations/odds/0003_kbisai_odds_points.sql`），
一行一个真实变化点，`provider` 列从第一天就有（不同于 `bronze_ng_odds_snap`
的历史遗留问题）。幂等靠 `point_hash`（覆盖 handicap_line/三个赔率数值/
closed_flag/statusId/goingTime/score）+ `dup_ordinal` 的 UNIQUE 键，
**已验证保留"同一 changeTime 两条不同赔率"的真实边界情况**（match 2000000
的真实样本），也已验证保留"同一响应里两条字节完全相同的记录"的真实边界
情况；append-only 触发器拦截 UPDATE 在所有连接上生效，拦截 DELETE 仅在
`PRAGMA recursive_triggers=ON` 的连接上对 `INSERT OR REPLACE` 的隐式删除生效
（`connect_rw` 目前未开该 PRAGMA，本项目的写入路径本身不使用
`INSERT OR REPLACE`，真正的持久保证是 S3 备份，不是这个触发器）。

本轮真实采集验收（2026-08-04/05）：320 条真实行，三目标公司在挪超 7 场 +
瑞典超 2 场里每场每市场 **100% 到位**；复跑同一批目标 `inserted=0/duplicate=320`
（幂等）；英超第一轮 10 场比赛本身可发现，但三目标公司截至采集时点**均未
发布**任何赔率（其它公司如 5/6/11/14/15/20 已有真实数据）——如实记 0 行，
未改用其它公司替代、未扩大采集范围，需临近开球时重新采集确认。

## 1. FotMob(已验证)

- **能力(已真实验证)**:五大联赛(英超 47 / 法甲 53 / 德甲 54 / 意甲 55 / 西甲 87)
  比赛与球员数据已落库 core(allwin.db):dim_match、fact_match_events、fact_shotmap、
  fact_team_match_stats、fact_player_match_stats、阵容等(行数见 `docs/current-state.md`)。
  季级表覆盖(2026-08-04 真实回填,`backend/cli/backfill_season_tables.py`,
  167 次请求 0 失败):fact_league_table 五大联赛 × 各 6 季全量;
  fact_season_player_stats 英超 6 季、其余四联赛仅 2025/2026(历史赛季
  球员榜未回填,约需 740 次请求,留待需要时执行)。英超另有 26/27 赛程(NotStarted)。
- **采集方式**:`backend/fotmob_client.py`(curl_cffi Chrome TLS 指纹 + ThorData 住宅代理)。
  模块 import 与显式 `FotMobClient(proxy="")` 不读取代理或 `.env`；只有默认 live
  client 构造时才解析 `THORDATA_PROXY`，缺失则安全失败。离线场景使用已保存
  payload 和纯函数。
- **阵容/伤停快照**(odds.db):`extract_lineup_snapshot` / `extract_sideline_snapshot`
  从 match_details 的 pageProps 提取最小 canonical 子集(球员按 id 排序保证 hash 稳定;
  payload 无阵容时给空侧,视为一次合法观察,交给 hash-diff)。
- **时间声明**:FotMob 不声明阵容/伤停的来源更新时间 → `source_updated_at` 恒 NULL;
  另注意其 SSR 页面有 5–20 分钟 CDN 缓存,观察时间只能算 `observed_at`。
- **精确开球时刻**:`ingest_future_fixtures.py`/`ingest_match.py` 保留来源
  `utcTime` → `dim_match.kickoff_at_utc`(exact + `kickoff_source='fotmob:fixtures'`
  或 `'fotmob:match_details'`);来源只给日期时 `kickoff_precision='date_only'`,
  `kickoff_at_utc` 为 NULL,不补 00:00 伪装精确(见 `docs/prediction-integrity.md`)。
  2026-07-21 瑞典超接入真实 ingest 验证:105 场完赛 + 135 场未开赛均带真实
  `exact` 精度 kickoff。历史五大联赛旧数据在补列前落库,仍为 `date_only`
  (如实,未回填)。

### 1.2 数据管道重建 Phase 0 探测（2026-08-10,真实网络）

产物:`runtime/research/pipeline-v2-probe/`(raw 原始字节 + summary.json)。7 个待接入联赛
逐一 `league_matches(id)`(不传 season → 由响应发现赛季),`details.id` 全部匹配:

| id | 联赛 | 发现赛季 | 总场次 | T+7 窗口 | 最早 kickoff |
|---|---|---|---|---|---|
| 48 | 英冠 | 2026/2027 | 552 | 11 | 2026-08-14 |
| 57 | 荷甲 | 2026/2027 | 306 | 9 | 2026-08-07 |
| 61 | 葡超 | 2026/2027 | 306 | 9 | 2026-08-07 |
| 268 | 巴甲 | **2026(自然年)** | 380 | 9 | 2026-01-28 |
| 42 | 欧冠 | 2025/2026 | 189 | **0(季外)** | 2025-09-16 |
| 73 | 欧联 | 2025/2026 | 189 | **0(季外)** | 2025-09-24 |
| 10216 | 欧协联 | 2025/2026 | 153 | **0(季外)** | 2025-10-02 |

- **欧战三项资格赛天然排除已实证**:42/73/10216 的 round 集合 =
  `{1..8(联赛阶段), playoff, 1/8, 1/4, 1/2, final}`,最早 kickoff 均在 9 月及以后,
  **无 7-8 月资格赛**。`round='playoff'` 存在且保留(用户已确认附加赛要抓)。
- **欧战当前是季外**:2026-08 时 FotMob 只广告上一季(2025/2026),T+7 窗口 0 场——
  这是 `off_season` 诚实路径要处理的情形,非故障。26/27 抽签开始后 FotMob 才广告新季。
- **巴甲是自然年赛季**(`2026`),与五大联赛的跨年串不同,赛季解析不得按月份推断。

### 1.3 多联赛历史覆盖 probe（2026-07-30）

真实 probe 使用同一 FotMob transport，在 gitignored private runtime 中保存
不可变 raw artifact 和 durable request ledger。验证赛事包括 MLS 130、J. League
223、K League 1 9080、A-League 113、Eredivisie 57、Championship 48、
Liga Portugal 61、Brazil `Serie A` 268、Champions League 42、Europa League
73 和 Conference League 10216；另用 Premier League 47、Eliteserien 59、
Allsvenskan 67 作控制。

127 个 season/结构通过 source identity、advertised/returned season、
pagination、fixture 和 ID/kickoff/status 门禁；351 场 completed/non-cancelled
比赛作确定性三点采样。三点结果只可标 `SAMPLED_SAFE`，不可声明全赛季完整。
自然年、跨年、tournament season 分开解析，跨年不按月份推断。J. League 的真实
`2026/2027` 广告季作为 regime transition 保留；UECL 从实际 2021/22 开始。

详细字段路径、safe-start、xGOT NULL/zero drift、round/group 结构与边界见
`docs/audits/multi-league-season-coverage-probe-v1.md`。这不是 production
historical backfill 已完成的证据。

## 2. NowGoal(部分验证,逐项标注)

### 2.1 端点与格式

Base:`https://www.nowgoal26.com`(`providers/nowgoal.py`)。

| 端点 | 用途 | 验证状态 |
|---|---|---|
| `GET /ajax/SoccerAjax?type=6&date=YYYY-MM-DD&order=time&timezone=8&flesh=<rand>` | 当日日程 | **已真实验证(2026-07-19、2026-07-21 两次 probe,HTTP 200)**:响应 Data 文本按行;`A[` 开头的行是比赛,titan_id 为 `=[` 后第一段数字;引号内 JS Date 元组(如 `'2026,7,24,17,00,00'`,**月份 0 基**)是 kickoff——**2026-07-21 用真实比赛交叉验证确认该字段本身就是 UTC**(不是早先假设的北京时间),`date=` 查询参数按 `timezone=8` 决定返回哪个北京日历日,但行内 kickoff 值不再转换,详见下方"实体解析安全规则" |
| `GET /ajax/soccerajax?type=14&t=1&id=<titan_id>&h=0&s=0&flesh=<rand>` | 单场赔率 | **已真实验证(2026-07-21,titan_id=2912218,ErrCode=0)**:顶层 `{ErrCode, Data:{mixodds:[...]}, MatchState}`,`mixodds` 每元素 `{cid, cn, euro, ah, ou}`(`cn`=公司名,不是旧项目审计构造假设的 `name`);`euro`/`ah`/`ou` 各含 `f`=初盘/`l`=最新,`u`/`g`/`d` 三槽位与旧假设一致。真实首次探测发现并修复两处解析 bug(见下方"首次真实验证发现的缺陷");公司覆盖范围(除 Bet365/Sbobet 外)与历史回填仍 UNVERIFIED |

解析器(`parse_schedule` / `parse_odds`)是纯函数,离线测试用
`tests/fixtures/nowgoal/{schedule_sample.txt, odds_sample.json, poll_fixture.json,
odds_sample_real_shape.json}`(fixture 内也注明各自的验证状态)。

### 2.1.1 首次真实验证发现的缺陷(2026-07-21,瑞典超接入实验)

此前 type=6/type=14 的行为完全基于旧项目代码审计构造,从未在本项目做过真实网络
探测。2026-07-21 用瑞典超真实比赛(FotMob Match_ID=5107547,Västerås SK vs
Örgryte,真实 `kickoff_at_utc='2026-07-24T17:00:00Z'`)首次交叉验证,发现并修复
两处真实缺陷:

1. **kickoff 时区假设方向错误**:`entity_resolution._kickoff_diff_seconds` 原按
   "`timezone=8` 请求参数 ⇒ 行内 kickoff 是北京墙上时间,需 -8h 转 UTC"的假设实现,
   从未验证过。真实探测中,titan_id=2912218 的行内 kickoff 原始值为
   `'2026-07-24 17:00:00'`,与 FotMob 真实 UTC kickoff 逐位相同——`kickoff_diff_seconds`
   实测恰为整 `-28800`(-8 小时,不是随机噪声,是系统性单位换算错误的典型信号)。
   已改正为"行内 kickoff 本身就是 UTC,不转换";`timezone=8` 只影响 `date=` 参数
   应该请求哪个北京日历日(该用法本身已用真实数据验证正确)。影响面:此前的假设
   会导致**任何联赛**的自动实体解析都无法通过 kickoff 差值校验(±30min)达到
   `auto_ok`——这是一个全局性缺陷,不只影响瑞典超。
   **2026-07-21 独立复核补充交叉验证**:再取 2 场不同日期/不同开球时刻的真实
   Allsvenskan 比赛只读探测,结论一致,零反例:
   - titan=2912212(Degerfors vs Djurgårdens),行内 kickoff `'2026-07-25 13:00:00'`
     = FotMob `2026-07-25T13:00:00Z`,查询北京日期 2026-07-25,diff=0;
   - titan=2912214(Häcken vs AIK),行内 kickoff `'2026-07-27 17:00:00'`
     = FotMob `2026-07-27T17:00:00Z`,查询北京日期 2026-07-28(跨日验证
     `timezone=8` 日期分桶同样正确),diff=0。
   三场比赛(2 个不同开球小时、3 个不同日期)全部一致、零反例,"行内 kickoff
   即 UTC"结论证据强度已从单场提升为多场独立交叉验证;仍保留
   `_parse_nowgoal_wall_clock`/`_kickoff_diff_seconds` 的显式时间格式校验作为
   防御性测试,不假设该来源结构永久不变。
2. **type=14 响应结构与旧审计构造不符**:真实响应顶层是
   `{ErrCode, Data:{mixodds:[...]}, MatchState}`,`_iter_companies` 原来的
   `Data → list(dict.values())` 逻辑取不到 `Data.mixodds` 这一层,导致
   `parse_odds` 对真实响应恒返回空列表;公司名字段是 `cn` 不是 `name`/`company`,
   导致 `company_name` 恒为空。均已修复,`_iter_companies`/`_company_records`
   同时兼容旧构造形状(`companies` 直接列表 + `name` 字段)与真实形状,新增
   `tests/backend/test_nowgoal_provider.py::TestParseOddsRealShape` 用脱敏真实
   结构 fixture(`odds_sample_real_shape.json`)锁定回归。

两处修复后,真实链路验证通过:titan_id=2912218 ↔ FotMob Match_ID=5107547 达成
`auto_ok`(confidence=1.0,kickoff_diff_seconds=0),`fetch_odds` 返回 3 条真实
Bet365 记录(1x2/ah/ou,initial+latest 均非空)并成功落库
`bronze_ng_odds_snap`,`market_phase='pre_match'`;二次相同 payload 重跑
`skipped=3,inserted=0`,hash-diff 正确生效。

### 2.2 快照能力边界(重要)

- **每公司每市场只有两组快照:`f`(初盘)与 `l`(最新)——不是完整时间序列。**
  本系统靠自己的轮询把"最新"的历次变化累积成时间线;两次轮询之间发生又回撤的变化
  观测不到。任何页面/文档不得把它描述为"完整多公司赔率时间序列"。
- 公司选择:优先 CID 8(Bet365)、31(Sbobet)(`DEFAULT_TARGET_CIDS`);
  一家都没命中时回退第一家有效公司。其他公司覆盖 **UNVERIFIED**。
- **实时公司面板已实测(2026-08-10,3 场未开赛比赛,`runtime/research/pipeline-v2-probe/`)**:
  单场 `type=14&t=1` 稳定返回 **12 家公司**:
  `8 Bet365 · 31 Sbobet · 50 1xBet · 17 Mansion88 · 24 12bet · 3 Crown(皇冠) ·
  42 18Bet · 12 Easybet · 1 Macauslot(澳门) · 4 Ladbrokes · 14 Vcbet · 19 Interwetten`。
  数据管道重建选定的三家 **Bet365(8) / 澳门(1) / 皇冠(3)** 在 3/3 场全部出现,
  且各自 euro/ah/ou 三市场齐全。**Pinnacle 不在实时面板**(此前文档"Pinnacle 有赛前 1X2"
  的结论仅来自历史 archive 端点对已完赛比赛的验证,不适用实时;已由用户改选皇冠)。
- **赛后补抓 sweep 不可行(已探测,fail-closed)**:对一场未开赛比赛调 `mix_history`
  (`type=14&t=20`)返回 0 行、无时间戳 → 该端点对未开赛/临近比赛不提供可用的带时间戳序列。
  因此数据管道重建**不建赛后补抓 sweep**,临场收盘只靠 10 分钟档 + last_call 强制轮询保证。
- 主客反转归一(`normalize_for_inversion`,依据 `dim_match_xref.home_away_inverted`):
  1x2 交换 home/away;AH 交换双边并把盘口线取负;OU 对称不换。
- WAF:响应命中 `just a moment`/`cloudflare` 等标记即抛 `WAFBlockedError`,
  调用方跳过并记 source_health,不重试硬闯。

### 2.3 历史回填

当前已验证的 `type=6 date → Titan ID → type=14` 路径为
**SAMPLED_UNAVAILABLE**。2026-07-30 独立
直连 probe 从 11 个赛事各取早/中/晚 3 场，共 33 场、32 个北京日期
(`2015-07-04`～`2026-04-12`)；32 次历史日程响应均为 HTTP/JSON 成功、
`ErrCode=0`，但 `matchcount=0` 且没有比赛行，因此无法发现任何历史 Titan ID，
没有进入历史赔率请求。

同一 `trust_env=False` 直连 transport 的当前控制排除了整体服务/解析失败：
`2026-08-01` 日程返回 988 场，已验证 Titan `2912857` 的 type=14 仍返回两家
目标公司及 1X2/AH/OU、initial/latest。34 次请求全部成功，零 WAF、零住宅代理。
这只证明该日期发现路径不可用。后续已真实验证 NowGoal season web archive：
英超 `36`、意甲 `34`、西甲 `31`、德甲 `8`、法甲 `11` 的 2020–21 完整赛季
catalog 均可返回历史 Titan ID；每个联赛确定性抽取一场的真实 probe 中，Bet365、
Macauslot、Pinnacle 均有带时间戳的赛前 1X2，且 Bet365/Macauslot 另有 AH/OU。
40 次请求全部成功、零住宅代理。该结果仅证明五场样本能力，不等于所有比赛或
2020–21 之后所有赛季已完整覆盖。旧仓另有 probe 记录
(`miaomiaodi` 仓 `backend/logs/nowgoal_xhr_probe.json`，本项目未读)。

旧 type=14 的 `f/l` 仍没有已验证时间戳，不能冒充历史收盘价；archive history
则必须按其逐行时间戳和 kickoff 明确筛选 pre-match。完整证据见
`docs/audits/nowgoal-historical-capability-probe.md`。

### 2.4 实体解析安全规则(P0-B 收口,`backend/ingest/entity_resolution.py`)

跨源映射(NowGoal ↔ FotMob canonical)的自动通过(`review_status='auto_ok'`)是**自动候选**,
`verified` 恒为 0——只有管理员在 `/api/v1/admin/xref` 确认才 `verified=1`。

**召回 vs 身份证明**:候选比赛的**召回打分**用别名 ∪ dim_team_xref 直查的**并集**(历史
provider xref 帮助召回一个候选),但**并集本身不足以 auto_ok**——历史 provider xref 不得
单独充当身份证明,否则错误队名会被历史 xref 掩盖(见反例)。auto_ok 的**全部**必要条件:

1. **最终身份证明:本轮 `home_name`/`away_name` 经 `dim_team_alias` 独立、唯一地解析到候选
   比赛(按方向计算)的预期 canonical**;名称未知、错误或多 canonical 歧义 → `needs_review`
   (`home_team_name_mismatch` / `away_team_name_mismatch`);
2. 方向唯一,无同分歧义;
3. **双边都有可严格解析的精确 kickoff**:core 侧统一验证器 `normalize_exact_kickoff`
   (`kickoff_precision='exact'` ∧ 非空真实来源 ∧ 显式时区可解析),NowGoal 侧行内 kickoff
   (本身即 UTC,2026-07-21 真实比赛交叉验证确认,见 §2.1.1;不再做 -8h 转换)
   真实含时间部分且可解析;
4. 两侧 kickoff 差值可计算且 `|diff| ≤ 30 分钟`;
5. **provider 主客球队 ID 都存在、互不相同**,且本次映射蕴含的
   `(provider_team_id, canonical_team_id)` pairs 内部自洽(同一 provider ID 不指向多个
   canonical、主客 canonical 不被折叠成同一支);
6. provider ID 对应的既有 team xref:**不存在**时允许在本次严格通过后创建、**已存在**时
   必须与预期 canonical 完全一致;
7. 比赛 xref 一对一约束与 team xref 均无冲突。

任一侧缺精确 kickoff、时间无法解析或差值无法计算 → `kickoff_diff_seconds` 记 **NULL** 且强制
`needs_review`(**不再因 `kickoff_diff is None` 放行**)。

**provider 球队身份完整性**:主队或客队 provider ID 缺失、相同、或 pairs 内部矛盾时,整场
`needs_review`、**不写任何 team xref**,返回明确 `validation_errors`
(`provider_team_id_missing` / `provider_team_ids_not_distinct` /
`provider_team_internal_conflict`)——**绝不依赖 `INSERT OR IGNORE` 把"重复 provider ID 只写一行"
的半映射吞成看似成功**。

**team xref 冲突显式化 + 事务一致**:auto_ok 写库前先检查 `(provider, provider_team_id)` 是否已
映射到**不同** canonical。若冲突 → 整场降级 `needs_review` 且**不写任何 team xref**(不产生
"比赛 auto_ok 但 team xref 未写"或反之的半成功);相同 canonical → 幂等复用,不改写既有
`verified`/`manual` 行。pair 内部校验、DB team xref 冲突检查、match xref 占用检查与写入都在
同一个 `BEGIN IMMEDIATE` 事务内完成。返回值带 `team_conflicts` 明细供审计。

**既有 auto_ok 每次重新遇到都重新验证**(`_revalidate_existing_auto_ok`,不盲信历史结论):
除 kickoff provenance 与差值外,还用**本次 schedule_row 的队名/别名**独立验证主客方向——按
`home_away_inverted` 计算 NowGoal 主客队各自预期 canonical,要求两侧队名经 `dim_team_alias`
明确、唯一解析到预期 canonical(不匹配对手、无多 canonical 歧义);并要求 provider 球队 ID
存在、互异,且**两个 provider ID 对应的 dim_team_xref 行真实存在**(既有行被删、或本轮换成
从未映射的新 provider ID → `provider_team_xref_missing`)**且都指向预期 canonical**(存在但
canonical 不符 → `team_conflicts`,与 missing 用不同信号区分)。任一项不满足(纯换名、主客名
互换、provider ID 缺失/重复、team xref 缺失或冲突、kickoff 漂移)→ **原子降级 `needs_review`**,
该轮不进入赔率采集;重验证**只允许保留 auto_ok 或降级**,绝不静默创建、补写或覆盖任何 team
xref。

**事务边界**:existing auto_ok 重验证把 match xref 当前权威状态、alias、dim_team_xref 的读取
与"保留 auto_ok 还是降级"的最终判定放在**同一个 `BEGIN IMMEDIATE` 事务**内——**保持 auto_ok
的成功路径也在事务内做最终复核,不在事务开始前提前 return**;core 库全程只读(在持有 odds
事务时读取)。先在事务内重查确认仍是 `auto_ok`+`verified=0` 才更新,避免与并发人工操作竞态。

**既有映射保护**:`confirmed`/`verified`/`manual` 的既有 match/team xref 不被自动流程或上述
重验证覆盖;`rejected` 映射不被自动复活(同 provider_match_id 早返回原状态)。

**边界**:needs_review / rejected 映射不进入 Silver(变化点/共现构建只取 auto_ok/confirmed)。
真实 NowGoal 端点连续采集 **UNVERIFIED**,以上规则由离线 fixture + 单元测试验证同一条代码链路
(`tests/backend/test_odds_pipeline.py` 的 `TestEntityResolutionSafety`、
`TestProviderIdentityAndRevalidation`、`TestTeamXrefExistenceAndAliasGate`)。

## 3. 轮询策略(窗口到期调度,CLAUDE.md §6.3)

- **触发**:`allwin-poll.timer` 每 5 分钟触发一次"到期判断"(`worker --job
  nowgoal_snapshot` + `--job fotmob_snapshot`);这不代表每 5 分钟都请求数据源。
- **窗口与频率**(`backend/ingest/poll_windows.py`,离线 fixture 已验证):
  - 只有 `kickoff_at_utc` 精确、状态 NotStarted、开球落在 [now, now+72h] 的比赛
    进入采集窗口(缺精确开球时间的比赛不进入,不按当天 00:00 伪装);
  - 距开球 2–72h:同一 (source, 比赛) 最小间隔 15 分钟;0–2h:5 分钟;
  - 已开球即退出赛前窗口(in-play 采集是显式的另一件事,当前未实现);
  - 到期状态持久化于 odds.db `poll_state`,进程重启不重复采集。
- **日程发现**:窗口内未映射比赛按其北京日期(NowGoal timezone=8)抓日程,
  同一日期最小间隔 15 分钟;不再只抓"当天"而漏掉未来 72h。
- **单轮流程**(`poll_nowgoal.run_due_poll` / 单日模式 `run_poll`):日程失败记
  source_health 并跳过该日期;单场赔率失败继续其余场次。只有
  `review_status ∈ (auto_ok, confirmed)` 的映射才抓赔率;needs_review 不抓
  (等人工审核,见 `/api/v1/admin/xref`)。
- **FotMob 快照**(`poll_fotmob_snapshots.run_snapshot_poll`):同窗口同频率;
  同一比赛同一轮只抓一次 match payload,阵容 + 两队伤停三条快照共用
  `observed_at` 与 `poll_run_id`。

## 4. 落库规则(hash-diff)

`ingest/odds_snapshots.py`,目标表 `bronze_ng_odds_snap`(每 (provider_match_id,
market, company_id) 一条序列)、`bronze_fm_lineup_snap`、`bronze_fm_sideline_snap`:

1. record → canonical JSON(`canonical_payload_json`:排序键、紧凑分隔符)→ SHA-256;
2. 与同序列最近一条 `payload_hash` 比较:**不变则跳过,变了才 INSERT**(append-only,
   从不 UPDATE 旧快照);
3. `market_phase` 精确判定(不按自然日、不按字符串是否含 'T' 粗判):来源状态 + **完整
   kickoff provenance**(唯一真源 `normalize_exact_kickoff`:exact ∧ 非空来源 ∧ 显式时区
   可解析)+ 观察时间共同决定——core status='NotStarted' 且 now < kickoff(datetime 比较,
   非裸字符串)→ `pre_match`;core status='InPlay' → `in_play`;信息不足(含无精确 kickoff、
   缺来源、naive/非法时间)→ `unknown`。
4. `FINAL`(`silver/odds_moves.final_pre_match_snapshot`):精确 kickoff 前最后一条
   `market_phase='pre_match'` 快照,同样经 `normalize_exact_kickoff` 判定,明确排除
   unknown/in_play;kickoff 缺失、precision 非 exact、缺来源或不可解析时返回 None
   ——**不声称任何快照是收盘快照**。

## 5. 时间戳四件套语义(CLAUDE.md §6.2)

| 字段 | 语义 | 本项目取值 |
|---|---|---|
| `source_updated_at` | 来源自己声明的更新时间 | NowGoal 与 FotMob 均不声明 → **恒 NULL**,禁止用抓取时间伪装 |
| `observed_at` | 本系统首次观察到该内容的 UTC 时间 | 由调用方传入,同一轮采集统一取轮次开始时刻 |
| `ingested_at` | 数据库写入 UTC 时间 | 落库模块内取 `utc_now_iso()` |
| `poll_run_id` | 同一轮采集标识 | 每轮 `run_poll` 生成一个 UUID |

用户文案统一写"系统于 observed_at 检测到",不写"来源于 X 时更新"。

## 6. 降级行为

- `source_health`(odds.db)**append-only**:每轮日程/赔率阶段各记一条
  {source, checked_at, ok, latency_ms, error_summary, meta_json};失败**只追加记录,
  绝不覆盖/删除最后一次成功落库的数据**。
- 采集失败不影响 API:serving 全部走 `mode=ro` 只读连接读最后成功数据;
  页面应展示最后成功更新时间与 stale 状态,不伪造新鲜度。
- worker 链中 `nowgoal_snapshot` 失败会让链上后续步骤记 skipped(依赖检查),
  但不会崩掉 API 进程(采集与 serving 进程分离)。
- FotMob 侧:`THORDATA_PROXY` 缺失时任务记 failed + 清晰错误(worker `require_env`
  前置检查),不崩溃、不重试。

## 7. 验证状态总表

| 项 | 状态 |
|---|---|
| FotMob 五大联赛历史 + 英超 26/27 赛程落库 | 已验证(库内真实行数) |
| FotMob 瑞典超(league 67)2026 赛季历史 + 未来赛程落库 | **已验证(2026-07-21 真实 ingest:105 场完赛 + 135 场未开赛,隔离实验库,库内真实行数)** |
| FotMob 阵容/伤停 extract 纯函数 | 已验证(pytest 离线) |
| NowGoal type=6 日程端点与行格式 | 已验证(2026-07-19、2026-07-21 两次 probe,HTTP 200) |
| NowGoal type=6 行内 kickoff 时区语义 | **已修正并验证(2026-07-21)**:行内 kickoff 本身是 UTC,不是此前假设的北京时间(-8h 转换已移除,见 §2.1.1) |
| NowGoal type=14 赔率端点与格式 | **已验证(2026-07-21,titan_id=2912218 真实响应)**:真实结构 `Data.mixodds[]` + `cn` 字段与旧项目审计构造不同,已修复解析并锁定回归测试(见 §2.1.1) |
| NowGoal 公司覆盖(除 Bet365/Sbobet 优先级设定外) | **UNVERIFIED**(2026-07-21 真实响应确认该场至少 12 家公司,已验证 Bet365 命中,其余公司字段结构未逐一核对) |
| NowGoal 历史回填 | **ARCHIVE SAMPLE AVAILABLE / FULL BACKFILL NOT RUN**：旧 type=6 日期发现样本不可用；season archive 已验证五大联赛 2020–21 各一场，Bet365/Macauslot/Pinnacle 均有带时间戳赛前 1X2，前两家另有 AH/OU。尚未证明每场、每市场、后续每赛季完整覆盖 |
| NowGoal 实体解析 auto_ok(真实比赛) | **已验证(2026-07-21)**:FotMob Match_ID=5107547 ↔ titan_id=2912218,confidence=1.0,kickoff_diff_seconds=0,`dim_team_xref` 正确写入两队 |
| NowGoal 赔率快照落库 + hash-diff 幂等(真实数据) | **已验证(2026-07-21)**:首次落库 3 条(1x2/ah/ou,Bet365),二次相同 payload 重跑 `inserted=0/skipped=3` |
| NowGoal 赔率二次变化快照(真实数据) | **已验证(2026-07-28→07-30,沙箱库)**:match 2912857 Bet365 1x2 两次真实观测(`2026-07-28T07:01:27Z latest={home 1.62,draw 4.10,away 4.75}` → `2026-07-30T17:18:49Z latest={home 1.57,draw 4.33,away 5.25}`),6 个序列(2 家公司 × 3 市场)各 2 条快照 |
| Silver move(`silver_odds_moves`,真实数据) | **仍 UNVERIFIED**:`build_odds_silver` 从未对含真实二次变化的库运行过——本项目历史上从未产出过一条 `silver_odds_moves`。计划见 `docs/data-plan.md` §3/§5 |
| `--due` 72h 到期窗口(真实数据) | **TIME-DEPENDENT UNVERIFIED**:2026-07-21 会话内样本比赛距开球 > 72h(窗口约 8 小时后开启),`--due` 如实返回 0 候选,未伪造当前时间 |
| T-72h/15min + T-2h/5min 分级轮询 | **已实现**(`poll_windows.required_interval_seconds`:2–72h→900s,0–2h→300s;`poll_state` 持久化节流);离线 fixture 验证,真实端点连续采集仍 UNVERIFIED |
| `market_phase` / `FINAL` 精确判定 | **已实现**(唯一真源 `normalize_exact_kickoff`,按完整 provenance 判 pre_match/in_play/unknown;不精确时 FINAL 返回 None);对缺精确开球的比赛如实标 `unknown`,不伪装收盘 |
| 采集窗口混合时区筛选 | **已修复**(`upcoming_precise_matches` 用 `julianday()` 比较窗口边界,带 `-05:00`/`+08:00` 偏移的合法 kickoff 不被裸文本范围误排除) |
| kbisai comp/category 联赛 id 发现 | **已验证(2026-08-04/05)**:英超=82、瑞典超=184(与真实赛程 kickoff+队名交叉核实，排除同名相近的瑞典超甲=185/瑞典甲=186)、挪超=201(上一轮) |
| kbisai futureMatch_b 赛程发现(T+17~20天窗口) | **已验证(2026-08-04)**:此前只验证 T+3 天内，本轮验证 T+17~20 天(英超第一轮)同样可用；`matchDate` 分桶有重叠，需按 `provider_match_id` 去重 |
| kbisai AES-256-CBC+ZeroPadding 解密(`cryptography`) | **已验证**:与探测阶段 openssl 子进程解密结果逐字节一致；`allCompany` 真实注册表核对通过 |
| kbisai matchAllOdds 完整变化序列 | **已验证(2026-08-04)**:320 条真实变化点(挪超7场+瑞典超2场)，AH/OU 盘口线 100% 非空，三目标公司(36\*/澳\*/平\*)覆盖率 100%，复跑幂等(inserted=0) |
| kbisai↔FotMob 身份解析(独立实现,不复用 entity_resolution.py) | **已验证(2026-08-04)**:25 场真实目标 16 场成功(7 auto_ok + 9 needs_review)，9 场诚实 fail-closed(同刻多场缺别名/别名字面量不匹配，非 bug) |
| kbisai market_phase 跨接口复用 `_STATUS_GROUPS` | **部分 UNVERIFIED**:该枚举在 protobuf 比分端点验证过，跨到 matchAllOdds 复用未独立验证；320 条真实数据全部落在 statusId=1(NOT_STARTED)分支，未覆盖 in_play/finished |
| kbisai 英超第一轮目标公司覆盖(T-17~20d) | **诚实负结果**:比赛可发现(10/10)，但三目标公司均未发布赔率(其它公司已有真实数据)，需临近开球重新采集确认 |
