# 春秋直播公开实时比分 Provider 运行手册

## 用途

该命令从春秋直播官网匿名页面实际使用的公开足球接口获取一次当前比分快照，
可选再观察一段 WebSocket 增量。当前只写 gitignored artifact，不写
`data/allwin.db`、`data/platform.db`、`data/odds.db` 或
`data/verify_leagues.db`。

## 运行

只抓一次 REST 全量快照：

```bash
PYTHONDONTWRITEBYTECODE=1 \
python -m backend.cli.kbisai_live_scores
```

抓快照后观察 10 秒比分/状态增量：

```bash
PYTHONDONTWRITEBYTECODE=1 \
python -m backend.cli.kbisai_live_scores \
  --listen-seconds 10 \
  --max-events 20
```

自定义输出目录：

```bash
python -m backend.cli.kbisai_live_scores \
  --output-dir /var/lib/allwin/research/kbisai-live-scores
```

## 产物

每次成功运行建立独立 run 目录，目录 0700、文件 0600：

- `realtime-match-list.pb`：来源原始 Protobuf；
- `matches.json`：标准化比赛快照；
- `updates.json`：观察期内 type=12/13 增量；
- `summary.json`：请求时间、SHA、赛事/球队/比赛/进行中数量和 WS 状态。

`ws_status=OBSERVED` 且 `ws_update_count=0` 表示连接和订阅成功、观察期没有
比分/状态变化，不代表连接失败。可选 WebSocket 失败时 REST 成功快照仍保留，
`ws_status=UNAVAILABLE`。

## 安全与运营规则

- 不设置、读取或提供 `KBISAI_TOKEN/COOKIE/USERNAME/PASSWORD`；
- 不加载仓库 `.env`，HTTP transport 不继承环境代理；
- 不增加 Cookie、Authorization 或账号身份；
- 不请求主播、聊天室、赔率、推荐或付费接口；
- 单次 WebSocket 观察最长 55 秒、最多 100 条；代码不自动重连；
- 生产定时任务尚未实现。上线前必须另行确定频率、来源许可、失败退避和缓存；
- `provider_clock_reference` 不能展示为比赛分钟，直至完成状态逐项语义验证。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPYCACHEPREFIX="$(mktemp -d /tmp/allwin-kbisai-pycache.XXXXXX)" \
python -m pytest -p no:cacheprovider -W default -q \
  tests/backend/test_kbisai_live_scores.py
```

