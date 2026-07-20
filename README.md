# all-win(欧赢)

面向中文足球用户的专业数据分析订阅平台,同时是站长制作视频/长图/口播稿的内容工作台。
核心:用中文讲清一场比赛的数据、模型概率与不确定性;把 FotMob 事件与 NowGoal 赔率
快照放在同一时间轴上展示"同期事件"(不声称因果);公开、连续、不可选择性删除的
预测登记与赛后评估。

- 长期架构与工程纪律:[CLAUDE.md](CLAUDE.md)(锁定条款,偏离需用户批准)
- 视觉系统:[DESIGN.md](DESIGN.md)
- 真实当前状态(审计结论):[docs/current-state.md](docs/current-state.md)
- 模块与数据流:[docs/architecture.md](docs/architecture.md)

技术栈(锁定):Next.js 16 App Router + React 19 + TypeScript / FastAPI + Python /
SQLite(WAL,三库)/ ECharts / CSS Modules / pytest + Vitest + Playwright /
Cloudflare → Nginx → systemd 单机部署。

## 目录结构

```text
all-win/
├── backend/
│   ├── api/            # FastAPI 薄路由(/api/v1;app.py 为生产入口)
│   ├── auth/           # 微信 OAuth / Device Login / opaque session / entitlement
│   ├── commands/       # 可写业务命令(预测登记簿、订阅、兑换码、审计)
│   ├── queries/        # 只读查询(mode=ro)
│   ├── db/             # 连接工厂 + migration runner + 路径
│   ├── migrations/     # core / platform / odds 三库 SQL 迁移
│   ├── ingest/         # FotMob 采集脚本、实体解析、odds 快照落库
│   ├── providers/      # NowGoal / FotMob 快照 Provider Adapter
│   ├── silver/         # Silver 聚合与 odds 变化点/时间共现
│   ├── models/         # 特征构建 + DC baseline + 未来赛程预测
│   ├── eval/           # 评估指标纯函数
│   ├── studio/         # analysis_bundle 与内容导出
│   ├── worker/         # 轻量任务链(job_runs + 文件锁 + 重试)
│   ├── cli/            # 命令行工具(见下)
│   └── api_server.py   # 旧 /api/league/* 兼容层(deprecated)
├── frontend/           # Next.js(app/ components/ lib/ tests/)
├── data/               # SQLite 三库与运行数据(不进 git)
├── deploy/             # nginx / systemd / 发布与备份脚本
├── docs/               # 文档真源(见 CLAUDE.md §16)
└── tests/              # 后端 pytest
```

## 安装

后端(Python 3.13):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 按下表手填;真实凭证绝不进 git
python -m backend.db.migrate --all   # 建/升级 platform.db 与 odds.db(core 不破坏性重建)
```

前端(Node,frontend/):

```bash
cd frontend
npm install
```

## 环境变量(.env)

| 变量 | 含义 |
|---|---|
| `APP_ENV` | `development` / `production`。production 启用 fail-fast 配置校验与 Secure cookie |
| `ALLWIN_DATA_DIR` | (可选)重定向数据目录,默认 `<repo>/data`;测试与部署共享目录用 |
| `THORDATA_PROXY` | ThorData 住宅代理(`http://user:pass@host:port`),FotMob 抓取必需 |
| `DASHSCOPE_API_KEY` | 阿里云百炼 key,Qwen-MT 翻译球员/球队中文名(官方 SDK 约定名,勿改) |
| `WECHAT_AUTH_ENABLED` | `1` 启用微信登录;production 下启用则缺 AppID/Secret 拒绝启动 |
| `WECHAT_AUTH_PROVIDER` | `real` / `mock`。mock 仅 development;production 检测到 mock 拒绝启动 |
| `WECHAT_OA_APP_ID` / `WECHAT_OA_APP_SECRET` | 服务号凭证(只存在服务端) |
| `PUBLIC_BASE_URL` | 对外基础地址(OAuth 回调、扫码 URL);production 必须 https |
| `FRONTEND_BASE_URL` | 登录后跳转前缀;生产同域留空,本地指向 Next dev server |
| `NEXT_PUBLIC_API_BASE` | 浏览器端 API 基址,**next build 构建期内联**;生产留空走同源 `/api/v1`,开发/E2E 显式指定 |
| `INTERNAL_API_BASE` | Next.js 服务端(RSC)请求 FastAPI 的内网基址(默认 `http://127.0.0.1:8000`),运行期读取 |
| `ALLOWED_ORIGINS` | 写请求 Origin/Referer 白名单(逗号分隔) |
| `SESSION_TTL_DAYS` | 会话有效期(默认 30) |
| `OAUTH_STATE_TTL_SECONDS` | OAuth state 有效期(默认 600) |
| `DEVICE_REQUEST_TTL_SECONDS` | 扫码登录请求有效期(默认 300) |
| `COOKIE_SECURE` | development 下置 `1` 强制 Secure cookie(production 恒开) |
| `ALLWIN_ADMIN_PASSWORD` | (可选)create_admin 非交互模式的密码来源;交互模式走 getpass |
| `S3_BACKUP_BUCKET` | (可选)备份 S3 桶;未配置时备份脚本只做本地备份 |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | (可选)不用 EC2 instance role 时的 AWS 凭证 |
| `BACKUP_KEEP` | 本地保留备份份数(默认 14,必须为正整数) |
| `KEEP_RELEASES` | release.sh 保留最近几个 release 目录(默认 5;current/previous 永不清理) |
| `MIN_FREE_DISK_MB` | release.sh preflight 的磁盘可用空间下限(默认 2048) |
| `OPS_DISK_WARN_PCT` / `OPS_DISK_CRITICAL_PCT` | `ops_check` 磁盘告警/严重阈值(默认 70/85) |
| `OPS_BACKUP_STALE_HOURS` / `OPS_JOB_STUCK_MINUTES` / `OPS_JOB_STALE_HOURS` / `OPS_SOURCE_STALE_HOURS` | `ops_check` 备份新鲜度/任务卡住/任务过期/数据源过期阈值 |

见 [docs/deployment-aws-cloudflare.md](docs/deployment-aws-cloudflare.md) §5/§6/§11。

## 常用命令

以下均在仓库根目录、`.venv` 激活状态下执行。

### 数据库

```bash
python -m backend.db.migrate --all              # 三库全部迁移(幂等)
python -m backend.db.migrate --status           # 查看各库版本与 pending
python -m backend.db.migrate --db platform      # 单库
python -m backend.db.migrate --all --data-dir /tmp/x   # 重定向数据目录(测试)
```

### 后端服务

```bash
uvicorn backend.api.app:app --host 127.0.0.1 --port 8000            # 生产入口
uvicorn backend.api.app:app --host 127.0.0.1 --port 8000 --reload   # 本地开发
# development 下 API 文档:http://127.0.0.1:8000/api/v1/docs
# 探针:GET /healthz(进程存活)、GET /readyz(三库可读 + migration 无 pending)
```

### CLI 工具(backend/cli/)

```bash
python -m backend.cli.create_admin --username admin            # 创建管理员(getpass 交互)
python -m backend.cli.create_admin --username admin --reset-password
python -m backend.cli.import_gold_predictions --dry-run        # gold → 预测登记簿(先看口径)
python -m backend.cli.import_gold_predictions                  # 正式导入(幂等)
python -m backend.cli.evaluate_predictions                     # 结算 + 正式口径评估
python -m backend.cli.build_manifest [--date YYYY-MM-DD]       # 每日正式预测 manifest
python -m backend.cli.poll_nowgoal --due                       # NowGoal 窗口到期采集(worker 用)
python -m backend.cli.poll_nowgoal --date 2026-07-19           # NowGoal 指定单日采集
python -m backend.cli.poll_nowgoal --due --offline-fixture f.json --now 2026-08-21T10:00:00Z  # 离线验证
python -m backend.cli.poll_fotmob_snapshots --due              # FotMob 阵容/伤停快照(需 THORDATA_PROXY)
python -m backend.cli.poll_fotmob_snapshots --match-id 5795363 # 指定单场
python -m backend.cli.resolve_entities                         # 实体解析:别名种子 + xref 状态
python -m backend.cli.build_odds_silver                        # odds 变化点 + 时间共现(幂等)
python -m backend.cli.export_openapi                           # 导出 OpenAPI → frontend/lib/openapi.json
```

### Worker(任务链)

```bash
python -m backend.worker.runner --list                     # 列出注册任务
python -m backend.worker.runner --job core_silver_build    # 单任务(silver_build 为兼容别名)
python -m backend.worker.runner --job schedule_sync --key 2026-07-19    # 幂等键
python -m backend.worker.runner --chain                    # 全链(生产 allwin-worker.timer 每 15 分钟)
python -m backend.worker.runner --chain --from core_silver_build        # 从中间步骤重跑
# 赛前采集另由 allwin-poll.timer 每 5 分钟触发 nowgoal_snapshot + fotmob_snapshot
# (真实采集频率由 odds.db poll_state 节流:2–72h 每 15 分钟,0–2h 每 5 分钟)
```

### 数据脚本(legacy,直接以脚本运行)

```bash
python backend/ingest/ingest_league.py --league-id 47 --season 2025/2026   # FotMob 整季采集
python backend/ingest/ingest_future_fixtures.py --league-id 47 --season 2026/2027
python backend/silver/build_silver.py                    # Bronze → Silver 五表
python backend/models/features/build_match_features.py   # int_match_features
python backend/models/build_wdl_baseline.py              # 训练 + 固化工件(整季重写 gold)
python backend/models/predict_wdl_future.py              # 固定参数对 26/27 出预测
python backend/i18n/translate_players.py [--limit N]     # 中文名翻译(需 DASHSCOPE_API_KEY)
```

注意:FotMob 相关脚本需要 `THORDATA_PROXY`;`build_silver` / `build_wdl_baseline`
为 DELETE+INSERT 重建,重跑前先想清楚范围(见 docs/current-state.md 关于联赛覆盖的提醒)。

### 前端(frontend/)

```bash
npm run dev         # 本地开发(http://localhost:3000)
npm run build       # 生产构建
npm run lint        # ESLint
npm run typecheck   # tsc --noEmit
npm run test        # Vitest
npm run gen:api     # openapi.json → lib/api-types.ts(先跑后端 export_openapi)
```

类型生成链:Pydantic(后端)→ `python -m backend.cli.export_openapi` →
`npm run gen:api`,不手写第二套 schema。

## 测试与验收

```bash
pytest                          # 后端全部(tests/;含 migration/auth/entitlement/
                                #   预测锁定/NowGoal 解析/odds 管线/studio/worker/api_v1)
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build
```

- 免费 DTO 不含受限字段、锁定预测不可改、OAuth state 与扫码一次性消费、CSRF 等
  安全断言都在 pytest 内(CLAUDE.md §15)。
- `CI=1 npm run e2e`(Playwright,`frontend/playwright.config.ts` + `frontend/e2e/`
  五个 spec:匿名浏览、mock 登录、admin+studio 导出、Device Login 双 context、
  Studio PNG signature+像素校验)——真实运行 9/9 通过,无 skip;不设 `CI=1` 时
  默认复用已在跑的本地 dev 服务(`reuseExistingServer`),便于本地调试单个用例。

## 部署与备份

单机(东京 EC2)+ Cloudflare,systemd 管理,release 目录 + `current` 软链发布
(preflight 拒绝 dirty 源码树/同 SHA 重复覆盖,失败自动回滚并重新验收),
每日 SQLite `.backup`(原子发布 + checksum + 并发锁)+ 可选 S3。
只读运维检查:`python -m backend.cli.ops_check --json`(见 §11)。
完整步骤、Cache Rules、恢复演练见
[docs/deployment-aws-cloudflare.md](docs/deployment-aws-cloudflare.md);
材料在 `deploy/`(nginx / systemd / release.sh / backup_sqlite.sh / restore_verify.sh)。

## 其他文档

| 文档 | 内容 |
|---|---|
| [docs/auth-wechat.md](docs/auth-wechat.md) | 公众号配置、OAuth/扫码流程、会话与 CSRF、Mock 用法、fail-fast |
| [docs/data-sources.md](docs/data-sources.md) | FotMob/NowGoal 能力与验证状态、轮询、hash-diff、时间戳纪律、降级 |
| [docs/model-api-contract.md](docs/model-api-contract.md) | 模型输入输出、评估口径、free/pro DTO 差异 |
| [docs/prediction-integrity.md](docs/prediction-integrity.md) | 登记簿、锁定不可改、撤回、manifest、legacy 导入 |
