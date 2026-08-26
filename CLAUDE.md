# CLAUDE.md — all-win / 欧赢工程宪法

> 本文件只保存长期有效的产品边界、架构决策和工程纪律，不记录完成进度、临时任务或某次会话结论。
> 当前状态、数据量和未完成事项必须通过代码、数据库与 `docs/current-state.md` 重新审计，不得从旧文档猜测。
> 除非用户明确批准，任何实现不得偏离标记为“锁定”的条款。

## 1. 产品使命

all-win 是面向中文足球用户的专业数据分析订阅平台，同时也是站长每日制作视频、长图和口播稿的内容工作台。

核心体验是：

1. 用中文把一场比赛的数据、模型概率和不确定性讲清楚。
2. 将 FotMob 比赛事件与 NowGoal 赔率快照放在同一时间轴上，展示“同一时段观察到了什么”，不声称因果。
3. 同一份分析数据同时驱动网站页面、竖屏图卡、视频文案与字幕。

不得使用“因为盘口变化”“导致盘口变化”“必胜”“稳赚”“红单”“连红”等无法证实或收益承诺式表述。

## 2. 工作原则

### 2.1 先审计，再修改

每次任务开始必须：

1. 读取本文件、`README.md`、`DESIGN.md`、`docs/`、相关源码和测试。
2. 执行 `git status --short`，保留用户已有修改和未跟踪文件。
3. 通过代码、SQLite schema、真实行数和命令输出建立当前状态。
4. 把一次性的审计结果写入 `docs/current-state.md`，不得反向污染本文件。
5. 只修改当前任务所需文件，不顺手清理无关代码。

### 2.2 真实输出纪律

- 没有运行命令，不得声称已经构建、测试、部署或验证。
- 网络、凭证或外部服务不可用时，结论标记为 `UNVERIFIED`。
- DEMO 数据必须带 `is_demo=true` 或独立测试数据库，不能混入真实战绩。
- 不得编造数据源能力、样本量、模型表现、部署状态或用户数量。
- 不通过删除失败记录、选择起止日期或覆盖旧预测改善公开战绩。

## 3. 锁定技术栈

### 3.1 应用

- 前端：现有 Next.js 16 App Router + React 19 + TypeScript。
- 后端：FastAPI + Python。
- 数据库：SQLite + WAL，按写入特征拆库。
- 图表：ECharts，项目只保留一个通用图表库。
- 样式：现有 CSS Modules + `frontend/app/globals.css` 设计变量；不为追求统一而迁移 Tailwind。
- API 契约：Pydantic 为服务端单一真源，从 OpenAPI 生成 TypeScript 类型；不手写第二套相互漂移的 schema。
- 测试：pytest、Vitest/React Testing Library、Playwright。
- 生产入口：Cloudflare → Nginx → Next.js/FastAPI。
- 进程管理：systemd。
- 文件存储与备份：AWS S3。

### 3.2 当前阶段禁止引入

未经用户批准，不引入或迁移到：

- Vite 重写、Pages Router 重写或其他前端框架；
- PostgreSQL、Supabase、Prisma；
- Redis、Celery、Kafka；
- Docker/Kubernetes/ECS；
- Clerk、Auth0 等托管认证；
- 第二套重复的前后端业务逻辑；
- 多实例部署。

当且仅当真实指标证明单机 SQLite 或单实例成为瓶颈时，再提交迁移提案。不得为“以后可能用到”提前复杂化。

## 4. 运行拓扑

```text
中国大陆及其他地区用户
        ↓
Cloudflare：DNS / TLS / 静态缓存 / WAF / 基础限流
        ↓
AWS 东京 Nginx
        ├── /api/v1/*  → FastAPI 127.0.0.1:8000
        └── 其他路径   → Next.js 127.0.0.1:3000

Python Worker / systemd timers（按任务拆分为多个独立定时器，§13）
        ├── FotMob 采集与聚合
        ├── NowGoal 快照
        ├── 预测生成、锁定、赛后结算
        ├── 分析包生成
        └── SQLite 在线备份 → S3
```

Cloudflare 普通全球网络不能被描述为“中国大陆境内 CDN”。公开页面和匿名 JSON 应尽量预生成、可缓存；用户、会员、后台和导出请求必须回源且禁止共享缓存。

单实例阶段使用 Next.js 本地持久 ISR 缓存。只有扩展为多实例后才讨论共享缓存。

## 5. 仓库与数据库边界

### 5.1 目录

```text
all-win/
├── backend/
│   ├── api/                 # FastAPI 路由；薄路由
│   ├── auth/                # 身份、会话、CSRF、微信适配器
│   ├── commands/            # 可写业务命令
│   ├── queries/             # 只读查询
│   ├── db/                  # 连接工厂与 migration runner
│   ├── migrations/
│   │   ├── core/
│   │   ├── platform/
│   │   └── odds/
│   ├── ingest/              # 数据源采集
│   ├── providers/           # FotMob / NowGoal / Auth / Payment Adapter
│   ├── silver/
│   ├── models/
│   ├── eval/
│   ├── studio/              # analysis_bundle 与内容导出
│   ├── worker/
│   └── cli/
├── frontend/
│   ├── app/
│   ├── components/
│   ├── features/
│   ├── lib/
│   └── public/brand/
├── data/                    # 运行数据，不提交 Git
├── deploy/
│   ├── nginx/
│   ├── systemd/
│   └── scripts/
├── docs/
├── tests/
├── DESIGN.md
├── README.md
└── CLAUDE.md
```

目录迁移必须渐进完成。不得为了匹配目录图一次性移动全部现有模块；先增加兼容层和测试，再逐步迁移。

### 5.2 三个 SQLite 文件

#### `data/allwin.db`

保留现有 FotMob Bronze、i18n、Silver、模型特征和已有 Gold 产物。现有表不能被破坏性重建。对旧 schema 的调整必须先备份并通过 migration。

#### `data/platform.db`

保存：

- 用户、外部身份、会话；
- Role、Plan、Subscription、Entitlement；
- 产品与价格配置；
- 模型版本、正式预测快照、评估；
- 收藏、内容草稿、导出任务；
- JobRun、AuditLog、最小化 AnalyticsEvent。

#### `data/odds.db`

保存：

- provider 实体映射；
- NowGoal 赔率原始快照；
- 阵容和伤停变化快照；
- 赔率变化、事件变化与时间共现派生表；
- 采集运行和数据质量结果。

SQLite 文件必须位于 EC2 本地 EBS，不放在 EFS、S3 或其他网络文件系统上。

### 5.3 连接纪律

- 查询路径使用 `mode=ro` 只读连接。
- Command、Admin、Migration、Ingestion 使用独立可写连接和显式短事务。
- 每个数据库启用 WAL、foreign keys、busy timeout。
- API 请求内禁止执行长跑批、模型训练或批量采集。
- 一个数据库同一类写任务由单写者队列或进程内锁串行化。
- 活跃 SQLite 数据库备份使用 SQLite Backup API 或 `.backup`，禁止在 WAL 活跃时只复制主文件。

## 6. 数据架构

```text
FotMob → Bronze(core) ─┐
                       ├→ canonical entity/xref → Silver → Gold → API
NowGoal → Bronze(odds) ┘
```

### 6.1 实体解析和中文显示分离

- `dim_team_xref(provider, provider_team_id, canonical_team_id)` 只负责跨源身份。
- `dim_team_alias(canonical_team_id, alias, source)` 只负责别名解析。
- `dim_match_xref` 必须保证 FotMob 与 NowGoal 一对一，并保存 confidence、verified、主客是否反转。
- i18n 表只负责中文显示，不承担跨源主键对齐。
- 自动映射必须校验开球时间、主客方向和球队实体；低置信度进入人工审核，不得静默写成已验证。

### 6.2 时间戳

所有原始快照统一使用：

- `source_updated_at`：来源声明时间，可空；
- `observed_at`：本系统首次观察到变化的 UTC 时间；
- `ingested_at`：数据库写入 UTC 时间；
- `poll_run_id`：同一轮采集标识。

来源没有声明更新时间时必须为 NULL，不得用抓取时间伪装来源更新时间。用户文案写“系统于 observed_at 检测到”。

### 6.2.1 精确开球时间

canonical 比赛实体必须区分：

- `match_date`：比赛自然日，可用于列表和历史聚合；
- `kickoff_at_utc`：来源提供的精确 UTC 开球时刻，可空。

来源只提供日期时，`kickoff_at_utc` 必须为 NULL，不得补成当天 00:00 并当成精确时间。正式预测赛前判定、赔率轮询、`FINAL`、`pre_match/in_play` 和倒计时必须使用精确 `kickoff_at_utc`。

缺少精确开球时间的比赛不得生成新的正式赛前预测，不得声称某条快照是收盘快照；页面和分析包必须暴露相应数据质量提示。

### 6.3 赔率与赛前信息快照

只有具有精确 `kickoff_at_utc` 的比赛才允许赛前轮询；已开球比赛是否继续采集由
明确的 in-play 任务决定，不能继续伪装为赛前采集。以下三条是所有来源共同遵守
的节奏**下限**（各来源可以在其上分级更密，但不得比这条更稀，`backend/ingest/
poll_windows.py` 的代码注释同样把这三条标注为下限）：

- **首次发现即采**：该 (来源, 比赛/联赛) 在持久化的到期状态里尚无任何采集
  记录时，不论距开球多远都立即采集一次；小联赛可能要到赛前 4–5 天才真的有
  盘口，这一枪拿不到数据属正常，不是失败告警；
- 距开球 2–72 小时：同一来源和比赛至少间隔 15 分钟；
- 距开球 0–2 小时：同一来源和比赛至少间隔 5 分钟。

多个 systemd 定时器各自按自己的频率触发"到期判断"（定时器触发 ≠ 真的发起了
一次外部请求；调度拓扑见 §13）；真正是否请求数据源、间隔多久，统一由
`backend/ingest/poll_windows.py` 按来源查表决定。当前按来源实现的分级表（均
满足上面三条下限，只是在窗口内部分得更细）：

- **NowGoal 赔率**（`CADENCE_NOWGOAL_ODDS`，`allwin-odds.timer` 每 5 分钟触发
  到期判断）：距开球 72h–24h 每 24 小时一次；24h–6h 每 6 小时一次；6h–0h 每
  1 小时一次；另加距开球 ≤15 分钟仍未在窗内轮询过时强制补采一次
  （`last_call`，用于把 `FINAL` 快照尽量拉近开球时刻）；首次发现即采同上。
- **FotMob 阵容/伤停**（`cadence_fotmob_lineup(league_id)`，`allwin-lineup.timer`
  每 5 分钟触发到期判断）：节奏窗口为开球前 72 小时，窗内三档——T-72h 到
  T-24h 每 24 小时一次；T-24h 到"观察窗起点"每 12 小时一次；观察窗起点到
  开球每 5 分钟一次，且**不做早停**（真正判定"官方已确认首发"从而可以提前
  停止追问的逻辑属于 P5，尚未实现——当前是老实地全程每 5 分钟采到开球）。
  观察窗起点：五大联赛（英超 47 / 西甲 87 / 意甲 55 / 德甲 54 / 法甲 53）为
  开球前 1.5 小时，其余已接入联赛为开球前 1.25 小时。**候选池宽度独立于
  节奏窗口**，为 168 小时（`FOTMOB_LINEUP_CANDIDATE_WINDOW_HOURS`，与
  NowGoal 的 `DISCOVERY_WINDOW_HOURS` 同构）——候选池必须严格宽于节奏窗口，
  否则本节第一条"首次发现即采"下限对该来源结构性不可达；168 小时以外的
  比赛一生只补采一枪，采完即被持久化的到期状态挡住，不会无界重复。
  "首次发现即采"这条下限对 FotMob 阵容在 168 小时以内可达，以外仍不可达
  （与 NowGoal 同一近似，不是完全满足"不论距开球多远"）。
- **赛后完赛数据补全**（`fotmob_incremental_multi`，`allwin-postmatch.timer`
  每 30 分钟触发；这是赛后而非赛前信息，节奏与上面两条的赛前下限无关，单列
  于此是因为同属"到期判断"这一套机制）：不是固定周期节流，而是事件驱动——
  一个联赛内存在至少一场 `status != 'Finish'` 且已过开球 2.5 小时的比赛即
  视为该联赛到期，立即补抓；没有这种比赛时仍保留 6 小时兜底档。为防止
  FotMob 数据源永远不把某场比赛翻成 `Finish` 而导致该联赛无限期每轮都到期，
  单场比赛的"仍未解决"检查次数上限为 20 次（约 kickoff+12.5h），达到上限后
  停止对该场比赛单独触发到期、记录耗尽原因，并发一次 CRITICAL 告警，不静默
  丢弃（`backend/ingest/postmatch_retry.py`）。

其余落库规则：

- 轮询到期状态必须可持久化或由最后一次 poll run 可靠推导，进程重启不能造成无界重复采集。
- 只有 canonical payload hash 相对上一条发生变化才落库。
- `market_phase` 必须由来源状态、精确 kickoff 和观察时间共同判定；信息不足时使用 `unknown`，不得仅按自然日判断。
- `FINAL` 是开球前最后一个 `market_phase='pre_match'` 的有效快照，明确排除 `unknown` 和 `in_play`。
- 同一轮 FotMob 比赛请求应复用原始 payload，阵容与两队伤停使用相同 `poll_run_id` 和 `observed_at`。
- 首先支持真实验证过的公司和市场；不得把“抓到一家公司的初盘/最新”描述为完整多公司时间序列。
- 历史回填能力、时间粒度和公司覆盖写入 `docs/data-sources.md`；不可验证则标 `UNVERIFIED`。
- 判定"比赛已完赛、该落库明细数据了"不得只精确匹配单一 `status` 值（如
  `status='NotStarted'`）。赛程同步等其它写路径可能在比赛进行中把 `status`
  改写成 `InPlay` 等中间态，一旦离开被精确匹配的那个值就永久漏检、且没有
  任何后续机制能再捞回来（2026-08-24 真实事故：`backend/scheduler.py` 的
  完赛判据曾经精确匹配 `status='NotStarted'`，21 场比赛卡在 `InPlay` 后
  永久零数据，直到人工发现）。正确判据是"该结束却还没有明细数据"（如
  `status != 'Finish'` 且已过开球+阈值，与 `backend/ingest/poll_windows.py`
  的 `league_stale_unresolved_match_ids` 同一口径），不是某个中间状态的
  白名单。且任何"新数据只在事件发生的那一刻写入"的落库任务，都必须配一条
  可按 ID 重跑的补采路径（如 `backend/cli/reingest_matches.py`）——没有
  这条路径，任何一次漏检都是永久的，不会被后续轮询自动纠正。
- **「实现了 CLI 开关但没在 `backend/worker/runner.py` 的 argv 里接线」与
  「根本没实现」在生产上等价**（2026-08-24 真实事故：`--write-match-details`
  早已完整实现，但 `runner.py` 注册 `fotmob_snapshot` 的 argv 漏了它，导致
  该任务跑了 1.4 万+ 次、每轮都抓回整份 payload 又当场丢弃，场馆/天气/球队
  配色三类数据在 16934 行 dim_match 里非空数为 0/0/1，且 CLI 自身的全部
  测试一直是绿的——这类缺陷没有任何常规测试能抓到）。任何新增的采集开关，
  必须同时有一条断言 worker argv 包含它的测试
  （`tests/backend/test_worker_argv.py`）。
- **任何"标识一条记录属于哪个赛季/哪个分区"的参数，绝不能有一个看起来合理、
  实际可能是错的字面量默认值**（2026-08-25 真实事故：`backend/fotmob_client.py::
  parse_match_dim()` 的 `season` 参数曾硬编码默认值 `"2025/2026"`，
  `backend/ingest/ingest_match.py` 只在调用方显式传参时才透传 `season`、
  不传就静默漏给这个旧默认值兜底；`backend/ingest/ingest_league.py` 和
  `backend/verify/verify_league.py` 的命令行 `--season` 参数同样默认
  `"2025/2026"`。一次用 `ingest_league.py --match-ids ...` 对个别场次做手动
  补采、忘了带 `--season`，把 2026-08-21/22 的 5 场英超新赛季揭幕战全部
  错标成上赛季 `Season='2025/2026'`——分数、xG、射门、阵容等比赛本身的数据
  全部正确，只有赛季这一个字段错了，而且这类错误**不会被下游任何一致性检查
  发现**：这 5 场很快变成 `status='Finish'`，此后 `scheduler.py` 的"新完赛"
  扫描（`league_stale_unresolved_match_ids`）判定"已解决、不需要再管"，
  `job_runs` 里再也不会出现这几场的踪迹；按赛季过滤的查询（球队风格象限、
  联赛列表、榜单……）会把它们连同数据一起悄悄漏掉，而不是报错或留白——
  站长凭真实赛程"英超揭幕轮应该有 10 场"的常识才发现只查到 5 场。
  当日先做了"season 改必填"的止血，随即发现那仍是"靠调用方传对"——打错字、
  复制旧命令产生完全一样的损坏；且按同一推导全量核对生产 18,056 行，共
  **878 行**错标（不止那 5 场），全部错行的标签恰好都是旧默认值 `2025/2026`。
  终版修复（2026-08-25 当日二次收口）按 FotMob 架构（APK 反编译实证：其
  Match 模型**没有 Season 字段**，赛季由比赛 id 隐含、按需从服务端派生）：
  **`Season` 是赛程同步独占的派生列，明细抓取根本没有写它的能力**——
  `parse_match_dim()`/`ingest_match()` 彻底删除 `season` 形参，`ingest_match`
  的 dim_match 写入改为列作用域 upsert（`MATCH_DETAIL_OWNED_COLUMNS`，
  不含 Season；此前整行 `INSERT OR REPLACE` 会把赛程同步写对的赛季用手填值
  冲掉，正是事故机制），行不存在时在任何网络请求前 fail closed；赛季值只来自
  provider 回声校验/发现（`backend/ingest/season_identity.py`，五份散落实现
  收敛为一份）；存储层由 `dim_league_season_regime` 制度表（按 effective_from
  分版本——日职 2026-07 真实换制）+ `dim_match` 触发器
  （`migrations/core/0011`）保证写入的 Season 必须等于按 (League_ID, Date)
  推导的赛季，未登记联赛直接拒绝；质量门 G12 盯超出存量基线的新增漂移。
  存量 878 行**只报不改**（`backend/cli/season_audit.py`，无 --commit），
  修复需另行决策（牵涉 Silver 分区重建/SEO URL/模型切分）。通用纪律：
  **标识"这行数据属于哪个时间分区/赛季/批次"的值，能派生就绝不手填；
  必须外部提供时只认来源回报的值，且写入端要有与派生一致性的存储层校验**——
  "必填参数"只是把静默错误变成大声错误，仍不是正确性保证。

### 6.4 时间共现

- `silver_odds_moves`：盘口/赔率变化点；
- `silver_event_moves`：阵容、伤停和可观察事件变化点；
- `gold_move_cooccurrence`：固定时间窗内的同期事件。

模块可以内部使用 `attribution` 作为技术名称，但表字段和用户文案不得使用 `cause`、`reason_for_move` 或其他因果名称。

## 7. 认证与账户

### 7.1 核心原则

- `users.id` 是网站内部 UUID，是所有权益、预测操作和审计的唯一用户主键。
- OpenID、UnionID、用户名等全部是可绑定、可解绑的外部身份，不能直接作为业务主键。
- 微信昵称和头像不是身份凭证，不能决定权益。
- Role 只表达身份；Plan/Subscription/Entitlement 表达付费能力，二者不得合并。
- 浏览器不保存 API token 到 localStorage、sessionStorage、URL 或可读 NextAuth session。

### 7.2 身份表

```text
users
auth_identities
auth_sessions
oauth_states
device_login_requests
account_links
```

`auth_identities` 至少包含：

```text
user_id
provider                 # wechat_oa / wechat_open / password / email / phone
provider_app_id
provider_subject         # OpenID 等
union_id                 # 可空
created_at
last_used_at
```

唯一约束为 `(provider, provider_app_id, provider_subject)`。UnionID 只能在公众号绑定到同一微信开放平台且真实返回后使用，不能推测或伪造。

### 7.3 微信登录（2026-08 修订，经用户批准）

唯一认证路线是**已认证服务号「带参数二维码」+ 消息推送 webhook**。
网页授权（`snsapi_base`）路线已废弃且不得恢复：微信「网页授权域名」要求域名通过
ICP 备案，备案硬前提是网站部署在中国大陆；本项目部署 AWS 东京，且出于内容合规
考量不迁回大陆备案。用户扫码后在微信 App 内触发事件、微信服务器主动回调本站，
全程不在微信内打开本站网页，因此不受备案约束。

登录流程（三种环境同一条链路，仅界面提示不同）：

1. 浏览器创建 `device_login_request`，获得只留在浏览器内存的 secret。
2. 服务端调用公众号「生成带参数的二维码」接口（`QR_STR_SCENE`，
   `scene_str` = 公开 request id），二维码内容绝不包含浏览器 secret。
3. 用户微信扫码（电脑端扫屏幕；微信内长按识别；手机浏览器截图后相册识别），
   微信服务器把 `SCAN` / `subscribe`（带 `qrscene_` 前缀）事件 POST 到本站 webhook。
4. webhook 校验共享 Token 签名 + 时间戳新鲜度（±300s）+ nonce 一次性防重放，
   按 openid 获取或创建内部 User，原子批准该 request（幂等：重复投递不改状态）。
5. 浏览器轮询时必须同时提交 secret；成功后原子消费 request 并设置会话 Cookie。
6. request 短期有效、只能消费一次、状态持久化到 SQLite，不能存在进程内存字典。

公众号 `access_token` 每个 AppID 全局唯一、重新获取会使上一个立即失效：必须持久化
缓存（platform.db）、临过期才串行刷新，不得多处各自获取互相顶掉。AppSecret 与
webhook Token 只存在 FastAPI 环境变量和服务端请求中。

已知外部单点（如实声明，不做隐藏降级）：「生成带参数的二维码」接口权限绑定微信
认证年审，年审过期该接口返回 `errcode=48001`——必须结构化记录并向用户界面如实
反馈“扫码服务暂不可用”，不得伪装成功。

不得沿用旧项目的以下模式：普通 `random` 四位验证码、只存在内存的登录状态、把 JWT 放进查询参数、把 API token 暴露到客户端 session、用 `users.openid='USER_xxx'` 伪造其他身份。

如果 `WECHAT_AUTH_ENABLED=1`，production 启动时缺少 AppID、AppSecret、
`WECHAT_WEBHOOK_TOKEN` 或 HTTPS 对外地址必须拒绝启动。Development 可以使用显式
Mock Provider，但 Mock 在 production 必须 fail-fast。

认证开关必须具有三种明确状态：

- production + `WECHAT_AUTH_ENABLED=0`：公开站点必须可以无微信凭证启动；微信登录端点返回结构化 `AUTH_DISABLED`，不得尝试实例化真实 Provider；
- production + `WECHAT_AUTH_ENABLED=1`：只能使用 Real Provider，缺 AppID、AppSecret、webhook Token 或 HTTPS 对外地址必须 fail-fast；
- development：只有显式配置时才允许 Mock Provider，production 检测到 Mock 必须 fail-fast。

扫码登录的验收必须覆盖“浏览器创建 → webhook 签名事件批准 → 浏览器轮询 → 原子
消费 → 设置会话”的完整流程；webhook 入站链路不依赖 Provider，必须可用签名 fixture
离线验证。登录页必须渲染真实二维码图像，不能只展示待编码 URL。真实微信服务器的
出站能力（access_token、二维码创建）与入站回调在拿到真实凭证并完成公众号后台
配置前一律标 `UNVERIFIED`。

### 7.4 网站会话

MVP 使用数据库持久化的 opaque session，不使用浏览器可见长效 JWT：

- 登录生成至少 256 bit 随机 token；数据库只保存 SHA-256 hash。
- Cookie：`Secure`、`HttpOnly`、`SameSite=Lax`、host-only、`Path=/api/v1`。
- 会话可撤销、可过期、登录后轮换；原始 token 不记录日志。
- 写请求校验 Origin/Referer allowlist，并使用 CSRF token。
- 登录、回调、会话、账户接口全部 `Cache-Control: private, no-store`。
- 付费用户后续应能绑定恢复身份；MVP 未接真实短信/邮件时必须在界面如实说明。

## 8. Role、每日精选授权（2026-08-17 权限口径修正，经用户批准，废止此前的
「三段可见性」模型）

商业定位：站点是短视频/自媒体的流量载体与用户沉淀池。**除"每日精选"外，
全站比赛内容对任何人（含匿名）都免费**，不存在"登录才免费"或"付费/免费"
的内容分层。登录只用于身份类个人功能（收藏、关注、个人记录、账户设置、
每日精选权限查询），不解锁任何比赛数据。当前不接支付、不设订阅套餐、不设
Premium 层级——付费板块「每日精选」的授权只能由管理员按"用户 + 单场比赛/
单条精选"逐条发放（含兑换码，兑换码同样只对应一条精选，不是一整个套餐）。

Creator Studio（管理后台同理）不属于上面这类"任何登录用户都能用"的身份类
个人功能——**Studio 是 analyst/admin 专用工具，普通登录用户（role=user）
登录后依然看不到，未登录用户同样看不到**（`backend/api/routes_studio.py::
require_analyst` 要求 `ctx.role in ("analyst","admin")`，`frontend/app/
studio/page.tsx` 页面注释同样写明"analyst/admin 专用"）。这一次每日精选
按场授权改造完全没有触碰 Studio 的这层角色门禁，二者是两套独立机制，不要
混为一谈。

### 8.1 维度

- 认证状态：anonymous / authenticated——**只影响能否使用身份类个人功能，
  不影响任何普通比赛内容的可见性**。
- Role：user / analyst / admin，只表达身份，不承载**付费**能力（不因为
  Role 不同而看到不同的比赛数据分层）；但 Role 仍然是 Creator Studio 这类
  内部工具的合法功能性门禁——`role=user` 的普通登录用户和管理员一样都不能
  进入 Studio，这不是本次任务要移除的"免费/Premium"分层，是产品既有的
  "内部工具 vs 面向用户的产品"边界，不受影响。
- Plan/Subscription/Entitlement（`backend/auth/entitlements.py`、
  `plans`/`plan_entitlements`/`subscriptions` 表）仍然存在，但**不再驱动任何
  普通比赛内容的可见性**——free/member/daily_picks（以及已下架的 pro/premium，
  is_active=0，行保留供历史订阅外键引用）这套 Plan 机制目前只是历史遗留的
  账户身份展示层，不得在任何新代码里用它裁剪比赛内容。
- 每日精选真正的访问控制单位是 `reco_access_grants` 表（见 §8.2），不是
  entitlement。

普通比赛内容（首页比赛卡片、比赛详情、胜平负概率、MODEL/MARKET_BASELINE
概率、积分榜、近期及赛季数据、数据可视化、射门图、xG/xGA、阵容和伤停、
赔率当前值和时间线、比赛分析要点、同期事件、联赛和球队资料）**对匿名和
登录用户返回完全相同的响应字段**——没有 `requires_login`、`tier`、
`free_outcome`、`locked_outcomes`、`is_premium` 这类裁剪字段，也没有
"登录才能看完整概率/完整赔率时间线/完整联赛列表"这类分支。

`reco:track_record`（每日精选战绩归档）同样对匿名开放：命中/未中/走水与
作废全展示（不挑选、不隐藏），与模型公开战绩（`/api/v1/track-record`）
是同一先例——这是站点自身的公开运营记录，不是"个人"内容，不受每日精选
按场授权约束。

### 8.2 每日精选按场授权

每日精选是全站唯一需要管理员授权的内容，且**必须按"用户 + 单条精选
（reco_slip）"授予**——拿到一场比赛的授权不能看到其它场次，全局
entitlement/plan/subscription 都不能作为授权判据。

```text
reco_access_grants
  id / user_id / slip_id / status(active|revoked)
  granted_at / granted_by / revoked_at / revoked_by / note
```

- `GET /api/v1/reco/daily`（列表）：未登录 401；已登录 200，每条 slip 按
  当前用户是否有 active 授权二选一投影——有授权给完整正文，无授权只给
  存在性 + `access_required:true`（标题/腿/赔率/理由等字段物理不下发，
  不是置 null）。
- `GET /api/v1/reco/daily/{slip_id}`（单条正文）：未登录 401；已登录但
  无 active 授权 403（响应体只有 `{code:"reco_access_required"}`，不含
  任何正文字段）；已登录且有 active 授权 200。撤销后立即再次访问变回 403。
- `GET /api/v1/reco/my-access`：已登录用户查询自己的授权记录（个人功能，
  允许要求登录）。
- Admin 授权/撤销（`POST /admin/reco/access-grants`、
  `POST /admin/reco/access-grants/{id}/revoke`）：admin + CSRF，每次操作
  写 AuditLog；admin 角色本身不自动获得任何精选内容访问权，管理端预览走
  独立的 `GET /admin/reco/slips/{id}/preview`，不能污染普通用户接口。
- 兑换码（`backend/commands/redeem.py`）：一个兑换码只对应一条具体的
  `slip_id`，兑换成功即调用 `grant_access(user_id, slip_id)`，不再是
  "兑换一整个 daily_picks 订阅"。
- 旧的全局 `reco:daily` entitlement 不再驱动任何内容访问判定；历史
  `subscriptions` 行保留（不做破坏性删除），但持有历史订阅不会让用户
  无条件解锁任何 slip——必须由 `reco_access_grants` 显式授权。

### 8.3 权限校验

- 前端只负责体验，后端是权限真源；普通比赛内容的后端查询/路由层不得再有
  entitlement 分支。
- 每日精选的访问判定必须查 `reco_access_grants`，不得回退到 plan/
  entitlement/subscription。
- Admin 不能只靠隐藏路由或秘密 URL。
- 未接真实支付；每日精选授权一律走管理员发放、撤销和按场兑换码；所有
  操作写 AuditLog。
- SEO/GEO 面（sitemap、robots、llms.txt）：普通比赛内容页面现在对匿名
  完全可见，可以按需纳入可索引范围；每日精选正文页面仍需登录+授权，不
  纳入 sitemap。

## 10. API 与缓存

### 10.1 路径

新接口统一 `/api/v1`。旧 `/api/league/...` 在前端迁移完成前可以保留兼容层，但必须标记 deprecated，不能继续扩展。

核心接口：

```text
GET  /api/v1/leagues
GET  /api/v1/leagues/{id}/standings
GET  /api/v1/leagues/{id}/fixtures
GET  /api/v1/matches
GET  /api/v1/matches/{id}
GET  /api/v1/matches/{id}/odds
GET  /api/v1/matches/{id}/cooccurrence
GET  /api/v1/products

POST /api/v1/auth/wechat/device
POST /api/v1/auth/wechat/device/{id}/claim
GET  /api/v1/auth/wechat/webhook
POST /api/v1/auth/wechat/webhook
POST /api/v1/auth/logout
GET  /api/v1/me

POST /api/v1/favorites
POST /api/v1/exports
GET  /api/v1/admin/...
```

### 10.2 缓存边界

- `/_next/static/*` 和带 hash 的静态资源：长期 immutable。
- 匿名首页、比赛列表和公开比赛页：按数据新鲜度使用 `s-maxage`。
- 会员数据、登录、账户、Studio、Admin、导出：`private, no-store`。
- 带 Session Cookie、Authorization 或 Set-Cookie 的响应不得进入 Cloudflare 共享缓存。
- 公共 HTML 不因用户身份变化；登录后会员数据由私有 API 加载，避免缓存变体泄漏。
- Next.js Server Component 调 FastAPI 时优先走 `127.0.0.1` 内网地址，不绕 Cloudflare 回源。

### 10.3 API 契约与运行时地址

- 每个返回 JSON 的 FastAPI operation 必须声明明确的 Pydantic 成功响应模型和统一错误模型。
- Redirect、204 和文件流可以不使用普通 response model，但必须在 OpenAPI 中明确声明状态码和 content type。
- OpenAPI 必须从实际 FastAPI app 生成，前端 TypeScript API 类型必须从该 OpenAPI 自动生成。
- 前端不得手写与 API 响应重复的 DTO；页面展示类型只能从生成类型派生。
- 契约检查必须确保所有 JSON 2xx response 都有非空 schema，并验证生成类型没有漂移。

生产环境的浏览器请求必须使用同源 `/api/v1`，不得默认访问 `127.0.0.1` 或 `localhost`。Next.js 服务端可以通过 `INTERNAL_API_BASE` 请求 localhost FastAPI。任何 `NEXT_PUBLIC_*` 变量都必须在 `next build` 前确定，不能依赖 systemd 运行期注入改变已构建 bundle。

## 11. 前端与设计

### 11.1 页面

```text
/
/matches
/matches/[matchId]
/track-record
/about-model
/pricing
/login
/account
/studio
/studio/matches/[matchId]
/admin
```

现有联赛排名、赛程、球员和球队统计页面必须保留并逐步接入新导航与 API，不能为做新首页而删除。

比赛详情是核心页，顺序为：

1. 比赛头部与数据更新时间；
2. 完整主胜/平局/客胜概率（对匿名与登录用户一致）；
3. 支持证据与反向证据；
4. xG、射门、近期表现等可视化；
5. 赔率时间轴；
6. 同期事件；
7. 模型版本、cutoff、局限和赛后记录。

### 11.2 设计纪律

- `DESIGN.md` 是视觉系统真源；现有用户对该文件的未提交修改必须保留。
- 中文默认，面向中文用户默认按北京时间（UTC+8）展示比赛/预测/赔率等赛程相关时间戳，数据库统一 UTC；账户、运维等非赛程语境可按用户本地时区展示。
- 响应式以手机为第一优先；窄屏主动减少列，不缩小到不可读。
- 图表必须有文字摘要、单位、时间范围、空状态和数据更新时间。
- 不用假倒计时、虚构在线人数、诱导弹窗或赌场式视觉语言。
- 深浅模式使用同一份 JSX 和 CSS 变量；禁止复制两套组件树。
- 字体和图片自托管，不依赖中国大陆不可稳定访问的外链资源。
- 用户可见文字最小 12px（30-40 岁手机用户可读下限）；正文 14px 起，
  核心概率/比分用大号数字。图形内部标注（球场站位、徽章首字母）不受限。
- 颜色语义固定：深蓝=品牌/大标题，青绿=主要操作/选中，黄绿=推荐发布/关键
  数据（只用于深色底），橙=等待更新/临近开球，红=真实错误或不可用，
  灰=辅助说明。"推荐待发布""数据等待刷新"不得用红色。
- 赔率涨跌方向（2026-08-26 赔率展示重做）用专用 token `--odds-up`（品牌青绿
  深阶）/`--odds-down`（板岩蓝），**刻意不借用红或橙**——涨跌是中性的价格
  移动，不是"错误"也不是"等待更新"，用那两色会和上面锁死的语义冲突（这正是
  NowGoal 客户端的反面教材：它用中国股市的红涨绿跌，把红色一色多用到涨、
  比分、盘口值、主队；同时"封盘"复用了跌的绿，语义全乱）。方向必须**颜色 +
  箭头(↑/↓) + 带符号幅度三通道冗余**，色盲用户不依赖颜色也能读；两个方向色
  对 surface/surface-soft 浅深四种底实测均 ≥4.7:1（对比 NowGoal 那套涨 4.23、
  跌 3.78、平 3.12 全不达标）。徽标类小字号最易漏检对比度（字号最小 + 底色
  最浅），改配色时的合成对比度断言必须覆盖徽标，不只覆盖正文数字。
- 状态标签不堆胶囊墙：每张比赛卡最多"联赛·轮次 + 比赛状态 + 可行动提示"
  三层；赛季等第二层信息进正文行。内部枚举值（MARKET_BASELINE 等）
  不得直接出现在用户界面。

### 11.3 图表实现纪律（2026-08-24，经真实事故确认）

2026-08-23 势头图（`visualMap` 开区间配置在项目实际使用的 ECharts ^6.1.0
上抛异常，整图不渲染且异常冒泡到路由级错误边界拖垮整个比赛详情页）与射门
落点图（非进球标记半透明压在绿茵球场上，合成对比度只有 1.09~1.17:1，等于
隐形）两个 bug，都在 `vitest run` 全绿的情况下上线——现有图表测试全部只测
`summarizeMomentum`/`buildBuckets`/`filterShots` 这类抽出来的纯函数，从不
真的把构造出的 `option` 交给 ECharts 渲染一次，也没有任何测试算过标记颜色
和背景的合成对比度。据此固化以下纪律：

- **纯函数测试不算图表验证。** 凡是构造 ECharts `option` 的组件，必须导出
  一个可独立调用的 `buildOption`（或等价的纯函数），并有一条测试用项目
  自带的 echarts 做一次 headless 渲染（`echarts.init(null, null,
  {ssr:true, renderer:'svg'})` + `setOption` + `renderToSVGString()`）——
  异常在这里必须真实抛出，不能被组件内部悄悄吞掉。参见
  `frontend/tests/chart-render-smoke.test.ts`。
- **ECharts 6 的 `visualMap.pieces` 必须给闭区间。** 只给 `min` 或只给
  `max`（包括 `gte`/`lt` 写法）在 `MarkLineView` 里会抛
  `Cannot read properties of undefined (reading 'coord')`；加
  `type:'piecewise'` 不能规避。边界必须从数据的实际范围算出来，不能留空。
- **图上标记的可见性必须对着真实渲染背景算合成对比度，不能只挑颜色板看着
  顺眼。** 换配色时把 `opacity` 合成后的结果与实际背景比，非文字图形
  ≥3:1（WCAG）。有色背景（球场、热区等）优先用中性底色（浅 `#F8FAFA` 系 /
  深 `#333333` 系，同 FotMob 官方 App 的射门图做法），把颜色信号完全让给
  标记，而不是让标记去将就一个高饱和度背景——品牌色压在中性底上能稳定拿到
  4.5:1 以上，压在高饱和背景上无论怎么调都很难。参见
  `frontend/tests/shot-map-contrast.test.ts`。改了标记色必须同步检查图例
  颜色，两者对不上本身就是没有真的看过一眼的信号。
- **描边/强调色不能跨主题硬编码一个值。** 白色描边在浅色系背景上会失效
  （近乎白压白），黑色描边在深色系背景上同理。凡是需要"不管当前是浅色
  模式还是深色模式，都要跟背景反向"的颜色，走 `useChartColors()` 暴露的
  主题感知 token（如 `ink`），不要在组件里写死一个十六进制值。
- **单个图表异常不得白屏整页。** 所有图表通过共享的 `EChart` 封装渲染
  （`frontend/components/EChart.tsx`）；该封装自带两层防线——内部
  `setOption` 调用 try/catch（兜住命令式 API 抛出的异常，这类异常不保证
  被 React 错误边界捕获）+ 外层 `ChartErrorBoundary`（兜住 `option` 计算
  本身在渲染阶段抛出的异常）。新增图表组件不需要重新实现这两层，只要走
  `EChart` 封装即可自动获得；不要绕开它直接调 `echarts.init`。
- **图上的聚合数字必须与图上画的点同源，且不得把缺失值当 0。**（2026-08-24
  真实排查确认）射门图底部「N 次射门、M 球、xG 合计 Z」三个数字必须同时取
  自筛选后的集合——这一条当时的代码是对的，但**没有任何测试断言过它**：
  `frontend/tests/` 全目录 grep「次射门」零命中，等于这条正确性完全靠人肉
  维持。同一次排查还查出两个真实缺陷：① **FotMob 的 shotmap 把乌龙球记在
  「打进自家球门那一队」名下**（全库 1022 条 xG 为 NULL 的射门 100% 是
  乌龙球，avg X_Coord=5.34 vs 正常进球 94.78，即本方球门端），直接按
  `is_home` 分组数进球会归错队——对照实验 400 场含乌龙球比赛错 392 场
  （98%）；② `xg ?? 0` 把缺失 xG 静默当 0 累加，与 `MatchDataModules.tsx`
  引用 §6.2 明确拒绝这么做的既有纪律自相矛盾。据此固化：**凡是在图上展示
  聚合数字的组件，聚合逻辑必须抽成可独立调用的纯函数并有测试断言它随筛选/
  输入变化；缺失值一律不得静默填 0，要么排除、要么如实标注**（进球数按
  受益方计乌龙球，见 `buildShotMapSummary` / `summarizeSide` 与
  `backend/queries/match_report.py::_own_goal_fields`）。
- **对比度安全阀不能对"差一点点"和"差很多"一视同仁，直接丢弃真实数据。**
  （2026-08-26 真实事故，站长报告）势头图西甲瓦伦西亚 vs 皇家贝蒂斯的队伍
  配色和 FotMob 官方不一致：查证后端数据完全正确（生产库存值与 FotMob
  实时接口逐字节一致，`teamColors.lightMode.home="#ff671f"`），根因在
  `frontend/components/charts/matchTeamColors.ts::resolveTeamColor()`——
  真实球队色对着实际渲染背景算出的对比度只要低于阈值（`MIN_CONTRAST=3`），
  不管差多少，一律直接丢弃换成品牌兜底色。瓦伦西亚的橙色对势头图白色卡片
  背景是 2.91:1，只比阈值差 0.09，就被整个换成了和 FotMob 毫无关系的品牌
  青绿。修复：新增 `nudgeForContrast()`（`colorContrast.ts`），勉强不达标时
  在一个小明度预算内（`MAX_NUDGE_LIGHTNESS=0.06`，约 6% HSL 明度）朝"远离
  背景"的方向微调，救回一个肉眼几乎看不出差别但真正达标的颜色（瓦伦西亚
  案例微调后是 `#ff6015`，对比度 3.03:1）；预算内救不回来的（真正差得远，
  如既有回归测试的 `#035db8` 对深色球场需要约 11% 明度）仍然正确回退品牌
  色——**不是把安全阈值整体放宽，是不让阈值边缘的真实数据和真正不安全的
  数据共用同一种"一刀切丢弃"处理方式**。实现该微调时踩过一次真实的方向
  判断反了的 bug（该判断前景比背景暗时应该继续变暗、比背景亮时应该继续
  变亮，写反会把颜色调去让对比度变得更差而不是更好）——凡是实现"朝目标
  调整某个数值"这类逻辑，必须对着真实案例算出的期望方向写测试断言方向，
  不能只断言"调整后达标"这一个终态（那样测不出方向反了但凑巧还是达标的
  情况）。见 `frontend/tests/color-contrast-nudge.test.ts`。
- **"真实点击测试"如果只断言字段值、不断言对象引用，测不出 ECharts 内部
  克隆数据这类 bug。**（2026-08-26 真实事故，站长报告）2026-08-25 射门图
  标记形态重做时把点击详情面板一起换掉了，上线后点击任意射门标记完全没有
  反应——连改动前就有的旧详情框都消失了。查证：`ECharts` 的 custom 系列在
  `chart.setOption()` 内部处理时，会把 `data[i]` 里嵌套的非原始值（这里是
  塞进 `{value, shot: s}` 的 `shot` 对象）克隆一份；点击事件 `params.data.shot`
  拿到的是结构相同但**引用不同**的副本——用项目自带 echarts 在 jsdom 里
  `renderer:"svg"` 真实 `init`+`setOption`+zrender 事件派发复测，
  `received[0].data.shot === target` 实测为 `false`，证实这不是"jsdom 测试
  环境模拟不到位"或"canvas 渲染器特有"，是 ECharts 本身跨渲染器的通用行为。
  真正的根因是 `resolveSelectedShot()` 下游用 `plotted.includes(selected)`
  做**引用相等**判断——`selected`（点击拿到的克隆副本）永远不会通过，详情
  面板因此永远拿不到非空的 `activeSelected`。`frontend/tests/
  shot-map-click.test.ts` 当时号称"真实点击测试"、也确实用了真实 ECharts
  渲染和事件派发，但断言只写了 `resolved!.player_id === "clicked"`（字段级
  相等），从未断言过 `resolved === target`（引用相等）——"用了真实渲染管线"
  和"断言对了要害"是两件事，前者不能替代后者。修复两处：①
  `resolveClickedShot()` 改为 `seriesName==='shots'` 时优先信任 `dataIndex`
  （直接从调用方传入的 `ordered` 数组按下标取，不经过这条克隆路径），
  `.data.shot` 降级为它本身没有 `dataIndex` 时的兜底；②
  `resolveSelectedShot()` 从引用相等改为结构化 key 匹配（球员+分钟+半场+
  坐标+结果的组合），返回 `plotted` 里的真实引用而不是 `selected` 本身，
  双保险防止未来任何新代码路径重演"选中态依赖对象引用是否被下游意外克隆"
  这一类问题。据此固化：**测试如果要证明"点击后能正确关联到原始数据对象"，
  必须显式断言引用相等（`toBe`），断言字段值相等（`toEqual`/取字段比较）
  证明不了这一点，两者在这类 bug 上是完全不同的两条命题**。见
  `frontend/tests/shot-map-click.test.ts` 与
  `frontend/tests/shot-map-chart.test.ts` 里对应的克隆场景回归测试。

### 11.4 Server/Client Component 边界纪律（2026-08-26，经真实生产事故确认）

2026-08-25 晚间部署（进攻区域图功能）上线后，比赛详情页**整页白屏**（服务端
渲染阶段直接抛异常，落到通用错误边界，用户点开任何一场比赛都只看到"页面
出错了"）。生产 `journalctl -u allwin-web` 里唯一的错误签名：

```
⨯ Error: Attempted to call zoneSplitFrom() from the server but zoneSplitFrom
is on the client. It's not possible to invoke a client function from the
server, it can only be rendered as a Component or passed to props of a
Client Component.
```

根因：`AttackingZonesChart.tsx` 顶部是 `"use client"`（组件内部用
`useState` 做时段切换器），但同一文件里还导出了一个纯函数
`zoneSplitFrom()`（不依赖任何 React hook，纯数据转换）。`MatchShotsSection.tsx`
是服务端组件，直接 `import { zoneSplitFrom } from "./AttackingZonesChart"`
在服务端调用它来组装 props。**`"use client"` 是整个文件级别的边界，不是
逐个 export 判断**——服务端组件从一个 client 文件里 import 任何东西（哪怕
是零依赖的纯函数），Next.js 都会把它替换成一个"客户端引用"对象，服务端
调用这个引用会直接抛出上面这个异常，且这个异常在 `next build` 阶段**不会
出现**（比赛详情页是动态路由 `ƒ /matches/[matchId]`，构建期不会真的渲染
一个具体比赛的服务端组件，只有线上收到真实请求时才会执行到这行调用）——
`npm run build`、`vitest run`、发布脚本的候选进程冒烟全部通过，问题只在
真实用户点开比赛详情页时才暴露。

更深一层：release.sh 的业务冒烟（`verify_next_assets.py` 的 `CORE_PAGES`）
其实包含了一个具体的 `/matches/5104968`，理论上应该能测出页面渲染异常——
但 `zoneSplitOf()` 的调用路径是"先在 `teamStats` 里 `.find()` 到匹配的行，
再调用 `zoneSplitFrom()`"，找不到匹配行时提前 `return null`，根本不会走到
那个会崩的调用。**同一个 bug 在某些比赛上完全触发不了，在另一些比赛上必崩**
——发布流程里固定用一个 match_id 冒烟，测的只是"这一个路径没崩"，不代表
"这一类组件的所有渲染分支都没崩"。

据此固化：

- **判断一个纯函数要不要放进 `"use client"` 文件，问的不是"它本身需不需要
  客户端能力"，而是"有没有服务端组件要 import 它"。** 只要答案是"有"，
  这个函数就必须搬进一个不带 `"use client"` 的独立文件（本次修复：
  `zoneSplitFrom`/`buildAttackingZonesSummary`/`AttackingZoneSplit` 类型
  搬进 `frontend/components/matches/attackingZones.ts`），客户端组件和
  服务端组件都从这个纯文件 import；`"use client"` 文件本身不再重新导出
  这些符号（重新导出同样会把调用方拖进"客户端引用"陷阱，不是安全的
  中转层）。
- **动态路由页面的自动化验证不能只信一个固定 match_id 的冒烟结果。**
  `next build` 对 `ƒ` 标记的动态路由完全不执行服务端组件的真实渲染；
  发布脚本的业务冒烟目前也只跑一个 match_id，无法代表"所有数据形状都能
  正确渲染"。新增比赛详情页组件时，如果逻辑依赖某些数据是否存在（如本例
  "找不到匹配的 team_stats 行就提前返回"），必须显式确认真实生产环境里
  同时存在"命中"和"不命中"这两类比赛各测过一次，不能假设发布流程的固定
  冒烟路径覆盖了所有分支。
- 事故处理时间线（UTC）：2026-08-25 15:33 部署（`current -> 302df736e77d`）
  → 15:36 起线上持续报 `zoneSplitFrom` 异常（此时尚无人工感知）→ 次日站长
  报告"点开比赛详情没有内容" → 生产日志定位根因 → 拆分纯逻辑文件到
  `attackingZones.ts` → 本地 tsc/eslint/vitest/build 全绿 → 重新部署恢复。
  全程未涉及数据损坏，纯前端渲染问题；从部署到真正被发现间隔了约一天，
  说明发布流程的自动化验证没有能力捕获这类"依赖具体数据形状才触发"的
  服务端组件异常。

## 12. Creator Studio

网站和内容制作必须共享一个版本化 `analysis_bundle`：

```text
bundle_version
match
data_cutoff_at
model_version
prediction_public
prediction_member
evidence[]
counter_evidence[]
uncertainty[]
odds_timeline[]
cooccurring_events[]
chart_specs[]
script_sections[]
subtitle_cues[]
source_notes[]
```

同一个 bundle 驱动比赛详情和 Studio。不得在 Studio 再写一套模型解释逻辑。

MVP Studio 提供：

- 选择比赛和数据快照；
- 六段式竖屏场景；
- 可编辑标题、证据、风险与口播稿；
- 导出 1080×1920、1080×1350 PNG；
- 导出纯文本、JSON、SRT；
- Draft → Reviewed → Published 状态；
- 导出中显示数据截止时间和模型版本。

自动配音、自动发布和完整 MP4 合成不属于 MVP。

## 13. Worker 与任务调度

不引入 Celery。使用一个轻量 Worker（`backend/worker/runner.py`）、SQLite
`job_runs` 记录每个任务的全生命周期，配合多个 systemd timers 触发。

任务必须支持：

- 幂等键；
- 依赖检查；
- 超时；
- 有限重试和退避；
- 单任务锁；
- `pending/running/succeeded/failed/skipped`；
- 输入输出计数；
- 错误摘要；
- 从指定步骤重跑。

调度拓扑：没有任何定时器周期性触发"整条任务链"。每个任务只属于一个独立的
systemd 定时器，各自失败域互不影响——一个任务失败或被文件锁挡住，不会级联
跳过与它无关的其它任务；级联跳过（依赖检查）只保留给下面手动全链重跑时使用。
调度共 7 个定时器：4 个单任务定时器直接 `runner --job <name>`；3 个"任务组"
定时器用 `backend/worker/group_runner.py --group <name>` 顺序尝试组内每个
任务，任一任务失败不阻止组内其余任务被尝试，整体退出码只在组内出现
failed/locked 时非零：

- `allwin-odds`（每 5 分钟触发到期判断）→ `nowgoal_snapshot`；
- `allwin-lineup`（每 5 分钟触发到期判断）→ `fotmob_snapshot`；
- `allwin-fixtures`（每 30 分钟）→ `schedule_sync_multi`；
- `allwin-gates`（每 30 分钟）→ `pipeline_gates`（质量门，故意独立于其它任何
  定时器的成败，发现问题只通过告警表达，不通过任务失败表达）；
- `allwin-postmatch`（每 30 分钟，任务组）→ `fotmob_incremental_multi` →
  `core_silver_build` → `reco_auto_settle`；
- `allwin-derive`（每 30 分钟，任务组）→ `odds_silver_build` →
  `analysis_bundle_build`；
- `allwin-maintenance`（每天 04:00 Asia/Shanghai，任务组）→
  `entity_resolution`。

以上 7 个定时器合起来的任务集合必须恰好等于下面手动全链的全部任务，不多不
少、不重复调度（`tests/backend/test_job_order.py` 校验这条不变量）。另有
独立的 `allwin-digest.timer`（每天 23:30 Asia/Shanghai，`--key <北京日期>`
幂等）调度 `daily_digest`，它和 `silver_build`（`core_silver_build` 的兼容
别名）一样，是注册在 Worker 里但不挂在任何链式定时器上的任务。同一先例下
还有 `allwin-physical-stats.timer`（每 30 分钟触发到期判断）调度
`physical_stats_poll`：FotMob 部分球队体能统计（`physical_metrics_distance_
covered` 等，落在 `fact_team_match_stats.extra_json`）是异步计算的，常在
比赛完赛后才出现终值，现有管道只在比赛刚解决时抓一次、之后不再回访；该
任务只处理 League_ID=47（英超），在 kickoff+6h/12h/24h 三个固定检查点回查
`ingest_match()`，主客两队 `physical_metrics_distance_covered` 都
≥50000 米即视为终值并停止，三次检查点后仍未达标则记 `exhausted_at` 并经
`backend.notify` 告警恰好一次（纯判断逻辑在
`backend/ingest/physical_stats_poll.py`，落库与调用在
`backend/cli/poll_physical_stats.py`，状态表 `physical_stats_poll_state`
落 core/allwin.db，理由见迁移 `0013_physical_stats_poll_state.sql` 头注释）。
同一先例下还有 `allwin-standings.timer`（每 30 分钟触发到期判断）调度
`standings_refresh_poll`：联赛积分榜（`fact_league_table`，由
`GET /api/v1/leagues/{id}/standings` 读取）只由
`ingest_league.py::ingest_season_tables()` 写入，而该函数从未被任何 worker
任务调度过——旧的手动 CLI 短路判断（`_season_tables_done()`，多赛季回填场景
下仍然合法保留）只要该 (League_ID, Season) 有过任何一行就永久跳过，导致
赛季初始 skeleton（`played=0`）落库后积分榜再也不会自动刷新，即使联赛已经
踢完多轮（2026-08 英超 2026/2027 真实事故，已手动修复一次，非持久修复）。
该任务只处理 League_ID=47（英超），触发条件是"该联赛当前赛季最近一场完赛
比赛（`status='Finish'`）的 `kickoff_at_utc` + 6 小时"——纯粹的时间到期
判断，不是有限次检查点或有效值重试（这一点与 `physical_stats_poll` 不
同构：`ingest_season_tables()` 是整体 pull-and-replace，请求成功即结构上
有效，没有"有效/无效"数据判断）；到期即调用一次
`ingest_season_tables()`，赛季从"最近一场完赛比赛自身的 `Season` 列"读取
（该列已由迁移 0011 的触发器保证与 (League_ID, Date) 推导一致），不重新
调用 `season_for_match()`/`resolve_current_season()`。纯判断逻辑在
`backend/ingest/standings_refresh_poll.py`（`due_refresh`），落库与调用在
`backend/cli/poll_standings.py`，状态表 `standings_refresh_state`
落 core/allwin.db，理由见迁移 `0014_standings_refresh_state.sql` 头注释。
它同样不挂在 7 个既有定时器上、不进 `DEFAULT_CHAIN`，自己独立一个 timer。

`--chain`（`DEFAULT_CHAIN`）现在只是人工全量重跑/故障排查用的手动逃生舱，
生产没有任何定时器再周期性调用它；裸 `--chain` 仍按顺序执行、某步
failed/locked 后续步骤记 skipped（依赖检查）、`--from <step>` 支持从中间
步骤重跑：

```text
schedule_sync_multi
→ fotmob_incremental_multi
→ nowgoal_snapshot
→ fotmob_snapshot
→ entity_resolution
→ core_silver_build
→ odds_silver_build
→ analysis_bundle_build
→ reco_auto_settle
→ pipeline_gates
```

`pipeline_gates` 必须排在最后：它用告警表达发现的问题，不通过任务失败表达，
没有下游需要被级联跳过。

具体任务可以在实现中合并，但以下能力不得只是 `optional` 模块探测占位：

- NowGoal 快照；
- FotMob 阵容和伤停快照；
- 实体解析；
- 赔率/事件变化点与时间共现构建；
- analysis bundle 构建。

外部凭证缺失时任务可以诚实记录 `skipped` 或 `failed` 及原因，但对应代码路径、离线 fixture 测试和 Worker 注册必须真实存在。默认任务因“候选模块不存在”跳过时，不得声称核心链路已经完成。

采集器失败不能拖垮 API。页面显示最后成功更新时间和 stale 状态，不展示伪造的新鲜数据。

## 14. 部署、安全与备份

### 14.1 Production

- Nginx、Next.js、FastAPI 和 Worker 使用 systemd。
- Next.js/FastAPI 只监听 localhost。
- Cloudflare TLS 使用 Full (strict)。
- Origin 只开放必要端口；SSH 使用密钥并限制来源。
- `.env`、数据库、模型二进制、日志和备份不得提交 Git。
- Production 不允许默认密码、Mock Auth、测试会员参数或调试后门。
- 禁止 `?simulate_membership=paid`、`?token=<jwt>`、`?dev=1` 等生产绕过入口。

### 14.2 发布

使用 release 目录和 `current` 软链接：

```text
/opt/allwin/releases/<git-sha>
/opt/allwin/current
/opt/allwin/shared/data
/opt/allwin/shared/logs
```

发布顺序：备份 → 构建 → migration → 启动候选服务 → healthcheck → 切换 → 冒烟测试。失败必须可回滚到上一 release，禁止直接覆盖线上目录。

发布脚本的业务冒烟不能只检查进程存活和首页 HTTP 200。至少必须验证：

- `/api/v1/products` 返回符合契约的 JSON；
- `/api/v1/matches` 返回符合契约的 JSON；
- 首页或核心公开页能够通过生产地址读取 API 数据；
- 浏览器构建产物没有把生产用户指向 `127.0.0.1`；
- 认证关闭时公开站点能够启动；
- 冒烟失败时在切换后自动回滚。

构建所需环境变量必须在 `npm run build` 前加载。

### 14.3 备份

- 每日对三个 SQLite 数据库做一致性在线备份并上传 S3 versioned bucket。
- migration 前额外备份。
- 预测 manifest 与普通数据库备份分开保存。
- 每月执行恢复演练并记录真实结果。
- 磁盘 70% 告警、85% 严重告警。

## 15. 测试与验收

每次重要改动至少验证：

### 后端

- migration 可在临时副本执行且可重复运行；
- SQLite `integrity_check`；
- pytest；
- 权限矩阵测试（普通比赛内容对匿名与登录一致；每日精选按场授权 401/403/200）；
- 普通比赛内容 DTO 不含 requires_login/tier/free_outcome 等已废止的裁剪字段；
- 内容编辑必须经 `edit_snapshot` 留痕（`prediction_snapshot_edits` 正确写入，`edit_count`/`last_edited_at` 更新）；
- webhook 签名/时间戳/nonce 防重放、设备扫码一次性消费、会话撤销与 CSRF 测试；
- 数据源不可用时的降级测试。

### 前端

- ESLint、TypeScript、Vitest；
- `npm run build`；
- Playwright 覆盖：匿名浏览完整比赛内容、微信 Mock 登录、每日精选按场授权与撤销、
  Admin 拒绝、Studio 导出。
- 新增或修改图表：至少一条渲染冒烟测试（真实调用 `buildOption` + ECharts
  headless 渲染，异常必须真实抛出，见 §11.3）；标记/背景配色变化附一条
  合成对比度断言，不能只测"没抛异常"就当作验证完成——渲染出来但肉眼不可见
  的 bug，纯逻辑测试和渲染冒烟测试都抓不到，只有对比度断言能抓到。

### 核心链路验收

- 使用临时数据库和固定 fixture 跑通 xref → Bronze → Silver → Gold → API → analysis bundle；
- fixture 必须包含至少两次赔率快照和两次阵容/伤停快照，证明变化点及时间共现实际产生；
- 同一链路重跑必须幂等；
- 真实外部访问与离线 fixture 验证必须分别汇报。

### 登录与导出验收

- Playwright 必须覆盖完整扫码登录（浏览器创建 → webhook 签名批准 → 轮询领取），不得只覆盖 claim 单端点；
- Studio PNG 验收必须检查下载文件 PNG signature 和实际像素尺寸，后台创建导出记录不能替代图片生成测试。

### 部署

- `/healthz`：进程存活；
- `/readyz`：数据库可读、migration 版本一致；
- Nginx 路由冒烟；
- Cloudflare 缓存检查：公开 HIT、私有 BYPASS；
- 备份文件能在临时目录恢复并通过 integrity check。

## 16. 文档真源

- `CLAUDE.md`：长期架构与纪律。
- `DESIGN.md`：视觉系统。
- `docs/current-state.md`：可更新的真实当前状态。
- `docs/data-plan.md`：数据层单一真源——联赛 × 数据层覆盖、验证状态、依赖排序计划。
- `docs/architecture.md`：模块、数据库和运行拓扑。
- `docs/auth-wechat.md`：公众号后台配置、带参二维码 + webhook 扫码登录和账号恢复。
- `docs/data-sources.md`：来源、验证状态、时间粒度、降级方式。
- `docs/deployment-aws-cloudflare.md`：东京 AWS、Nginx、systemd、Cloudflare、备份和回滚。
- `docs/audits/`：逐模块独立复核报告，`docs/current-state.md` 的证据层。
- `README.md`：安装、开发、测试和常用命令。

动态进度、服务器 IP、真实域名、用户数量、数据行数和临时排期不得写入本文件。数据层覆盖与排期只写 `docs/data-plan.md`。

## 17. 绝对禁止

- 破坏或覆盖用户未提交改动。
- 将真实 `.env`、AppSecret、JWT、Cookie、私钥或数据库输出到对话、日志或 Git。
- 未备份直接修改现有生产数据库。
- 把 OpenID、昵称或头像当作会员权限。
- 把 paid 字段下发后用 CSS 遮挡。
- 让公开缓存响应因 Cookie 或用户身份混用。
- 在 API 请求内训练模型或执行长跑批。
- 把 DEMO 战绩表现成真实结果。
- 网络验证失败时编造成功结论。
- 为满足测试而删除测试、降低断言或跳过 build。
- 未经授权执行 Git commit、push、部署或修改 Cloudflare/AWS 线上状态。

## 18. 完成定义

任务只有在以下条件同时满足时才算完成：

1. 实现与本文件的架构边界一致；
2. 原有功能和用户改动未被破坏；
3. migration、测试、构建和必要冒烟实际运行；
4. 权限、缓存和认证安全测试通过；
5. 文档描述与真实代码一致；
6. 所有无法验证的外部能力明确标记 `UNVERIFIED`；
7. 最终汇报列出真实命令、退出码、失败项和未完成项，以及仍需用户提供的外部凭证。
8. 默认 Worker 中的核心采集、实体解析、赔率 Silver/Gold 和 analysis bundle 任务不是模块缺失占位；
9. 生产浏览器 API 地址、认证启用和关闭两种模式、业务 API 冒烟已经通过自动化验证；
10. “代码实现”“离线 fixture 验证”“真实外部服务验证”分别汇报，任一核心能力仅为 `UNVERIFIED` 时不得笼统声称生产验证完成。
