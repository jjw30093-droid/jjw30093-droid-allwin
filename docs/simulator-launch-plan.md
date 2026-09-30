# 比赛模拟器上线方案

> 状态:**方案已确认(2026-09-30,站长)**,分两次发版。第一次发版(软上线)的代码在本地实现并通过测试后,发版仍需站长逐次确认;
> 本文件写成与修订时没有对生产环境做任何改动(服务器上只做了只读查询)。模型口径见 `docs/simulator-model.md`。

## 0. 现状审计(2026-09-30,只读)

| 项 | 实测 |
|---|---|
| 生产线上 release | `/opt/allwin/current → releases/9698471dc097`(XgRaceChart 修复那一版) |
| 服务器 source 检出 | `6a1712e`(只为跑导出/回测脚本 ff 过,未发版) |
| main 相对线上的非模拟器改动 | `docs/current-state.md`、`frontend/package.json` / `package-lock.json`(新增 `@dnd-kit/core`)、`frontend/public/brand/simulator-share-*.png`;`.claude/launch.json` 被 release.sh 排除,不进 release。其余全部是 `/simulator` 专属文件 |
| `/opt/allwin/shared/.env` | 没有任何 `SIMULATOR_*` 变量 |
| 生产 `/simulator` | 404(页面门禁要求 `NODE_ENV !== "production"`,生产固定 `NODE_ENV=production`) |
| 参数导出目录 | `/opt/allwin/shared/exports/simulator/`(allwin:allwin),81 MB,绝大部分是 Phase 2 回测快照 `backtest/` 与 `forward_2026-2027/` |
| 磁盘 | `/` 58G,已用 43% |
| release 的 Python | `/opt/allwin/current/.venv/bin/python` 3.10.12;导出脚本只依赖标准库 |
| 前端路由 | `/simulator`、`/matches`、`/matches/[matchId]` 是动态(ƒ);`/`、`/about`、`/reco`、`/login` 等是静态(○),其布局(含顶栏)在构建时渲染 |
| 当前参数体积 | 英超 + 西甲一份 646 KB;五大联赛约 1.6 MB——**不能再整份塞进页面**(见 §3.2) |
| worker 任务之间的依赖 | 不存在跨定时器的依赖机制:7 个定时器各跑各的;`group_runner` 组内任务互相独立(失败不阻止后续);只有人工 `--chain` 有"上游失败则跳过"。CLAUDE.md §13 要求 7 个定时器的任务集合恰好等于 `DEFAULT_CHAIN`(`tests/backend/test_job_order.py` 校验),所以不能把导出塞进 `postmatch` 组 |

## 两次发版

| | 第一次(软上线) | 第二次(正式上线) |
|---|---|---|
| 内容 | 新开关逻辑(三态)、按联赛加载参数、五大联赛范围、每日参数导出任务 + 新鲜度质量门、`noindex` / canonical | 比赛页"模拟这场比赛"按钮、桌面端顶栏"模拟器"入口、"原型"改为"测试版" |
| 入口 | 无(不加顶栏、不加比赛页按钮;只能直接访问 `/simulator`) | 顶栏 + 比赛页按钮 |
| 开关 | 发版后由站长在生产把 `SIMULATOR_ENABLED` 设为 `1` | 不变 |
| 时机 | 本地测试通过、站长确认后 | 站长真机测试完成后 |

比赛页按钮放到第二次:按钮若跟随运行时开关,第一次发版时开关为 `1` 就会提前出现;所以第一次发版的代码里根本没有按钮。

## 1. 访问开关(保留 `SIMULATOR_ENABLED`,不删除)

### 1.1 三态(与 CLAUDE.md §7.3 认证开关同一风格,站长已同意)

| `SIMULATOR_ENABLED` | `/simulator` | 比赛页按钮(第二次发版起) |
|---|---|---|
| 未设置 | 404(未上线,不暴露存在) | 不显示 |
| `0` | "模拟器暂时下线"说明页(HTTP 200,noindex,附返回首页链接),不读参数、不渲染任何模拟内容 | 不显示 |
| `1` | 正常 | 显示(另需满足 §4.1 的条件) |

- 代码:`frontend/app/simulator/page.tsx` 去掉 `NODE_ENV !== "production"` 条件,改为只读三态;开发环境用法不变(`SIMULATOR_ENABLED=1`)。
- `0` 不直接 404 的原因:第二次发版后顶栏入口在静态页面(○)里是构建时渲染的,关掉开关后这些页面上的入口要到下一次发版才消失;用户点进来看到"暂时下线"比 404 好。

### 1.2 在哪里设置、如何生效

- 位置:`/opt/allwin/shared/.env`(allwin-web.service 的 `EnvironmentFile=`,release 之间共享,不进 git)。第一次发版前新增:
  ```
  SIMULATOR_PARAMS_DIR=/opt/allwin/shared/exports/simulator/daily
  ```
  发版并核验后,由站长再加:
  ```
  SIMULATOR_ENABLED=1
  ```
- 两者都是**运行时**服务端变量(不带 `NEXT_PUBLIC_`;`/simulator` 是动态路由,每次请求读取),改完执行 `sudo systemctl restart allwin-web` 即生效,不需要重新构建、不需要发版。
- 第二次发版的顶栏入口用**构建期**变量 `NEXT_PUBLIC_SIMULATOR_NAV=1`(同样写在 `/opt/allwin/shared/.env`;release.sh 在 `npm run build` 前 `source shared/.env`,CLAUDE.md §10.3),第一次发版时不设。

## 2. 联赛范围:只开放五大联赛

v0.3 只在五大联赛(英超 47、西甲 87、意甲 55、德甲 54、法甲 53)上回测校准过。

导出脚本:
1. `scripts/simulator/params_core.py` 新增共用常量 `CALIBRATED_LEAGUES = (47, 87, 55, 54, 53)`,`export_params.py` 与 `backtest_snapshots.py` 都从这里取(不再各写一份)。
2. `SEASONS` / `CURRENT_SEASON` 不变(`2025/2026` + `2026/2027`)。
3. 发布前断言参数里的联赛集合恰好等于 `CALIBRATED_LEAGUES`,多一个或少一个都不发布(§3.1)。
4. `calibration` 块不变。

页面:联赛下拉只列 `CALIBRATED_LEAGUES` 与参数里联赛的交集;链接里的 `lg` 不在五大联赛里时回到默认对阵(英超 阿森纳 vs 曼城)。

## 3. 参数每日更新

### 3.1 触发方式:采集完成即导出(就绪检查),不用固定时刻

**为什么不用固定 05:30**:最近 7 个有五大联赛比赛的夜晚,每晚最后一场比赛(北京时间 03:00/03:30 开球)的明细入库完成时间如下(`job_runs` 里 `fotmob_incremental_multi` 成功运行日志中的 `match_id=… 落库完成`;日志只保留尾部,部分场次的入库行被截断,表中是能找到记录的场次里最晚的一场,属于下限):

| 比赛夜(北京) | 五大联赛场次 | 最后开球 | 最后一场入库完成 | 开球 → 入库 |
|---|---|---|---|---|
| 09-14 | 5 | 09-15 03:00 | 09-15 05:42 | 2h42m |
| 09-15 | 3 | 09-16 03:30 | 09-16 06:20 | 2h50m |
| 09-16 | 3 | 09-17 03:30 | 09-17 06:29 | 2h59m |
| 09-17 | 2 | 09-18 03:30 | 09-18 06:09 | 2h39m |
| 09-18 | 5 | 09-19 03:00 | 09-19 05:50 | 2h50m |
| 09-19 | 23 | 09-20 03:00 | ≥ 09-20 01:59(找到 5/23 场) | — |
| 09-20 | 20 | 09-21 03:00 | ≥ 09-21 03:09(找到 7/20 场) | — |

(9 月 21 日之后是国际比赛日,没有五大联赛比赛。)欧洲晚场的最后一场通常在北京时间 06:30 前后才入库,固定 05:30 会漏掉当晚最后一轮。

**方案**:沿用 `physical_stats_poll` / `standings_refresh_poll` 的先例——worker 注册任务 + 独立定时器每 30 分钟做一次"到期判断",由数据本身判断采集是否完成:

- worker 任务 `simulator_params_export`(`kind: subprocess`,`argv: [python, -m, backend.cli.simulator_params_export, --due]`,加入 `NON_CHAIN_JOBS`;`tests/backend/test_job_order.py` 与 `tests/backend/test_worker_argv.py` 同步断言)。
- `allwin-simparams.timer`:`OnCalendar=*:05,35`(每 30 分钟,错开 postmatch 的整点/半点附近),`Persistent=true`。
- 到期判断(`--due`,全部只读):
  1. 今天(北京日期)的参数已发布(`daily/simulator_params_<北京日期>.json` 存在)→ 不到期,跳过;
  2. 北京时间早于 04:30 → 跳过(避开 04:00 的 maintenance,也避免在当晚比赛还没结束时就导出);
  3. **就绪**:最近一次 `fotmob_incremental_multi` 运行成功,且五大联赛里没有"开球已超过 2.5 小时但仍未完赛落库"的比赛(与 postmatch 同一判据 `league_stale_unresolved_match_ids`,扣除 `postmatch_retry_state` 里已耗尽重试的场次)→ 立即导出,`trigger = "ready"`;
  4. 未就绪且北京时间早于 12:00 → 跳过(等下一轮);
  5. 未就绪但已到 12:00 → 仍然导出(`trigger = "deadline"`),在 meta 里列出未就绪的场次,并发一条 WARN 告警。
- 没有比赛的日子,04:35 那一轮就会就绪并导出。

**导出结果的 meta 新增**:
- `latest_included_kickoff_utc`:参数中纳入的已完赛比赛里最晚的精确开球时间(UTC);
- `export_trigger`:`{"trigger": "ready" | "deadline" | "manual", "checked_at": …, "unresolved_match_ids": [...], "last_collection_run": {...}}`。

**systemd 单元**(与 `allwin-standings.service` 同构):
- `User=allwin`、`Group=allwin`;`EnvironmentFile=/opt/allwin/shared/.env`;`WorkingDirectory=/opt/allwin/current`;
- `ExecStart=/usr/bin/nice -n 19 /usr/bin/ionice -c3 /opt/allwin/current/.venv/bin/python -m backend.worker.runner --job simulator_params_export`;
- `ReadWritePaths=/opt/allwin/shared/data /opt/allwin/shared/exports/simulator`(`data` 只为写 `job_runs`;数据库一律 `mode=ro` + `query_only` 只读打开);
- `ProtectSystem=full`、`NoNewPrivileges=true`、`PrivateTmp=true`、`UMask=0022`、`TimeoutStartSec=900`。

**发布与保留**(导出脚本新增 `--publish --keep 7`):
1. 写到 `daily/.tmp-<时间戳>.json`;
2. 校验:JSON 可解析;五个联赛恰好齐全;每个联赛球队数 ≥ 18;有最近首发的球队 ≥ 90%;`calibration` 与 `meta.effective_version` 一致;文件大小 0.5–10 MB;
3. 通过 → 原子改名为 `daily/simulator_params_<北京日期>.json`,再原子替换软链 `daily/current.json`;
4. 只保留最近 7 份(`current.json` 指向的那份永不删除);
5. 任一步失败:删除临时文件,不动 `current.json`,非零退出 → `job_runs` 记 failed → CRITICAL 告警。

### 3.2 生产页面如何读取最新参数

- 读取顺序:`$SIMULATOR_PARAMS_DIR/current.json` → 读不到或校验不过时按文件名日期从新到旧尝试 `simulator_params_*.json` → 全部失败时页面显示"模拟器参数暂不可用,请稍后再试"(HTTP 200,不进错误边界),服务端 `console.error` 进 `journalctl -u allwin-web`。开发环境仍可用 `SIMULATOR_PARAMS_PATH` 指向单个文件。
- 进程内按(文件路径, mtime)缓存解析结果;`current.json` 被替换后下一次请求自动换新。
- **按联赛下发**(站长已同意整页刷新):页面只把当前联赛的球队、球员、赛程,加上共用的 meta / calibration / 联赛级参数 / 阵型模板 / 位置映射传给客户端;切换联赛 = 跳转 `/simulator?lg=<id>`。"有 Crown 盘口的真实比赛"列表用一个五个联赛合并的小索引,点其它联赛的比赛同样跳转。不新增任何 API 路由。
- 参数日期超过 48 小时未更新时,页头提示"参数不是最新的"。

### 3.3 导出失败如何发现

- `job_runs` 记 failed → runner 失败分支立即发 CRITICAL(ServerChan,与其它任务同一去重);
- 12:00 截止仍未就绪 → WARN(列出未就绪场次);
- 每日 23:30 管道日报"失败任务(24h)"会列出它;
- 日志:`journalctl -u allwin-simparams.service`;页面读取失败在 `journalctl -u allwin-web`;
- 新鲜度质量门:`pipeline_gates` 新增一条——设置了 `SIMULATOR_PARAMS_DIR` 时,`current.json` 的 `meta.generated_at` 早于 36 小时即 WARN(覆盖"定时器没触发 / 被禁用"这种 `job_runs` 里根本没有失败记录的情况);未设置时跳过。

## 4. 入口(第二次发版)

### 4.1 比赛页"模拟这场比赛"

- 位置:`frontend/components/matches/MatchHeaderPre.tsx` 头部卡片内、开球时间行下方新增一行按钮(新 class)。
- 显示条件(服务端判断,比赛页是动态路由):`SIMULATOR_ENABLED === "1"` 且联赛 ∈ 五大联赛 且比赛未开赛(有精确开球时间且在未来);已完赛比赛用 `MatchHeaderFinished`,不加。
- 链接:`/simulator?lg=<league_id>&h=<home_team_id>&a=<away_team_id>&fx=<match_id>`;模拟器解析到"只有两队、没有首发"的轻量链接时,两队自动用各自最近一场首发,有 `fx` 且参数里有这场的 Crown 盘口时默认盘口定锚。某队没有参数时提示"该队暂无参数"。

### 4.2 桌面端顶栏"模拟器"

- `frontend/components/SiteNav.tsx` 的 `NAV_ITEMS` 在 `NEXT_PUBLIC_SIMULATOR_NAV === "1"` 时追加 `{ href: "/simulator", label: "模拟器", mobile: false }`,放在"每日精选"之后;`mobile: false` 使手机顶栏隐藏;手机底部导航(`BottomNavLinks`)不改。
- 实现后在 1024 px 与 1280 px 截图确认新项不进 `.nav` 的横向滚动区。

### 4.3 会动到的现有组件

| 文件 | 改动 | 已有元素 |
|---|---|---|
| `components/SiteNav.tsx` | `NAV_ITEMS` 按构建期变量追加一项 | 已有链接 class、字号、间距、手机底部导航全部不动;不改 `SiteNav.module.css` 已有规则 |
| `components/matches/MatchHeaderPre.tsx` | 新增一行按钮 | 已有元素的 class、字号、间距不动 |
| `components/matches/MatchHeaderPre.module.css` | 只新增 class | 不改已有规则 |
| `components/matches/MatchDetailBody.tsx` / `app/matches/[matchId]/page.tsx` | 把运行时开关状态作为 prop 传给头部 | 不动其它 |

实现后按 CLAUDE.md §11.5 实际打开页面,桌面与 375 px 手机各截改动前后对照图。

## 5. 页面状态标注

- **位置核对不作为上线前提**;核对完成前"位置未校验"保留。
- 先补做意甲、德甲、法甲的位置自动检查,并重新生成覆盖五大联赛的分层抽样表(每联赛 16 人,按位置组分层,共 80 人),放在 `.local-data/simulator/`(不进 git),供站长人工核对。
- "原型"改为"测试版"在第二次发版;说明文字保留现在的校准范围描述。

## 6. 索引

- 测试版期间 `/simulator` 一律 `noindex`;带任何查询参数的 `/simulator?…`(分享链接、比赛页入口)**永远** `noindex`,`canonical` 指向 `/simulator`。
- `/simulator` 不进 sitemap(测试版结束后是否纳入另行决定)。

## 7. 发版与核验

### 7.1 第一次发版(软上线)

开发机:
1. `pytest`、`vitest`、`tsc`、`eslint`、`npm run build` 全绿;本地生产构建下三态各跑一遍。
2. commit、push;发版前 `git diff --stat <线上 sha>..origin/main` 确认没有模拟器以外的未上线改动,有就先报告。

服务器(CLAUDE.md §14.2):
1. `cd /opt/allwin/source && git fetch origin && git merge --ff-only origin/main`
2. 安装新单元(人工确认,不自动化),先不 enable timer:
   ```bash
   sudo cp deploy/systemd/allwin-simparams.service deploy/systemd/allwin-simparams.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
3. `/opt/allwin/shared/.env` 加 `SIMULATOR_PARAMS_DIR=/opt/allwin/shared/exports/simulator/daily`(`SIMULATOR_ENABLED` 此时不设 → `/simulator` 仍 404)。
4. `bash deploy/scripts/release.sh`(任一阶段失败自动回滚)。
5. 手动导出一次并确认:
   ```bash
   sudo -u allwin mkdir -p /opt/allwin/shared/exports/simulator/daily
   sudo systemctl start allwin-simparams.service      # 未到期时会记 skipped,可改用下一行强制导出
   sudo -u allwin sh -c 'cd /opt/allwin/current && set -a && . /opt/allwin/shared/.env && set +a && nice -n 19 .venv/bin/python -m backend.cli.simulator_params_export --force'
   journalctl -u allwin-simparams.service -n 50 --no-pager
   ls -la /opt/allwin/shared/exports/simulator/daily/
   ```
   `current.json` 存在、五个联赛齐全后:`sudo systemctl enable --now allwin-simparams.timer`。
6. 三路核验(§7.3)通过后交给站长:站长在 `.env` 加 `SIMULATOR_ENABLED=1` → `sudo systemctl restart allwin-web`。

### 7.2 第二次发版(正式上线)

站长真机测试完成后:实现 §4、§5 的"测试版"改名 → 本地全套测试 → `.env` 加 `NEXT_PUBLIC_SIMULATOR_NAV=1` → `release.sh` → 三路核验 → 线上核验清单 → Cloudflare Purge Everything(顶栏改动影响全站 HTML)。

### 7.3 三路核验(每次发版后)

```bash
systemctl show allwin-web -p WorkingDirectory -p ExecStart -p MainPID
pid=$(systemctl show allwin-web -p MainPID --value); sudo readlink -f /proc/$pid/cwd
systemctl list-timers allwin-simparams.timer --no-pager
sudo nginx -T | grep -n -A2 "upstream allwin_"
curl -sI https://miaomiaodi.vip/simulator | grep -iE "^HTTP|cf-cache-status|cache-control|x-robots"
curl -s  https://miaomiaodi.vip/simulator | grep -oE 'name="robots"[^>]*|rel="canonical"[^>]*'
```

### 7.4 线上核验清单(隐私模式,桌面 + 手机各一遍)

第一次发版(开关设为 1 之后):
- [ ] `/simulator` 200,页头模型 v0.3、参数日期为当天;`noindex` 与 canonical 正确;带参数的链接同样 `noindex`;
- [ ] 五个联赛都能选到(整页跳转),各跑一次模拟;动画、结果页、xG 赛跑图正常;
- [ ] 侧重点胜率影响、拖拽排阵、切换阵型正常(生产构建下 Web Worker 能加载);
- [ ] 分享图两种尺寸生成成功,中文正常,二维码扫出 `https://miaomiaodi.vip/simulator`;
- [ ] 分享链接新窗口打开原样展示;微信内转发 `/simulator` 看卡片预览(微信不保证读取 og 标签,如实记录);
- [ ] `SIMULATOR_ENABLED=0` 演练:重启后显示"暂时下线";再改回 `1`;
- [ ] `journalctl -u allwin-web --since "-30 min" | grep -iE "error|⨯"` 无新增错误。

第二次发版另加:
- [ ] 五大联赛未开赛比赛有"模拟这场比赛"按钮,点进去两队与首发已带好;非五大联赛、已完赛比赛没有按钮;
- [ ] 桌面顶栏"模拟器"入口;手机底部导航无变化;
- [ ] 页头"测试版"。

### 7.5 Cloudflare Purge

第一次发版只动 `/simulator`(动态路由,`private, no-store`,不进共享缓存)与新增静态图,可不 Purge;第二次发版顶栏影响全站 HTML,核验后 **Purge Everything**,隐私模式复核。Purge 由站长操作。

## 8. 回滚

### 8.1 只下线模拟器(首选,1 分钟内)

```bash
sudoedit /opt/allwin/shared/.env        # SIMULATOR_ENABLED=1 改为 0
sudo systemctl restart allwin-web
curl -s https://miaomiaodi.vip/simulator | grep -o "暂时下线"
```
- `/simulator` 立即变成"暂时下线"说明页;比赛页按钮(第二次发版起)立即消失;静态页面顶栏入口要到下一次发版(不设 `NEXT_PUBLIC_SIMULATOR_NAV`)才消失,期间点进来看到下线说明页。
- 参数导出可继续跑,也可停:`sudo systemctl disable --now allwin-simparams.timer`。
- 回到"不存在"状态:删掉 `SIMULATOR_ENABLED` 这一行(→ 404),第二次发版之后还需发一个不含入口的版本。

### 8.2 整体回滚到上一个 release

- 发版中任一阶段失败,release.sh 自动回滚并重新验收。
- 发版成功后才发现问题:
  ```bash
  ls -1dt /opt/allwin/releases/*
  sudo ln -sfn /opt/allwin/releases/<上一版> /opt/allwin/current.tmp && sudo mv -T /opt/allwin/current.tmp /opt/allwin/current
  sudo systemctl restart allwin-web allwin-api
  ```
  然后三路核验 + Cloudflare Purge。本次不含数据库 migration,代码回滚不涉及数据回滚。
- 回滚到不含 `simulator_params_export` 的版本后,`sudo systemctl disable --now allwin-simparams.timer`(否则每轮都会失败告警)。

## 9. 回测残留文件清理(待站长确认清单后再删)

服务器 `/opt/allwin/shared/exports/simulator/` 下的文件(2026-09-30 核对;"本地副本"按 sha256 逐文件比对):

| 路径 | 大小 | 本地副本 / 可否重新生成 |
|---|---|---|
| `backtest/`(181 个文件:逐日快照、outcomes、bet365_1x2、truncation_test) | 71,978,531 B | `.local-data/simulator/backtest/` 181/181 一致;也可由 `backtest_snapshots.py` 重新生成 |
| `forward_2026-2027/`(34 个文件) | 8,461,176 B | `.local-data/simulator/forward_2026-2027/` 34/34 一致;每月 `forward_check.sh` 会重新生成 |
| `enable_v03_after.json` | 645,819 B | 与 `.local-data/simulator/simulator_params_v03.json` 一致 |
| `five_leagues_check.json` | 1,506,651 B | 与 `.local-data/simulator/simulator_params_5leagues_20260930.json` 一致 |
| `enable_v03_before.json` | 645,028 B | **无本地副本**;启用 v0.3 前后对照用,结论(只差 3 项)已记录;依赖当时数据库状态,不可原样重新生成 |
| `refactor_check_new.json` / `_old.json` / `_v3.json` / `_v4.json` | 629,450 / 629,465 / 629,450 / 645,028 B | **无本地副本**;重构前后对照用,结论(0 差异)已记录;不可原样重新生成 |

待站长确认后再删;每月前瞻复检仍会在 `forward_2026-2027/` 重新写入。
