# Kbisai 公开实时比分 Provider v1 审计

日期：2026-07-31（Asia/Shanghai）

## 结论

`KBISAI_PUBLIC_LIVE_SCORE_PROVIDER_VALIDATED`

已完成匿名公开 REST 快照、Protobuf 解码、canonical 归一、公开 WebSocket
握手/订阅、有限观察、私有 artifact 和永久离线测试。没有接入生产数据库、
Worker、systemd、API、frontend 或 AWS。

## 公开前端契约证据

官网公开 Web bundle 的 Axios client 固定 `scope=web`、随机 `webID`、
`bk=w<uuid>` 和毫秒 timestamp。实时足球列表函数调用：

```text
POST https://kbisailive.com/api/v1/football/realtimeMatch_b
responseType=arraybuffer
```

返回 `application/x-protobuf`，消息为 `MatchListResp`：

- field 1：`CompetitionInfo[]`
- field 2：`TeamInfo[]`
- field 3：`LotteryInfo[]`
- field 4：`MatchInfo[]`
- field 5：业务 code

`MatchInfo` 使用 `IntItemList`/`StringItemList` 压缩公共字段，前端公开构造器
将整数数组依次解释为 match、competition、home team、away team、match time、
status 等字段，并使用 score 数组索引 0 显示当前总比分。

公开 WebSocket 地址为 `wss://kbisailive.com/ws/match`。匿名握手参数是公开的
`scope=web`、Unix 秒、`MD5(scope+timestamp)` 再 JSON/Base64；它不包含账号
secret。客户端订阅 type=10，足球比分/状态增量为 type=12/13。

## 实现边界

`backend/providers/kbisai_live_scores.py`：

- 使用有明确字节、字段、嵌套和字符串预算的本地 Protobuf reader；
- 未知 wire type、截断、超大输入、重复 match/team/competition、未知实体引用、
  未知 status、比分数组不一致均 fail closed；
- 只保留赛事、球队、比赛、比分和状态；主播/房间/聊天内容直接跳过；
- HTTP 使用 `httpx.Client(trust_env=False)`，无 Cookie/Authorization；
- 网络、协议异常均转为固定项目异常，不携带 URL、body、header、底层异常或
  cause/context；
- WebSocket 是显式、最长 55 秒、无自动重连的有限观察；
- optional WebSocket 失败不会丢弃已验证的 REST 快照。

真实样本证明整数数组第 10 项是 epoch-like 值（例如 1785447233），不是分钟。
实现因此输出 `provider_clock_reference` 与
`clock_semantics=UNVERIFIED`，没有制造荒谬的“第 1785447233 分钟”。

## 真实运行

最终 run：

```text
run_id = 20260730T221825432063-ca8fe5ca03dc
observed_at = 2026-07-30T22:18:25.432063Z
REST status = 200
content-type = application/x-protobuf
raw bytes = 56331
raw sha256 = 00f103bfb6b0946fd341b90c9dd68b5036f790cca8087c34da94b7f166b8687b
competitions = 105
teams = 686
matches = 344
in play = 14
WebSocket = OBSERVED
WebSocket observation = 10 seconds
type 12/13 updates = 0
```

快照中其它状态为：`FINISHED=160`、`NOT_STARTED=162`、`OTHER=8`。进行中
样本包含厄瓜乙、哥伦杯、巴拉甲等公开比赛；主客队名、当前比分、status ID
和开球时间均由同一 Protobuf 响应解析。

探索阶段合计 6 次 REST transport attempt：3 次成功，3 次因过窄
`Accept: application/octet-stream` 得到 HTTP 406 的真实 RED；修复为浏览器兼容
的 Protobuf+通配 Accept 后稳定成功。WebSocket 共 3 次连接，均未自动重试；
最终两次完成 type=10 订阅，10 秒窗口内没有实际比分变化。

产物位于：

```text
runtime/research/kbisai-live-scores/
  20260730T221825432063-ca8fe5ca03dc/
```

目录为 0700，四个文件均为 0600。该目录 gitignored。

## 永久测试

`tests/backend/test_kbisai_live_scores.py` 覆盖：

- REST Protobuf 各消息及 packed int/string；
- canonical identity、UTC、状态、比分、彩票 ID 与 `source_updated_at=NULL`；
- 截断、超大、未知 wire type、非零 code；
- 重复 team/match、未知实体、未知 status、比分长度不一致；
- 匿名直连 header、`trust_env=False`、HTTP/content-type/size gate；
- transport marker 不进入异常任何表面，cause/context 均为 None；
- WebSocket 公开签名、订阅编码、ScoreObj type=12/13 解析；
- 非比分事件忽略、有限观察；
- 0600 artifact 与 WS 失败时的 REST 保留。

最终定向结果为 23 passed；唯一 warning 是仓库既有
Starlette/httpx deprecation。

## 完整性

四个真实 SQLite 主文件保持既有 SHA-256：

- `allwin.db`: `92a6a39c...ab364e`
- `platform.db`: `c21e7008...8ea2d`
- `odds.db`: `cdc5fd54...15e093`
- `verify_leagues.db`: `603163b5...0a19c0`

两个 WAL 仍为 0 bytes；两个 SHM 内容 SHA 均保持
`fd4c9fda...9389eb`。没有新增数据库 sidecar，未执行真实 migration。

严格 cache integrity 为 **FAIL**。本轮最终验收时间点在 `.venv` 的
requests/urllib3/charset_normalizer 下创建 6 个 `__pycache__` 目录和 54 个
`.pyc`，使此前记录的 109/813 变为 115/867。没有新增 source-tree pyc，也没有
删除、touch 或伪造恢复这些文件。产品代码、真实数据和 artifact 结论不受影响，
但不得把本轮写成完整性 PASS。

## 未完成与上线条件

- 公开接口稳定性、许可、缓存、署名和商业再分发条款仍需运营确认；
- statusId 2–7 的精确半场/加时文案和 clock reference 分钟换算未验证；
- 未实现 durable polling、hash-diff、source_health、跨源 match xref；
- 未写真实数据库，未接 API/frontend；
- 未安装 systemd，未部署 AWS。

因此本轮完成的是“获取并标准化公开实时比分数据”，不是生产实时比分服务上线。
