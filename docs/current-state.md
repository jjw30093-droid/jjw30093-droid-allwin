# 当前状态审计(docs/current-state.md)

> 本文件是可更新的"真实当前状态",由命令与只读查询建立,不写入 CLAUDE.md。
> 本次审计:2026-07-19,4 路并行只读审计(backend / frontend / allwin.db / 旧项目参考代码)。

## 0. 开工保护(已执行)

- `git status --short`(开工时):`M DESIGN.md`(用户未提交修改,保留不动)、`?? backend/verify/`、`?? data/`(未跟踪,保留)。
- `data/allwin.db` 已用 SQLite `.backup` 备份 → `data/backups/allwin-pre-migration-20260719.db`(406,061,056 B,与主库一致)。
- `PRAGMA integrity_check` → `ok`。
- CLAUDE.md 已替换为新工程宪法(2026-07-19)。

## 1. 数据库真实状态(data/allwin.db,387MB,WAL)

**18 张表,无视图,user_version=0(此前无 migration 体系)。**

| 层 | 表(行数) |
|---|---|
| Bronze dim | dim_match (11,115), dim_player (6,271) |
| Bronze fact | fact_match_events (212,727), fact_match_lineup (451,161), fact_player_match_stats (328,083), fact_season_player_stats (59,139), fact_shotmap (269,071), fact_team_match_stats (45,468), fact_league_table (600) |
| i18n | dim_team_i18n (30), dim_player_i18n (1,378) |
| 特征 | int_match_features (2,280) |
| Silver | silver_team_season_stats (120), silver_league_season_summary (6), silver_over_under_thresholds (36), silver_score_distribution (202), silver_goal_minute_buckets (42) |
| Gold | gold_wdl_predictions (760) |

### 联赛覆盖(重要:库已领先旧文档记载)

- dim_match 已含 **5 个联赛**:47 英超 2,660(2,280 完赛 + 380 NotStarted 26/27)、53 法甲 2,058、54 德甲 1,836、55 意甲 2,281、87 西甲 2,280。status 只有 `Finish`(10,735)/`NotStarted`(380)。
- match 级 fact 表五联赛基本全覆盖;但 **fact_season_player_stats 与 fact_league_table 只有英超**。
- **Silver / int_match_features / gold 全部只覆盖英超**,且构建于 2026-07-09,早于 2026-07-12 的五联赛合并 → 相对 Bronze 已 stale。直接重跑 build_silver 会静默把范围扩到五联赛,需显式决策。
- 数据小刺:意甲 2022/2023 有 381 场(真实保级附加赛 Spezia–Verona 重复对阵);silver_goal_minute_buckets 含 26/27 空壳行(count=0/pct NULL);dim_player_i18n 有 2 条孤儿 Player_ID。

### i18n 中文覆盖

- 球队:英超 30/30(100%);法甲/德甲/意甲/西甲 0%。
- 球员:英超 1,375/1,377 ≈ 99.9%;其他联赛(约 4,900 人)未翻译。翻译源 qwen-mt-plus 1,363 + 人工 15,needs_review=1 共 5 条。

### gold_wdl_predictions(预测完整性关键事实)

- schema:`match_id PK, league_id, season, lambda_home/away, lambda_*_is_fallback, p_home/p_draw/p_away, calibrated, updated_at, confidence, reason`。
- **没有 generated_at / created_at / 模型版本列**,只有批次共享的 `updated_at`;760 行全部产于 2026-07-09(19:42 与 23:40 两批)。
- 2025/2026 的 380 行:该赛季比赛 2025-08→2026-05 已全部踢完,而写入时间是 2026-07-09 → **确定为赛后回测产物,不可作为正式赛前战绩**,只能导入 `legacy_unverified`。
- 2026/2027 的 380 行:比赛 2026-08 之后开球,写入早于开球 → 可导入 draft,由管理员在开球前发布并锁定后才进 track record。
- 写入方式为整季 DELETE+INSERT(可整季重写),印证"当前产物,不是不可篡改账本"。

## 2. 后端真实状态(backend/,FastAPI + Python 3.13.5)

- **API 面**:仅 4 个 GET 路由,全部 `/api/league/{league_id}/...`:`overview`(免费)、`betting`(名义付费)、`matches`(免费)、`wdl-predictions`(付费核心 + 7 天有效期闸门)。CORS 只允许 localhost:3000,只放 GET。无 /healthz,无限流,无日志。
- **门禁现状**:`require_membership()` @ api_server.py:59-65 → `request.query_params.get("simulate_membership") == "paid"`。
  - `/betting` @ :217 调用了但**返回值被丢弃,实际对匿名完全开放**(付费聚合数据当前裸奔);
  - `/wdl-predictions` @ :356 真实分支:付费才带 p_home/p_draw/p_away(键级省略,合规),未付费给 tendency+locked。
  - **本次任务必须移除 simulate_membership,换成真实 entitlement。**
- **模型**:Dixon-Coles + isotonic 校准(纯 scipy/sklearn,无 LightGBM)。ρ=-0.005274。特征只用 4 个 l10 滚动 xG。工件:`backend/models/artifacts/wdl_baseline_params.pkl`(2,842B,2026-07-10,gitignored,唯一副本;sklearn 版本敏感)。
- **评估口径**:walk-forward,train 2020/21–2024/25,test 2025/26;test RPS 0.2143,对照为**历史主/平/客频率基线 0.2281**(不是收盘赔率共识,不得写错)。
- **写库者清单**:ingest_match/ingest_league/ingest_future_fixtures(Bronze)、seed_curated/translate_players(i18n)、build_silver(Silver)、build_match_features(特征,全表 DELETE+INSERT)、build_wdl_baseline + predict_wdl_future(gold,整季 DELETE+INSERT)、fix_event_scores(一次性已跑)、verify/merge_into_allwin(2026-07-12 已跑)。serving 层是 mode=ro 只读。
- **fotmob_client.py**:curl_cffi + Chrome TLS 指纹 + ThorData 住宅代理;**模块 import 时就要求 THORDATA_PROXY 存在**,离线工作也会挂 → 所有 ingest/scheduler 模块受牵连。
- **测试**:零。无 pytest/conftest/任何测试文件(2026-07-19 起本次施工新增 tests/)。
- **requirements.txt 未钉版本,且缺 dashscope**(translate_players.py 在用)。

## 3. 前端真实状态(frontend/,Next.js 16.2.10)

- next 16.2.10 / react 19.2.4 / typescript 5.9.3,依赖仅 next+react+react-dom。**无图表库、无测试(vitest/playwright 均无)、无 typecheck script**。
- AGENTS.md 规则:写代码前必须先读 `node_modules/next/dist/docs/`(存在,已确认);frontend/CLAUDE.md 仅 `@AGENTS.md`。
- 6 个页面全部 server component(全仓库无 "use client"):`/`、`(public)/league/[id]/{standings,matches,team-stats,players}`、`(member)/league/[id]/wdl-predictions`。无 loading/error/not-found/middleware/route handlers。
- 唯一 API client `lib/api.ts`:`NEXT_PUBLIC_API_BASE`(默认 127.0.0.1:8000),全部 `cache: "no-store"`,类型**手写**,付费字段在类型里刻意缺席。
- 概率卡页通过 `?membership=paid` → `simulate_membership=paid` 模拟付费(需随后端一起移除)。
- 设计:纯 CSS Modules + globals.css 金色/暖黑 token,暗色单主题;字体 next/font/google(Oswald + Noto Sans SC)——注意宪法 §11.2 要求字体自托管,后续需处理。
- 硬编码:首页 league 47、各页"英超"“26/27”标题、QUAL_LABELS 色映射等。

## 4. 旧项目审计结论(miaomiaodi.vip,只读参考)

**可复用(概念)**:微信 webhook SHA1 验签方案(改 hmac.compare_digest);"网页出码→公众号回码→webhook 绑 OpenID→网页轮询"的无密码 UX;OpenID 首次接触 lazy 建用户;**jti 白名单 + user_sessions 服务端撤销/设备数限制(最有价值的一块)**;解码时复验订阅到期的防御习惯。

**明确淘汰(已在旧代码定位确认)**:`random.randint(1000,9999)` 四位码(9000 值域 + 跨会话碰撞可致账号接管);进程内 `_sessions` 字典(多 worker 即失效);JWT 经 JSON 落入浏览器可读 session(30 天 bearer,XSS 即失窃);客户端不验签解析 JWT(伪造 role=vip 可骗过 UI);`users.openid` 单列承载全部身份 + `USER_` 前缀伪 OpenID;`role=vip` 混同订阅;`ENABLE_VIP_GATING` 未设时**默认全员 VIP**(默认开放,致命);前端自铸 user_id=0 超管 token(无 jti 不可撤销)+ 密钥降级回退;日志打 OpenID/jti。

**NowGoal 真实能力(如实记录)**:
- 比赛发现:无持久 id 映射,靠 `www.nowgoal26.com/ajax/SoccerAjax?type=6&date=...` 日程 + 球队名模糊匹配(3 日雷达),`_match_score` 同时给出 is_swapped(主客反转);
- 赔率:`ajax/soccerajax?type=14&id=<titan_id>` 每公司仅 `f`(初盘)/`l`(最新)两快照,**没有完整时间序列**;主要取 CID 8(Bet365)/31(Sbobet),不足回退第一家有效公司;
- 反转处理:1x2 交换 home/away;AH 交换并把盘口线取负;OU 不换(对称)——这套解析经验直接复用;
- closing_odds_watcher:60s 轮询、T-10min 单次 closing 快照、幂等去重;
- 历史回填能力:probe 结论写在旧仓 `backend/logs/nowgoal_xhr_probe.json`(本次未读),**allwin 侧标 UNVERIFIED**,详见 docs/data-sources.md。

## 5. 本次施工完成状态(2026-07-19 收尾)

MVP P0 已全部交付并真实验证(命令与退出码见当次会话汇报):

- **数据库**:migration runner + `data/platform.db`(24 业务表 + 触发器)+ `data/odds.db`(10 表);三库 `integrity_check` ok;迁移幂等。
- **认证**:opaque session(SHA-256)+ 微信 OA OAuth(Mock 已端到端验证,真实凭证 UNVERIFIED)+ Device Login + CSRF/Origin + create_admin CLI;production 缺配置/Mock fail-fast。
- **权限**:role 与 plan 分离;free/pro/premium 权益种子;免费预测 DTO 物理省略另两项概率(pytest + Playwright 双层断言);`simulate_membership` 前后端全部移除。
- **预测登记簿**:760 条导入(380 条 2025/26 → `legacy_unverified`,380 条 2026/27 → `draft`);锁定不可改由 DB 触发器 + service 双层强制;track-record 无正式样本时诚实空态;评估 CLI 如实报 0。
- **API**:`/api/v1` 50 paths;OpenAPI → TS 类型生成链路;缓存边界(公开 s-maxage / 私有 no-store)已验证;旧 `/api/league/*` 保留为 deprecated 兼容层。
- **NowGoal**:Provider 解析器(日程 JS Date 元组、1x2/AH/OU、主客反转+盘口取负)+ xref/alias + hash-diff 快照 + poll_nowgoal CLI(支持 --offline-fixture);真实端点历史能力 UNVERIFIED(见 docs/data-sources.md)。
- **前端**:17 条路由(新 11 页 + 保留旧 6 页);ECharts 单图表库;lint/typecheck/vitest/build 全绿;Playwright E2E 6/6(匿名/免费概率/Mock 登录/会员解锁/Admin 拒绝/Studio 导出)。
- **Worker/部署**:job_runs 全生命周期;deploy/nginx+systemd+release/backup/restore 脚本;备份→恢复演练通过;S3 未配置(如实标注仅本地)。

已知遗留(不阻塞上线,见汇报"仍未完成"):dev(Turbopack)在自动化浏览器下水合停滞(生产构建正常,E2E 因此跑生产构建);/model/metrics 与 /products 无 response_model(前端手写局部类型);admin users 列表不返回订阅 ID(撤销需手输)。

## 6. 环境

- Python 3.13.5(.venv);2026-07-19 新装 pytest / httpx / argon2-cffi。
- 关键既有依赖:fastapi 0.139.0、pydantic 2.13.4、uvicorn 0.51.0、scikit-learn 1.9.0、pandas 3.0.3、curl_cffi 0.15.0。
- Node:frontend/node_modules 已安装(next 16.2.10)。
