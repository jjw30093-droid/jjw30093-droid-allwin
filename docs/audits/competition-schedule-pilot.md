# 赛事端点聚合球队全赛事赛程 pilot(Manchester City,2024/2025)

> 本文件是一次性技术验证报告,不是生产能力声明。范围严格限定于本轮实际验证的
> 内容;未验证的赛事(如 FIFA Club World Cup)明确标注未覆盖,不得推广为"全部
> 比赛完整"。

## 0. 范围与非目标

**范围**:验证能否通过现有、已在生产 `ingest_league.py`/`ingest_future_fixtures.py`
中真实用于历史回填的 `FotMobClient.league_matches(league_id, season)`(赛事端点),
对 Manchester City(FotMob Team ID=**8456**)2024/2025 赛季所涉及的多个赛事分别
查询、统一解析、按 Match ID 去重合并,在跨赛事时间线上计算真实 `rest_hours`,
并证明"联赛之间插入的杯赛/欧战会改变真实休息时间"。

**非目标**(本轮明确不做):
- 不是生产批量采集,不写生产 Bronze/Silver/Gold;
- 不是五大联赛批量回填;
- 不构建 Gold 数据集或训练特征;
- 不扩展到其他球队或更多赛季;
- 不对 FIFA Club World Cup 做受限发现(见 §7,理由已单独说明)。

## 1. 阶段 A:收口 team_schedule_pilot 已知问题(前置门槛)

阶段 A 修改 `analysis/team_schedule_pilot/fotmob_team_schedule_pilot.py` 与
`test_fotmob_team_schedule_pilot.py`、`docs/audits/team-schedule-pilot.md`:

- **A1**:`parse_team_schedule_response()` 新增 `isinstance(m, dict)` 守卫,
  fixtures 数组内非 dict 元素抛出新增的 `ScheduleSchemaError`(而非裸
  `AttributeError`),不静默生成部分赛程。新增 4 组反例测试(None/字符串/数组/整数)。
- **A2**:新增 `_parse_strict_positive_int()` 严格 ID 解析——只接受 JSON
  integer(非 bool)或纯十进制数字字符串;拒绝 float(含 9.0/9.9 等整数值
  float)、bool、负数、0、小数点字符串、科学计数法字符串、空字符串、非数字
  字符串。应用于 `provider_match_id`(非法→拒绝该记录)、`home`/`away` 的
  team ID(非法→不猜测,置 None)、`competition_id`(非法类型→置 None,不
  静默截断)。新增 9 组反例测试。
- **A3**:报告 §5 修正"unknown=33 中含 1 场 matchTimeTbd=false"为准确的
  **33/33**。
- **A4**:报告 §11.2 `data/odds.db` 哈希转录错字 `...b315e093` 修正为真实值
  `...f315e093`。

**阶段 A 验收**:59 项测试(33 原有 + 26 新增)全部通过,0 skip/0 xfail;
`compileall`/`git diff --check` 均 exit 0;四库 SHA-256/size/mtime 与本轮
开始前基线完全一致;WAL/SHM 相对基线无新增变化;本阶段**未发起任何真实球队
端点请求**;`team_data()` 历史能力结论维持 **NO_GO**,未改判。

**Current team-endpoint pilot closure = PASS。**

## 2. 请求预算与实际请求清单

| # | 类型 | 赛事/端点 | 参数 | HTTP | 计入预算 |
|---|---|---|---|---|---|
| 1 | competition endpoint | Premier League(47) | season=2024/2025 | 200 | 是 |
| 2 | competition endpoint | Premier League(47) | season=2023/2024 | 200 | 是(season 差异验证) |
| 3 | competition endpoint | FA Cup(132) | season=2024/2025 | 200 | 是 |
| 4 | competition endpoint | FA Cup(132) | season=2023/2024 | 200 | 是(season 差异验证) |
| 5 | competition endpoint | Champions League(42) | season=2024/2025 | 200 | 是 |
| 6 | competition endpoint | Champions League(42) | season=2023/2024 | 200 | 是(season 差异验证) |
| 7 | competition endpoint | Community Shield(247) | season=2024/2025 | 200 | 是 |
| 8 | competition endpoint | EFL Cup 候选(133) | season=2024/2025 | 200 | 是 |

**合计:8 次真实请求**(competition endpoint 8/8,daily_matches 0/3,
match_details 0/2)。总预算 15 次,**保留 7 次未使用**(远超"至少保留 2 次"
的要求;未为凑满预算而继续请求)。前三项关键赛事(英超/足总杯/欧冠)全部
通过身份、season 有效性检查,未触发"停止扩大请求"条款。

未使用 daily_matches:EFL Cup 候选 133 在 competition endpoint 直接验证通过
(名称/ID 均一致),无需受限发现;Club World Cup 未尝试发现(理由见 §7)。
未使用 match_details:5 个赛事的 `league_matches()` 结构已通过生产代码
(`ingest_future_fixtures.py::fetch_fixture_rows`)预先验证过 home/away 提取
逻辑,且本轮 5 份真实响应内部结构一致、身份/日期范围自洽,未发现需要逐场
交叉核对的疑点。

历史 live pilot 当时通过默认 `FotMobClient` 解析代理配置，未在任何脚本、
日志、fixture、报告中打印其值、用户名、密码或完整代理 URL。当前实现已收口
为 import-time 零凭证访问：只有默认 live client 构造且既有环境缺失时才尝试
加载 `.env`；显式 proxy/空 proxy 均不读环境或 dotenv。

## 3. 每个赛事:ID / 真实名称 / 身份 / season 有效性

| requested_id | expected_name | observed_name | identity | season 有效性 | fixture 数 | Man City 场次 |
|---|---|---|---|---|---|---|
| 47 | Premier League | Premier League | **IDENTITY_VERIFIED** | **SEASON_PARAMETER_EFFECTIVE**(380 vs 381 场,日期区间完全不同) | 380 | 38 |
| 132 | FA Cup | FA Cup | **IDENTITY_VERIFIED** | **SEASON_PARAMETER_EFFECTIVE**(123 vs 151 场) | 123 | 6 |
| 42 | Champions League(**已更正**,见下) | Champions League | **IDENTITY_VERIFIED** | **SEASON_PARAMETER_EFFECTIVE**(189 vs 125 场,两季赛制不同,场次数与真实赛制结构精确吻合) | 189 | 10 |
| 247 | Community Shield | Community Shield | **IDENTITY_VERIFIED** | 只验证主赛季(按任务允许) | 1 | 0(见 §6 说明) |
| 133 | EFL Cup(候选) | EFL Cup | **IDENTITY_VERIFIED** | 只验证主赛季(按任务允许) | 93 | 2 |

**registry 名称更正说明**:UCL 注册表初始 `expected_name` 猜测为
"UEFA Champions League",真实响应 `details.name` 只声明为 **"Champions
League"**(不带 "UEFA" 前缀)。`details.id=42` 精确匹配请求 ID,已把
registry 的 `expected_name` 更正为与来源一致的真实观测值——这是**基于真实
证据的更正**,不是放宽比对规则去凑答案(比对逻辑本身未改动)。

**season 有效性的双重证据**(不仅信任单一字段):
1. 响应自带 `details.selectedSeason` 字段,5 个赛事全部精确回显
   `"2024/2025"`(与请求一致,`season_mismatch=False`);
2. 对 PL/FA Cup/UCL 三个关键赛事做了 season=2024/2025 vs 2023/2024 的真实
   两次请求对比:Match ID 集合**零重叠**、日期区间完全不同、赛制结构随赛季
   变化(UCL 新旧赛制场次数精确吻合理论值:2024/25 新 36 队联赛阶段
   144+16+16+8+4+1=189;2023/24 旧小组赛 96+16+8+4+1=125)。这与球队端点
   `team_data()` 那种"改哪个 season 参数返回内容都完全相同"的滚动窗口行为
   **截然相反**——`league_matches()` 是真正按赛季查询的历史赛程端点。

**Community Shield 的 `selectedSeason` 命名语义(需单独说明,避免误读)**:
Community Shield(247)以 `season=2024/2025` 请求,响应内 `details.selectedSeason`
如实回显 `"2024/2025"`,但该场比赛真实 kickoff 是 **2025-08-10**——落在自然
日历意义上的"2025/2026 赛季揭幕战"窗口,而不是"2024/2025 赛季"惯常认知的
2024 年 8 月至 2025 年 5-6 月区间。这不是抓错赛季或数据错误:FotMob 对
Community Shield 这类单场资格赛事采用"以谁的冠军身份参赛"来标注 season(即
"2024/2025 赛季的联赛冠军与足总杯冠军之间的对决"),而不是"比赛发生在哪个
日历年度"。`season_mismatch` 字段按代码里的字面值比较(`selectedSeason` 是
否等于请求值)不会标记这种情况,因为两者字符串确实相等——**这是该字段设计
本身覆盖不到的一类语义偏移,不是本 pilot 的判定错误**。若后续把该 pilot
泛化为正式功能,需要在 `docs/data-sources.md` 里显式记录这一命名惯例,否则
容易被误读为"该队某赛季缺少揭幕战数据"。

`fixtures.allMatches` JSON 路径与生产代码
(`backend/ingest/ingest_future_fixtures.py::fetch_fixture_rows`)已验证的
结构完全一致:每场比赛 `{id, home:{id,name}, away:{id,name}, round,
status:{utcTime,finished,cancelled,started}}`。**重要细节**:该端点的
`id`/`home.id`/`away.id` 均为 **JSON 字符串**(如 `"8456"`),与
`team_data()` 球队端点的整数类型不同——严格 ID 解析器
`_parse_strict_positive_int()` 对两种类型均正确处理(纯十进制数字字符串
被接受),未假设两个端点 JSON 结构相同。

## 4. Pagination / 截断检查

5 份既有保存响应的 `fixtures` 对象只有 `{firstUnplayedMatch, allMatches,
hasOngoingMatch, fixtureInfo}` 四个键,没有 `next`/`nextPage`/`nextUrl`/
`cursor`/`nextCursor`/`previousFixturesUrl`/`nextFixturesUrl`/`hasMore`,
也没有 `currentPage < totalPages` 或 `page < pageCount`。当前代码只把这类
响应记为 **`pagination_status=NOT_DETECTED`**:它表示"这份响应中没有检测到
已知 continuation marker",**不证明 endpoint 永远不分页**。

各赛事场次数与真实赛制的理论值吻合仍是有价值的结构性支持——Premier
League 380=20×19×2;UCL 2024/25 新赛制 189=144+16+16+8+4+1;UCL
2023/24 旧赛制 125=96+16+8+4+1;EFL Cup 93 场按轮次分布
`{R1:35,R2:25,R3:16,R4:8,QF:4,SF:4,决赛:1}` 求和为 93——但它不能代替
pagination 协议证据。永久 mutation 测试已确认：只在 `fixtures` 对象的直接
pagination metadata 上，任一已知 continuation marker 非空/为真/页码显示未到
末页，或 marker 状态无法判定时，均得到 `PAGINATION_UNRESOLVED` 并阻断下游；
不进入 `allMatches`，不扫描 QAData/details/比赛/球队/球员/事件等业务对象。本轮
不实现后续页面抓取。

## 5. Manchester City 每项赛事真实比赛数(2024/2025)

| 赛事 | Man City 场次 | 备注 |
|---|---:|---|
| Premier League | 38 | 与 allwin.db 历史记录逐场对齐(见 §8) |
| FA Cup | 6 | R3→R4→R5→QF→SF→**决赛**(决赛 1-0 负 Crystal Palace,真实比分,`scoreStr="1 - 0"`) |
| EFL Cup | 2 | R3 主场 2-1 胜 Watford,R4 客场 1-2 负 Tottenham,提前出局 |
| Champions League | 10 | 新赛制联赛阶段深度晋级(2023/24 旧赛制同样 10 场) |
| Community Shield | 0 | **真实原因,非数据缺陷**:2025 年社区盾是 Crystal Palace(足总杯冠军)vs Liverpool(联赛冠军),Man City 输了足总杯决赛,不满足参赛资格,故未出现——数据内部完全自洽 |
| FIFA Club World Cup | 未覆盖(UNVERIFIED) | 见 §7 |

`required_for_pilot=True` 的 5 个赛事中,4 个 Man City 场次 > 0;Community
Shield 的 0 场经核实是真实赛果导致(非结构性缺失),不视为覆盖失败。

## 6. 多赛事合并

`merge_competition_schedules()` 对 5 个赛事共 **786** 条原始记录(380+123+
189+1+93)合并:
- **合并前**:786 条(每条记录 `requested_competition_id` 严格等于发起该
  次查询的赛事 ID,不同赛事之间的 Match ID **零重叠**,因此合并**零冲突**);
- **合并后**:786 条(无重复,`source_provenance` 均为单元素列表)。
- 冲突检测逻辑(home/away/kickoff/competition_id/competition_name 任一不同
  即 fail-loud)由当前永久对抗性测试覆盖,在既有保存的真实响应上未触发
  (符合预期——5 个赛事天然不相交);当前实际测试数量统一见 §16。

筛出 Manchester City 参与的比赛:**56 条**(38 league + 8 domestic_cup(6
FA Cup + 2 EFL Cup) + 10 continental(UCL) + 0 super_cup + 0 friendly)。
Match ID 重复数 = 0。

## 7. FIFA Club World Cup:明确未覆盖

本轮**未尝试**发现 Club World Cup 的赛事 ID,理由:
1. 受限发现流程(`daily_matches`)要求"只能选择已知 Manchester City 实际
   参加该赛事的具体日期做定点发现",本轮没有可靠的真实比赛日期可用于定点
   查询,盲目尝试多个日期将违反"不得扫描连续日期"的纪律;
2. 真实世界 FIFA Club World Cup(扩军版)传统上在北半球夏季(6-7 月)举行,
   即 2024/2025 英超赛季结束(2025-05-25)之后、2025/2026 赛季揭幕(见
   Community Shield 2025-08-10)之前的休赛期——**即便本数据集中存在该赛事
   且 Man City 参赛,其比赛时间点也不会插入到任何一场 2024/2025 英超比赛
   之前**,不影响本轮"英超赛前 rest_hours"这一核心验证目标。

**Club World Cup 覆盖状态:UNVERIFIED / 未纳入注册表(不得推断为"完整覆盖"
或"确认不存在")。**

## 8. 与 allwin.db 对齐(只读,`mode=ro&immutable=1`)

Premier League Match ID 与生产库对齐(SQL 实时查询,未硬编码 38):

| 指标 | 值 |
|---|---:|
| allwin_count(League_ID=47, Season=2024/2025, team=8456) | **38** |
| pilot_epl_count | **38** |
| intersection | **38** |
| only_in_allwin | **0** |
| only_in_pilot | **0** |
| home/away 方向冲突 | **0** |
| Match ID 重复 | **0** |
| kickoff_comparable_count | **0**(allwin.db 该赛季历史行 `kickoff_at_utc` 全为 NULL,补列前落库——如实标注"不可比较",不伪造 kickoff_conflict=0 的强结论) |
| date_comparable_count | **38** |
| date_conflict_count | **0** |

**kickoff 不可比较的范围说明(避免误读)**:`kickoff_at_utc` 为 NULL 不是本次
查询条件(League_ID=47, Season=2024/2025)特有的现象——`data/allwin.db` 的
`dim_match` 表**全库范围内**(不限任何 League_ID/Season)该列全部为 NULL,
是 CLAUDE.md §6.2.1 补列前落库的既有生产数据缺口,与本 pilot 无关。
`kickoff_comparable_count=0` 必须读作"没有可比较的样本对,该维度不可判定",
**绝不能**把 `kickoff_conflict_count=0` 单独摘出来解读成"开球时间完全吻合"
——0/0 不构成任何强度的一致性证据,只是诚实的"不可比较"。

**League completeness(Premier League)= VERIFIED**——38/38 全量对齐,
0 冲突,0 重复。**注意**:此 VERIFIED 仅针对英超本身,不代表国内杯赛/欧战
同样完整(见下)。

## 9. kickoff 精度 / 状态分布

786 条合并记录、56 条 Man City 记录:**kickoff_precision 100% exact,
status 100% finished、0% cancelled、0% upcoming**——2024/2025 是完整已结束
赛季,不含 TBD/cancelled/upcoming 样例。

**需要明确披露的验证边界**:`compute_rest_hours()`/`build_team_match_records()`
里排除 `cancelled=true`、排除 `finished=false`(upcoming)、排除
`competition_class='friendly'` 的三段过滤逻辑,在本轮 5 份真实响应(786 场)
上**一次都没有被真实触发**——因为这些历史数据本身 100% 是已完赛、非取消、
且 5 个已注册赛事里没有一个是 Club Friendlies。也就是说,这三条过滤分支
目前只有**离线单元测试**(`test_cancelled_excluded_from_rest`、
`test_unfinished_not_counted_as_previous`、`test_synthetic_cancelled_upcoming_case`
等,均用人工构造/显式标注 synthetic 的数据)验证过其行为正确,**未经真实
数据验证其在真实响应上同样生效**。这是诚实的验证边界披露,不是缺陷——
只是提醒:如果未来某个真实赛季数据里出现了取消赛事或未来赛程,这三条过滤
逻辑届时才第一次接受真实数据检验。

## 10. 国内杯赛 rest 案例(真实,live 验证)

跨赛事合并后的 56 场 Man City 时间线上,`find_cross_comp_rest_examples()`
真实找到 **17 个** `all_comp_rest_hours < league_only_rest_hours` 的样本。
挑选一个由 **EFL Cup** 缩短英超休息时间的真实案例:

- 当前英超比赛:Match ID **4506320**(2024-09-28 11:30 UTC)
- 上一场英超:Match ID **4506309**(2024-09-22 15:30 UTC)→ `league_only_rest_hours = 140.0h`
- 上一场任意正式比赛:Match ID **4620887**,**EFL Cup**(2024-09-24 18:45 UTC,主场 2-1 胜 Watford)→ `all_comp_rest_hours = 88.75h`
- **差值:51.25 小时**

另一个由 **FA Cup** 缩短的真实案例:当前英超 4506514(2025-01-14 19:30)
league_only 上一场 4506509→244.5h,all_comp 上一场 4684077(FA Cup,
2025-01-11 17:45)→73.75h,差值 **170.75 小时**。

**Cross-competition domestic rest = OFFLINE_AND_LIVE_VERIFIED**(离线单测
`test_domestic_cup_shortens_league_rest` 用合成数据验证逻辑,真实抓取数据
独立复现出 EFL Cup + FA Cup 两类真实案例)。

## 11. 欧冠 rest 案例(真实,live 验证)

同一时间线上找到多个由 **Champions League** 缩短英超休息时间的真实案例,
其中一例:

- 当前英超比赛:Match ID **4506309**(2024-09-22 15:30 UTC)
- 上一场英超:Match ID **4506299** → `league_only_rest_hours = 193.5h`
- 上一场任意正式比赛:Match ID **4621492**,**Champions League**
  (2024-09-18 附近开球)→ `all_comp_rest_hours = 92.5h`
- **差值:101.0 小时**

56 场时间线中共 17 个跨赛事 rest 缩短案例里,多数(约 12/17)由 Champions
League 插入造成,其余由 FA Cup/EFL Cup 造成——欧冠对英超赛前休息时间的
真实压缩效应比国内杯赛更显著且更频繁,与"欧冠中场周赛程更密集"的常识
一致。

**Cross-competition continental rest = OFFLINE_AND_LIVE_VERIFIED**(离线
单测 `test_champions_league_shortens_league_rest` + 真实数据独立复现)。

## 12. Point-in-time 纪律

`compute_rest_hours()` 的 `matches_last_7d`/`matches_last_14d`/`rest_hours`
只回看 `qualifying[:idx]`(当前比赛之前的记录),不引用列表中排在当前比赛
之后的任何记录。离线单测 `test_future_match_does_not_change_past_rest_or_lookback`
构造"在末尾追加一场未来比赛"的反例,验证此前比赛的 `rest_hours`/
`matches_last_7d`/`matches_last_14d` 完全不受影响(逐字段相等断言)。
本 pilot **未实现**"未来 7/14 天比赛数"这类需要 point-in-time schedule
snapshot 才能安全使用的特征,不存在提前实现的风险。

## 13. 不可计算 / 不可声称的内容

- **kickoff 冲突对比**:allwin.db 该历史赛季无精确 `kickoff_at_utc`,只能
  比较 `Date`,不得声称"kickoff 完全一致"。
- **加时赛**:`went_to_extra_time` 恒为 `None`——来源(`league_matches()`)
  未提供加时/点球信息,不编造 `False`。
- **国内杯赛/欧战完整性 ≠ 英超完整性**:英超 38/38 VERIFIED 不能反推
  FA Cup(123 场)、EFL Cup(93 场)、UCL(189 场)同样完整——本轮**未**对
  这三个赛事做过 allwin.db 式的"外部独立真实源"交叉验证(allwin.db 本身
  只覆盖五大联赛,不含这些赛事的历史真实记录可供比对),完整性判定只能
  基于结构性证据(场次数与真实赛制吻合、无分页字段)作为**强支持**,不是
  第三方独立源交叉核对意义上的 VERIFIED。
- **FIFA Club World Cup**:完全未覆盖,不得推断真实赛程如何。
- **真实"负荷"/"体能"**:rest_hours 只是赛程间隔,不代表球员实际出场分钟、
  是否首发、是否加时——不得把间隔小时数直接等同于"比赛负荷"。

## 14. 生产数据库完整性

四库 SHA-256/size/mtime 与本轮(含阶段 A)开始前基线**逐字节一致**:

| 库 | SHA-256(前后相同) | size | mtime |
|---|---|---:|---|
| `data/allwin.db` | `92a6a39c...ab364e` | 406073344 | 2026-07-19 23:21:06 |
| `data/verify_leagues.db` | `603163b5...940a19c0` | 305598464 | 2026-07-11 16:46:03 |
| `data/platform.db` | `c21e7008...751e99e8ea2d` | 716800 | 2026-07-20 05:14:07 |
| `data/odds.db` | `cdc5fd54...f315e093` | 147456 | 2026-07-19 23:37:07 |

`data/allwin.db-shm`(32768B)/`-wal`(0B,mtime 2026-07-23 02:46)与
`data/odds.db-shm`(32768B)/`-wal`(0B,mtime 2026-07-21 15:27 / 07-20
20:48)**均为本轮开始前已存在**(历史只读连接遗留),本轮未新增、未删除、
未 checkpoint。全部只读查询使用 `mode=ro&immutable=1`。

git:未产生新 commit(`HEAD` 始终 `cfe0272`);`git stash list` 为空;
`git status --short` 与阶段 A/B 开始前基线逐行一致(仅
`analysis/`、`docs/audits/`、`tests/fixtures/fotmob/` 下新增未跟踪文件,
均属本轮 pilot 产出,不影响既有 dirty worktree)。凭证扫描:pilot 代码/
fixture/报告/临时 SQLite/stdout 均未出现 `THORDATA_PROXY=` 赋值或
`user:pass@host` 形状字符串。

## 15. Fail-closed 收口(2026-07-24,离线实现与 mutation 验证)

本节取代此前基于"场次数吻合 + 未看到分页字段"给出的放行建议。收口前先用
临时目录和离线变异响应真实复现了四个缺陷:

1. `allMatches=[]` 被写成成功状态，CLI 继续 merge、calendar/team 写入与
   rest 计算;
2. returned season 缺失仍被当作成功;
3. `hasMore=true` 被硬编码保存为 `pagination_detected=0`;
4. 同一 competition 的第二个 season 被当作同一行幂等跳过,只保留第一季。

修复后的门禁:

- `allMatches=[]` 明确为 `EMPTY_FIXTURES`;非空赛事但目标球队 0 场仍允许
  通过,两种情况不再混淆;
- returned season 缺失、空白、非字符串或不符合 `YYYY` / `YYYY/YYYY`
  (跨年必须相邻)时为 `SEASON_UNVERIFIABLE`;合法但不匹配时为
  `SEASON_MISMATCH`;
- 已知 pagination marker 只检查 `raw["fixtures"]` 的直接字段：`hasMore`；
  `next`/`nextPage`/`nextUrl`；`cursor`/`nextCursor`；
  `previousFixturesUrl`/`nextFixturesUrl`；`currentPage/totalPages`；
  `page/pageCount`；`page/totalPages`。禁止递归和进入 `allMatches`。
  URL/cursor 只有非空字符串
  是 `DETECTED`；URL/cursor 的 `None`/`False`/空字符串/空容器表示没有
  continuation，数字、`True`、非空容器等非法类型是 `UNRESOLVED`。
  `hasMore` 只有严格 `True`/`False`/`None` 合法，其他类型为 `UNRESOLVED`；
  三种 page pair 都只接受非布尔、非负整数；`current<total` 为
  `DETECTED`、相等为无 continuation、`current>total` 或类型错误为
  `UNRESOLVED`。不完整 page family 的 evidence 分别命名实际存在字段和明确
  缺失 companion，不生成不存在字段的伪路径。完整方言仅消费自身的 key，
  所以缺少可选别名不会误报；但完整方言不能掩盖额外、未被任何完整方言
  消费的已知 page-family marker。该孤立 marker 无论值为整数、字符串、
  bool 或负数都为 `UNRESOLVED`，并与完整 pair 的 detected evidence
  分别保留。多个完整方言语义冲突时记录 conflict 并保留 detected evidence。
  detected 与 unresolved 同时出现时分别保留并持久化证据并集，整体 fail
  closed。所有
  已知 marker 先按 `casefold()` 建立“一对多”索引；同一标准化 key 对应两个
  原始字段时，无论值相反、一个为空或两个值完全相同，都直接
  `UNRESOLVED`，并为每个原始字段路径保存 collision evidence，不选择任一值;
  collision 只消费其自身 normalized key，不能屏蔽其他非碰撞 orphan。
  orphan 的 companion 只有完全不在 marker index 时才记录 missing；若它已
  存在但 collided，则只保留 collision evidence，不伪造 missing evidence;
- `pilot_competition_registry` 自然键改为
  `(competition_id, requested_season)`。同一自然键所有验证字段相同才
  幂等 skip;任一关键字段变化都抛 `ScheduleConflictError`。现有表必须有
  精确相同的复合主键和全部依赖列；旧单列主键不自动迁移，抛
  `PilotSchemaIncompatibleError` 并要求新 output directory;
- parser 自身在解析记录前验证 requested/returned season 及相等关系，分别
  抛 `SeasonUnverifiableError` / `SeasonMismatchError`；上层状态仍保持
  `SEASON_UNVERIFIABLE` / `SEASON_MISMATCH`;
- backend 测试 app fixture 动态绑定 tmp core `DB_PATH`；FotMob client import
  不加载 dotenv、不读取代理环境变量，显式 proxy 路径完全隔离。`_get()`
  不再记录完整 URL、HTTP body 或原始外部异常文本，最终只抛
  `FotMobTransportError` / `FotMobHTTPError`，message 仅含安全类别、状态码
  和 attempt 计数，并用 `from None` 断开原始异常链。`status_code` accessor
  位于 worker `try/except` 内；五个公开 JSON API、SSR text 与
  `__NEXT_DATA__` JSON 统一通过安全 helper，decode 失败只暴露固定 operation
  和异常 class，并抛 `FotMobDecodeError from None`。
  `parse_season_player_stats()` 的单维度失败 warning 也不再记录外部 stat
  name、URL 或 `str(exc)`;
- 任一 required competition 失败时,先把全部成功/失败 registry 结果写入,
  然后固定 exit 1;merge、team projection、calendar/team 写入和 rest
  计算均不得被调用。

### `season_parameter_verified` 的精确语义

returned season 等于 requested season 只证明**这一份响应自称属于所请求
season**,不能单独证明 endpoint 的 season 参数有效。虽然 §3 记录了此前
PL/FA Cup/UCL 的跨赛季真实观测,当前离线 fixture/CLI 输入没有携带那几组
配对原始响应,代码没有把历史报告文字硬编码成机器证据。因此本轮 registry
中的 `season_parameter_verified` 保持 `NULL`,成功响应的 completeness 为
`RESPONSE_VALIDATED_SEASON_PARAMETER_UNVERIFIED`,而不是 `VERIFIED`。
`verify_season_parameter_effectiveness()` 仍可对两份明确输入的响应产生
跨赛季判定,但当前单季 CLI 不冒用它。

## 16. 当前测试与验收证据(2026-07-25)

- 最终独立 P2 复核确认 P0=0、P1=0，但以 **P2=2 /
  `FIX_REQUIRED`** 拒绝放行：真实 `curl_cffi.Response` 的安全错误为
  `FotMobDecodeError ... UnicodeDecodeError`，永久测试却写死
  `JSONDecodeError`；四份 closure 文档也把 `-W default` 下的 warnings
  错写为 0。本轮仅修正该测试契约和文档，没有任何生产代码变化；
- 真实 response 测试修正前 **1 failed**、修正后 **1 passed**。当前断言锁定
  `FotMobDecodeError`、固定 `team_data` operation、identifier-only class、
  marker 全表面清除及无 cause/context，而不绑定 curl_cffi 版本选择 UTF-8
  还是 JSON decoder；FakeResponse 四异常类型矩阵保持不变；
- 第五轮独立复核在修复前给出 **FIX_REQUIRED**：3 个 P1 为 collision
  全局屏蔽独立 orphan、HTTP-200 JSON/text/SSR decode 泄漏、
  `status_code` accessor 在 worker 安全边界外；2 个 P2 为错误的
  “collision only”永久断言和不准确的文档状态/计数。新增测试落盘后旧实现
  真实 RED：pagination collision+orphan **8 failed**，FotMob
  status/decode/downstream-warning redaction **25 failed**。
- 本轮新测试落盘、实现修改前的定向 RED：**21 failed**，真实复现本次独立
  报告的五类缺口;
- 第三轮最终修复的永久测试在旧实现上再次真实 RED：pagination collision
  矩阵 **16 failed / 2 path-scoping controls passed**，FotMob transport/HTTP
  redaction 矩阵 **4 failed**;
- 第四轮 page-dialect 27 项定向永久测试在旧实现上真实 RED：
  **21 failed / 6 existing controls passed**;
- 第四轮窄幅 orphan-marker 13 项定向永久测试在旧实现上真实 RED：
  **8 failed / 5 complete-dialect controls passed**;
- 第五轮 collision+orphan 定向 **8 passed**；完整
  pagination/orphan/collision scope **61 passed**；FotMob
  status/decode/downstream-warning redaction 定向 **25 passed**；
- FotMob status/decode/downstream-warning 定向 **25/25**；
  competition pilot:**193/193**；team pilot:**93/93**；联合
  **286 collected / 286 passed**。上述命令均 0 failed/skip/xfail/warnings;
- Allsvenskan API 类 **7 passed**；fixture 使用一次计算的 timezone-aware
  `UTC now + 3 days` 同步生成 season/date/kickoff，并断言仍在未来及 publish
  后状态为 `published`;
- API、cache-policy、studio 三处实际 publish fixture 均已改为运行时
  timezone-aware `UTC now + 3 days`，publish 前显式断言仍在未来；
- legacy 数据库隔离定点测试通过；`tests/backend/test_contract.py`
  **30/30**，1 个 `StarletteDeprecationWarning`;
- API/cache/studio 三个全文件 **78 passed**；安全九文件 FotMob/provider
  **342 collected / 342 passed**，0 failed/skip/xfail；两条命令各有 1 个
  相同来源的 `StarletteDeprecationWarning`。Allsvenskan **7/7**，同样 1 个
  该 warning。十文件范围只做 collect，真实为 **346 collected**，collection
  也出现同一 warning；
  `test_e2e_seed.py` 四项按明确禁令 **NOT RUN / UNVERIFIED**;
- 在 isolation 通过后运行排除 `test_e2e_seed.py` 的同范围后端广泛回归：
  **531 collected / 531 passed**，0 failed/skip/xfail、3 warnings：上述
  Starlette/httpx 第三方弃用警告 1 个，以及两个既有测试清理
  `ResourceWarning`。其一是 auth uvicorn smoke 的
  `Popen(stdout=PIPE)` BufferedReader 未关闭（延迟到后续测试报告）；其二是
  release rollback 测试的 `HTTPServer` 只 shutdown 而未 server_close。
  两者均非生产逻辑且不属于本轮 P2，未扩大施工。相同 Starlette warning
  在多个独立进程各出现一次，不代表多个不同缺陷;
- `PYTHONPYCACHEPREFIX=$(mktemp -d /tmp/allwin-final-p2.XXXXXX)
  python -m compileall -q analysis backend tests/backend` exit 0;
- 本轮所有 mutation、CLI 和 SQLite 写入都在 pytest 临时目录;没有网络请求,
  没有读取代理凭证,没有生产 migration/Worker 接入。

## 17. 当前最终判定

1. **Current team-endpoint pilot closure:PASS**(原 NO_GO 能力边界不变)
2. **Competition response fail-closed gate:PASS**
3. **Competition identity on saved responses:VERIFIED**(5/5)
4. **Returned-season gate:PASS**
5. **Season parameter effectiveness in current registry rows:UNVERIFIED**
6. **Pagination path scoping:PASS**
7. **Pagination casefold collision safety:PASS**
8. **Pagination three-dialect semantics and evidence completeness:PASS**
9. **Pagination orphan-marker fail-closed safety:PASS**
   (collision 不会屏蔽独立 orphan；已存在但 collided 的 companion 不伪报
   missing)
10. **Old-schema explicit rejection:PASS**
11. **Legacy test DB isolation:PASS**
12. **FotMobClient import credential isolation:PASS**
13. **FotMob transport/status accessor/HTTP/JSON/text/SSR/downstream-warning
    redaction:PASS**
14. **Allsvenskan relative-time fixture:PASS**
15. **Low-level parser defense:PASS**
16. **Original EMPTY_FIXTURES gate:PASS**
17. **Original required identity gate:PASS**
18. **Full endpoint pagination behavior:UNVERIFIED**
19. **Premier League saved-response alignment:VERIFIED**(38/38)
20. **Domestic cup / Champions League completeness:PARTIAL**
21. **FIFA Club World Cup:UNVERIFIED / NOT REQUESTED**
22. **Documentation correction:PASS / AWAITING INDEPENDENT RE-REVIEW**
23. **Database main-file integrity:PASS**(四库 SHA-256/size/mtime 未变)
24. **Strict WAL/SHM integrity:PASS**(集合、哈希、大小、mtime 未变)
25. **Cache integrity from third-round baseline:PASS**
26. **Ready for final independent P2 re-review:YES**
27. **Ready for Club World Cup single-competition pilot:NO**
28. **Ready for scale:NO**

当前模块状态保持 **OPEN / READY FOR FINAL INDEPENDENT P2 RE-REVIEW**。
进入 Club World Cup 单赛事 pilot 仍需要一个新的、独立的对抗式复核任务。
本轮没有联网、没有进行该赛事请求,也没有给批量采集或扩大范围放行。

上一轮独立只读复核误刷新了三个既有 `.pyc`，该历史过程事件仍是
review-integrity **FAIL**，没有被删除、touch、重建或伪造恢复。本轮在事件
发生后只比较本轮开始/结束快照；2026-07-25 两端均为 107 个
`__pycache__` 目录、808 个 `.pyc`，内容摘要
`9cd6b06d389483315befa6cc07c8936fb8f363a5810210245674e2771789cda7`、
元数据摘要
`9db30ce3fda74a4fa7844a93439698cd7f26ac39300d8dde51fb5a035d7692f3`
均不变。该数值不是永久基线；pytest 全部带
`PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`，compileall 仅写 `/tmp`。
