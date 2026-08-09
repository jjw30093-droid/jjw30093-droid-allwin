# 球队全赛事赛程采集 pilot(可行性验证,非生产迁移)

> 范围:严格受限的技术 pilot——验证能否通过现有 FotMob 链路取得一支球队一个
> 历史赛季的完整跨赛事赛程,并证明"联赛间插入的杯赛/欧战会改变真实休息时间"。
> **不是正式批量采集,不是生产迁移,不构建完整 Gold 数据集**。真实网络请求
> 全程顺序执行、单代理、无 fan-out,总计 **7/10** 次(其中 `match_details`
> **2/3** 次),均在预算内。

## 0. 结论摘要(先说人话)

`https://www.fotmob.com/api/data/teams?id=<team_id>&season=<season>` 端点
**真实可达、返回结构清晰、能解析出跨赛事(联赛+国内杯赛+友谊赛+社区盾)的
比赛**,但它**不是一个"按赛季查询历史赛程"的接口**——`season` 查询参数对
返回内容**没有任何可观测影响**,响应恒为"以当前时刻为中心的最近+未来滚动
窗口"(实测 50 场)。这意味着:

- **无法**用它一次性取得 Manchester City 2024/2025(或任何指定历史赛季)的
  完整赛程;
- 真实窗口(2026-04-22 ~ 2027-05-30)与 2024/2025 赛季**零重叠**,与
  2025/2026 赛季也只有**尾部 6/38** 场重叠;
- 但窗口内 8 场已完赛比赛(6 英超 + 2 足总杯)真实、完整地复现了任务要求
  验证的核心现象:**足总杯插入英超赛程之间,会让"任意赛事口径"的休息时间
  短于"纯英超口径"**(两个真实样例,见 §8)。

因此:**核心技术假设(能否用一次请求拿到某队完整历史赛季跨赛事赛程)不成立**,
但**解析器 + 休息时间计算 + 跨赛事样例验证**这条能力链路本身是真实、可行、
已用真实数据验证的。详见 §15 最终判定。

## 1. 请求范围与真实请求次数

- 测试球队:Manchester City,FotMob Team ID = **8456**
- 主测试赛季:**2024/2025**;因该赛季历史数据不可获取,按任务允许的"最多
  再尝试一次",追加验证 **2025/2026**(结果:两次请求返回**完全相同**的
  50 场滚动窗口,证明 season 参数确实无效,而不是偶然)
- **真实网络请求总数:7 / 10**(预算上限 10)
  1. `GET /api/data/teams?id=8456`(无 season)
  2. `GET /api/data/teams?id=8456&season=2024%2F2025`(保存原始响应)
  3. `GET /api/data/teams?id=8456&season=2025%2F2026`(确认 season 参数无效)
  4. `GET https://pub.fotmob.com/prod/db/api/team/8456/fixture-by-date?beforeTimestamp=...`
     (探测历史分页机制,**HTTP 400,失败**——见 §7)
  5-6. `match_details(4813708)`(英超)、`match_details(5293793)`(足总杯)
     ——交叉校验赛程 feed 与既有已验证 `match_details()` 端点字段一致性
  7. `--live` 真实 pilot CLI 正式运行一次(`team_id=8456, season=2024/2025`)
- **`match_details` 请求数:2 / 3**(预算上限 3)
- 全程未批量抓五大联赛,未逐日请求 365 天,未转为大量逐赛事采集,发现
  season 参数无效、分页端点不可用后按任务要求**到此停止扩大网络范围**。

## 2. 现状审计(先读代码,再动手)

阅读 `CLAUDE.md`、`README.md`、`backend/fotmob_client.py`、
`backend/ingest/ingest_league.py`、`backend/ingest/ingest_future_fixtures.py`、
`backend/db/util.py`、`docs/data-sources.md`、`docs/architecture.md` 后确认:

| 项 | 现状 |
|---|---|
| 已有能力 | `FotMobClient.league_matches(league_id, season)` 只能**按联赛**取赛程(`enumerate_fixtures`/`fetch_fixture_rows` 均消费该端点);`match_details(match_id)` 能取单场详情(比分/阵容/事件/射门图) |
| 本项目是否已有"按球队取跨赛事历史赛程"的正式能力 | **没有**——`docs/data-sources.md`/`docs/architecture.md` 均未提及任何 team-schedule 端点或能力;`grep` 全仓库确认无此前实现 |
| `normalize_utc_iso`(`backend/db/util.py`) | 已存在且语义与 CLAUDE.md §6.2.1 一致(只接受带时间部分的值,纯日期/不可解析→None),pilot 直接复用,未重新发明 |
| 端点/JSON 结构 | 未经真实探测前不得假定有效——本轮先做了 §3 的有限探测,再写解析器 |

## 3. 受限端点发现(真实探测结果)

**端点**:`GET https://www.fotmob.com/api/data/teams?id=<team_id>&season=<season>`
(与既有 `league_matches()` 的 `api/data/leagues?id=` 同构;非 SSR 页面,直接
返回 JSON)。

| 检查项 | 结果 |
|---|---|
| HTTP 状态 | 200(3 次真实请求全部 200) |
| 响应顶层 key | `tabs, allAvailableSeasons, details, seostr, QAData, table, transfers, overview, stats, fixtures, squad, history` |
| 赛程所在 JSON 路径 | `fixtures.allFixtures.fixtures[]`(另有 `overview.overviewFixtures`,内容与前者相同,50 场) |
| 是否支持 season 参数 | **否**——`season=2024/2025` 与 `season=2025/2026` 两次真实请求返回**逐字节相同**的 50 场窗口(同一 min/max kickoff:2026-04-22T19:00Z ~ 2027-05-30T15:00Z);`details.latestSeason`/`overview.season` 恒为来源当前赛季 `2026/2027`,`overview.selectedSeason` 恒为空字符串 |
| 是否包含历史完整赛程 | **否**——是以"当前时刻"为中心的最近+未来滚动窗口(50 场),不是按赛季组织 |
| 是否包含赛事 ID/名称 | 是:`tournament.{name, leagueId, stage}`(无独立的"类型"字段,见 §4 分类方法论) |
| 是否包含精确 kickoff UTC | 部分——`status.utcTime` 存在,但真实数据里 **33/50 场**未开赛比赛带 `matchDateTbd=true`(日期本身来源声明未定,即便字符串是完整 ISO 格式,也不能算精确,见 §6) |
| 是否包含 home/away | 是:`home.{id,name,score}` / `away.{id,name,score}` |
| 是否包含 finished/cancelled/started | 是:`status.{finished, started, cancelled}` |
| 历史分页机制 | 响应含 `fixtures.previousFixturesUrl`(形如 `pub.fotmob.com/prod/db/api/team/8456/fixture-by-date?beforeTimestamp=...`);**真实请求返回 HTTP 400**(现有 `FotMobClient` 重试 3 次均失败,可能需要额外签名/认证头),本轮**未验证**该路径是否可行,也未继续尝试更多参数组合(按任务要求到此停止扩大网络范围) |

**结论**:`HISTORICAL_SEASON_PARAMETER_VERIFIED = FAILED`——不是"某个参数格式
不对",而是这个端点在结构上就不是按赛季查询的接口。

## 4. 赛事分类方法论(heuristic_name,如实标注)

来源每场比赛只提供 `{name, stage, leagueId}`,**没有独立的赛事类型枚举**。
因此 pilot 的 `competition_class`(league/domestic_cup/continental/
super_cup/friendly/other/unknown)**全部**由人工整理的名称表(`_LEAGUE_NAMES`/
`_DOMESTIC_CUP_NAMES`/`_CONTINENTAL_NAMES`/`_SUPER_CUP_NAMES`/`_FRIENDLY_NAMES`,
定义于 `analysis/team_schedule_pilot/fotmob_team_schedule_pilot.py`)推断得到,
`classification_method` 字段**恒为 `heuristic_name`**,不包装成来源原生分类。

真实抓取窗口(50 场)按此方法分类:

| competition_class | 数量 | 真实赛事名 |
|---|---:|---|
| league | 44 | Premier League |
| domestic_cup | 2 | FA Cup |
| friendly | 3 | Club Friendlies |
| super_cup | 1 | Community Shield |
| continental | **0** | (真实窗口内没有出现任何欧战比赛——见下方说明) |

**关于欧战样例缺失(如实披露,不编造)**:真实抓取窗口横跨 2026-04-22 ~
2027-05-30,恰好落在"上赛季末段 + 夏窗友谊赛/社区盾 + 新赛季初段"这段
欧战淡季,窗口内确实没有任何 UEFA 赛事。为了不为凑齐"至少一场欧战"而
虚构一条伪装成真实观测的数据,`continental` 分类逻辑改用测试文件
(`test_classify_competition_continental_synthetic_case`)里**显式标注为
synthetic**(如 `"UEFA Champions League"`)的最小 dict 单独覆盖,离线
fixture(`tests/fixtures/fotmob/team_schedule_pilot_minimal.json`)本身
**不含**任何伪造的欧战记录。

## 5. kickoff 精度判定(真实数据发现的重要细节)

`derive_kickoff()` 依据显式 `matchDateTbd`/`matchTimeTbd` 标记判定精度,
不凭字符串形状推断(同 CLAUDE.md §6.2.1 `normalize_exact_kickoff` 的纪律):

- `matchDateTbd`/`matchTimeTbd` 均为假 且 能严格解析 → `exact`
- 日期本身可信(`matchDateTbd` 为假)但时间不满足严格解析 → `date_only`
- 日期本身来源声明未定(`matchDateTbd=true`) → `unknown`(**不**当作
  "日期可信只是时间未知"处理,即便 `utcTime` 是一个语法完整的 ISO 字符串)

真实窗口精度分布:**exact = 17**(8 场已完赛 + 9 场近期未开赛)、
**unknown = 33**(全部为 `matchDateTbd=true` 的远期未开赛比赛,**33/33**
同时 `matchTimeTbd=false`——即便时间字段本身不算 TBD,日期 TBD 已经足以拒绝
exact)、**date_only = 0**(真实数据未观测到,解析器仍保留该分支以应对
"仅日期无时间"来源,只是本次真实响应没有触发)。

## 6. 状态分布

真实窗口(50 场):**finished = 8**、**cancelled = 0**、**upcoming = 42**。
真实响应中没有出现 cancelled 样例(任务允许的"如果真实响应存在"条款——
这里确实不存在,fixture 因此不含 cancelled 样例,只含大量真实的
`notStarted=true` 未完赛样例)。

## 7. 分页/截断风险(重要,直接影响完整性判定)

- 端点**没有**"只返回最近 N 场"的隐藏文档,但实测行为**就是**如此:固定
  50 场、以当前时刻为中心、season 参数无效。
- `previousFixturesUrl` 理论上提供了向历史翻页的机制,但真实请求返回
  **HTTP 400**(3 次重试均失败),现有 `FotMobClient` 不具备使其工作所需
  的额外认证/签名能力,本轮**未验证**、也未继续排查(避免转为大规模摸索
  性请求)。
- **结论**:即使把这个翻页端点跑通,要覆盖 Manchester City 一个完整赛季
  (2024/2025,38 场英超 + 杯赛 + 欧战,时间跨度约 10 个月)大概率需要**远
  超预算**的翻页请求数——这正是任务要求"到此停止扩大网络范围"的场景。

## 8. 休息时间计算(真实样本,核心验证目标)

规则:只对 `requested_team_id=8456`、已完赛、非取消、`kickoff_precision=exact`、
正式比赛(排除 friendly)的 8 场比赛按 `kickoff_utc` 升序计算。`rest_hours`
为权威值(`calendar_gap_days = rest_hours/24`,不単独声称"天数"是唯一正确
口径)。

### 真实跨赛事样例 1(主样例)

| | Match ID | 赛事 | kickoff(UTC) |
|---|---|---|---|
| 当前英超比赛 | 4813720 | Premier League | 2026-05-04T19:00:00Z |
| league-only 上一场 | 4813708 | Premier League | 2026-04-22T19:00:00Z |
| all-competition 上一场 | 5293793 | **FA Cup** | 2026-04-25T16:15:00Z |

- `league_only_rest_hours` = 288.0h(12 天)
- `all_comp_rest_hours` = **218.75h**(约 9.11 天)
- 相差 **69.25 小时**——中间插入的足总杯比赛把"任意赛事口径"的休息时间
  拉短了近 3 天。

### 真实跨赛事样例 2(第二次独立复现,同一批真实数据)

| | Match ID | 赛事 | kickoff(UTC) |
|---|---|---|---|
| 当前英超比赛 | 4813735 | Premier League | 2026-05-19T18:30:00Z |
| league-only 上一场 | 4813681 | Premier League | 2026-05-13T19:00:00Z |
| all-competition 上一场 | 5315746 | **FA Cup** | 2026-05-16T14:00:00Z |

- `league_only_rest_hours` = 143.5h;`all_comp_rest_hours` = **76.5h**;
  相差 **67.0 小时**。

`CROSS_COMP_REST_VERIFIED = VERIFIED`(2 个独立真实样本,同一次真实抓取,
`--live` CLI 运行输出与手工复算完全一致)。

**局限**:窗口左边界(2026-04-22 的那场英超)没有更早数据可比较,
`rest_hours=None`——这是窗口截断本身造成的边界效应,如实记为 `None`,不臆造。
另外来源没有"是否踢满 90 分钟/是否加时"的信息,`went_to_extra_time` 恒为
`NULL`(见 §10)。

## 9. 与 allwin.db 交叉核对(只读,`mode=ro&immutable=1`)

| | allwin 英超比赛数 | 团队赛程窗口中重叠的 Match ID 数 | 主客方向冲突 |
|---|---:|---:|---:|
| 2024/2025 | 38 | **0** | 0(无重叠可比较) |
| 2025/2026 | 38 | **6**(窗口只覆盖该赛季末 6 轮) | 0 |

- `only_in_allwin`(2024/2025):全部 38 场(窗口完全不覆盖该赛季)
- `only_in_team_schedule`:44 场英超里,6 场与 allwin 2025/2026 重叠,
  其余 38 场是 2026/27 赛季(allwin.db 里 2026/2027 也已有 38 场历史 core
  数据,但本次交叉核对聚焦请求的赛季,未展开逐场比对 2026/27)
- 完整性通过的最低要求(38/38 对齐、0 冲突)**未达成**,原因是根本没有
  拿到 2024/2025 赛季数据,不是对齐逻辑本身有问题。
- **未硬编码 38**——两个赛季的英超场数均是从 `data/allwin.db` 用
  `SELECT COUNT(*) ... WHERE League_ID=47 AND Season=? AND (Home_Team_ID=8456
  OR Away_Team_ID=8456)` 实时查出。
- allwin.db 本身这批历史数据 `kickoff_at_utc` 全部为 NULL(`docs/data-sources.md`
  已如实记录:历史五大联赛旧数据在补列前落库,仍为 `date_only`),因此
  "开球时间冲突数"这一项在 2024/2025、2025/2026 两个赛季均**不可比较**
  (allwin 侧没有可比较的精确时间),如实记为不可比较,不伪造对比结果。

## 10. 不能计算/不能声称的内容

- **真实出场分钟、加时负荷**:来源赛程 feed 不提供逐球员出场时间或
  是否加时,`went_to_extra_time` 在本 pilot 中**恒为 NULL**,未编造 `False`。
- **完整赛季语义**:任何一次真实响应都不能被当作"完整赛季",无论传什么
  `season` 参数。
- **公司/市场覆盖**(不适用于本 pilot,仅确认本 pilot 未涉及 NowGoal)。
- **窗口左边界之前的比赛**:该队在窗口开始之前的最后一场比赛是什么、
  何时踢的,本次真实数据完全不可见(`rest_hours=None` 的直接原因)。

## 11. 数据库完整性(核心安全边界)

### 11.1 pilot 自己的临时数据库(`/tmp/allwin-team-schedule-pilot-20260723/`)

- 幂等写入验证(自动化测试 + 手工双跑):`pilot_match_calendar`/
  `pilot_team_match` 连续写入两次,行数**均不增加**(12 → 12,两次输出
  除 `inserted`/`skipped` 计数互换外完全一致)。
- 真实 `--live` 运行一次:写入 50 行,与手工分析数字完全吻合。

### 11.2 四个生产数据库(开始 / 结束对照)

| 数据库 | SHA-256 | size | mtime | WAL/SHM |
|---|---|---:|---|---|
| `data/allwin.db`(前) | `92a6a39c...ab364e` | 406073344 | 2026-07-19 23:21:06 | 无 |
| `data/allwin.db`(后) | `92a6a39c...ab364e`(**相同**) | 406073344(**相同**) | 2026-07-19 23:21:06(**相同**) | **新出现 `-wal`(0 字节)+ `-shm`(32768 字节)** |
| `data/verify_leagues.db`(前后) | `603163b5...3940a19c0`(相同) | 305598464(相同) | 2026-07-11 16:46:03(相同) | 无 |
| `data/platform.db`(前后) | `c21e7008...9e8ea2d`(相同) | 716800(相同) | 2026-07-20 05:14:07(相同) | 无 |
| `data/odds.db`(前后) | `cdc5fd54...f315e093`(相同) | 147456(相同) | 2026-07-19 23:37:07(相同) | 有(**任务开始前已存在**,与本轮无关) |

**必须如实披露的发现**:`data/allwin.db` 的主文件内容(哈希/大小/mtime)
**完全未变**,但出现了此前不存在的 `-wal`(空,0 字节)/`-shm` 旁路文件。
根因排查(只读排查,未进一步写库):

1. 本轮遗留的 `analysis/`、`backend/fotmob_client.py` 相关代码**从未**以
   非 `mode=ro&immutable=1` 方式打开 `data/allwin.db`——pilot 模块自身
   只写自己的临时 SQLite,从不触碰生产库。
2. 真正的触发点是本轮按任务要求"修改了 `backend/fotmob_client.py` 后,
   运行所有与 FotMob 相关的现有测试"这一步——其中
   `tests/backend/test_e2e_seed.py` 会以子进程方式运行**既有**(非本轮
   编写)的 `tests/e2e/seed_e2e.py`,该脚本通过符号链接以
   `connect_ro("core")`(`backend/db/connections.py:32`,只用 `mode=ro`,
   **不带** `immutable=1`)读取真实 `data/allwin.db`。
3. `data/allwin.db` 自身的持久化 `journal_mode` 早已是 WAL(项目历史上的
   正常写连接设置,与本轮无关)。SQLite 的标准行为是:只要不显式声明
   `immutable=1`,读连接在打开一个 journal_mode=WAL 的数据库时,仍需要
   实例化 `-shm`/`-wal` 以正确判断"WAL 里是否有比主文件更新的页"——即便
   全程只读、从未提交任何写入,也会创建这两个旁路文件。这是 SQLite 的
   标准记录行为,不是数据被修改。
4. **这是既有测试基础设施本身的、与本轮 pilot 代码无关的、预先存在的
   行为**——任何人在任何时候运行 `test_e2e_seed.py` 都会复现同样的旁路
   文件创建,只是此前的会话可能没有专门对比检查过。

**本轮明确决定**:不主动删除/checkpoint 这两个旁路文件——即使已确认它们
是空的、无害的(`-wal` 为 0 字节,代表没有任何未提交的写入),主动对生产
数据库路径执行任何操作都超出本任务授权范围,交由用户自行决定是否要用
`sqlite3 data/allwin.db "PRAGMA wal_checkpoint;"` 这类只读安全操作清理。

**判定**:四个数据库的**内容**(SHA-256/size/mtime,任务给定的判定标准)
**全部未变**;`data/allwin.db` 因运行既有测试而产生了空的 WAL/SHM 旁路
文件,已如实披露、已定位根因、确认零数据影响,不属于本 pilot 代码造成的
数据修改。

## 12. Parser/幂等/CLI 测试

- 2026-07-25 第四轮最终实跑 `--collect-only`:team pilot **68 collected**;
  competition pilot **186 collected**;合计 **254 collected**。
- 同一轮离线联合执行:**254 passed,0 failed,0 skipped,0 xfailed,
  0 warnings**;
  其中 team pilot **68/68 passed**。命令均使用
  `PYTHONDONTWRITEBYTECODE=1` 与 `-p no:cacheprovider`,无网络访问。
- 覆盖任务要求的全部 16 项规则(主客场解析、非本队拒绝、去重、冲突拒绝、
  exact/date_only/unknown 精度、cancelled 排除、unfinished 不作为上一场、
  跨赛事缩短 league rest、7/14 天边界、SQLite 幂等写入+冲突报错、凭证
  防护、乱序输入不产生负 rest、缺失 competition_id 不伪造、
  went_to_extra_time 恒 NULL)+ 分类方法论 + 新增 `team_data()` 方法(无
  网络,只测 URL 构造)+ 端到端解析真实裁剪 fixture + CLI 全流程 + A2 收口
  新增的严格 ID 解析反例(fixtures 非 dict 元素抛 `ScheduleSchemaError`、
  float/bool/负数/小数点字符串 Match ID 拒绝、非法 competition_id 不截断)。

## 13. 因修改 `backend/fotmob_client.py` 而运行的历史测试

历史 pilot 只新增一个公开只读方法 `FotMobClient.team_data(team_id, season="")`。
本轮凭证隔离施工又移除 import-time `load_dotenv()`、`THORDATA_PROXY` 读取和
默认 proxy 常量：显式空/显式 proxy 不读环境或 dotenv；默认 client 先使用
已有环境，仅在缺失时加载 dotenv。无凭证 import、URL 构造、默认/显式路径和
清理错误均有永久离线测试。第三轮又把 `_get()` 的日志和最终异常改为安全
项目异常：只保留底层异常 class name、HTTP status 和 attempt，不记录完整 URL、
response body、`str/repr(exc)`，并断开原始异常链。相关 snapshot provider
不再重复预读 dotenv。

历史验收按任务要求运行了 10 个提及 "fotmob" 的既有测试文件:

```
tests/backend/test_cache_policy.py tests/backend/test_studio.py
tests/backend/test_predictions.py tests/backend/test_pipeline_e2e.py
tests/backend/test_e2e_seed.py tests/backend/test_api_v1.py
tests/backend/test_odds_pipeline.py tests/backend/test_kickoff_provenance.py
tests/backend/test_poll_scheduling.py tests/backend/test_ops_check.py
```

该清单属于原 team pilot 的历史验收范围；以下数字是当时验收快照，不是
最终 P2 当前 warning 结果（当前结果见 §17）。该历史轮 collect-only 为
**346 collected**，不能写成 passed。当前广泛回归排除会写仓库 `data/e2e`
的 `test_e2e_seed.py`；该文件的四项按明确禁令
**NOT RUN / UNVERIFIED**。其余九文件
该历史轮 **342 collected / 342 passed**，API/cache/studio 三个全文件
**78 passed**，当时记录为 0 failed/skip/xfail、0 warnings；最终 P2
`-W default` 重跑已在 §17 明确更正 warning。app fixture 修复 legacy
DB 隔离后执行的广泛回归：其余 `tests/backend` 为
**531 collected / 531 passed**、当时记录 0 failed/skip/xfail/warnings；
最终 P2 当前值同样以 §17 为准。四库和全部
既有 WAL/SHM 前后元数据逐项一致。

## 14. 其余验收

- `PYTHONPYCACHEPREFIX=$(mktemp -d /tmp/allwin-page-dialect-compile.XXXXXX)
  .venv/bin/python -m compileall -q analysis backend tests/backend` → exit 0;
  不在仓库内生成新 `__pycache__`
- pilot 离线模式连续运行两次(`--offline-fixture` 同一 fixture、同一
  `--output-dir`):除 `observed_at` 外完全一致;`calendar_write`/
  `team_match_write` 从 `{inserted:12,skipped:0}` 变为
  `{inserted:0,skipped:12}`(符合幂等预期,不是异常);DB 行数
  12 → 12(**未增加**)
- 真实网络 pilot(`--live`)全程只运行一次受限流程(§1 请求 #7)
- `git status`/`git diff --stat`:保留用户既有 dirty worktree；本轮相关 backend
  变更包含 `fotmob_client.py` 的 lazy proxy 解析和 snapshot provider 的配套
  docstring/重复预读移除；未 commit/push/tag/deploy;未清理 dirty worktree
- stdout/stderr 均经 `_redact_check` 防御性检查 + 人工复核,未出现
  `THORDATA_PROXY` 赋值或 `user:pass@host` 形状的凭证

## 15. 最终判定

| 维度 | 判定 |
|---|---|
| Endpoint | **VERIFIED**(可达,HTTP 200,JSON 结构清晰、可复现) |
| Parser | **VALIDATED**(当前 team 93/93 离线测试通过,覆盖规则 + 分类 + CLI + FotMob transport/status/HTTP/JSON/text/SSR/downstream-warning redaction) |
| Historical season support | **FAILED**(season 参数对返回内容零可观测影响,2024/2025 与 2025/2026 两次真实请求验证一致) |
| League completeness | 2024/2025 = **FAILED**(0/38);2025/2026 = **PARTIAL**(6/38,窗口只覆盖尾部) |
| Full all-competition completeness | **FAILED**(单次调用连一个完整赛季的英超都覆盖不到,更谈不上"全部赛事完整") |
| Cross-competition rest calculation | **VERIFIED**(2 个独立真实样本,`--live` CLI 输出与手工复算一致) |
| Safety/integrity | **PASS**(四库主文件及全部既有 WAL/SHM 的 SHA-256/size/mtime 前后未变;未泄露凭证;未 commit/push;未清理 dirty worktree) |
| **Scale recommendation** | **NO_GO** |

**NO_GO 理由**:任务给出的 GO 条件要求"2024/2025 或明确替代赛季的历史
完整赛程可获取"且"英超 Match ID 全量对齐"且"没有分页截断"——这三项
均不成立,且不是参数调整就能解决的问题(是端点本身的行为边界,`season`
参数被证实完全无效)。翻页机制(`previousFixturesUrl`)存在但本轮受限
预算内验证为不可用(HTTP 400)。在没有先解决"如何拿到历史赛季数据"这个
根本问题之前,**不建议基于这个端点扩大采集**。

**CODE IMPLEMENTED**:解析器、分类器、休息时间计算、SQLite 幂等写入、CLI、
`FotMobClient.team_data()` ——全部已实现且离线测试通过。
**OFFLINE FIXTURE VERIFIED**:`tests/fixtures/fotmob/team_schedule_pilot_minimal.json`
(12 场真实裁剪比赛)+ 当前 68 项 team pilot 离线测试全部通过。
**LIVE ENDPOINT VERIFIED**:端点可达、真实解析出 50 场比赛、2 个真实
跨赛事休息时间样例、与 allwin.db 真实交叉核对(6/38、0/38)。
**HISTORICAL COMPLETENESS UNVERIFIED → 结论其实是 FAILED**(不是没测,是
测了两次、结果一致地证明这条路走不通)——不用一个笼统的 DONE 掩盖这一点。

## 16. 如果要继续,下一步该做什么(仅建议,本轮不执行)

1. 排查 `previousFixturesUrl`(`pub.fotmob.com`)返回 400 的真实原因
   (可能需要额外请求头/签名),单独作一次更小范围的验证,而不是直接
   扩大到批量翻页采集。
2. 若翻页机制被验证可行,需重新评估"覆盖一个完整历史赛季大约需要多少次
   翻页请求",据此判断是否在预算/频率纪律下可行,而不是假设可行就扩大。
3. 在没有解决历史赛季检索问题之前,不建议把 `team_data()` 接入任何生产
   Worker 任务链。

## 17. Competition fail-closed 收口同步(2026-07-25)

本报告的 team endpoint `NO_GO` 结论不变。后续 competition endpoint pilot
已关闭空 fixtures、returned season、direct-fixtures pagination、多赛季
registry、旧 schema 和低层 parser 的 fail-open 路径；pagination 不递归扫描
业务对象，detected/unresolved 双证据完整保留。已知 marker 的大小写折叠
碰撞现在一律 `UNRESOLVED`，每个原始路径均保留，不采用 last-write-wins；
page metadata 支持 `currentPage/totalPages`、`page/pageCount`、
`page/totalPages` 三种方言；不完整 family 精确记录 present/missing
companion。完整方言不会因为可选别名缺失而误报，但也不能掩盖额外、未被
任一完整方言消费的孤立已知 marker；孤立 marker 无论值的类型或范围都
`UNRESOLVED`，并与完整 pair 的 detected evidence 分别保留。多完整方言
冲突保留 detected 与 conflict evidence 并 fail closed；
第五轮独立复核在修复前给出 **FIX_REQUIRED**：3 个 P1 为 collision
全局屏蔽独立 orphan、HTTP-200 JSON/text/SSR decode 泄漏及 `status_code`
accessor 越出 worker 安全边界；2 个 P2 为错误的“collision only”永久断言
和不准确的文档状态/计数。新测试落盘后旧实现真实 RED：pagination
collision+orphan **8 failed**，FotMob status/decode/downstream-warning
redaction **25 failed**。

当前实现逐 key 处理 page-family：collision 不屏蔽其他非碰撞 orphan；已存在
但 collided 的 companion 不伪报 missing。FotMob transport/HTTP 原始文本和
异常链已清除，`status_code` 在 worker `try/except` 内读取；五个公开 JSON
入口与 SSR text/`__NEXT_DATA__` 统一使用安全 decode helper，失败只暴露固定
operation/exception class 并抛 `FotMobDecodeError from None`。
`parse_season_player_stats()` warning 不再记录 stat name、URL 或异常文本；
Allsvenskan API fixture 改用
UTC 当前时间 +3 天；cache-policy 与 studio 的 publish fixtures 也改为运行时
未来时间。

最终独立 P2 复核在上述生产安全边界全部通过后记录
**P0=0 / P1=0 / P2=2 / `FIX_REQUIRED`**：真实
`curl_cffi.Response` 对非法 UTF-8 安全地产生
`FotMobDecodeError ... UnicodeDecodeError`，测试却不可移植地写死
`JSONDecodeError`；四份文档也错误声称 warnings 为 0。本轮只修改该永久测试
和文档，生产代码、pagination 与动态时间 fixture 均未变化。该测试修正前
**1 failed**，修正后 **1 passed**；现在锁定固定 `team_data` operation、
安全 identifier、全 secret surface 和无异常链，不绑定底层 decoder 类型，
FakeResponse 四异常类型矩阵保持不变。

第五轮 collision+orphan **8/8**、完整
pagination/orphan/collision **61/61**、FotMob status/decode/warning
redaction **25/25**；当前 competition **193/193**、team **93/93**、联合
**286 collected / 286 passed**、contract **30/30**、
API/cache/studio **78/78**、安全九文件 **342/342**
（十文件仅 **346 collected**；`test_e2e_seed.py` 四项
NOT RUN / UNVERIFIED）、隔离后后端广泛回归
**531/531** 均通过，0 failed/skip/xfail。FotMob/team/competition/combined
均 0 warnings；contract、API/cache/studio、Allsvenskan、安全九文件及十文件
collection 各出现一次同源 `StarletteDeprecationWarning`，这是同一个
Starlette/httpx 第三方弃用来源在独立进程重复报告，不是多个不同缺陷。backend
全套为 3 warnings：同一个 Starlette warning，加两个既有测试资源清理
`ResourceWarning`——auth uvicorn smoke 未关闭 `Popen(stdout=PIPE)` reader，
以及 release rollback 的 `HTTPServer` 未 `server_close()`。它们不属于本轮
生产代码冻结的 P2 范围，未隐藏也未顺手修改。四个数据库主文件和全部
既有 WAL/SHM 的 SHA-256/size/mtime 与本轮新基线逐项相同，严格 sidecar
integrity = **PASS**。

上一轮只读复核误刷新三个既有 `.pyc` 是保留在历史中的 review-integrity
FAIL；本轮没有删除、重建、touch 或伪造恢复。本轮缓存完整性仅比较本轮
开始/结束快照。2026-07-25 两端均为 107 个 `__pycache__` 目录、
808 个 `.pyc`，内容摘要
`9cd6b06d389483315befa6cc07c8936fb8f363a5810210245674e2771789cda7`、
元数据摘要
`9db30ce3fda74a4fa7844a93439698cd7f26ac39300d8dde51fb5a035d7692f3`
均不变；这不是永久基线。验收命令均禁用 repo bytecode/pytest cache，
compileall 写入 `/tmp/allwin-safe-response-pycache.*`。

`NOT_DETECTED` 仍只代表单份响应的 fixtures 直接字段没有已知 marker，不证明
endpoint 永远不分页。FIFA Club World Cup 与完整分页行为仍 `UNVERIFIED`；
状态保持 **OPEN / READY FOR FINAL INDEPENDENT P2 RE-REVIEW**；
Ready for final independent P2 re-review = **YES**；Club World Cup
单赛事 pilot 仍为 **NO**。
