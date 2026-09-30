# 比赛模拟器上线方案(草案,待站长确认)

> 状态:**只是方案**。本文件写成时(2026-09-30)没有对生产环境做任何改动;以下所有步骤都要站长逐项确认后才执行。
> 模型口径见 `docs/simulator-model.md`(v0.3 已启用、已知局限、前瞻复检)。

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
| release 的 Python | `/opt/allwin/current/.venv/bin/python` 3.10.12;导出脚本只依赖标准库(json/sqlite3/math…) |
| 前端路由 | `/simulator`、`/matches`、`/matches/[matchId]` 是动态(ƒ);`/`、`/about`、`/reco`、`/login` 等是静态(○),其布局(含顶栏)在构建时渲染 |
| 当前参数体积 | 英超 + 西甲一份 646 KB;五大联赛预计约 1.6 MB——**不能再整份塞进页面**(见 §3.2) |

## 1. 访问开关(保留 `SIMULATOR_ENABLED`,不删除)

### 1.1 语义改为三态(与 CLAUDE.md §7.3 认证开关同一风格)

| `SIMULATOR_ENABLED` | `/simulator` | 比赛页"模拟这场比赛"按钮 |
|---|---|---|
| 未设置 | 404(与今天一致:未上线,不暴露存在) | 不显示 |
| `0` | "模拟器暂时下线"说明页(HTTP 200,noindex,附返回首页链接),不读参数、不渲染任何模拟内容 | 不显示 |
| `1` | 正常 | 显示(另需满足 §4.1 的条件) |

- 代码改动:`frontend/app/simulator/page.tsx::enabled()` 去掉 `NODE_ENV !== "production"` 条件,改为读三态;开发环境保持今天的用法(`SIMULATOR_ENABLED=1`)。
- 为什么 `0` 不直接 404:顶栏入口在静态页面(○)里是构建时渲染的(§4.2),关掉开关后这些页面上的入口要到下一次发版才消失;用户点进来看到"暂时下线"比看到 404 好。

### 1.2 在哪里设置、如何生效

- 位置:`/opt/allwin/shared/.env`(allwin-web.service 的 `EnvironmentFile=`,release 之间共享,不进 git)。新增:
  ```
  SIMULATOR_ENABLED=1
  SIMULATOR_PARAMS_DIR=/opt/allwin/shared/exports/simulator/daily
  ```
- 两者都是**运行时**服务端变量(不带 `NEXT_PUBLIC_`,`/simulator` 是动态路由,每次请求读取),改完只需:
  ```bash
  sudo systemctl restart allwin-web
  ```
  不需要重新构建、不需要发版。
- 顶栏入口单独用一个**构建期**变量控制(见 §4.2):`NEXT_PUBLIC_SIMULATOR_NAV=1`,同样写在 `/opt/allwin/shared/.env`;release.sh 在 `npm run build` 前已 `source shared/.env`(CLAUDE.md §10.3:`NEXT_PUBLIC_*` 必须在 build 前确定),改它需要重新发版才生效。

## 2. 联赛范围:上线只开放五大联赛

v0.3 只在五大联赛(英超 47、西甲 87、意甲 55、德甲 54、法甲 53)上回测校准过。

导出脚本 `scripts/simulator/export_params.py` 需要的改动:
1. `LEAGUES = (47, 87)` → `(47, 87, 55, 54, 53)`;与回测脚本 `backtest_snapshots.py` 的 `LEAGUES` 同一组(建议两个脚本从 `params_core` 共用一个常量 `CALIBRATED_LEAGUES`,避免再次各写一份)。
2. `SEASONS` / `CURRENT_SEASON` 不变(`2025/2026` + `2026/2027`,五大联赛赛季写法一致)。
3. 导出前断言:参数里的联赛集合恰好等于 `CALIBRATED_LEAGUES`,多一个或少一个都以非零退出(不发布,见 §3.3)。
4. `calibration` 块保持不变(合并主场系数与强度回归本来就是五大联赛合并估计的)。
5. 新增 `--publish` / `--keep N` 两个开关(§3.1);按 CLAUDE.md §6.3 纪律,新开关必须同时在 `tests/backend/test_worker_argv.py` 里断言 worker argv 带上了它们。

页面:
- `SimulatorClient.tsx` 的 `LEAGUE_NAME` 目前只有英超、西甲,补全五个联赛;联赛下拉只列参数里的联赛(导出已保证恰好是这五个),不写死列表。
- 链接里的 `lg` 不在这五个联赛里时,忽略该链接的设定,回到默认对阵(英超 阿森纳 vs 曼城)。

## 3. 参数每日更新

### 3.1 定时导出:systemd timer + worker 任务(不用 crontab)

理由:CLAUDE.md §13 的全部周期任务都是"worker 注册任务 + 独立 systemd timer",并有 `job_runs` 全生命周期记录、单任务锁、超时、失败即 CRITICAL 告警(`backend/worker/runner.py` 失败分支调用 `backend.notify`);crontab 这几样都要另写一套。先例:`allwin-digest.timer` / `allwin-standings.timer` 都是"注册在 worker 里、不进 DEFAULT_CHAIN、自己一个 timer"。

- worker 任务 `simulator_params_export`(`kind: subprocess`,加入 `NON_CHAIN_JOBS` 白名单,`tests/backend/test_job_order.py` 同步):
  ```
  argv: [.venv/bin/python, scripts/simulator/export_params.py,
         --data-dir /opt/allwin/shared/data,
         --out-dir  /opt/allwin/shared/exports/simulator/daily,
         --publish --keep 7]
  timeout_seconds: 900, max_attempts: 1
  ```
- `deploy/systemd/allwin-simparams.timer`:`OnCalendar=*-*-* 05:30:00 Asia/Shanghai`,`Persistent=true`。
  05:30 的理由:欧洲夜场比赛(北京时间凌晨 3–5 点结束)的赛后数据由 `allwin-postmatch.timer`(每 30 分钟)补全后再导出;避开 04:00 的 `allwin-maintenance` 与 03:07 左右的每日备份。
- `deploy/systemd/allwin-simparams.service`:与 `allwin-digest.service` 同构——
  - `User=allwin`、`Group=allwin`(不以 root 运行);
  - `EnvironmentFile=/opt/allwin/shared/.env`、`WorkingDirectory=/opt/allwin/current`;
  - `ExecStart=/usr/bin/nice -n 19 /usr/bin/ionice -c3 /opt/allwin/current/.venv/bin/python -m backend.worker.runner --job simulator_params_export --key "$(TZ=Asia/Shanghai date +%%Y-%%m-%%d)"`(`--key` 按北京日期幂等,Persistent 补跑与正点触发同一天只导出一次);
  - `ReadWritePaths=/opt/allwin/shared/data /opt/allwin/shared/exports/simulator`(`data` 只为写 `job_runs`;导出本身用 `open_ro`:SQLite `mode=ro` + `query_only`,与今天手动导出相同);
  - `ProtectSystem=full`、`NoNewPrivileges=true`、`PrivateTmp=true`、`UMask=0022`(导出文件要让 allwin-web 读;两者同为 allwin 用户,0077 也可读,取 0022 只是便于人工排查)。
- 输出与保留(`--publish --keep 7`):
  1. 写到 `daily/.tmp-<时间戳>.json`;
  2. 校验:JSON 可解析;五个联赛都在;每个联赛球队数 ≥ 18、每队 `last_lineup` 与球员参数齐全的球队 ≥ 90%;`calibration.version == meta.effective_version`;文件大小在 0.5–10 MB;
  3. 校验通过 → 原子改名为 `daily/simulator_params_YYYYMMDD.json`,再原子替换软链 `daily/current.json`(`ln -sfn` 到临时名再 `mv -T`);
  4. 删除最旧的,只保留最近 7 份(`current.json` 指向的那份永不删除);
  5. 任何一步失败:删除临时文件,**不动** `current.json`,非零退出 → `job_runs` 记 failed → CRITICAL 告警。
- 首次上线时顺带清理:`backtest/`、`forward_2026-2027/` 与 `refactor_check_*`、`enable_v03_*` 等一次性文件移出 `exports/simulator/`(回测快照已拉回本地 `.local-data`;每月前瞻复检脚本会按需重新生成 `forward_2026-2027/`)。清理前列清单给站长确认。

### 3.2 生产页面如何读取最新参数(以及体积)

- 服务端读取顺序:`$SIMULATOR_PARAMS_DIR/current.json` → 解析失败或校验不过时,按文件名日期从新到旧依次尝试 `simulator_params_*.json` → 全部失败时页面显示"模拟器参数暂不可用,请稍后再试"(HTTP 200,不抛异常、不进错误边界),同时服务端打一条 `console.error`(进 `journalctl -u allwin-web`)。
- 缓存:进程内按(文件路径, mtime)缓存解析结果,1.6 MB 的 JSON 不在每次请求时重新解析;`current.json` 被替换后 mtime 变化,下一次请求自动换新。
- **按联赛下发**:页面只把"当前联赛"的球队、球员、赛程,加上共用的 meta / calibration / 五个联赛的联赛级参数 / 阵型模板 / 位置映射传给客户端(约为整份的 1/5);切换联赛改为跳转 `/simulator?lg=<id>`,由服务端重新取那一个联赛的切片。不新增任何 API 路由(CLAUDE.md §3.1 / §10.3:不另起一套 JSON API)。"有 Crown 盘口的真实比赛"列表另给一个五个联赛合并的小索引(比赛 id、两队中文名、盘口线),点其它联赛的比赛同样跳转。
- 参数日期:页头已显示"参数导出于 …";距今超过 48 小时时,在页头加一句"参数不是最新的(上次更新 …)"。
- 分享链接里的参数日期与当前不同 → 已有的"参数已更新"提示与"用最新参数重新模拟"按钮(Phase 5)。

### 3.3 导出失败如何发现

- `job_runs` 记 failed,runner 失败分支立即发 CRITICAL(ServerChan,`dedup_key=worker:simulator_params_export`,与其它任务相同的去重);
- 每日 23:30 的管道日报"失败任务(24h)"会列出它;
- 日志:`journalctl -u allwin-simparams.service`(导出脚本的 JSON 摘要与报错都在这里);页面侧读取失败在 `journalctl -u allwin-web`;
- 新鲜度兜底:`pipeline_gates` 加一条质量门——`daily/current.json` 的 `meta.generated_at` 早于 36 小时即 WARN(覆盖"timer 没触发 / 被禁用"这种 job_runs 里根本没有失败记录的情况)。

## 4. 入口(先写方案,确认后再实现)

### 4.1 比赛页"模拟这场比赛"

- 位置:`frontend/components/matches/MatchHeaderPre.tsx` 头部卡片内、开球时间行下方,新增一行一个按钮(新 class,例如 `.simulateRow` / `.simulateBtn`,样式沿用全站次要按钮的规格)。
- 显示条件(服务端判断,比赛页是动态路由,开关关掉立即生效):`SIMULATOR_ENABLED === "1"` 且 联赛 ∈ 五大联赛 且 比赛未开赛(`status` 为未开赛且有精确 `kickoff_at_utc` 且开球时间在未来);已完赛比赛用的是 `MatchHeaderFinished`,不加。
- 链接:`/simulator?lg=<league_id>&h=<home_team_id>&a=<away_team_id>&fx=<match_id>`(FotMob team id 与参数里的球队 id 同源)。模拟器解析到"只有两队、没有首发"的轻量链接时,两队自动用各自最近一场首发(`lastLineupSetup`),有 `fx` 且参数里有这场的 Crown 盘口时默认用盘口定锚。需要扩展 `shareLink.ts::parseSetupQuery` 接受这种轻量形式,完整分享链接格式不变。
- 某队在参数里不存在(例如升班马数据不足)时,模拟器提示"该队暂无参数",不报错。

### 4.2 桌面端顶部导航"模拟器"

- 改动:`frontend/components/SiteNav.tsx` 的 `NAV_ITEMS` 追加 `{ href: "/simulator", label: "模拟器", mobile: false }`,只在 `process.env.NEXT_PUBLIC_SIMULATOR_NAV === "1"` 时加入。`mobile: false` 使它在手机顶栏隐藏;手机底部导航(同文件 `BottomNavLinks`,独立的 5 项列表)不改。
- 为什么用构建期变量:顶栏在 `/about`、`/reco`、`/login` 等静态页面里是构建时渲染的,运行时变量在这些页面上不会生效;所以顶栏入口跟着发版走,页面本身跟着运行时开关走(§1.1 的 `0` = 下线说明页兜住)。
- 风险:`NAV_ITEMS` 注释写明 `.nav` 是 `overflow-x:auto`,第 8 项在窄桌面会被推进横向滚动区。目前 6 项,加上后 7 项;实现后在 1024 px 与 1280 px 两个宽度截图确认"模拟器"不进滚动区。
- 顺序:放在"每日精选"之后、"关于我们"之前。

### 4.3 会动到的现有组件,以及不改动的承诺

| 文件 | 改动 | 已有元素 |
|---|---|---|
| `components/SiteNav.tsx` | `NAV_ITEMS` 按构建期变量追加一项 | 已有链接的 class(`styles.link` / `styles.active`)、字号、间距、手机底部导航全部不动;不改 `SiteNav.module.css` 已有规则 |
| `components/matches/MatchHeaderPre.tsx` | 新增一行按钮 | `.card`、`.metaRow`、`.matchup`、`.kickoffRow` 等已有元素的 class、字号、间距不动;新样式只加新 class |
| `components/matches/MatchHeaderPre.module.css` | 只新增 class | 不改已有规则 |
| `components/matches/MatchDetailBody.tsx` | 如需把开关状态传给头部组件,只加一个 prop | 不动其它 |
| `app/matches/[matchId]/page.tsx` | 读取运行时开关并往下传 | 不动其它 |

实现后按 CLAUDE.md §11.5:打开页面实际比对,桌面与手机(375 px)各截一张改动前后对照图。

## 5. 页面状态标注

- 去掉"位置未校验":**前提是站长完成人工核对**。核对材料:`scripts/simulator/position_check.py` 的输出(规则解码的位置与球员主要位置对照;上次在英超 + 西甲上扣除预期内不一致后为 95.8%,不一致类别已知,如 4-4-2 / 5-4-1 / 4-4-1-1 的边路格子)。五大联赛上线前需在服务器上重跑一次,把意甲、德甲、法甲的结果一并交给站长核对。核对通过后删除:阵容池每行的"(位置未校验)"、球场槽位的提示文字、导出 `meta.position_decoding` 里的 `unverified` 字样与 `position_unverified` 标志;规格 `docs/simulator-model.md` 同步注明核对日期与结论。未核对完之前保持原样。
- "原型"改为"测试版":页头徽标文字与 `<title>`(`比赛模拟器(测试版)`)同步改;说明文字保留现在的校准范围描述("进球率、主场系数与时段系数经 25/26 五大联赛回测校准;侧重点乘数未校准。结果不代表预测")。
- `robots`:测试版期间建议继续 `noindex`、不进 sitemap(待站长决定,见 §8)。

## 6. 发版与核验

### 6.1 发版前(开发机)

1. 实现 §1–§5 已确认的部分;`pytest`、`vitest`、`tsc`、`eslint`、`npm run build` 全绿;本地用生产构建(`next start`)+ `SIMULATOR_ENABLED` 三态各跑一遍。
2. commit、push 到 main;确认 main 上除模拟器以外没有其它未上线改动(发版前再跑一次 `git diff --stat <线上 sha>..origin/main`,有就先报告,不发)。

### 6.2 服务器(按 CLAUDE.md §14.2)

1. 同步 source:
   ```bash
   cd /opt/allwin/source && git fetch origin && git merge --ff-only origin/main
   ```
2. 安装新的 systemd 单元(人工确认,不自动化):
   ```bash
   sudo cp deploy/systemd/allwin-simparams.service deploy/systemd/allwin-simparams.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
   (此时先**不** enable timer。)
3. 设置 `/opt/allwin/shared/.env`:先加 `SIMULATOR_PARAMS_DIR=…` 与 `NEXT_PUBLIC_SIMULATOR_NAV=1`,**`SIMULATOR_ENABLED` 暂不设**(发版后 `/simulator` 仍 404,入口按钮不显示;顶栏入口会随本次构建出现,点进去 404——所以 §6.2 第 5 步必须在发版后立即完成,或者本次发版先不设 `NEXT_PUBLIC_SIMULATOR_NAV`,开关验证通过后再发一次小版本打开顶栏入口。推荐后者)。
4. 发版:`bash deploy/scripts/release.sh`(preflight → 构建 → 备份 → migration → 候选冒烟 → 切换 → 线上验收 → 业务冒烟;任一阶段失败自动回滚)。
5. 手动跑一次导出并确认产物:
   ```bash
   sudo systemctl start allwin-simparams.service
   journalctl -u allwin-simparams.service -n 50 --no-pager
   ls -la /opt/allwin/shared/exports/simulator/daily/
   ```
   确认 `current.json` 存在且五个联赛齐全后,`sudo systemctl enable --now allwin-simparams.timer`。
6. 打开开关:`.env` 加 `SIMULATOR_ENABLED=1` → `sudo systemctl restart allwin-web`。

### 6.3 三路核验(CLAUDE.md §14.2)

```bash
# 1) systemd:进程 cwd / ExecStart 指向新 release;新 timer 已加载
systemctl show allwin-web -p WorkingDirectory -p ExecStart -p MainPID
pid=$(systemctl show allwin-web -p MainPID --value); sudo readlink -f /proc/$pid/cwd
systemctl list-timers allwin-simparams.timer --no-pager
# 2) nginx:反代目标是本机端口
sudo nginx -T | grep -n -A2 "upstream allwin_"
# 3) 域名指纹:线上真的在提供这次改动
curl -sI https://miaomiaodi.vip/simulator | grep -iE "^HTTP|cf-cache-status|cache-control"
curl -s  https://miaomiaodi.vip/simulator | grep -o "比赛模拟器(测试版)"
curl -s  https://miaomiaodi.vip/simulator | grep -o 'property="og:image"[^>]*'
```

### 6.4 线上核验清单(隐私模式,桌面 + 手机各一遍)

- [ ] `/simulator` 可访问(200),页头显示"测试版"、模型 v0.3、参数日期为当天;
- [ ] 五个联赛都能选到,并各跑一次模拟(英超、西甲、意甲、德甲、法甲),动画、结果页、xG 赛跑图正常;
- [ ] 侧重点胜率影响、拖拽排阵、切换阵型正常(Web Worker 在生产构建下能加载);
- [ ] 分享图 1080×1350、1080×1920 生成成功,中文正常,二维码扫出 `https://miaomiaodi.vip/simulator`;
- [ ] 分享链接复制 → 新隐私窗口打开 → 原样展示同一结果;手机微信内打开同一链接(**微信卡片预览**:转发 `/simulator` 页面链接看是否出现标题、描述、缩略图——微信不保证读取 og 标签,结果如实记录);
- [ ] 比赛页入口:五大联赛一场未开赛比赛有"模拟这场比赛"按钮,点进去两队与首发已带好;非五大联赛比赛、已完赛比赛没有按钮;
- [ ] 桌面顶栏"模拟器"入口(若本次打开),手机底部导航无变化;
- [ ] `SIMULATOR_ENABLED=0` 演练一次:重启后 `/simulator` 显示"暂时下线",比赛页按钮消失;再改回 `1`。
- [ ] 日志无新增错误:`journalctl -u allwin-web --since "-30 min" | grep -iE "error|⨯"`。

### 6.5 Cloudflare Purge

顶栏入口改动会影响全站 HTML(静态页面可能被 Cloudflare 缓存);发版并核验后在 Cloudflare 控制台 **Purge Everything**,再用隐私模式复核首页顶栏与 `/simulator`。`/simulator` 本身是动态路由(`private, no-store`,不进共享缓存)。Purge 由站长操作。

## 7. 回滚

### 7.1 只下线模拟器(首选,1 分钟内)

```bash
sudoedit /opt/allwin/shared/.env        # SIMULATOR_ENABLED=1 改为 0
sudo systemctl restart allwin-web
curl -s https://miaomiaodi.vip/simulator | grep -o "暂时下线"
```
- 效果:`/simulator` 立即变成"暂时下线"说明页;比赛页按钮立即消失(动态路由);静态页面顶栏的"模拟器"入口要到下一次发版(构建时 `NEXT_PUBLIC_SIMULATOR_NAV` 不设)才消失,期间点进来看到的是下线说明页。
- 参数导出可以继续跑,也可以停:`sudo systemctl disable --now allwin-simparams.timer`。
- 要彻底回到"不存在"状态:删掉 `SIMULATOR_ENABLED` 这一行(→ 404),并按 §7.2 发一个不含顶栏入口的版本。

### 7.2 整体回滚到上一个 release

- 发版过程中任一阶段失败,release.sh 自动回滚到上一 release 并重新验收。
- 发版成功后才发现问题、需要整体回退代码:
  ```bash
  ls -1dt /opt/allwin/releases/*       # 找到上一版(当前为 9698471dc097)
  sudo ln -sfn /opt/allwin/releases/<上一版> /opt/allwin/current.tmp && sudo mv -T /opt/allwin/current.tmp /opt/allwin/current
  sudo systemctl restart allwin-web allwin-api
  ```
  然后做 §6.3 的三路核验与 Cloudflare Purge。(`KEEP_RELEASES=3`,上一版目录在保留范围内。)本次发版不含数据库 migration,代码回滚不涉及数据回滚。
- 回滚后 `allwin-simparams.timer` 若已启用,`disable --now` 停掉(上一版代码里没有这个 worker 任务,会失败并告警)。

## 8. 待站长决定

1. §1.1 的三态语义(未设置 = 404,`0` = 下线说明页)是否同意。
2. §3.2 按联赛下发参数(切换联赛 = 跳转 `?lg=`)是否同意——这会改变联赛下拉的交互(整页刷新而非页内切换)。
3. 顶栏入口是否按 §6.2 第 3 步"先发版验证、再发小版本打开顶栏"分两次上线。
4. `/simulator` 测试版期间是否继续 `noindex`、不进 sitemap。
5. §5 位置人工核对的时间;核对完成前页面保留"位置未校验"。
6. §3.1 首次上线时清理 `exports/simulator/` 下的回测文件(清单另行确认)。
