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
| `ALLOWED_ORIGINS` | 写请求 Origin/Referer 白名单(逗号分隔) |
| `SESSION_TTL_DAYS` | 会话有效期(默认 30) |
| `OAUTH_STATE_TTL_SECONDS` | OAuth state 有效期(默认 600) |
| `DEVICE_REQUEST_TTL_SECONDS` | 扫码登录请求有效期(默认 300) |
| `COOKIE_SECURE` | development 下置 `1` 强制 Secure cookie(production 恒开) |
| `ALLWIN_ADMIN_PASSWORD` | (可选)create_admin 非交互模式的密码来源;交互模式走 getpass |
| `S3_BACKUP_BUCKET` | (可选)备份 S3 桶;未配置时备份脚本只做本地备份 |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | (可选)不用 EC2 instance role 时的 AWS 凭证 |

部署侧另有 `BACKUP_KEEP`(本地保留备份份数,默认 14),见
[docs/deployment-aws-cloudflare.md](docs/deployment-aws-cloudflare.md) §9。

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
python -m backend.cli.poll_nowgoal --date 2026-07-19           # NowGoal 单轮采集
python -m backend.cli.poll_nowgoal --date 2026-07-19 --offline-fixture tests/fixtures/nowgoal/poll_fixture.json
python -m backend.cli.export_openapi                           # 导出 OpenAPI → frontend/lib/openapi.json
```

### Worker(任务链)

```bash
python -m backend.worker.runner --list                # 列出注册任务
python -m backend.worker.runner --job silver_build    # 单任务
python -m backend.worker.runner --job schedule_sync --key 2026-07-19   # 幂等键
python -m backend.worker.runner --chain               # 全链(生产由 systemd timer 触发)
python -m backend.worker.runner --chain --from silver_build            # 从中间步骤重跑
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
- `npm run e2e`(Playwright)脚本已就位,但 playwright.config 与 e2e 用例尚未编写
  ——当前跑不出结果,属 P0.8 前端产品页交付范围。

## 部署与备份

单机(东京 EC2)+ Cloudflare,systemd 管理,release 目录 + `current` 软链发布,
每日 SQLite `.backup` + 可选 S3。完整步骤、Cache Rules、恢复演练见
[docs/deployment-aws-cloudflare.md](docs/deployment-aws-cloudflare.md);
材料在 `deploy/`(nginx / systemd / release.sh / backup_sqlite.sh / restore_verify.sh)。

## 其他文档

| 文档 | 内容 |
|---|---|
| [docs/auth-wechat.md](docs/auth-wechat.md) | 公众号配置、OAuth/扫码流程、会话与 CSRF、Mock 用法、fail-fast |
| [docs/data-sources.md](docs/data-sources.md) | FotMob/NowGoal 能力与验证状态、轮询、hash-diff、时间戳纪律、降级 |
| [docs/model-api-contract.md](docs/model-api-contract.md) | 模型输入输出、评估口径、free/pro DTO 差异 |
| [docs/prediction-integrity.md](docs/prediction-integrity.md) | 登记簿、锁定不可改、撤回、manifest、legacy 导入 |
