# FIVE_CRITICAL_PRODUCT_FIXES_V1

状态：CORE PRODUCT FIXES VALIDATED — CURRENT INTEGRITY FAIL

日期：2026-07-30

本轮严格只处理五个直接影响移动端上线、信任和日常使用的问题。没有修改公众号
配置、支付、模型训练、历史 backfill、真实采集、AWS 或 Studio 安全字段边界。

## 1. Freshness truthfulness

公开状态不再直接信任 `content_status.json` 中一次写入的 `LIVE`。后端在每次公开
请求中使用同一个 `project_freshness()`，按最近成功时间、下一计划时间、精确
kickoff 和固定注入的当前时间投影：

- `FRESH`：数据仍在当前采集 phase 的 deadline + grace 内；
- `STALE`：曾成功，但 deadline/next planned 已逾期，或比赛已开球；
- `UNAVAILABLE`：没有可信成功时间或精确 kickoff。

集中 cadence 仍是 T-72h～T-2h 15 分钟、T-2h～开球 5 分钟；超过 72 小时沿用
低频初始观测和显式 next planned。采集失败保留上一份成功数据并强制 `STALE`。
旧 artifact 中的 `LIVE` 只作为兼容输入，不再成为公开输出。

首页、比赛列表和详情都消费相同的 MatchSummary 投影，中文文案分别是“数据已
更新”“数据等待刷新”“部分数据暂不可用”。真实 Match 5104968 的旧反例现投影为
`STALE`，页面同时显示最近成功时间、数据已过期和计划/恢复提示。

## 2. Atomic production build and preview

生产发布原本已经使用不可变 `releases/<sha>` 和 `current` 软链。本轮补齐两层
此前缺失的门禁：

- `scripts/verify_next_assets.py` 静态解析 prerendered HTML，拒绝任何不存在的
  `/_next/static` CSS/JS；运行态还会访问七个核心页面并逐个检查本地 asset 200；
- `scripts/local_preview_proxy.py` 在绑定监听端口之前先验证 frontend/backend，
  任一上游死亡就安全失败，不留下空响应监听壳；
- `scripts/build_local_preview.sh` 在独立 immutable staging release 构建，通过
  browser bundle 与 asset 门禁后才用 `os.replace()` 原子切换
  `runtime/previews/current`。旧 Next 进程的资源目录不会被新 build 覆盖；
- `deploy/scripts/release.sh` 在切 current 前执行静态 asset 门禁，切换后的业务
  smoke 再执行七页运行态 asset 门禁。

旧 `.next` 已由门禁稳定复现出 5 个 missing asset；独立 staging 新 build 的
HTML/static 集合一致。preview proxy 对双上游死亡真实返回非零，且目标端口没有
遗留监听。

## 3. Dynamic league discovery

主导航“联赛数据”现在固定指向 `/leagues`，不再写死英超 47。联赛目录由
`/api/v1/leagues` 和 core 真实行共同驱动，公开：

- 中英文名；
- 当前赛季；
- `AVAILABLE / NOT_SYNCED`；
- 当前 entitlement 是否可访问、是否需要 Pro；
- 可用时的排名、赛程、球队数据入口；
- 只有可信 acquisition 状态匹配该联赛时才显示最近更新时间。

有真实数据且可访问的联赛优先。当前隔离 Eliteserien 数据副本把挪威超 59 排为
可用入口；英超若没有 serving data 会诚实显示“暂未同步”。比赛页提供明确联赛
目录入口，手机用户两次点击内可从比赛页到挪威超积分榜。

旧联赛页不再渲染 exception body、Python 文件名、内部 code、路径或裸 league id；
错误统一退化为“该联赛数据尚未同步”或“数据暂时无法加载”。

## 4. Team display and standings

新增统一 `backend.queries.teams` 投影，优先级固定为：

1. 已有 i18n 审核名；
2. `dim_match` / `fact_league_table` 中真实 provider 名；
3. “球队名称待同步”。

`Team <ID>`、纯数字和内部 placeholder 都被拒绝。比赛列表、详情、近期状态、
V1 积分榜和 legacy 联赛页面复用同一来源。页面仍只使用本地 crest pipeline；
缺队徽由 `TeamBadge` 首字母 fallback，不会在渲染时远程下载。

真实隔离挪威超积分榜 16 队均显示审核中文名或 provider 英文名。手机积分榜直接
保留排名、球队、场次、净胜球、积分，隐藏次要列并提供横滑提示；排名和球队列为
sticky，根页面无横向溢出。`#FFD908/#02CCF0/#FFA72F/#FF4646` 等只作为视觉
颜色，图例显示欧冠资格赛、欧协联资格赛、降级附加赛、降级区，不显示 HEX。

## 5. Seven-day match discovery

`/matches` 的产品默认值是未开赛 + 未来七天。后端支持并可组合：

- `window=today|tomorrow|3d|7d|all`（today/tomorrow 按 Asia/Shanghai 日历）；
- `content=analysis|odds`；
- `q=` 中文名、provider 英文名和已审核 alias；
- status、league、date、分页。

有已发布分析或已验证真实赔率的比赛作为第一优先级；每个优先级内严格按
`kickoff_at_utc ASC, Match_ID ASC`，NULL kickoff 放最后。分页沿同一稳定顺序，
不会重复或漏场。当前真实页面中北京时间 01:00 的 5104968 位于 03:00 的
5104962 之前。

筛选全部保存在 URL。详情页提供返回原筛选、同联赛上一场/下一场和未来七天入口。
较长英文队名在手机卡片允许换行，不再裁成不可辨认的省略文本。

## 保持不变的产品边界

- 匿名 prediction 响应仍只有一个 `top_outcome/top_probability`；
- `home_probability/draw_probability/away_probability` 物理缺失；
- anonymous analysis 的 `prediction_member=null`，赔率时间线为空；
- `MARKET_BASELINE` 不改名为 MODEL；
- 一个赔率观测点不制造变化曲线；
- Studio safe-mode 字段门禁未修改。

## 完整性说明

四个受保护的 `data/*.db` 全程只读。首次本地浏览器 preview 错误地直接指向
gitignored `runtime/data`，页面已有 analytics 客户端因此向
`runtime/data/platform.db` 追加了浏览事件。该变化不会伪造恢复；发现后所有后续
preview 立即切到新建 `/tmp` 数据副本。最终完整性判定必须如实包含此事件。

## 最终实跑

- 后端相关集合：collected/selected 169，passed 169，failed 0，skipped 0，
  xfailed 0；warning summary 4（Starlette deprecation 1、既有 ResourceWarning
  3）。
- frontend typecheck：通过；ESLint：通过。
- Vitest：8 files，46 passed，failed/skipped/xfailed 0，warnings 0。
- OpenAPI drift：通过。
- production staging build：通过；11 个 prerendered HTML、32 个本地 asset
  静态一致。
- 七页运行态 asset smoke：7 pages、25 个去重 CSS/JS asset 全部 200。
- Playwright：collected/selected 3，passed 3，failed/skipped/xfailed 0；
  Node 环境 warning 1。验证 console error 0、static 404 0、四视口与深浅主题。
- `git diff --check` 与本轮新增文件 whitespace：通过。

## 最终完整性

- branch/HEAD/stash 未变；worktree 只保留既有 dirty 资产和本轮授权路径。
- `data/allwin.db`、`data/odds.db`、`data/platform.db`、
  `data/verify_leagues.db` 的 SHA/size/mtime/inode 未变；WAL 内容未变。
- `data/allwin.db-shm`、`data/odds.db-shm` 的内容/size/inode 未变，但 mtime 相对
  启动基线发生变化；不伪造恢复，也不把 metadata 变化写成 PASS。
- `runtime/data/platform.db` 因已披露的首次 preview analytics 追加而 SHA/mtime
  改变；`runtime/data/allwin.db`、`runtime/data/odds.db` 与 content status 内容
  未变。
- repository pycache 109、pyc 813、`.pytest_cache` metadata 和旧
  `frontend/.next` BUILD_ID/static pathset 均与本轮基线一致。

因此五项产品修复本身均已验证，但 `Current worktree/database integrity=FAIL`。
本报告不输出要求完整性 PASS 才允许的完成标签。
