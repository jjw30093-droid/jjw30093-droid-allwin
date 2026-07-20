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

### 2.4 实体解析安全规则(P0-B 收口,`backend/ingest/entity_resolution.py`)

跨源映射(NowGoal ↔ FotMob canonical)的自动通过(`review_status='auto_ok'`)是**自动候选**,
`verified` 恒为 0——只有管理员在 `/api/v1/admin/xref` 确认才 `verified=1`。

**召回 vs 身份证明**:候选比赛的**召回打分**用别名 ∪ dim_team_xref 直查的**并集**(历史
provider xref 帮助召回一个候选),但**并集本身不足以 auto_ok**——历史 provider xref 不得
单独充当身份证明,否则错误队名会被历史 xref 掩盖(见反例)。auto_ok 的**全部**必要条件:

1. **最终身份证明:本轮 `home_name`/`away_name` 经 `dim_team_alias` 独立、唯一地解析到候选
   比赛(按方向计算)的预期 canonical**;名称未知、错误或多 canonical 歧义 → `needs_review`
   (`home_team_name_mismatch` / `away_team_name_mismatch`);
2. 方向唯一,无同分歧义;
3. **双边都有可严格解析的精确 kickoff**:core 侧统一验证器 `normalize_exact_kickoff`
   (`kickoff_precision='exact'` ∧ 非空真实来源 ∧ 显式时区可解析),NowGoal 侧行内 kickoff
   (北京墙上时间,-8h 转 UTC)真实含时间部分且可解析;
4. 两侧 kickoff 差值可计算且 `|diff| ≤ 30 分钟`;
5. **provider 主客球队 ID 都存在、互不相同**,且本次映射蕴含的
   `(provider_team_id, canonical_team_id)` pairs 内部自洽(同一 provider ID 不指向多个
   canonical、主客 canonical 不被折叠成同一支);
6. provider ID 对应的既有 team xref:**不存在**时允许在本次严格通过后创建、**已存在**时
   必须与预期 canonical 完全一致;
7. 比赛 xref 一对一约束与 team xref 均无冲突。

任一侧缺精确 kickoff、时间无法解析或差值无法计算 → `kickoff_diff_seconds` 记 **NULL** 且强制
`needs_review`(**不再因 `kickoff_diff is None` 放行**)。

**provider 球队身份完整性**:主队或客队 provider ID 缺失、相同、或 pairs 内部矛盾时,整场
`needs_review`、**不写任何 team xref**,返回明确 `validation_errors`
(`provider_team_id_missing` / `provider_team_ids_not_distinct` /
`provider_team_internal_conflict`)——**绝不依赖 `INSERT OR IGNORE` 把"重复 provider ID 只写一行"
的半映射吞成看似成功**。

**team xref 冲突显式化 + 事务一致**:auto_ok 写库前先检查 `(provider, provider_team_id)` 是否已
映射到**不同** canonical。若冲突 → 整场降级 `needs_review` 且**不写任何 team xref**(不产生
"比赛 auto_ok 但 team xref 未写"或反之的半成功);相同 canonical → 幂等复用,不改写既有
`verified`/`manual` 行。pair 内部校验、DB team xref 冲突检查、match xref 占用检查与写入都在
同一个 `BEGIN IMMEDIATE` 事务内完成。返回值带 `team_conflicts` 明细供审计。

**既有 auto_ok 每次重新遇到都重新验证**(`_revalidate_existing_auto_ok`,不盲信历史结论):
除 kickoff provenance 与差值外,还用**本次 schedule_row 的队名/别名**独立验证主客方向——按
`home_away_inverted` 计算 NowGoal 主客队各自预期 canonical,要求两侧队名经 `dim_team_alias`
明确、唯一解析到预期 canonical(不匹配对手、无多 canonical 歧义);并要求 provider 球队 ID
存在、互异,且**两个 provider ID 对应的 dim_team_xref 行真实存在**(既有行被删、或本轮换成
从未映射的新 provider ID → `provider_team_xref_missing`)**且都指向预期 canonical**(存在但
canonical 不符 → `team_conflicts`,与 missing 用不同信号区分)。任一项不满足(纯换名、主客名
互换、provider ID 缺失/重复、team xref 缺失或冲突、kickoff 漂移)→ **原子降级 `needs_review`**,
该轮不进入赔率采集;重验证**只允许保留 auto_ok 或降级**,绝不静默创建、补写或覆盖任何 team
xref。

**事务边界**:existing auto_ok 重验证把 match xref 当前权威状态、alias、dim_team_xref 的读取
与"保留 auto_ok 还是降级"的最终判定放在**同一个 `BEGIN IMMEDIATE` 事务**内——**保持 auto_ok
的成功路径也在事务内做最终复核,不在事务开始前提前 return**;core 库全程只读(在持有 odds
事务时读取)。先在事务内重查确认仍是 `auto_ok`+`verified=0` 才更新,避免与并发人工操作竞态。

**既有映射保护**:`confirmed`/`verified`/`manual` 的既有 match/team xref 不被自动流程或上述
重验证覆盖;`rejected` 映射不被自动复活(同 provider_match_id 早返回原状态)。

**边界**:needs_review / rejected 映射不进入 Silver(变化点/共现构建只取 auto_ok/confirmed)。
真实 NowGoal 端点连续采集 **UNVERIFIED**,以上规则由离线 fixture + 单元测试验证同一条代码链路
(`tests/backend/test_odds_pipeline.py` 的 `TestEntityResolutionSafety`、
`TestProviderIdentityAndRevalidation`、`TestTeamXrefExistenceAndAliasGate`)。

## 3. 轮询策略(窗口到期调度,CLAUDE.md §6.3)

- **触发**:`allwin-poll.timer` 每 5 分钟触发一次"到期判断"(`worker --job
  nowgoal_snapshot` + `--job fotmob_snapshot`);这不代表每 5 分钟都请求数据源。
- **窗口与频率**(`backend/ingest/poll_windows.py`,离线 fixture 已验证):
  - 只有 `kickoff_at_utc` 精确、状态 NotStarted、开球落在 [now, now+72h] 的比赛
    进入采集窗口(缺精确开球时间的比赛不进入,不按当天 00:00 伪装);
  - 距开球 2–72h:同一 (source, 比赛) 最小间隔 15 分钟;0–2h:5 分钟;
  - 已开球即退出赛前窗口(in-play 采集是显式的另一件事,当前未实现);
  - 到期状态持久化于 odds.db `poll_state`,进程重启不重复采集。
- **日程发现**:窗口内未映射比赛按其北京日期(NowGoal timezone=8)抓日程,
  同一日期最小间隔 15 分钟;不再只抓"当天"而漏掉未来 72h。
- **单轮流程**(`poll_nowgoal.run_due_poll` / 单日模式 `run_poll`):日程失败记
  source_health 并跳过该日期;单场赔率失败继续其余场次。只有
  `review_status ∈ (auto_ok, confirmed)` 的映射才抓赔率;needs_review 不抓
  (等人工审核,见 `/api/v1/admin/xref`)。
- **FotMob 快照**(`poll_fotmob_snapshots.run_snapshot_poll`):同窗口同频率;
  同一比赛同一轮只抓一次 match payload,阵容 + 两队伤停三条快照共用
  `observed_at` 与 `poll_run_id`。

## 4. 落库规则(hash-diff)

`ingest/odds_snapshots.py`,目标表 `bronze_ng_odds_snap`(每 (provider_match_id,
market, company_id) 一条序列)、`bronze_fm_lineup_snap`、`bronze_fm_sideline_snap`:

1. record → canonical JSON(`canonical_payload_json`:排序键、紧凑分隔符)→ SHA-256;
2. 与同序列最近一条 `payload_hash` 比较:**不变则跳过,变了才 INSERT**(append-only,
   从不 UPDATE 旧快照);
3. `market_phase` 精确判定(不按自然日、不按字符串是否含 'T' 粗判):来源状态 + **完整
   kickoff provenance**(唯一真源 `normalize_exact_kickoff`:exact ∧ 非空来源 ∧ 显式时区
   可解析)+ 观察时间共同决定——core status='NotStarted' 且 now < kickoff(datetime 比较,
   非裸字符串)→ `pre_match`;core status='InPlay' → `in_play`;信息不足(含无精确 kickoff、
   缺来源、naive/非法时间)→ `unknown`。
4. `FINAL`(`silver/odds_moves.final_pre_match_snapshot`):精确 kickoff 前最后一条
   `market_phase='pre_match'` 快照,同样经 `normalize_exact_kickoff` 判定,明确排除
   unknown/in_play;kickoff 缺失、precision 非 exact、缺来源或不可解析时返回 None
   ——**不声称任何快照是收盘快照**。

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
| T-72h/15min + T-2h/5min 分级轮询 | **已实现**(`poll_windows.required_interval_seconds`:2–72h→900s,0–2h→300s;`poll_state` 持久化节流);离线 fixture 验证,真实端点连续采集仍 UNVERIFIED |
| `market_phase` / `FINAL` 精确判定 | **已实现**(唯一真源 `normalize_exact_kickoff`,按完整 provenance 判 pre_match/in_play/unknown;不精确时 FINAL 返回 None);对缺精确开球的比赛如实标 `unknown`,不伪装收盘 |
| 采集窗口混合时区筛选 | **已修复**(`upcoming_precise_matches` 用 `julianday()` 比较窗口边界,带 `-05:00`/`+08:00` 偏移的合法 kickoff 不被裸文本范围误排除) |
