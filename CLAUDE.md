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

用户可见文案必须使用“同期事件”“同时段检测到”“时间共现”等表述。不得使用“因为盘口变化”“导致盘口变化”“必胜”“稳赚”“红单”“连红”等无法证实或收益承诺式表述。

本项目不实现线上代购、远程出票、投注金额或仓位建议、博彩网站导流、平台审核规避，也不把模型输出包装成确定性结果。

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

### 6.3 赔率快照

- 赛前 72 小时起每 15 分钟轮询；赛前 2 小时起每 5 分钟轮询。
- 只有 canonical payload hash 相对上一条发生变化才落库。
- 明确区分 `pre_match` 与 `in_play`。
- `FINAL` 是开球前最后一个 `pre_match` 有效快照，明确排除滚球。
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

### 7.3 微信登录

MVP 的首选认证是已认证服务号网页授权：

1. 微信内移动端点击登录后，使用 `snsapi_base` 获取最小身份信息。
2. OAuth `state` 使用一次性随机值，服务端哈希存储、短期有效、使用后销毁。
3. 回调地址使用固定 allowlist；`next` 只能是本站相对路径，禁止开放重定向。
4. AppSecret 只存在 FastAPI 环境变量和服务端请求中。
5. 获取或创建内部 User，再创建网站自己的会话。

电脑端使用一次性 Device Login：

1. 浏览器创建 `device_login_request`，获得二维码 URL 和只留在浏览器的 secret。
2. 二维码只包含公开 request id，不包含浏览器 secret。
3. 手机微信扫码后完成同一个公众号 OAuth，并批准该 request。
4. 电脑轮询时必须同时提交浏览器 secret；成功后原子消费 request 并设置会话 Cookie。
5. request 短期有效、只能消费一次、状态持久化到 SQLite，不能存在进程内存字典。

不得沿用旧项目的以下模式：普通 `random` 四位验证码、只存在内存的登录状态、把 JWT 放进查询参数、把 API token 暴露到客户端 session、用 `users.openid='USER_xxx'` 伪造其他身份。

如果 `WECHAT_AUTH_ENABLED=1`，production 启动时缺少 AppID、AppSecret、回调配置必须拒绝启动。Development 可以使用显式 Mock Provider，但 Mock 在 production 必须 fail-fast。

### 7.4 网站会话

MVP 使用数据库持久化的 opaque session，不使用浏览器可见长效 JWT：

- 登录生成至少 256 bit 随机 token；数据库只保存 SHA-256 hash。
- Cookie：`Secure`、`HttpOnly`、`SameSite=Lax`、host-only、`Path=/api/v1`。
- 会话可撤销、可过期、登录后轮换；原始 token 不记录日志。
- 写请求校验 Origin/Referer allowlist，并使用 CSRF token。
- 登录、回调、会话、账户接口全部 `Cache-Control: private, no-store`。
- 付费用户后续应能绑定恢复身份；MVP 未接真实短信/邮件时必须在界面如实说明。

## 8. Role、套餐与权限

### 8.1 维度

- 认证状态：anonymous / authenticated。
- Role：user / analyst / admin。
- Plan：free / pro / premium。
- Entitlement：具体能力标识。

推荐 entitlement：

```text
free:
  league:epl
  prediction:top_probability
  odds:summary_delayed

pro:
  league:top5
  prediction:full_wdl
  prediction:score_matrix
  report:deep
  export:basic

premium:
  继承 pro
  odds:history_full
  odds:raw
  export:full
  alert:odds
```

### 8.2 免费概率边界

匿名和 Free 用户只能获得：

```json
{
  "top_outcome": "home",
  "top_probability": 0.48
}
```

另外两项概率不得返回 null 占位，更不得下发真值后用 CSS 遮挡。Pro/Premium 响应才包含完整 `home/draw/away`。服务端必须使用不同 DTO 或显式字段投影，测试响应体不存在受限字段。

### 8.3 权限校验

- 前端只负责体验，后端是权限真源。
- API service 和 query 层均需 entitlement 校验。
- Admin 不能只靠隐藏路由或秘密 URL。
- 套餐价格与权益从数据库/API 读取，不在组件写死。
- MVP 未接支付时，使用管理员发放、撤销、延期和兑换码；所有操作写 AuditLog。

## 9. 预测完整性与模型评估

### 9.1 不可覆盖的预测登记簿

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
```

规则：

- 三项概率在容差内等于 1。
- 开球后生成的预测不得进入正式赛前统计。
- 锁定后禁止 UPDATE 概率；修正必须追加新版本并保留旧版本。
- 删除只允许撤回状态，不允许物理删除正式预测。
- 公开 track record 默认展示全部正式样本。
- 每日正式预测 manifest 生成稳定 hash，可上传 S3 版本桶；启用 Object Lock 时优先 governance 模式。

历史 `gold_wdl_predictions` 如果无法证明生成时间早于开球，只能导入为 `legacy_unverified` 或 draft，不能冒充正式历史战绩。

### 9.2 评估口径

实现 Accuracy、Brier、Multiclass Log Loss、RPS、Calibration buckets 和样本量。评估命令离线运行，不进入在线 FastAPI 请求。

现有 RPS 0.2143 的已知对照是简单历史主/平/客频率基线 0.2281，不是博彩公司收盘共识。除非完成可复现的收盘赔率评估，不得写“模型优于/劣于收盘盘口”。市场基线需要固定并记录：公司集合、去水公式、缺失规则、聚合方式、配对样本和 RPS 公式。

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

GET  /api/v1/auth/wechat/oa/start
GET  /api/v1/auth/wechat/oa/callback
POST /api/v1/auth/wechat/device
POST /api/v1/auth/wechat/device/{id}/claim
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
- 中文默认，时间按用户时区显示，数据库统一 UTC。
- 响应式以手机为第一优先；窄屏主动减少列，不缩小到不可读。
- 图表必须有文字摘要、单位、时间范围、空状态和数据更新时间。
- 不用假倒计时、虚构在线人数、诱导弹窗或赌场式视觉语言。
- 深浅模式使用同一份 JSX 和 CSS 变量；禁止复制两套组件树。
- 字体和图片自托管，不依赖中国大陆不可稳定访问的外链资源。

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

推荐任务链：

```text
schedule_sync
→ fotmob_incremental
→ nowgoal_snapshot
→ entity_resolution
→ silver_build
→ model_predict
→ prediction_register
→ analysis_bundle_build
→ postmatch_settle
→ metrics_rebuild
```

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
- 锁定预测不可修改；
- OAuth state、设备扫码一次性消费、会话撤销与 CSRF 测试；
- 数据源不可用时的降级测试。

### 前端

- ESLint、TypeScript、Vitest；
- `npm run build`；
- Playwright 覆盖：匿名浏览、免费概率、微信 Mock 登录、会员解锁、Admin 拒绝、Studio 导出。

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
- `docs/architecture.md`：模块、数据库和运行拓扑。
- `docs/auth-wechat.md`：公众号后台配置、OAuth、扫码登录和账号恢复。
- `docs/data-sources.md`：来源、验证状态、时间粒度、降级方式。
- `docs/model-api-contract.md`：模型输入输出和评估口径。
- `docs/prediction-integrity.md`：锁定、哈希、撤回和公开战绩规则。
- `docs/deployment-aws-cloudflare.md`：东京 AWS、Nginx、systemd、Cloudflare、备份和回滚。
- `README.md`：安装、开发、测试和常用命令。

动态进度、服务器 IP、真实域名、用户数量、数据行数和临时排期不得写入本文件。

## 17. 绝对禁止

- 破坏或覆盖用户未提交改动。
- 将真实 `.env`、AppSecret、JWT、Cookie、私钥或数据库输出到对话、日志或 Git。
- 未备份直接修改现有生产数据库。
- 把 OpenID、昵称或头像当作会员权限。
- 把 paid 字段下发后用 CSS 遮挡。
- 让公开缓存响应因 Cookie 或用户身份混用。
- 在 API 请求内训练模型或执行长跑批。
- 事后覆盖、删除或挑选正式预测。
- 把 DEMO 战绩表现成真实结果。
- 网络验证失败时编造成功结论。
- 为满足测试而删除测试、降低断言或跳过 build。
- 未经授权执行 Git commit、push、部署或修改 Cloudflare/AWS 线上状态。

## 18. 完成定义

任务只有在以下条件同时满足时才算完成：

1. 实现与本文件的架构边界一致；
2. 原有功能和用户改动未被破坏；
3. migration、测试、构建和必要冒烟实际运行；
4. 权限、缓存、预测锁定和认证安全测试通过；
5. 文档描述与真实代码一致；
6. 所有无法验证的外部能力明确标记 `UNVERIFIED`；
7. 最终汇报列出真实命令、退出码、失败项和未完成项，以及仍需用户提供的外部凭证。
