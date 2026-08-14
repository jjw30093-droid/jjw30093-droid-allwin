# 当前状态(docs/current-state.md)

> 可更新的真实当前状态。最后全面更新:2026-07-19(收口轮:数据链路闭环 / 预测账本不变量 /
> 生产 API 地址 / 认证三态 / 契约单一真源)。2026-07-20 增补:kickoff provenance +
> 实体映射安全性收口(见 §7);同日第二轮封口 5 缺陷(provider ID 完整性 / existing auto_ok
> 队名方向重验证 / 三处统一资格口径 / Studio 完整 provenance / 混合时区窗口,见 §8)。
> 2026-07-21 增补:瑞典超(Allsvenskan)正式接入 + 双样本本地隔离实验,见 §12
> ——过程中真实发现并修复 NowGoal type=6/14 此前从未验证过的两处缺陷。
> 动态数据只写这里,不写 CLAUDE.md。
> 本文件持续追加到 2026-08-04(见 §21、§22);上面这段"最后全面更新"日期
> 只反映最早几轮,不代表整份文件的新鲜度——数据层当前状态请看
> `docs/data-plan.md`,本文件只保留逐轮真实记录。
> 2026-08-08 增补:挪超(59)/瑞典超(67)2026 赛季已完赛比赛 + 历史赔率回补 +
> 32 队中文名双重验证,见 §30——补齐了 §12/§18 只接入未来赛程留下的缺口。

## 0. 三种验证状态的区分(CLAUDE.md §18)

- **代码实现完成**:代码与测试存在并通过;
- **离线 fixture 验证完成**:同一条代码链路用临时三库 + 固定 fixture 端到端跑通;
- **真实外部服务验证完成**:对真实微信/NowGoal/FotMob/S3 的实际访问验证。

**当前:** 验证状态按模块分别记录，不能再使用“所有外部访问均 UNVERIFIED”的旧总括。
FotMob/NowGoal 已有后续模块的真实访问证据；微信真实 OAuth、S3/AWS 和未逐项执行的
provider 能力仍保持 UNVERIFIED。最新 FotMob 多联赛历史覆盖证据见 §20.11。

## 1. 真实数据库状态(2026-07-19 收口后)

> ⚠️ 本节行数快照已被 §21 与 `docs/data-plan.md` §2 取代(五大联赛 Silver 覆盖、
> 挪超接入、迁移状态均已变化),仅作历史记录保留,不代表当前状态。

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

> ⚠️ 以下计数是 2026-07-20 收口时的快照,已被后续多轮工作超过(如 §21 记录
> 768 pytest)。测试数以运行 `pytest`/`vitest`/`playwright` 的真实输出为准,
> 不看本节数字。

- pytest:231 项(2026-07-20 第二轮封口后;migration/auth/entitlement/api-v1/合约/
  预测不变量/统一资格口径/NowGoal 解析/实体映射安全/odds 管线/管线端到端/studio/worker);
- vitest:20 项(api-base 解析矩阵 + api-v1);
- Playwright:9 项(匿名×4、mock 登录链、admin+studio 导出、device login×2、PNG 验收);
- 具体最新计数以当次验收汇报为准,不在本文件反复更新。

## 4. UNVERIFIED(真实外部)

> ⚠️ 这份列表是 2026-07-19 时点快照。"NowGoal 真实端点连续采集"与
> "FotMob 实时抓取"两项此后已有真实外部验证证据(见 §12、§19、§21、
> `docs/data-plan.md` §3),不再是完全 UNVERIFIED——但仍非全量场景覆盖,
> 具体范围以 `docs/data-plan.md` §3 验证状态表为准。

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

> ⚠️ 本节行数已被 §21 与 `docs/data-plan.md` §2 取代,仅作历史记录。

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
- **验证(历史阶段快照)**:该阶段当时的 pytest 验收通过(含新增
  `TestKickoffProvenanceIntegrity` §6.1、`TestEntityResolutionSafety` §6.2、
  `test_kickoff_provenance.py` migration/repair；当前计数只见 §13.3);
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

## 11b. §10 定点修正:`_sanitize_summary` 真正泄漏 + ConfigError 回显 + 人为 skip(2026-07-20)

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

## 12. 瑞典超(Allsvenskan)正式接入 + 双样本本地隔离实验(2026-07-21)

### 12.1 联赛接入(代码实现 + 真实数据验证)

- **联赛元数据**:`backend/queries/leagues.py::LEAGUE_META` 新增
  `67: {code: "allsvenskan", name_zh: "瑞典超", entitlement: "league:lottery"}`
  ——FotMob league id 67 已用真实 API 探测确认(240 场 2026 赛季赛程),season 参数
  为自然年字符串 `"2026"`(不是五大联赛式 `"2025/2026"`)。
- **entitlement**:新迁移 `platform/0004_lottery_entitlement.sql` 把
  `league:lottery` 加进 free/pro/premium 三档(全新库、从 0003 升级两种路径均验证,
  重复执行不产生重复行);`platform/0005_model_league_scope.sql` 新增
  `model_versions.applicable_league_ids` + `prediction_snapshots.league_id`
  (锁定后不可改写,新触发器 `trg_pred_snap_locked_league_immutable`)。
- **API**:`/api/v1/leagues` 正确列出 67(`accessible=true` 对匿名/free 同样成立,
  与 `league:top5` 的 pro-only 不同);fixtures/standings/match-detail 均验证通过;
  未知联赛 id 仍 404;不污染 top5 过滤。`openapi.json`/`api-types.ts` 本轮
  **零漂移**(未改动任何 Pydantic 响应模型)。
- **前端**:`/matches` 页面 100% 由 `/api/v1/leagues` 驱动,新增联赛零代码改动即可
  出现;真实发现并修复一个**预先存在的缺陷**——5 个旧 `/league/[id]/*` 页面
  (standings/matches/players/team-stats/wdl-predictions)标题写死"英超",
  与实际查看的联赛无关;改为 `fetchLeagueNameZh()`(复用 `/api/v1/leagues`
  同一单一真源,不新建联赛名单)动态取值,`wdl-predictions` 页顺带去掉写死的
  "26/27"赛季前缀。
- **模型适用范围保护**:`backend/commands/predictions.py` 新增
  `_require_model_applicable_to_league`,opt-in 设计(不传 `league_id` 的历史/
  既有调用方零回归,全量 ~500 项既有测试原样通过);`publish_snapshot`/
  `lock_snapshot`/`supersede_snapshot` 均已接入。`dc-baseline-1.M.2`
  (`applicable_league_ids=[47]`)无法被误发布为瑞典超正式预测,新增 6 项回归
  测试(`TestModelLeagueScope`)覆盖未声明范围/范围不含/范围含/不传 league_id
  向后兼容/lock 独立拦截/supersede 继承。
- **中文球队名**:新增 `backend/i18n/seed_allsvenskan_teams.py`,16 支真实球队
  (真实 FotMob team_id,取自 2026-07-21 真实 ingest)译名写入 `dim_team_i18n`,
  `source` 诚实标注为 `allsvenskan_onboarding_single_pass_not_3vote_verified`
  ——**不**冒用 `seed_curated.py` 的 `workflow_verified`(那是英超 28 队三票
  独立核验过的标签,本批未经同等核验)。有争议译名(单人判断,非共识):
  AIK → "AIK索尔纳"(常见替代:直接不译"AIK");Djurgården → "尤尔加顿"
  (常见替代转写:"迪尔加登"等)。

### 12.2 真实外部数据验证(隔离实验目录,未写入任何真实 data/*.db)

实验目录:`/tmp/allwin-allsvenskan-pilot-20260721T080254Z/data`(独立
`ALLWIN_DATA_DIR`,`backend/init_db.py` 建核心表 + `migrate.apply_all` 建三库
migration 版本,与真实库完全隔离)。

- **样本 A(最近完赛)**:Match_ID=5107539,Kalmar FF 2-2 Malmö FF,
  2026-07-20T17:00:00Z(exact,`fotmob:match_details`),第 13 轮。真实验证:
  xG(主 1.31 / 客 0.90)、射门(fact_shotmap 逐次射门坐标+xG)、阵容
  (4-2-3-1 等真实阵型 + 真实球员姓名)、赛事(进球/牌/换人时间轴)、中文队名
  (卡尔马FF/马尔默FF)、match-detail API 与前端页面均正确渲染。
- **样本 B(最近未开赛)**:Match_ID=5107547,Västerås SK vs Örgryte,
  2026-07-24T17:00:00Z(exact,`fotmob:fixtures`)——UTC/Europe-Stockholm
  (19:00)/Asia-Shanghai(次日 01:00)/Asia-Tokyo(次日 02:00)均用
  `zoneinfo` 真实换算,与题面提示的候选一致(独立复核,非盲信)。
  - NowGoal 日程探测:按换算出的北京日期 `2026-07-25` 查询,真实命中
    titan_id=2912218(队名 "Vasteras SK FK"/"Orgryte",无变音符号 + 带 "FK"
    后缀,与 FotMob 别名字符串不同)。
  - 实体解析:补充两条真实证据支持的别名(`vasteras sk fk`→6194,
    `orgryte`→10002,`source` 标注为人工补充依据)后,`resolve_entities`/
    `poll_nowgoal --date` 真实达成 `auto_ok`(confidence=1.0,
    `kickoff_diff_seconds=0`,`dim_team_xref` 正确写入两队,无方向反转)。
  - 赔率快照:`fetch_odds` 返回 3 条真实 Bet365 记录(1x2/ah/ou,initial+latest
    均非空),`market_phase='pre_match'` 正确判定,首次落库 3 条,二次相同
    payload 重跑 `inserted=0/skipped=3`(hash-diff 幂等验证)。
  - **TIME-DEPENDENT UNVERIFIED**(诚实标注,未伪造 `--now`):会话内(截至
    2026-07-21T09:02Z)距开球尚有约 80 小时,`--due` 72h 到期窗口仍未开启
    (如实返回 0 候选);市场尚未产生第二个真实变化点,故第二条变化快照与
    `silver_odds_moves`/`gold_move_cooccurrence` 的真实 move 形成本轮未能
    自然发生,`build_odds_silver` 如实返回 0 条。
- **免费概率投影(真实数据 + 真实浏览器验证)**:为瑞典超比赛注册一条测试预测
  (显式 `league_id=67`,模型显式声明 `applicable_league_ids=[67]`,不影响
  `dc-baseline-1.M.2` 的 EPL 专属范围),真实浏览器(端口 3100→8100)验证:
  匿名/免费只返回 `top_outcome`/`top_probability`,`home_probability` 等受限
  字段在网络响应体、DOM、Next.js 水合状态中均不存在(不是 null 占位);
  真实登录 + 授予 pro 后同一比赛返回完整三项概率。

### 12.3 首次真实验证发现并修复的缺陷(超出瑞典超本身范围)

`backend/providers/nowgoal.py`(type=14)与 `backend/ingest/entity_resolution.py`
(NowGoal kickoff 时区语义)此前完全基于旧项目代码审计构造,从未在本项目做过真实
网络探测。本轮首次真实交叉验证(用样本 B 的真实 FotMob kickoff 作为基准)发现:

1. NowGoal type=6 行内 kickoff **本身就是 UTC**,不是此前假设的"北京墙上时间,
   需 -8h 转换"——错误假设会导致**任何联赛**的自动实体解析都无法通过 kickoff
   差值校验,是全局性缺陷,不只影响瑞典超。已修正 `_kickoff_diff_seconds`,
   同步修正三处测试 fixture 的时间构造语义(`test_odds_pipeline.py`、
   `test_pipeline_e2e.py`)。
2. NowGoal type=14 真实响应结构是 `{ErrCode, Data:{mixodds:[...]}, MatchState}`,
   公司名字段是 `cn`;旧构造假设的 `{companies:[...]}` + `name` 字段与真实结构
   不符,导致解析器对真实响应恒返回空列表。已修复 `_iter_companies`/
   `_company_records`,新增真实结构脱敏 fixture 与回归测试
   (`odds_sample_real_shape.json`),旧构造形状仍受支持(不破坏离线测试)。

详见 `docs/data-sources.md` §2.1.1。

### 12.4 验证矩阵

- 后端全量 pytest **535 passed, 0 failed, 0 skipped**(511 本轮开始前 → 535,
  净增 24 项:6 模型范围保护 + 3 migration + 6 API v1 + 4 i18n seed + 5 NowGoal
  真实结构回归);`compileall`/`git diff --check` 通过。
- 前端 `typecheck`/`lint`/`npm test`(28 passed)/`check:api-drift` 全部通过;
  `npm run build`(全新 `.env` 空 `NEXT_PUBLIC_API_BASE`)+
  `check_browser_bundle.sh` 确认浏览器产物零回环地址。
- 本地隔离 Preview(后端 127.0.0.1:8100 指向实验库,前端 127.0.0.1:3100)真实
  浏览器验证:首页/导航显示瑞典超、赛程/积分榜可访问、完赛样本详情完整、
  未开赛样本状态正确、中文队名正确、诚实空状态(无预测/无反向证据均如实提示)、
  免费概率投影、Pro 完整概率、Studio 可选中瑞典超比赛、移动端(375×812)无溢出、
  控制台无报错;全程未触碰用户自己的 127.0.0.1:3000/8000 进程。
- 真实 `data/{allwin,platform,odds}.db` 全程 SHA-256/size/mtime 逐字节不变
  (阶段 0 与收尾时两次比对完全一致);所有写库操作均显式指定隔离
  `ALLWIN_DATA_DIR`。
- 未执行任何 git commit/push/tag/deploy;未触碰任何真实 AWS/Cloudflare/生产
  systemd 状态。

### 12.5 独立复核后收口:真实时间窗口 + Playwright(2026-07-21,同日续接)

独立对抗式复核(见会话记录,判定 PARTIALLY VERIFIED,零 P0/P1)后,针对复核指出
的两个缺口(时间性项未自然到达、Playwright 未运行)做真实收口,不修改任何已被
复核验证过的实现:

- **72h due 窗口**:复核结束时真实 UTC 为 2026-07-21T09:56:22Z,样本 B 开球
  2026-07-24T17:00:00Z,窗口开启于 2026-07-21T17:00:00Z——**仍未到期**(约剩
  7h04m)。跑了一次真实 `poll_nowgoal --due` 与 `poll_fotmob_snapshots --due`
  确认诚实返回 0 候选(未用 `--now` 伪造),既有 xref/快照未受影响。**结论不变**:
  `--due` 仍为 TIME-DEPENDENT UNVERIFIED,第二条真实变化快照与 Silver move
  同样仍为 TIME-DEPENDENT UNVERIFIED——本轮未尝试用任何方式伪造市场变化。
- **NowGoal kickoff UTC 语义**:补充 2 场不同日期/不同开球小时的真实交叉验证,
  与已有一场共 3 场零反例(详见 `docs/data-sources.md` §2.1.1)。
- **Playwright(上一轮遗漏,本轮补齐)**:新增独立配置
  `frontend/playwright.allsvenskan.config.ts`(端口 8200/3200,不复用/不占用
  3000/8000/8010/3010)+ `frontend/e2e-allsvenskan/allsvenskan.spec.ts`
  (9 个 test,覆盖联赛入口/积分榜标题/完赛详情/未开赛详情+诚实空状态/中文队名/
  免费单概率+多层无泄漏验证/Pro 完整概率/Studio 可选中/console-404-横向溢出)+
  `tests/e2e/seed_allsvenskan_pw.py`(仅创建 admin 与预置 mock pro 身份,复用
  `frontend/e2e/auth.spec.ts` 同一套 mock 身份约定,不新增机制)。**9/9 全部
  真实通过**,数据源为真实 FotMob/NowGoal ingest 产出的隔离库副本,非合成
  fixture。既有 `CI=1 npm run e2e`(8010/3010,合成 seed)同样 **9/9 通过**,
  零回归。
- **全量验收重跑**:后端 pytest **535 passed, 0 failed, 0 skipped**;
  `compileall`/`git diff --check` 通过;前端 typecheck/lint/`npm test`
  (28 passed)/`check:api-drift` 通过;删除 `.next` 后全新 plain
  `npm run build` + `check_browser_bundle.sh` 通过,独立扫描确认产物不含任何
  完整 loopback/私有网段 URL 与 ThorData 值。
- 真实三库 SHA-256/size/mtime 本次收口前后再次比对,**逐字节不变**;
  `git rev-parse HEAD` 仍为 `cfe0272`,0 tag,无 commit/push/deploy;用户
  127.0.0.1:3000/8000 全程未被触碰(3000 于复核前后两次探测均为其自身既有
  500 状态,与本轮改动无关——本轮从未向该端口发送任何写请求)。

## 13. Competition schedule fail-closed 收口与复核后施工(2026-07-24～25)

### 13.1 五轮 RED 证据

第一轮永久测试在旧 fail-open 实现上真实得到 **24 failed**，覆盖空
`allMatches`、returned season、pagination marker 和多赛季 registry。独立
对抗复核随后确认 path scoping、旧 schema、legacy test DB、import-time
credentials 和低层 parser 仍有缺口。本轮新增测试落盘后、改实现前的定向
RED 又真实得到 **21 failed**；失败栈直接复现全部五类问题，不是依据报告推断。

第三轮最终确认修复在旧实现上继续先落永久测试：pagination 大小写碰撞矩阵
真实得到 **16 failed / 2 path-scoping controls passed**，FotMob transport/
HTTP 泄漏矩阵真实得到 **4 failed**。前者复现后写字段覆盖前字段导致
`NOT_DETECTED`/`DETECTED` 而非 `UNRESOLVED`；后者复现原始异常文本和 403/500
body 进入日志或最终异常。

第四轮严格 page-dialect 收口先落 27 个定向永久测试；旧实现真实得到
**21 failed / 6 existing controls passed**。RED 覆盖新增 `page/totalPages`
方言、准确的 incomplete evidence、布尔/字符串/负数/倒置页码、单侧
casefold collision、多完整方言语义冲突，以及 CLI 下游零写入门禁。

第四轮窄幅 P1 orphan-marker 补丁又先落 13 个定向永久测试；旧实现真实
得到 **8 failed / 5 complete-dialect controls passed**。RED 直接证明完整
`currentPage/totalPages` 会掩盖额外 `pageCount`，完整 `page/pageCount`
会掩盖额外 `currentPage`，且字符串、布尔和负数孤立值同样被错误放行。

第五轮独立复核的实现前结论为 **FIX_REQUIRED**：三个 P1 分别是任一
page-family collision 会全局屏蔽其他非碰撞 orphan、HTTP 200 JSON/text/SSR
decode 异常泄漏外部文本、`response.status_code` accessor 位于 worker 安全
边界外；两个 P2 是永久测试固化了“collision only”错误证据契约及文档状态/
计数不准确。本轮先落永久测试，旧实现真实得到 pagination collision+orphan
**8 failed**，FotMob status/decode/downstream-warning redaction **25 failed**。

最终独立 P2 复核在上述生产安全边界全部通过后给出
**P0=0 / P1=0 / P2=2 / `FIX_REQUIRED`**：真实本地
`curl_cffi.Response` 对非法 UTF-8 先抛 `UnicodeDecodeError`，已被安全转换为
`FotMobDecodeError` 且无 secret 泄漏，但永久测试不可移植地写死必须出现
`JSONDecodeError`；同时四份 closure 文档把 `-W default` 下真实存在的 warnings
写成了 0。本轮只修正该测试契约和文档，没有修改
`backend/fotmob_client.py`、competition pagination 或动态时间 fixture。

### 13.2 当前实现

- 原有门禁保持：空赛事为 `EMPTY_FIXTURES`；非空赛事但目标球队 0 场允许；
  required 失败先保存全部 registry，exit 1，merge/calendar/team/rest
  零调用；required identity gate 不变。
- pagination 只检查 `raw["fixtures"]` 的**直接 metadata**，不从 `$` 递归，
  不进入 `fixtures.allMatches`。比赛内 `business.next`、
  `stats.page/pageCount`，以及 `QAData.cursor`、`details.next` 均不误判。
  fixtures 直接 URL/cursor 的 `None`/`False`/空字符串/空容器表示没有
  continuation，非空字符串为 `DETECTED`；数字、`True`、非空容器等非法
  类型为 `UNRESOLVED`。`hasMore` 只有严格 `True`/`False`/`None` 合法，其他
  类型为 `UNRESOLVED`。页码支持 `currentPage/totalPages`、
  `page/pageCount`、`page/totalPages` 三种方言；值必须是非布尔、非负整数，
  current 小于 total 为 `DETECTED`、相等为无 continuation、大于或非法为
  `UNRESOLVED`。不完整方言只记录实际存在字段及其明确缺失的 companion，
  不制造不存在字段的路径。完整方言只消费组成该方言的 key，因此可选别名
  缺失不会误报；但额外存在且未被任一完整方言消费的已知 page-family
  marker 仍是孤立字段，无论值为整数、字符串、布尔或负数都
  `UNRESOLVED`。多个完整方言语义矛盾时记录 conflict、保留已 detected
  evidence 并 fail closed。两类证据同时出现时分别保留，并把证据并集
  持久化。已知 key 使用
  `casefold() -> list[(original_key,value)]` 索引；任何大小写折叠碰撞（包括
  两个值相同）直接 `UNRESOLVED`，每个原始字段路径均持久化，不选择其中一值。
  collision 只影响其自身 normalized key，不得全局屏蔽其他非碰撞 orphan；
  companion 若已存在但发生 collision，只记录 collision、不得伪称 missing。
  无关 `someKey/SOMEKEY` 和 `allMatches` 内碰撞继续不进入 pagination 范围。
- `init_pilot_db()` 在任何 schema/data 写入前用 `PRAGMA table_info` 验证
  registry 的真实列与精确复合主键
  `(competition_id, requested_season)`。旧单列主键或缺列表抛
  `PilotSchemaIncompatibleError`；不自动迁移、不创建 downstream 表、不修改
  旧行。CLI 输出结构化 JSON、exit 1、提示新 output directory，不暴露普通
  `sqlite3.IntegrityError`。
- `parse_competition_schedule_response()` 自身验证 requested/returned
  season 及两者相等；缺失/非法抛 `SeasonUnverifiableError`，错季抛
  `SeasonMismatchError`。上层状态仍是 `SEASON_UNVERIFIABLE` /
  `SEASON_MISMATCH`。
- backend app fixture 先导入 legacy 模块，再用动态 `db_path("core")`
  monkeypatch `backend.api_server.DB_PATH`，然后装配 app。invalid-season
  合约测试在 tmp core 种入唯一 sentinel season，并断言响应来自临时库；
  请求前后真实 `allwin.db-shm` size/mtime 不变。
- `backend.fotmob_client` import 不调用 `load_dotenv()`、不读取
  `THORDATA_PROXY`，也没有 import-time proxy 常量。显式空/显式 proxy
  完全绕过环境和 dotenv；只有默认 live client 在既有环境缺失时才加载
  dotenv，最终仍缺失则抛不含代理值的 RuntimeError。snapshot provider
  同步移除重复预读逻辑。底层 `_get()` 现在只记录/抛出安全异常类别、
  HTTP status 和 attempt；不访问 HTTP body，不记录完整 URL 或外部
  `str/repr(exc)`，最终用 `FotMobTransportError` / `FotMobHTTPError from None`
  阻断含秘密的原始异常链。`status_code` 的读取与类型验证也在 worker
  `try/except` 内，accessor 异常按 max retries 进入相同 transport redaction。
  五个公开 JSON 入口统一使用安全 JSON helper；`match_details()` 的
  `response.text` 与 `__NEXT_DATA__` JSON 使用安全 text/SSR helper。所有 decode
  错误只保留代码内固定 operation 和安全 exception class，在原 `except` 结束
  后以 `FotMobDecodeError from None` 抛出，cause/context 均不可访问。
  `parse_season_player_stats()` 跳过失败维度的原业务语义不变，但 warning
  不再记录外部 stat name、URL 或 `str(exc)`。
- 会发布 prediction 的 API、cache-policy 与 studio fixtures 不再依赖会过期
  的固定未来日期；各 fixture 在运行时计算 timezone-aware UTC 当前时间
  +3 天，在其内部复用同一结果，并在 publish 前断言 kickoff 仍严格在未来。
  API fixture 还从同一结果派生 season、date、`kickoff_at_utc` 和 snapshot。
- 当前单季 CLI 没有跨赛季配对原始响应，故
  `season_parameter_verified=NULL`；成功状态仍是
  `RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED`，不把 returned season
  匹配夸大成 endpoint 参数有效。

### 13.3 当前验收

- 真实 curl response 测试修正前单独运行 **1 failed**，实际安全输出为
  `FotMobDecodeError ... UnicodeDecodeError`；测试契约修正后 **1 passed**。
  当前测试继续严格检查固定 `team_data` operation、安全 Python identifier、
  proxy/Basic/Bearer/token/body/path/invalid-bytes marker 在
  str/repr/traceback/args/log/stdout/stderr 中均不存在，以及
  cause/context 为 `None`、`suppress_context=True`。FakeResponse 的
  ValueError/JSONDecodeError/UnicodeDecodeError/自定义异常矩阵未削弱。
- FotMob status/decode/downstream-warning 定向 **25 collected / 25 passed**；
  competition pilot **193/193**；team pilot **93/93**；联合
  **286 collected / 286 passed**。这些命令均 0 failed/skip/xfail/warnings。
- API/cache/studio 三个全文件 **78/78**；`tests/backend/test_contract.py`
  **30/30**；Allsvenskan API 类 **7/7**；三条独立命令都各有 1 个相同来源的
  `StarletteDeprecationWarning`。
- 安全九文件 FotMob/provider **342 collected / 342 passed**，0
  failed/skip/xfail，1 个 `StarletteDeprecationWarning`。十文件范围只做
  collect，真实为 **346 collected**，collection 出现同一个 warning；
  `tests/backend/test_e2e_seed.py` 的四项为 **NOT RUN / UNVERIFIED**。
- 确认隔离后运行同范围后端广泛回归
  (`tests/backend --ignore=tests/backend/test_e2e_seed.py`)：
  **531 collected / 531 passed**，0 failed/skip/xfail、3 warnings：
  1 个上述 Starlette/httpx 第三方弃用警告，以及 2 个既有测试资源清理
  `ResourceWarning`。其一由
  `TestProductionDisabledUvicornSmoke` 的 `Popen(stdout=PIPE)` 分配但未关闭
  BufferedReader，延迟到 `TestLegacyGateRemoval` 附近才被 GC 报告；其二由
  `TestMarkerNotInjectable` 创建 `HTTPServer` 后只 shutdown、未
  `server_close()`，留下 socket。二者是测试清理问题，不是生产逻辑或第三方
  库根因，也不属于本轮 FotMob P2，故如实保留而未用 filter 隐藏。
- `StarletteDeprecationWarning` 在多个独立 pytest 进程各出现一次，但属于
  同一个 warning 来源，不相加冒充多个独立缺陷。
- compileall 使用
  `PYTHONPYCACHEPREFIX=$(mktemp -d /tmp/allwin-final-p2.XXXXXX)`，覆盖
  `analysis backend tests/backend`，不在仓库写 bytecode。
- 本轮未联网、未读取真实代理凭证、未发 Club World Cup 请求、未增加
  production migration/Worker 接入。

### 13.4 当前完整性与判定

- `data/{allwin,odds,platform,verify_leagues}.db` 的 SHA-256、size、mtime
  与本轮新基线逐项相同。
- 全部既有 WAL/SHM 的集合、SHA-256、size、mtime 相同；没有新增、删除、
  checkpoint、mtime 恢复或给生产连接加 `immutable=1` 伪装修复。
- Database main-file integrity = **PASS**；strict WAL/SHM integrity =
  **PASS**。
- branch/HEAD 仍为 `main@cfe0272`，stash 为空；无 commit/push/tag/deploy，
  未清理用户 dirty worktree。
- 上一轮独立只读复核误刷新了三个既有 `.pyc`；该历史 review-integrity
  事件仍是 **FAIL**，本轮没有删除、重建、touch 或伪造恢复。本轮在事件后
  只比较各轮自己的开始/结束快照。2026-07-25 本轮快照开始/结束均为
  107 个 `__pycache__` 目录、808 个 `.pyc`；内容摘要均为
  `9cd6b06d389483315befa6cc07c8936fb8f363a5810210245674e2771789cda7`，
  元数据摘要均为
  `9db30ce3fda74a4fa7844a93439698cd7f26ac39300d8dde51fb5a035d7692f3`。
  `.pytest_cache` 两端都存在 8 个子路径，path/content/metadata 摘要分别为
  `2c20c901a05c082f2b73cc3477a0c7df138df4afe365e8554fb1e5b23b6259c0`、
  `109a0908bc90086540dac44e17d6d8cf6e8966b335f7376b6fef59d25f9d92ae`、
  `e5762be1694a87a4309980e752b93448addccab72e08329e7adfb85c4e6f5683`。
  排除 `.git` 内 Codex 自身 turn-diff 元数据后的仓库 pathset 两端均为
  40,496 项，摘要均为
  `8badae4439c6033aceff051a27294d453a75ff690e185452ee392fddb6b732eb`。
  这不是永久基线；所有 pytest 禁止 bytecode/pytest cache，compileall 只写
  `/tmp`。

完整 endpoint pagination 行为和 Club World Cup 仍 `UNVERIFIED`。
当前模块状态为 **OPEN / READY FOR FINAL INDEPENDENT P2 RE-REVIEW**。
Ready for final independent P2 re-review = **YES**；
Ready for Club World Cup single-competition pilot = **NO**；
Ready for scale = **NO**。

## 14. Club World Cup 单赛事 pilot（2026-07-25）

前一模块经独立复核得到
`READY_FOR_CLUB_WORLD_CUP_SINGLE_PILOT` 后，用户单独授权最多三次真实
FotMob HTTP 请求。本轮实现独立
`analysis/club_world_cup_single_pilot/`，不修改 production FotMobClient、
registry、数据库、Worker、systemd 或 frontend。

### 14.1 实际请求与发现

底层 `cffi_requests.get()` 实际 **3/3** 次，client 固定
`max_retries=1, retry_delay=0`，并有 transport 前计数门禁；没有第四次请求、
隐藏 retry、`check_ip`、`match_details`、`team_data` 或 pagination follow。

1. `daily_matches("20250619")` 唯一发现：
   Match `4685744`，Man City (`8456`) vs Wydad Casablanca (`102050`)，
   kickoff `2025-06-18T16:00:00Z`，competition `78` /
   `FIFA Club World Cup Grp. G`。
2. `league_matches(78, "2025")`：
   details `78` / `FIFA Club World Cup`，returned season `2025`，
   pagination `NOT_DETECTED`，daily Match ID 与主客方向交叉验证通过。
3. `league_matches(78, "2023")`：
   returned season `2023`，7 个唯一 Match ID，日期
   `2023-12-12`～`2023-12-22`，与 2025 交集 0，
   `SEASON_PARAMETER_EFFECTIVE`，pagination `NOT_DETECTED`。

请求 1 后初版 display-name gate 曾把来源简称 `Man City` 错拒为非
`Manchester City`，workflow 当时正确停止且没有请求 2。修正先加永久测试，
再复用已保存的 0600 daily response，只给恢复流程剩余 2 次底层预算；没有
重放请求 1，最终总数仍为 3。

### 14.2 2025 数量与 pagination 边界

2025 `fixtures.allMatches` 实际 **66** 条，Match ID 全有效且无重复：

- finished / non-cancelled：**63 / 63**；
- source-declared cancelled：**3**（León 小组赛每轮一条）；
- Manchester City：**4**；
- 日期范围：`2025-06-15`～`2025-07-13`。

因此 raw total 相对理论 63 是 `+3`，状态为
`COUNT_DIFFERS_FROM_OFFICIAL_FORMAT_REFERENCE`；没有删除 cancelled 记录来
凑数。finished/non-cancelled 的 63 与赛制参考对齐，曼城 4 场也对齐。

2025 与 2023 两份 response 的已知 pagination marker 均为
`NOT_DETECTED`、evidence 空。这只说明保存响应未检测到 continuation，不证明
endpoint 永不分页。本轮没有跟随任何 pagination。

### 14.3 测试、artifact 与状态

- 新模块永久离线测试：**37 passed**；
- team + competition pilot：**286 passed**；
- failed/skipped/xfailed/warnings：均 **0**；
- `/tmp` compileall、`git diff --check`：exit 0；
- raw/summary 仅在
  `/tmp/allwin-cwc-single-pilot-20260725T100615Z/`，全部 mode 0600；
- stdout/stderr/report/raw redaction 扫描：0 findings / PASS；
- 未 commit/push/tag/deploy/stash/clean。

最终 verdict：
**GO_SINGLE_COMPETITION_DATA_VALIDATED**。

GO 只验证 competition `78` 的保存 2025 单赛事响应，不授权批量抓取、
production integration、自动分页或“所有 season 永远有效”。详细证据见
`docs/audits/club-world-cup-single-pilot.md`。

### 14.4 Pilot runner 永久封存收口（2026-07-25）

后续独立零联网复核分离确认：

- Data = **GO_SINGLE_COMPETITION_DATA_VALIDATED**；
- Runner = **SEALED / REPLAY_GUARD_FIX_REQUIRED_BEFORE_REUSE**；
- 三次授权请求已全部消耗（3/3），没有第四次授权。

复核用 fake transport 证明原
`resume_live_from_saved_daily()` 的 `RequestBudgetGuard` 只在单次调用中有效：
对已完成 output directory 重复恢复，会先进入
`league_matches(78, "2025")` 并消耗一次 fake transport，之后才在保存 2025
raw 时因 `O_EXCL` 发现文件已存在。该反例分类为
`RECOVERY_REPLAY_GUARD_NOT_DURABLE`，不推翻三份已保存 raw 的独立数据重算。

本轮没有把一次性 pilot 改造成通用 collector，也没有新增 durable request
ledger。选择永久封存：

- `run_live()` / `resume_live_from_saved_daily()` 现在在 output allocation、
  artifact read/write、`FotMobClient` 构造、代理读取、DNS 和 transport 之前
  固定以非零码返回 `LIVE_RUNNER_SEALED`；
- runner verdict = **PERMANENTLY_SEALED**；
- `execute_pilot(client, tmp_path)` 仅保留为 FakeClient 离线 fixture harness；
- 未来 production integration 必须另建具有持久化 job/poll 状态的模块，不得
  复活本 pilot runner；
- production integration = **NOT STARTED**，未授权 registry、Worker、
  systemd、production DB 或批量接入。

永久测试先在旧实现得到 runner-seal **4 failed / 37 deselected**，随后定向
**4 passed / 37 deselected**。最终本轮离线验收：CWC pilot
**41 collected / 41 passed**；team + competition
**286 collected / 286 passed**；FotMob status/decode/downstream-warning
**25 collected / 25 passed**。全部通过命令均 0 failed/skip/xfail/warnings。
本轮 network request count = **0**。

数据边界不变：competition `78` 的 2025 raw 仍为 66 条（63
non-cancelled、3 cancelled、Man City 4），2023 为 7 条且 ID 交集 0；
pagination 结论严格是 **NOT_DETECTED for these saved responses**，不声明
endpoint 永不分页。

本轮开始/结束复核确认：branch/HEAD/stash、dirty/untracked pathset，四主库及
既有 WAL/SHM，六个 live artifact 的 SHA/size/mode/mtime，以及 round-local
pyc/pytest cache 均除获准源码、测试和三份文档内容修改外保持不变；没有
commit/push/tag/deploy/stash/clean。

## 15. Club World Cup 离线 production-integration 设计（2026-07-25）

当前 active module 是独立的离线设计/临时库证明，不是 production
integration。历史数据结论仍为
`GO_SINGLE_COMPETITION_DATA_VALIDATED`；一次性 live runner 仍为
`PERMANENTLY_SEALED`，历史授权请求仍为 3/3 已耗尽。本轮没有联网，也没有
调用、恢复或修改 sealed runner。

### 15.1 永久 fixture 与输入边界

只使用已保存且前轮验证的
`competition_78_2025.json` 生成永久 fixture。生成前实际校验 source
SHA-256：
`6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`。

新增
`tests/fixtures/fotmob/cwc_2025_competition_schedule_canonical.json`：

- 25,256 bytes；
- SHA-256
  `020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1`；
- 全部 66 场，不删 cancelled；
- 只保留 competition identity / selectedSeason、match/team ID/name、
  status、utcTime、round 和固定 provenance；
- 不含 headers、请求 URL、代理、认证或无关 response metadata；
- 写明 `trimmed from validated saved response`、source SHA、转换版本及
  `NOT_DETECTED_FOR_SAVED_RESPONSE`。

永久测试运行时只读取仓库 fixture，不依赖 `/tmp` artifact 继续存在。原始
六个 live artifact 本轮只读，未修改。

### 15.2 Prototype schema 与数据证明

新增 `analysis/cwc_production_integration_design/`。`run_prototype()` 只接受
调用方显式给出的 `/tmp` 或 pytest temp 根目录内 SQLite；仓库 `data/` 在
connect 前拒绝。独立复核随后用同一 fixture、晚 5 分钟的 `observed_at`
重跑，真实触发 `prototype_competition_registry` 冲突，证明前版
`DESIGN_VALIDATED_WITH_TEMP_DB` 的幂等性只覆盖相同 observation time，不能
作为 production migration 依据。收口后五张表均使用 `prototype_` 前缀：

- `prototype_competition_registry`：
  `(provider, competition_id, requested_season)`；
- `prototype_match_calendar`：
  `(provider, provider_match_id)`；
- `prototype_team_match`：
  `(provider, provider_match_id, team_id)`；
- `prototype_team_rest_feature`：
  `(provider, provider_match_id, team_id, feature_version, input_set_hash)`；
- `prototype_schedule_observation`：
  `(provider, competition_id, requested_season, observed_at)`。

registry/calendar/team/rest 只保存稳定业务内容，`observed_at` 不参与这些行或
payload hash；observation 表追加“何时观察到哪个 source content hash”。同一
时间同一内容完全 skip；晚 5 分钟同内容只追加一条 observation；早于已有时间
的事件也允许按明确的
`APPEND_ONLY_EVENT_TIME_CAN_BE_OUT_OF_ORDER` 语义追加，不覆盖首次证据。
内容冲突会在任何 insert 前先完成业务实体预检，并在同一个事务中回滚新的
observation。

真实 fixture 的离线结果：

- registry：1；
- calendar：66（63 non-cancelled、3 cancelled）；
- team relations：132；
- observed historical rest features：126；
- León cancelled `4685727` / `4685729` / `4685730` 均完整保留，
  其 6 条 team relation 都是 `eligible_for_load=0`、
  `exclusion_reason=cancelled`，且不会出现在 previous-match 或 7/14 日回看；
- Manchester City (`8456`) 的 4 场为
  `4685744` / `4685746` / `4685748` / `4685772`，全部 finished、
  non-cancelled；按 kickoff 实算 gap 为
  `NULL` / `105` / `90` / `102` 小时；
- `4685772` 的 `AET` 被保留，但 gap 定义仍是 kickoff-to-kickoff，不扣除
  推测的加时、实际终场、旅行或训练负荷。

显式 `COMPETITIVE_CLASSES` 包含 `league` / `domestic_cup` /
`continental` / `super_cup` / `international_club`，但只有 registry
已验证时才 competitive；friendly/unknown/other/unverified registry 均不
eligible，且不从赛事名称猜分类。只有 finished + non-cancelled + exact
kickoff + verified competitive 才进入 observed load。
season strategy 显式使用 `calendar_year=2025`；另行验证 adjacent
`split_year` 和 exact configured label，绝不按赛事名称猜格式。

五表在一个事务中写入。rest feature 的稳定 payload 不含墙上时间。
`input_set_hash` 对每个 feature 分别由该队按 kickoff 排序、截至当前比赛的
`timeline[:i+1]` 计算，不含任何未来比赛。改变一场 eligible input 只会改变
该场及之后受影响的 feature lineage；当前原型的 immutable calendar 策略会
显式 conflict 并保留旧 feature，而不是覆盖。完全相同重跑全部 skipped；
同一自然键的任意内容差异抛 `PrototypeConflictError`，整批 rollback，不使用
`INSERT OR REPLACE`、silent UPDATE 或 last-write-wins。partial/drifted/constraint-weakened/
mixed-purpose prototype schema 在写前拒绝；兼容性比较完整规范化 DDL，不只看
列名和主键。

### 15.3 Point-in-time 与生产边界

当前 feature scope 固定为 `observed_historical`，只使用已经 finished 的合格
比赛。以后如设计未来赛程间隔，必须使用独立名称和 `projected` 标记，不能与
历史 observed feature 混表冒充同一语义。

当前 `prototype_match_calendar` 只证明“单份已完成历史响应的不可变导入”，
不能原样复制为定时生产轮询表或正式 migration DDL。正式 schema 必须分开：

1. `stable_match_identity`：只保存 `provider`、`provider_match_id`、
   competition identity 等稳定身份，不存可变化比赛状态；
2. `append_only_match_state_snapshot`：保存 kickoff、status、finished、
   cancelled、round、home/away（含 TBD 修正）、单场 `payload_hash` 和
   `observed_at`；每次来源状态变化追加版本，不覆盖历史；
3. `current_match_state_projection`：从有效 snapshot 确定性选出当前状态，
   必须明确排序、冲突和同事件时间多版本规则；projection 不是历史真相表；
4. `versioned_feature_lineage`：每个 as-of feature 指向实际使用的
   snapshot/input set，使后来状态不能污染早期 feature，并支持按观察时间
   重建历史计算。

可变化状态必须追加版本或快照，不得 UPDATE 覆盖旧状态，也不得把正常的
NS→FT、改期、取消或来源球队修正永久判为 immutable-key conflict。只保存
response-level hash 而没有单场快照，无法重建哪场在何时变化，同样不合格。
现有 `prototype_schedule_observation` 可以继续作为 response-level 观察证据，
但不能替代上述四层中的任何一层。
完成该 per-match mutable snapshot 设计是正式 migration 的阻断条件；本轮没有
实现它。

本轮没有：

- 正式 schema migration；
- production registry 或 `data/*.db` 写入；
- persistent job/poll state；
- Worker/systemd/API/frontend 注册；
- batch/live request；
- 对 sealed `run_live()` / `resume_live_from_saved_daily()` 的调用；
- 对其它赛事推广 competition `78` 的 season 结论。

production 长期代码不得 import `analysis.*`。下一阶段应先把稳定的纯
parse/eligibility/rest 规则提升为 `backend/schedules/` 单一真源，再让
analysis pilot 调用它；随后另行授权正式 schema/migration 与
disabled-by-default 持久 job。**Production readiness 仍为 NO**。

### 15.4 RED/GREEN 与离线回归

- 先加永久测试后，旧/未实现状态：
  **1 collection error**（目标 module 不存在），构成合理 RED；
- 初次实现：**32 passed / 2 failed**，据此修正“unfinished 保留但不进
  observed load”及冲突预置边界；
- observation/idempotency 新契约在旧实现上的 RED：
  **51 collected / 15 failed / 36 deselected**；
- observation/idempotency 定向修复后：
  **51 collected / 15 passed / 36 deselected**；
- 最终 prototype：**51 collected / 51 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition pilots：
  **286 collected / 286 passed**；
- migration + contract：**44 collected / 44 passed**；
- 所有最终有效命令均 0 failed/skipped/xfailed。本轮当前环境中每个独立
  pytest 进程均出现 1 次 SeleniumBase legacy hook 的启动期第三方
  `PytestDeprecationWarning`；未使用 warning filter。前轮记录的
  `StarletteDeprecationWarning` 在本轮这些命令中没有再次出现。

一次无效的 migration/contract harness 尝试把所有 `socket.socket` 都阻断，
误伤 AnyIO 的本地 `AF_UNIX socketpair`，得到 29 passed / 15 harness
failures。该结果不是产品失败，也不作为验收证据；修正为只阻断
`AF_INET/AF_INET6`，同时继续硬阻断 DNS、urllib、requests、curl_cffi 后，
实际 44/44 通过。没有用 warning filter。

所有 pytest 均使用
`THORDATA_PROXY=http://offline.invalid:1`、`PYTHONDONTWRITEBYTECODE=1`、
`-p no:cacheprovider -W default`。详细设计和证据见
`docs/audits/cwc-production-integration-design.md`。

`/tmp` `PYTHONPYCACHEPREFIX` compileall 与 `git diff --check` 均 exit 0。
收尾比较确认 branch/HEAD/tag/stash 不变；四主库、四个既有 WAL/SHM、六个
0600 live artifact 的 SHA/size/mtime_ns 不变；cache 仍为 107 个
`__pycache__` / 808 个 pyc 且内容/元数据摘要与本轮基线相同；
`.pytest_cache` 的 8 个子路径及内容/元数据摘要不变。observation closure
开工/收尾排除 `.git` 的 worktree pathset 均为 40,508，摘要均为
`bda75745091446efcd9c149d24f7f79f1cb17b611ee5326246e65527efbaf4be`；
Git dirty/untracked pathset 不变。Database/WAL/artifact/cache/worktree
integrity 均 **PASS**。

### 15.5 Per-feature point-in-time lineage 收口

独立反例只把 Manchester City 最后一场 `4685772` 的 kickoff 从
`2025-07-01T01:00:00Z` 改到 `02:00:00Z`。第一场 `4685744` 的
previous-match、gap、7/14 日计数及其它实际 feature 字段全部不变，但旧实现
因整支球队共用完整 timeline hash，错误地改变了第一场的
`input_set_hash` 与 `payload_hash`。永久测试在修复前真实得到：
**1 collected / 1 failed / 0 skipped / 0 xfailed / 0 warnings**。

现在 `build_feature_input_set_hash()` 是纯 point-in-time 计算：对 feature
index `i` 只接收 `timeline[:i+1]` 的稳定业务输入，并显式拒绝晚于当前 kickoff
的记录。永久测试证明：

- 修改最后一场：前三场 `input_set_hash` / `payload_hash` 不变，最后一场改变；
- 修改第二场 kickoff：第一场不变，第二场及依赖它的后续场次改变；
- 第二场变为 unfinished 或 cancelled：第一场不变，当前 feature 被移除，
  后续场次 lineage 改变；
- 只修改 observation time：四张业务表及全部 feature hash 不变，只追加
  observation ledger；
- Manchester City 仍为 4 场、`NULL / 105 / 90 / 102`，最后一场 AET 保留；
  总量仍为 66 calendar / 63 non-cancelled / 3 cancelled / 132 team /
  126 rest feature。

本轮最终有效命令：

- lineage 定向：**55 collected / 5 passed / 50 deselected**；
- prototype 全文件：**55 collected / 55 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition pilots：**286 collected / 286 passed**；
- migrations + contract：**44 collected / 44 passed**。

以上命令均 0 failed/skipped/xfailed。lineage、prototype、sealed pilot 和
team+competition 各为 0 warnings；migrations+contract 为 1 个既有
`StarletteDeprecationWarning`（FastAPI TestClient 的 Starlette/httpx
兼容路径）。未使用 warning filter；前一 observation closure 的 SeleniumBase
warning 是历史运行结果，不作为永久计数。
`PYTHONPYCACHEPREFIX=$(mktemp -d /tmp/allwin-cwc-lineage-pycache.XXXXXX)`
compileall 与最终 `git diff --check` 均 exit 0。

该轮历史状态（不代表后续 §17.2 sidecar closure）：

- `POINT_IN_TIME_FEATURE_LINEAGE_VALIDATED`；
- `NO_FUTURE_MATCH_IN_EARLIER_FEATURE_HASH`；
- `LATER_OBSERVATION_IDEMPOTENCY_VALIDATED`；
- `PRODUCTION_MUTABLE_SNAPSHOT_SCHEMA_REQUIRED`；
- Production integration：**NOT STARTED**。

### 15.6 Deterministic feature-lineage input contract 收口

最终独立对抗复核没有推翻 canonical parser 的 point-in-time 传播结果，但用
直接 helper 反例确认两个未关闭项：

- **P1**：`build_feature_input_set_hash()` 接受全部属于历史、但前两场乱序的
  prefix 并生成另一个有效 hash；相邻/非相邻重复 Match ID 不能可靠识别；
  两个不同 Match ID 的 kickoff 相同仍被接受；
- **P2**：文档和永久测试只描述 caller 已排序，没有定义或覆盖 helper 自身
  强制执行的输入契约。

该独立结论为 **P0=0 / P1=1 / P2=1 / `FIX_REQUIRED`**，作为历史保留。本轮
先增加永久测试，旧实现真实得到：
**61 collected / 6 selected / 2 passed / 4 failed / 55 deselected**，
0 skipped/xfail/warnings。失败分别是乱序未拒绝、相邻重复未拒绝、非相邻重复
误报 future，以及 equal kickoff 未拒绝。

现在 helper 自身要求：

- prefix 非空；
- 每个 Match ID 可解析且唯一；
- kickoff 严格递增；
- 乱序、相同 kickoff、相邻或非相邻重复 ID、以及任何晚于最后 current
  feature 的比赛全部以固定安全 `PrototypeDataError` fail closed；
- 不静默排序、去重、丢弃非法行，也不通过 Match ID 为相同 kickoff 强制定序。

canonical parser 的排序不再是唯一防线。合法 canonical input 的稳定字段与
hash 算法不变；本轮没有实现正式 schema、migration、Worker、systemd、API、
frontend、live runner 或真实数据库写入。Prototype 仍不是 production
schema；正式 append-only/versioned mutable per-match source-state snapshot
设计仍是下一模块。Production integration：**NOT STARTED**。

最终有效验收：

- deterministic helper 定向（含保留的 future gate）：
  **61 collected / 7 selected / 7 passed / 54 deselected**；
- prototype 全文件：**61 collected / 61 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition pilots：**286 collected / 286 passed**；
- migrations + contract：**44 collected / 44 passed**。

全部命令均 0 failed/skipped/xfailed；前四项 0 warnings，
migrations + contract 为 1 个既有 `StarletteDeprecationWarning`。独立
canonical probe 对 126 个合法 timeline prefix 逐一比较收口前稳定输入算法与
当前 hash，全部相同；66 calendar / 132 team / 63 non-cancelled /
3 cancelled / 126 rest、Manchester City `NULL / 105 / 90 / 102` 和 AET
保留均不变。cancelled 仍不进入 current/previous load。`/tmp` 隔离
compileall 与 `git diff --check` 均 exit 0。

### 15.7 Safe timestamp error boundary 与四层 schema 文档收口

deterministic helper 收口后的独立复核没有推翻 point-in-time lineage 或
observation ledger，但发现新的 **P1 / `FIX_REQUIRED`**：
`_parse_utc()` 使用
`raise PrototypeDataError(...) from exc`，使带 synthetic marker 的非法时间
可以经格式化 traceback、`__cause__` 和 `__context__` 访问底层
`ValueError`。此前“prototype 错误已经统一安全”的宽泛表述过早，本节明确
撤回；当前结论只限本 prototype 已覆盖的 timestamp 调用路径，不声称仓库全部
异常面都已审计。

永久测试先落盘，旧实现真实 RED：
**76 collected / 15 selected / 1 passed / 14 failed / 61 deselected**，
0 skipped/xfail，1 个 SeleniumBase legacy-hook 启动期
`PytestDeprecationWarning`。攻击矩阵覆盖普通非法时间、绝对路径、proxy
credential URL、Basic/Bearer、token、JSON/body、非法 timezone、非字符串、
Unicode/非法字符，以及 helper 和完整 canonical parser 路径。

修复后的 `_parse_utc()`：

- 对合法 `Z` 和显式 `+00:00` 保持原解析语义；
- naive 和非 UTC offset 继续拒绝；
- 解析失败只返回固定
  `PrototypeDataError("invalid UTC timestamp")`；
- 在底层 parser 的 `except` 已结束后抛出，不保存底层异常对象，不拼原始
  value、field label 或 `str/repr(exc)`；
- 永久测试扫描 `str` / `repr` / `args` / 格式化 traceback /
  cause / context / stdout / stderr / captured log，所有覆盖路径均
  `cause=None`、`context=None`、`suppress_context=True`，synthetic marker
  为 0 finding。

本轮最终有效命令：

- safe timestamp 定向：
  **76 collected / 15 selected / 15 passed / 61 deselected**；
- deterministic helper 定向：
  **76 collected / 7 selected / 7 passed / 69 deselected**；
- prototype 全文件：**76 collected / 76 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition pilots：**286 collected / 286 passed**；
- migrations + contract：**44 collected / 44 passed**。

全部命令均 0 failed/skipped/xfailed。每个独立 pytest 进程各出现 1 次同源
SeleniumBase legacy-hook `PytestDeprecationWarning`；这是同一个第三方启动
warning 在多个进程中重复，不冒充多个产品缺陷。本轮没有出现历史
Starlette warning，也没有使用 warning filter。独立 canonical probe 匹配
全部 126 个合法 input hash，并重算确认 66 calendar / 132 team /
63 non-cancelled / 3 cancelled / 126 rest，Manchester City
`NULL / 105 / 90 / 102`、AET 与 cancelled exclusion 均不变。

正式 production schema 的阻断条件现已明确为
`stable_match_identity`、`append_only_match_state_snapshot`、
`current_match_state_projection`、`versioned_feature_lineage` 四层；
`prototype_schedule_observation` 只是可保留的附加观察账本，不能替代四层。
本轮没有实现正式 schema、migration、Worker、API、frontend、systemd 或任何
网络路径。`/tmp` 隔离 compileall 与 `git diff --check` 均 exit 0。

开工/收尾 round-local 完整性重算确认 branch/HEAD/tag/stash、Git
dirty/untracked pathset、四主库、四个既有 WAL/SHM、canonical fixture、六个
0600 历史 artifact、107 个 `__pycache__` / 808 个 `.pyc`、8 个
`.pytest_cache` 子路径和 40,508 项 worktree pathset 均不变；没有新增仓库
path、真实 DB 写入、cache 清理或历史 pyc 伪恢复。Production integration：
**NOT STARTED**。

## 16. Production schedule-state schema v1 离线设计（2026-07-26）

Prototype 前置阶段已以
`PROTOTYPE_VALIDATED_READY_FOR_PRODUCTION_SCHEMA_DESIGN` 结束；§15.6/15.7
保留的两次 `FIX_REQUIRED` 是已经关闭的历史，不被改写或删除。新 active module
是正式 schema/migration 的纯离线证明，不是 source/Worker/API 接入。

### 16.1 数据库归属与兼容边界

源码与真实 schema 审计确认新层应位于 core / `allwin.db`：

- canonical FotMob `Match_ID`、legacy `dim_match` 和既有模型特征均在 core；
- `dim_match_xref` / `dim_team_xref` / `poll_state` 属于 odds.db 的跨源解析与
  采集节流职责；
- SQLite 无法跨数据库强制 FK。

新增 `backend/migrations/core/0003_schedule_state_v1.sql`。它只创建新对象，
不 `ALTER` / `UPDATE` / 重建 / 回填 legacy `dim_match`，不触碰 odds xref、
poll_state、既有 feature/model 表或其 reader/writer。现有
`INSERT OR REPLACE dim_match` 语义原样保留，尚未切换到新 current projection。

本轮以 `mode=ro&immutable=1` 实际读取四主库 schema，确认一个此前文档与真实
库的冲突：真实 `data/allwin.db` 当前只登记
`core/0001_dim_match_kickoff.sql`，`dim_match` 有 `kickoff_at_utc`，但**没有**
`kickoff_precision` / `kickoff_source`；§6/§7 关于真实库已应用 0002 的历史
描述不准确。按真源优先级，以本节实际 schema 为准。本轮没有对真实库补迁移、
回填或伪造 mtime；永久测试新增“真实 version-1 形状在新 `/tmp` 副本中连续
升级 0002+0003”路径。真实四库均不存在任何 `schedule_*` 表。

### 16.2 五层正式模型

- `schedule_match_identity`：内部 ID +
  `(provider, provider_match_id)`，可选 FK 到现有 canonical
  `dim_match.Match_ID`；不含 kickoff/status/team/competition 等 mutable
  state，UPDATE/DELETE 由 trigger 拒绝，也不允许 silent rebind。
- `schedule_match_state_snapshot`：append-only 单场业务状态，包含 kickoff
  precision、status/finished/cancelled、TBD 可空的主客队、competition/
  season/round/stage、source_updated_at、first_observed_at、ingested_at 和
  provenance。业务 hash 不含观察/写入时间、DB path 或机器状态；同一业务状态
  复用 snapshot，变化追加 snapshot。
- `schedule_observation_event` 保存 provider/source/scope/event-time/
  poll-run/payload 证据；`schedule_match_observation` 把同一事件关联到每个
  match identity/snapshot。一个 observation event 可关联多场；相同事件完全
  skip，later/earlier append，同 event-time 冲突整笔 rollback。
- `current_schedule_match_state` + as-of query：按 observed event time DESC、
  observation ID DESC 确定性投影；ingested/source-updated 时间不决定 current。
- feature 分为 `schedule_rest_lineage_set`、有序
  `schedule_rest_lineage_input` 和最终可消费 `schedule_rest_feature`。
  只有 expected/actual count、连续 ordinal、unique identity/snapshot、严格
  kickoff、无 future input、target finality 全部成立后才能 finalization；
  `schedule_rest_feature_input` 是只展示 finalized lineage 的只读兼容 view。

详细 key、冲突、retention/archive 与 rollback 规则见
`docs/architecture/production-schedule-state-schema.md`。

### 16.3 临时 SQLite 证明

永久测试覆盖：

- fresh、legacy→new、twice；
- partial、wrong same-name object、drifted、weakened constraint；
- mid-migration failure、FK violation、整事务 rollback；
- NS→FT、改期更晚/更早、postponed、cancelled、cancelled→scheduled、
  TBD→concrete、round/stage correction；
- same-state later/earlier observation、不同 state 的 late earlier arrival、
  same-time conflict、repeated poll、forced transaction failure；
- current/as-of、全部 append-only trigger、same-input feature idempotency；
- CWC 66 current / 63 non-cancelled / 3 cancelled / 126 rest，
  Manchester City `NULL / 105 / 90 / 102`、AET 保留、cancelled 不进入
  feature input；
- 最后一场变化只新增最后 lineage；第二场变化新增第二场及 downstream；
  future match 不进入早期 feature lineage。

首次定向实现真实得到 **35 collected / 26 passed / 9 failed**，发现并修正：

1. migration 顶层消息虽固定，底层 SQLite 仍留在 `__context__`；
2. as-of SELECT 未显式暴露 `snapshot_id` alias。

修复并补齐真实 v1 形状升级与 DB trigger 反例后，最终验收为：

- migration/schema target：**39 collected / 11 selected / 11 passed /
  28 deselected**；
- state/observation target：**39 collected / 19 selected / 19 passed /
  20 deselected**；
- current/as-of target：**39 collected / 10 selected / 10 passed /
  29 deselected**；
- lineage target：**39 collected / 7 selected / 7 passed /
  32 deselected**；
- schedule-state 全文件：**39 collected / 39 passed**；
- CWC prototype：**76 collected / 76 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition pilots：**286 collected / 286 passed**；
- existing migrations + contract：**44 collected / 44 passed**。

全部执行均 0 failed/skipped/xfailed。schedule-state 各进程与
migrations+contract 各出现 1 个相同既有
`StarletteDeprecationWarning`；prototype、sealed CWC、team+competition 为
0 warnings。没有使用 warning filter。`/tmp` 隔离 compileall 与
`git diff --check` 均 exit 0。

### 16.4 未开始项

- production FotMob/NowGoal ingestion：**NOT STARTED**
- persistent schedule job / Worker / systemd：**NOT STARTED**
- API / frontend：**NOT STARTED**
- 真实 `data/allwin.db` migration/backfill：**NOT STARTED**
- live/batch request：**NOT STARTED**

CWC fixture 只提供已验证的离线语义样本，不授予 production source 权限；
sealed live/resume runner 未调用、未修改、不得复活。

### 16.5 最终状态与完整性

- Stable identity schema：**VALIDATED**
- Append-only state snapshots：**VALIDATED**
- Observation ledger：**VALIDATED**
- Deterministic current projection：**VALIDATED**
- As-of query：**VALIDATED**
- Versioned rest-feature lineage：**VALIDATED**
- Migration/rollback：**VALIDATED**
- Legacy compatibility：**VALIDATED**
- Integrity：**PASS**

收尾重算确认 branch `main`、HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`、无 exact tag、空 stash 均与
开工基线一致。四主库 SHA-256/size/mtime、既有 allwin/odds WAL/SHM、canonical
fixture、六个 mode 0600 历史 artifact 均逐项一致；真实库仍无
`schedule_*`。仓库 cache 仍为 107 个 `__pycache__`、808 个 `.pyc` 和 9 项
`.pytest_cache` pathset，且所有现存 cache mtime 均早于本模块开工。

排除 `.git` 的 worktree pathset 从 40,508 增至 40,516；精确新增 8 项均为
本模块授权路径（migration、`backend/schedules/` 目录及两文件、永久测试、
architecture 目录及文档、audit 文档）。既有 dirty/untracked 资产未清理、
stash、commit 或伪造恢复。Git status digest 因本模块授权修改而按预期变化，
不是完整性失败。

独立对抗复核前的阶段状态（随后被 §16.6 推翻）：
`PRODUCTION_SCHEDULE_STATE_SCHEMA_V1_VALIDATED_OFFLINE` /
`READY_FOR_INDEPENDENT_SCHEMA_REVIEW`。Production ingestion、Worker/systemd、
API/frontend 与真实数据库 migration/backfill 均为 **NOT STARTED**。

### 16.6 Direct-SQL 独立反例与最终收口

独立复核不采信 §16.3 的自证，实际复现 **P0=0 / P1=5 / P2=1**：

1. migration manifest 只有 `0001`、`0003` 时，runner 仍登记 `1,3`；
2. service 的 `...00Z` 与 `...00.500000Z` 是变长 TEXT，字典序会把较早的
   零微秒值选为 current；直接 SQL 还可插入 `+00:00`、非 UTC offset、naive、
   短小数、尾空格和非法日期；
3. 默认 `recursive_triggers=OFF` 下，`INSERT OR REPLACE` 可绕过原
   UPDATE/DELETE append-only guard；
4. `foreign_keys=OFF` 下，原 scalar comparison trigger 的 `NULL` 三值逻辑
   可放入业务孤儿；
5. 原 `schedule_rest_feature(input_count=2)` 可在实际 input=0 时提交并被读取；
6. ledger filename 与 manifest filename 不同、version/checksum 相同会被接受。

上述结论保留为本轮真实 `FIX_REQUIRED` 历史。先落永久测试、旧实现真实 RED：

- manifest/identity：**19 collected / 5 selected / 0 passed / 5 failed /
  14 deselected**；
- direct-SQL：**56 collected / 17 selected / 0 passed / 17 failed /
  39 deselected**。

每个进程各出现 1 个同源 SeleniumBase legacy-hook
`PytestDeprecationWarning`；没有 warning filter。

收口后的正式契约：

- manifest 必须非空且从 version 1 连续；执行任何 DB 写入前验证；
- migration identity 是精确三元组 `(version, filename, checksum)`；ledger
  中不存在于 manifest 的版本、filename mismatch、checksum drift 均拒绝；
- 所有用于排序/唯一/current/as-of/lineage 的 timestamp 固定为
  `YYYY-MM-DDTHH:MM:SS.ffffffZ`；service 统一规范化，SQLite 独立检查固定
  长度/字符位置/数字/日期 round-trip/时间范围/末尾 `Z`；
- connection 启用并确认 `foreign_keys=ON` 与
  `recursive_triggers=ON`，但不把 PRAGMA 当作唯一防线；
- identity、snapshot、observation event/association、lineage set/input、
  finalized feature 全部有 BEFORE INSERT 主键/自然键冲突 guard；调用方主动
  关闭 recursive triggers 后，显式 PK 与省略 PK 的 REPLACE、UPSERT UPDATE、
  普通 UPDATE/DELETE 仍全部拒绝；
- 关键引用全部用 `NOT EXISTS` guard；调用方主动关闭 foreign keys 后，孤立
  snapshot、observation association、lineage set/input、final feature 仍拒绝；
- feature 使用 lineage set → ordered inputs → finalized feature 单事务；
  incomplete header 可作为未完成 build state 存在，但不可出现在
  `schedule_rest_feature` 或 finalized-input view。第二条 input 强制失败时三层
  均回滚为 0，安全项目异常没有底层 `cause/context`。

14 形态 `/tmp` migration matrix 重新验证：

- fresh 应用 `1,2,3`；真实 0001 catalog 形状应用 `2,3`，legacy 行不变；
- 0001+0002 后只应用 0003；完整第二次为 applied=0；
- partial/complete-but-unregistered 0002、partial/complete-but-unregistered
  0003、weak same-name object、中途故障、checksum drift、ledger filename
  drift 均 fail closed；
- manifest 缺 0002 的 fresh DB 在 DB 文件创建前拒绝；真实 0001 临时形状保持
  ledger=1、无 schedule 对象、legacy 行不变。

最终验收：

- manifest/gap：**20 collected / 8 selected / 8 passed / 12 deselected**；
- timestamp direct-SQL：**75 collected / 17 selected / 17 passed /
  58 deselected**；
- REPLACE/UPSERT：**75 collected / 8 selected / 8 passed /
  67 deselected**；
- FK-off：**75 collected / 5 selected / 5 passed / 70 deselected**；
- feature finalization：**75 collected / 5 selected / 5 passed /
  70 deselected**；
- current/as-of：**75 collected / 8 selected / 8 passed /
  67 deselected**；
- schedule-state 全文件：**75 collected / 75 passed**；
- migrations + contract：**50 collected / 50 passed**；
- CWC prototype：**76 collected / 76 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition：**286 collected / 286 passed**。

全部为 0 failed/skipped/xfailed。每个独立 pytest 进程各出现 1 个同源
SeleniumBase legacy-hook `PytestDeprecationWarning`，不相加冒充多个产品
缺陷。`/tmp` compileall 与 `git diff --check` 通过。

当前模块状态：
`PRODUCTION_SCHEDULE_STATE_SCHEMA_V1_DIRECT_SQL_VALIDATED` /
`READY_FOR_FINAL_INDEPENDENT_SCHEMA_RE_REVIEW`。

Production ingestion / Worker / systemd / API / frontend / 真实数据库
migration/backfill：全部 **NOT STARTED**。真实 `allwin.db` 仍只登记 0001，
本轮没有执行真实 migration。

Direct-SQL closure 的 round-local 收尾重算确认：branch `main`、HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`、无 exact tag、空 stash 不变；
四主库及四个既有 WAL/SHM 的 SHA-256/size/mtime_ns、canonical fixture、
六个 mode 0600 历史 artifact 均与开工基线完全一致。仓库 cache 仍为
107 个 `__pycache__`、808 个 `.pyc`；`.pytest_cache` 仍为 8 个 descendants
（连同根目录共 9 个 path），内容与 metadata digest 均未变化。排除 `.git`
的 worktree pathset 开工/收尾均为 40,516 项，digest 相同；untracked 仍为
47 项且 digest 相同。Git status digest 只因本轮授权的 tracked migration
runner 与永久测试进入 modified 状态而变化；既有 dirty/untracked 用户资产
没有被清理、覆盖、stash 或伪造恢复。

## 17. Temporary production-DB copy migration/import trial（2026-07-26）

本轮未迁移真实库。真实 `data/allwin.db` 的 WAL 为 0 bytes，普通文件复制前后
主库 SHA/size/mtime/inode 以及既有 WAL/SHM 均稳定。新建 `/tmp` mode 0600
副本与源 SHA
`92a6a39c40dfb21f9dacfe6a8e8953f6b0a971ebb5b40a6ae9f253ad00ab364e`
完全一致，`integrity_check=ok`，ledger 只有 0001，`dim_match=11115`。

当前正式 runner 在副本上严格应用 0002、0003，最终 ledger 为 1/2/3，42 个
reviewed schedule 对象齐全，FK 与 integrity 均通过。18 张 legacy 表的所有
既有列经过 typed deterministic digest 前后比较均一致；`dim_match` 仅新增
11,115 个 `date_only` precision，`kickoff_source` 全部为 NULL。第二次 runner
为 applied=0，副本 SHA/size/mtime 全部不变。

独立 fault manifest 让 0002 提交后在临时 0003 尾部失败：0003 ledger、全部
schedule 对象和 fault object 都回滚为 0，legacy digest 不变。未迁移 recovery
image 与 pre-copy SHA 完全一致；恢复后 ledger 回到 0001，再运行正式 runner
仍 applied=2、integrity/FK 通过。

历史身份审计确认 11,115 个 `Match_ID` 全部非 NULL、唯一、正整数，competition
与 team legacy reference 均完整；仓库 FotMob ingestion 源码证明其表级 provider
provenance。正式 service 新增窄入口 `record_match_identity()`，只写 immutable
identity 而不伪造 state。首轮插入 11,115（1.154s），重跑全部 skip（0.182s），
canonical binding 无冲突，`dim_match` 未改变。

历史 state snapshot eligibility 明确为 0：`kickoff_at_utc` 全空，11,115 个
`Date` 只能证明自然日，`Finish`/`NotStarted` 不能独立证明正式
finished/cancelled，且没有可信 observation time。没有用午夜补 exact kickoff，
没有编造 source update/observation，也没有从行存在推断 finished。全部 11,115
场在 state 层仍需要重新采集。

CWC canonical fixture 仅离线导入副本。由于 fixture 不含可信 observation time，
event source 明确为 `trial_synthetic_observation:canonical_fixture`，
`source_updated_at=NULL`。首轮插入 66 identity / 66 snapshot / 66 association /
126 finalized feature；相同 event 全 skip；晚 5 分钟相同内容仅新增第二个 event
和 66 association。最终 current 66、non-cancelled 63、cancelled 3、AET 3；
formal snapshot 的 home/away 字段表达 132 个来源 team relationship，不另造表；
Manchester City 为 `NULL / 105 / 90 / 102`，cancelled 不进入 feature target
或 lineage input。Current 与 T0 as-of 选择不同 observation evidence、同一
immutable business snapshot；identity/as-of 查询使用既有唯一/index path。

真实验收：

- trial tool + identity target：**7 collected / 7 passed**；
- schedule-state：**76 collected / 76 passed**；
- CWC prototype：**76 collected / 76 passed**；
- sealed CWC pilot：**41 collected / 41 passed**；
- team + competition：**286 collected / 286 passed**；
- migrations + contract：**50 collected / 50 passed**。

全部为 0 failed/skipped/xfailed/warnings（`-W default`，无 warning filter）。
`/tmp` compileall 与 `git diff --check` exit 0。详细 manifest、18 表 digest、
rollback/recovery、性能与 query-plan 证据见
`docs/audits/schedule-state-temp-copy-migration-trial.md`。

当前结论：

- `TEMP_PRODUCTION_DB_COPY_MIGRATION_VALIDATED`
- `HISTORICAL_IDENTITY_BACKFILL_VALIDATED`
- `CWC_OFFLINE_FORMAL_SCHEMA_IMPORT_VALIDATED`
- `READY_FOR_INDEPENDENT_TEMP_COPY_REVIEW`

真实数据库 migration/backfill、production ingestion、persistent poll job、
Worker/systemd、API/frontend 均为 **NOT STARTED**；本轮无网络请求，未读取凭证，
未执行 sealed live/resume。

### 17.1 Temp-copy safety 与 identity cross-run closure

§17 的初版结论随后被独立复核以
**P0=0 / P1=3 / P2=2 / `FIX_REQUIRED`** 推翻，历史不删除：

1. 原 `migrate_exact_copy(path)` 可写任意通过宽松 `/tmp` 检查的既有路径，
   没有证明 exclusive creation、single-link、source WAL 与跨阶段 inode/SHA
   稳定；
2. identity 的 `created_at` 被错误纳入既有 row equality，旧证明两次都传 T0，
   只覆盖 same-T0 immediate replay；
3. provider/provider-match-ID 缺少统一 canonicalization；
4. 永久测试缺少 hardlink/symlink、existing target、WAL、mutation/replacement、
   T±1 与非法 identifier 反例；
5. 文档把 same-T0 结果过宽写成长周期幂等。

先补永久反例并在旧实现上真实得到：
**135 collected / 55 selected / 2 passed / 53 failed / 80 deselected**，
0 skipped/xfail/warnings。

修复后的单一入口为
`prepare_trial_copy(source) -> PreparedTrialCopy ->
migrate_prepared_trial_copy(handle)`。public migration 不再接受裸 path。工具在
resolved system temp root 下创建当前 UID、mode `0700` 的新 run directory，
以 exclusive/no-follow 语义创建 mode `0600`、`st_nlink=1` 的 destination
与 recovery。Prepared handle 绑定 creator PID/UID、run-directory
device/inode、source main/WAL/SHM fingerprint 以及 destination/recovery
device/inode/SHA；写 migration 前全部重验。symlink、hardlink、既有普通文件
或 SQLite、world-writable custom parent、非普通文件、非空 source WAL、
复制中/复制后 source mutation、run-dir/destination replacement、mode/link/
owner drift 均 fail closed；不 checkpoint、不覆盖调用方文件。

`created_at` 现定义为 identity 首次成功提交的 provenance，first-write-wins，
不参与既有 immutable identity equality，不 UPDATE。新 exact-copy 首轮 T0
插入 11,115；关闭并重开连接后 T0+1 day 重放为
**0 inserted / 11,115 skipped / 0 conflict**，所有 row 仍保留原 T0。
provider `" FOTMOB "` 与 zero-padded numeric ID 形式再次重放仍全 skip；
canonical binding mutation 仍显式 conflict。identity 总数 11,115，
`dim_match=11115`，snapshot/observation=0。

Provider 经 NFKC、trim、lower 后必须是最长 32 的 ASCII slug：
`[a-z][a-z0-9_-]*`。Provider match ID 只接受正的 non-bool integer 或最长
128 的受支持 ASCII string；numeric string 去 leading zero 并必须 >0。
list/dict/float/bool/null、空白、path/control/unsupported Unicode 均拒绝，
不再任意 `str(object)`。0003 的 direct-SQL CHECK 同步收紧；新 checksum 为
`7e69e2a15b469ed9286345c0e21ebe49efdeb09561b2e2075bc0222dce050b57`。

在新 prepared exact copy 上重新应用 0002/0003，legacy 18 表 digest、
`integrity_check`、FK、42 个对象、second-run applied=0 均通过。故障 0003
保持 ledger=1/2 且 schedule objects=0；未迁移 recovery image SHA 与源一致，
再 prepared-copy 恢复后 applied=2。CWC 回归仍为 66 snapshot、2 events、
132 associations、126 finalized features、334 lineage inputs；
Manchester City `NULL/105/90/102`，cancelled 不进入 feature，
`source_updated_at=NULL`。

最终 `-W default` 验收：

- copy safety：**19 collected / 12 selected / 12 passed / 7 deselected**，
  0 warnings；
- replay/normalization：**139 collected / 48 selected / 48 passed /
  91 deselected**，1 `StarletteDeprecationWarning`；
- trial full：**19 collected / 19 passed**，0 warnings；
- schedule-state：**120 collected / 120 passed**，
  1 `StarletteDeprecationWarning`；
- manifest/migration：**20 collected / 20 selected / 20 passed**，
  1 `StarletteDeprecationWarning`；
- migrations + contract：**50 collected / 50 passed**，
  1 `StarletteDeprecationWarning`；
- CWC prototype：**76/76**；sealed CWC：**41/41**；
  team + competition：**286/286**；以上三类均 0 warnings。

全部为 0 failed/skipped/xfailed。warning 是各独立 pytest 进程各报告一次的
同一既有 Starlette/httpx deprecation；没有抑制或相加冒充多个缺陷。
`/tmp` compileall 与 `git diff --check` exit 0。

当前状态：

- `TEMP_COPY_TRIAL_SAFETY_VALIDATED`
- `HISTORICAL_IDENTITY_CROSS_RUN_IDEMPOTENCY_VALIDATED`
- round-local integrity：**FAIL**
- `READY_FOR_FINAL_TEMP_COPY_RE_REVIEW`: **NO**

收尾时 branch/HEAD/tag/stash、Git status/untracked set、四主库、allwin/odds
既有 WAL/SHM、canonical fixture、六个 mode `0600` artifact 和
`.pytest_cache` 均与开工基线一致。但 04:35:38 基线后，早期 RED 命令在
04:36:03 新建了
`analysis/cwc_production_integration_design/__pycache__/` 及两个 `.pyc`。
cache 数从 107/808 变为 108/810，worktree pathset 从 41,797 变为 41,800，
精确新增路径只有该目录与两个 pyc。本轮按禁令没有删除、touch 或伪造恢复，
因此行为门禁通过但 round integrity 必须判 FAIL；是否处理这些生成 cache
由用户决定。§17.2 不处理这些路径，只从其真实存在的状态建立新基线。

Production ingestion / Worker / systemd / API / frontend / 真实 migration：
**NOT STARTED**。

### 17.1b Offline schedule shadow ingestion v1

初版 offline shadow 曾给出 validated 标签，但后续独立复核真实确认
**P0=0 / P1=5 / P2=2 / `FIX_REQUIRED`**：default JSON duplicate-key
last-write-wins、pagination/completeness 由 envelope 自证、status flags 没有精确
matrix、`COMPLETED` 直接信任 manifest.result、session/recovery 依赖 creator
PID 与进程内 `_SESSIONS`，并且 artifact type/size 与并发边界不完整。该历史不
删除、不改写成“当时已通过”。

本次 `SHADOW_ARTIFACT_TRUST_AND_DURABLE_RECOVERY_CLOSURE` 先新增永久 RED；
旧实现真实得到 **43 collected / 43 selected / 17 passed / 26 failed**，
0 skipped/xfailed。修复后的正式链路改为：

```text
immutable raw-provider projection
→ same-FD SHA-before-strict-JSON
→ shared raw pagination inspector
→ exact status/flag normalization
→ atomic identity/snapshot/observation
→ DB-derived current/as-of + feature/lineage truth
→ signed manifest hint + signed durable descriptor
→ cross-process recovery under exclusive flock
```

artifact 必须是当前 UID、single-link、非 group/world writable 的 regular file，
拒绝 symlink/hardlink，固定上限 16 MiB；同一 FD 的相同 bytes 同时用于 SHA 与
parse，前后 fstat 的 inode/size/mtime 等必须稳定。strict parser 拒绝任意层级
duplicate key（同值也拒绝）、NaN/±Infinity、BOM、非法 UTF-8、trailing
data、顶层非 object 和超结构预算；固定异常不保留 raw payload、路径、URL、
marker、cause/context 或系统异常文本。

正式 shadow fixture 改为保存 raw response 的脱敏 projection：

- `tests/fixtures/fotmob/cwc_2025_competition_schedule_raw.json`
- projection SHA：
  `b2852c04cdcfd812a92164e482309cdca634c3a38dca4973adcf94a3ebbc67fc`
- 已验证 source artifact SHA：
  `6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`

canonical fixture 保持不变，但不再单独证明 raw completeness。
`backend/schedules/pagination.py` 成为 competition pilot 与 shadow 共用的纯
backend 单一真源；raw payload 的 DETECTED、UNRESOLVED、collision、orphan、
malformed 或多方言 conflict 均拒绝，envelope 的 `NOT_DETECTED` 不能覆盖它。
competition ID/name、season、non-empty fixtures、count、Match/team ID 和 schema
也从 raw 独立交叉验证。

FotMob 精确支持 matrix：

- `NS = false/false/false`
- `FT/AET/Pen = true/true/false`
- `Can = false/false/true`

started/finished/cancelled 必须是真 bool；未知、unsupported live 或任何矛盾组合
拒绝整个 artifact。

manifest 现在只是签名恢复提示。每次调用（包括 `COMPLETED`）都纯查询重建
`NO_STATE / STATE_COMPLETE / FEATURES_COMPLETE /
PARTIAL_OR_CONFLICTING`：核对精确 identity、state content hash、snapshot
fields/provenance、run event、association，以及每个 feature 的 team/target/
version/input-set hash、ordered identity/snapshot inputs、value/payload hash 和
provenance。manifest 落后于已 commit DB 时向前 reconcile；manifest 领先、
partial 或 conflict 时 fail closed。返回 summary 来自 DB 重新验证的
identity/snapshot/event/association/feature/lineage-input 数量；manifest 中的
insert/skip 只保留执行审计。伪造 completed+空 DB 和已完成 result tamper 均有
永久反例。

phase 只允许：

```text
NEW → ARTIFACT_VALIDATED → STATE_APPLIED → FEATURES_APPLIED → COMPLETED
```

FAILED 只可从未完成 phase 进入，retry 回到 last successful phase。全部 phase
pair 都有 allow/deny 永久测试。manifest strict JSON、signature、mode 0600、
single-link、exclusive temp、file fsync、atomic replace、directory fsync、
symlink/hardlink/unknown/corruption rejection 均已固化。

`PreparedTrialCopy` 仍只在创建进程中消费一次；随后随机 256-bit capability 与
签名 durable descriptor 绑定 0700 workspace、session/run path、source、
destination/recovery main、完整 basename companion pathset、migration
schema/ledger 和所有 signed manifests。creator PID 只是签名 provenance，
不是 reopen gate；`_SESSIONS` 只是 cache。signed DB redirection、wrong nonce、
descriptor corruption、unknown workspace/companion、committed WAL、journal、
schema/source/recovery drift 均 fail closed。SQLite 正常 close/reopen 可移除或
重建 zero-byte WAL/private SHM，但 committed non-empty WAL 永不 rebind。

真实 subprocess 永久测试分别在 state commit/manifest 前、feature
commit/manifest 前终止 creator；新 Python 进程可 reopen 并从 DB truth 完成，
不重复 snapshot/feature。session 全程使用非阻塞跨进程 exclusive `flock`；
第二进程（即使不同 run ID）固定拒绝且零部分写。明确：
`CONCURRENT_SHADOW_RUNS: UNSUPPORTED_BY_DESIGN`，crash-safe 不等于
concurrency-safe。

atomic batch、exact/later/out-of-order observation、absence、changed-state
versioning、feature retry、PIT lineage 和 CWC 三轮业务结果保持：66
identity/snapshot，63 non-cancelled + 3 cancelled，126 feature，334 lineage
inputs，Manchester City `NULL/105/90/102`，AET 保留，cancelled 不进入
feature target/input。

最终有效验收：

- closure 定向门禁分别为 **16/16、11/11、7/7、3/3、44/44、
  7/7、1/1、4/4 passed**；
- shadow 两个永久测试文件联合 **132 collected / 132 selected /
  132 passed**；
- competition pagination 全文件 **193/193 passed**；
- schedule-state **123/123 passed**，migrations + contract **50/50 passed**，
  CWC prototype **76/76 passed**，network hard block 定向 **1/1 passed**；
- 全部 pytest 为 0 failed / 0 skipped / 0 xfailed；仅 schedule-state 与
  migrations+contract 各报告 1 条相同的既有 Starlette/httpx deprecation
  warning，其余命令 0 warnings；
- `/tmp` 隔离 compileall、tracked/untracked whitespace check 与
  `git diff --check` 均通过。

结束核对确认 branch/HEAD/tag/stash、四主库与 WAL/SHM、canonical fixture、
六个历史 artifact、109 个 `__pycache__`、812 个 `.pyc`、cache path/content
digest、`.pytest_cache` 和 ignored worktree pathset 均相对本轮基线不变；新增
path 仅属于本模块授权文件。结论为 **P0=0 / P1=0**：

- `SHADOW_ARTIFACT_TRUST_BOUNDARY_VALIDATED`
- `DURABLE_CROSS_PROCESS_SHADOW_RECOVERY_VALIDATED`
- `READY_FOR_FINAL_SHADOW_RE_REVIEW`
- `CURRENT_SHADOW_INGESTION_INTEGRITY: PASS`

永久保留：

- `HISTORICAL_PYCACHE_INTEGRITY_EVENTS: FAIL (2)`
- Historical state backfill：**BLOCKED**
- `PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED`
- Production network ingestion / Worker / systemd / API / frontend /
  real migration：**NOT STARTED**

### 17.2 Destination SQLite sidecar binding closure

§17.1 之后的最终独立只读复核没有推翻 identity、normalization、0003、legacy
或 CWC 结论，但以 **P0=0 / P1=1 / P2=2 / `FIX_REQUIRED`** 推翻了“destination
exact-copy 已完整绑定”的过宽表述。反例在已准备的 destination 上启用 WAL、
关闭 auto-checkpoint 并提交普通业务值 mutation；destination 主文件的
SHA/inode/mode/nlink 全部不变，新的只读连接却能看到 WAL 中的逻辑变化，而旧
`migrate_prepared_trial_copy()` 仍接受 handle 并开始 migration。P2 分别是永久
测试没有 destination WAL mutation，以及文档只描述 main-file fingerprint
却宣称 exact-copy safety 已完整验证。

先增加永久反例，旧实现真实得到
**28 collected / 9 selected / 9 failed / 19 deselected**，0
skipped/xfailed/warnings。除真实 committed WAL mutation 外，RED 还覆盖：
晚出现的 zero-byte destination WAL、destination SHM replacement、rollback
journal、sidecar symlink/hardlink、recovery-image WAL、read-only integrity
后最后一刻出现的 sidecar，以及 migration 返回前遗留的 non-empty WAL。

窄幅修复为 `PreparedTrialCopy` 新增 destination/recovery
`SQLiteSidecarSetFingerprint`。受信状态现在是 main DB fingerprint 加完整的
已知 `-wal`/`-shm`/`-journal` set/fingerprint。任何已存在 sidecar 都必须为
当前 UID、mode `0600`、`st_nlink=1` 的普通文件；WAL 必须为 0 bytes，
rollback journal 必须不存在。prepare 在只读 integrity 后记录集合；migration
gate 在 integrity 前比较完整 fingerprint，在 integrity 后再次比较集合、inode、
owner、mode、link count、size、SHA，并做最后一次 exact recheck。SQLite
只读 WAL lock 可以只推进 SHM mtime，因此该特定 post-integrity 比较只允许
SHM mtime 变化，不允许 SHM identity/size/content 变化。正常已绑定的 zero-byte
WAL + private SHM 控制组仍可迁移。public API 返回成功前拒绝 non-empty WAL
或 rollback journal，并确认 recovery main/sidecars 未变。

本轮 `-W default` 实际回归：

- sidecar 定向：**29 collected / 10 selected / 10 passed /
  19 deselected**；
- trial 全文件：**29 collected / 29 passed**；
- schedule-state schema：**120 collected / 120 passed**；
- migrations + contract：**50 collected / 50 passed**；
- CWC prototype：**76 collected / 76 passed**。

全部 0 failed/skipped/xfailed/warnings。该修复没有改 identity、normalization、
migration SQL、legacy 或 CWC 业务实现，没有运行真实 migration、sealed runner、
endpoint、Worker、API 或 frontend。

前一施工轮产生一个 repository `__pycache__` 和两个 `.pyc` 的
round-local integrity **FAIL** 永久保留；本轮不删除、touch 或伪造恢复这些
路径，而是从包含它们的当前真实状态建立独立新基线。本轮最终完整性结果见本节
后续收尾记录。

收尾重算确认 branch `main`、HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`、无 exact tag、空 stash、
Git status/untracked set 与本轮开工基线一致。四主库及既有 allwin/odds
WAL/SHM、canonical fixture、六个历史 mode `0600` artifact 的
SHA/size/mtime_ns/inode/mode 全部不变。repository cache 仍为
108 个 `__pycache__`、810 个 `.pyc`，内容和 metadata 均不变；
`.pytest_cache` 仍为 9 paths。完整 pathset 41,822、排除 `.git` 后 40,524，
均与本轮开始相同；untracked 仍为 51。故本轮 sidecar closure
round-local Integrity = **PASS**，同时前一轮 pycache Integrity = **FAIL**
继续永久保留。`READY_FOR_FINAL_TEMP_COPY_RE_REVIEW = YES`。

### 17.3 Unknown SQLite companion pathset closure

最终窄幅复核保留 §17.2 的 committed-WAL 门禁，但发现固定枚举
`-wal`/`-shm`/`-journal` 仍不是完整的 workspace 路径边界。历史结论为
**P0=0 / P1=1 / P2=2 / `FIX_REQUIRED`**：destination/recovery basename
下的 `-wal2`、super-journal 形状、`-journal.extra`、任意 suffix 以及
symlink/hardlink/directory/FIFO 均可被旧实现忽略并进入 runner；永久
unknown-sidecar 反例缺失；文档还没有准确区分真实 SQLite lifecycle 和不存在的
public restore atomicity。该历史保留，不重开 identity、normalization、0003、
legacy 或 CWC 结论。

先落永久测试，旧实现真实结果为 **55 collected / 26 selected / 24 failed /
2 passed / 29 deselected**，0 skipped/xfailed/warnings。destination 与
recovery 使用相同 unknown/non-regular 矩阵；另有只读 integrity 后最后一刻
出现 unknown companion、runner 后遗留 unknown companion、recovery journal
与 SHM drift。旧实现放行前 24 项，只有原有 recovery journal/SHM drift 两项
通过。

窄幅修复没有改 schema SQL 或 migration 业务逻辑。`PreparedTrialCopy` 现在
分别保存 destination/recovery 的完整允许 companion-name pathset 和已知
sidecar fingerprint。工具对独占 mode `0700` workspace 执行 basename 前缀
`scandir`；除精确 `-wal`、`-shm`、`-journal` 名称外，任何其它目录项均在
读取内容或跟随 symlink 前以固定安全异常拒绝。unknown 文件不会被删除、
truncate、rename 或自动加入 allowlist。已知 sidecar 仍须当前 UID、mode
`0600`、single-link regular，WAL 只能 0 bytes，journal 必须不存在。最终
pre-open scan 后没有 callback/wait；首次 schema apply 关闭 connection 后先
重绑精确已知 pathset 再进入内部 no-op apply，成功返回前再次重绑最终 pathset
并验证独立 basename 的 recovery pathset 未变。

committed-WAL 永久测试保持写连接与未 checkpoint WAL，直接证明 runner=0；
WAL 的 device/inode/owner/mode/nlink/size/SHA/mtime_ns 全部不变；destination
main identity 不变；ledger 只有 0001；schedule objects=0；WAL 中普通业务
mutation 仍可被逻辑读取且没有被 migration 消费。正常 bound zero-byte
WAL/private SHM 控制组继续通过。

真实 `/tmp` lifecycle 证据按观察值而不是永久 inode 不变量记录：migration
前 WAL=0、SHM=32768 bytes；migration 中 WAL 峰值约 181312 bytes；
connection 关闭后 WAL=0 且 journal 不存在；WAL/SHM inode 可能由 SQLite
重建；main 包含 ledger 1/2/3 与 schedule schema；`integrity_check=ok`；
FK findings=0；内部 second apply=0。稳定契约是工具拥有 lifecycle、逻辑提交
进入 main、返回时没有 non-empty WAL/journal、最终 pathset 重新绑定。

Recovery 已验证范围仅为 image fingerprint/sidecar/pathset 安全、历史人工
`/tmp` 文件恢复演练及 clean recovery image 重新 migration。仓库不存在
public restore API，因此明确保留：
**`PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED`**。不得据此声称 public
restore 失败无部分覆盖或真实 production restore runbook 已验证。

本轮 `-W default` 行为回归：

- unknown companion：**55 collected / 26 selected / 26 passed /
  29 deselected**；
- committed WAL：**55 collected / 1 selected / 1 passed /
  54 deselected**；
- known sidecar/companion：**55 collected / 34 selected / 34 passed /
  21 deselected**；
- trial 全文件：**55 collected / 55 passed**；
- schedule-state schema：**120 collected / 120 passed**；
- migrations + contract：**50 collected / 50 passed**；
- CWC prototype：**76 collected / 76 passed**。

以上均为 0 failed/skipped/xfailed/warnings；`/tmp`
`PYTHONPYCACHEPREFIX` compileall 与 `git diff --check` 的最终状态见收尾报告。
未读取凭证、未联网、未运行真实 migration、endpoint、sealed runner、Worker、
systemd、API 或 frontend。Production integration 继续 **NOT STARTED**。

完整性必须分开记录。§17.1 的历史 cache 事件永久 **FAIL**，§17.2 以其后
108/810 状态建立的独立轮次为 **PASS**。本 §17.3 执行中一次错误的直接
`py_compile` 又创建
`analysis/schedule_state_migration_trial/__pycache__/` 与两个 `.pyc`，
使本轮基线 108/810 变为 109/812；没有删除、touch 或伪造恢复。因此本轮自身
Integrity = **FAIL**，产品行为门禁通过也不能将其写成 PASS，
`READY_FOR_FINAL_UNKNOWN_SIDECAR_REVIEW = NO (INTEGRITY)`。完整 pathset
41,834→41,837、排除 `.git` 后 40,524→40,527，差异仅为该目录和两文件；
全部既有 cache 内容/metadata 不变。Git status/untracked、四主库及既有
WAL/SHM、canonical fixture、六个 artifacts 与 `.pytest_cache` 均匹配基线。

## 18. Single-competition live-shadow acquisition 离线设计（2026-07-28）

在已经独立验证的 offline schedule shadow 与 temporary-copy 边界之外新增
独立 acquisition 层；没有修改或复制 strict JSON、raw pagination、FotMob
status matrix、schedule-state transaction、feature lineage 或 shadow recovery
实现。本模块网络请求数为 **0**，没有 live transport，也没有接入 Worker、
systemd、API、frontend 或 production migration。

### 18.1 瑞典超 Phase-0 只读审计

仓库存在 worker job 和 `deploy/systemd/allwin-poll.timer/service`，这只证明
有代码与可部署配置。本机没有相关 Python 进程、launchd/cron job 或 Docker
container；systemd runtime 不存在。immutable 读取真实库得到：core 中
League_ID 67 为 0 行，odds 的 Bronze/Silver/xref/poll_state/source_health
均 0 行。platform job ledger 只有 2026-07-19 一次 NowGoal success 和一次
FotMob failed。现存 `/tmp/allwin-allsvenskan-*` data 目录为空，日志结束于
2026-07-21 的本地隔离实验。

因此不能证明采集器持续运行；真实 odds DB 中也没有 2026-07-25/26 周末快照。
仓库文本中的 ID 67 缺少仍存的 raw/实验 DB 证据，且本轮禁止联网，故该
competition/provider ID 为 **UNVERIFIED IN THIS REVIEW**，不作猜测。

### 18.2 Durable budget、artifact 与恢复

`AcquisitionConfig` 明确绑定单 provider、competition ID/name、season、
operation allowlist、budget、fixture count、competition class 与 artifact
schema。private SQLite control ledger 持久记录 run/request ID、competition、
season、operation、attempt ordinal、budget、intent time、dispatch/receipt
state、response/artifact SHA+size 和 terminal outcome。

每次可能的底层 transport 调用前先 commit intent 与 `DISPATCH_STARTED`；
retry 逐次消耗 budget。allowlist 不匹配和 budget exhausted 在 transport 前
拒绝。可能已 dispatch 但未持久收到 response 的边界固定成为
`OUTCOME_UNKNOWN`，重启后零 transport 自动重试，必须由未来人工流程处置。
没有 check_ip、daily discovery、proxy probe 或 health call。

Fake response 使用 16 MiB 上限、exclusive mode 0600 staging、file fsync、
atomic rename、parent fsync，并在 attempt/artifact ledger 双重绑定 SHA/size。
最终 artifact 必须为 current-UID regular single-link mode 0600 文件。
rename/ledger 间 crash 只在 final fingerprint 精确匹配时恢复；tamper 在业务
apply 前拒绝；completed replay 也重新验证 artifact 与 DB truth。

之后只调用既有 `load_artifact_envelope()`、pagination inspector、status/
normalization 和 `run_shadow_ingestion()`。唯一 DB apply 目标是
`PreparedTrialCopy` 管理的 `/tmp` migration copy；raw path 拒绝。

真实 subprocess 永久测试覆盖七个边界：intent/transport 前、transport/receipt
前、receipt/rename 前、rename/ledger 前、validation/apply 前、state
commit/manifest 前、feature commit/final manifest 前。第二进程被 acquisition
exclusive `flock` 在 transport 前拒绝。仍明确：
`CONCURRENT_SHADOW_RUNS: UNSUPPORTED_BY_DESIGN`。

### 18.3 业务与回归结果

acquisition 入口保留 exact replay、later/out-of-order observation、
changed-state versioning、absence fail-closed、feature retry 和 PIT lineage。
CWC raw fixture 的正式 handoff 得到 66 identity/snapshot、126 features、
334 lineage inputs；同内容 later observation 不重复 snapshot/feature，
单场 kickoff 变化新增 immutable snapshot 与相关新 feature version，旧行保留。

永久 RED 在实现文件不存在时真实得到 collection import error（无测试执行）。
最终实跑：

- acquisition design：**26 collected / 26 selected / 26 passed**；
- offline shadow 两文件：**132 / 132 / 132 passed**；
- migration trial：**55 / 55 / 55 passed**；
- schedule-state + migrations + contract：**173 / 173 / 173 passed**；
- sealed CWC pilot + prototype：**117 / 117 / 117 passed**。

全部 0 failed/skipped/xfailed。schedule-state + migrations + contract 有 1 条
既有 Starlette/httpx deprecation warning，其余命令 0 warnings；没有 warning
filter、skip、xfail、live client、transaction mock 或 fixture 改写。

当前正式标签：

- `SINGLE_COMPETITION_LIVE_SHADOW_DESIGN_VALIDATED_OFFLINE`
- `DURABLE_NETWORK_REQUEST_BUDGET_DESIGN_VALIDATED`
- `READY_FOR_EXPLICIT_SINGLE_COMPETITION_NETWORK_AUTHORIZATION`
- `CURRENT_MODULE_INTEGRITY: PASS`

这些标签不表示 live ingestion 已验证。live transport 与 target competition
仍 **NOT AUTHORIZED**，未来 request budget 必须由用户另行明确授权。永久保留：

- `HISTORICAL_PYCACHE_INTEGRITY_EVENTS: FAIL (2)`
- Historical state backfill：**BLOCKED**
- `PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED`
- Production network ingestion / Worker / systemd / API / frontend / real
  migration：**NOT STARTED**

完整实现与边界见
`docs/audits/single-competition-live-shadow-design.md`。

收尾完整性重算：branch `main`、HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`、无 exact tag、空 stash 和
既有 Git status digest 均不变。排除本模块 4 个授权新增文件后，untracked
NUL pathset digest 仍为
`7e7284abead0656ec74c3ef15ee4bc1541a81a57fcef5786ade76875b36176e3`；
排除授权新路径后的 worktree pathset 仍为 40,536 entries、digest
`dc18501b4d263109b060ded0ec01ae5070fc375ffb3461d2b45db152aaaa1ac5`。
四主库与既有 WAL/SHM、canonical/raw fixture、六个历史 artifacts 的
SHA/size/mtime/inode/mode 均匹配开工基线。repository cache 保持 109 个
`__pycache__`、812 个 `.pyc`，path/content/metadata digest 全部不变；
`.pytest_cache` 仍为 9 paths 且 path/content digest 不变，ignored pathset
也不变。因此本模块 current integrity 为 **PASS**；这不改写两次历史
pycache **FAIL**。

## 19. Active-league real content MVP（2026-07-28）

本轮获得真实联网授权，并在 8/50 次底层 transport 尝试内验证三个候选。
FotMob 67 为 Allsvenskan 2026，但最近未来比赛不在 72 小时内；FotMob 59
为 Eliteserien 2026，FotMob 130 为 MLS 2026，二者同样没有 72 小时内比赛。
因此没有伪造未来比赛，也没有输出 DONE 标签。按候选顺序选择最接近的挪威超
继续验证纵切：FotMob 5104968 与 NowGoal/Titan 2912857 的主客方向一致，
开球均为 `2026-07-31T17:00:00Z`，时间差 0 秒。

真实 artifact 仅保存在私有 `/tmp` 目录，真实 `data/*.db` 未写入。隔离副本
落入 240 场赛程、80 条积分榜、两队各最近 8 场和 6 条真实赔率记录。
Bet365 最新 1X2 为 1.65/3.90/4.50，去水结果
55.8739%/23.6390%/20.4871%，严格标记 `MARKET_BASELINE`。匿名 API 只下发
最高一项，另外两项不在响应或页面 payload 中。

production build 在 `http://127.0.0.1:3300` 完成本地浏览器验收：首页、详情、
精确开球时间、中性公众号 CTA、Studio 及 1080×1920/1080×1350 PNG、
JSON、SRT 均通过真实样板比赛验证。PNG 签名与 IHDR 尺寸已独立核对；中文
口播时间轴为 74.9 秒。`scripts/daily_content.sh` 能幂等重放已保存 artifact
并刷新隔离数据和内容，但尚未自行执行每日新鲜 provider acquisition。
定向 backend/API/Studio/cache/contract 回归最终为 112 collected / 112 passed，
0 failed/skipped/xfailed，1 条既有 Starlette deprecation warning；frontend
typecheck、production build、browser bundle localhost 检查和
`git diff --check` 通过。

因此当前是“真实纵切可运行但业务 closure 未完成”：缺少真实 72 小时候选、
自动化 fresh sync、服务器调度/监控和 AWS 部署。完整证据与边界见
`docs/audits/active-league-content-mvp.md`。

### 19.1 Fresh daily content pipeline（2026-07-28）

产品口径已修正：网站与 Studio 的真实内容候选窗口为未来 7 天，赔率高频窗口
仍独立保持 T-72h 至开球；“未来 72 小时没有比赛”不再构成内容纵切失败。
Eliteserien 已成为正式 league config：FotMob competition 59、返回名称
`Eliteserien`、season `2026`。Allsvenskan 与 MLS 已配置但 fresh promotion
默认关闭，新增联赛不复制整条 pipeline，也不会在未完成 identity/mapping
门禁时猜测继续。active-league assembler 已改为显式接收本轮选择的
league/match/team context，不再在 apply 阶段写死 5104968；有 reviewed alias
时优先使用，没有时用 live candidate 的实际队名做方向匹配，因此样板开球后
可以继续选择同联赛下一场。

`scripts/daily_content.sh` 现在有三个互斥入口：

- `--fresh`：真实获取赛程、积分榜、样板详情、两队数据、到期 NowGoal
  日程/赔率，保存不可变 raw/validated artifact，应用隔离 runtime DB，
  构建 analysis bundle 与 Studio 内容源；
- `--replay`：校验 last-success manifest 和 SHA 后完全离线重放；
- `--due --dry-run`：只读判断赔率任务，外部请求固定为 0。

默认落盘为 gitignored `runtime/{data,artifacts,studio}`，支持
`ALLWIN_DATA_DIR`、`ALLWIN_ARTIFACT_DIR`、`ALLWIN_STUDIO_OUTPUT_DIR`、
`ALLWIN_CONTENT_HORIZON_DAYS`、`ALLWIN_MAX_REQUEST_ATTEMPTS` 与
`ALLWIN_API_BASE`。生产建议路径为 `/var/lib/allwin/...`，没有硬编码进
pipeline。fresh 失败不替换 latest-success，公开状态转为 STALE；没有成功
基线时才为 UNAVAILABLE。

真实执行保留 FotMob 5104968 / NowGoal 2912857，主客方向一致、开球
`2026-07-31T17:00:00Z`、差 0 秒、confidence 1.0。第一次 provider 获取后的
本地初始化错误安全失败；修复 runtime core 初始化后，第二次 fresh 6 requests
成功，紧接着幂等 fresh 5 requests 成功且自然跳过未到期 odds。因此本轮真实
provider attempts 合计 22/50；其中最终参数化实现再次真实 fresh 使用 5 次
并成功，赔率未到期所以没有请求赔率 endpoint。最终 runtime 为 240 fixture
rows、80 table rows、两队各 8 场近期比赛、6 条真实 1X2/AH/OU 记录。Bet365 当前值
1.62/4.10/4.75，去水为 57.5979%/22.7582%/19.6439%，来源严格为
`MARKET_BASELINE`。

赔率只有一个真实系统观测点。相同 payload 不追加业务快照，但 poll/run 事实
可保留；页面显示“当前赔率”，不伪造变化曲线。样板在实跑时仍在 T-72h
之外，due dry-run 自然得到下一次 `2026-07-28T17:00:00Z`，没有伪造系统
时间。开球后关闭赛前采集，in-play 排除。

本地 production preview 为 `http://127.0.0.1:3400`。浏览器真实检查确认：
首页有样板比赛及 LIVE/update/source/count，匿名详情只有主胜 58%，公开 DTO
key 仅为 `meta/tier/top_outcome/top_probability`；Premium 显示完整三项概率
与真实 1X2/AH/OU；Studio 打开同一比赛并导出真实 1080×1920、1080×1350
PNG、JSON 与 SRT。PNG signature/IHDR/尺寸/大小通过，控制台 0 warning/error。
口播稿、三个标题与公众号摘要也在 durable Studio 目录；公众号二维码缺配置时
继续中性占位。

最终回归：

- fresh/API/odds/schedule-state/migrations/contract 九文件：
  **335 collected / 335 passed**；
- NowGoal/provider 独立文件：**25 / 25 passed**；
- frontend：**3 files / 28 tests passed**，typecheck、production build
  （12/12 static pages）与 browser bundle loopback 检查通过；
- 0 skip、0 xfail；一条既有 Starlette/httpx deprecation warning source。

一次把 provider 文件放在已导入 `fotmob_client` 的同一 pytest process 后执行，
暴露既有 test-isolation 假设并产生 1 个失败；该文件在其要求的独立无代理环境
25/25 通过。此项记录为 P2 test debt，没有修改 transport，也不阻塞已真实验证
的 daily product path。

已准备但没有安装/部署：daily systemd service/timer、EnvironmentFile 示例、
logrotate、health/backup/retention、AWS 东京目录权限与 Cloudflare/Nginx
origin 指引。真实 `data/*.db` 未迁移或写入，未部署 AWS、systemd 或
Cloudflare。

当前产品状态：

- `FRESH_DAILY_CONTENT_PIPELINE_READY`
- `ACTIVE_LEAGUE_MVP_READY`
- `DAILY_STUDIO_WORKFLOW_READY`
- `AWS_DEPLOYMENT_PACKAGE_READY`

## 20. 品牌视觉基础 V1（2026-07-29）

品牌语言已锁定为“欧赢 ALLWIN｜足球数据与比赛分析”，主口号为
“看数据，也看门道。”，辅助文案为“赛程、状态、xG、赔率变化和历史记录，
一场一场看，一条一条说。”，信任表达为“数据有出处，判断有记录。”

第一轮视觉落地撤下黑金主系统，改为暖纸白、煤黑、朱砂红和少量冰蓝的中文
编辑部方向。已修改全站颜色变量、导航品牌锁定、首页首屏、栏目标题、卡片、
比赛行与 metadata；保留现有比赛、权限、Studio 和公众号业务逻辑。完整规范见
`docs/brand/brand-foundation-v1.md`。

本轮前端验证：

- `npm run typecheck`：通过；
- `npm run lint`：通过；
- `npm run test`：3 files / 28 tests passed；
- 隔离 `/tmp` production build：12/12 页面成功生成；
- 本地浏览器检查新版首页：主口号、辅助文案、导航和 CTA 正确渲染，
  console 0 warning/error；
- `git diff --check`：通过。

本轮未部署 AWS、未修改真实数据库，也未改动现有采集、模型、权限或支付逻辑。
下一步为比赛详情与 Studio 内容模板的品牌迁移，以及专题页/社交传播模板。

### 20.1 品牌视觉 V2：产品首页去 AI 化（2026-07-29）

V1 的品牌语言继续保留；海报式超大口号、方法宣言卡和抽象装饰退出首页。
首页改为“今天看什么 → 真实比赛 → 产品入口 → 公开记录 → 权益 →
Studio → 公众号”的使用顺序。

全局视觉切换为深海蓝、赛场青、荧光黄和中性产品表面；采用小圆角、薄边框和
无悬浮阴影的数据产品结构。结构参考 Opta Analyst、Hudl StatsBomb、FotMob、
Sofascore 与 FBref 的内容优先、证据优先和比赛优先原则；不复制其商标、配色、
组件或文案。完整规范见 `docs/brand/brand-foundation-v2.md`。

### 20.2 首张真实数据图卡（2026-07-29）

首页新增“今日重点分析”，样板为挪威超 Match `5104968`：
瓦勒伦加 vs 汉坎。图卡使用
`runtime/studio/match-5104968-analysis-bundle.json` 的已保存真实数据，
确定性导出 1600×900 与 1080×1350 两种尺寸。

公开图卡仅展示匿名接口允许的一项概率：主胜 `58%`。平局与客胜概率没有进入
公开图片；`MARKET_BASELINE` 明确标记为 1X2 赔率去水结果，不表述为本站自有
模型预测。近期战绩、赛季累计 xG/xGA、休息时间、数据截止时间、来源和系统
观测点均来自同一 bundle，缺失数据不补零。

真源、输出 hash 和权限边界见
`docs/brand/data-card-match-5104968.md` 及
`frontend/public/brand/data-cards/match-5104968-manifest.json`。

### 20.3 首页现实足球视觉预览（2026-07-29）

首页首屏增加一张现实比赛庆祝场景图片，与挪威超 Match `5104968` 的数据图卡
明确分区，不把图中人物或赛事暗示为样板比赛内容。原图等比例优化为
1600×1082 WebP，大小 168,666 bytes；人物、奖杯、Getty 水印、图片编号与
Alex Pantling - FIFA 署名均未裁切、删除或改写。

当前图片授权状态未验证，页面直接标注“现实足球视觉预览”与
“正式上线前需替换为已取得网站使用授权的原图”。因此本地预览可用，但该资源
不是 AWS 正式部署就绪资产。原图、输出 SHA、处理方式与授权边界见
`docs/brand/home-editorial-photo-preview.md` 和
`frontend/public/brand/editorial/getty-2286810893-home-preview.json`。

本轮前端验证：typecheck、lint、3 files / 28 tests、production build
（12/12 pages）和 browser bundle loopback 门禁通过。真实浏览器确认 WebP
以 1600×1082 自然尺寸加载，移动端无横向溢出，首屏与下方数据图卡保持独立
层级，console 0 warning/error。本地 production preview 更新至
`http://127.0.0.1:3500/`，未部署 AWS。

### 20.4 手机端首页收口与“关于我们”（2026-07-29）

根据运营目标，首页不再承担品牌介绍：首屏调整为“今日重点分析 → 瓦勒伦加
vs 汉坎 → 真实数据图卡 → 查看完整分析/更多比赛”，随后才展示近期赛程。
正式评估、权益长表、Studio 工作流、公众号介绍和现实足球视觉从首页移出，
底部仅保留公开记录、会员权益与关于我们的轻量入口。

新增 `/about` 静态页面，集中说明平台定位、数据来源、公开记录、内容工作流
与合作方向。Getty/FIFA 预览图迁到该页并继续保留未授权上线警示；首页不再
加载该图。手机导航固定只显示比赛、会员和关于我们，Logo 作为首页入口；
分析员 Studio 与管理入口不再占用手机导航宽度，桌面端仍保留完整导航。

本轮验证：

- typecheck、lint、3 files / 28 tests：通过；
- production build：13/13 pages，通过；
- Playwright 匿名首页/关于/比赛/权限/信任页：5/5，通过；
- browser bundle loopback 门禁：通过；
- 真实浏览器 369×733：首屏完整显示重点标题、比赛、竖版数据卡和两个动作，
  无横向溢出；
- `/about` 在同一移动视口正确显示平台介绍与合作入口；
- 首页和关于页面 console 0 warning/error。

本地 production preview 保持 `http://127.0.0.1:3500/`，未部署 AWS。

### 20.5 首页公开战绩证据带（2026-07-29）

首页在“今日重点分析”之前新增一条手机优先的公开战绩证据带，使用正式
`/api/v1/track-record` 真源，不新增手填战绩或演示收益。信息层级为：
正式记录、命中率、已结算数量、最近已结算样本的累计命中率走势，以及逐场记录
入口；今天的重点比赛仍紧随其后。

当前真实正式样本为 0，因此首屏如实显示“公开验证中”、0 场和等待首批正式
样本，不用历史研发预测、回测区间或虚构收益填充版式。正式样本进入账本并完成
离线评估后，同一组件会直接显示 API 返回的样本量与 Accuracy，并用已结算
逐场结果绘制近期累计命中率；固定投入回报和最大回撤尚未进入正式 API 口径，
本轮不伪造、不提前展示。

视觉采用薄边框、编辑部式数字排版和无面积渐变的小型折线区，不使用发光、
玻璃拟态、3D 图标或抽象 AI 装饰。匿名 Playwright 首页用例同步增加公开战绩与
“公开验证中”断言。

### 20.6 首页横滑比赛概率带（2026-07-29）

首页最上方新增手机优先的“今日比赛”横滑卡片带，借鉴体育数据站顶部比赛
ticker 的快速浏览机制，但不复制其视觉。卡片读取真实
`/api/v1/matches?status=upcoming` 与逐场匿名 prediction DTO；有公开概率的
比赛优先排列，其余比赛明确显示“分析准备中”，不补造概率或模型输出。

匿名卡片只消费 `top_outcome`、`top_probability` 和 `probability_source`。
平局、客胜等另外两项概率没有进入公开组件；“完整三项概率 · 会员分析”只作
权限说明，不包含受限数值。当前 8 场未来比赛中只有 Match `5104968`
具备公开概率，因此该场排在首位，其余 7 场保持无数字的准备状态。

交互使用原生横向滚动和 CSS `scroll-snap`，不自动播放；手机卡宽为约
`82vw`，保留下一张卡的视觉露出。页面顺序为比赛卡片带 → 压缩后的公开战绩
证据带 → 今日重点分析。手机端公开战绩继续展示正式记录、命中率和已结算，
累计走势只在更宽屏幕显示，从而把重点比赛留在首屏附近。

真实 production preview 首轮验收还暴露首页 `RecentMatchesSection` 的逐场
prediction fetch 没有设置 revalidate，静态首页在运行期会被 `no-store` 请求
触发 Next.js static-to-dynamic error。该请求现与 ticker 和比赛列表统一为
60 秒再验证；重新构建后首页、ticker 与近期比赛均从东京 API 本地代理正常
取数，不再落入“API 暂时无法加载”降级框。

最终验证：typecheck、lint、3 files / 28 tests、匿名 Playwright 5/5、
production build 13/13 pages、browser bundle loopback 门禁与
`git diff --check` 均通过。真实浏览器 390×844 验证 ticker
`scrollWidth=2974 > clientWidth=390`、卡宽约 320px、横滑位置可推进、
页面总宽等于视口 390px、首张只含主胜 58% 与来源文字、公开战绩图表在手机端
收起且三个正式指标保留，console 0 warning/error。

### 20.7 首页移动端重点比赛合并卡（2026-07-29）

首页首屏完成第二次信息收口：原横滑带首卡、硬编码“今日重点分析”和大型竖版
传播图卡合并为一张数据驱动的重点比赛卡。重点比赛从未来比赛中按“具有合法
匿名公开概率优先、随后按开球时间”自动选择；没有公开概率时仍保留最近比赛
入口并显示“分析准备中”。其他比赛横滑列表明确排除重点比赛，手机宽度约
72vw、卡高约 104px，不自动轮播。

重点卡只显示匿名 DTO 已公开的一项概率和真实 `probability_source`，并从现有
analysis bundle 按 `form → season_xg → rest` 各取最多一条真实依据。某类缺失
时直接减少条数；analysis 请求失败只让依据区显示未完成状态，不影响比赛、
公开概率和详情入口。大型 1080×1350/1080×1920 图片继续保留在 Studio 与传播
素材目录，不再进入首页。

首页比赛数据收敛到一个 React server-side 聚合入口：未来/完赛比赛、逐场公开
prediction 和重点场 analysis 共用一次请求结果；比赛与分析均 60 秒再验证，
公开战绩 120 秒再验证。近期赛程也排除首屏重点比赛，不再重复请求或连续重复
展示同一场。

公开战绩改为三个真实状态：有正式样本才显示场次、Accuracy、已结算和走势；
样本为零只显示“公开验证中，从首场锁定记录开始”；接口失败显示“公开记录
暂时无法加载”，不会再把失败误报为 0 场。

手机顶部缩为 52px，只保留欧赢品牌与账户/套餐状态；新增固定底栏
“首页 / 比赛 / 战绩 / 我的”，匿名“我的”进入登录，登录后进入账户。
底栏适配 safe-area，主要点击区不小于 44px。桌面仍保留完整顶部导航，重点卡
采用“公开结论 + 本场依据”双栏。全页继续使用深海蓝、赛场青和少量荧光黄，
没有新增渐变光晕、玻璃效果、厚阴影或大面积装饰。

新增 `frontend/lib/homepage.ts` 纯函数及 4 项永久单元测试，覆盖重点比赛选择、
无预测降级、依据缺失和战绩零样本/失败分离。最终前端静态验收：

- typecheck、lint：通过；
- Vitest：**4 files / 32 tests passed**；
- OpenAPI 类型漂移检查：通过；
- production build：**13/13 pages**；
- Playwright：**13/13 passed**，包含 360×800、390×844、430×932、
  1280×800、重点比赛不在横滑列表重复、匿名受限概率不进入首页、底栏匿名/
  登录态与页面无横向溢出；
- fresh build browser bundle loopback 门禁与 `git diff --check`：通过。

本地 production preview 在真实浏览器 390×844 下进一步确认：重点卡底部为
404px，三条真实依据与 44px 主按钮完整可见；document 宽度等于 390px，其他
比赛横滑可滚动且不含 Match `5104968`，匿名页面没有受限概率，手机底栏四个
入口正确，console 0 warning/error。1280×800 下重点卡为约 362/644px 双栏，
页面无横向溢出且手机底栏隐藏。

本轮不新增 API、不修改权限规则、采集流程、Studio 图片、真实数据库或部署
配置；AWS 仍未部署。

验收后完整性复核发现既有 `tests/e2e/seed_e2e.py` 把 core 主库以 symlink
接入 `data/e2e`；Playwright 虽未改变 `allwin.db` 的 SHA/size/mtime，但 SQLite
只读连接刷新了真实 `allwin.db-shm` 的 mtime。该事实不掩盖、不伪造恢复。
E2E seed 随即改为先确认真实 core WAL 为 0，再把主库复制到隔离目录并设为
0600；后续 E2E API 不再打开真实 core 文件或真实 sidecar。

### 20.8 全站深色与浅色主题切换（2026-07-29）

全站新增深色/浅色主题切换。首次访问默认跟随操作系统偏好；用户手动切换后，
选择写入浏览器 `localStorage` 的 `allwin-theme`，刷新与重新打开页面时继续
沿用。根布局在 React hydration 前执行同源内联启动脚本，先确定
`data-theme` 与浏览器 `color-scheme`，避免页面先亮后暗的主题闪烁。

两套主题复用同一份 JSX、组件和设计 token，不复制页面树。浅色模式保持既有
编辑部式体育数据页面；深色模式使用深海军蓝表面、赛场青交互色和少量荧光黄
状态色，不增加光晕、玻璃拟态、厚阴影或大面积装饰。桌面导航和手机顶部栏均
提供不小于 44×44px 的可访问切换按钮，按钮具有明确中文标签与
`aria-pressed` 状态；系统启用减少动态效果时关闭主题过渡动画。

主题覆盖首页、比赛详情、Studio、账户及会员页面所共用的全局 token。手机端
底部导航继续使用当前主题表面，safe-area 与页面点击区域规则不变。真实浏览器
检查首页和 Match `5104968` 详情页，两套主题均无横向溢出，控制台
0 warning/error；深色模式下比赛结论、依据、概率条和导航层级保持清晰。

最终前端验收：

- typecheck、lint、OpenAPI 类型漂移检查：通过；
- Vitest：**5 files / 33 tests passed**；
- Playwright：**14/14 passed**，新增 390×844 主题切换、刷新持久化、
  切回浅色与无横向溢出检查；
- production build：**13/13 pages**；
- fresh production browser bundle loopback 门禁与 `git diff --check`：通过。

本地 production preview 继续运行于 `http://127.0.0.1:3500`。本轮未新增
后端 API，未修改权限规则、采集流程或真实数据库，也未部署 AWS。

### 20.9 统一球队队徽链路（2026-07-29）

球队队徽已收敛为一条 provider-aware 链路：`dim_match` 中的 FotMob Team ID
由显式离线 CLI 同步到 `ALLWIN_MEDIA_DIR`，经 PNG 完整性、尺寸、大小与 SHA-256
校验后原子写入 manifest 和本地缓存；公开 API 只返回
`/api/v1/media/team-crests/...?...` 同源版本化地址，不暴露 FotMob 来源 URL，
普通页面请求也不会触发远程下载。媒体根、`team-crests` 与 provider 目录必须
都是非 symlink 真实目录，文件自身也必须为单链接普通文件。共享
`TeamRef.crest_url` 为 optional/nullable，比赛摘要、详情、近期状态、积分榜和
公开战绩统一使用同一 resolver。

前端新增单一 `TeamBadge` 组件，首页重点比赛、其他比赛横滑卡、`/matches`
列表、Match `5104968` 详情和积分榜均复用。组件固定尺寸并保持
`object-fit: contain`；URL 缺失或加载失败时显示稳定盾牌字母 fallback，不影响
比赛、预测、分析或每日内容流程。装饰性图片使用空 `alt`，独立使用时提供可访问
名称；深色与浅色主题复用同一 JSX。

真实同步仅访问获准的 `images.fotmob.com`，对挪威超 2026 的 16 支当前球队执行
16 次 HTTP transport attempt，结果 `inserted=16 / skipped=0 / failed=0`。
随即以同一命令重跑为 `inserted=0 / skipped=16 / failed=0 /
request_attempts=0`。瓦勒伦加 `8007`、汉坎 `8448`、博德闪耀 `8402`、
利勒斯特罗姆 `8476` 的本地文件均为真实 PNG（192×192），manifest 和图片位于
gitignored `runtime/media/team-crests/`；真实 SQLite 数据库没有参与写入。

本地 production preview 保持在 `http://127.0.0.1:3500`。真实浏览器验收覆盖
360×800、390×844、430×932、1280×800：首页没有横向页面溢出，重点场只出现
一次，真实队徽显示为 48/32/24px 且没有外部图片直链；比赛详情只使用
8007/8448 两枚 56px 队徽；积分榜 16 行对应 16 支球队；比赛列表显示统一组件；
深浅主题下队徽可见，console 0 warning/error。隔离 E2E 的无队徽种子在首页与
详情各验证两个 fallback；匿名 API 与页面仍只含一项公开概率，受限概率字段物理
缺失。

最终验收：

- 后端相关回归：**119 collected / 119 passed**，0 failed、0 skipped、0 xfailed；
  有 3 条已披露 warning（Starlette/httpx deprecation 1 条、测试退出时
  SQLite ResourceWarning 2 条）；
- Vitest：**6 files / 39 tests passed**；
- typecheck、lint、OpenAPI drift：通过；
- production build：**13/13 pages**；
- Playwright：**14/14 passed**，0 failed/skipped/xfailed；启动过程有
  `NO_COLOR` 被 `FORCE_COLOR` 覆盖的 Node warning，不影响用例；
- browser bundle loopback 门禁：通过。

AWS 首次同步、新联赛同步、缓存刷新和商标/来源条款说明见
`docs/operations/team-crest-sync.md`。本轮没有 commit、deploy 或修改真实
数据库；AWS 仍未部署。

### 20.10 Studio 六张“球队打法拆解”传播图卡（2026-07-29）

Studio 新增 `douyin-safe-v1` 并在真实球队风格数据可用时设为默认传播模式；
既有 `internal-full-v1` 完整分析继续保留。安全版不是用 CSS 隐藏敏感字段，
而是由独立白名单 view model 生成，物理上不包含 prediction、probability、
1X2、odds、market-baseline、推荐或收益字段。公开比赛 API 与匿名权限规则
没有改变。

数据链路直接复用每日内容流水线已经保存的主客队 FotMob team artifacts，
不增加网络请求。解析器对球队 ID、联赛、赛季、积分榜样本场次、指标 participant、
rank 和两个 artifact SHA 做一致性校验；所有指标单位和换算由 canonical
registry 固定。profile 以 `(match_id,data_cutoff_at)` 写入 runtime/E2E 专用
append-only 表，精确 replay 幂等跳过，同键不同内容冲突。真实
`data/platform.db` 没有应用该 migration。

六张卡依次为比赛封面、控球与组织、禁区威胁、边路与定位球、无球与防守压力、
对位总结与风险。每张最多一个主要比较图和三项指标；xGA 明确标注“越低越好”；
缺失指标缩减，不补零；定位球严格写真实“定位球进球”，没有改称或推导定位球
xG。视觉采用深海蓝、赛场青、少量黄色、真实队徽与直接数值标签，不使用雷达图、
黑金模板、比赛视频或版权图片。右侧交互区和底部文案区只留空，不把设计辅助线
文字导出。

真实挪威超 Match `5104968` replay 使用 14 场样本，paired source hash 为
`aa8cccdd481d5f0a708ccbe24421d1307e99957661413ae46e85c55d82e9ea2f`。
典型真实值包括控球率 49.3%/46.1%、场均 xG 1.71/1.44、场均禁区触球
32.3/24.2、场均角球 7.1/3.9、赛季定位球进球 3/6 和场均 xGA
1.89/1.77。重复运行 `daily_content.sh --league eliteserien --replay`
为零 transport attempt，profile 结果为 `skipped`。

安全输出包含 12 张 PNG、45–60 秒口播、SRT、三个标题、小红书正文、公众号
摘要及安全 JSON；文件名包含 profile、match、cutoff 和 source hash。
最终 PNG 文件位于 `runtime/studio/douyin-safe-v1/`，均以 `no-model` 隔离
内部模型命名。六张 1080×1920 和六张 1080×1350 已逐张视觉检查；检查过程
真实发现并修正 snake-case `crest_url` 映射与 1350 紧凑版页脚重叠。

最终验收为：后端广泛回归（排除 4 项会重建 E2E seed 的测试）
**715/715 passed**；Vitest **7 files / 43 passed**；Playwright
**15/15 passed**；typecheck、lint、OpenAPI drift、13/13 route production
build、同源 browser-bundle 门禁与 `git diff --check` 通过。真实 production
preview 在 390×844、1280×800 以及 Studio 深浅主题下均为 6 scenes、
0 overflow、0 broken image、0 console warning/error。

完整审计见 `docs/audits/douyin-safe-studio-v1.md`。本轮不部署 AWS、不修改
真实数据库、不 commit。

### 20.11 FotMob 多联赛多赛季覆盖 probe v1（2026-07-30）

新增统一 `backend/season_resolver.py` 与独立
`analysis/multi_league_season_coverage/`。resolver 区分自然年、跨年和 tournament
season；跨年绝不按当前月份猜测，必须由 provider 广告列表或显式已复核映射支持，
且 returned season 必须与请求完全一致。现有 Eliteserien fresh 自然年行为保持
`2026`；没有为 Allsvenskan、MLS 或其它联赛打开新的 fresh 能力。

真实 durable run 位于 gitignored
`runtime/research/league-coverage/multi-league-season-coverage-v1/`。最终结果：

- 14/14 目标赛事/控制组 `COMPLETED`；
- 508/800 次真实 transport attempt，494 succeeded、14 failed；失败 attempt
  永久留在 ledger，所需 artifact 均在显式 resume 中补齐；
- 127 个 provider season/结构通过 identity、returned season、fixture、
  pagination、Match/team ID、kickoff 和 status 门禁；
- 351 场 finished/non-cancelled 早/中/晚确定性样本；
- 全范围 replay 零新增请求，replay 前后 raw+ledger 汇总 SHA-256 均为
  `55787a909f498cdec449eaf19ad795f175e6e7fa16e9c7d39e9012e233febf8f`；
- 全部 runtime 文件 mode 0600；未写四个真实 SQLite 数据库。

真实身份修正包括：A-League search 唯一发现 Australian league `113`，并由
schedule 再确认 `id=113/name=A-League/season=2026/2027`；Brazil competition
`268` 的来源 canonical name 是 `Serie A`。J. League `223` 同时广告 `2026`
和有 380 fixtures 的 `2026/2027`，后者按来源证据记
`TOURNAMENT_SEASON/SEASON_REGIME_TRANSITION`，没有强塞进自然年或按月份推断。

completed-season 三点样本只产生 `SAMPLED_SAFE`。core xG/shot xG 的主要
sampled-safe 起点为：MLS 2020、J1 2022、K League 1 2022、A-League
2020/21、Eredivisie/Championship/Liga Portugal/UCL/UEL 2020/21、Brazil
2023、UECL 2021/22。UECL 实际首季为 2021/22。11 个长跨度赛事均观测到
xGOT NULL→literal-zero encoding shift；物理字段无证据时保持 UNVERIFIED。

永久测试 **44 collected / 44 passed**，0 failed/skipped/xfailed/warnings。
完整审计与字段级 CSV 见
`docs/audits/multi-league-season-coverage-probe-v1.md`。

结论：

- `MULTI_LEAGUE_SEASON_COVERAGE_PROBE_VALIDATED`
- `CROSS_YEAR_SEASON_RESOLUTION_VALIDATED`
- `READY_FOR_ISOLATED_HISTORICAL_BACKFILL`

未运行真实 historical backfill、migration、Worker、systemd、API、frontend 或
deployment；下一步只能先设计隔离目标库的 provenance、事务、reconciliation
和 rollback。

### 20.12 NowGoal 历史赔率能力直连 probe（2026-07-30）

新增 `analysis/nowgoal_historical_capability_probe/`，只读取 §20.11 已验证的
FotMob schedule artifact，按 11 个历史赛事各自 early/middle/late completed
season 确定性选取 33 场（32 个北京日期，范围 `2015-07-04`～`2026-04-12`）。
transport 固定 `httpx.Client(trust_env=False)`，不读取/继承任何代理环境；本轮
住宅代理流量为 0，未写真实 SQLite。

真实 live run 共 34 次直连请求，全部 succeeded、0 failed/retry/WAF：

- 32 个历史 type=6 日程响应均为合法 JSON、`ErrCode=0`，但全部
  `matchcount=0`、无比赛行；
- 因此 33 场均无历史 schedule、无 Titan 映射、无历史 type=14 请求；
- 同 transport 的当前控制 `2026-08-01` 返回 988 场，排除直连或 parser 整体
  失效；
- 当前已验证 Titan `2912857` 的 type=14 控制返回两家目标公司、
  1X2/AH/OU，initial/latest 均存在，排除赔率端点整体失效。

所以当前已验证的 `historical date → Titan → odds` 路径为
**SAMPLED_UNAVAILABLE**，历史 NowGoal 回填状态为 **BLOCKED**。这不证明不存在
其它未记录 archive endpoint 或外部历史 Titan 映射；两者继续 UNVERIFIED。
即使 type=14 有 `f/l`，也没有来源时间戳或完整变化序列，`l` 不得冒充收盘价。

同 run-id replay 零网络，request ledger + raw artifact 汇总摘要前后均为
`e44436212cfeedf636a90297077df79ed728b5bdead21b520e4e82aacf475eff`。
永久测试 **14 collected / 14 passed**，0 failed/skipped/xfailed/warnings。
完整证据见 `docs/audits/nowgoal-historical-capability-probe.md`。

结论：

- `NOWGOAL_CURRENT_DIRECT_PATH_VALIDATED`
- `NOWGOAL_HISTORICAL_DATE_DISCOVERY_SAMPLED_UNAVAILABLE`
- `NOWGOAL_HISTORICAL_ODDS_BACKFILL_BLOCKED`

FotMob 历史 raw acquisition 不受此项阻断；使用当前 NowGoal 路径做严格历史市场
基线或投注策略回测仍被阻断。

#### 20.12.1 五大联赛 2020–21 web archive 补充验证

随后不再使用已证明无历史行的 `type=6 date`，而是从 NowGoal season archive
读取英超 `36`、意甲 `34`、西甲 `31`、德甲 `8`、法甲 `11` 的 2020–21
赛季 catalog，并对每个 catalog 的确定性中间比赛读取历史赔率。v5 live run：

- 5 个 catalog 分别含 380 / 380 / 380 / 306 / 380 场；
- 40 次直连请求全部成功，`trust_env=False`，住宅代理流量 0；
- 五场样本的 Bet365、Macauslot、Pinnacle 均有带时间戳的赛前 1X2；
- Bet365 与 Macauslot 五场样本均另有带时间戳的赛前 AH/OU；
- 1X2 独立 catalog 验证公司 ID：Bet 365 `281`、Macauslot `80`、
  Pinnacle `177`；
- archive verdict 现在要求三家公司全部命中，不再只检查前两家。

结论更新为 `NOWGOAL_TOP5_2020_2021_ARCHIVE_SAMPLE_AVAILABLE`。旧的
`NOWGOAL_HISTORICAL_DATE_DISCOVERY_SAMPLED_UNAVAILABLE` 仍作为历史事实保留，
但不再能支持“没有 archive endpoint”的泛化结论。当前只验证每联赛一场样本，
尚未运行全比赛或 2020–21 之后逐赛季 backfill，不能声称完整覆盖。
## 20.12b FIVE_CRITICAL_PRODUCT_FIXES_V1(2026-07-30)

- freshness：公开状态改为请求时投影 `FRESH / STALE / UNAVAILABLE`；旧落盘
  `LIVE` 不再直接公开。Match 5104968 的真实过期反例现在为 `STALE`。
- build/preview：本地构建进入 immutable staging，再原子切 current；新增静态和
  运行态 asset 门禁；proxy 在监听前验证双上游。
- league：主导航改为 `/leagues`；目录按真实 core 数据可用性和 entitlement
  展示，挪威超 59 可被发现，未同步英超不再成为默认死路。
- team：统一审核中文名 → provider 英文名 → “球队名称待同步”；隔离挪威超真实
  16 队不再出现 `Team <ID>`；手机积分榜保留关键列并中文化资格图例。
- matches：默认未来七天未开赛；支持今天/明天/三天/七天、分析/赔率、球队中英
  文/alias 搜索及可分享组合 query；内容优先级内按精确 kickoff + match id 稳定
  排序，5104968 在同日 5104962 前。
- 权限边界未变：匿名仅一项概率，analysis member 投影和 Premium odds timeline
  仍物理缺失，MARKET_BASELINE 口径不变。
- 完整性事件：首次浏览器 preview 误指向 gitignored `runtime/data`，既有
  analytics 客户端向 `runtime/data/platform.db` 追加事件；四个受保护
  `data/*.db` 未写。发现后 preview 全部切换到新建 `/tmp` 副本；不伪造恢复。
- 最终实跑：后端 169 passed；Vitest 46 passed；Playwright 3 passed；
  typecheck/lint/OpenAPI drift/production staging build/static+runtime asset smoke/
  `git diff --check` 均通过。无 skip/xfail。
- 当前完整性：FAIL。四个 `data/*.db` 主文件未变，但两个真实 SHM 的 mtime
  相对启动基线变化；另有上文已披露的 `runtime/data/platform.db` analytics
  追加。cache 109/813、`.pytest_cache` 和旧 `.next` 未变。不得输出依赖
  integrity PASS 的 `FIVE_CRITICAL_PRODUCT_FIXES_V1_COMPLETE` 标签。

## 20.13 春秋直播公开实时比分 Provider v1（2026-07-31）

新增 `backend/providers/kbisai_live_scores.py` 和
`backend/cli/kbisai_live_scores.py`。只使用官网匿名 Web 客户端公开调用的
`POST /api/v1/football/realtimeMatch_b` 与
`wss://kbisailive.com/ws/match`；没有读取 Cookie、账号、验证码、付费内容、
`.env` 或代理凭证，也没有调用主播、聊天室、赔率或推荐接口。

REST 返回 `application/x-protobuf` 的 `MatchListResp`。本地有界 reader 解析
competition、team、lottery、match、packed score 和业务 code；未知 wire type、
截断、超大输入、重复实体/比赛、未知引用、未知 status 和不一致比分均 fail
closed。标准化输出包含 provider identity、UTC kickoff、状态、当前比分和原始
比分分段；来源没有可验证更新时间，故 `source_updated_at=NULL`。

真实样本发现压缩整数数组的所谓 `goingTime` 为 epoch-like 数值而不是比赛分钟。
实现只输出 `provider_clock_reference` 与 `clock_semantics=UNVERIFIED`，禁止在
产品中直接展示为“第 N 分钟”。

WebSocket 匿名握手复现公开前端的 scope/timestamp/MD5/Base64 算法，type=10
订阅足球列表，且只接收 type=12/13 比分/状态增量。实际连接与订阅成功；最终
10 秒观察窗口没有发生变更，因此 `ws_status=OBSERVED`、
`ws_update_count=0`。该观察器最长 55 秒、最多 100 条、不自动重连。

最终真实 run `20260730T221825432063-ca8fe5ca03dc`：

- 105 个赛事、686 支球队、344 场比赛；
- `IN_PLAY=14`、`FINISHED=160`、`NOT_STARTED=162`、`OTHER=8`；
- raw 56331 bytes，SHA-256
  `00f103bfb6b0946fd341b90c9dd68b5036f790cca8087c34da94b7f166b8687b`；
- raw Protobuf、matches、updates、summary 均以 0600 写入 gitignored
  `runtime/research/kbisai-live-scores/`；
- 没有写四个真实 SQLite 数据库。

探索期 6 次 REST attempt 中 3 次 200、3 次因 Accept 过窄得到 406 的真实 RED；
修正为浏览器兼容的 Protobuf+通配 Accept 后成功。3 次 WebSocket 连接没有自动
retry。永久定向测试最终 23 passed，唯一 warning 是既有 Starlette/httpx
deprecation。

当前标签：

- `KBISAI_PUBLIC_LIVE_SCORE_PROVIDER_VALIDATED`
- `KBISAI_PUBLIC_WEBSOCKET_SUBSCRIPTION_VALIDATED`
- `KBISAI_REALTIME_ARTIFACT_READY`

完整性必须单独披露：四个真实 DB 主文件、两个空 WAL 和两个 SHM 内容均保持
既有指纹，未新增数据库 sidecar；但最终验收过程在 `.venv` 的
requests/urllib3/charset_normalizer 下创建 6 个 `__pycache__` 目录和 54 个
`.pyc`，cache 由此前记录的 109/813 变为 115/867。没有 source-tree pyc，
也没有删除、touch 或伪恢复。因此 `CURRENT_MODULE_CACHE_INTEGRITY=FAIL`，不影响
真实快照与 Provider 行为证据，但不得标 PASS。

仍未完成：来源商业再分发许可确认、精确分钟语义、durable polling/hash-diff、
source health、跨源 match xref、真实 DB schema/write、API/frontend、systemd
和 AWS 部署。完整审计见
`docs/audits/kbisai-live-score-provider-v1.md`。

## 21. 五大联赛接入新产品面:数据回填 + /league 页面迁移到 /api/v1(2026-08-04)

目标:让西甲/德甲/意甲/法甲在现有产品面真实可看。此前四大联赛只有逐场
Bronze/fact 数据(`dim_match` 等 11115 场),四个聚合数据源
(`fact_league_table` / `silver_team_season_stats` / `silver_league_season_summary` /
`fact_season_player_stats`)全部只有英超;且 `/league/[id]/*` 四个页面还在调
legacy `/api/league/{id}/overview`(无联赛门禁,对西甲恒返回空数组),导致
Pro 用户看到的是空表格而非数据,匿名用户看到的也是空表格而非会员引导。

### 数据回填(真实网络,共 167 次请求,0 失败)

- 备份先行:`data/backups/allwin-pre-top5-backfill-20260803T161220Z.db`
  (SQLite Backup API,integrity_check ok)。
- 新增 `backend/cli/backfill_season_tables.py`(复用 ingest_league 既有构件;
  响应身份校验 details.id/selectedSeason 不一致即拒绝;解析 0 行不 DELETE,
  不能用坏响应清掉已有数据)。
- `fact_league_table`:53/54/55/87 × 各自 dim_match 全部 6 个赛季
  (2020/2021–2025/2026),每赛季 1 次 league_matches 请求。
- `fact_season_player_stats`:四联赛仅 2025/2026(每赛季约 37 个维度请求);
  历史赛季球员榜**未回填**(约 740 次请求,留待需要时批量执行)。
- `backend/silver/build_silver.py` 离线重跑:silver 五表现覆盖全部
  5 联赛 × 6 季(此前只有英超——silver 表建于 7/11 五大联赛 fact 合并之前)。

### 后端新增

- `/api/v1/leagues/{id}/team-stats`(免费字段投影:射门/射正/控球/xG/xGOT;
  角球/红黄牌/零封/BTTS 等付费深度字段物理不进 SQL 与响应)与
  `/api/v1/leagues/{id}/players`(免费 5 维度 top10,中文名服务端解析)。
  查询在新模块 `backend/queries/league_stats.py`;DTO 在 schemas.py;
  两端点均走 `_require_league_access` 门禁,Cache-Control 47=public 其余
  no-store,并已加入 cache_policy PUBLIC_ALLOWLIST。
- **修复 v1 fixtures 赛季过滤缺陷**:原实现先 SQL LIMIT 再 Python 筛赛季,
  多赛季联赛下目标赛季可能 0 命中(单联赛单赛季的 runtime 库掩盖了该缺陷)。
  season 过滤现下推进 `list_matches` SQL(新增 season 参数),带回归测试。

### 前端迁移(4 页 + 付费墙)

- `/league/[id]/{standings,matches,team-stats,players}` 全部从 legacy
  `/api/league/*` 迁移到 `/api/v1/leagues/{id}/*`:服务端匿名取数
  (`serverGetOptional`),免费联赛保持 SSR;401/403 时渲染客户端
  `MemberLeagueSection`(浏览器带会话 cookie 重取;匿名 → 登录+会员方案引导,
  免费登录 → 会员方案引导,404 → 诚实空态,错误 → 可重试)。
  **修掉了"Pro 联赛匿名/无权益显示空表格"的产品缺口。**
- 展示组件抽为共享纯组件(`components/league/StandingsTable|FixtureRounds|
  TeamStatsBoards|PlayerBoards`),SSR 与客户端会员路径渲染同一份 JSX;
  样式随组件迁移,页面模块只留外壳。`leagueSectionPath` 定义在 lib/api-v1.ts
  (不能定义在 "use client" 模块——client 导出对服务端组件是不可调用引用)。
- legacy fetcher(fetchLeagueOverview/fetchLeagueMatches)已删除;
  legacy 后端端点保留(deprecated)。wdl-predictions 页不动。
- 顺带修复 Codex 时期的时间炸弹测试布景:
  `test_five_critical_product_fixes.product_seeded` 写死 kickoff
  2026-08-01,日历越过后 publish 被登记簿正确拒绝 → 改为动态 now+3 天
  (与 test_api_v1.seeded 同款)。

### 验证(全部真实运行)

- 后端 pytest 全量 **768 passed / 0 failed / 0 skipped**(含 8 个新端点测试
  + 1 个 fixtures 回归测试);契约 `check:api-drift` 无漂移
  (export_openapi 54 paths → gen:api)。
- 前端 typecheck / eslint / Vitest 46 / `next build` 全绿。
- 浏览器实测(生产构建 `next start`,按 playwright.config 既有结论:
  dev/Turbopack 在自动化浏览器下有水合停滞,生产构建无此问题):
  - 匿名英超:SSR 直出真实排名表(阿森纳/利物浦/曼城中文名);
  - 匿名西甲:公共 HTML **零数据泄漏**(仅骨架),客户端 401 → 会员引导 CTA;
  - Pro 登录西甲:排名榜(Barcelona 94 分冠军)、球员榜(Mbappé 25 球,
    有 i18n 映射的球员显中文)、球队榜(控球率 Barcelona 68.8%)全真实渲染;
  - Pro 登录法甲:赛程按轮分组(第 1 轮 Rennes 1-0 Marseille 等)。
  - 本地会话提示:浏览器需经 `127.0.0.1:3000` 访问(与 API 127.0.0.1:8000
    同 site,SameSite=Lax cookie 才随 XHR 发送;localhost:3000 是另一个 site)。

### 已知边界(如实)

- 四大联赛历史赛季(2020/2021–2024/2025)球员榜为空(端点返回诚实
  empty_reason);积分榜/球队榜/赛程历史赛季齐全。
- 西甲/德甲/意甲/法甲球队与球员绝大多数无中文名映射,按 display 规则回退
  provider 英文名(中文名批量翻译是既有独立链路 translate_players,未在本轮)。
- 生产库写入仅限本轮回填的三张聚合表;`dim_match` 等 fact 层未动;
  odds/platform/verify_leagues 未动。
- 全量 Playwright(15 用例)发现 5 个**Codex 期遗留回归**(anonymous.spec 的
  首页 featured-match-card / 比赛列表链接断言,依赖 e2e 种子库的
  kickoff/content 投影;该 spec 7/29 扩写后,7/30 five-critical 只选跑了
  3 个产品修复用例,全量从未复验)。已用 git stash 基线对照 + 代码 delta
  分析确认与本轮改动无交集(本轮对 /api/v1/matches 链路唯一改动是
  list_matches 新增 season 参数,默认 None 时 SQL 不变;首页/比赛页零改动);
  与本轮相关的 10 个用例全部通过。修复该 5 例属独立课题(种子库 kickoff
  provenance 与 content_status schema v2 读写对齐)。

## 22. 数据层规划整合 + 挪超(59)生产接入 + 周末赔率实测启动(2026-08-04)

真实命令与输出(节选;完整规划见 `docs/data-plan.md`,今日发现的所有过期声明
和矛盾已在该文件 §7 逐条登记,不在此重复)。

### 22.1 备份 + 迁移(生产三库)

```
$ BACKUP_KEEP=14 bash deploy/scripts/backup_sqlite.sh
== 备份完成: data/backups/20260804T003934Z(3 库,integrity_check 全部 ok,原子发布)==

$ .venv/bin/python -m backend.db.migrate --db core
[core] applied 0002_kickoff_provenance.sql
[core] applied 0003_schedule_state_v1.sql
[core] up to date: 3 applied (+2 new)

$ .venv/bin/python -m backend.db.migrate --db platform
[platform] applied 0003_snapshot_kickoff_provenance.sql
[platform] applied 0004_lottery_entitlement.sql
[platform] applied 0005_model_league_scope.sql
[platform] applied 0006_team_style_profiles.sql
[platform] up to date: 6 applied (+4 new)
```

三库 `PRAGMA integrity_check` 全部 `ok`;`migrate --status` 三库 `pending=none`;
`/readyz` 返回 200。11115 条历史行验证全部变为 `kickoff_precision='date_only'`
且 `kickoff_source IS NULL`(未伪造 `exact`,与迁移文件语义一致);
`plan_entitlements` 新增 `league:lottery` × free/pro/premium 三档。

迁移前 `core/0003`、`platform/0004~0006` 四个文件为 untracked,已先
`git commit`(commit `665378a`),避免应用后误 `git clean` 造成
`schema_migrations` ledger 与文件不一致。

### 22.2 挪超(59)赛程 + 积分榜(真实网络)

```
$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 59 --season 2026
抓到未开赛场次: 118 (league_id=59, season=2026)
写入 dim_match 完成: 118 行
历史 bronze 里从未出现过的新球队(16 支)…

$ .venv/bin/python -m backend.cli.backfill_season_tables --league-id 59
[OK] L59 2026: table=80 players=0 requests=1
```

验证:round 17 周末 7 场(5104970/71/72/73/74/75/76)全部
`kickoff_precision='exact'`、`kickoff_source='fotmob:fixtures'`;
第 8 场 5104969(Tromsø vs Brann)确认为提前踢完的 `Finish` 比赛
(2026-04-29),被 `ingest_future_fixtures.py` 正确跳过,不计入本轮周末样本。

### 22.3 NowGoal 别名播种

```
$ .venv/bin/python -m backend.cli.resolve_entities
[resolve_entities] 别名新增 16,总数 194
```

另手工插入 9 条北欧球队的真实 NowGoal 拼写(取自
`runtime/artifacts/runs/eliteserien/*/nowgoal-schedule-*.raw.txt`,经
`backend.providers.nowgoal.parse_schedule` 解析,每条 5-7 次独立观测,非猜测),
`dim_team_alias` 总数 203。用 `_alias_team_ids()` 逐一验证 14 支周末相关球队
FotMob 拼写 ↔ NowGoal 拼写解析到同一 `canonical_team_id`,**14/14 全部通过,
0 失败**——这是 `entity_resolution.py` `auto_ok` 六道门中的第一道(别名唯一
解析),真实窗口打开后触发的其余五道门(kickoff 差值/唯一性/provider id
一致性等)尚待验证。

### 22.4 窗口逻辑诚实性验证

```
$ date -u    # 2026-08-04T00:44:50Z(T-72h 窗口于 2026-08-04T17:00Z 开启)
$ .venv/bin/python -m backend.cli.poll_nowgoal --due
[poll_nowgoal --due] now=2026-08-04T00:45:52Z run=ce3f535a-c656-4967-a2d4-b5dcdf93602f
  窗口候选: 0,到期: 0,未到期跳过: 0
  日程抓取: 0 次 0 行;映射 auto_ok=0 needs_review=0
  赔率: 已抓 0 场,落库 0 条,hash 未变跳过 0 条
```

窗口未开时诚实返回 0,且 `schedule_fetches=0`——未在窗口外发起任何真实网络
请求(`run_due_poll` 在 `window_candidates=0` 时提前 return)。

### 22.5 本地轮询调度(macOS 无 systemd 的等价替代)

这台机器是 macOS,`deploy/systemd/allwin-poll.timer` 无法运行。新增
`deploy/scripts/poll_local.sh` + macOS LaunchAgent
`~/Library/LaunchAgents/com.allwin.poll.local.plist`(60 秒 tick,真实节流
仍完全由 `backend/ingest/poll_windows.py` 的 `poll_state` 决定,不是新的采集
频率)。已 `launchctl bootstrap` 加载并验证首次 `RunAtLoad` 真实触发成功
(`nowgoal_snapshot`/`fotmob_snapshot` 均 `status=succeeded`)。

停止方式(记录以备将来关闭):
```
launchctl bootout gui/$(id -u)/com.allwin.poll.local
rm ~/Library/LaunchAgents/com.allwin.poll.local.plist
```

### 22.6 待完成(下一轮/窗口打开后)

- 2026-08-04T17:00Z 后:真实赔率采集验收(AC-1~AC-6,见 `docs/data-plan.md`);
- 首次真实 `--due` 触发后断言 `dim_match_xref` 全部 `auto_ok`/`confidence=1.0`;
- 赛后(2026-08-09 后)结算前必须先跑完全部验收查询(`ingest_match` 的
  `INSERT OR REPLACE` 会用 `parse_match_dim` 重算 kickoff 三列,可能把
  `fotmob:fixtures` 覆盖成 `fotmob:match_details` 或降级为 `date_only`);
- 新赔率源只读能力探测(等用户提供接口细节)。

### 22.7 已知边界(如实)

- 本轮验证范围限于挪超(59);瑞超(67)明确排除在外(`content_pipeline.py` 的
  `fresh_enabled=False`,别名/xref 均为空,本轮未处理);
- `gold_move_cooccurrence` 结构性阻塞(见 `docs/data-plan.md` §3/§4);
- `docs/data-plan.md` 的联赛覆盖矩阵为手工查询产物,尚无自动化重新生成脚本。

## 23. 联赛页赛季切换 + 北京时间统一 + 队徽渲染修复(2026-08-04)

用户报告三个问题,实测后根因与初步猜测均不同,详见对话记录;真实命令与输出节选如下。

### 23.1 赛季钉死根因与修复

实测证明:`/league/47/standings`(不带 season)渲染的导航链接携带
`?season=2025%2F2026`,`/league/47/matches` 携带 `?season=2026%2F2027`——
每个页面把**自己解析出的默认赛季**传给了 `LeagueNav`,导致一个 tab 的默认值
变成其它 tab 的显式选择。修复:五个页面(含 `wdl-predictions`)改为把
`seasonParam`(用户原始输入)传给 `LeagueNav`,`resolvedSeason`(后端实际
返回值)只用于徽章展示。

新增 `LeagueFixturesResponse`(`league_id/season/available_seasons/...`),
不复用 `/api/v1/matches` 共用的 `MatchListResponse`;赛程默认赛季定义为
"最早一场未开赛比赛所在赛季"而非 `max(Season)`(`backend/queries/matches.py
::default_fixture_season`);四个 v1 端点统一"未知赛季不再 400,静默回退 +
响应体如实标注实际赛季"。概率卡(legacy 端点,`invalid_season`/
`no_wdl_predictions_for_league` 仍 400)改为捕获后渲染诚实空状态而不是
"数据服务暂时不可用"的故障文案。

新增 `SeasonSwitcher` 服务端组件(`<Link>` chip 横排,无 JS 依赖),自动 chip
的 `aria-label` 与可见文本用同一条件门控(`自动选择赛季(当前 2025/2026)`,
仅当处于自动态且已解析出赛季时附带后缀)。

### 23.2 队徽根因与修复

```
curl :8000/api/v1/media/team-crests/fotmob/8402.png?v=... → 200
curl :3000/api/v1/media/team-crests/fotmob/8402.png?v=... → 404(Next.js 404 页)
```
`frontend/next.config.ts` 此前为空,无任何 rewrite。`crest_url` 是后端下发的
**相对路径**,浏览器按 `:3000` 自身 origin 解析,404 后 `TeamBadge` 的
`onError` 换成首字母兜底——视觉上与"没有队徽"完全一样。生产环境靠 Nginx
`location /api/` 同源代理才正常,这个假设只在生产成立。

修复:`next.config.ts` 新增 `/api/v1/media/:path*` → `NEXT_PUBLIC_API_BASE`
的 rewrite(复用 `lib/api-base.ts` 的 `clientApiBase()` 单一真源,未设置该
环境变量时返回空数组,生产不受影响)。未选择"在 TeamBadge 里拼绝对地址":
Studio PNG 导出用 html-to-image 把页面转成图片,跨源 `<img>` 会污染 canvas。

同步五大联赛队徽(139 支球队,分联赛 × 赛季共 31 次真实调用,0 失败):
```
league 47: 2020/2021..2026/2027 共 30 支
league 53: 2020/2021..2025/2026 共 18 支
league 54: 2020/2021..2025/2026 共 18 支
league 55: 2020/2021..2025/2026 共 20 支
league 87: 2020/2021..2025/2026 共 20 支
manifest 总计: 155 entries(16 挪超 + 139 五大联赛),unavailable={}
```
`frontend/e2e/anonymous.spec.ts` 原有两处 `team-badge-fallback` 数量==2 的
断言把这个 bug 当成了预期行为(队徽同步后会失败),已改为断言真实渲染
(`naturalWidth>0`)+ 新增一条同源媒体路由回归测试。

### 23.3 北京时间统一(需求④)

用户明确要求"时间都按照北京开球时间来排序"。实测发现挪超 118 场里 74 场
(63%)的北京自然日 ≠ 当时 UI 显示的 `date_utc`(如
`2026-08-07T17:00:00Z` = 北京 `2026-08-08 01:00`,页面却显示 8月7日)。

- 新增纯函数格式化器(`components/matches/zh.ts`):固定 +8h 算术换算,
  不依赖 Intl/ICU;对 date_only 输入(如英超 2026/2027 曾经的 380 场
  全部 `kickoff_precision='date_only'`)一律返回 `null`,不编造具体时刻。
- `backend/queries/matches.py::list_matches` 的 `ORDER BY` 从
  `CASE WHEN kickoff_at_utc IS NULL THEN 1 ELSE 0 END, ...`(把没有精确
  kickoff 的行整体压到最后)改为 `julianday(COALESCE(kickoff_at_utc, Date))`
  统一时间轴。回归验证:`/api/v1/matches?status=upcoming` 此前会把挪超
  118 场(有精确 kickoff)全部排在英超任何一场(仅有 Date)前面,不论英超
  那场实际早或晚;修复后按真实日期正确交替(...8/29→8/30→9/4→9/5...)。
- `FixtureRounds.tsx` 轮次分组从按轮次号排序改为按组内最早比赛时间排序
  (挪超真实存在一个改期到赛季中段的"第 12 轮",此前排在最前面;现在正确
  出现在第 24 轮与第 25 轮之间)。顺带修复 `Number("")===0` 导致无轮次
  比赛排在第 1 轮之前的问题。`wdl-predictions/page.tsx` 的轮次排序做了
  同样的按最早日期修复(该端点只有 `date`,没有精确 kickoff)。
- `components/matches/LocalTime.tsx`(比赛/预测/赔率语境,16 处调用点)
  改为直接算北京时间,不再需要 `useSyncExternalStore` 水合后再换算(北京
  时间固定偏移,服务端客户端结果恒等)。`OddsTimeline.tsx` 的图表横轴此前
  用浏览器本地时区、与同组件表格的 `LocalTime` 不一致,已统一。
  `components/studio/api.ts` 的 `fmtUtc` 发现真实缺陷:调用方
  `SafeSceneCards.tsx` 一直标注"北京时间 {fmtUtc(...)}",但实现用的是
  浏览器本地时区——标签与实际展示的时区不一致,面向公开发布的导出内容
  (烧录进图片/字幕)标错时区是真实缺陷;已修正实现匹配已有标签。
  `components/trust/LocalTime.tsx`(账户/研究语境:模型评估记录、兑换码
  到期)与 `app/account/page.tsx`/`app/admin/page.tsx` 的本地时区展示
  保持不变,判定为非赛程语境,用户自己的时区是合理选择。
- 新增 `tests/timezone-discipline.test.ts`:仓库级 grep 守卫,新增一处
  `.toLocaleString()`/`new Intl.DateTimeFormat()` 调用如果不在显式登记的
  白名单里(目前只有 admin/account 两个非赛程语境)测试失败。
- `CLAUDE.md` §11.2 由"时间按用户时区显示"改为"面向中文用户默认按北京
  时间(UTC+8)展示比赛/预测/赔率等赛程相关时间戳...账户、运维等非赛程
  语境可按用户本地时区展示"。

顺带真实重新采集英超 2026/2027 精确开球时间(此前全部 380 场为
`date_only`):
```
$ python backend/ingest/ingest_future_fixtures.py --league-id 47 --season 2026/2027
写入 dim_match 完成: 380 行
```
FotMob 现在(赛季临近开赛)已发布精确开球时刻,380/380 变为
`kickoff_precision='exact'`——如实报告,不是编造。

### 23.4 验证(真实命令与输出)

- 后端:`pytest tests/backend` 全绿(exit 0,无 F);三库
  `PRAGMA integrity_check`=ok;`migrate --status` 三库 `pending=none`。
- 前端:`tsc --noEmit` / `eslint .` / `npm run check:api-drift` 全部 0
  问题;`vitest run` 54/54 通过(含新增 `zh.test.ts` 6 项、
  `timezone-discipline.test.ts` 2 项);`npm run build` 通过。
- Playwright 全量(16 用例,4 个 spec 文件):**15 通过,1 失败**
  (`首页匿名可浏览`,卡在第 16 行 `48%` 概率文案不可见——发生在任何
  本轮改动代码路径之前,是种子比赛 kickoff 距今 17 天、超出概率卡 7 天
  展示窗口导致的既存问题,与本轮赛季/队徽/时区改动无关,不在本轮范围内
  修复)。
- 浏览器实测(生产构建):问题①(历史赛季可切换)、②(默认显示未来赛程)、
  ③(五大联赛+挪超队徽真实渲染)、④(挪超/英超时间正确按北京时间排序与
  展示,含跨天场景)均已截图/DOM 核实;Pro 门禁联赛正确隐藏切换器且不
  崩溃;暗色模式下新组件视觉正常。

## 24. 五大联赛 26/27 赛程 + 北欧联赛赛前数据实测 + kbisai 完整赔率序列采集(2026-08-04/05)

真实命令与输出(节选;完整前后台事实与本轮推翻的旧结论见 `docs/data-plan.md`
§2/§4/§7,`docs/data-sources.md` §0.1)。8 个 agent(4 设计 + 4 对抗验证)先对
计划做了逐行核对,推翻了多条前提并发现一个 P0(§24.0),因此实现顺序与最初
设想不同。

### 24.0 P0 闸门:`predict_wdl_future.py` 缺 `League_ID` 谓词(最先修复)

审计发现:`load_future_fixtures` 是 `WHERE Season=? AND status='NotStarted'`,
没有 `League_ID` 谓词;`write_predictions` 的
`DELETE FROM gold_wdl_predictions WHERE season=?` 同样没有。四个新联赛
26/27 赛程一旦落地,该脚本会把非英超比赛也当成预测目标——模型基准参数只用
英超历史拟合,对其它联赛的输出没有统计意义,一旦经 `prediction_register`
流程发布,按 CLAUDE.md §9.1 将永久进入公开预测账本、不可撤回。修复:两处都
加 `league_id` 参数(默认 47),新增 3 条回归测试
(`tests/backend/test_predict_wdl_future.py`)。本机因无 systemd/cron(只有
本地 launchd `poll_wrapper`,`JOBS` 不含 `model_predict`)未实际触发,但代码
层面漏洞真实存在,已在任何新联赛赛程落地**之前**修复并验证:

```
$ .venv/bin/python -m pytest tests/backend/test_predict_wdl_future.py -q
...                                                                      [100%]
$ .venv/bin/python -m pytest tests/backend -q   # 全量回归,确认未破坏既有功能
........ (全绿)
```

### 24.1 赛前 `status` 判定 bug(唯一真 bug)+ 赛季身份校验

审计推翻两个前提:`parse_match_dim` 其实已经在写 kickoff 三列(11 份仓库内
真实赛前 payload 验证 11/11 正确),不需要修;但真实赛前 `header.status`
没有 `reason` 键,旧代码 `status_obj.get("reason",{}).get("short","Unknown")`
因此对任何赛前比赛恒返回 `'Unknown'`,会让该场比赛被
`poll_windows.upcoming_precise_matches` 永久排除出赔率轮询窗口(17181 个
真实 status 对象统计确认)。修复为共享函数 `derive_match_status()`,
`ingest_future_fixtures.py::_status_from_fixture` 与 `parse_match_dim`
现在共用同一实现,不会再各写一份互相漂移。

`ingest_future_fixtures.py` 另外补了赛季身份校验(照抄
`backfill_season_tables._verify_identity`):`league_matches()` 响应的
`details.id`/`selectedSeason` 与请求不一致时抛 `SeasonIdentityError`,
拒绝落库,防止来源尚未发布目标赛季时静默返回旧赛季数据被当成新赛季写入。

```
$ .venv/bin/python -m pytest tests/backend/test_fotmob_prematch_status.py \
    tests/backend/test_ingest_future_fixtures_identity.py -q
....... .....                                                           [100%]
```

### 24.2 五大联赛 26/27 + 北欧联赛 2026 赛程回填(6 次真实请求)

备份(`bash deploy/scripts/backup_sqlite.sh`)后执行:

```
$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 87 --season 2026/2027
抓到未开赛场次: 380 (league_id=87, season=2026/2027)
写入 dim_match 完成: 380 行
历史 bronze 里从未出现过的新球队(3 支):Racing Santander / Deportivo A Coruña / Malaga

$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 55 --season 2026/2027
抓到未开赛场次: 380 (league_id=55, season=2026/2027)

$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 54 --season 2026/2027
抓到未开赛场次: 306 (league_id=54, season=2026/2027)
历史 bronze 里从未出现过的新球队(2 支):Elversberg / Paderborn

$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 53 --season 2026/2027
抓到未开赛场次: 306 (league_id=53, season=2026/2027)
历史 bronze 里从未出现过的新球队(1 支):Le Mans

$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 59 --season 2026
抓到未开赛场次: 118 (league_id=59, season=2026)   # 幂等重跑,行数不变

$ .venv/bin/python backend/ingest/ingest_future_fixtures.py --league-id 67 --season 2026
抓到未开赛场次: 121 (league_id=67, season=2026)   # 瑞超首次接入
历史 bronze 里从未出现过的新球队(16 支):全部 16 支瑞超球队
```

验证:六个联赛 `status` 只出现 `NotStarted`,`kickoff_precision` 全部
`exact`,`Season` 字面量精确匹配;P0 闸门复核
`SELECT DISTINCT league_id FROM gold_wdl_predictions WHERE season='2026/2027'`
只返回 `{47}`,确认新联赛落地未触发跨联赛预测。

### 24.3 挪超周末 7 场裁判/天气/伤停真实采集

`poll_fotmob_snapshots.py` 新增 `--write-match-details`:复用同一份已抓到的
`match_details` payload 定向 `UPDATE dim_match` 的
`Referee`/`Temperature`/`Wind_Speed` 三列(COALESCE 式,新值非空才覆盖,
不碰其它 16 列),同时照常写 `bronze_fm_lineup_snap`/`bronze_fm_sideline_snap`——
不跑 `ingest_match.py`(会把预测阵容当确认阵容写进 `fact_match_lineup`)。

```
$ .venv/bin/python -m backend.cli.poll_fotmob_snapshots --write-match-details \
    --match-id 5104970 --match-id 5104971 --match-id 5104972 --match-id 5104973 \
    --match-id 5104974 --match-id 5104975 --match-id 5104976
[poll_fotmob_snapshots] mode=match_ids now=2026-08-04T16:16:00Z
  窗口候选: 0,本轮抓取: 7,未到期跳过: 0
  快照: 落库 21 条,hash 未变跳过 0 条
  裁判/天气: 7/7 场写入了至少一个非空字段

# 复跑验证幂等
  快照: 落库 0 条,hash 未变跳过 21 条
  裁判/天气: 7/7 场写入了至少一个非空字段
```

真实结果(7/7 场全部拿到真实裁判姓名与温度/风速,如
`Joergen Haugen`/`18°C`/`4`;`bronze_fm_sideline_snap` 14 行,伤停原因
分布只出现 `injury`/`suspension` 两种真实值;`bronze_fm_lineup_snap` 7
行)。`source_health` 两次运行均 `ok=1`。

### 24.4 五处未过滤 `dim_match_xref` 读取加 `provider='nowgoal'`

`routes_public.py`(`/matches/{id}/odds` 的 xref 查询 + `content=odds` 的
JOIN)、`studio/bundle.py:253`、`content_pipeline.py`(两处)——全部下游查
`bronze_ng_odds_snap`(nowgoal 形状的表),不加过滤在 kbisai xref 与 nowgoal
xref 同时存在时可能返回错误 provider 的行。回归测试**先插 kbisai 行再插
nowgoal 行**(验证过 `EXPLAIN QUERY PLAN` 是 `SCAN`,`.fetchone()` 返回
rowid 较小者,插入顺序颠倒会产生假绿)。

本轮上线后偶然获得了一次真实生产验证:生产 launchd 后台 `nowgoal_snapshot`
轮询独立为 Match_ID=5104970 创建了一条真实 `nowgoal` xref
(`provider_match_id='2912862'`),与本轮写入的 `kbisai` xref
(`provider_match_id='4467576'`)同时存在于同一 `fotmob_match_id`。
`curl http://localhost:8000/api/v1/matches/5104970/odds` 与直接调用
`build_analysis_bundle` 均正确返回 nowgoal 的真实数据(Sbobet/Bet365 两家
公司、1x2/ah/ou 三个市场),未被 kbisai 行污染——修复在真实并发场景下验证
通过,不是仅靠单测证明。

### 24.5 kbisai 协议层:AES 解密 + 4 个 fetch 函数(`cryptography`)

`requirements.txt` 加 `cryptography`,新模块 `backend/providers/kbisai_odds.py`
实现 AES-256-CBC + ZeroPadding 解密(密钥/IV 是站点公开 JS bundle 里的静态
常量,非账号凭证,不放 `.env`)+ 4 个 fetch 函数
(`fetch_competition_category`/`fetch_all_companies`/`fetch_future_matches`/
`fetch_match_all_odds`)。用 `cryptography` 重放探测阶段保存的真实密文,
逐字节核对:

```
$ .venv/bin/python3 -c "... cryptography 解密 raw-comp-category.json ..."
MATCHES SAVED DECRYPTED PAYLOAD: True
```

### 24.6 kbisai 联赛 id 发现(真实网络请求)

```
$ .venv/bin/python3 -c "... fetch_competition_category() ..."
LIVE fetch_competition_category() succeeded, categorys count: 7
```

解析真实响应树:英超="英超"→**82**(三处路径一致,无歧义)。瑞典超候选有
三个命名相近的联赛(184/185/186),不能凭名字判断,用真实赛程交叉核实:

```
--- competitionId=184 (瑞典超): 8 matches this weekend ---
  2026-08-08T13:00:00Z 奥尔格里特 vs AIK索尔纳     ← 与 FotMob Örgryte vs AIK 完全一致
  2026-08-08T15:30:00Z 米亚尔比 vs 埃尔夫斯堡        ← Mjällby vs Elfsborg
  ...(8/8 场开球时刻精确到秒、队名音译全部对上 FotMob league_id=67 真实赛程)
--- competitionId=185 (瑞典超甲): 7 matches ---(完全不同的球队,如厄斯特松德/松兹瓦尔)
--- competitionId=186 (瑞典甲): 17 matches ---(完全不同的球队)
```

8/8 场精确匹配确认 **184=瑞典超(Allsvenskan)**,185/186 是完全不同级别的
联赛,排除。挪超=201 沿用上一轮验证结果。

### 24.7 迁移 `odds/0003_kbisai_odds_points.sql`

新表 `bronze_kbisai_odds_point`(一行一个真实变化点,`provider` 列从第一天
就有,UNIQUE 含 `point_hash`+`dup_ordinal` 保留"同 changeTime 不同赔率"与
"字节相同重复条目"两种真实边界情况,AH/OU 盘口线 CHECK 强制非空)。备份→
临时库 dry-run(含功能性验证:UNIQUE/CHECK/append-only 触发器全部按预期工作)
→生产库应用:

```
$ bash deploy/scripts/backup_sqlite.sh
== 备份完成: data/backups/20260804T164130Z(3 库,integrity_check 全部 ok)==

$ .venv/bin/python -m backend.db.migrate --db odds
[odds] applied 0003_kbisai_odds_points.sql
[odds] up to date: 3 applied (+1 new)

$ sqlite3 data/odds.db "PRAGMA integrity_check;"
ok
```

### 24.8 kbisai↔FotMob 身份解析 + 完整赔率序列采集(25 场真实目标)

`backend/ingest/kbisai_match_resolution.py`(独立于 `entity_resolution.py`
的实现,kickoff 精确匹配为主、CJK 队名为消歧/方向确认信号,fail-closed 不
猜测)+ `backend/cli/poll_kbisai_odds.py`(消费已写入的 xref,不在采集时
重新做身份解析)。

目标:挪超周末 7 场 + 瑞超周末 8 场 + 英超第一轮 10 场 = 25 场。真实身份
解析结果(kickoff 候选池:挪超 7/瑞超 8/英超 10,均来自真实
`futureMatch_b` 请求,英超验证了 T+17~20 天窗口此前 UNVERIFIED、本轮确认
可用):

```
matched: 16/25
  eliteserien 7/7  全部 needs_review(diff=0,但挪超无别名数据无法独立确认主客方向)
  allsvenskan 2/8  needs_review(Örgryte vs AIK、Mjällby vs Elfsborg——kickoff 唯一)
  epl         7/10 auto_ok(CJK 别名双向确认)

UNMATCHED (9) — 均为诚实 fail-closed,非 bug:
  allsvenskan ×6  2 candidates within kickoff tolerance and no alias data (3 对同刻开球)
  epl         ×3  kickoff matched a single candidate but team names disagree with alias
                  data in both directions(kbisai 用全称"曼彻斯特联"/"曼彻斯特城",
                  别名表只有简称"曼联"/"曼城")
```

16 场写入 `dim_match_xref`(provider='kbisai'),真实赔率采集:

```
$ .venv/bin/python -m backend.cli.poll_kbisai_odds \
    --match-id 5104970 ... (7 挪超) --match-id 5107559 --match-id 5107560 (2 瑞超) \
    --match-id 5795363 ... (7 英超) \
    --companies 2,7,22 --markets eu,asia,bs --max-requests 60
{
 "status": "OK", "requests_made": 49, "requests_planned": 49,
 "totals": {"duplicate": 0, "inserted": 320, "rejected_constraint": 0}
}

# 幂等复跑
{"totals": {"duplicate": 320, "inserted": 0, "rejected_constraint": 0}}
```

验收(全部真实 SQL,生产库):

```sql
-- handicap_line 非空率(ah/ou 必须 100%)
market=ah: 119/119   market=ou: 101/101   market=1x2 含 handicap_line 的行: 0

-- 三目标公司每场每市场到位情况(反连接找缺失组合)
(空结果 = 挪超7场+瑞超2场 × 3市场 × 3公司 全部到位,无缺失组合)

-- statusId × market_phase 交叉表
statusId=1, market_phase='pre_match': 320(全部)
```

英超第一轮(T-17~20d):10 场比赛本身可发现,但三目标公司
(36\*/澳\*/平\*)截至采集时点均未发布任何赔率——直接验证过其它公司(如
5/6/11/14/15/20)对同一场比赛确有真实数据,排除"接口坏了"的可能性,如实
记 0 行,未改用其它公司替代、未扩大采集范围。

### 24.9 前后端全量回归 + 浏览器实测

```
$ .venv/bin/python -m pytest tests/backend -q
........................................................................ [100%]
（本轮新增 8 个测试文件,合计新增约 130 个测试用例，全部通过）

$ npm run typecheck && npm run lint && npm run test && npm run check:api-drift && npm run build
（全部通过；vitest 54/54；api-types.ts 与 openapi.json 无漂移——本轮未改任何
  Pydantic 响应模型；build 14 个路由全部编译成功）
```

浏览器实测(本地 dev server):`/league/87/matches`(西甲,Pro 门禁)正确显示
"该联赛数据为 Pro 会员内容"付费墙(证明路由与数据管道打通、且正确按设计
门禁);`/league/67/matches`(瑞典超,免费层)真实渲染 121 场赛程,含正确的
北京时间换算;`/league/47/matches`(英超第一轮)真实渲染 10 场赛程且赛季
自动选中 2026/2027;`/matches/5104970` 详情页验证了 §24.4 描述的真实
provider 隔离场景。

### 24.10 明确未做(超出本轮范围,已在 `docs/data-plan.md` §5 登记后续计划)

- 挪超/瑞超已完赛比赛的比赛级数据(用户已选"只要赛程行");
- `extract_lineup_snapshot` 增加 `lineup_type` 字段(会改变 payload hash,
  产生一条"来源并未发生的变化"污染 silver 层,未获批准);
- `connect_rw` 增加 `recursive_triggers=ON`(影响全部三库的写路径,需独立
  评估);
- kbisai 接入 worker 自动链路(本轮只有手动可重跑的 CLI);
- 扩大 `dim_team_alias` 覆盖以提高 kbisai 身份解析命中率(西甲/意甲/法甲/
  德甲/挪超/瑞超的中文别名目前均为 0 覆盖,或每队仅 1 个别名不含全称变体);
- 探测 kbisai 的封禁阈值;
- 前端展示裁判/天气/伤停数据(本轮已采集入库,但没有任何页面组件读取)。

## 25. 历史赔率旧资产整合 + 已完赛比赛精确 kickoff 全量回填(2026-08-06)

前情:§24 之后的两轮 NowGoal 历史回填(mix-history/euro-history 端点,经
住宅代理)已把五大联赛 2,156 场"数据库缺口"比赛的完整赛前赔率时间线抓齐
(734,812 条 bronze 快照,中位数 326 观测点/场,0 失败)并入库
`bronze_ng_odds_snap` + `dim_match_xref`(2,156 行) + `silver_odds_moves`
(1,477,750 行,本项目首次产出)。本轮在此之上整合三份本机旧资产并回填 kickoff。

### 25.1 旧资产审计结论(全部为真实查询/多子代理独立复核)

- Asset A `miaomiaodi.vip/backend/odds/match_odds_data*.json`:8,294 场唯一
  fotmob_id,仅 Bet365 初盘+临场两点,无任何时间戳;与已入库的 2,156 场
  **零重叠**(互为补集,当初 gap 清单就是按"不在 Asset A"生成的)。
- Asset B:两个 repo 内的 `football_uk.db` 都是 **0 字节空文件**;真实数据在
  `~/Desktop/Football_Data_Lake/football_uk.db`(41,165 行 silver_match_odds,
  6,790 场,2023/24 起,亦为 opening/closing 两点,FotMob match_id 直接可 join)。
- 旧爬虫(titan_odds_spider.py:190)用的是 `soccerajax?type=14` 端点——该端点
  本身只返回 f/l 两组,**磁盘上不存在可重解析的完整历史**,想要 2021-2023 完整
  走势只能重爬(已量化:两季约 9 小时)。
- 两处方向性缺陷(均用悬殊比分实证):Asset A 19.6%(1,587/8,088)记录
  match_name 主客反转,其 1x2 按 match_name 方向、AH 按 FotMob 方向
  (内部一致性交叉表:正常记录 5580/5580=100.0% vs 反转记录 2/1388=0.1%);
  Asset B footballdata 源 AH 线符号相反((line>0)==(主队赢) 仅 24.5%)。

### 25.2 入库(migration odds/0004 + backend/cli/ingest_legacy_odds.py)

新表 `bronze_legacy_odds_summary`(不混入 bronze_ng_odds_snap:旧资产无
observed_at,§6.2 禁止伪装)。修正规则:反转记录只交换 1x2 的 home/away;
footballdata 的 ah 线取反;OU 恒不动。

```
$ python3 -m backend.cli.ingest_legacy_odds --live --asset-a-dir ... --asset-b-db ...
rows_inserted=74,863  distinct_matches=8,336  orientation_fixed_rows=13,015
review_count=150(match_name 两种顺序都对不上,写 review 文件,不带病入库)
重跑 rows_inserted=0(幂等)  PRAGMA integrity_check → ok
```

方向性验收(真实比分):AH 线指向 ≥4 球悬殊赢家 460/493=93.3%;修正后
asset_a 内部一致 6919/6921=100.0%;A/B 独立来源同场 AH 线 4,503/4,503 全部
相差 ≤0.5 球。覆盖:五大联赛已完赛 10,735 场中 **10,492 场=97.7%**
(完整时间线 2,156 + 两点摘要 8,336;仍无覆盖 243)。

API:`/api/v1/matches/{id}/odds` 新增 `coverage_tier`
(full_timeline/open_close_only),两点摘要走 `summary_points` 字段,
entitlement 投影为免费/Pro 仅 latest、Premium 含 initial;前端
OddsTimeline.tsx 对 open_close_only 只出表格不画走势图。OpenAPI 已重导出,
api-types 无漂移。浏览器实测 /matches/3609929(布伦特福德 2-0 阿森纳,
2021/22 揭幕战):表格正确渲染,方向正确(阿森纳客队热门 2.05 / 线 -0.25)。

### 25.3 已完赛比赛精确 kickoff 全量回填(backend/cli/backfill_kickoff_from_fotmob.py)

FotMob leagues 端点对已完赛比赛同样返回 utcTime(磁盘已有原始响应先行实证:
PL 2024-25 完赛 380/380 带 utcTime 且与 dim_match 零日期不一致)。按
(League_ID, Season) 30 个分区、每分区一个请求:

```
$ python3 -m backend.cli.backfill_kickoff_from_fotmob --live
partitions_processed=30  total_updated=10,735  missing=0  date_mismatch=0  失败=0
$ sqlite3 data/allwin.db "SELECT kickoff_precision, kickoff_source, COUNT(*) ..."
exact|fotmob:fixtures|1991
exact|fotmob:leagues|10735        ← 此前 10,735 行全为 date_only/NULL
integrity_check → ok;重跑 dry-run 分区=0(幂等)
```

交叉校验(不入库):与 NowGoal 侧独立开球时间(旧 mapping 文件,排除 23:59
哨兵)比对,两来源皆有的 7,848 场中相差 >30 分钟仅 1 场(0.01%)——同时验证
了 kickoff 值与实体映射。旧 mapping 的 NowGoal 二手 kickoff 方案弃用,
比赛详情页"开球时间只精确到比赛日"的不确定性提示随之消失(浏览器实测)。

已知限制留痕:ingest_future_fixtures.py 的 Finish-skip 行为已加注释说明
历史回填由本 CLI 负责。

### 25.4 验证汇总

```
pytest tests/backend → 1008 passed(其中本轮新增 18:test_ingest_legacy_odds 10
  + test_backfill_kickoff 8;运行中一度 4 个 e2e-seed 失败为环境性——kickoff
  回填正在写 allwin.db 导致 WAL 非空、seeder 按设计拒绝拷贝,回填完成
  checkpoint 后全绿)
frontend: eslint 通过 / vitest 58 passed / tsc --noEmit 通过 / next build 成功 /
  check:api-drift 无漂移
两库 integrity_check ok;三个写库操作均有带时间戳备份(migration 前、
legacy ingest 前、kickoff 回填前)
```

### 25.5 本轮明确未做

- 243 场仍无任何赔率(113 场两侧来源均无 + 130 场 review);
- 2021/22、2022/23 完整走势重爬(独立决策,约 9 小时,等页面用起来再定);
- 150 条 review 记录的人工/别名对齐;
- J1/K1/荷甲赔率已在 Asset B 中存在(838/490/476 场)但对应 FotMob base data
  未 ingest,不属于本轮范围(扩联赛依赖顺序见 data-plan.md)。

## 26. J1/韩K联/澳超三联赛接入:FotMob 全量 ingest + 网站上线 + 中文名(2026-08-07)

前情:§25.5 曾登记"J1/K1 赔率已在旧资产中存在但 FotMob base data 未 ingest"。
另经真实核查修正一处旧文档错误:所谓第三个联赛不是荷甲——旧库
`~/Desktop/Football_Data_Lake/football_uk.db` 中 476 场的 League_ID=113 实为
**澳超 A-League**(球队为阿德莱德联等,FotMob 113;荷甲 57 在旧库仅 9 场且 0 场有赔率)。

### 26.1 旧库赔率复核(NowGoal 实爬对照)

- 旧库 `silver_match_odds` 标 `source='footballdata'` 的数据**实为 NowGoal 数据**:
  抽样 10 场(跨三联赛、2023–2026)从 NowGoal 实爬对照,1x2/AH/OU × 开盘/收盘
  共 60 组数值**全部精确一致**(NowGoal 历史 AH/OU 为香港赔率,+1 换算后对比)。
- NowGoal 内部联赛 id(season archive 路径发现并验证):J1=25、K1=15、澳超=273。
- 旧库 30 条 OU `raw_line` 缺失记录已从 NowGoal archive 补齐(开盘+收盘全量,
  数据在 /tmp 复核产物中,**未写回旧库**——旧库属外部项目)。

### 26.2 FotMob 全量 ingest(真实抓取,`backend/ingest/ingest_league.py`)

三进程并行、零人工间隔(ThorData 轮换代理),9 个联赛-赛季:

```
J1(223):  2024=380/380  2025=380/380  2026(进行中)=197/197  → 960 场
韩K联(9080): 2024=227/228+重试1  2025=228/228  2026=126/126  → 582 场
澳超(113): 23/24=169/169  24/25=176/176  25/26=163/163      → 508 场
合计 2,050 场,全部 status=Finish、kickoff_precision=exact
比赛级明细:player_stats 63,937 / shotmap 53,810 / events 35,117 / team_stats 12,320
```

唯一失败场次暴露真实解析 bug 并已修复:`fotmob_client.py`
`parse_player_stats_records` 对 `content["lineup"]` 为显式 `None`(FotMob 该场
无阵容,实例 4404667)时崩溃,改 `(content.get("lineup") or {})` 兜底;重试成功。

### 26.3 网站接入

- `backend/queries/leagues.py` LEAGUE_META 增 223 日职联/9080 韩K联/113 澳超,
  entitlement 均为 `league:lottery`(与挪超/瑞超同档,免费可见;platform 0004
  已有该 entitlement,无需新迁移)。
- 前端 `zh.ts`:LEAGUE_ZH 增三联赛;ENTITLEMENT_ZH 补 `league:lottery` 文案。
- 浏览器实测:/leagues 十联赛齐全;/league/223/matches?season=2025 380 行
  全部可点、赛季切换器 2024/2025/2026;详情页(大阪钢巴 2-5 大阪樱花)证据/
  近期战绩/xG 真实渲染,赔率区如实显示"暂无已验证的赔率数据映射"。

### 26.4 球队中文名(53 队,Qwen+联网交叉核验)

与 §24 同款管线:5 批并行,qwen-max(enable_search) 译 → 独立 WebSearch 核验
→ 汇总复核。53/53 全部落 `dim_team_i18n`(source=`qwen_max_websearch_verified`,
总数 145→198),零撞名。有据可查的改名/易错点:蔚山HD(2023-12 弃"蔚山现代")、
济州SK(2025-01 弃"济州联")、奥克兰FC(≠奥克兰城,后者为另一俱乐部)、
磐田喜悦/町田泽维亚(Qwen 首答错误,被搜索核验覆盖)。
产物:`runtime/research/team-i18n-jka/`。

### 26.5 验证汇总

```
pytest tests/backend → 1023 passed
frontend: tsc 通过 / vitest 73 passed / eslint 通过 / next build 成功 /
  check:api-drift 无漂移(本轮无 schema 变更)
allwin.db PRAGMA integrity_check → ok
```

### 26.6 本轮明确未做

- 三联赛赔率尚未入 odds.db(旧库两点摘要 + NowGoal archive 完整历史均可作
  来源,需先建 dim_match_xref 映射;详情页赔率区当前如实为空);
- ~~fact_league_table 三个空档~~已闭合:根因是分组赛制赛季用 `data.tables[]`
  复合结构(K1 完赛季=常规+冠军/保级组,J1 2026=赛制切换过渡期东西分组),
  旧解析器只认 `data.table`。`parse_league_table` 已支持复合结构(总表按归一化
  联赛名识别落 `all`,分组落 `all:组名`;名称归一化是必须的——真实响应存在
  "K League 1" vs "K-League 1" 连字符差异)。重跑后 K1 2024/2025 standings
  真实可查(蔚山HD 61 分/全北现代 71 分夺冠,与真实赛果一致);J1 2026 无总表
  是赛制事实,standings 诚实为空。新增离线测试
  `test_parse_league_table_composite.py`(3 用例);
- silver 5 表、模型特征、预测对三联赛均未构建(与挪超/瑞超同状态)。

## 27. J/K/A 三联赛历史赔率复核与导入(2026-08-07)

§26.6 第一条闭合。全量复核(`runtime/research/jka-odds-ingest/audit.py`,
报告 audit-report.json)后导入 `bronze_legacy_odds_summary`。

### 27.1 复核结论(全部真实查询/实爬)

- 旧库 `football_uk.db` J/K/A 子集 1,810 场、10,953 行、全部 Bet365;
  fotmob_match_id 1810/1810 存在于 all-win dim_match 且联赛一致、
  主客方向 token 比对零反转;数值全量过界(赔率下限/overround/线步进)0 违例。
- **来源定性**:标签 footballdata、数据实为 NowGoal——11 场跨联赛跨年份实爬
  对照 66 组数值全部精确一致(含 5130400 仲裁:archive 收盘=fd 值,
  ≠watcher 盘中快照)。与同一旧表的五大联赛子集(真 football-data.co.uk,
  Asset B 入库时 AH 线取反)是**两个不同真实来源**。
- **AH 线符号**:同场同期 1x2 热门方向交叉验证 2,834 agree / 1 borderline
  (4965624 开盘近平半盘)→ 本子集符号 = canonical(line>0=主队让球),
  入库**不取反**。
- 32 场 2026 年 4 月尾部比赛旧数据不可用(开盘 OU 无线 30 场、收盘只有
  watcher 过时快照、5139869 无行、4410075 的 1x2 开盘为脏数据:主胜 1.5 配
  平手盘),改用 NowGoal season archive 实爬两点摘要;4410075 archive 收盘为
  空哨兵(赛前封盘),只入开盘 ah/ou,诚实缺失。

### 27.2 导入(migration odds/0005 + backend/cli/ingest_jka_legacy_odds.py)

- 0005 重建表扩 source CHECK(+`football_uk_jka`/`nowgoal_archive_refetch`),
  既有 74,863 行保留;迁移前后各有带时间戳备份。
- 导入 10,856 行 = fd 类 1,778 场 × 6 + refetch 类 32 场 188 行;
  watcher 行 0 导入;重跑幂等(INSERT OR IGNORE 撞唯一键);integrity_check ok。
- 覆盖:J/K/A 已完赛 2,050 场中 **1,810 场 = 88.3%** 有两点赔率
  (J1 841+22/960、K1 493+9/582、澳超 476+9/508 —— 约数,精确口径见
  odds_coverage_sets);其余 240 场旧库无源(可由 NowGoal archive 补,未做)。

### 27.3 前端验证(浏览器实测)

/matches/4690961(大阪钢巴 2-5 大阪樱花):赔率时间轴区渲染两点存档提示 +
1x2/让球/大小球表格,免费层只见临场一点,无走势图、无伪造时间戳;
列表徽标"赔率:初盘与临场";content=odds 筛选含 K1 2024 全部 228 场;
bundle odds_coverage_tier=open_close_only。

### 27.4 验证汇总

```
pytest tests/backend → 1030 passed(+4:test_ingest_jka_legacy_odds
  migration 保留/闸门/refetch 哨兵/幂等)
frontend vitest 73 passed / tsc 通过(无前端代码改动,复用既有
  open_close_only 渲染路径)
odds.db integrity_check ok;备份×2(0005 前、ingest 前)
```

### 27.5 本轮明确未做

- 240 场无源比赛的 archive 补爬;
- J/K/A 完整走势时间线(NowGoal archive 有逐时间戳历史,约 1,800 场×2 请求,
  独立决策);
- 三联赛 silver/模型/预测(同 §26.6)。

## 28. 补齐赔率缺口至 98.4% + J1 跨年新赛季接入(2026-08-07)

### 28.1 240 场缺口复核结论

逐场核对后确认:旧库 `football_uk.db` 是某个历史时点的静态快照(约
2026-04-15~19 截断),此后打的比赛它完全不知道,不是抓取遗漏。用同一套
NowGoal season archive 方法(§27 已验证 66 组数值精确一致)重新匹配:

- 208 场命中并实爬成功(0 失败),写入 1,248 行(`nowgoal_archive_refetch`);
- 修正一处脚本 bug:澳超赛季在 all-win 库里是斜杠格式(`2023/2024`),
  archive key 是连字符格式(`2023-2024`),未转换直接导致 52 场假性未命中,
  修复后其中 20 场找回;
- **剩余 32 场(1.6%)是真实来源边界**,非可修复缺口:12 场澳超
  2023/24、2024/25、2025/26 三个赛季末的**季后赛/总决赛轮**、20 场 J1
  2026 赛季末**冠军系列赛**(5/30~6/6,两回合对阵)。NowGoal season
  archive 只收录常规赛轮次,不含附加赛;已扫描过的联赛目录里也没有找到
  独立的"附加赛"归档 id。

覆盖率:2,050 场已完赛比赛中 **2,018 场 = 98.4%** 有赔率(此前 §27 为 88.3%)。

### 28.2 J1 跨年新赛季(2026/2027)接入

FotMob 已切换 J. League 默认赛季为 `2026/2027`(`allAvailableSeasons` 同时
广告 `2026` 与 `2026/2027`,与 `docs/current-state.md` §20.11 此前记录的
`SEASON_REGIME_TRANSITION` 观察一致——本轮验证该过渡已真实落地)。
`backend/ingest/ingest_future_fixtures.py --league-id 223 --season 2026/2027`
真实抓取:380 场未开赛赛程,首轮 2026-08-07(本周末)开赛,末轮
2027-06-06;全部 `exact` kickoff(`fotmob:fixtures`);20 支球队均已有
§26.4 的中文名,零新增未翻译球队。

浏览器/API 验证:`/api/v1/leagues/223/fixtures?season=2026/2027` 380 场,
`available_seasons` 含 `2026/2027`,`current_season` 自动切至新赛季。

### 28.3 验证汇总

```
pytest tests/backend → 1030 passed(与 §27 相同用例,新增数据未改变契约)
odds.db integrity_check → ok
```

### 28.4 本轮明确未做

- 剩余 32 场附加赛赔率(来源边界,当前已知方法无法覆盖);
- J1 新赛季(2026/2027)全为未开赛,尚无比分/统计/赔率,等真实开赛后交给
  `ingest_league.py` 常规流程回填。

## 29. J1 首轮实时赔率接入生产轮询链路 + 首页一次性置顶(2026-08-07)

### 29.1 J1 首轮(2026/2027 赛季)赔率——走生产轮询链路,非历史回填

与 §26-28 的历史两点摘要不同,首轮 10 场比赛尚未开赛,正确路径是现有生产
链路(`backend/cli/poll_nowgoal.py` + `entity_resolution.py`),不是
`bronze_legacy_odds_summary`。

- `seed_team_aliases` 早前已把 J1/K1/澳超球队别名种进 `dim_team_alias`
  (446 条,含本轮新增前状态);
- `poll_nowgoal --date` 对 2026-08-07/08/09 三天真实轮询,首轮 10 场初次
  6/10 auto_ok,3 场 needs_review(NowGoal 侧队名拼写变体未入别名表,
  正确 fail-closed 未猜测)、1 场未解析;
- 补 5 条真实观测到的 NowGoal 队名别名(横滨水手→"yokohama marinos"、
  冈山绿雉→"fagiano okayama"、广岛三箭→"hiroshima sanfrecce"、
  千叶市原→"jef united ichihara chiba"、京都不死鸟→"kyoto sanga",均为
  当次抓日程时的真实原文,非合成变体),清空对应 needs_review 记录重新解析
  → **10/10 全部 auto_ok**,`bronze_ng_odds_snap` 落 64 行真实 pre_match
  快照,`observed_at` 为真实抓取时刻。
- API 验证:`odds:history_full` 视角 `available=true`、
  `coverage_tier=full_timeline`;免费/Pro 视角因刚观测(<1 小时)被
  §8.2 延迟规则正确隐藏,详情页如实显示"已建立映射,但暂无满足口径的
  赔率快照(免费层延迟 1 小时)"——这是设计行为,不是缺陷。

### 29.2 首页一次性置顶(`frontend/lib/homepage.ts` HOMEPAGE_FEATURE_EVENT)

明确一次性活动编排,非通用运营置顶框架(过期后应直接删除,不沉淀为常驻
配置)。规则:首轮最早开球的横滨水手 vs 鹿岛鹿角(match_id 5803519,
2026-08-07T10:25:00Z,数据库真实查询得出)置顶为首页"今日重点";开球
2 小时后自动切换为瑞典超(67)/挪威超(59)当时离现在最近的一场
(`selectFeaturedOverride`,按 `|kickoff-now|` 最小选取,不区分未来/进行中)。

实现:`getHomePageData` 额外并行拉取瑞典超/挪超各 3 场候选(默认 8 场
"最近开球"池不保证含北欧联赛),覆盖判定优先于默认算法,`analysis` bundle
按最终 featured 重新请求。浏览器实测:当前(置顶窗口内)首页正确显示
"今日重点 · 日职联 · 横滨水手 vs 鹿岛鹿角"。

### 29.3 验证汇总

```
pytest tests/backend → 1030 passed(数据变更未改契约,无新增用例)
frontend: vitest 78 passed(+9:selectFeaturedOverride 5 + kickoff 前/窗口内/
  窗口后北欧回退/无候选兜底/置顶场次不在候选池) / tsc 通过 / eslint 通过 /
  next build 成功
odds.db integrity_check → ok
```

### 29.4 本轮明确未做

- 3 场 needs_review 之外、10 场里当轮仍有个别公司覆盖不全(部分场次
  Pinnacle/Macauslot 未必都命中,按 DEFAULT_TARGET_CIDS 优先级正常降级);
- 第 2 轮起赛程尚未轮询(生产链路依赖后续 worker/`--due` 常规调度,
  本轮只手工跑了首轮 3 个日期);
- 本轮对 208 场缺口回填与 5 条别名新增均为直接脚本写库,未先手工
  `.backup`(均为 INSERT OR IGNORE 幂等操作,写后 integrity_check 已核对
  ok,风险已知可控,但流程上少了惯例备份步骤,如实记录)。

## 30. 挪超(59)/瑞典超(67)2026 赛季已完赛比赛 + 历史赔率回补(2026-08-08)

§12/§18 只接入了两个联赛的**未来赛程**(`dim_match` 全部 `NotStarted`),
已完赛部分、球队中文名、历史赔率均是缺口。本轮补齐,前端/API 接线本身
(`LEAGUE_META`、`league:lottery`、`LEAGUE_ZH`)在此前已完成,一行未改。

### 30.1 FotMob 已完赛比赛 + 队徽(`backend/ingest/ingest_league.py`,无新代码)

```
挪超(59):  --no-skip-existing --force-season-tables → 123/123 成功,0 失败
瑞典超(67): --no-skip-existing --force-season-tables → 119/119 成功,0 失败
```
`--no-skip-existing` 是必需的:两联赛各有若干场已开踢但仍标 `NotStarted`
的行(旧的未来赛程写入),默认的 `--skip-existing` 会把它们永久跳过。
`--force-season-tables` 刷新了陈旧的 `fact_league_table`(挪超此前 80 行
`played=15-16` 已过期;瑞典超此前 0 行)。
队徽:`sync_team_crests --league-id 67` 补齐 16/16(挪超此前已有 16/16)。

### 30.2 32 支球队中文名双重验证(qwen-max + 独立 WebSearch 交叉核对)

新建通用 `backend/i18n/seed_team_names.py`(artifact 驱动、fail-closed 门禁),
**删除**未核验的 `backend/i18n/seed_allsvenskan_teams.py`(其自身 docstring
承认"单人经验判断……没有走三票核验",从未在生产库跑过)。

双重验证过程中独立发现并修正 qwen 的两处真实幻觉(如实记录,不是道听途说):
- `Start`(IK Start,挪超)qwen 误译成"斯塔贝克"——那其实是挪超**另一支
  完全不同的球队** Stabæk 的中文名。WebSearch 多个真实赛事页面确认
  IK Start 中文名为"斯达"。
- `Örgryte`(瑞典超)qwen 误译成"厄勒布鲁"——那其实是瑞典**另一个城市/
  球队** Örebro 的中文名。WebSearch 确认 Örgryte 中文名为"奥尔格里特"。

另有 6 处与既有(未核验)`seed_allsvenskan_teams.py` 版本不同,按多数信源修正
(如 `Degerfors` 代格福什 not 迪格弗斯、`Häcken` 赫根 not 海肯、
`Brommapojkarna` 布洛马波卡纳 not 布罗马普卡纳)。产物:
`runtime/research/team-i18n-nordic/final-results.json`(32 行,含
method/reasoning 字段)。写入结果:32/32,`source=qwen_max_websearch_verified`,
零撞名、零孤儿 team_id(seeder 自身门禁校验通过)。

### 30.3 NowGoal 历史赔率回补(新增 `backend/providers/nowgoal_archive.py` +
`backend/cli/ingest_nowgoal_season_odds.py`)

真实探测确认(不是猜测):挪超 NowGoal 内部联赛 id=**22**、瑞典超=**26**
(两个独立信源交叉确证:`/oddscomp/<titan>` 页面 `sclassId` 字段 + `type=6`
日程响应的联赛目录数组);season-archive 对自然年联赛(如 `"2026"`)用
**裸年份**做赛季键,不是 `"2026-2026"`(此前的假设是错的,已被真实探测推翻)。

新传输层 `NowGoalArchiveTransport`(curl_cffi + `THORDATA_PROXY` 住宅轮换
代理 + 硬性墙钟超时 + 短间隔)是**独立的**,不 import/不修改
`backend/providers/nowgoal.py` 的 `_http_get`(生产实时轮询专用路径,
本轮未触碰)。

身份解析改用 `backend/ingest/nowgoal_historical_match_resolution.py`
(不用 `entity_resolution.resolve_match`——那个模块假设入参 kickoff 已是
UTC,而 archive 的 kickoff 是北京墙上时间,直接喂会导致 100%
`kickoff_diff=-28800` 误判),并在此基础上加了原模块没有的门禁:
`|候选kickoff转UTC后 − 目标真实kickoff_at_utc| ≤ 1800s` 才允许 `auto_ok`。
`build_team_id_dictionary` 的票数门槛按单赛季规模重新校准(`min_votes=10`
是为原用途——2,269 场跨多赛季历史缺口——设计的,单联赛单赛季 16 队用
默认门槛只有 3/16 队能进词典;改成 `min_votes=3,margin=3.0` 后两个联赛
均验证是干净双射,零撞名)。

```
挪超(59):  123/123 auto_ok,734 行写入 bronze_legacy_odds_summary
          (source=nowgoal_archive_refetch),0 fetch 失败
瑞典超(67): 104/119 auto_ok,624 行写入,14 needs_review,1 待定(见下),
          0 fetch 失败
```

瑞典超原有 4 条卡在 `needs_review`(置信度 0.5)的 xref,根因是
`entity_resolution._norm` 不折叠变音符(`häcken`/`mjällby` 等)而 NowGoal
发的是 ASCII——不是开球时间判定问题(`kickoff_diff_seconds` 全部为 0)。
诚实修复(不降阈值、不加变音符折叠、不合成队名变体):从 archive `TeamInfo`
+ 已验证的 16 队双射词典拿到 NowGoal 观测到的真实英文名,写 16 条
`dim_team_alias`(`INSERT OR IGNORE`,12 条新增、4 条已存在),显式删除
这 4 个 titan_id 对应的 `needs_review` 行(不是批量 `WHERE review_status=`
扫全表),重跑 `poll_nowgoal --date 2026-08-08/09/10`:3/4 立即转
`auto_ok`(confidence=1.0);第 4 场(2026-08-10 开球)在重跑时仍未出现
在 NowGoal 可查询的日程窗口内——如实标注为"尚未到时间窗,非解析失败",
不强行重试,留给生产轮询链路在临近开球时自然拾取。

### 30.4 验证汇总

```
浏览器实测(真实数据,非构造):
  /leagues                          挪超/瑞典超均"已有真实数据/当前可访问"
  /league/59/standings              16 队真实积分榜,中文名正确渲染
  /league/67/standings              16 队真实积分榜(此前是空的),中文名正确
  /league/{59,67}/matches?season=2026  已完赛比分 + 未来赛程同时正确显示
  /matches/5104842(挪超已完赛详情页)  赔率区显示真实两点表格(1x2/让球/大小球),
                                     如实标注"历史存档赔率……无完整走势时间线"
  首页 hero                          回退到北欧联赛后,队名正确显示中文
                                     (此前显示 Bodø/Glimt、Vålerenga 原文)
  浏览器控制台 / server 日志          均无错误

pytest tests/backend/ -q → 全量 1,111 collected 起始,本轮净增
  test_nowgoal_archive.py(21)+test_ingest_nowgoal_season_odds.py(11)+
  test_seed_team_names.py(15)−test_seed_allsvenskan_teams.py(已删除)
frontend: tsc 通过 / vitest 78 passed / eslint 通过 / next build 成功 /
  check:api-drift 无漂移(本轮未改 API schema)
三库 PRAGMA integrity_check → ok(allwin/platform/odds)
git diff --check → 无冲突标记/无行尾空白问题
```

### 30.5 本轮明确未做

- 瑞典超第 4 场 needs_review(Sirius vs Brommapojkarna,2026-08-10 开球)
  仍未解析,留给生产轮询链路自然拾取,未强行重试;
- 未接入定时增量:`scheduler.step1_ingest_newly_finished` 仍硬编码
  `(47,'2026/2027')`,后续两联赛新增的已完赛比赛不会被自动抓取,需要
  未来单独决策是否接入调度(已在 `docs/data-plan.md` 登记为独立后续项);
- kbisai 对这两个联赛的 needs_review 映射(9 条)未处理,不在本轮范围;
- 只回补了 Bet365 一家公司的历史两点摘要,未尝试 Macauslot 等其它公司。

## 31. 微信扫码登录切换 + 商业模式重构第一部分(2026-08-10)

### 31.1 微信登录:网页授权 → 带参数二维码 + webhook(CLAUDE.md §7.3 修订)

网页授权(snsapi_base)因 ICP 备案硬前提废弃;唯一路线改为已认证服务号
「生成带参数的二维码」(QR_STR_SCENE,scene_str=device request id)+ 消息推送
webhook(`/api/v1/auth/wechat/webhook`,签名 + 时间戳 ±300s + nonce 防重放)。
Device Login 的一次性 secret/原子领取/opaque session 骨架原样复用。
新增 migration platform/0008(qr_ticket/qr_url 列、access_token 持久化缓存表、
nonce 表)。真实微信出站能力(access_token、qrcode/create)与真实回调在拿到
凭证并完成公众号后台配置前标 UNVERIFIED;离线签名 fixture + Playwright 全链路
已验证。详见 docs/auth-wechat.md。

### 31.2 三段可见性(CLAUDE.md §8 修订):足球数据去付费化

- migration platform/0009:新增 `member` plan(rank 1,不可购买,登录基线,
  物化并集含旧 premium 全部足球数据权益 + league:lottery);pro/premium 及其
  四个商品 is_active=0 下架(行保留供历史订阅外键)。
- `backend/auth/entitlements.py`:匿名=free 行;任何已登录用户恒并入 member
  基线,有效订阅只做追加;`effective_plan_id` 只认 is_active=1 的 plan。
- 边界移动:完整概率/比分矩阵/深度报告/完整赔率时间线/top5 联赛 = 登录即得;
  匿名保持原 free 面(英超+竞彩联赛、最高一项概率、延迟赔率摘要)。
  匿名概率边界纪律不变:受限字段物理不下发。
- 缓存证据(离线临时库实测):匿名无 Cookie 时 products/track-record/metrics/
  英超 standings·fixtures/matches 列表 = `public, s-maxage`;league 87(需登录)
  匿名 401 `private, no-store`;任何带 Cookie 请求全路径强制 `private, no-store`
  (中间件 default-deny,test_cache_policy.py 47 项断言)。
- SEO/GEO 面新增:`frontend/lib/site.ts`(单一真源)+ `app/sitemap.ts` +
  `app/robots.ts` + `app/llms.txt/route.ts`,只列匿名可完整浏览页面;
  需登录的 top5 联赛页 Disallow,防爬虫抓付费墙壳。
- 测试语义迁移:订阅生命周期/rank 解析改用测试内种的 `testpaid` plan 演练
  (机制保留给未来付费板块);Role⊥Entitlement 的守护对象移到 `reco:*`;
  e2e 种子不再发放已下架 plan,兑换码种子随之移除(付费板块 plan 落地后重建)。

### 31.3 第二部分:付费板块「每日精选」(2026-08-10,已实现)

- migration platform/0010:`reco_slips`/`reco_legs` 独立建表(不触碰
  prediction_snapshots 任何保护);`daily_picks` plan(rank 5,定价不写,
  products 无行);`reco:track_record` 并入 member 基线,`reco:daily` 付费专属。
- 可见性(用户确认):匿名引导登录;登录看全部战绩归档(命中/未中/走水与作废
  全展示,作废单列不进分母也不消失,对齐 miaomiaodi.vip 归档口径);付费看
  近 30 天赛前推荐。draft 与未结算单绝不出现在战绩面(防赛前内容经战绩面泄漏)。
- 结算口径:1 单位/单不谈金额;任一腿未中=0,全走水=1,其余=有效赔率乘积
  (走水腿计 1.0);命中率 = win/(win+lose),走水不计分母;净单位=Σ(回报-1)。
- 留痕:创建/编辑/发布/结算/作废全部写 audit_logs;结算修正 edit_count+1 并
  记录 prev_result;settled/voided 拒绝内容编辑;作废必须填原因。
- 链路:routes_reco.py(2 只读 + 5 admin 端点,全不进 PUBLIC_ALLOWLIST,
  default-deny 强制 no-store)、/reco 页三态渲染、admin「每日精选」页签、
  导航项。浏览器实测:匿名引导页、付费视图(汇总条+未结算单+未中印章)、
  扫码登录跳转全部正确。
- 测试:tests/backend/test_reco.py 22 项(四方权限矩阵/泄漏边界/结算数学
  参数化/重结算留痕/作废/汇总口径/与模型 track-record 分离)。

## 32. 数据管道重建:16→17 联赛 · Phase 0 探测 + Phase 1 注册表(2026-08-10,进行中)

计划文件 `~/.claude/plans/football-data-pipeline-*.md`。目标:采集面扩到 17 联赛、
每日 T+7 赛程刷新、赔率五段递进节流、方糖告警。本轮不做 7 个新联赛的历史回填。

### 32.1 Phase 0 fail-closed 探测(真实网络,产物 `runtime/research/pipeline-v2-probe/`)

- 7 个待接入联赛逐一 `league_matches(id)`(不传 season → 发现):`details.id` 全部匹配。
  英冠 48 / 荷甲 57 / 葡超 61 = 2026/2027;巴甲 268 = 2026(自然年);
  欧冠 42 / 欧联 73 / 欧协联 10216 = 2025/2026 且当前季外(T+7 窗口 0 场)。
- 欧战三项资格赛天然排除已实证(最早 kickoff 均 9 月+,round 含 playoff 但无 7-8 月资格赛)。
- NowGoal 实时公司面板实测(3 场未开赛):稳定 12 家,选定的 **Bet365(8)/澳门(1)/皇冠(3)**
  3/3 全present、三市场齐全。**Pinnacle 不在实时面板**(原定 Pinnacle 由用户改选皇冠)。
- 赛后补抓 sweep **不可行**(mix_history 对未开赛比赛 0 行无时间戳)→ 本轮不建 sweep,
  临场收盘靠 10 分钟档 + last_call。详见 `docs/data-sources.md` §1.2 / §2.2。

### 32.2 Phase 1 联赛注册表 + 去硬编码(已实现)

- `backend/queries/leagues.py` `LEAGUE_META` 10 → 17,新增 7 项(均 `league:lottery`,
  带 `season_kind` 供 T+7 赛季解析);新增 `FREE_LEAGUE_ENTITLEMENTS` 与
  `anonymous_cacheable_league_ids()` 单一真源。
- `backend/api/routes_public.py`:5 处 `league_id == 47` 缓存判据 → `in ANON_CACHEABLE`
  (匿名可缓存 = free 档全部联赛,不再只有英超;语义 = "匿名是否可见",§10.2)。
- `frontend/components/matches/zh.ts`:LEAGUE_ZH 补 7 项中文名,ENTITLEMENT_ZH 文案更新。
- `tests/backend/test_league_scope.py`(新,8 项):17 联赛集合、entitlement 分档、
  匿名可缓存=free 档且排除 top5、content_pipeline 双注册表不漂移、
  **FUTURE_LEAGUE_ID 仍为 47(扩联赛≠扩模型,防无意义概率进公开账本)**。
- **明确延后**:7 新联赛的 sitemap/`ANON_LEAGUES` 收录延到数据真正落库后(Phase 2),
  避免空壳页进 sitemap(§8.3);`runner.py`/`scheduler.py` 的联赛硬编码去除与 T+7 job
  耦合,放 Phase 2/6 一起改,不留半成品中间态。

### 32.3 Phase 2 T+7 赛程同步 job(已实现)

- migration `odds/0006_fixture_sync_ledger.sql`:`fixture_sync_ledger`(逐联赛逐次落行,
  verdict ∈ written/refused_regression/refused_downgrade/refused_identity/off_season/fetch_failed),
  是反退化基线来源 + 质量门 G1 数据源 + "赛程为何没更新"的审计证据。
- `backend/cli/sync_fixtures_window.py`(新):每日对 17 联赛逐一 `league_matches(id)`,
  赛季**发现**(不预设,解决 J1/巴甲/欧战赛季惯例不同 + 换季);poll_state 按
  `SOURCE_FOTMOB_FIXTURES` 6 小时/联赛节流。三道门禁:G-A 骤降>50% 拒写保留旧数据、
  G-B/G-C 已完赛/已有比分行不被赛程行覆盖、身份不符拒落库。off_season 空结果诚实
  记录不告警。`--dry-run` 零持久化副作用(core 与 ledger 都不写)。
- `ingest_future_fixtures.py`:抽出纯解析 `rows_from_payload` + discovery 身份
  `discover_season_identity`;**修复 INSERT OR REPLACE 清列缺陷**——新增
  `upsert_fixture_row` 用 `ON CONFLICT DO UPDATE` **只更新赛程拥有列**
  (Season/Date/队/status/round/kickoff 三件套),不碰 Referee/天气/比分。
  额外加第二个 sys.path 插入,使该脚本"独立运行"与"作为包导入"两种上下文都可用。
- `tests/backend/test_sync_fixtures.py`(新,11 项):赛季发现、身份门禁、off_season、
  **G-A 反退化拒写、G-B 完赛不降级、清列缺陷修复(裁判/天气/比分保留)**、ledger 逐联赛、
  幂等、dry-run 零副作用、真实 UCL 存档 round 透传。
- 真实网络 dry-run 实证:英冠 48 抓到 552 场真实赛程、赛季发现 = 2026/2027,零写库。

### 32.4 Phase 3 变音符别名 + 跨联赛占位修复(已实现,部分工具延后)

新联赛能否出赔率的命门是变音符:NowGoal 发 ASCII、FotMob 发带变音符原文,
auto-seed 别名保留变音符 → 荷甲/葡超/巴甲/欧协联整批 needs_review → 零赔率。

- `entity_resolution.seed_ascii_fold_aliases`(新):给带变音符的既有别名补一份
  ASCII 折叠别名(source='ascii_fold'),**撞名审计 fail-closed**(某折叠串映射到 ≥2
  个 canonical → 整串拒绝、列 rejected,绝不写歧义别名)。**不改 `_norm`**(它被 10
  联赛共用,改它会一次性改变全站别名语义)。真实 odds.db 实测:495 别名中 15 个含变音符,
  新增 9 条折叠别名、0 撞名;Häcken→'hacken'、Mjällby→'mjallby' 现可被 NowGoal ASCII 命中。
  既有北欧联赛同样受益。
- `entity_resolution._candidate_matches`:候选查询加 `AND League_ID IN (LEAGUE_META keys)`。
  修一个真实隐患——NowGoal 日程是全球的(单日约 988 场),不限联赛会让候选池与
  `UNIQUE(provider, fotmob_match_id)` 占位风险随 16 联赛显著上升,而被占住的行永久
  冻结在 needs_review(resolve_match 不再重评估)。只用 FotMob 侧范围,零成本。
- `tests/backend/test_ascii_fold_aliases.py`(新,6 项):折叠正确性、撞名 fail-closed、
  幂等、不碰无变音符别名、跨联赛候选过滤。

**本轮明确延后(如实)**:① `bronze_ng_schedule_row` 观测表 + `alias_coverage` 报表——
是别名人工补录的工作清单,真正有用要等新联赛赛程落库 + 一轮真实 NowGoal 轮询后
(依赖 Phase 2 的生产写入与生产运行);② `FOTMOB_TO_NOWGOAL_LEAGUE` 静默降级修复——
它只服务历史 archive 路径,向前采集根本不经过它,改其契约会动历史解析器测试而无收益;
③ 逐联赛真实命中率测量——需要新联赛有数据才能算,属生产运行阶段。
seed_ascii_fold_aliases 接入 entity_resolution job 的自动化在 Phase 6 一并做。

### 32.5 Phase 4 赔率五段节流 + 三家公司(已实现)

- `poll_windows.py`:新增 `PollCadence` + `CADENCE_BY_SOURCE` + `poll_decision(source,...)`。
  NowGoal 赔率走五段递进(72/48/24/12h 检查点 → 12h起每小时 → 3h起每20分 →
  1h起每10分 → T-15min last_call 强制补一枪),FotMob 快照/日程沿用 `CADENCE_LEGACY`
  (900/300 一字不改)。**`required_interval_seconds` 输出逐字节不变**(委托 legacy),
  content_pipeline/poll_fotmob_snapshots 零改动。检查点/last_call 判定仅依据持久化的
  `poll_state.last_polled_at` 与 kickoff 重算,重启/重放幂等(不加列、不存内存态)。
  逐档 ≥ §6.3 下限(2–72h≥900s、0–2h≥300s),§6.3 数值无需改。
- `poll_nowgoal.run_due_poll` 切换到 `poll_decision`。
- `nowgoal.DEFAULT_TARGET_CIDS` 由 `(8,31)` 改为 **`(8,1,3)` = Bet365/澳门/皇冠**;
  **删除 `parse_odds` 的静默"换公司"回退**(缺哪家少哪家、一家都没有返回空,空不是错误)。
  修正测试 fixture 一处公司名错标(cid 50 = 1xBet,非 Crown)。
- 实证:五段间隔逐档正确(72h→24h/18h→12h/6h→1h/2h→20min/40min→10min),
  last_call 在 T-15min 跨窗时强制触发。
- 测试:`test_poll_cadence.py`(新,18 项:分档/last_call/per-source 隔离/§6.3 下限/
  重放幂等/required_interval_seconds 不变);更新 `test_nowgoal_provider.py`(三家公司、
  不回退)、`test_odds_pipeline.py`(2→3 家计数)、`test_pipeline_e2e.py`(kickoff 移到
  1–3h 档以匹配新节奏,保持"先节流再到期"原意)。

### 32.6 Phase 5 方糖告警 + Phase 6 worker/systemd 接线(F 段,2026-08-10,已实现)

**Phase 5 告警通道**:

- migration `platform/0011_pipeline_alerts.sql`:告警账本(与失败日志/审计分表,
  前身项目 ingest_failure_log 混用教训);`odds/0007_poll_attempt_log.sql`:轮询尝试
  日志(append-only,`mark_polled` 同事务写入,tier/ok 记录命中档与成败)——
  hash-diff 下"没快照"≠"没轮询",没有它五段节流不可验证。保留 30 天,
  过期行由 daily_digest 清理。
- `backend/notify/`(新):持久化优先(先落 pipeline_alerts 再推)、解析响应体
  `code==0` 才算 sent(修正前身项目只看 HTTP 200 的缺陷,40024 配额耗尽如实记
  failed)、每日配额(软上限 4 只放行 CRITICAL;WARNING≤2/INFO≤1;CRITICAL 超硬配额
  照发、由服务端 40024 如实拒绝)、同 dedup_key 24h 去重(只看 sent,失败不吞重试)、
  title/body 逐行过 ops_check `_sanitize_summary` + sendkey 剥除。`notify()` 永不抛,
  三条独立暴露路径:① stderr `ALLWIN_ALERT_PERSIST_FAILED` 标记;② 结果写回
  `job_runs.meta_json`;③ ops_check 新增 `check_pipeline_alerts`(pending>1h /
  failed:misconfigured → WARN)。P0 白名单唯一定义 `notify.P0_ALERT_SOURCES`。
- 接线(两个汇聚点,不散调):`runner.run_job` 失败分支在 `_finish_run` 落库**之后**推
  CRITICAL(缺 THORDATA_PROXY 归 `proxy_unavailable`,其余 `pipeline_step_failure`);
  链上 cascade-skip 步骤不执行不告警,只有最初失败那步推(测试钉死)。
  `ops_check --notify`(WARN/CRITICAL 推脱敏摘要,dedup_key 含问题组件清单)。
- 配置:`NOTIFY_ENABLED` / `SERVERCHAN_SENDKEY` / `NOTIFY_DAILY_QUOTA`
  (.env.example 已记;调用时读取,不在 import 期;缺 key 不 fail-fast,
  由 ops_check WARN 暴露)。

**Phase 6 worker/systemd**:

- `runner.py` 去硬编码:`schedule_sync_multi`(→ `sync_fixtures_window --due`)与
  `fotmob_incremental_multi`(新 CLI `backend/cli/fotmob_incremental_multi.py`:
  遍历 LEAGUE_META × dim_match 真实 NotStarted 赛季,复用 scheduler.step1 同一条
  抓取路径,poll_state 每联赛 6h 节流)取代旧 47/2026/2027 硬编码任务;
  `DEFAULT_CHAIN` 尾接 `pipeline_gates`;`NON_CHAIN_JOBS` 白名单
  (silver_build 别名 / daily_digest)。
- `backend/cli/pipeline_gates.py`(新,fn job 挂链尾):九门——赛程窗口不一致
  (CRITICAL)/反退化拒写(CRITICAL)/kickoff 精度(WARN)/逐联赛实体解析
  (<60% WARN、==0 CRITICAL 点名联赛)/24h 赔率覆盖(<50% WARN)/T-15min 收盘覆盖
  (<80% WARN、<70% CRITICAL,含中位差)/比分不回退(CRITICAL)/公司口径
  (目标外 cid CRITICAL)/近 1h WAF(CRITICAL)。数据不足如实 skipped 不误报
  (季外联赛/样本<3、<5);门"发现问题"≠任务失败(避免重复告警),违反经 notify 推送。
- `resolve_entities` 接入 `seed_ascii_fold_aliases`(§32.4 延后项闭环);
  `daily_digest` fn job(24h 赛程/快照/轮询/失败任务/已推告警汇总 + attempt log 清理,
  INFO 一条,`--key <北京日期>` 幂等)。
- systemd 新增 `allwin-opscheck.{service,timer}`(30 分钟,`--json --notify`,
  `SuccessExitStatus=1 2` 防 WARN 刷 unit failure)与 `allwin-digest.{service,timer}`
  (`OnCalendar=23:30 Asia/Shanghai`、`Persistent=true`、`--key $(TZ=Asia/Shanghai date)`);
  两者复制 worker 加固块;既有两个 timer 未动(last_call 已保证收盘硬约束)。
- 测试:`test_job_order.py`(新,8 项:链顺序/gates 居尾/无孤儿任务/
  PERIODIC_CHAIN_EXCLUDE==poll_wrapper∩chain 可执行断言/spec 完整性/
  去硬编码回归钉);`test_notify.py`(新,19 项);`test_pipeline_gates.py`
  (新,15 项);`test_poll_scheduling.py` 增补新单元静态检查;
  `test_migrations.py` 表清单更新。

**验证(F 段)**:后端全量 pytest **1295 collected,exit 0(1 skip 为既有
test_pit_dataset 真实快照缺失项,与本轮无关)**;`compileall` 通过;真实三库
先 `backup_sqlite.sh`(20260810T082419Z,integrity 全 ok)后应用两条新迁移,
`PRAGMA integrity_check` 三库全 ok;`pipeline_gates --no-notify` 对真实库只读冒烟:
`company_scope` 正确检出近 24h 仍存在切换前旧目标公司 Sbobet(31) 的快照
(E 段刚改 8/1/3,24h 窗口滑过自然消退——门在做它该做的事);`ops_check --json`
真实退出 2(CRITICAL 来自数据盘 94.9% 占用,**独立于本轮的真实运维状态,需要
清理磁盘**)。前端 tsc/vitest(77)/eslint/build/check:api-drift 全过,零漂移
(本轮无前端与 API 契约改动)。**真实方糖推送 UNVERIFIED**(未配 SENDKEY,
推送路径由 stub 测试覆盖;首条真实推送需用户提供 Server 酱 SendKey);
systemd 单元真实 systemd-analyze/enable UNVERIFIED(本机无 systemd,静态测试覆盖)。

## 33. 比赛详情页完赛事实报告:阵容/射门图/统计/事件四 tab(2026-08-11)

DESIGN.md:220 的★核心页规格(事件时间线+阵型阵容+射门图+球队数据对比+球员评分)
首次实现。数据早已在五张 fact 表里(lineup/events/shotmap/team_stats/player_stats,
13,051 场完赛覆盖率约 100%),此前零 API、零前端。方案经 Opus 复核 + 真实库逐项
核验(docs/audits 级别的实证过程见会话计划文件),关键结论:

- **FotMob 射门坐标两队同朝一端(x→105)记录**(30 场随机抽样 30/30;
  fotmob_client.py:764 原样透传不变换)——前端展示层对客队做 `105-x, 68-y`
  镜像,后端保留原始坐标。业界(Opta/StatsBomb)同约定,但只作旁证,
  结论以自家数据实测为准。
- **单场评分唯一真源 = fact_match_lineup.rating**(player_stats 只有
  rating_title,同值不同精度);阵型站位 extra_json.horizontalLayout 已是
  0..1 归一化坐标,阵型图纯 CSS 实现,零新依赖。
- `fact_team_match_stats` 只取 `Period='All'`(全库 13,051 场 100% 存在,
  实证无缺失);extra_json 按 37-key 白名单投影(含字面带点的
  `"matchstats.headers.tackles"`),4 个恒 null 分组表头伪 key 排除。
- `fact_shotmap` 有 12 行 Team_ID 与主客都对不上的脏数据,查询显式
  `Team_ID IN (home,away)` 过滤。

交付:`backend/queries/match_report.py`(单入口,五表全空→None)、
`GET /api/v1/matches/{id}/report`(`MatchReportResponse` Union DTO;
**匿名可见**——纯历史事实属 §8"足球数据不收费"面,与积分榜/赛季统计同档,
进 cache_policy 公共缓存白名单,带 Cookie 自动降 no-store)、
`tests/backend/test_match_report.py`(13 项:三态可用性/Period 哨兵/脏 Team_ID
哨兵/评分来源哨兵/i18n 回退/联赛门禁/缓存头)+ coreseed 补五表。
前端:`MatchTabs`(总览/阵容/统计/事件,report 不可用时不渲染 tab 壳,
§11.1 六段作为"总览"内容一字未动)、`PitchFormation`(纯 CSS)、
`ShotMapChart`(EChart 底层封装+IntersectionObserver 懒挂载+客队镜像)、
`MatchLineupSection`/`MatchStatsSection`/`MatchEventsSection`、`RatingChip`
(globals.css 既有 --rate-* token,elite 档前景用 var(--bg) 双主题自适应)。

**验证**:后端 pytest 全量 exit 0(新增 13 项全绿);契约 export→gen:api→
check:api-drift 零漂移;前端 tsc/eslint/vitest(77)/build 全过;
干净环境构建 check_browser_bundle.sh **OK**(本地带 .env.local 构建 FAIL 属
既有已知现象);真实 API 实测 5107563:200 + public 缓存头,阵容 40/事件 15/
射门 18/控球 34-66/xG 0.32-2.31 与库直查一致,未开赛→available=false,
带 Cookie→no-store;浏览器实测(深浅双主题、375px/桌面):四 tab、阵型图
3-4-2-1 vs 4-2-3-1 镜像正确、射门图主右客左、事件时间线含补时角标与半场
分隔、移动端零横向溢出、未开赛比赛无 tab 退化为原布局。

**已知问题(非本轮引入)**:Playwright 17 项中 4 项失败——e2e 种子把预测种在
"最早英超比赛"(8/21 揭幕战),但 §32 的 T+7 真实同步后首页最近开球窗口被
北欧/日职比赛(8/14-15)占据,种子比赛进不了首页 featured 卡,48% 断言落空。
失败现场 dump 证实详情页渲染的是旧版无 tab 布局(未开赛路径,与本轮改动无关),
其中 3 项的失败产物在本轮改动前已存在。已登记独立修复任务(种子应选全局最近
开球比赛,或测试直接导航种子比赛 id)。

## 34. 权限体验与全站文案清理 + E2E 种子选场修复(2026-08-11)

### 34.1 背景

外部用户视角体验报告(sol5.6)结论:三层权限(游客/注册用户/精选授权)的
后端边界正确(权限回归 90 passed),但页面把这三层讲乱了——"付费/Pro/筹备中/
免费登录即享"混用,内部 entitlement 键值直接暴露,登录后回不到原页面,
每日精选与战绩混在一页,底部导航找不到精选。本轮只清理体验与文案,
不改任何权限判定逻辑。

### 34.2 改动(全部在前端与 E2E 层,后端零改动)

- `/pricing` 重写为「访问权限说明」:游客/注册用户/精选授权三张简卡
  (层级列表仍来自 /api/v1/products 的 is_active 套餐,未在前端虚构),
  删除权益对比表、"筹备中"、全部内部 entitlement 键值与付费套餐话术;
  兑换码区保留,改述为"获得精选授权"。
- 清除全站用户可见 Pro/Premium 残留:联赛目录"Pro 解锁"→"登录后免费查看"
  (带 login?next);比赛筛选 Pro 徽标→"登录";锁定联赛空状态与 WDL 概率卡
  脚注同步改写;RedeemBox 套餐名映射 free/member/daily_picks。
- 登录返回:MemberLeagueSection/MemberMatchDetail/PredictionCard/
  CooccurrenceSection/StudioGate/RedeemBox 全部登录入口改为携带
  `?next=<当前页>`;登录页新增"首次扫码即自动创建账号;登录完成后会返回
  刚才浏览的页面"。
- `/reco` 重构为「今日精选|历史战绩」双标签(?tab=daily|record):
  匿名只见登录引导(免费登录查看战绩/先看公开比赛资料);普通登录默认
  历史战绩,今日精选为锁定说明(联系站长,生效后自动显示);授权用户默认
  今日精选,顶部横幅"精选权限已开通·有效期至 X"(读自己的
  /api/v1/account 订阅);近 30 天推荐与归档不再重复展示。
- 底部导航固定五项:首页|比赛|精选(/reco?tab=daily)|战绩(/reco?tab=record)
  |我的;选中态感知 ?tab=(useSearchParams 包 Suspense,SSR fallback 同结构);
  bottomNav grid 4→5 列;顶栏"会员"→"权限说明","公开战绩"→"模型战绩";
  套餐徽标不再裸显 plan id(daily_picks→"精选")。
- 账户中心:匿名文案说明登录真实价值;登录后首屏三张权限状态卡
  (完整足球数据/历史推荐战绩=已开放,今日精选=未开通或已开通至 X);
  账号 ID/角色折叠进"账户详情(技术信息)";删除过时的"免费套餐仅最高一项
  概率"提示(登录即 member 基线,该状态已不存在)。
- E2E:pricing 断言改为三层文案 + Pro/Premium/entitlement 键值不出现;
  auth 流程补账户权限状态卡与 /reco 双标签断言;anonymous 补 /reco 匿名
  引导断言(登录按钮必须带 next=/reco)。
- 同轮移植 worktree wonderful-swirles-9cf0dc 已验证的 E2E 种子修复
  (此前 anonymous/auth 3 用例失败的根因):seed_e2e 复刻首页 featured
  选择(北欧 67/59 前 3 场全部种 48%/27%/25% 正式预测,priority 排序自洽),
  kickoff 三元组逐字取核心库真实值;admin-predictions-edit 用例改用隔离的
  窗口外编辑目标(不再污染 48% 主种子);tests/backend/test_e2e_seed.py
  断言同步加强(kickoff 必须与 dim_match 逐字一致)。

### 34.3 验证(真实命令)

```
python -m tests.e2e.seed_e2e            → ok(featured=5104979,targets=6,edit=5803539)
pytest tests/backend/test_e2e_seed.py   → 4 passed
cd frontend && npx tsc --noEmit         → 通过
npx eslint app components e2e tests     → 通过
npx vitest run                          → 77 passed
CI=1 npm run e2e                        → 17 passed(修复前同套件 3 failed)
浏览器实测(375×812):/pricing 三卡+单行五项底部导航;/reco 匿名引导、
  精选 tab 选中态;无水平溢出
```

### 34.4 已知限制

- 授权用户(reco:daily)的双标签页与有效期横幅只有代码与匿名/普通登录
  E2E 覆盖,真实授权账号视角未实测(E2E 种子未 grant daily_picks),
  标记 UNVERIFIED;
- 顶栏"模型战绩"改名只动 label,/track-record 页面自身未改;
- 旧 pro/premium 存量兑换码若仍存在,RedeemBox 成功提示会回退显示原始
  plan id(不再映射),属预期诚实展示。

## 35. 首页信息架构重组:面向竞彩用户的首屏 + 详情页降噪(2026-08-11)

### 35.1 背景

第二份用户视角体验报告结论:首页信息架构偏向数据研发人员,不服务
"每天找比赛、看推荐、查战绩"的目标用户。本轮按报告重组首页、清理
详情页技术噪声,底层数据/溯源/审计记录全部保留,只动展示层。

### 35.2 后端(唯一改动:匿名聚合端点)

- 新增 `GET /api/v1/reco/overview`(routes_reco + queries/reco.public_overview
  + RecoOverviewResponse):今日发布场数/最近发布时间(北京自然日判定)
  + 近 30 天已结算汇总(命中/未中/走水/作废/净单位)。只有计数与聚合,
  无任何单据内容;未结算 published 单只贡献计数,不泄漏付费赛果。
  测试 TestPublicOverview 断言 draft 不计入 + 标题/方向/赔率字段零泄漏。
- OpenAPI 重新导出 + `npm run gen:api`,check:api-drift 无漂移。

### 35.3 首页重组(app/page.tsx 按报告推荐结构)

顶部计数条(今晚|明天|未来7天,0 场不做成链接;今明全空时诚实展示
"最近比赛 X"入口)→ 重点比赛视觉卡(kicker 按北京日期动态为
今晚重点/明日重点/本周重点;无预测时改为"赛前资料摘要/推荐尚未发布",
不再渲染空"公开结论";移除"部分数据暂不可用"统一徽标)→ 今日精选卡
(已发布 N 场·更新时间,未发布时如实说明,不造虚假倒计时)→
近30天推荐记录摘要(样本数+命中/未中/走水+净单位;无记录诚实空态)→
本周比赛(按北京日期分组,每天 ≤5 场——替代原先重复的"其他比赛"横滑
+"近期赛程"两个区块)→ 我关注的比赛 → 常用入口/免责声明。
首页的模型公开战绩大区块撤下(/track-record 完整保留,快捷入口改名
"模型公开记录")。

### 35.4 关注比赛(首版本地)

`lib/followed-matches.ts`(localStorage,上限 20 场)+ 详情页
FollowButton + 首页 FollowedMatches(无关注时整个模块不渲染;匿名
无权限联赛如实跳过)。与登录用户的服务端收藏 /api/v1/favorites 互不影响。

### 35.5 详情页降噪(数据与顺序不变,只调展示)

- "模型与登记信息"→ 默认折叠的"数据来源与说明";概率卡内部字段
  (版本号/登记状态)折叠进"数据说明";
- "同期事件"→"关键变化",区块标题随组件走,无内容/加载失败时整体隐藏;
- 赔率时间轴:完整时间线默认每公司只显示初盘+最新(带点位标签),
  完整历史进"查看完整历史(N 条)"折叠;"系统检测时间"→"本站采集时间";
  去掉"N 个系统观测点"术语;
- header 移除"最近成功同步/下次计划/赔率观测 N 个点/裸 probability_source";
  概率来源中文化(赔率折算,非本站模型预测 / 本站模型 / 暂无可靠概率);
- MatchRow 逐行不再输出 MARKET_BASELINE/观测点数/统一模糊标签,
  只保留可行动的 STALE 提示;"市场去水基线"全站改"赔率折算概率";
  "数据截止"→"数据更新于"(Studio 制作工具内保留原措辞);
  "未来七天更多比赛"→"查看本周其他比赛"。

### 35.6 验证(真实命令)

```
pytest tests/backend/test_reco.py       → 23 passed(含新 overview 泄漏测试)
pytest tests/backend/ 全量               → exit 0(全部通过)
tsc --noEmit / eslint / vitest          → 通过 / 通过 / 77 passed
CI=1 npm run e2e                        → 17 passed(首页断言按新结构更新:
  计数条与重点卡结论必须在首屏、本周比赛不含重点场、今日精选/推荐记录可见)
浏览器实测(375×812,E2E 种子库):首屏=计数条+重点卡;今日精选/
  近30天推荐记录诚实空态;详情页关注按钮 → localStorage → 首页
  "我关注的比赛"完整闭环;控制台无错误
```

### 35.7 已知限制 / 未做(如实)

- "今日更新状态"一行(赛程/赔率/推荐三个更新时间)未做:赔率与赛程的
  全站级更新时间目前没有便宜的公开查询,留待 pipeline 状态端点设计;
- 详情页"30 秒看懂"摘要卡、吸顶 tab、per-match 推荐状态徽标未做
  (后者需要推荐单 legs.match_id 与比赛的公开映射设计,涉及付费边界);
- 统计页两个口径 xG 的命名区分未核查,本轮未动统计 tab;
- 重点卡未加"近期状态 X胜|赔率状态|推荐状态"三行(现有"本场依据"三条
  证据已覆盖近期状态;推荐状态同上,依赖 per-match 映射);
- 首页"整卡在首屏内"的旧 e2e 断言按新信息架构改为"计数条+结论在首屏",
  这是设计变更的必然调整,不是降断言(新增了计数条位置、模块可见性断言)。

## 36. 手动触发 T+7 赛程/赔率采集诊断 + 三项报告遗留项落地(2026-08-11)

### 36.1 手动触发结果(真实命令,非推测)

**T+7 赛程同步(`python -m backend.cli.sync_fixtures_window --league-id 47 --dry-run`):
被 Thordata 住宅代理拒绝,不是代码问题。**

```
[FotMob] transport error SSLError (attempt 1-3/3)
verdict: fetch_failed
```

逐层诊断(均为真实网络探测):
- 本沙盒直连 `https://www.fotmob.com`/`https://www.google.com` → 200,直连互联网本身正常;
- 通过 `.env` 里配置的 `THORDATA_PROXY`(住宅代理,FotMob 采集按 CLAUDE.md §6.3 强制要求)
  发起 CONNECT/HTTP 请求 → TCP 三次握手成功,但代理返回
  `HTTP 403` + `x-thor-error-code: Access_608` + `x-thor-error-msg: Does not support
  mainland China servers`;
- 结论:**本开发沙盒的出口 IP 被 Thordata 识别为中国大陆,该代理服务商明确拒绝大陆
  IP 发起请求**。这是本沙盒环境的网络位置限制,不是凭证过期、不是代码退化,也不是
  FotMob 反爬——只要出口 IP 判定不变,重试/换 league 都会得到同样结果,因此没有
  对全部 15 个联赛重复跑一遍(单联赛已完整复现失败模式)。生产环境(CLAUDE.md §4:
  AWS 东京)出口 IP 不在中国大陆,预期不受此限制,但本次未在生产环境验证,如实标记
  `UNVERIFIED`。
- 未尝试任何绕过代理直连 FotMob 的方式:直连会被 FotMob 反爬拦截且违反
  `backend/fotmob_client.py` "只用显式 CLI 且必须走住宅代理"的既定架构决策。

**赔率轮询(`python -m backend.cli.poll_nowgoal --due`):0 到期,真实且符合设计,不是 bug。**

```
窗口候选: 0,到期: 0,未到期跳过: 0
```

原因:CLAUDE.md §6.3 的赔率采集窗口是"距开球 ≤72 小时";触发时刻(2026-08-11
06:57 UTC)全部可轮询联赛里最早的未开赛比赛是 2026-08-14T10:00:00Z(挪超),距今
约 75 小时,还差约 3 小时才进入 72 小时窗口。NowGoal(`www.nowgoal26.com`)本身
直连可达(不需要代理),问题纯粹是"当前没有比赛落在窗口内",T+7 赛程即使同步成功
也不会让这个数字变化(第 7 天的新比赛更靠后,不会提前拉近最近一场的距离)。

### 36.2 三项报告遗留项:逐一复核后的真实结论

触发后复核发现:**三项都不是被"缺少今天抓的新数据"卡住**——数据库里已有的
历史时间戳(赛程 2026-08-10T23:53Z、赔率 2026-08-10T06:00Z、推荐 2026-08-09T21:10Z)
本身就是这三项功能需要的全部输入。据此:

1. **"今日更新状态"行**:✅ 已实现。新增 `GET /api/v1/status/freshness`
   (`backend/queries/freshness.py` + `routes_public.py`,进 `PUBLIC_ALLOWLIST`
   短 TTL 共享缓存)聚合三条独立时间戳:
   - 赛程:`fixture_sync_ledger` 最近一次 `written`/`off_season`(结论性成功;
     `fetch_failed`/`refused_*` 不计入,避免一次偶发网络失败在首页显示假的
     "刚刚更新");
   - 赔率:`bronze_ng_odds_snap` 最近 `observed_at`;
   - 推荐:`reco_slips` 最近 `published_at`(非 draft)。
   任一为空如实返回 `null`(前端渲染"尚无记录"),不用当前时间或另一条时间顶替。
   首页新增 `FreshnessLine`(`/`),真实数据验证:`赛程更新 2026-08-11 07:53
   ｜ 赔率更新 2026-08-10 14:00 ｜ 推荐更新 2026-08-10 05:10`(均为北京时间,
   数据库真实值)。

2. **详情页"30 秒看懂"摘要卡 / 吸顶 tab / per-match"推荐状态"徽标**:
   ⏸️ 仍未做,且**确认不是数据问题**。`reco_legs` 表本来就有 `match_id` 外键,
   技术上早就能查"这场比赛是否有推荐"——真正的阻塞是产品/口径决策:向匿名/
   免费用户展示"该场已有推荐"这一事实本身,是否构成对付费内容存在性的间接
   泄漏(即便不展示方向/赔率)。这是 CLAUDE.md §8 权限边界之外、需要站长明确
   拍板的问题,不属于我可以单方面决定的范围,本轮未实现,留待你确认后再做。

3. **统计 tab 两个 xG 口径命名**:✅ 已实现,且**真实数据证实是货真价实的
   两个数字,不是标签问题**。核查代码 + 抓一场真实完赛比赛(伯恩利 vs 曼联,
   Match_ID 3411340)对比:
   - 球队数据对比行(`fact_team_match_stats.expected_goals`,FotMob 团队统计
     接口原样):伯恩利 0.90 / 曼联 1.16;
   - 射门图逐次 xG 求和(`ShotMapChart` 按 `shots[].xg` 客户端相加,排除点球
     大战):伯恩利 **0.91** / 曼联 **1.17**。
   两者确实不完全相等(源自 FotMob 两个独立接口/模型口径),此前都叫"xG"或
   "预期进球 xG"容易让用户以为数字不一致是 bug。改名区分:球队对比行 →
   **"官方统计 xG"**;射门图摘要 → **"射门图 xG 合计"**(`components/matches/zh.ts`
   `TEAM_STAT_LABELS` + `ShotMapChart.tsx` 摘要文案)。

### 36.3 验证(真实命令)

```
pytest tests/backend/test_freshness.py  → 6 passed(公开缓存/凭证强制 private/
  三源独立取 MAX/空表返回 null/fetch_failed 与 refused_* 不计入/draft 不计入)
pytest tests/backend/ 全量              → exit 0(全部通过)
tsc --noEmit / eslint / vitest          → 通过 / 通过 / 77 passed
python -m backend.cli.export_openapi && npm run gen:api && check:api-drift
                                         → 65 paths,无漂移
CI=1 npm run e2e                        → 17 passed(首页新增 freshness-line 断言;
  中途一次运行因 next/font/google 抓取瞬时网络抖动导致 build 失败,单独跑
  `npm run build` 复现为绿色确认非代码问题后重跑 E2E 通过,不是回归)
真实数据验证(非 E2E 隔离库,连 data/allwin.db):
  curl /api/v1/status/freshness → 真实三条时间戳;
  首页渲染 → 北京时间正确换算;
  /matches/3411340 统计 tab → "官方统计 xG"与"射门图 xG 合计"两个真实不同的
  数字同屏可见,标签区分清楚
```

### 36.4 已知限制

- 生产环境(AWS 东京)是否真的不受 Thordata 大陆 IP 限制影响,本轮未验证,
  标记 `UNVERIFIED`,建议下次部署后跑一次 `sync_fixtures_window --league-id 47`
  确认;
- item 2(per-match 推荐状态徽标)需要你明确"是否允许匿名用户看到某场比赛
  存在推荐(不含内容)"这一产品决策后才能继续,本轮不擅自决定;
- "赔率数据暂未恢复,页面保留最近一次成功结果"这类异常态判定(报告原文示例)
  未实现——需要一个人为设定的"多久算异常"阈值,本轮只做了如实展示真实时间戳,
  没有编造任意阈值。

## 37. 视觉体系升级 + 推荐存在性公开 + 最近浏览(2026-08-11,第三份用户视角报告落地)

### 37.1 决策记录(站长授权)

用户转发报告并明确放权("可以脱离规则修改,冲突的规则改掉或删掉")。据此:

- **推荐存在性公开**:某场比赛"是否有已发布的赛前推荐单"(纯布尔)定为公开
  运营信息,可向匿名展示;方向/赔率/标题/备注等内容仍是付费面物理不下发,
  draft 存在性也不外泄。已写入 CLAUDE.md §8.2(此前一轮标记为"待站长拍板"
  的事项,本轮由用户放权落定)。
- CLAUDE.md §11.2 新增三条长期视觉纪律:12px 用户可见文字下限、固定颜色
  语义(深蓝=品牌/青绿=操作/黄绿=关键数据仅深底/橙=等待/红=真实错误/灰=辅助)、
  禁止状态胶囊墙与内部枚举值上屏。

### 37.2 改动

后端(2 个只读端点扩展,零新写路径):
- `queries/reco.py` 新增 `published_match_ids()`;`/api/v1/reco/overview` 增
  `published_match_ids` 字段;`/api/v1/matches/{id}` 增 `reco_published` 布尔
  与 `odds_coverage_tier`(与列表路由同口径,速览卡"状态完整度"用);
  reco 表未迁移时如实 False,不阻塞详情页。

前端:
- **详情页"30秒速览"卡**(仅未完赛渲染):推荐状态(已发布→链接精选页/
  待发布)→ 主要判断(与概率卡同源的最高概率)→ 关键依据(证据区第一条)
  → 最大风险(反向证据/不确定性第一条)→ 状态完整度行(赛程/近期数据/
  赔率/推荐 逐项 ✓/—,取代模糊标签)→ "查看详细数据↓"锚点。
- **详情页头部标签墙压缩**:五枚胶囊 → "联赛·第X轮"+比赛状态两枚,赛季并入
  日期行;完赛比分色修复(原 --gold-hi 浅色近白叠白底/深色深绿叠深底,
  两种模式都看不清 → --ink 32px/800,深浅色实测均清晰)。
- **首页**:重点卡新增"赔率已更新/待采集 · 推荐已发布/待发布"状态行(存在性,
  绿色=--win 语义色);今日精选两个按钮降级为文字链接(全页只保留重点卡
  "查看完整分析"一个最强按钮);本周比赛从边框盒改为轻分隔列表;新增
  「最近浏览」模块(本机 localStorage,详情页访问自动记录,与关注去重,
  无记录不渲染)。
- **字体下限清扫**:page/MatchRow/OddsTimeline/PredictionCard/Cooccurrence/
  SiteNav 底部导航等 10-11.5px 辅助文字统一提到 12px+,开球时间 13px;
  比赛列表队徽 32→40px。

### 37.3 验证(真实命令)

```
pytest tests/backend/test_reco.py         → 25 passed(新增存在性生命周期测试:
  draft 不可见 → published 可见且响应无任何单据内容 → settled 退出赛前状态;
  详情 reco_published 标志 + 无内容泄漏)
pytest tests/backend/ 全量                → exit 0
export_openapi + gen:api + check:api-drift → 无漂移
tsc / eslint / vitest                     → 通过 / 通过 / 77 passed
CI=1 npm run e2e                          → 17 passed(新增断言:重点卡状态行
  如实"待"态、速览卡可见、推荐待发布、赛程✓、详细数据锚点)
浏览器实测(375×812,E2E 隔离库):首页首屏计数条+更新状态+重点卡+单一主
  按钮;详情页速览卡完整;完赛比分浅色/深色模式均清晰(此前两种模式都不可读)
```

### 37.4 未做与已知限制

- 报告"第二阶段"功能(备选清单/分享比赛卡/偏好联赛/名词解释弹层)未做;
- 赔率变化摘要("3.48→3.20 最近6小时下降8%")未做——目前已折叠为初盘/最新
  两行,百分比变化摘要需要另算派生口径;
- 比赛列表行(/matches)未加逐行推荐状态徽标(需把 published_match_ids
  接入列表页,本轮只覆盖重点卡与详情页);
- 深色模式主题切换存在 ~0.3s 背景过渡,截图工具在过渡中会拍到混合色,
  非渲染缺陷(过渡完成后已实测正确)。

## 38. Canonical v2 数据模型规范化 Phase 0+1:身份框架真正接线(2026-08-11)

### 38.1 背景

用户拿到一份外部报告(GPT sol5.6,审计 cc 服务器 Sportmonks 世界杯库
`worldcup.db`,3.5GB),建议 all-win 吸收 Sportmonks 领域概念、建立
provider-neutral canonical v2。用户明确诉求:借鉴字段设计、不接入 Sportmonks
数据、渐进并存、约束未来解析代码、"数据是网站的重中之重,要严谨"。

执行前用一个 8-agent 只读审计工作流(`wf_869e24c4-4ed`)对三库 schema、
migration 机制、ingest 解析、消费方与文档契约做了穷尽复核,并 SSH 只读实读
cc 服务器 `worldcup.db` 的真实字段设计(未拉取任何数据)。

### 38.2 审计的决定性发现

**core 库(`allwin.db`)里已经存在一套完整的 provider-neutral canonical 比赛
身份/状态框架,但从未被任何生产代码写入——建好后闲置。**

```
backend/migrations/core/0003_schedule_state_v1.sql   911 行严格 DDL(已 applied,version=3)
backend/schedules/state.py                          1,480 行命令实现
backend/schedules/fotmob_schedule.py                  445 行 provider 适配器样板
tests/backend/test_schedule_state_schema.py         2,274 行 / 52 个测试(全通过)
```

改造前实测:`schedule_match_identity`/`schedule_match_state_snapshot`/
`schedule_match_observation` 三表均 0 行,全仓 `backend/api/`、`backend/queries/`、
`frontend/` 对这套表的引用为 0——不是设计缺失,是从未通电。

同时确认报告的部分论断对 all-win **不成立**(需与外部报告区分对待,执行细节
以本条实测为准,不采信报告推测):
- "raw_json 每行复制、需要 source_artifact 去重" ✗ 实测 `bronze_ng_odds_snap`
  735,793 行 payload 合计仅 27MB,占全库 5.64%;真正占空间的是逐行重复的
  `payload_hash`/`poll_run_id`(合计 ~16%)。
- "migration 管理弱" ✗ all-win 的 `backend/db/migrate.py` 有
  (version,filename,checksum) 三元组身份校验 + 严格缺号检测,强于报告参照的
  cc 服务器。
- 磁盘容量告警(92% 已用)✗ 审计执行沙盒自己的视图,本机实测 `df -h /` 为
  54%(15Gi 可用),非本机真实情况。

### 38.3 已完成:Phase 0(安全基线)+ Phase 1(身份回填,不含状态快照)

**关键决策修正**:`analysis/schedule_state_migration_trial/` 里已有的试验代码
论证过"身份回填安全,状态快照回填不安全"——`dim_match` 现有列反映的是"当前
已知最新值",不是"历史观测时刻";一次性回填会把 16,931 条 `observed_at`
全部伪造成同一个"回填运行时刻",违反 CLAUDE.md §6.2,且污染
`schedule_rest_feature` 依赖的 point-in-time 防泄漏语义。因此本轮**只做身份
回填**,状态快照留给未来的"实时双写"改造(需要先补一层 FotMob 原始 API
payload → `fotmob_schedule.normalize_raw_schedule_payload()` 期望的
`{artifactProvenance, details, fixtures}` 精确形状的转换器,当前不存在,
不在本轮仓促实现)。

新增/改动:
- `backend/db/connections.py`:`connect_rw`/`get_connection` 补
  `PRAGMA recursive_triggers = ON`(审计发现的真实洞:此前 append-only
  触发器可被 `INSERT OR REPLACE` 的隐式 DELETE 绕过,`migrate.py`/`state.py`
  各自的连接已开,唯独共享运行时连接工厂没开)。
- `backend/cli/schema_baseline.py` + `tests/backend/test_schema_baseline.py`
  (7 测试):三库表清单/行数/DDL sha256/关键列空值率快照与比对,后续每个
  Phase 前后都靠它证明零漂移。
- `backend/cli/backfill_schedule_identity.py` +
  `tests/backend/test_backfill_schedule_identity.py`(5 测试):`--dry-run`
  默认、`--commit` 显式;回填前对 `dim_match` 做完整性闸门(数量/非空/去重/
  类型),不通过直接拒绝;`UNIQUE(provider, provider_match_id)` 保证天然幂等。

**已对生产库执行 `--commit`**:`schedule_match_identity` 从 0 行填到
**16,931 行**,与 `dim_match` 一一对应,耗时 1.7 秒(直接回答了审计里
"触发器写入性能未知"的疑虑)。`schedule_match_state_snapshot`/
`schedule_match_observation` 按上述决策保持 0 行。

### 38.4 验证(真实命令,全部实跑)

```
python -m backend.cli.backfill_schedule_identity            → would_insert=16931, would_skip=0
python -m backend.cli.backfill_schedule_identity --commit    → inserted=16931, skipped=0(1.7s)
python -m backend.cli.backfill_schedule_identity --commit    → inserted=0, skipped=16931(幂等重跑)
三库 PRAGMA integrity_check                                  → ok / ok / ok
python -m backend.cli.schema_baseline --compare <before.json> → 0 regression, 0 schema_drift
200 场随机抽样(种子固定 20260811)逐字段核对                  → 200/200 一致
python -m backend.db.migrate --status                        → 三库均 pending=none
pytest tests/backend/ 全量                                   → exit 0(含新增 12 个测试)
python -m backend.cli.export_openapi && npm run gen:api
  && npm run check:api-drift                                 → 无漂移
cd frontend && npx tsc --noEmit && npx eslint app components lib
  && npx vitest run                                           → 通过 / 通过 / 77 passed
deploy/scripts/backup_sqlite.sh(改动前后各一次)+
  deploy/scripts/restore_verify.sh                            → 3 库 checksum/integrity_check/
                                                                 migration 全部通过
```

### 38.5 未完成(明确列出,不是被略过)

- Phase 1 剩余部分:状态快照实时双写(需要先设计 FotMob payload 转换器,
  建议独立排期,不建议在时间压力下仓促接线);
- Phase 2(canonical_team/competition/season/stage/venue/player 维度表 +
  provider_entity_xref 泛化)、Phase 3(指标注册表,拆解 `fact_team_match_stats.
  extra_json` 里 41 个键名混乱的指标)、Phase 4(赔率规范化,`market`/
  `company_id` 建维度表)、Phase 5(provider 适配器契约,约束未来解析代码)
  均未开始,完整方案见 `/Users/wanglujun/.claude/plans/typed-plotting-horizon.md`;
- 审计还发现但本轮未处理的真实问题(留作后续):`dim_match` 302 个 Team_ID
  里 8 个同 ID 多名、72 支球队无中文名、`LEAGUE_META` 漏登记 4 个联赛
  (48/57/61/268,共 1,311 场比赛在注册表外)、`fact_team_match_stats.
  extra_json` 键名污染(如 `matchstats.headers.tackles` FotMob i18n 键直接
  泄漏成指标名)、赔率侧同一博彩公司在不同 provider 用不同 id 且等价关系只存在
  于 Python 常量里、`docs/architecture.md`/`docs/data-plan.md`/
  `backend/migrations/core/README.md` 均有文档漂移。
- Sportmonks 数据本轮未接入,`worldcup.db` 仍留在 cc 服务器,只借鉴了字段
  设计(统一 provider 信封、比分分段存储、`is_placeholder` 占位球队等已记入
  上述 plan 文档)。

## 39. 72 支球队中文名回填:Qwen 翻译 + 独立 WebSearch 双重核验(2026-08-11)

### 39.1 背景

Canonical v2 审计(§38)发现 `dim_team_i18n` 覆盖 302 支球队里的 230 支,
72 支英冠/荷甲/葡超/巴甲球队(League_ID 48/57/61/268)完全没有中文名,
API 回落显示"球队名称待同步"。用户授权用 Qwen API(套餐即将到期,当天用完)
调用专门翻译能力处理。

### 39.2 执行方式

仓库已有 `backend/i18n/seed_team_names.py`,是删除过一个"单次翻译未经独立
核验就写库"的模块后新建的 fail-closed 版本:`--source
qwen_max_websearch_verified` 要求每一行的 `method` 落在双重验证白名单内
(`qwen_websearch_agree`/`websearch_override`/`websearch_confirmed_upgrade`/
`no_established_name_own_judgment`),不接受"只跑了 Qwen 没有独立核验"的行
冒用这个 source 标签。本轮严格按这套既有纪律执行,没有走捷径:

1. 装了缺失的 `dashscope` 依赖(在 `requirements.txt` 里但 venv 未装),
   调用 `qwen-max`(通用推理模型,不是字面直译的 `qwen-mt-plus`)为 72 支
   球队各生成一条候选中文名 + 置信度 + 理由。
2. 6 个并行 general-purpose agent(每个 12 支球队)对每一条候选做真实
   WebSearch 交叉核验(查百度百科/中文维基百科/球迷屋/懂球帝等中文体育
   媒体的实际通行叫法),不是照抄 Qwen 结果——最终 72 条里 **60 条与 Qwen
   一致确认**,**7 条被 WebSearch 纠正**,**5 条被升级为更准确的通行全称**。
3. 合并后跑 `seed_team_names.py` 既有的 fail-closed 门禁(CJK 非空、
   不退化成原文、批内/跨批不撞名、team_id 必须在给定联赛真实出现过),
   `--dry-run` 确认通过后再 `--live` 写入,写入前后各做一次
   `backup_sqlite.sh`。

### 39.3 过程中发现并纠正的一处自身错误

给 batch 1 agent 的背景提示里,误把 `team_id=7733`(Vitoria)当成葡超的
Vitória de Setúbal(实际真实 `League_ID=268`,是巴西巴伊亚州萨尔瓦多的
Esporte Clube Vitória),导致该条被核验成"塞图巴尔胜利"。写库前用真实
`dim_match` 数据逐条核对了全部 72 条的联赛归属(发现且仅发现这一处误判),
重新 WebSearch 确认真实身份并改回"维多利亚"(与 Qwen 原始候选一致)。

### 39.4 验证(真实命令)

```
python -m backend.i18n.seed_team_names --dry-run(两组:48/57/61 + 268) → gates all passed
backup_sqlite.sh                                     → 3 库 integrity_check=ok
python -m backend.i18n.seed_team_names --live(两组)  → rows_written=52 / rows_written=20
三库 PRAGMA integrity_check                          → ok
覆盖率核对:302 支球队 / 302 条 dim_team_i18n         → 100% 覆盖(此前 230/302)
全表撞名检查(GROUP BY name_zh HAVING COUNT>1)       → 空(无撞名)
backend.queries.teams.team_display_map() 抽样        → 正确读取新写入的中文名
python -m backend.cli.schema_baseline --compare      → 0 regression, 0 schema_drift
pytest tests/backend/test_seed_team_names.py
  tests/backend/test_league_scope.py                 → 23 passed
pytest tests/backend/ 全量                            → exit 0
```

### 39.5 数据留痕

72 条候选、6 批 WebSearch 核验结果与最终合并 artifact 保存在
`runtime/research/team-i18n-batch5/`(`qwen-candidates.json`/
`verified-batch1~6.json`/`final-results.json`),与既有
`team-i18n-nordic`/`team-i18n-jka`/`team-i18n-5leagues` 目录同一惯例,
供后续审计追溯每条译名的判定依据。`dim_team_i18n.source` 分布:
`qwen_max_websearch_verified` 272(200 原有 + 72 本轮)、`workflow_verified`
28、`manual_seed` 2。

## 40. NowGoal 采集网络根因排查:THORDATA_PROXY 对 NowGoal 不可用(2026-08-12)

**触发**:用户要求手动触发一次西甲本轮 NowGoal 赔率采集。执行后发现两个此前
未被发现的真实问题,均已第一手网络验证(非推测)。

### 40.1 T+7 候选发现窗口从未真正生效(已修复)

`backend/cli/poll_nowgoal.py::run_due_poll()` 调用 `upcoming_precise_matches()`
时沿用了默认 `window_hours=72`——而 `poll_decision()` 里 2026-08-11 新加的
"首次发现即采"逻辑只在候选已经进入这个函数之后才会判定,72h 外的比赛在
SQL 层就被排除,首次发现分支形同虚设。西甲首轮开球在 T+3~T+10 天,72h
候选池直接是空的。修复:候选发现窗口单独放宽到 `DISCOVERY_WINDOW_HOURS=168`
(T+7),已进 72h 窗口内比赛的实际节流节奏(2-72h/0-2h 档)不变。新增回归测试
`tests/backend/test_pipeline_e2e.py::TestT7DiscoveryWindow` 锁定。

### 40.2 NowGoal 直连返回"看起来成功但 Data 静默为空"(已修复检测,未修复网络)

真实交叉验证(同一天的请求,分别用三种网络路径测试):

| 路径 | 结果 |
|---|---|
| 本机直连(无代理,launchd worker 的实际路径) | HTTP 200,合法 JSON,但 `Data` 字段为空——`looks_blocked()` 认不出来,过去被误判为"该日期没有赛程" |
| `THORDATA_PROXY` + httpx | `SSLError: UNEXPECTED_EOF_WHILE_READING`(连续 3 次复现,非偶发) |
| `THORDATA_PROXY` + curl_cffi(`impersonate="chrome131"`,即 `nowgoal_archive.py` 的既有策略) | `SSLError: WRONG_VERSION_NUMBER` |
| 本机某个人工作站已有的本地代理(非项目配置,不可复现于生产) | 成功,单次请求拿到 197~1184 行真实日程 |

**结论:`THORDATA_PROXY` 目前对 NowGoal 不可用**(与 CLAUDE.md §14.1 已知的
"FotMob 侧 Thordata 拒绝大陆 IP"是同一类问题,但这是本轮才第一次针对
NowGoal 单独验证到)。`backend/providers/nowgoal.py::_http_get()` 此前完全
不读取 `THORDATA_PROXY`(直连),现已改为**无代理直接拒绝执行**并抛出明确
错误(不再允许"HTTP 200 + 空 Data"伪装成正常轮询结果)——这是诚实性修复,
不是网络问题本身的修复。真正让 NowGoal 采集在生产环境跑通,需要换一个
真实能连到 nowgoal26.com 的代理出口(住宅代理或其它供应商),这一步
`UNVERIFIED`,不在本次改动范围内。

### 40.3 附带修复:西甲球队别名缺口(已修复)

人工核对确认真实数据后,补了 4 条 NowGoal 简称 → canonical 别名(此前完全
缺失,导致比赛解析置信度卡在 0.5,进不了 auto_ok):

| canonical_team_id | 补充别名 | 原因 |
|---|---|---|
| 9866 | alaves | dim_match 存的是"Deportivo Alaves",NowGoal 用"Alaves" |
| 9783 | deportivo la coruna | dim_match 存的是"Deportivo A Coruña" |
| 8315 | athletic bilbao | dim_match 存的是"Athletic Club" |
| 8558 | rcd espanyol | dim_match 存的是"Espanyol" |

补齐后阿拉维斯 vs 赫塔菲(titan_id 3013642 ↔ fotmob 5868011)重新解析,
`confidence=1.0, review_status=auto_ok`,不再需要人工在 `/admin` 确认。

### 40.4 真实产出

手动通过工作代理拿到该场真实赔率并落库:`bronze_ng_odds_snap` 新增 9 条
(1x2/让球/大小球 × Bet365/另外两家),三库 `integrity_check` 均 `ok`。

### 40.5 最终解法:独立的 NowGoal 专用代理(2026-08-12 补记)

用户提供第二个 Thordata 配置(越南出口,`td-customer-...-country-VN`,与
FotMob 用的英国出口是完全独立的账号配置)专门测试。结果:

- 英国出口(`THORDATA_PROXY`)与越南出口对 NowGoal 表现**不同**——英国出口
  稳定被 Thordata 服务端拒绝(`403 Does not support mainland China servers`,
  用 `curl_cffi`/`httpx` 发 HTTPS 请求时这个拒绝页会被误报成 TLS 握手失败);
  越南出口稳定成功,拿到真实数据(2 次独立验证,789/788 行日程)。具体为何
  两个配置对同一目标行为不同,原因未知,如实按经验值使用,不代表已理解
  Thordata 内部路由逻辑。
- 新增独立环境变量 `THORDATA_PROXY_NOWGOAL`(`.env` / `.env.example`),
  `backend/providers/nowgoal.py::_http_get()` 改为读取这个变量而非复用
  `THORDATA_PROXY`——两个数据源用各自验证过真正能通的出口,不共用一个
  可能对另一方失败的配置。
- 用真实链路(entity_resolution → fetch_odds → ingest_odds_records)对本轮
  西甲 10 场里的 6 场完成解析 + 抓取 + 落库:阿拉维斯/赫塔菲、
  埃斯帕尼奥尔/莱万特、塞维利亚/巴列卡诺、桑坦德竞技/比利亚雷亚尔、
  塞尔塔/奥萨苏纳、马德里竞技/马拉加,每场 9 条快照(1x2/ah/ou × 3 家公司)。
  剩余 4 场(含拉科鲁尼亚、贝蒂斯、毕尔巴鄂竞技相关场次)本轮手动核对用的
  简易姓氏匹配脚本没有命中(疑似重音符号等命名细节,不是代理问题),交给
  自动 worker 下一轮(15 分钟节流到期后)用完整的 `resolve_match` 流程处理。
- 三库 `integrity_check` 均 `ok`;`pytest tests/backend/` 全量通过
  (含新增 `TestT7DiscoveryWindow` 回归测试)。

## 41. 球队象限图(M8)—— P3 收尾(2026-08-12)

### 41.1 修掉的真实空白

`/league/[id]/team-stats` 此前只有 5 张纯文本 top10 榜:20 队联赛里
**第 11–20 名完全不可见**。同时 `silver_team_season_stats` 的 xG 拆解列
(`avg_expected_goals_open_play` / `_set_play` / `_non_penalty`,767 行全非空)
前端零消费——运动战 xG vs 定位球 xG 是全站独一份的战术叙事素材。

新增 `frontend/components/league/TeamQuadrantChart.tsx`:一张三视角可切换的
象限散点图,20 队全部上图,象限直接给中文名(不是"高 xG 低 xGA")。

| 视角 | 横轴 | 纵轴 | 数据来源 |
| --- | --- | --- | --- |
| 攻防 | 每场创造 xG | 每场被创造 xG(轴反转) | silver + `fact_league_table` xg 档 |
| 战术 | 运动战 xG/场 | 定位球 xG/场 | silver(此前零消费) |
| 出手 | 每场射门数 | 每场 xG | silver |

### 41.2 后端改动

`backend/queries/league_stats.py::team_season_stats` 增加三列 xG 拆解,并
LEFT JOIN `fact_league_table`(`table_type='xg'`)换算出 `avg_expected_goals_conceded`
(= `xg_conceded / played`)。两源同口径已逐队核对:曼城 2025/2026
silver `avg_expected_goals` 1.877 == `71.33/38`。

付费深度字段(角球/红黄牌/零封/BTTS)仍然物理不进 SQL——xG 拆解是免费
字段 xG 的细分,不是新开放的付费字段。

### 41.3 数据诚实

- 缺任一坐标的球队**整点丢弃**,不补 0。0 在 xG 语境里是"一次机会都没创造"
  的真实值;补 0 会把没有数据的球队画成全联赛防守最好。
- `fact_league_table` 的 xg 档并非每个联赛赛季都有(法甲 2020/2021、
  J1 2026、瑞超 2024 都没有)。缺失时"攻防"视角**禁用并写明原因**,
  默认落到"战术",不静默回退。
- 参考线是本联赛本赛季的均值,摘要里明说不能跨联赛比较。
- 手机 375px 宽塞 20 个中文队名必然叠字,改为只标注离均值最远的 6 支
  (按各轴标准差归一化);其余球队点开有 tooltip,队名一个不少地列在
  下方分组清单里——这是展示密度取舍,不是丢数据。

### 41.4 修掉的一个自相矛盾 bug

首版 `quadrantOf` 按**数值高低**给象限命名,而配色按**好坏**判定。
在"每场被创造 xG 越低越好"的反转轴上,两者正好打架:真正攻守兼备的
8 支球队(切尔西、曼城、利物浦、阿森纳、水晶宫、布莱顿、曼联、纽卡斯尔联,
已用 SQL 逐队核对)被标成"对攻型",颜色却是绿的。

改法:`quadrantOf` 接受 `lowerIsBetterY`,索引统一按好/差;配色直接复用
同一个判定函数,单一真源。前端与 E2E 各有一条回归断言守着。

### 41.5 验证

- `python3 -m pytest tests/backend` 全量通过(新增
  `test_xg_breakdown_and_conceded_for_quadrant_chart`:47 有 xg 档 → 2.0,
  87 没有 → **必须是 null 而不是 0**);
- `npx vitest run` 103 passed(新增 5 条,含"y 越低越好时象限翻转");
- `npx tsc --noEmit` / `npx eslint .` 干净;`npm run build` 通过;
- `CI=1 npx playwright test` 20 passed(新增球队象限图 E2E,含禁用视角断言);
- 浏览器实测 375px 与 1280px 两个宽度,并与直接 SQL 逐队核对分组结果一致。

## 42. 赛前市场卡:P0-P3 实施(2026-08-12)

### 42.1 起点

站长原话:"这个是比赛详情页...从一个中国30-40岁男性竞彩用户,手机端来说,
他们会再次访问这个风格的网站吗?"以及后续反馈"所有的赛前比赛详情页都要
服务于足球比赛的投注选项...我们有同等fotmob的比赛数据,就要把每个选项
计算出他的归因、他的驱动因子...在比赛详情页,赛前,页面就是给出意见,
然后折叠驱动因子"。

实测 40 场未开赛比赛的真实 API 输出,确认赛前页此前只有:页头、一句
近5场文字、两列 W/D/L 列表、1 张 form 折线、1 张射门散点、33/40 场是灰色
"暂无赔率"框。根因是"赛前之墙":所有赛后事实表(含 `int_match_features`
滚动特征表)对 `status='NotStarted'` 精确为 0 行,未开赛比赛唯一能用的
"这场比赛特有"数据是两队历史聚合——此前完全没用。

### 42.2 决定性发现:意见的正确形态

用 12,372 场完赛比赛做时间序回测(赛前两队各自近 N 场均值 → 本场实际结果,
不用当场或未来数据):

- 同场相关性(本场角球 vs 本场其它指标)与**赛前预测**相关性差异巨大:
  角球从同场 r=0.553 掉到赛前预测 r=0.078——只能解释,不能预测。
- 14 因子多元回归对"猜训练集均值"基准的 RMSE 改善仅 0.5%-2.9%——单场
  足球本质上几乎不可预测,这不是数据差,是行业共识量级。
- 但**分档命中率可以单调**:黄牌线 3.5 分五档,最低档 37.5% 过线 →
  最高档 66.6% 过线,+29pp;这才是"意见"唯一站得住脚的形式。

### 42.3 P0 止血

- **射正超算修复**:射门图把"射正"算成 `Outcome IN ('Goal','AttemptSaved')`,
  但 `AttemptSaved` 混了门将扑救与被后卫封堵。实测 26,067 队场:我们算
  7.75/场 vs FotMob 官方 `ShotsOnTarget` 4.36/场,完全吻合仅 6.8%。
  `recent_shot_map_spec()`(`backend/queries/matches.py`)新增
  `official_stats` 字段,前端汇总数字改用官方口径,逐次射门点保留原始
  Outcome 标注为"被扑/被挡"(不假装能区分两者)。
- **赔率 initial/latest 显示修复**:`OddsTimeline.tsx` 的
  `summarizeCompanyOdds()` 重写摘要行生成——旧逻辑不管几条快照都固定打
  "初盘"/"最新"标签但都用 `flatOddsGroup`(优先 latest)取值,导致"初盘"
  行显示的其实是最新值,真实变化(如 Crown 2.85→2.83)从未展示。新逻辑
  每公司一行,变化用"→"箭头标出,不虚构分离的观测时间戳。同时给
  `bronze_legacy_odds_summary` 的重复行标注 `LEGACY_SOURCE_ZH`(存档
  批次来源),不再让同一 provider 两行不同数字看起来像未解释的 bug。
- **概率来源自相矛盾文案修复**:`backend/studio/bundle.py` 的
  `probability_source='UNAVAILABLE'` 曾经落进"概率来自...已发布快照"
  分支,同一句话自相矛盾。改成三态各自独立成句。
- **P0-4(四联赛历史补采)未执行**:Thordata 英国/越南两个出口对 FotMob
  均返回 `403 Does not support mainland China servers`(3 次重试稳定
  复现,非偶发)。直连本机测试成功(200,拿到 47 联赛完整赛季列表),
  但补采需数千请求,直连有让本机 IP 被 FotMob 限流的风险,可能连累
  现有实时轮询——已上报用户决策,未擅自执行。

### 42.4 P1 球队近 N 场聚合层

新建 `backend/queries/team_form.py::team_recent_profile()`:两队各自近
N 场历史,每项指标(复用 `match_report.py::TEAM_STAT_KEYS` 同一份 37-key
白名单)返回 for/against 场均值 + **各自独立的非空样本量**——
`touches_opp_box` 全库仅 89.05% 覆盖,不能和 100% 覆盖的指标共用一个
"共 N 场"。样本 <3 场返回 `avg=None`,绝不补 0(0 是"这场角球确实 0 个"
的合法值)。用阿森纳最近 10 场英超角球场均 5.1 与直接 SQL 核对完全吻合。

### 42.5 P2 离线标定层

新建 `backend/eval/calibrate_markets.py`(CLI,离线运行,绝不进 API 请求
路径)+ `platform.db` 新表 `market_calibration`
(`backend/migrations/platform/0013_market_calibration.sql`)。

方法论:预估值 = 两队各自近 10 场历史均值之和;严格时间序滚动(第 k 场
只用第 k 场之前的历史);前 80% 定 5 档边界,后 20% **从未参与定档**的
外样本上验证命中率单调性;外样本不单调 → `signal_grade=NULL`,前端据此
只展示数据面板,不给方向性结论。

真实标定结果(2026-08-12,跨联赛合并,`league_id=0` 哨兵):

| 市场 | 线 | 外样本 spread | 星级 |
| --- | --- | ---: | --- |
| 罚牌 | 3.5 | +22.9pp | ★★ |
| 大小球 | 2.5 | +16.6pp | ★★ |
| 角球 | 9.5 | +7.9pp | 不单调,无星级 |
| 角球 | 8.5 / 10.5 | +7.1pp / +10.6pp | ★ |

注:会话内早期用同一批数据做的**同场相关性**探索给出角球"±29pp"的印象,
那是方法论错误(同场相关≠赛前预测力,见 §42.2)。这里的表才是真实、
经外样本验证的数字。`league_id=0` 而非 `NULL` 表示跨联赛合并——SQLite
的 UNIQUE 约束把每个 NULL 当成互不相等,用 NULL 会让约束形同虚设。

### 42.6 P3 市场卡框架

新建 `backend/queries/market_cards.py::match_market_cards()` +
`GET /api/v1/matches/{id}/markets`(门禁与 `/report` 同级,只看联赛门禁,
不分付费档——这是本站建立信任的内容,不是收费深度报告)+
`frontend/components/matches/MarketCard.tsx`(结论区常驻 + `<details>`
折叠归因)。挂载在赛前页 QuickView 之后、"数据可视化"之前的新区块
"数据倾向"。

四种诚实降级路径,各自独立文案(不能混用):
- `data_quality='ok'` 且 `signal_grade` 非空:正常渲染"数据倾向:偏大/偏小"
  + 星级 + 历史命中率;
- `data_quality='ok'` 但 `signal_grade=None`(bucket 查到了、hit_rate 是
  真实数字,但那条线外样本不单调):**不渲染任何倾向文案**,只说"这个
  盘口线在样本外测试中不够稳定,暂不给出倾向"——这是最容易被漏判的
  状态,因为"有数字"极易被误当成"有信号"直接展示;
- `data_quality='no_calibration'`(该市场线从未标定,或标定时总样本
  <100 场):提示"暂无历史回测数据";
- `data_quality='insufficient_sample'` / `'no_history'`:两队历史不够 /
  联赛完全没有历史事实表,提示区分开(前者是"这队客观上没数据",后者是
  "我们的采集缺口")。

浏览器实测(阿拉维斯 vs 赫塔菲,5868011):罚牌卡"数据倾向:偏大 ★★,
60%命中率(样本213场)";大小球卡"数据倾向:偏小 ★★,49%(样本493场)";
角球卡诚实显示"有历史数据,但这个盘口线在样本外测试中不够稳定"且不给
方向。折叠区展开后显示两队真实的 xG/射正/绝佳机会/犯规/红牌对比数据。

### 42.7 验证

- `python3 -m pytest tests/backend` 全量通过(新增
  `test_shot_map_official_stats.py`/`test_team_form.py`/
  `test_calibrate_markets.py`/`test_market_cards.py`,以及
  `test_studio.py`/`test_five_critical_product_fixes.py` 的补充断言);
- `npx vitest run` 119 passed(新增 `shot-map-explorer.test.ts`/
  `market-card.test.tsx`,`odds-timeline.test.tsx` 补充"有变动"箭头断言);
- `npx tsc --noEmit` / `npx eslint .` 干净;`npm run build` 通过;
- `CI=1 npx playwright test` 21 passed(新增"赛前市场卡"E2E,覆盖三张卡
  渲染、有信号/无信号两种真实降级路径、折叠区默认隐藏/展开后可见、
  投注措辞红线);`tests/e2e/seed_e2e.py` 新增标定步骤(用同一套
  `calibrate_markets.run()` 对拷贝的真实 core 数据跑标定,E2E 断言看到
  的是与 dev 环境一致的真实历史命中率,不是编的测试桩数据);
- 浏览器实测 375px 与 1280px 两个宽度,driver_factors 折叠区展开验证
  真实数据渲染,网络面板确认 `/markets` 端点走 `public, s-maxage=300`
  公共缓存。

### 42.8 未完成事项

- **P0-4 四联赛历史补采**被 Thordata 代理阻塞,需站长决定:等代理恢复 /
  换代理配置 / 接受直连风险三选一。
- **仅标定了跨联赛合并(`league_id=0`)**,未按单联赛标定——单联赛样本量
  更小,可能因为达不到 `MIN_BUCKET_SAMPLE=20` 或外样本单调性检验而拿不到
  星级,这属于诚实的降级,不是缺陷,但尚未验证具体每个联赛的表现。
- **1x2/亚盘/半全场三个市场未标定、未上市场卡**——按 P2 计划这三个市场
  需要先跑标定才能决定上不上,本轮时间预算内只完成了罚牌/大小球/角球
  三个已验证信号的市场。
- **裁判尺度、角球转化率(`Situation='FromCorner'`)两个 P2/P3 计划中的
  子模块未实现**——已确认数据可行(角球转化率 42.1% 出射门/3.59% 进球,
  裁判赛前 100% 未知只能落完赛页),但受时间预算限制未编码。
