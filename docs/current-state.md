# 当前状态(docs/current-state.md)

> 可更新的真实当前状态。最后全面更新:2026-07-19(收口轮:数据链路闭环 / 预测账本不变量 /
> 生产 API 地址 / 认证三态 / 契约单一真源)。2026-07-20 增补:kickoff provenance +
> 实体映射安全性收口(见 §7);同日第二轮封口 5 缺陷(provider ID 完整性 / existing auto_ok
> 队名方向重验证 / 三处统一资格口径 / Studio 完整 provenance / 混合时区窗口,见 §8)。
> 动态数据只写这里,不写 CLAUDE.md。

## 0. 三种验证状态的区分(CLAUDE.md §18)

- **代码实现完成**:代码与测试存在并通过;
- **离线 fixture 验证完成**:同一条代码链路用临时三库 + 固定 fixture 端到端跑通;
- **真实外部服务验证完成**:对真实微信/NowGoal/FotMob/S3 的实际访问验证。

**当前:所有核心能力达到前两级;所有外部真实访问仍 UNVERIFIED**(无微信凭证、无住宅代理
网络访问、无 AWS 凭证;NowGoal 端点格式基于 2026-07-19 真实 probe 样例构造 fixture)。

## 1. 真实数据库状态(2026-07-19 收口后)

### data/allwin.db(core,387MB)

- dim_match 11,115 行(五大联赛);**kickoff_at_utc 已加列(migration core/0001),
  现有旧数据全部 NULL(如实——历史只有日期粒度)**;下次带代理跑 schedule_sync /
  fotmob_incremental 时由来源 utcTime 填充。
- 其余 Bronze/i18n/Silver/特征/Gold 表同前(英超 Silver,五联赛 Bronze)。

### data/platform.db

| 表 | 行数 | 说明 |
|---|---|---|
| prediction_snapshots | 760 | 380 legacy_unverified(25/26 回测)+ 380 draft(26/27) |
| prediction_outcomes | 380 | 25/26 赛果 |
| prediction_evaluations | 0 | 无正式口径样本,如实为 0 |
| prediction_manifests | 2 | 日 manifest(hash 稳定) |
| model_versions | 1 | dc-baseline-1.M.2 |
| users / subscriptions / redeem_codes | 0 | 未开放注册 |
| job_runs | 9 | 本轮真实 worker 运行记录 |

### data/odds.db

| 表 | 行数 | 说明 |
|---|---|---|
| dim_team_alias | 178 | **真实数据**:entity_resolution 任务从 core 全联赛队名+i18n 种入 |
| dim_team_xref / dim_match_xref | 0 | 待真实 NowGoal 轮询产生(离线链路已验证会写入) |
| bronze_ng_odds_snap / bronze_fm_*_snap | 0 | 待真实采集(需网络;离线 fixture 已验证同链路) |
| silver_odds_moves / silver_event_moves / gold_move_cooccurrence | 0 | 同上 |
| poll_state | 0 | 真实轮询开始后记录节流状态 |

**说明**:真实 odds 数据为 0 不是缺陷掩盖——采集需要真实网络与(FotMob 侧)住宅代理;
本轮交付的是"今天起可持续积累"的完整可运行链路,并用离线 fixture 全链验证
(tests/backend/test_pipeline_e2e.py,见 §3)。

## 2. 收口轮交付(全部代码实现 + 离线验证完成)

### P0-1 数据链路(DONE,离线验证)

- 精确开球:core migration `0001_dim_match_kickoff`;FotMob fixtures/match 解析保留
  utcTime → `kickoff_at_utc`(仅日期→NULL,不伪装);MatchSummary API 暴露该字段。
- 轮询调度:`backend/ingest/poll_windows.py` + odds `0002_poll_state`;72h 窗口,
  2–72h/15min、0–2h/5min 节流持久化;`poll_nowgoal --due`、`poll_fotmob_snapshots --due`;
  systemd `allwin-poll.timer` 每 5 分钟触发到期判断。
- market_phase 由来源状态+精确 kickoff+观察时间共同判定(不足→unknown);
  FINAL=`final_pre_match_snapshot()`(kickoff 前最后一条 pre_match,不精确不认收盘)。
- FotMob 快照:同场同轮单次 payload → 阵容+两队伤停三快照共用 observed_at/poll_run_id。
- 实体解析:全联赛别名(去英超硬编码);kickoff ±30min 校验(超差降 needs_review);
  **dim_team_xref 实际写入与复用**(auto_ok 时登记 provider 球队 id↔canonical,含反转换算);
  独立 CLI `resolve_entities`。
- Silver/Gold:`build_odds_silver` CLI;worker 默认链 = CLAUDE.md §13 十二步,核心任务
  全部实名注册(无"模块缺失 optional"占位);analysis_bundle_build 为真实任务
  (真实库上 50/50 构建成功)。
- 离线端到端:`tests/backend/test_pipeline_e2e.py`——映射→两次赔率快照(变化)→
  两次阵容/伤停快照(变化)→ moves → 共现 → API(premium 完整时间轴+同期事件)→
  bundle;重跑幂等;节流验证;FINAL 验证。

### P0-2 预测账本(DONE)

永久资格不变量落地:track record/评估/manifest 不再用 status/superseded_by 排除正式样本;
retract/supersede 只是透明标注;开球后 supersede 拒绝;无精确 kickoff 不得 publish/lock
(历史锁定样本不受影响);7 条回归测试;响应带修正链字段
(snapshot_id/superseded_by/correction_of/superseded_note)。

### P0-3 生产 API 地址(DONE,自动化验证)

`lib/api-base.ts` 单一真源:浏览器生产同源 `/api/v1`(实测:无 env 生产构建下浏览器请求
发往同源,bundle 经 `check_browser_bundle.sh` 零回环地址);服务端 INTERNAL_API_BASE;
release.sh 构建前加载 shared/.env + 业务冒烟(products/matches JSON + 首页真数据)+ 失败回滚。

### P0-4 认证三态 + 扫码(DONE,Mock E2E)

- production+ENABLED=0:无凭证可启动(实测 uvicorn:healthz 200,微信端点 503
  `{"code":"AUTH_DISABLED"}`,`/api/v1/auth/methods` 供前端显示"暂未开放");
- production+ENABLED=1 缺凭证 / production+mock:实测 fail-fast;
- 登录页真二维码(qrcode 本地 canvas,内容只含公开 URL);
- Playwright 双 context 完整 Device Login(桌面建请求→手机 OAuth 批准→桌面轮询→
  会话生效→二次 claim 410 / 错 secret 403 / 过期 410)。

### P0-5 契约单一真源(DONE)

全部 JSON operation 有 Pydantic response model(51 paths);Redirect/204/文件流在 responses
显式声明;统一 ErrorDetail + AuthDisabledDTO;`tests/backend/test_contract.py`(2xx schema
非空、free 分支无受限概率字段、openapi.json 与 app 同步);`npm run check:api-drift`;
前端手写 API DTO 迁移为生成类型派生(见前端代理汇报)。

### P1 Studio PNG(DONE,真实浏览器)

Playwright 真实下载 PNG:signature + IHDR 实际 1080×1920 / 1080×1350 + 大小下限;
JSON/SRT 服务端导出与画面内数据截止/模型版本同测。

## 3. 测试面(收口后)

- pytest:231 项(2026-07-20 第二轮封口后;migration/auth/entitlement/api-v1/合约/
  预测不变量/统一资格口径/NowGoal 解析/实体映射安全/odds 管线/管线端到端/studio/worker);
- vitest:20 项(api-base 解析矩阵 + api-v1);
- Playwright:9 项(匿名×4、mock 登录链、admin+studio 导出、device login×2、PNG 验收);
- 具体最新计数以当次验收汇报为准,不在本文件反复更新。

## 4. UNVERIFIED(真实外部)

- 微信服务号真实 OAuth/回调(需 AppID/AppSecret + 网页授权域名);
- NowGoal 真实端点连续采集与历史回填能力(fixture 按 2026-07-19 probe 样例构造);
- FotMob 实时抓取(需 THORDATA_PROXY 网络出口;fotmob_snapshot 任务在缺代理时如实 failed);
- S3 备份上传 / Object Lock(脚本在无凭证时如实只做本地);
- Cloudflare 缓存 HIT/BYPASS(需真实域名接入)。

## 5. 已知遗留(不阻塞,如实记录)

- 真实 odds 数据积累需在有网环境启用 allwin-poll.timer 后开始;
- in-play(滚球)采集为显式未实现(赛前窗口结束即停);
- dev(Turbopack)在自动化浏览器下水合停滞(生产构建正常,E2E 跑生产构建);
- 中文覆盖:球队/球员中文名仍主要覆盖英超(P1)。

## 6. 真实库行数快照(2026-07-20)

- **allwin.db**:dim_match 11,115;`kickoff_precision` 已加列,现有旧数据全部 `date_only`
  (有 Date、无精确时刻),`kickoff_source` NULL(不编造)。精确 kickoff 待带代理重抓 FotMob。
- **platform.db**:prediction_snapshots 760(380 legacy_unverified + 380 draft),全部
  `is_official=0` / 未锁定;**official+locked=0**(尚无正式样本)。迁移后 `kickoff_precision`
  默认 `unknown`;可运行 `repair_kickoff_provenance` 将其重分类为 `date_only`(真实库上
  已在临时副本验证:760→date_only,official/locked 保持 0)。
- **odds.db**:dim_team_alias 178(真实种子);xref/Bronze/Silver/Gold/poll_state 全 0(待真实采集)。

## 7. kickoff provenance + 实体映射安全性收口(2026-07-20,代码实现 + 离线/副本验证完成)

- **P0-A 精确开球时间来源**:core `dim_match` 与 platform `prediction_snapshots` 均加
  `kickoff_precision`(exact/date_only/unknown)+ `kickoff_source`(migrations core/0002、
  platform/0003)。publish/lock 的精确性资格由快照冻结的 provenance 判定,**不再看字符串
  是否含 'T' 或是否等于午夜**(唯一真源 `backend.db.util.is_exact_kickoff`:precision=exact
  + 非空来源 + 带显式时区可解析)。`import_gold_predictions` 不再拼 `T00:00:00Z` 伪精确;
  新增 `repair_kickoff_provenance`(dry-run / 幂等 / 只改未锁定 / 断言 official-locked 不变)。
  历史 official/locked 永久资格与数量不受迁移影响(当前为 0)。
- **P0-B 实体映射安全**:auto_ok 要求双边可解析的精确 kickoff(kickoff_diff 非 None 且
  ≤30min);team xref 冲突显式检测(同 provider_team_id → 不同 canonical)→ 整场降级
  needs_review 且不写任何 team xref(事务一致,无部分写入);不覆盖 verified/manual、
  不复活 rejected。详见 docs/data-sources.md §2.4。
- **验证**:pytest 全量 192 passed(含新增 `TestKickoffProvenanceIntegrity` §6.1、
  `TestEntityResolutionSafety` §6.2、`test_kickoff_provenance.py` migration/repair);
  migration 在三库真实副本上双跑幂等 + integrity ok;repair 在真实 platform.db 副本上
  dry-run/run/幂等实跑通过。真实 FotMob/NowGoal 网络访问仍 UNVERIFIED。

## 8. 第二轮封口:5 缺陷(2026-07-20,代码实现 + 离线验证完成)

延续 §7,关闭审查发现的 5 个剩余缺陷:

- **provider team 身份完整性**(`entity_resolution._new_pair_validation_errors`):新映射
  auto_ok 现要求 provider 主客 ID 都存在、互异、pairs 内部自洽;重复/缺失 provider ID
  一律 `needs_review` + `validation_errors`,**零 team xref 写入**,不再靠 `INSERT OR IGNORE`
  把半映射吞成看似成功。
- **existing auto_ok 重验证队名与方向**(`_revalidate_existing_auto_ok`):除 kickoff 外,
  用本次 schedule_row 的队名/别名按 `home_away_inverted` 独立验证主客方向;纯换名、主客
  互换、provider ID 缺失/重复、team xref 冲突都会原子降级 `needs_review`。confirmed/
  verified 不受影响。
- **三处统一正式资格口径**(`backend/prediction_scope.py::official_sample_where`):track
  record / evaluation / **daily manifest** 共用同一 `julianday()` 判据。修复前 manifest 只查
  `is_official`,会把开球后发布/未锁定的 official 也纳入,与 track record 集合不一致。
- **Studio 完整 provenance**(`studio/bundle.py::_kickoff_uncertainty`):按
  `normalize_exact_kickoff`(kickoff_at_utc + precision + source)判定,exact+缺来源/naive/
  非法时间也如实提示;date_only 文案"只精确到比赛日",其余"缺少可验证的精确开球时间"。
- **混合时区采集窗口**(`poll_windows.upcoming_precise_matches`):窗口边界改用
  `julianday()`,带 `-05:00`/`+08:00` 偏移的合法 kickoff 不再被裸文本范围误排除。
- **验证**:pytest 全量 **231 passed**;新增 20 类反例回归测试(entity 1-10 / manifest
  11-15 / studio 16-19 / poll 20),逐项通过公开函数与 DB 行为验证。5 个可复现反例
  (重复 provider ID / existing auto_ok 换名 / manifest 口径不一致等)修改前实测复现、
  修改后关闭。`frontend/lib/openapi.json` 与 `api-types.ts` 本轮未改动(SHA-256 前后一致);
  `check:api-drift` 为已知前置缺口(PRE-EXISTING / OUT OF SCOPE,本轮不修)。真实
  FotMob/NowGoal 网络访问仍 UNVERIFIED。

## 9. 会员权益投影 + HTTP/Cloudflare 缓存隔离(2026-07-20,代码实现 + 离线验证完成)

真实基线审计(执行在先,发现下述问题后才动代码)确认三类缺陷:

- **异常路径普遍缺 `Cache-Control`**:FastAPI 在 endpoint 内 raise 异常时,注入的
  `Response` 对象上设置的 header 会被丢弃,只有异常处理器产出的响应才到达客户端——
  401/403/404/422/500、`/healthz`/`/readyz` 之前一律没有该 header(真实 TestClient 探测
  确认,不是推测)。
- **`routes_public.py` 的"公开"端点完全不检查请求凭证**:`/api/v1/products` 等对携带
  Cookie/Authorization 的请求同样返回 `public, s-maxage=...`。
- **`backend/api_server.py` 的 4 个 legacy 端点从不设置任何 `Cache-Control`**,成功或失败
  路径皆然。
- **Goal A 数据投影**:`routes_public.match_analysis` 里 `counter_evidence` 的
  `draw_risk` 条目、以及由它预渲染出的 "risk" `script_section` 文本,会把真实平局概率
  原样带给免费层调用者,绕过 `prediction:full_wdl` 门禁(chart_specs/prediction_member/
  script_sections 的"probability"段此前已正确投影,但这一条被漏掉)——本轮修复。

修复:

- 新增 `backend/api/cache_policy.py`(纯 ASGI `CachePolicyMiddleware`,不用
  `BaseHTTPMiddleware`,只改写 `http.response.start` 的 header,不缓冲/改变 body):
  请求带 Cookie/Authorization 或响应带 Set-Cookie → 强制 `private, no-store`;路径不在
  显式 `PUBLIC_ALLOWLIST` → 同样强制;都不触发但完全没设置 header → 默认
  `private, no-store`;否则保留 endpoint 自己的声明。`backend.api.app:app` 与
  `backend.api_server.app` 均已接入。
- `error_handlers.py` 的未捕获异常(500)处理器直接在返回的 `JSONResponse` 上设置该
  header——这是唯一无法被上述中间件覆盖的路径(Starlette `ServerErrorMiddleware` 是最
  外层中间件,其兜底响应经顶层原始 `send` 直接发出,不经过任何 `user_middleware`,已用
  真实探测脚本验证并记录在测试注释里)。
- `routes_public.py::match_analysis`:免费层投影新增过滤 `counter_evidence` 里的
  `draw_risk` 条目,并用过滤后的列表重新拼接 "risk" script_section 文本(不能只在数组
  级过滤——该文本在过滤前已经被预渲染成字符串)。
- `deploy/nginx/allwin.conf.example`、`docs/deployment-aws-cloudflare.md`(legacy 纳入
  Bypass 规则、补充源站保证说明)、`docs/architecture.md`(登记 `cache_policy.py`/
  `error_handlers.py`)同步更新。

验证:新增 `tests/backend/test_cache_policy.py`(46 项,临时三库 fixture,不碰真实
`data/*.db`,不依赖网络)——权益矩阵覆盖 EPL/top5 门禁、prediction 全 JSON 深度扫描、
analysis bundle 递归哨兵值扫描(含此前遗漏的 draw_risk 泄漏点)、odds 延迟摘要 vs 完整
时间线(新鲜快照区分,非仅数组长度)、cooccurrence 计数 vs 明细、订阅状态(过期/撤销/
未来生效不计入,多条有效订阅取最高 rank,admin 角色不代替 entitlement);缓存矩阵覆盖
公开白名单、凭证强制降级(有效/无效 session、纯 CSRF cookie、Authorization)、
entitlement 敏感端点恒 no-store、认证/会员/admin 成功与失败路径、401/403/404/422/429/
500/AUTH_DISABLED 503、Set-Cookie 路径、legacy 双 app、未分类新路径默认 no-store。
后端全量 pytest **322 passed**(276 本轮开始前的基线 + 46 本轮新增);
`python -m compileall -q backend` 通过。前端 `npm test`(28 passed)/`typecheck`/
`lint`/`build`/`check:api-drift` 全部通过,`openapi.json`/`api-types.ts` SHA-256 本轮
未变。真实 Cloudflare 边缘 HIT/BYPASS 行为(而非源站响应头)仍 UNVERIFIED——本轮未接触
真实 AWS/Cloudflare 账号。

## 11. 生产可靠性收口:E2E provenance + 备份/发布/systemd/运维可观测性(2026-07-20,代码实现 + 离线验证完成)

真实基线审计(真实执行,非假设)确认 8 类风险全部成立:E2E seed 的 kickoff
provenance 不合法导致 Playwright webServer 起不来;`backup_sqlite.sh` 缺 1-2 个库
仍 `exit 0`;`restore_verify.sh` 用裸 glob 接受不完整备份;并发 backup 无锁保护、
同 UTC 秒时间戳会冲突;`release.sh` 无 dirty-source 检查、rsync 排除不全、允许
覆盖同 SHA release 目录、回滚不重新验收;`allwin-poll.service` 的两条独立
`ExecStart=` 会让 NowGoal 失败阻止 FotMob 被尝试;`allwin-worker.timer`(15 分钟)
与 `allwin-poll.timer`(5 分钟)重复调度 nowgoal_snapshot/fotmob_snapshot,且
`run_chain()` 把"被锁"等同"失败"会级联跳过整条 15 分钟链;`/readyz` 泄漏具体
迁移文件名与原始 SQLite 异常文本给未认证调用者。

- **A. E2E provenance**:`tests/e2e/seed_e2e.py` 改用显式 `kickoff_source=
  "e2e_fixture:synthetic_exact"` + `kickoff_precision="exact"`,诚实标注非真实
  数据源;`_require_precise_kickoff`/`normalize_exact_kickoff`/publish_snapshot/
  lock_snapshot/正式样本资格查询/migration 触发器一行未改;比赛选择改为
  `Date > 今天` 过滤,不再信任可能陈旧的 `status='NotStarted'`。`CI=1 npx
  playwright test` **9/9 全部真实通过**(含 Device Login 双 context、Studio PNG
  signature+像素尺寸校验)——此前两轮被这个问题阻塞的 E2E 现已打通。
- **B. 备份/恢复**:`backup_sqlite.sh` 改为 `.incomplete-<TS>-<PID>/` 临时目录
  暂存,三库 `.backup`+`integrity_check`+SHA-256 全部通过后 `mv` 原子发布为
  `backup_metadata.json` 标记 `complete:true` 的时间戳目录;缺任一库现在必定
  `exit 1`;新增 `noclobber` 文件锁(而非 mkdir+子文件,避免 TOCTOU——5 路并发
  压力测试验证:恰一个成功、其余明确 `exit 75`,不互相覆盖);`umask 077`;
  `BACKUP_KEEP` 校验为正整数。`restore_verify.sh` 只接受带
  `backup_metadata.json` 且 `complete:true` 的目录,额外校验 SHA-256 与
  migration 无 pending/checksum drift(`backend/db/migrate.py` 新增
  `status()` 的 `checksum_drift` 字段,`ops_check`/`restore_verify` 共用)。
  `tests/backend/test_backup_restore.py`(24 项,真实调用两个脚本,真实三库,
  非 mock)。
- **C. release.sh**:新增 `preflight()`(必需命令、三库存在、磁盘下限、
  `current` 软链形状合法、默认拒绝 dirty source、同 SHA release 目录拒绝覆盖);
  rsync 排除扩到 `.env`/`.env.*`/`frontend/.env.local`/`.pytest_cache`/
  `test-results`/`playwright-report`/`.claude`/`.codex`;`rollback()` 切回
  `previous` 后用同一套 `verify_live`+`business_smoke` **重新验收**,仍失败才
  报"人工介入"(不假装已恢复);`grep -qF --` 处理 `SMOKE_HTML_MARKER` 防选项
  注入;清理旧 release 严格限定在 `APP_ROOT` 内且 `current`/`previous` 永不清理。
  全部逻辑拆成可 `RELEASE_SH_SOURCE_ONLY=1` 单独 source 调用的函数;
  `tests/backend/test_release_rollback.py`(23 项,离线模拟,真实
  git/rsync/sqlite3,假 sudo/systemctl,不连真实 systemd)。
- **D. systemd 调度拓扑**:新增 `backend/worker/poll_wrapper.py` 顺序执行
  nowgoal_snapshot+fotmob_snapshot、汇总退出码,`allwin-poll.service` 从两条
  `ExecStart=` 改为一条;`runner.py` 新增 `--periodic`(排除
  `PERIODIC_CHAIN_EXCLUDE`),`allwin-worker.service` 改用 `--chain --periodic`
  避免与 `allwin-poll.timer` 重复调度;`allwin-poll.service` 补齐此前完全缺失
  的加固指令(`NoNewPrivileges`/`PrivateTmp`/`ProtectSystem`/`ReadWritePaths`/
  `TimeoutStartSec`/`TimeoutStopSec`/`UMask`),其余单元补齐缺失的
  `TimeoutStopSec`/`UMask`。`tests/backend/test_poll_scheduling.py`(21 项)。
- **E. ops_check + readyz 脱敏**:新增 `backend/cli/ops_check.py`(只读,三库
  可读性/migration 状态/备份新鲜度/磁盘/job_runs/source_health,阈值全部走
  `OPS_*` 环境变量,退出码 0/1/2,JSON/文本双输出);`/readyz` 不再把具体迁移
  文件名和原始 SQLite 异常文本下发给未认证调用者,只返回
  `pending_migrations=N`/`unavailable` 等稳定标识,真实异常记服务端日志。
  `tests/backend/test_ops_check.py`(35 项,含"读迁移状态查询/损坏库查询本身
  也要 try/except,不能让一个库的损坏崩溃整个工具"这类真实发现)。

**验证**:后端全量 pytest **430 passed**(322 本轮开始前 + 108 本轮新增,含
`test_migrations.py` 新增的 `checksum_drift` 单测);`python -m compileall -q
backend` 通过;全部 4 个 `deploy/scripts/*.sh` `bash -n` 通过;`shellcheck`
本机未安装,UNVERIFIED;前端 `npm test`(28 passed)/`typecheck`/`lint`/`build`/
`check:api-drift` 全部通过;`CI=1 npx playwright test` **9/9 真实通过**;
`git diff --check` 无空白错误;真实 `data/{allwin,platform,odds}.db` 全程
size/mtime 字节级不变(所有测试/手工验证只使用临时目录或指定测试用 tmp_path);
`openapi.json`/`api-types.ts` 因 `/readyz` docstring 变化而改变(`description`
字段文本,非结构/类型变化),已用 `python -m backend.cli.export_openapi &&
npm run gen:api` 规范重新生成,`check:api-drift` 确认零漂移。真实 AWS
EC2/systemd/Nginx/S3 Versioning/Cloudflare 仍 UNVERIFIED——本轮未接触任何真实
线上账号或凭证。

## 10. 生产可靠性收口:定点修正(2026-07-20,针对独立复核 PARTIAL 结论关闭 3 项)

独立攻击式复核对上一轮(§9 之后的 E2E/发布/备份/systemd/ops_check 收口)判定
**PARTIAL**,确认 1 个 P1 + 2 个 P2,均已修复,不涉及其余已确认无问题的部分
(权益投影/缓存隔离/E2E provenance/备份恢复原子性/回滚重验/systemd 调度拓扑
均维持复核通过状态,未重新改动):

- **P1 release.sh 凭证文件排除不完整**:`do_rsync()` 的 `--exclude` 列表原来
  只覆盖 `.env` 家族,遗漏通用私钥/证书/凭证文件。补齐 `.ssh`/`.aws` 整个目录、
  `id_rsa(.pub)`/`id_dsa(.pub)`/`id_ecdsa(.pub)`/`id_ed25519(.pub)`、
  `*.pem`/`*.key`/`*.p12`/`*.pfx`、`credentials.json`——rsync 排除模式不带 `/`
  时按 basename 匹配任意深度,根目录和嵌套子目录同样生效,不需要额外配置;
  只排除具体文件名/扩展名,不粗暴排除所有 `*.json`。另加
  `assert_no_credentials_in_release()` 作为复制完成后的纵深防御扫描(不是
  主机制,rsync `--exclude` 才是)。真实临时 fake-source(根目录 + 嵌套子目录
  各放一份全部 11 类凭证文件)验证:`do_rsync()` 后凭证文件与 `.ssh`/`.aws`
  目录均未进入 release,`backend/app.py`/普通 `.json`/`README.md` 仍正常复制。
- **P2 ops_check 阈值非法时静默回退**:原 `_env_float`/`_env_int` 解析失败直接
  返回默认值,可能让写错的阈值悄悄关掉真实告警(如
  `OPS_DISK_CRITICAL_PCT=200` 会让磁盘用满也不报 CRITICAL)。新增
  `ConfigError` + `OpsConfig`(frozen dataclass):`OpsConfig.from_env()` 严格
  校验(磁盘百分比必须 finite 且 `0 < warn < critical <= 100`;四个时间阈值
  必须严格正整数),非法立即抛出,不返回部分校验结果;`main()` 捕获后打印
  可定位到变量名的简短 stderr(不含 traceback/Secret/完整路径)并以
  `2`(CRITICAL)退出,不执行任何数据库/磁盘检查。同一次 `run_all_checks()`
  只 resolve 一次配置,传给 `check_backup/check_disk/check_job_runs/
  check_source_health`,避免同一次运行前后阈值不一致。`check_*` 函数保持
  `config: OpsConfig | None = None` 签名,import 阶段不读取/校验任何环境变量
  (不会因残留的非法 `OPS_*` 环境变量拖垮测试收集)。真实子进程验证:非法阈值
  → 退出码 2、stderr 含变量名、stdout 无(部分)JSON、无 Traceback;合法自定义
  阈值 → 正常运行且确实改变检查结果(如 warn=80% 时 75% 磁盘占用判 OK,默认
  70% 时判 WARN)。
- **P2 ops_check 摘要脱敏不足**:原 `_sanitize_summary` 只做长度截断,不识别
  内容。重写为"单行化 + 去控制字符 → 脱敏 → 最后截断"三步:URL userinfo、
  常见 `password=/token=/secret=/api_key=/authorization=/user=` 键值对、
  `Authorization: Bearer <token>`、`proxy=user:pass@host`、Unix 绝对路径
  (`/Users`/`/home`/`/root`/`/opt`/`/srv`/`/var`/`/etc`/`/tmp`)、Windows 绝对
  路径均替换为稳定占位符;SQL 语句与 Traceback/异常堆栈形状的内容整体退化为
  `[SQL_REDACTED]`/`[TRACEBACK_REDACTED]`(结构不可预测,不做局部脱敏);脱敏
  严格先于截断,避免在半个 Secret 处截断。真实临时 `odds.db` 注入含唯一标记
  的 7 类攻击摘要(URL userinfo、键值对、Bearer token、Unix/Windows 路径、SQL、
  多行 traceback),分别验证 `_sanitize_summary`/`check_source_health`/
  `run_all_checks`/CLI `--json` 输出均不含任何原始标记;`connection refused`
  等安全消息原样保留。

**验证**:定向测试 `pytest tests/backend/test_release_rollback.py
tests/backend/test_ops_check.py` **115 passed, 4 skipped**(25 + 90;4 个
skip 是显式记录原因的空字符串边界用例,由另一条测试覆盖同一语义,非隐瞒);
后端全量 pytest **487 passed, 4 skipped**(430 本轮开始前 + 本轮新增
61 项测试——`test_release_rollback.py` 23→25、`test_ops_check.py` 35→94,
94 项里 4 个显式 skip,故净增 57 passed);
`python -m compileall -q backend` 通过;`deploy/scripts/{backup_sqlite,
restore_verify,release,check_browser_bundle}.sh` `bash -n` 全通过;
`backend/worker/poll_wrapper.py` 是 Python 模块而非 `.sh` 脚本,用
`python -m py_compile` 做等价语法检查(未使用不存在的
`deploy/scripts/poll_wrapper.sh`);`git diff --check` 无空白错误;前端
`typecheck`/`lint`/`npm test`(28 passed)/`check:api-drift`/`build` 全部通过;
`CI=1 npm run e2e` **9/9 真实通过**(含 Device Login 双 spec、Studio PNG
signature+像素校验),无 skip。真实 `data/{allwin,platform,odds}.db` 本轮前后
SHA-256 逐字节一致;真实 `data/backups/` 目录清单与内容未变;
`openapi.json`/`api-types.ts` SHA-256 本轮**未变**(本轮未改动任何 Pydantic
响应模型或 API 路由)。真实 AWS/systemd/Nginx/S3/Cloudflare 仍
UNVERIFIED——本轮未接触任何真实线上账号或凭证。

## 11. §10 定点修正:`_sanitize_summary` 真正泄漏 + ConfigError 回显 + 人为 skip(2026-07-20)

独立只读复核在 §10 判定 DONE 后,真实复现 `_sanitize_summary()` 仍有 4 类局部
替换泄漏(只删 `Basic` 留下完整 Base64 凭证;引号内值只删第一个词;JSON 形式
`key:value` 完全绕过;带空格的绝对路径只删前半段),以及 `ConfigError` 把
`raw!r` 原始环境变量值写进 stderr、和 4 个人为制造的 pytest skip。三项均已
关闭:

- **`_sanitize_summary` 改为"安全优先、整体退化"**:不再对命中的敏感模式做
  局部子串替换(那需要准确判断 secret 从哪到哪结束,引号/JSON/带空格路径都
  会让判断失败),而是分类检测"这段文本是否命中某种不安全形状",命中即把
  **整条** text 换成稳定占位符:`[SQL_REDACTED]`(新增 `WITH`/`REPLACE`/
  `ATTACH`/`DETACH`/`VACUUM` 关键词)、`[TRACEBACK_REDACTED]`、
  `[AUTH_REDACTED]`(`Authorization:`/`Bearer`/`Basic` 三种形状统一整体清除,
  不再只删 scheme 名词)、`[CREDENTIAL_REDACTED]`(URL userinfo、
  `proxy=user:pass@host`)、`[SECRET_REDACTED]`(敏感 key 只检测"是否存在
  `key 分隔符` 形状",不试图捕获/替换 value——天然兼容带引号、JSON、带空格的
  value)、`[PATH_REDACTED]`(Unix/Windows 绝对路径只检测前缀是否出现,不猜
  路径在哪结束)。检测顺序即优先级,不影响安全性——命中任一类,原文都被整体
  清除。`connection refused` 等不命中任何模式的安全文本原样保留。
- **`ConfigError` 不再回显原始环境变量值**:`_parse_finite_float`/
  `_parse_positive_int`/磁盘阈值关系校验的错误消息统一只报变量名和规则(如
  "OPS_DISK_CRITICAL_PCT 不是合法有限数字"),不再拼接 `raw!r`——即便有人误把
  Secret 填进一个数字阈值变量,配置校验失败本身也不会把它打进 stderr/journal。
  新增子进程测试:把 `OPS_DISK_CRITICAL_PCT` 设为唯一标记
  `SUPER_SECRET_SHOULD_NOT_APPEAR`,断言该标记完全不出现在 stdout/stderr。
- **删除人为 skip**:`test_time_thresholds_reject_non_positive_integers` 原来
  把空字符串混进"非法值"参数化列表、又在测试体里手动 `pytest.skip()`——空
  字符串本是"未设置,用默认值"的合法语义,不应该出现在非法值列表里。现在从
  非法值列表移除空字符串,改为 4 个时间阈值各自独立的
  `test_time_threshold_blank_value_uses_default` 参数化测试,不使用 skip 或
  xfail。

**验证**:定向测试 `pytest tests/backend/test_ops_check.py` **114 passed, 0
skipped**;`pytest tests/backend/test_release_rollback.py
tests/backend/test_ops_check.py` **139 passed, 0 skipped**;后端全量 pytest
**511 passed, 0 skipped, 0 failed**(487+4skip 本轮开始前 → 511+0skip,net 24
新增测试且清零全部 skip);`compileall`/`git diff --check` 均通过。真实复现
4 类原始报告的泄漏样例(`Authorization: Basic ...`、`password="TOP SECRET
VALUE"`、`{"password":"JSON_SECRET_123"}`、带空格绝对路径)在修复后均确认
唯一标记完全不出现;`ConfigError` 唯一标记测试确认 stdout/stderr 均不含原始
环境变量值。真实 `data/{allwin,platform,odds}.db` SHA-256、真实
`data/backups/` 目录、`openapi.json`/`api-types.ts` SHA-256 本轮均未变(未
接触真实 data/*.db 或生成任何 API 契约改动)。
