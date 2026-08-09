# 架构(docs/architecture.md)

> 本文描述真实代码的模块、数据库边界与数据流,以仓库内实际文件为准(2026-07-19 核对)。
> 长期纪律与锁定条款见 `CLAUDE.md`;动态数据量与未完成项见 `docs/current-state.md`。

## 1. 运行拓扑

拓扑真源是 `CLAUDE.md` §4,部署细节见 `docs/deployment-aws-cloudflare.md`。摘要:

```text
用户 → Cloudflare(DNS/TLS/缓存/WAF) → 东京 EC2 Nginx
        ├── /api/v1/*、/healthz、/readyz → FastAPI(uvicorn,127.0.0.1:8000)
        └── 其他路径                     → Next.js(next start,127.0.0.1:3000)
Python Worker(systemd timer,15 分钟)→ 采集/预测/结算/manifest/备份
SQLite 三库位于本地 EBS(data/ 或 ALLWIN_DATA_DIR)
```

- 生产 API 入口:`uvicorn backend.api.app:app --host 127.0.0.1 --port 8000`。
- `/healthz`:进程存活,恒 200;`/readyz`:三库可读 + migration 无 pending,否则 503 + problems 列表(`backend/api/app.py`)。

## 2. 后端模块地图(backend/)

### 2.1 新架构模块(本次施工交付)

| 目录/文件 | 职责 | 关键内容 |
|---|---|---|
| `api/app.py` | FastAPI 应用装配 | `create_app()`:加载 AuthSettings(production fail-fast)、构建微信 Provider、装配 v1 路由、挂载旧 `/api/league/*` 兼容层(标 deprecated)、`/healthz` `/readyz` |
| `api/cache_policy.py` | 缓存隔离(app 层 default-deny) | 纯 ASGI `CachePolicyMiddleware`:请求带 Cookie/Authorization 或响应带 Set-Cookie → 强制 `private, no-store`;路径不在显式 `PUBLIC_ALLOWLIST` → 同样强制;未设置任何 Cache-Control(异常路径等遗漏)→ 默认 `private, no-store`;`backend.api.app:app` 与 `backend.api_server.app` 都已接入,回归见 `tests/backend/test_cache_policy.py` |
| `api/error_handlers.py` | 全站统一错误契约 | `register_error_handlers(app)`:HTTPException/422/未捕获异常统一为 `{code,message,details}`;未捕获异常(500)由 Starlette `ServerErrorMiddleware` 直接发送、不经过任何 `user_middleware`,故其 `Cache-Control` 头在这里直接设置,不依赖 cache_policy 中间件兜底 |
| `api/deps.py` | 请求级依赖 | 三库 ro/rw 连接依赖、`AuthContext`(user/role/plan/entitlements)、`require_user/require_admin/require_entitlement/require_csrf`、Origin allowlist、限流键 |
| `api/schemas.py` | Pydantic DTO(单一真源) | `PredictionFreeDTO`(只有 top_outcome/top_probability)与 `PredictionFullDTO` 物理分离;OpenAPI → 前端 TS 类型生成 |
| `api/routes_auth.py` | `/api/v1/auth/*`、`/api/v1/me` | 微信 OAuth start/callback、Device Login、密码登录(admin)、logout |
| `api/routes_public.py` | `/api/v1` 公开数据 | leagues/standings/fixtures/matches/prediction/analysis/odds/cooccurrence/track-record/model/metrics/products |
| `api/routes_member.py` | `/api/v1` 会员 | redeem、favorites、account、sessions/revoke |
| `api/routes_admin.py` | `/api/v1/admin/*` | users、grant/revoke 订阅、redeem-codes、predictions publish/lock/retract/publish-upcoming、audit-logs |
| `api/routes_admin_odds.py` | `/api/v1/admin/xref` | dim_match_xref 人工审核(confirm/reject) |
| `api/routes_studio.py` | `/api/v1/studio/*` | bundle 读取、drafts CRUD、状态流转、导出(png 登记/txt/json/srt 服务端生成)、下载 |
| `api/routes_analytics.py` | `/api/v1/analytics/events` | 最小化匿名埋点(204) |
| `api/ratelimit.py` | 应用层限流 | 内存滑窗 `limiter.allow(key, n, seconds)` |
| `auth/config.py` | 认证配置 | 环境变量 → `AuthSettings`;production 缺配置/检测到 Mock 拒绝启动 |
| `auth/providers.py` | 微信 Provider Adapter | `RealWechatOAProvider`(snsapi_base)/ `MockWechatProvider`(仅 development,双保险拦截) |
| `auth/service.py` | 认证核心逻辑 | opaque session(只存 SHA-256)、oauth state 一次性消费、device login 原子领取、CSRF 校验 |
| `auth/entitlements.py` | 权益解析 | 有效订阅中 rank 最高 plan → entitlement 集合;匿名/无订阅 = free |
| `commands/` | 可写业务命令 | `predictions.py`(登记簿状态机/结算/manifest)、`subscriptions.py`、`redeem.py`、`audit.py` |
| `queries/` | 只读查询 | `leagues.py`(LEAGUE_META + 门禁)、`matches.py`、`predictions.py`(对外快照口径)、`track_record.py`(正式样本口径) |
| `db/` | 连接工厂 + migration | `paths.py`(三库路径,ALLWIN_DATA_DIR 重定向)、`connections.py`(`connect_ro`/`connect_rw`/`tx`)、`migrate.py`(SQL migration runner)、`util.py`(UTC/UUID/hash/token) |
| `migrations/` | SQL 迁移 | `core/`(现有 allwin.db,暂只有 README)、`platform/0001_init.sql + 0002_seed.sql`、`odds/0001_init.sql` |
| `ingest/entity_resolution.py` | NowGoal↔FotMob 实体解析 | 别名种子、按日 ±1 天候选匹配、auto_ok/needs_review,绝不静默 verified |
| `ingest/odds_snapshots.py` | odds.db 快照落库 | hash-diff(payload 不变不落库)、source_health append-only |
| `providers/nowgoal.py` | NowGoal Provider Adapter | 日程/赔率解析(纯函数)、主客反转归一、WAF 检测、网络获取分离 |
| `providers/fotmob_snapshots.py` | FotMob 阵容/伤停快照 | `extract_*` 纯函数离线可测;`fetch_match_payload` 延迟 import fotmob_client |
| `silver/odds_moves.py` | odds.db 派生 | 变化点(silver_odds_moves/silver_event_moves)+ 时间共现(gold_move_cooccurrence),幂等 |
| `models/` | 模型 | `features/build_match_features.py`(int_match_features)、`build_wdl_baseline.py`(DC+isotonic 训练)、`predict_wdl_future.py`(固定参数出未来预测) |
| `eval/metrics.py` | 评估指标纯函数 | Accuracy/Brier/LogLoss/RPS/Calibration,离线运行 |
| `studio/bundle.py` | analysis_bundle | 同一份 bundle 驱动比赛详情页与 Studio;含导出渲染(txt/srt) |
| `worker/runner.py` | 轻量 Worker | 任务注册表、job_runs 全生命周期、文件锁、幂等键、有限重试/退避/超时、`--chain --from --periodic`(`--periodic` 跳过 `PERIODIC_CHAIN_EXCLUDE`=nowgoal_snapshot/fotmob_snapshot,避免与 allwin-poll.timer 重复调度) |
| `worker/poll_wrapper.py` | allwin-poll.service 调度包装 | 顺序执行 nowgoal_snapshot + fotmob_snapshot,任一失败不阻止另一个被尝试,汇总退出码(代替两条独立 systemd ExecStart=) |
| `cli/` | 命令行工具 | `create_admin`、`import_gold_predictions`、`evaluate_predictions`、`build_manifest`、`poll_nowgoal`、`poll_fotmob_snapshots`、`resolve_entities`、`build_odds_silver`、`repair_kickoff_provenance`、`export_openapi`、`ops_check`(只读运维检查,见 docs/deployment-aws-cloudflare.md §11) |

### 2.2 既有(legacy)模块,保留兼容

| 文件 | 状态 |
|---|---|
| `api_server.py` | 旧 4 路 `/api/league/*` GET 路由;由 `api/app.py` 挂载并标 deprecated,不再扩展;`?simulate_membership` 绕过入口已移除,门禁走 AuthContext |
| `fotmob_client.py` | curl_cffi + Chrome TLS 指纹 + ThorData 住宅代理;模块 import 时即要求 `THORDATA_PROXY` |
| `scheduler.py` | 旧英超增量调度脚本;worker 的 `fotmob_incremental` 复用其 step1 |
| `schema.py` / `init_db.py` | allwin.db 旧建表 DDL 与初始化 |
| `ingest/ingest_league.py` / `ingest_match.py` / `ingest_future_fixtures.py` | FotMob Bronze 采集脚本 |
| `i18n/` | 中文名映射(seed_curated / translate_players,DashScope Qwen-MT) |
| `silver/build_silver.py` | Bronze → Silver 五表聚合(按联赛+赛季 DELETE+INSERT 幂等) |
| `verify/` / `scripts/` | 一次性核对与修复脚本(已跑过,留档) |

旧脚本 `from db import get_connection` 仍可用:`backend/db/__init__.py` 保留 `DB_PATH`/`get_connection` 兼容导出。

## 3. 前端结构(frontend/)

```text
frontend/
├── app/
│   ├── layout.tsx / page.tsx / globals.css
│   ├── (public)/league/[id]/{standings,matches,team-stats,players}/page.tsx
│   └── (member)/league/[id]/wdl-predictions/page.tsx
├── components/
│   ├── SiteNav.tsx / LeagueNav.tsx / LeaderboardCard.tsx(+ CSS Modules)
│   ├── EChart.tsx(ECharts 封装)
│   └── charts/SpecCharts.tsx(chart_spec 渲染)
├── lib/
│   ├── api.ts        # 旧 /api/league client(随兼容层逐步退役)
│   ├── api-v1.ts     # /api/v1 client
│   ├── api-types.ts  # openapi-typescript 生成,不手写
│   ├── openapi.json  # python -m backend.cli.export_openapi 的输出
│   └── analytics.ts
├── tests/api-v1.test.ts + vitest.config.ts
└── package.json      # dev/build/lint/typecheck/test/gen:api/e2e
```

说明:

- 全部页面目前为 Server Component;样式为 CSS Modules + globals.css 设计变量。
- CLAUDE.md §11.1 的产品页(`/matches`、`/matches/[matchId]`、`/track-record`、`/pricing`、`/login`、`/account`、`/studio`、`/admin` 等)属于 P0.8/P0.9 交付范围,截至本文核对尚未建成;现有联赛页保留并逐步接入 `/api/v1`。
- `frontend/features/` 目录(CLAUDE.md §5.1 目标结构)尚未创建;目录迁移按宪法要求渐进进行。

## 4. 三个 SQLite 库与连接纪律

| 库 | 文件 | 内容 | migration |
|---|---|---|---|
| core | `data/allwin.db` | FotMob Bronze(dim_match/dim_player/fact_*)、i18n、int_match_features、Silver 五表、gold_wdl_predictions；离线验证但尚未真实迁移的 schedule identity/state/observation/current/rest-lineage v1 | `core/0001`～`0003`；现有表不破坏性重建，0003 尚未应用真实库 |
| platform | `data/platform.db` | users/auth_identities/auth_sessions/oauth_states/device_login_requests/account_links;roles/plans/plan_entitlements/products/subscriptions/redeem_codes;预测登记簿六表;favorites/content_drafts/export_jobs;job_runs/audit_logs/analytics_events | `platform/0001_init.sql`(含锁定触发器)+ `0002_seed.sql` |
| odds | `data/odds.db` | dim_team_xref/dim_team_alias/dim_match_xref;bronze_ng_odds_snap/bronze_fm_lineup_snap/bronze_fm_sideline_snap;silver_odds_moves/silver_event_moves/gold_move_cooccurrence;source_health;poll_state | `odds/0001_init.sql`～`0002_poll_state.sql` |

连接纪律(`backend/db/connections.py`,CLAUDE.md §5.3):

- 查询路径 `connect_ro(name)`:URI `mode=ro` + `PRAGMA query_only`,物理上无法写。
- 写路径 `connect_rw(name)`:autocommit 模式 + `tx(conn)` 显式 `BEGIN IMMEDIATE … COMMIT/ROLLBACK` 短事务。
- 每个连接启用 WAL、foreign_keys、busy_timeout=30000。
- API 请求内不执行长跑批/训练/批量采集;跑批全部走 CLI 或 worker。
- migration runner(`backend/db/migrate.py`):每个迁移单事务、失败整体回滚、checksum 漂移拒绝执行、重复运行幂等;`--all/--db/--status/--data-dir`。
- 备份用 SQLite `.backup`(`deploy/scripts/backup_sqlite.sh`),WAL 活跃时禁止只复制主文件。

## 5. 数据流:Bronze → Silver → Gold → API

```text
FotMob ──ingest_league/ingest_match/ingest_future_fixtures──▶ core Bronze(dim_match, fact_*)
   │                                                               │
   │  providers/fotmob_snapshots(阵容/伤停)                        ├─▶ i18n(dim_team_i18n/dim_player_i18n)
   ▼                                                               ├─▶ silver/build_silver ─▶ Silver 五表
odds.db bronze_fm_*_snap                                           ├─▶ models/features ─▶ int_match_features
                                                                   └─▶ models/*_wdl_* ─▶ gold_wdl_predictions
NowGoal ──providers/nowgoal + cli/poll_nowgoal──▶ odds.db bronze_ng_odds_snap
   │        (ingest/entity_resolution 建 dim_match_xref/dim_team_alias)
   ▼
silver/odds_moves ─▶ silver_odds_moves / silver_event_moves ─▶ gold_move_cooccurrence

gold_wdl_predictions ──cli/import_gold_predictions──▶ platform.db 预测登记簿
   (draft/legacy_unverified → 管理员 publish/lock → official → 赛后 settle → 评估/manifest)

三库 ──backend/queries(mode=ro)──▶ backend/api(/api/v1 DTO 投影)──▶ 前端 / Studio
```

要点:

- 实体身份(xref)与中文显示(i18n)分离:`dim_team_xref`/`dim_match_xref` 管跨源对齐,`dim_team_i18n` 只管显示(CLAUDE.md §6.1)。
- `gold_wdl_predictions` 是模型当前产物(整季 DELETE+INSERT 可重写);公开账本是 platform.db 登记簿,锁定后不可改(见 `docs/prediction-integrity.md`)。
- 时间共现只表达"固定时间窗内同期发生",表名与文案不用因果词(CLAUDE.md §6.4)。

## 6. Worker 任务链

`backend/worker/runner.py`,job_runs 记录在 platform.db。生产两个 timer:
`allwin-worker.timer`(15 分钟)触发 `--chain --periodic`(跳过
nowgoal_snapshot/fotmob_snapshot,这两步已由 allwin-poll.timer 独立调度,
避免两个定时器争抢同一把 `data/locks/<job>.lock`——`run_chain()` 把"被锁"
和"失败"同等对待,重复调度会让整条 15 分钟链被良性锁竞争无谓地级联跳过);
`allwin-poll.timer`(5 分钟)通过 `backend/worker/poll_wrapper.py` 顺序执行
nowgoal_snapshot + fotmob_snapshot 两个采集任务的"到期判断"(真实频率由
odds.db poll_state 节流:2–72h 每 15 分钟,0–2h 每 5 分钟),两者互不阻塞、
汇总退出码。手动端到端重跑仍用不带 `--periodic` 的完整 `--chain`。

```text
schedule_sync → fotmob_incremental → nowgoal_snapshot → fotmob_snapshot
→ entity_resolution → core_silver_build → odds_silver_build → model_predict
→ prediction_register → analysis_bundle_build → postmatch_settle → metrics_rebuild
```

核心任务全部为真实注册任务(CLAUDE.md §13):
- `nowgoal_snapshot` = `python -m backend.cli.poll_nowgoal --due`;
- `fotmob_snapshot` = `python -m backend.cli.poll_fotmob_snapshots --due`(需 THORDATA_PROXY);
- `entity_resolution` = `python -m backend.cli.resolve_entities`(全联赛别名种子 + xref 状态);
- `odds_silver_build` = `python -m backend.cli.build_odds_silver`(moves + 时间共现,幂等);
- `analysis_bundle_build` = 包内函数,对窗口内 NotStarted 场次逐场构建
  (与详情页 /Studio 共用同一 `backend/studio/bundle.py`)。
外部凭证缺失时任务如实记 failed + 原因,不以"模块不存在"跳过。

- 某步 failed 后,后续步骤记 skipped(依赖检查);`--from <step>` 支持从中间重跑。
- 幂等键 `(job_name, idempotency_key)` 已成功 → skipped;`--force` 强制重跑。
- 文件锁 `data/locks/<job>.lock` 防叠跑(陈锁按 pid 存活自动清理)。
- 采集失败只写 job_runs/source_health,不拖垮 API,不覆盖最后成功数据。
