# CLAUDE.md — all-win / 欧赢工程宪法

> 本文件只保存长期有效的产品边界、架构决策和工程纪律，不记录完成进度、临时任务或某次会话结论。
> 当前状态、数据量和未完成事项必须通过代码、数据库与 `docs/current-state.md` 重新审计，不得从旧文档猜测。
> 除非用户明确批准，任何实现不得偏离标记为“锁定”的条款。

## 1. 产品使命

all-win 是面向中文足球用户的专业数据分析订阅平台，同时也是站长每日制作视频、长图和口播稿的内容工作台。

核心体验是：

1. 用中文把一场比赛的数据、模型概率和不确定性讲清楚。
2. 将 FotMob 比赛事件与 NowGoal 赔率快照放在同一时间轴上，展示“同一时段观察到了什么”，不声称因果。
3. 公开、连续、不可选择性删除地记录正式预测和赛后评估。
4. 同一份分析数据同时驱动网站页面、竖屏图卡、视频文案与字幕。

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

Python Worker / systemd timer
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

- Worker 每 5 分钟触发一次到期判断，不代表所有比赛每 5 分钟都请求数据源。
- 只有具有精确 `kickoff_at_utc` 的比赛才允许轮询，采集节奏分三档：
  - **首次发现即采**：该 (来源, 比赛) 尚无任何采集记录时，不论距开球多远都立即采集一次；
    小联赛可能要到赛前 4–5 天才真的有盘口，这一枪拿不到数据属正常，不是失败告警；
  - 距开球 2–72 小时：同一来源和比赛至少间隔 15 分钟；
  - 距开球 0–2 小时：同一来源和比赛至少间隔 5 分钟；
  - 已开球比赛是否继续采集由明确的 in-play 任务决定，不能继续伪装为赛前采集。
- 轮询到期状态必须可持久化或由最后一次 poll run 可靠推导，进程重启不能造成无界重复采集。
- 只有 canonical payload hash 相对上一条发生变化才落库。
- `market_phase` 必须由来源状态、精确 kickoff 和观察时间共同判定；信息不足时使用 `unknown`，不得仅按自然日判断。
- `FINAL` 是开球前最后一个 `market_phase='pre_match'` 的有效快照，明确排除 `unknown` 和 `in_play`。
- 同一轮 FotMob 比赛请求应复用原始 payload，阵容与两队伤停使用相同 `poll_run_id` 和 `observed_at`。
- 首先支持真实验证过的公司和市场；不得把“抓到一家公司的初盘/最新”描述为完整多公司时间序列。
- 历史回填能力、时间粒度和公司覆盖写入 `docs/data-sources.md`；不可验证则标 `UNVERIFIED`。

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

## 8. Role、套餐与权限（2026-08 三段可见性修订，经用户批准）

商业定位：站点是短视频/自媒体的流量载体与用户沉淀池。足球数据本身不收费；
收费只针对每日人工独家推荐板块。登录的产品意义是沉淀用户与公众号涨粉，
不是解锁足球数据付费墙。

### 8.1 维度

- 认证状态：anonymous / authenticated。
- Role：user / analyst / admin。
- Plan：free（匿名基线）/ member（登录基线，不可购买）/ 付费板块 plan（独家推荐）。
  旧 pro/premium 已下架（is_active=0，行保留供历史订阅外键引用）。
- Entitlement：具体能力标识。

三段可见性：

```text
匿名（free）:
  league:epl
  league:lottery
  prediction:top_probability
  odds:summary_delayed

已登录（member 基线，登录即得，无需订阅）:
  全部足球数据 entitlement（league:top5、prediction:full_wdl、
  prediction:score_matrix、report:deep、odds:history_full、odds:raw、
  export:basic、export:full、alert:odds，物化并集含 free 全部行）

付费（daily_picks「每日精选」，admin grant / 兑换码发放，定价不展示）:
  member 基线 ∪ reco:daily（近 30 天赛前推荐内容）
```

reco:track_record（推荐战绩归档）属 member 基线：战绩对全部登录用户公开，
命中/未中/走水与作废全展示（不挑选、不隐藏）；付费墙只在赛前内容。

解析规则（`backend/auth/entitlements.py`）：匿名 = free 行；任何已登录用户恒并入
member 基线，有效订阅只做追加——订阅用户的权益绝不能反而少于普通登录用户。
plan_entitlements 仍是物化并集、无跨行继承。

### 8.2 匿名概率边界

匿名用户只能获得：

```json
{
  "top_outcome": "home",
  "top_probability": 0.48
}
```

另外两项概率不得返回 null 占位，更不得下发真值后用 CSS 遮挡。已登录响应才包含完整
`home/draw/away`。服务端必须使用不同 DTO 或显式字段投影，测试响应体不存在受限字段。
边界从"付费/免费"移到"登录/匿名"，但纪律本身不变：后端是权限真源，
受限字段物理不下发。

每日精选的**存在性状态**（某场比赛是否有已发布的赛前推荐单，布尔）是公开运营
信息，可向匿名展示（2026-08-11 站长授权）；推荐的方向、赔率、标题、备注等
**内容**仍属付费面，匿名与免费响应中物理不下发。draft 单的存在性也不得外泄。

### 8.3 权限校验

- 前端只负责体验，后端是权限真源。
- API service 和 query 层均需 entitlement 校验。
- Admin 不能只靠隐藏路由或秘密 URL。
- 套餐价格与权益从数据库/API 读取，不在组件写死。
- 未接真实支付；付费板块权限一律走管理员发放、撤销、延期和兑换码；所有操作写 AuditLog。
- SEO/GEO 面（sitemap、robots、llms.txt）只列匿名可完整浏览的页面，
  与 `frontend/lib/site.ts` 单一真源对齐；需登录页面不进 sitemap，
  防止爬虫抓到付费墙壳页。

## 9. 预测完整性与模型评估

### 9.1 可编辑但强制留痕的预测登记簿

> 适用范围（2026-08 修订，经用户批准）：本节的锁定、哈希、永久公开资格等不变量
> 只约束**模型预测**。付费独家推荐板块是人工内容，独立建表、不纳入本登记簿，
> 其内容与战绩允许管理员修改；作为交换，推荐板块的战绩页必须与模型公开战绩
> 在产品与代码上明确区分，不得混用 track_record 查询、评估口径或"锁定不可改"
> 的可信度表述。**不得为容纳人工推荐而放松 `prediction_snapshots` 的任何既有
> 保护**（三概率和为 1 的 CHECK、`model_version_id NOT NULL`、
> `trg_pred_snap_locked_immutable` 触发器均为全表级，动之即削弱全部模型样本）。

现有 `gold_wdl_predictions` 是模型当前产物，不是公开历史账本。正式记录使用 `platform.db` 中的：

```text
model_versions
prediction_runs
prediction_snapshots
prediction_outcomes
prediction_evaluations
prediction_manifests
```

正式快照必须包含：

```text
match_id / model_version_id / generated_at / published_at / locked_at
input_cutoff_at / input_snapshot_hash / prediction_hash
home_win / draw / away_win / expected_home_goals / expected_away_goals
confidence / visibility / status / is_official
last_edited_at / edit_count
```

规则：

- 三项概率在容差内等于 1。
- 开球后生成的预测不得进入正式赛前统计。
- 锁定后的概率等预测内容允许直接编辑，但必须经统一的 `edit_snapshot` 写入
  `prediction_snapshot_edits`（修正前后值、操作者、原因、时间），不得绕过留痕
  直接 UPDATE；`prediction_snapshot_edits` 本身 append-only，不可再改或删。
- 公开 track record 默认展示全部正式样本。
- 每日正式预测 manifest 生成稳定 hash，可上传 S3 版本桶；启用 Object Lock 时优先 governance 模式。

正式样本集合具有公开样本口径不变量：

- 一条快照一旦满足 `is_official=1`、`locked_at IS NOT NULL` 且 `published_at < kickoff_at_utc`，就永久属于公开正式样本集合，这条资格判据只看是否曾经合格地锁定过，不因内容后续被编辑而改变；
- `status='retracted'`、`superseded_by`、管理员撤回或后续修正版不得使旧样本从公开列表、manifest 或评估分母中消失；
- 修正版可以追加为新快照（`supersede`，旧版和新版均可查询并显示修正链），也可以直接编辑已有快照（`edit_snapshot`，留痕但不产生新记录）；两种机制并存，互不替代；
- 开球后禁止创建 supersede 版本；直接编辑不受开球/结算前后限制；
- 赛后撤回只能作为公开说明和审计状态，不能改变评估资格；
- track record 和 evaluation 查询不得使用 `status` 或 `superseded_by` 选择性排除已经成为正式样本的记录；
- 内容修正必须通过 `edit_snapshot` 留痕，修正历史（次数、最近时间）在公开战绩页可查，不得静默覆盖。

任何能让已经公开的失败预测从公开列表或指标分母中消失的实现都属于 P0 数据完整性缺陷；未经留痕直接覆盖正式预测内容同样属于 P0 数据完整性缺陷。

历史 `gold_wdl_predictions` 如果无法证明生成时间早于开球，只能导入为 `legacy_unverified` 或 draft，不能冒充正式历史战绩。

### 9.2 评估口径

实现 Accuracy、Brier、Multiclass Log Loss、RPS、Calibration buckets 和样本量。评估命令离线运行，不进入在线 FastAPI 请求。

市场基线需要固定并记录：公司集合、去水公式、缺失规则、聚合方式、配对样本和 RPS 公式。

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
GET  /api/v1/matches/{id}/prediction
GET  /api/v1/matches/{id}/odds
GET  /api/v1/matches/{id}/cooccurrence
GET  /api/v1/track-record
GET  /api/v1/model/metrics
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
2. 免费最高一项概率 / 会员完整概率；
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
- 状态标签不堆胶囊墙：每张比赛卡最多"联赛·轮次 + 比赛状态 + 可行动提示"
  三层；赛季等第二层信息进正文行。内部枚举值（MARKET_BASELINE 等）
  不得直接出现在用户界面。

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

不引入 Celery。使用一个轻量 Worker、SQLite `job_runs` 和 systemd timers。

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

核心任务链至少包含：

```text
schedule_sync
→ fotmob_incremental
→ nowgoal_snapshot
→ fotmob_snapshot
→ entity_resolution
→ core_silver_build
→ odds_silver_build
→ model_predict
→ prediction_register
→ analysis_bundle_build
→ postmatch_settle
→ metrics_rebuild
```

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
- 权限矩阵测试；
- 免费 DTO 不含受限字段；
- 内容编辑必须经 `edit_snapshot` 留痕（`prediction_snapshot_edits` 正确写入，`edit_count`/`last_edited_at` 更新）；
- webhook 签名/时间戳/nonce 防重放、设备扫码一次性消费、会话撤销与 CSRF 测试；
- 数据源不可用时的降级测试。

### 前端

- ESLint、TypeScript、Vitest；
- `npm run build`；
- Playwright 覆盖：匿名浏览、免费概率、微信 Mock 登录、会员解锁、Admin 拒绝、Studio 导出。

### 核心链路验收

- 使用临时数据库和固定 fixture 跑通 xref → Bronze → Silver → Gold → API → analysis bundle；
- fixture 必须包含至少两次赔率快照和两次阵容/伤停快照，证明变化点及时间共现实际产生；
- 同一链路重跑必须幂等；
- 真实外部访问与离线 fixture 验证必须分别汇报。

### 预测账本验收

- retract 后公开样本数和评估分母不变；
- supersede 后旧样本仍公开并进入评估；
- 开球后 supersede 被拒绝；
- 旧版、新版、撤回版均能通过 track record 查询；
- manifest 不得只包含修正链最新版。

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
- `docs/model-api-contract.md`：模型输入输出和评估口径。
- `docs/prediction-integrity.md`：锁定、哈希、撤回和公开战绩规则。
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
4. 权限、缓存、预测锁定/编辑留痕和认证安全测试通过；
5. 文档描述与真实代码一致；
6. 所有无法验证的外部能力明确标记 `UNVERIFIED`；
7. 最终汇报列出真实命令、退出码、失败项和未完成项，以及仍需用户提供的外部凭证。
8. 默认 Worker 中的核心采集、实体解析、赔率 Silver/Gold 和 analysis bundle 任务不是模块缺失占位；
9. 生产浏览器 API 地址、认证启用和关闭两种模式、业务 API 冒烟已经通过自动化验证；
10. “代码实现”“离线 fixture 验证”“真实外部服务验证”分别汇报，任一核心能力仅为 `UNVERIFIED` 时不得笼统声称生产验证完成。
