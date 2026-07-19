# 数据源(docs/data-sources.md)

> 依据真实代码撰写:`backend/providers/nowgoal.py`、`backend/providers/fotmob_snapshots.py`、
> `backend/ingest/odds_snapshots.py`、`backend/cli/poll_nowgoal.py`、
> `tests/fixtures/nowgoal/`(2026-07-19 核对)。
> 纪律:能力必须与验证状态一起写;不可验证一律标 **UNVERIFIED**,不得夸大。

## 1. FotMob(已验证)

- **能力(已真实验证)**:五大联赛(英超 47 / 法甲 53 / 德甲 54 / 意甲 55 / 西甲 87)
  比赛与球员数据已落库 core(allwin.db):dim_match、fact_match_events、fact_shotmap、
  fact_team_match_stats、fact_player_match_stats、阵容等(行数见 `docs/current-state.md`)。
  英超另有 fact_league_table / fact_season_player_stats 与 26/27 赛程(NotStarted)。
- **采集方式**:`backend/fotmob_client.py`(curl_cffi Chrome TLS 指纹 + ThorData 住宅代理)。
  该模块 **import 时就要求 `THORDATA_PROXY` 环境变量**;
  `providers/fotmob_snapshots.fetch_match_payload` 因此延迟 import 并在缺失时抛可读异常,
  离线场景改用已存 payload 调 `extract_*` 纯函数。
- **阵容/伤停快照**(odds.db):`extract_lineup_snapshot` / `extract_sideline_snapshot`
  从 match_details 的 pageProps 提取最小 canonical 子集(球员按 id 排序保证 hash 稳定;
  payload 无阵容时给空侧,视为一次合法观察,交给 hash-diff)。
- **时间声明**:FotMob 不声明阵容/伤停的来源更新时间 → `source_updated_at` 恒 NULL;
  另注意其 SSR 页面有 5–20 分钟 CDN 缓存,观察时间只能算 `observed_at`。
- **限制**:dim_match 只有比赛日期,无开球时刻(下游 kickoff 口径因此按比赛日
  00:00 UTC 保守处理,见 `docs/prediction-integrity.md`)。

## 2. NowGoal(部分验证,逐项标注)

### 2.1 端点与格式

Base:`https://www.nowgoal26.com`(`providers/nowgoal.py`)。

| 端点 | 用途 | 验证状态 |
|---|---|---|
| `GET /ajax/SoccerAjax?type=6&date=YYYY-MM-DD&order=time&timezone=8&flesh=<rand>` | 当日日程 | **已真实验证(2026-07-19 probe,HTTP 200)**:响应 Data 文本按行;`A[` 开头的行是比赛,形如 `A[1]=[2912838,3,505,498,'Molde','Brann',...]`;titan_id 为 `=[` 后第一段数字;开球时间是引号内 JS Date 元组 `'2026,6,18,16,00,00'`(**月份 0 基**,按 probe 样例与查询日期对齐推断) |
| `GET /ajax/soccerajax?type=14&t=1&id=<titan_id>&h=0&s=0&flesh=<rand>` | 单场赔率 | **UNVERIFIED**:响应格式按旧项目(miaomiaodi.vip)代码审计构造(每公司 `f`=初盘 / `l`=最新;市场 `euro`(1x2: u=主 g=平 d=客)/ `ah`(u=上/主 g=盘口线 d=下/客)/ `ou` 同形),真实端点未在本项目实测 |

解析器(`parse_schedule` / `parse_odds`)是纯函数,离线测试用
`tests/fixtures/nowgoal/{schedule_sample.txt, odds_sample.json, poll_fixture.json}`
(fixture 内也注明各自的验证状态)。

### 2.2 快照能力边界(重要)

- **每公司每市场只有两组快照:`f`(初盘)与 `l`(最新)——不是完整时间序列。**
  本系统靠自己的轮询把"最新"的历次变化累积成时间线;两次轮询之间发生又回撤的变化
  观测不到。任何页面/文档不得把它描述为"完整多公司赔率时间序列"。
- 公司选择:优先 CID 8(Bet365)、31(Sbobet)(`DEFAULT_TARGET_CIDS`);
  一家都没命中时回退第一家有效公司。其他公司覆盖 **UNVERIFIED**。
- 主客反转归一(`normalize_for_inversion`,依据 `dim_match_xref.home_away_inverted`):
  1x2 交换 home/away;AH 交换双边并把盘口线取负;OU 对称不换。
- WAF:响应命中 `just a moment`/`cloudflare` 等标记即抛 `WAFBlockedError`,
  调用方跳过并记 source_health,不重试硬闯。

### 2.3 历史回填

**UNVERIFIED。** 未在本项目验证 NowGoal 是否提供任何历史赔率回填端点;
旧仓另有 probe 记录(`miaomiaodi` 仓 `backend/logs/nowgoal_xhr_probe.json`,本项目未读)。
在验证之前,odds.db 的时间线起点 = 本系统开始轮询之日,更早的只有当时抓到的 `f`(初盘)。

## 3. 轮询策略

- **锁定目标(CLAUDE.md §6.3)**:赛前 72 小时起每 15 分钟;赛前 2 小时起每 5 分钟。
- **当前实现(如实)**:`allwin-worker.timer` 每 15 分钟触发任务链,其中
  `nowgoal_snapshot` 步骤跑一轮 `backend.cli.poll_nowgoal`(当日日程 → 实体解析 →
  已映射比赛抓赔率)。**T-2h 加密到 5 分钟的分级轮询尚未实现**;上线赔率产品前需补
  (可行方案:独立 5 分钟 timer 只跑 nowgoal_snapshot,按开球时间过滤)。
- 单轮流程(`poll_nowgoal.run_poll`):日程失败 → 整轮终止并记 source_health;
  单场赔率失败 → 继续其余场次,末尾汇总。只有 `review_status ∈ (auto_ok, confirmed)`
  的映射才抓赔率;needs_review 不抓(等人工审核,见 `/api/v1/admin/xref`)。

## 4. 落库规则(hash-diff)

`ingest/odds_snapshots.py`,目标表 `bronze_ng_odds_snap`(每 (provider_match_id,
market, company_id) 一条序列)、`bronze_fm_lineup_snap`、`bronze_fm_sideline_snap`:

1. record → canonical JSON(`canonical_payload_json`:排序键、紧凑分隔符)→ SHA-256;
2. 与同序列最近一条 `payload_hash` 比较:**不变则跳过,变了才 INSERT**(append-only,
   从不 UPDATE 旧快照);
3. `market_phase`:按 core dim_match 日期粗判——比赛日尚未过去 → `pre_match`,
   否则 `unknown`。**dim_match 无开球时刻,无法精确判 `in_play`,代码不硬猜**;
   `FINAL`(开球前最后一个 pre_match 快照)的精确判定同样受此限制,待开球时刻数据补齐。

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
| FotMob 阵容/伤停 extract 纯函数 | 已验证(pytest 离线) |
| NowGoal type=6 日程端点与行格式 | 已验证(2026-07-19 probe,HTTP 200) |
| NowGoal type=14 赔率端点与格式 | **UNVERIFIED**(按旧项目代码审计构造,有离线 fixture 测试) |
| NowGoal 公司覆盖(除 Bet365/Sbobet 优先级设定外) | **UNVERIFIED** |
| NowGoal 历史回填 | **UNVERIFIED** |
| T-72h/15min + T-2h/5min 分级轮询 | 目标策略;当前仅 15 分钟统一轮询(worker timer) |
| in_play / FINAL 精确判定 | 受 dim_match 无开球时刻限制,当前为保守粗判 |
