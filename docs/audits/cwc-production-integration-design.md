# Club World Cup offline production-integration design audit

> 日期：2026-07-25
> 状态：CLOSED / PROTOTYPE_VALIDATED_READY_FOR_PRODUCTION_SCHEMA_DESIGN /
> LATER-OBSERVATION IDEMPOTENCY CLOSURE VALIDATED /
> POINT-IN-TIME FEATURE LINEAGE CLOSURE VALIDATED /
> DETERMINISTIC FEATURE INPUT CONTRACT CLOSURE VALIDATED /
> SAFE TIMESTAMP ERROR BOUNDARY CLOSURE VALIDATED /
> INDEPENDENT RE-REVIEW COMPLETE
> Production integration readiness：NO

## 1. 判定与严格边界

本轮交付的是 competition `78` 的离线生产形态设计原型，不是生产接入。

- Historical data verdict：
  **GO_SINGLE_COMPETITION_DATA_VALIDATED**
- Historical live runner：
  **PERMANENTLY_SEALED**
- Historical authorized requests：**3/3 consumed**
- This-round network requests：**0**
- Production integration：**NOT STARTED**

本轮没有真实 HTTP/DNS/代理探测，没有读取 `.env` 或 ThorData 凭证，没有
调用 sealed live/resume，没有写真实数据库，没有 migration、Worker、
systemd、API、frontend、batch 或 deployment。

## 2. 真源与永久 fixture

唯一输入真源是前轮已保存并验证的：

`/tmp/allwin-cwc-single-pilot-20260725T100615Z/raw/competition_78_2025.json`

转换前先校验：

- size：858,430 bytes；
- mode：0600；
- SHA-256：
  `6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`。

确定性转换输出：

`tests/fixtures/fotmob/cwc_2025_competition_schedule_canonical.json`

- size：25,256 bytes；
- SHA-256：
  `020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1`；
- transformation version：`cwc_schedule_canonical_v1`；
- description：`trimmed from validated saved response`；
- pagination：
  `NOT_DETECTED_FOR_SAVED_RESPONSE`；
- fixture count：66。

fixture 只保留以下公开、必要字段：

- competition id/name/selectedSeason；
- match ID；
- home/away team ID/name；
- status short/finished/cancelled/started/utcTime；
- round；
- source SHA、转换版本和有限 provenance。

未复制 headers、请求 URL、proxy、Authorization、认证信息、响应 cookie 或无关
metadata。redaction/credential-shape 扫描为 0 finding。永久测试只依赖该
仓库 fixture，不依赖 `/tmp` artifact 永久存在；原 artifact 未被修改。

## 3. Season 与 registry 设计

CWC registry 固定：

| 字段 | 值 |
|---|---|
| provider | `fotmob` |
| competition_id | `78` |
| expected/observed name | `FIFA Club World Cup` |
| competition_class | `international_club` |
| requested/returned season | `2025` |
| season_strategy | `calendar_year` |
| identity/season verified | `1 / 1` |
| pagination_status | `NOT_DETECTED_FOR_SAVED_RESPONSE` |
| fixture_count | `66` |
| completeness_status | `VALIDATED_SAVED_RESPONSE_ONLY` |

registry 自然键为
`(provider, competition_id, requested_season)`。

`validate_season_strategy()` 只接受显式配置：

- `calendar_year`：四位 `YYYY`；
- `split_year`：相邻的 `YYYY/YYYY`；
- `explicit`：调用方提供且完全相同的固定 label。

bool、非法字符串、`2025/2025`、`2025/2027`、缺失 explicit label 或按
competition name 猜测一律拒绝。本轮只证明 competition `78` 使用自然年
`2025`，不推广到其它赛事。

## 4. Prototype schema

所有表只创建在 caller-supplied `/tmp` 或 pytest temp SQLite：

### `prototype_competition_registry`

自然键：
`(provider, competition_id, requested_season)`。

保存稳定 identity、season strategy/verification、pagination、fixture
count、source SHA 和 completeness；不保存 `observed_at`。

### `prototype_match_calendar`

自然键：
`(provider, provider_match_id)`。

保存 competition/season/class、exact UTC kickoff、主客 ID/name、状态、
finished/cancelled、round、有限 source endpoint label、source SHA 和
immutable payload hash；hash 不含 observation/run time。

### `prototype_team_match`

自然键：
`(provider, provider_match_id, team_id)`。

每场生成主客两行，保存 opponent、home/away、competitive、finished、
cancelled、eligible_for_load、exclusion_reason、season 和 payload hash。

### `prototype_team_rest_feature`

自然键：
`(provider, provider_match_id, team_id, feature_version, input_set_hash)`。

保存 previous match、kickoff、kickoff gap、calendar gap、72/96 小时门槛、
过去 7/14 日比赛数、feature version、previous status、provenance 和
payload hash。每个 feature index `i` 的 `input_set_hash` 只来自该队按
kickoff/Match ID 排序、截至当前场的 eligible prefix `timeline[:i+1]`；
`observed_at` / `computed_at`、临时 DB 路径、本轮运行时间和当前场之后的
fixture 均不进入 feature payload。`build_feature_input_set_hash()` 自身强制
prefix 非空、Match ID 唯一且 kickoff 严格递增；乱序、相同 kickoff、相邻或
非相邻重复 ID、以及任何晚于最后一场 current feature 的输入都 fail closed。
helper 不会静默排序、去重、丢弃非法行或用 Match ID 为相同 kickoff 强制定序。
canonical parser 的排序不是唯一防线。

Timestamp parsing has a separate fixed safety boundary. `_parse_utc()` accepts
valid `Z` and explicit `+00:00` values, rejects naive/non-UTC/malformed inputs,
and maps every covered invalid value to
`PrototypeDataError("invalid UTC timestamp")`. It does not echo the raw value
or field label and does not retain the lower parser's cause or context.

### `prototype_schedule_observation`

自然键：
`(provider, competition_id, requested_season, observed_at)`。

保存 source artifact SHA、稳定 source content hash、fixture count、
pagination status 和 transformation version。它只表达“系统在这个事件时间
观察到这份内容”，不声称本轮重新联网或重新验证数据。

任何 partial、列/主键 drift、约束被弱化或混入其它 user table 的临时 schema
都在写前 fail closed。gate 会比较完整的规范化 DDL，不会因列名/主键表面相同
就接受缺失 CHECK/FK/NOT NULL 的表。仓库 `data/` 和非临时路径在
`sqlite3.connect` 前拒绝。

## 5. 写入、幂等与冲突语义

前一版的相同时间重跑曾通过，但独立复核以
`2026-07-25T12:00:00Z` 首次写入、`12:05:00Z` 同 fixture 重跑时，真实得到
`PrototypeConflictError in prototype_competition_registry`。因此原
`DESIGN_VALIDATED_WITH_TEMP_DB` 只证明同一 `observed_at` 幂等，已被该
later-observed-at 反例推翻，不能直接提升为 production migration。

修正后解析/全量校验发生在事务之前；事务开始后先比较所有已有业务实体，再
比较 observation key，最后才在同一个 `BEGIN IMMEDIATE` 中写五张表：

- natural key 不存在：plain `INSERT`；
- natural key 存在且所有持久字段完全相同：`skipped`；
- 同一内容、后续 `observed_at`：四张业务表保持逐字段不变，只新增一个
  observation；
- 同一内容、早于既有时间：作为独立 event-time 追加，语义固定为
  `APPEND_ONLY_EVENT_TIME_CAN_BE_OUT_OF_ORDER`；
- natural key 存在但任何字段不同：`PrototypeConflictError`；
- conflict：整批 rollback，包括尚未提交的新 observation。

不存在 `INSERT OR REPLACE`、`UPDATE` 或 last-write-wins。mutation test 把
León cancelled `4685727` 的 `cancelled` 改为 false，实际触发 payload
conflict，五表行数与逐行内容不变。另一个测试改变 Manchester City 一场
eligible kickoff，证明仅该时间点及之后的 `input_set_hash` 改变；由于
calendar 是不可变业务实体，当前原型选择显式 conflict 并保留旧 feature，
而不是静默追加一组与冲突 calendar 脱节的 feature 或覆盖旧证据。

## 6. 66 / 132 / 126 数据证明

离线 fixture 实算：

| 结果 | 数量 |
|---|---:|
| registry | 1 |
| source/calendar fixtures | 66 |
| non-cancelled | 63 |
| cancelled | 3 |
| team relations | 132 |
| observed historical rest features | 126 |

三条 source-declared León cancelled：

- `4685727`
- `4685729`
- `4685730`

它们全部保留在 calendar，主客六条 relation 也保留，并满足：

- `cancelled=true`
- `eligible_for_load=false`
- `exclusion_reason=cancelled`

它们不会成为任何 rest feature 的 current/previous match，也不会进入
`matches_last_7d` / `matches_last_14d`。总记录数没有通过删除它们从 66
伪装成 63。

## 7. Manchester City 时间线

Team ID：`8456`。

| Match ID | source status | kickoff gap hours |
|---:|---|---:|
| 4685744 | FT | NULL |
| 4685746 | FT | 105 |
| 4685748 | FT | 90 |
| 4685772 | AET | 102 |

四场均 `finished=true`、`cancelled=false`。间隔由 timezone-aware UTC
datetime 实际相减，不在业务函数内硬编码。

`kickoff_gap_hours` 的定义严格为：

`current kickoff_at_utc - previous eligible kickoff_at_utc`

它是赛程开球间隔，不是生理恢复程度，不扣除实际终场时间、加时分钟、旅行或
训练负荷。最后一场 `AET` 被保留用于来源语义，但没有擅自从 102 小时扣除
30 分钟。

## 8. Eligibility 与 point-in-time

竞争分类不再写成 CWC 专用的隐含条件。显式
`COMPETITIVE_CLASSES` 为：

- `league`
- `domestic_cup`
- `continental`
- `super_cup`
- `international_club`

上述类别也只有在 registry 已验证时才 competitive。observed load 必须同时：

- finished；
- non-cancelled；
- exact UTC kickoff；
- verified competitive class。

cancelled 优先给出 `cancelled`；unfinished、friendly、unknown/other class、
unverified registry 和非 exact kickoff 各自有显式 exclusion reason。
unknown 不会自动升级成 competitive，也不从 competition name 猜分类。

当前 feature scope 仅为 `observed_historical`。若未来生产需求包含未开赛但已
排期的比赛，必须另建明确命名和标注的 `projected schedule gap`，不能与
observed feature 共用语义或冒充历史事实。

### 8.1 Per-feature point-in-time input lineage

修复前，代码在逐场 feature 循环外对球队完整 timeline 计算一个共享 hash。
canonical 反例仅修改最后一场 `4685772` kickoff，第一场 `4685744` 的
previous-match、gap、7/14 日计数和其它业务 feature 值全部不变，但
`input_set_hash` 与包含它的 `payload_hash` 都变化。永久测试的真实 RED 为：
**1 collected / 0 passed / 1 failed / 0 skipped / 0 xfailed / 0 warnings**。

修复后纯函数 `build_feature_input_set_hash(timeline_prefix, match_by_id)`：

- 对 feature index `i` 只 hash `timeline[:i+1]`；
- stable input 包含 provider/match/team/opponent/home-away identity、
  competitive/finished/cancelled/eligible、season、kickoff 和 source status；
- 不含 observation/computation time、DB path 或未来 fixture；
- 输入若含 kickoff 晚于当前比赛的记录，显式 `PrototypeDataError`。

传播矩阵实际通过：

- 最后一场改期：前三场 input/payload hash 不变，最后一场改变；
- 第二场改期：第一场不变，第二场和所有依赖它的后续场次改变；
- 第二场变为 unfinished/cancelled：第一场不变，当前 feature 被移除，
  后续 feature hash 改变；
- observation time 单独变化：所有业务 feature/hash 不变，仅 observation
  ledger 追加。

### 8.2 Deterministic helper input contract

最终独立对抗复核确认 canonical parser 的传播边界正确，但用直接 helper
反例得到 **P0=0 / P1=1 / P2=1 / `FIX_REQUIRED`**：

- 合法排序 `[t1, t2, t3]` 与同一历史集合的
  `[t2, t1, t3]` 都被接受，却生成不同 hash；
- `[match_1, match_1]` 未拒绝；
- `[match_1, match_2, match_1]` 被误归类为 future match，而不是 duplicate；
- 两个不同 Match ID 的 kickoff 完全相同仍生成 hash；
- 文档和永久测试没有定义 helper 自身的排序/唯一性契约。

本轮先增加永久测试，旧实现真实 RED 为：
**61 collected / 6 selected / 2 passed / 4 failed / 55 deselected**，
0 skipped/xfail/warnings。随后 helper 自身改为先解析全部 Match ID，拒绝重复，
保留 existing future-current gate，再要求 kickoff 序列严格递增。它不会静默
修复非法输入；错误统一为固定、不含 payload、路径或凭证的
`PrototypeDataError`。

这只是离线 prototype 输入契约收口，不改变任何合法 canonical 业务字段或
hash 算法。production integration 仍为 **NOT STARTED**；正式
append-only/versioned mutable source-state snapshot schema 仍是下一模块。

最终验收结果：

| 命令范围 | Collected | Selected | Passed | Failed | Skipped | Xfailed | Warnings |
|---|---:|---:|---:|---:|---:|---:|---:|
| deterministic helper target | 61 | 7 | 7 | 0 | 0 | 0 | 0 |
| prototype full | 61 | 61 | 61 | 0 | 0 | 0 | 0 |
| sealed CWC pilot | 41 | 41 | 41 | 0 | 0 | 0 | 0 |
| team + competition pilots | 286 | 286 | 286 | 0 | 0 | 0 | 0 |
| migrations + backend contract | 44 | 44 | 44 | 0 | 0 | 0 | 1 |

target 的其余 54 项为 deselected，不是 skip/xfail。唯一 warning 是既有
`StarletteDeprecationWarning`。没有 warning filter。独立 canonical probe
逐一重建收口前稳定输入算法并比较全部 126 个合法 prefix，所有
`input_set_hash` 均未变化；业务数量、Manchester City 间隔、AET 和
cancelled exclusion 也全部不变。`/tmp` compileall 与
`git diff --check` 均 exit 0。

### 8.3 Safe timestamp exception boundary

deterministic-input closure 后的独立复核保留了 canonical lineage 和
observation 结论，但确认新的 **P1 / `FIX_REQUIRED`**。旧
`_parse_utc()` 在 `datetime.fromisoformat()` 的 `except` 内执行
`raise PrototypeDataError(...) from exc`；输入本身虽然没有直接进入顶层
message，却仍可从格式化 traceback、`__cause__` 和 `__context__` 恢复。
因此此前泛称“prototype 所有错误已经统一安全”的结论过早，现已撤回。本文的
安全结论只覆盖本 prototype 永久测试触达的 timestamp 路径，不推广到仓库所有
异常类型。

永久测试先于实现修改落盘。旧实现真实 RED：
**76 collected / 15 selected / 1 passed / 14 failed / 61 deselected**，
0 skipped/xfail，1 个 SeleniumBase 启动期
`PytestDeprecationWarning`。synthetic marker 矩阵包括：

- 普通非法时间；
- 绝对路径；
- proxy credential URL；
- `Authorization: Basic` / `Authorization: Bearer`；
- `token=` 和 JSON/body 形状；
- 非法 timezone、非字符串、Unicode/非法字符；
- `build_feature_input_set_hash()` 的非法 kickoff；
- 完整 canonical parser 的非法 fixture kickoff。

修复后，底层解析异常只在 `except` 内转成布尔失败状态；固定安全
`PrototypeDataError("invalid UTC timestamp") from None` 在该 `except` 已结束
且没有活动底层 parser exception 的位置抛出。实现不保留底层异常对象，不调用
`str/repr(exc)`，不记录原始 timestamp，也不把 field label 拼入消息。合法
`Z` / `+00:00` 语义不变；naive 与非 UTC offset 继续 fail closed。

永久断言逐项扫描 `str(exc)`、`repr(exc)`、`exc.args`、格式化 traceback、
cause、context、stdout、stderr 和 captured logs。所有覆盖路径均满足：

- 顶层类型固定为 `PrototypeDataError`；
- message/args 固定为 `invalid UTC timestamp`；
- synthetic marker 全表面 0 finding；
- `cause is None`；
- `context is None`；
- `suppress_context is True`。

本补丁没有改变 point-in-time hash 算法、observation ledger、canonical
fixture、prototype DDL、正式 migration、Worker 或任何 live path。

## 9. RED / GREEN 与回归

全部 pytest 使用：

- `THORDATA_PROXY=http://offline.invalid:1`
- `PYTHONDONTWRITEBYTECODE=1`
- `-p no:cacheprovider`
- `-W default`
- `socket` 的 `AF_INET/AF_INET6`、DNS、urllib、requests、curl_cffi 硬阻断
  （保留测试框架需要的本地 `AF_UNIX` socketpair）。

实际结果：

| 命令范围 | Collected | Passed | Failed | Skipped | Xfailed | Warnings |
|---|---:|---:|---:|---:|---:|---:|
| pre-implementation RED | collection error | 0 | 1 collection error | 0 | 0 | 0 |
| prototype 初次实现 | 34 | 32 | 2 | 0 | 0 | 0 |
| prototype final | 36 | 36 | 0 | 0 | 0 | 0 |
| observation closure RED | 51 | 0 | 15 | 0 | 0 | 1 |
| observation closure targeted | 51 | 15 | 0 | 0 | 0 | 1 |
| prototype closure final | 51 | 51 | 0 | 0 | 0 | 1 |
| point-in-time lineage RED | 1 | 0 | 1 | 0 | 0 | 0 |
| point-in-time lineage targeted | 55 | 5 | 0 | 0 | 0 | 0 |
| point-in-time prototype final | 55 | 55 | 0 | 0 | 0 | 0 |
| sealed CWC pilot | 41 | 41 | 0 | 0 | 0 | 0 |
| team + competition pilots | 286 | 286 | 0 | 0 | 0 | 0 |
| migrations + backend contract | 44 | 44 | 0 | 0 | 0 | 1 |

上述 closure 各独立 pytest 进程的真实 stdout/stderr 均出现 1 次当前环境
SeleniumBase pytest plugin 的启动期 `PytestDeprecationWarning`（legacy
`pytest_runtest_makereport` hook 配置）。本轮没有 warning filter；先前记录的
Starlette warning 在本轮最终命令中没有出现。表内早期 sealed CWC 和
team+competition 的 0 warnings 是前一轮历史结果；本轮复跑的真实 warning
数在本节下方 closure 命令表更新。

本轮 closure 最终命令：

| 命令范围 | Collected | Passed | Failed | Skipped | Xfailed | Warnings |
|---|---:|---:|---:|---:|---:|---:|
| observation/idempotency targeted | 51 | 15 | 0 | 0 | 0 | 1 |
| prototype full | 51 | 51 | 0 | 0 | 0 | 1 |
| sealed CWC pilot | 41 | 41 | 0 | 0 | 0 | 1 |
| team + competition pilots | 286 | 286 | 0 | 0 | 0 | 1 |
| migrations + backend contract | 44 | 44 | 0 | 0 | 0 | 1 |

本轮 point-in-time lineage closure 的最终命令：

| 命令范围 | Collected | Passed | Failed | Skipped | Xfailed | Warnings |
|---|---:|---:|---:|---:|---:|---:|
| lineage targeted | 55 | 5 | 0 | 0 | 0 | 0 |
| prototype full | 55 | 55 | 0 | 0 | 0 | 0 |
| sealed CWC pilot | 41 | 41 | 0 | 0 | 0 | 0 |
| team + competition pilots | 286 | 286 | 0 | 0 | 0 | 0 |
| migrations + backend contract | 44 | 44 | 0 | 0 | 0 | 1 |

lineage target 的其余 50 项为 deselected，不是 skip/xfail。唯一 warning 是
migrations+contract 进程中既有的 `StarletteDeprecationWarning`：FastAPI
TestClient 导入了 Starlette/httpx 的弃用兼容路径。本轮未使用 warning filter；
前一 observation closure 记录的 SeleniumBase startup warning 是历史命令
输出，不是本轮固定预期。

lineage closure 的 compileall 使用
`PYTHONPYCACHEPREFIX=$(mktemp -d /tmp/allwin-cwc-lineage-pycache.XXXXXX)`，
覆盖 prototype、三个 schedule pilot、backend 和 migration/contract 测试，
exit 0；最终 `git diff --check` exit 0。

本轮 safe timestamp closure 的当前有效命令：

| 命令范围 | Collected | Selected | Passed | Failed | Skipped | Xfailed | Warnings |
|---|---:|---:|---:|---:|---:|---:|---:|
| safe timestamp pre-fix RED | 76 | 15 | 1 | 14 | 0 | 0 | 1 |
| safe timestamp target | 76 | 15 | 15 | 0 | 0 | 0 | 1 |
| deterministic helper target | 76 | 7 | 7 | 0 | 0 | 0 | 1 |
| prototype full | 76 | 76 | 76 | 0 | 0 | 0 | 1 |
| sealed CWC pilot | 41 | 41 | 41 | 0 | 0 | 0 | 1 |
| team + competition pilots | 286 | 286 | 286 | 0 | 0 | 0 | 1 |
| migrations + backend contract | 44 | 44 | 44 | 0 | 0 | 0 | 1 |

target 之外的 61/69 项是 deselected，不是 skip/xfail。每个独立 pytest
进程的 1 warning 都是同一个 SeleniumBase legacy-hook 启动期
`PytestDeprecationWarning`，不是多个独立产品缺陷；本轮没有出现历史
Starlette warning，也没有使用 warning filter。所有命令继续硬阻断
`AF_INET/AF_INET6`、DNS、urllib、requests 和 curl_cffi，同时保留
测试框架使用的 `AF_UNIX`。

独立 canonical probe 使用与 production helper 分离的稳定 JSON/SHA-256
重算器，逐项匹配 126 个合法 input hash，并确认 66 calendar / 132 team /
63 non-cancelled / 3 cancelled / 126 rest、Manchester City
`NULL / 105 / 90 / 102`、AET 保留和 cancelled exclusion 均不变。

第一次 migrations + contract 运行误把全部 `socket.socket` 都替换为失败探针，
连 AnyIO 本地 `AF_UNIX socketpair` 也阻断，得到 29 passed / 15 harness
failures 和由破坏 event-loop 初始化产生的 warning。该命令证明 blocker
错误，不证明产品错误，因此不作为有效验收；改成只阻断网络 family 并继续
阻断 DNS/HTTP 后，44/44 通过。

`compileall` 使用本轮 `mktemp` 创建的独立
`PYTHONPYCACHEPREFIX=/tmp/allwin-cwc-observation-pycache.4vlBaz`，覆盖新
module、三个 schedule pilot 及相关 backend/migration/contract 文件，exit 0。
`git diff --check` exit 0。

## 10. Production 迁移方案与未完成项

当前 module 自包含是为了隔离证明，不是允许 production 长期 import
`analysis.*`。为了避免 competition/team pilot 与 production parser 漂移，
下一阶段应：

1. 把稳定的 identity/season/fixture normalization、eligibility 和 observed
   rest 纯函数提升到 `backend/schedules/` 单一真源；
2. 让 analysis pilots 和 production adapter 都调用该单一真源；
3. 先设计 stable match identity 与 mutable source-state snapshot 的正式
   分层，再设计 core schema/migration；
4. 用持久化 job/poll 状态实现 disabled-by-default ingestion；
5. 再做临时三库、回滚和升级路径验证；
6. 独立授权后才考虑 Worker 注册。

`prototype_match_calendar` 只证明一份已完成历史响应的不可变导入，不能原样
复制为定时生产轮询表或正式 DDL。正式 schema 的阻断设计条件明确是四层：

1. `stable_match_identity`：保存 `provider`、`provider_match_id` 和
   competition identity 等稳定身份；不存任何会变化的比赛状态。
2. `append_only_match_state_snapshot`：保存 kickoff、status、finished、
   cancelled、round、home/away（包括来源 TBD 修正）、per-match
   `payload_hash` 和 `observed_at`；来源状态每次变化追加新版本，不覆盖历史。
3. `current_match_state_projection`：从有效 snapshot 确定性选出当前状态，
   明确排序、冲突和同一事件时间多版本规则；current projection 不是历史真相
   表。
4. `versioned_feature_lineage`：每个 as-of feature 指向实际使用的
   snapshot/input set，使后来的比赛状态不能污染早期 feature，并能按观察时间
   重建历史计算。

`prototype_schedule_observation` 可以作为附加的 response-level 观察账本，但
不能替代上述任一层。正常 NS→FT、改期、取消或球队修正不得 UPDATE 覆盖历史，
也不得永久触发 immutable-key conflict；只留 response-level hash 而不能重建
单场在某次 observation 的变化，同样不合格。

因此 `PRODUCTION_MUTABLE_SNAPSHOT_SCHEMA_REQUIRED`，且该条件在本轮没有实现。

不得复活永久封存的 CWC pilot runner。production integration readiness
仍为 **NO**；下一阶段是 schema/migration + disabled job implementation，
不是 live collection。

本轮只修改离线 analysis prototype、永久测试与文档，没有新增正式 migration
或 Worker 注册。成功目标限定为：
`DESIGN_VALIDATED_WITH_TEMP_DB`、
`LATER_OBSERVATION_IDEMPOTENCY_VALIDATED`、
`OBSERVATION_LEDGER_APPEND_ONLY`、
`BUSINESS_CONTENT_IMMUTABLE`、
`POINT_IN_TIME_FEATURE_LINEAGE_VALIDATED`、
`NO_FUTURE_MATCH_IN_EARLIER_FEATURE_HASH`、
`SAFE_TIMESTAMP_ERROR_BOUNDARY_VALIDATED`。这些结果已完成最终独立复核；
prototype 阶段以
`PROTOTYPE_VALIDATED_READY_FOR_PRODUCTION_SCHEMA_DESIGN` 结束。
Production integration 仍为 **NOT STARTED**。

## 11. 完整性结论

开工/收尾比较：

- branch / HEAD：均为
  `main@cfe027283ab6318a1298d89d544eb9fa351fa713`；
- exact tag：无；stash：两端均为空；
- pre-existing dirty/untracked 资产全部保留；本 closure 只修改
  `cwc_production_integration_design.py`、其永久测试、`PLANS.md`、
  `docs/current-state.md` 和本 audit，未新增或删除 repository path；
- 排除 `.git` 后，开工/收尾 worktree pathset 均为 40,508，摘要均为
  `bda75745091446efcd9c149d24f7f79f1cb17b611ee5326246e65527efbaf4be`；
- 四主库 SHA-256 / size / mtime_ns 与开工基线逐项相同；
- 既有 WAL/SHM 集合仍只有
  `allwin.db-{wal,shm}` / `odds.db-{wal,shm}`，各自 SHA / size /
  mtime_ns 逐项相同；
- 六个 live artifact 均为 regular file、mode 0600，SHA / size /
  mtime_ns 与开工基线逐项相同；
- repository cache 两端均为 107 个 `__pycache__`、808 个 `.pyc`；
  pyc 内容摘要均为
  `c9343a3b93df844f51b588dbee0f7ec0fc9deebeee5eab8ed6fd3886e9ff229c`，
  元数据摘要均为
  `71b0d04aefc1f7adb9ec7055cc1550a74cd74ffbf261b3ad587cf4b0beb55879`；
- `.pytest_cache` 两端均存在 8 个子路径，内容摘要
  `c22e19991a816fd789f59c9cf4827e0217b0fbef7be25b9a965823b77590fb77`
  与元数据摘要
  `9d8a3c2f352bdba65dea70cd9376caa91f6fc08b058d3c5023c2c63ed15f4cbe`
  均不变；
- compileall 只写 `/tmp`；pytest 禁用 bytecode 和 cacheprovider；
- 无 commit/push/tag/deploy/stash/clean。

本轮 safe timestamp closure 另以同一 round-local probe 在开工/收尾各重算
一次；上面的旧摘要保留为历史轮次快照，不被改写成永久常量。本轮两端均为：

- branch/HEAD `main@cfe027283ab6318a1298d89d544eb9fa351fa713`，
  exact tag 无、stash 空；
- Git status pathset digest
  `b148552fa1f7b920586028e0e3c586342d51c06f2dc2dff478c63f76c4beef1d`，
  untracked pathset 41 项、digest
  `441d9054c8c388ddd51e55d7b056c55a753df09f79cd08b41839167fceeafa71`；
- 四主库与四个既有 WAL/SHM 的集合、SHA-256、size、mtime_ns 逐项相同；
- canonical fixture 仍为 25,256 bytes、mode 0644、SHA-256
  `020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1`，
  mtime_ns 不变；
- 六个历史 artifact 仍为 mode 0600，SHA-256、size、mtime_ns 逐项相同；
- cache 仍为 107 个 `__pycache__` / 808 个 `.pyc`，本轮一致的内容摘要
  `c51b8119d97ce21bbafb93cda59244efaeb3103796afb21b7d1261b43b813cf3`
  与元数据摘要
  `9d55e0e604b7a0136226d0b33e1ec4d345cb166750319d2e507bf9b8b1df88b4`；
- `.pytest_cache` 仍存在 8 个子路径，path/content/metadata 摘要依次为
  `a06f2446e530e0c375ea4835c024d0d2424c45d18640920332652cd7368b3337`、
  `8e382420a4ea80bc36a67296f2edafc955cdac1239813504c6b08a12c3bd04b5`、
  `054cebeca88dc97439f97e6ba0030f2217b71a4cdedd8e671fdd315b2b9e3391`；
- 排除 `.git` 的 worktree pathset 两端均为 40,508 项，digest
  `447097295716996ca187310e47c5d5aaa2ec96f23d426b86e3a8bc0e2e2c8568`。

数据库 / WAL / artifact / cache integrity = **PASS**。
Git/worktree authorized-delta integrity = **PASS**。
