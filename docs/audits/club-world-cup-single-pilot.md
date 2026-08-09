# FIFA Club World Cup 单赛事 pilot（2025）

> 本报告只记录一次、最多三次底层 HTTP 调用的受限验证。它不是批量采集、
> production registry、Worker 接入或 endpoint 永久完整性声明。

## 1. 最终判定

- Data：**GO_SINGLE_COMPETITION_DATA_VALIDATED**
- Runner：**PERMANENTLY_SEALED**
- Production integration：**NOT STARTED**

放行范围仅为：已验证 competition ID `78` 的 2025 单赛事响应可以作为后续
独立工程设计的真实样本。以下内容仍未放行：

- 自动跟随 pagination；
- 其它赛事、其它日期或第四个 season；
- 批量抓取；
- production database、registry、Worker/systemd 接入；
- “endpoint 永不分页”或“所有 season 都有效”的永久结论。

## 2. 请求预算与执行顺序

以下是已经完成的一次性 live pilot 的历史记录。预算按
`backend.fotmob_client.cffi_requests.get()` 的实际调用计数。pilot 当时同时
固定 `FotMobClient(max_retries=1, retry_delay=0)`，并在底层 transport 前安装
`RequestBudgetGuard`。guard 不保存或输出 URL、headers、args、kwargs、proxy。

| # | 固定 operation | 参数范围 | 结果 |
|---|---|---|---|
| 1/3 | `daily_matches` | 仅 `20250619` | HTTP 响应成功，唯一发现目标比赛 |
| 2/3 | `league_matches_2025` | 仅发现的 competition ID `78` | 身份/season/schema/pagination/cross-link 通过 |
| 3/3 | `league_matches_2023` | 同一 competition ID `78` | season 对照有效 |

实际底层请求数：**3**；transport attempt：**3**。没有第四次请求，没有隐藏
retry，没有调用 `check_ip`、`match_details` 或 `team_data`。

三次授权现已全部耗尽。上表不得解释为入口仍可重放；当前
`run_live()` / `resume_live_from_saved_daily()` 均已永久封存。

### 请求 1 后的本地 gate 修正

第一次 live workflow 在请求 1 后按设计停止，状态为
`DISCOVERY_TARGET_MISMATCH`。保存响应离线复算显示它实际是唯一正确候选：
FotMob 使用简称 `Man City`，而初版永久测试只接受 `Manchester City`。这是
本地 display-name gate 过严，不是来源身份冲突。

修正先落永久测试：权威身份仍由 Team ID `8456` 决定，显示名只接受明确的
`Man City` / `Manchester City` 两种来源形状。恢复流程复用已保存、0600 的
daily JSON，把 guard 限额收紧为剩余 **2**，没有重放请求 1。最终总请求数仍
严格为 3。

### 独立复核发现的恢复重放风险与永久封存

独立零联网复核用 fake transport 对已完成 output directory 复现：

1. `resume_live_from_saved_daily()` 每次创建新的进程内
   `RequestBudgetGuard`；
2. 先进入 `client.league_matches(78, "2025")` 并消耗一次 fake transport；
3. 后进入 `_private_json_write()`；
4. 最后才因既有 `competition_78_2025.json` 的 `O_EXCL` 抛
   `FileExistsError`。

分类为 `RECOVERY_REPLAY_GUARD_NOT_DURABLE`。该反例不推翻三份保存 raw 的
数据真实性，但证明原 runner 不可复用。

本轮选择永久封存，而不是为一次性 pilot 新增 durable request ledger：

- `run_live()` 和 `resume_live_from_saved_daily()` 在 output allocation、
  artifact read/write、`FotMobClient` 构造、代理/DNS/transport 之前固定返回
  非零码及 `LIVE_RUNNER_SEALED`；
- data verdict 仍单独保留为 `GO_SINGLE_COMPETITION_DATA_VALIDATED`；
- `execute_pilot(client, tmp_path)` 只保留为注入 FakeClient 的离线 fixture
  harness，不是获准的 live 入口；
- 未来 production integration 必须在独立模块中使用持久化 job/poll 状态，
  不得复活本 pilot runner。

## 3. 定点发现

唯一查询日期：`20250619`（Asia/Shanghai）。

| 字段 | 来源值 |
|---|---|
| Match ID | `4685744` |
| home | `8456` / `Man City` |
| away | `102050` / `Wydad Casablanca` |
| competition | `78` / `FIFA Club World Cup Grp. G` |
| kickoff UTC | `2025-06-18T16:00:00Z` |
| finished / cancelled | `true / false` |

没有扫描 `20250618`、`20250620` 或其它日期。

## 4. 2025 competition response

competition endpoint 声明：

- `details.id = 78`；
- `details.name = "FIFA Club World Cup"`；
- returned/selected season = `"2025"`；
- 日期范围：`2025-06-15` ～ `2025-07-13`；
- `fixtures.allMatches` 存在、为 list、非空；
- 所有 Match ID 都是有效正整数且无重复；
- 定点 Match ID `4685744` 存在，主客 ID 与 daily response 完全一致；
- Manchester City 场次：**4**。

### 名称规范化

规则固定为：

1. Unicode NFKC；
2. casefold；
3. 标点/空白归一为 alphanumeric tokens；
4. 只删除 FotMob daily bucket 末尾的结构化 group-stage 后缀
   `Grp. <单字符>` / `Group <单字符>`。

因此 daily 的 `FIFA Club World Cup Grp. G` 与 competition 的
`FIFA Club World Cup` 判为同一名称；`FIFA Club World Cup` 与
`Club World Cup` 仍判为不同，绝不删除 `FIFA` 等语义词来凑匹配。

### 66 与理论 63 的差异

来源 `allMatches` 实际为 **66**，不是理论参考值 63：

- finished：**63**；
- non-cancelled：**63**；
- cancelled：**3**；
- Manchester City：**4**；
- Match ID duplicate：**0**。

三条额外记录是来源明确保留的、`cancelled=true` 的 León 小组赛
（每轮各一条）。pilot 没有删除这些记录来凑 63，也没有补造比赛。因此总数
结论为：

`COUNT_DIFFERS_FROM_OFFICIAL_FORMAT_REFERENCE`（66 vs 63，delta `+3`）

同时可记录：来源的 finished/non-cancelled 数为 63，与赛制参考总场次对齐；
这不能反向改写 `allMatches` 的真实总长度 66。

## 5. Pagination

2025 response：

- status：`NOT_DETECTED`
- detected evidence：空
- unresolved evidence：空

这只说明该保存响应的 `fixtures` 直接 metadata 没有已知 continuation
marker；不证明 endpoint 永远不分页。pilot 没有跟随任何 next/cursor/URL。

## 6. 2023 season 对照

同一 competition ID `78` 的 `season=2023` 响应：

- `details.id = 78`；
- `details.name = "FIFA Club World Cup"`；
- returned/selected season = `"2023"`；
- fixture count / unique Match IDs：**7 / 7**；
- 日期范围：`2023-12-12` ～ `2023-12-22`；
- 与 2025 Match ID 交集：**0**；
- pagination：`NOT_DETECTED`，无 evidence。

`verify_season_parameter_effectiveness(2025, 2023)` 结果：
**SEASON_PARAMETER_EFFECTIVE**。

该结论只适用于这两份已保存响应，不推广为所有 season 永远有效。

## 7. 永久离线门禁与回归

联网前：

- 新模块初版：35 passed；
- team + competition：286 passed；
- compileall：exit 0；
- `git diff --check`：exit 0。

请求 1 后的 display-name 修正：

- 新模块：37 passed；
- compileall / `git diff --check`：exit 0。

联网完成后、只使用保存响应：

- 新模块：**37 passed**；
- team + competition：**286 passed**；
- failed/skipped/xfailed/warnings：均为 **0**；
- compileall：exit 0；
- `git diff --check`：exit 0。

永久测试覆盖三请求预算、第四调用 transport 前阻断、无隐藏 retry、唯一/零/
多候选、identity/season/schema/empty/pagination/duplicate/cross-link、
2025/2023 三类判定、redaction、请求 2 失败时请求 3 零调用、禁止 check_ip
和禁止自动跟随 pagination。

永久封存收口的本轮离线验收：

- 新增 seal 测试在旧 runner 上：**4 failed / 37 deselected**，0
  skip/xfail/warnings；
- 修改后 seal 定向：**4 passed / 37 deselected**；
- pilot 全文件：**41 collected / 41 passed**；
- team + competition：**286 collected / 286 passed**；
- FotMob status/decode/downstream-warning：**25 collected / 25 passed**；
- 上述通过命令均 0 failed/skip/xfail/warnings；
- 本轮 network request count：**0**。

## 8. Artifact 与安全

全部 live artifact 只位于：

`/tmp/allwin-cwc-single-pilot-20260725T100615Z/`

三个 raw response、原始停止摘要、恢复摘要及离线复算摘要均为 mode `0600`。
stdout、stderr、公共摘要和 raw artifact 的 redaction 扫描均为
**0 findings / PASS**。没有输出 `.env`、THORDATA proxy URL、headers、
Authorization、proxy dict、response repr 或底层异常原文。

独立复核记录的不可变 artifact 校验信息：

| Artifact | Size | Mode | SHA-256 |
|---|---:|---:|---|
| `raw/daily_20250619.json` | 66,553 | `0600` | `32acad454a11955fef1e7f876efd8331b2488db64b4dc7bcad23bdf1538734a2` |
| `raw/competition_78_2025.json` | 858,430 | `0600` | `6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d` |
| `raw/competition_78_2023.json` | 198,744 | `0600` | `92cdc0f9a9fcffe455f05cc04da8e9c72f1f190816e6b111941b27b5a2a0637c` |
| `summary.json` | 582 | `0600` | `aa51c6791b16bbab15e240654369a1ce7f97d843ec3850422ef27d7830466e31` |
| `summary-resumed.json` | 3,127 | `0600` | `cff07bbbbbbcae626d31675c4ded1f9fa9dcb166dae7dae8074f80f7eb37197a` |
| `recomputed-summary.json` | 3,116 | `0600` | `32e2e392a4a7a7ed19086844899eebc515b917e078164a5461e57951dac152a0` |

## 9. 数据与生产边界

- 未打开或写入任何 `data/*.db`；
- 未修改现有 competition registry 或 pilot SQLite；
- 未写 calendar/team/rest；
- 未修改 `backend/fotmob_client.py`；
- 未修改 production Worker/systemd/frontend；
- 未 commit、push、tag、deploy、stash 或 clean。
- live runner 已永久封存；本轮没有读取或改写上述 `/tmp` artifact。

收尾逐项对照：

- Git 仍为 `main@cfe027283ab6318a1298d89d544eb9fa351fa713`，无 tag，
  stash 空；既有 coarse dirty pathset 不变；
- 四主库 SHA-256、size、mtime 与开工基线逐项相同；
- 四个既有 WAL/SHM 的集合、SHA-256、size、mtime 逐项相同；
- cache 开始/结束均为 107 个 `__pycache__`、808 个 pyc，内容摘要
  `9cd6b06d389483315befa6cc07c8936fb8f363a5810210245674e2771789cda7`、
  元数据摘要
  `9db30ce3fda74a4fa7844a93439698cd7f26ac39300d8dde51fb5a035d7692f3`
  均不变；`.pytest_cache` 状态/内容/元数据不变；
- worktree pathset 从 40,496 增至 40,501，新增的 5 个路径恰为获准的新
  module 目录、3 个 module 文件和本 audit；另只更新获准的 `PLANS.md` /
  `docs/current-state.md`。

Database/WAL/cache integrity = **PASS**；Git/worktree authorized-delta
integrity = **PASS**。
