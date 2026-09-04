# 付费独家推荐板块「每日精选」(已实现,2026-08-10)

> **本文件描述的"plan=daily_picks 整体解锁"授权模型已被 2026-08-17
> REMOVE_SITEWIDE_FREE_PREMIUM_FIELDS_AND_FIX_DAILY_PICK_PER_MATCH_ACCESS
> 取代,见 CLAUDE.md §8.2 与 docs/current-state.md §50**——每日精选现在按
> "用户 + 单条精选(reco_slip)"通过 `reco_access_grants` 表授权,不再是
> 持有 daily_picks entitlement 即可看到近 30 天全部推荐单;兑换码同样改为
> 只对应一条具体精选。本文件下方关于 plan/entitlement/`reco:daily`
> 门禁机制的描述仅作历史记录,不代表当前实现;`reco_slips`/`reco_legs`
> 表结构本身与内容编辑/结算/审计留痕相关的部分仍然准确。
>
> 状态:**已实现**。用户确认的 5 个决策:①plan=daily_picks「每日精选」,定价不写
> (products 无行,购买走公众号联系/兑换码);②战绩对齐 miaomiaodi.vip 归档口径,
> 命中/未中/走水全展示、作废单列不消失;③战绩登录即可见(reco:track_record 属
> member 基线);④固定 1 单位/单,不谈金额;⑤付费内容窗口近 30 天。
> 实现:migration platform/0010、commands/queries/routes_reco、/reco 页、
> admin「每日精选」页签、tests/backend/test_reco.py(22 项)。
> 约束前提(CLAUDE.md §9.1 适用范围修订,经用户批准):人工推荐独立建表,
> 不纳入模型预测登记簿;`prediction_snapshots` 的全部既有保护原样不动。
> 以下为原设计稿,与实现的偏差已在上方标注为准。

## 1. 产品形态

- 每日由站长人工创建 0..N 条推荐;一条推荐 = 一个"推荐单",可以是单场,
  也可以是 2串1/3串1 等串关(N 场比赛的组合)。
- 付费用户(admin grant / 兑换码发放的付费板块 plan)可见推荐内容与战绩明细;
  匿名与普通登录用户只能看到板块存在、样本量与聚合战绩(引流展示,具体选项
  与内容不下发——沿用"受限字段物理不下发"纪律)。
- 推荐内容与战绩允许管理员事后修改(与模型登记簿的核心区别),但每次修改
  写审计日志(audit_logs 已有),战绩页展示"最近编辑时间",不伪装不可改。

## 2. 新表(platform.db;命名与模型侧彻底分开,前缀 reco_)

```sql
-- 推荐单(一条可含多腿;单场就是一腿)
CREATE TABLE reco_slips (
  id            TEXT PRIMARY KEY,          -- UUID
  slip_date     TEXT NOT NULL,             -- 归属自然日(北京时间,YYYY-MM-DD,列表/战绩按日聚合)
  title         TEXT NOT NULL,             -- 如"今日三串一"
  note          TEXT,                      -- 思路说明(可空,§1 文案纪律适用)
  stake_units   REAL NOT NULL DEFAULT 1,   -- 名义单位注(战绩只按单位计,不涉真实金额建议)
  combo_type    TEXT NOT NULL,             -- single / parlay(串关)
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','published','settled','voided')),
  result        TEXT CHECK (result IN ('win','lose','push','partial')),  -- settled 后填
  return_units  REAL,                      -- 结算回报(单位),win=兑现赔率乘积×stake
  published_at  TEXT,
  settled_at    TEXT,
  created_by    TEXT NOT NULL REFERENCES users(id),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  edit_count    INTEGER NOT NULL DEFAULT 0
);

-- 腿:一场比赛上的一个选项
CREATE TABLE reco_legs (
  id            TEXT PRIMARY KEY,
  slip_id       TEXT NOT NULL REFERENCES reco_slips(id) ON DELETE CASCADE,
  match_id      INTEGER,                   -- 可空:允许引用站外赛事(如实填文本描述)
  match_desc    TEXT NOT NULL,             -- "曼城 vs 阿森纳 2026-08-15"(展示冗余,防 match 数据缺失)
  market        TEXT NOT NULL,             -- 1x2 / ah / ou / 其它(自由文本,人工内容不锁枚举)
  selection     TEXT NOT NULL,             -- "主胜" / "让-0.5 主" / "大2.5"
  odds          REAL NOT NULL CHECK (odds > 1.0),   -- 录入时的赔率
  result        TEXT CHECK (result IN ('win','lose','push')),
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
```

不建 `reco_track_record` 物化表:战绩聚合(按月/按 combo_type 分组的
胜/负/走盘计数、命中率、单位盈亏 = Σ(return_units - stake_units))样本量
在个位数到百位数级,直接查询聚合即可,避免第二份真源漂移。

## 3. Entitlement 与 plan

- 新 entitlement:`reco:daily`(当日与历史推荐内容)、`reco:track_record`(战绩明细)。
- 新 plan:`insider`(名称待定,rank 5,is_active=1),plan_entitlements =
  member 基线全部行 ∪ 两个 reco 行(物化并集,遵守 0004 约定)。
- products 行按需新增(展示价格用;无真实支付,购买路径 = 联系公众号 →
  admin grant / 兑换码,定价页如实说明)。
- 解析层零改动:`resolve_entitlements` 已实现"登录基线恒并入,订阅追加"。

## 4. API(全部挂 /api/v1;付费内容一律 private, no-store)

```text
GET  /api/v1/reco/summary          # 匿名可见:板块介绍 + 聚合战绩(样本量/命中率/盈亏单位)
                                   # 不含任何 selection/odds;PUBLIC_ALLOWLIST 候选(见 §6)
GET  /api/v1/reco/slips            # reco:daily;按日列推荐单(含腿明细)
GET  /api/v1/reco/slips/{id}       # reco:daily
GET  /api/v1/reco/track-record     # reco:track_record;逐单历史与明细
POST /api/v1/admin/reco/slips              # admin:新建(draft)
PATCH /api/v1/admin/reco/slips/{id}        # admin:编辑(edit_count+1,审计)
POST /api/v1/admin/reco/slips/{id}/publish # admin:发布
POST /api/v1/admin/reco/slips/{id}/settle  # admin:逐腿录结果 → 汇总 result/return_units
POST /api/v1/admin/reco/slips/{id}/void    # admin:作废(保留可查,不物理删除)
```

- 匿名/普通登录用户请求 slips/track-record → 403 + `{"code":"reco_membership_required"}`
  (前端据此渲染引导卡,不做假倒计时/诱导弹窗,§11.2 纪律)。
- DTO 投影:summary 的聚合数字对所有人一致(这是引流面,故意公开);
  slips/track-record 的 DTO 只在拥有对应 entitlement 时构造,受限字段物理不下发。

## 5. 与模型战绩的区分(§9.1 修订的交换条件)

- 路由分开:模型 `/track-record`(页 + API)不动;推荐板块用 `/reco`
  (页:`/reco`、`/reco/track-record`),导航文案区分"模型公开战绩"与
  "独家推荐战绩"。
- 查询分开:reco 聚合绝不进 `queries/track_record.py`,新建 `queries/reco.py`;
  评估口径(Brier/RPS 等)只属于模型侧,推荐侧只有胜负/盈亏单位统计,
  不复用、不混排。
- 可信度表述分开:推荐战绩页必须展示"人工内容,可由管理员修正,最近编辑时间"
  声明;不得使用"锁定不可改"表述(那是模型登记簿的资格)。

## 6. 缓存边界(本轮风险最高点的落地方案)

- `GET /api/v1/reco/summary` 是唯一候选进 PUBLIC_ALLOWLIST 的新路径:
  响应体只含聚合数字,任何身份看到的一致 → 可 `public, s-maxage=300`。
  进入 allowlist 需同时满足:endpoint 不读 AuthContext 做字段分叉 +
  合同测试断言响应体不含 selection/odds/leg 字段。
- 其余 reco 路径一律不进 allowlist:中间件 default-deny 已保证不在名单即强制
  `private, no-store`;付费用户请求天然带 Cookie,同样触发强制 no-store。
  两层兜底 + 合同测试三方断言(匿名 403 / 会员 no-store / 无 Cookie 且无权限 403)。
- `("GET","/api/v1/track-record")` 保持在 allowlist(模型公开战绩,语义未变)。

## 7. Admin 前端(缺口补齐)

`/admin` 增加"每日推荐"页签:草稿列表 + 新建表单(选比赛可搜 dim_match,
也可手填 match_desc)+ 发布/结算/作废操作。复用既有 admin 鉴权与 CSRF 链路,
无新权限模型。

## 8. 测试计划

- 权限矩阵:匿名/member/insider/admin(无 insider)四方 × summary/slips/track-record。
- 受限字段物理不下发:member 响应体不含 selection/odds 哨兵。
- 缓存:summary 匿名 public;slips 会员 no-store;allowlist 快照测试防误增路径。
- 结算:逐腿 win/lose/push → slip result/return_units 汇总正确;编辑留审计;
  void 后聚合分母处理(见 §9 待决策)。
- e2e:admin 建单→发布→insider 可见→结算→战绩页更新;普通登录用户看到引导卡。

## 9. 需要用户决策的点(实现前请逐项确认)

1. **plan 命名与定价展示**:付费板块 plan id/名称(暂拟 `insider`/「独家推荐」),
   定价页展示价格与购买引导文案。
2. **作废(void)的战绩口径**:作废单是否计入公开聚合分母?
   (模型侧纪律是永不出分母;人工侧允许修改,但"作废不进分母"若被滥用
   等价于删失败记录——建议:void 单仍计入"总单数"并单列"作废 N 单",
   聚合胜率分母用已结算单。请确认。)
3. **匿名聚合战绩的展示口径**:命中率/盈亏单位是否对匿名完整公开(引流 vs
   审慎),还是只公开样本量 + 模糊区间?
4. **腿数上限与 stake_units 语义**:是否固定 1 单位/单,彻底不展示金额概念
   (§1 禁止仓位建议,建议固定 1 单位并只讲"单位回报")?
5. **历史推荐可见期**:付费用户能回看全部历史,还是最近 N 天?

确认以上 5 点后,第二部分按本设计实施(预计:migration 0010 + queries/reco.py +
commands/reco.py + routes_reco.py + admin 页签 + 测试全套)。

## 10. 每日公推(board 字段,2026-09,已实现,经用户批准)

新增与「每日精选」并列的完全公开板块「每日公推」——不需要登录、不需要
`reco_access_grants` 授权,管理端操作方式与精选完全一样(建单/编辑/发布/
结算/作废),只是建单时多选一个板块。

- 数据模型:`reco_slips.board`(migration `0018_reco_board.sql`,`ALTER TABLE
  ADD COLUMN`,`NOT NULL DEFAULT 'daily_pick'`,`CHECK IN ('daily_pick',
  'daily_public')`)。默认值保证历史数据(本迁移前全部 21 条精选单)零变化。
- 读面:新增 `GET /api/v1/reco/public`(完全公开,`queries/reco.py::
  public_slips()`,近 `RECO_PUBLIC_WINDOW_DAYS=7` 天,复用既有
  `_slip_dto`/`_legs_by_slip`,不存在锁定态)。既有 `daily_slips` /
  `daily_slip_detail` / `track_record_slips` / `track_record_summary` /
  `public_overview` 全部加 `board='daily_pick'` 过滤,保证公推板块的存在
  与结算绝不污染精选已公开的数字。
- 业务约束:同一 `(match_id, market)` 不得跨板块重复推荐——只提醒不拦截
  (`queries/reco.py::cross_board_market_conflicts()`,应用层判定,DB 层
  无法表达"同板块允许、跨板块禁止"这种条件唯一性)。
- 安全阀:已发布的公推单不能改回精选(内容已公开过,收不回);draft 状态
  与"精选→公推"方向不受限。
- 授权:对公推单调用 `grant_access()` 直接拒绝(`RecoAccessError`)——完全
  公开内容不需要也不能被"授权"。
- 缓存:`GET /reco/public` 是本文件唯一进入 `PUBLIC_ALLOWLIST` 的 reco 路径,
  `PUBLIC_CACHE_SHORT`(`public, s-maxage=60, stale-while-revalidate=30`)。
- 前端:`/reco` 页(既有 client-only 组件)新增第三个 tab(`?tab=public`),
  不新建独立路由——复用既有 `SlipCard`/`LegRow`,匿名可见,继承 `/reco`
  既有的"不进 sitemap"隔离(SEO 不新增任何配置)。
- 公推板块的战绩归档本期不做(用户决策),但页面窗口内含已结算结果。
- 测试:`tests/backend/test_reco_board.py`(22 项,含两条"红线"断言——
  结算公推单前后 `/reco/track-record`/`/reco/overview` 数字逐字段不变)、
  `tests/admin-reco-tab.test.tsx` 新增板块相关用例、`tests/backend/
  test_migrations.py` 新增 0018 的 fresh_db + upgrade_from_pre_0018。

## 11. 首页公推 banner(2026-09,已实现,经用户批准)

首页「重点比赛」上方加一个 banner:有在架公推时才出现,显示**比赛 + 推荐
选项**、**不含赔率**(赔率留在 `/reco` 页),比赛开球 2 小时后撤下。

- 端点 `GET /api/v1/reco/public/current`(零 auth 依赖,双库注入
  `platform_ro` + `core_ro`,`PUBLIC_CACHE_SHORT`,进 `PUBLIC_ALLOWLIST`
  ——这是第二条、也是目前最后一条进 allowlist 的 reco 路径)。
- 查询 `backend/queries/reco.py::public_current_slips()`:
  `board='daily_public' AND status='published'`,窗口
  `RECO_PUBLIC_CURRENT_WINDOW_DAYS=2`(按**北京自然日**算 cutoff——
  `slip_date` 是站长录入的北京日,2 天窗口下 8 小时时差会真实影响边界,
  这点与 `public_slips` 的 7 天窗口不同)。开球时刻用
  `_kickoffs_for_match_ids()` 批量取(**不复用** `list_matches`:它 limit
  默认 50 会静默丢腿、`league_ids` 是硬过滤、且会多跑 COUNT/队名/队徽三次
  查询,而 banner 一个都不用)。腿投影 `_public_current_legs_by_slip()`
  在 **SQL 层就不 SELECT odds**,值从头到尾不进进程。
- **撤下判定刻意不在服务端做**:该端点走 `s-maxage=60` 共享缓存、首页又是
  ISR(`revalidate:60`),服务端算出的"该不该显示"会被烘进共享 HTML 并随
  缓存变陈旧、所有访客共用同一份陈旧判定。后端只下发 `kickoff_at_utc`
  这个**事实**,由各客户端按自己的当前时间判定
  (`frontend/lib/reco-banner.ts::visiblePublicPicks`,接收 `now` 参数、
  不读时钟,与 `physical_stats_poll.py::within_candidate_window` 同一写法)。
  服务端组件也用同一个纯函数粗过滤一次,保证 SSR 首帧不含已过期的单。
- **两条撤下规则的精度不同,如实记录**:「开球 +2h 撤下」是精确的(客户端
  每 60 秒重算,页面开着也会自动撤);「结算/作废撤下」受缓存链路限制,
  最坏约 2.5 分钟(`s-maxage=60` + ISR 60 + swr 30)。
- 串关按**最后一场**开球 +2h 撤下;任一条腿缺精确 `kickoff_at_utc` 则整单
  **不进 banner**(fail-closed:算不出何时该撤的东西不能放进一个靠时间自动
  撤下的位置;§6.2.1 也禁止对缺精确时间的比赛补零推断)。实践上几乎不会
  触发——发布要求每条腿有真实盘口溯源,而赔率采集只针对有精确开球时间的
  比赛。
- 前端三文件边界(§11.4):纯逻辑 `frontend/lib/reco-banner.ts`(**无
  `"use client"`**,服务端与客户端都 import)、`PublicPicksBanner.tsx`
  (server,取数 + 粗过滤;`Date.now()` 放在 `cache()` 包住的普通异步函数
  里而不是组件 render 体内,组件 render 必须纯)、`PublicPicksBannerLive.tsx`
  (client,精确过滤 + 渲染)。客户端初始 state 必须是 `null` 并原样渲染
  服务端列表,首帧逐字节等同 SSR——写成 `useState(() => filter(…, Date.now()))`
  会水合不匹配,且本地 dev 往往复现不出来。
- 测试:`tests/backend/test_reco_board.py::TestPublicCurrentEndpoint`(12 项,
  含"响应原始文本不得出现 odds/result/entry_type/snapshot_ref"的哨兵断言、
  精选板块不得泄漏、结算/作废即撤下、缺 kickoff 如实下发 null)、
  `frontend/tests/reco-banner.test.ts`(21 项纯函数,含恰好 2h 边界、串关取
  最后一场、缺 kickoff fail-closed、返回同一对象引用)、
  `frontend/tests/public-picks-banner.test.tsx`(11 项,含 SSR 产物水合安全、
  定时器到点自动撤下、渲染结果不含赔率)。

## 12. 首页战绩 banner(2026-09,经站长明确决定的**择优展示**)

首页最顶部(公推 banner 之上)展示推荐战绩,**从数十个统计口径候选里挑当前
最好看的一个**。站长在被明确告知这与 CLAUDE.md §2.2/§8.1 冲突后重申坚持,
并同步删除了 CLAUDE.md 的三处相关条款(原 :17 禁用词与因果表述、:37 战绩
完整性三条、:479 不挑选不隐藏),且明确选择**不补任何替代条款**。

### 与记录面的分工(最重要的一条)

- **记录面**(`/reco?tab=record`、`GET /api/v1/reco/track-record`)仍然全样本,
  命中/未中/走水/作废一条不少。本功能**不改它们的任何数字**,有红线测试守住
  (`test_reco_highlight.py::TestHighlightEndpoint::test_does_not_change_record_face_numbers`)。
- **banner**是引流位,择优展示,整块链回记录面。

### 口径定义

- **连中**:排序 `published_at ASC, id ASC`——**不用 `settled_at`**(它会被
  重结算覆盖,`commands/reco.py::settle_slip`,沿用它会导致"纠正一次 8 月旧
  结算 → 首页那个'近5单全中'静默变数");也不用 `slip_date`(只到天,同日多单
  无法定序)。`win` 延续;`lose`/`half_loss`/**`half_win`** 断连(「全中」不能
  由半赢撑起来);`push` 跳过且必须披露;`voided` 跳过且披露。必须是**当前**
  连中,不是历史最佳——挑一段历史连胜写成"近N单全中"是事实性谎言,有测试守住。
  被拒方案:按最后一场腿的 `kickoff_at_utc` 排序(语义最贴近真实先后,但
  `match_id`/`kickoff_at_utc` 均可空,缺一条腿就得 fail-open 或整单剔除,
  等于用不完整数据决定一个对外宣称的数字)。
- **命中率**:复用 `reco.py::hit_rate_from_counts`(2026-09 从
  `_summarize_result_row` 提取,算式只有一份实现)。**只统计单关**——站长决策
  "串子是串子,串子和单关分开算";这同时解决了腿/单口径冲突(单关腿==单,
  market/league 归属唯一,不存在串关混 ah+ou 的歧义)。
- **串关**:走"回报"口径(`Σreturn_units - 单数`),不算也不展示命中率。

### 选择顺序

连中(≥`MIN_STREAK`=3)→ 命中率候选 argmax。阈值 70% **只决定 kind**
(`rate_qualified` / `rate_best_effort`),不决定选谁——达标与否共用同一个
argmax,避免两套破平逻辑。比较器全序:命中率 → 样本量 → 窗口(天窗口优先于
场次窗口、宽优先)→ 分段(overall > market > league > league_market)→
`candidate_key`。破平方向刻意偏向**更少自由度、更难被挑选**的候选。

站长明确拒绝样本量下限,所以 n=1 的 100% 是可能被选中的——正因如此,展示层
强制把原始计数写出来(见下)。

### 展示约定(设计决定,不再有文档条款兜底)

**百分比永远与原始计数同现**(主行「5 单 5 中」,细行「命中率 100.0%(5 / 5)」),
每条带完整口径标签(板块+窗口+分段+单位),窗口除名义值外还给
`observed_from_date`/`observed_to_date` 实际覆盖区间(名义"近30天"会高估跨度)。
DTO 把 `hit_rate` 与 `decided_count` 放在同一个对象里,让"只渲染百分比"在类型
上就很别扭;`frontend/tests/reco-highlight.test.ts` 有正则断言守住。
**删掉那个测试,就没有任何东西阻止后人把文案简化成裸百分比。**

### 结构差异(不要照抄公推 banner)

公推 banner 需要 `"use client"` 组件是因为它有"开球+2h 精确撤下"的时间判定;
战绩 banner **没有任何时间撤下逻辑**,内容只在某张单结算时变化,`PUBLIC_CACHE`
(300s)陈旧无害且自愈——所以纯服务端渲染,不需要 client 组件、不需要定时器。

### 上线时的真实预期

生产 20 单(2026-08-14~09-02,16胜3负1走)按 `published_at` 排序的尾部序列是
`win×5, push, lose` → **连中 = 5**,banner 会显示「每日精选 · 近 5 单全中」。
连中一旦被打断,阶梯的下一顺位是西甲(联赛 87)5单5中 100%(真实数据,已核对)。

## 13. 首页两条 banner 改版为横条(2026-09,经站长批准)

参照站长另一个项目 `miaomiaodi.cc` 的 `frontend/components/worldcup/
VipPromoBanner.tsx`(线上当时 522,读的是源码)重做。改版前两块合计 740px
(真机 375px 实测),把「重点比赛」整个挤出首屏;改版后约 250px。

### 借过来的四个装置

1. 横条形态,不是竖着的卡——省地方主要靠这一条;
2. 两端浅染、中间留白的横向渐变底 + 同色柔光投影;
3. 脉冲圆点 badge(`animate-ping`);
4. 胶囊 CTA + 持续扫光(`banner-shimmer`)+ 箭头 nudge。

### 刻意没照抄的

- 配色:`.cc` 用 sky/blue,这里换成站内 `--brand-gold` 与 `--odds-up`
  (后者是"红涨"那一档,不是 `--brand-red` 那个错误态语义色,§11.2);
- `.cc` 那个位置放的是**赔率大数字**,公推的硬性要求是不出赔率,换成
  推荐选项并用 `--brand-teal`(§11.2「青绿 = 主要操作/选中」);
- 战绩条的圆点**不脉冲**:脉冲表示"正在发生",战绩是过去式;同屏两个点
  一起跳也吵。脉冲只留给公推条;
- `prefers-reduced-motion` 下三个动画全部关掉(`.cc` 原样保留了这条)。

### 腿行:为了画队徽新增的下发字段

`RecoPublicCurrentLegDTO` 补 `league_id` / `league_name_zh` / `home` /
`away`(后两个是新的 `RecoPublicCurrentTeamDTO`:`team_id` + 中文名 +
同源 `crest_url`,**不含 name_en**——banner 上没有位置展示英文名)。

取数在 `backend/queries/reco.py::_banner_match_facts_for_ids`(整批取、
跨库不 ATTACH,与 `reco_highlight.py::_league_ids_for_match_ids` 同一范式)。

**全部字段可空且缺失即退化**:腿没有 `match_id`、`dim_match` 里查不到
这一行、联赛不在 `LEAGUE_META`、队徽还没被媒体管线采到——任何一种情况
都只是少画一个图标,前端仍用 `match_desc` 文本把这条腿完整渲染出来,
绝不因缺图藏腿。三种退化路径各有一条后端测试。

联赛徽走自托管静态图 `frontend/public/brand/leagues/{league_id}.png`,
不是外链(§11.2);队徽 `crest_url` 为 None 时 `TeamBadge` 渲染两字缩写
兜底,与全站既有行为一致,不是错误态。

### 两处为宽度做的取舍(390px 实测,不是估计)

- **玩法名不进可见文案**。这一行的实测余量只有约 19px,而「胜平负 」要
  46px,加进去队名立刻被省略成「阿···」。腿行的展示优先级是"哪两支队、
  几点、推什么";玩法放进 `title`(悬停可见),完整玩法在 /reco 公推页。
- **开球时刻同日只出钟点,跨日才带日期**。一律写 "01:00" 在 2 天窗口下
  分不清哪天;一律带 "9月5日" 又多占约 40px。用 `slip_date` 与开球的北京
  自然日比对来二选一——**两个字符串比较,不读时钟**,服务端与客户端结果
  恒等,没有水合风险。
- 队名是这一行唯一允许收缩的元素(徽标、vs、时间、选项全是 `flex:none`):
  窄屏先省略长队名,而不是让末尾的开球时刻被容器 `overflow` 裁掉——
  时间被裁成"9月4日 16:"是残缺的事实,队名短一截只是省略的名字。

### 战绩条的文案拆分

`highlightLines` 从返回单个 `main` 扩成 `{main, boardShort, value, emphasize}`:
横条把板块短标签(「精选」,脱掉「每日」前缀)做成灰色前缀、口径与计数用
强调色。`main` 保留不变,它是"文案必含原始计数""不得出现裸百分比"两条
不变量的断言对象;`frontend/tests/reco-highlight.test.ts` 另加一条
`main === ${板块} · ${value}` 的防漂移断言,免得守卫守着一个页面上并不
存在的字符串。

## 14. 战绩横条:放大连中数 + 挂回报率(2026-09,经站长批准)

站长在手机端反馈只有「精选 近 5 单全中」一行太单调,要求放大数字并加回报率,两件都做。

### 口径(站长明确选定)

**回报只算连中那 N 单**——与「近 N 单全中」逐单同一批样本。被跳过的
`push`/`voided` 两头都不进(既不进分子也不进分母),连中之外的单一条不进。
理由:横条上「近 N 单全中」和「回报 +X%」是同一行里的两个数字,取自不同
批次就等于在骗人(同 §11.3「图上的聚合数字必须与图上画的点同源」)。

`Streak.net_units = Σ(return_units) − length`,与 `queries/reco.py` 的 SQL
`SUM(return_units - 1)` 及 parlay 分支的 `sum(...) - len(...)` 同口径,不发明
第三种算式。回报率 = `net_units / length`,**由前端派生**——后端刻意不下发
算好的百分比,同 `RecoHighlightRateDTO` 把 `hit_rate` 与 `decided_count` 塞在
同一对象里的用意:让"只渲染百分比、不渲染样本量"在类型层面就很别扭。

### 站长已知悉并接受的三点

1. **这个 ROI 结构上只可能是正数**:连中按定义全是 `win`,每单
   `return_units > 1`。它不是会波动的绩效指标。站长选择**不在界面上披露**
   这一点——「近 N 单全中」已经把样本讲清楚,读者可自行推出。
2. **被串关拉高**:上线时那 5 单里 1 张 2 串 1(返还 3.66)贡献了 2.66 净单位,
   占 5.965 的 45%。
3. **这是站内第一个回报百分比**。既有的回报表述一律是绝对值「净单位 +X.XX」
   (`app/page.tsx`、`app/reco/page.tsx`、本文件 parlay 分支),两种单位就此并存。

### 文案拆成结构化分段(`HighlightPart[]`)

`value` 是一整个字符串,没法只放大其中一个字;而三个 kind 的数字位置完全不同
(`近 N 单全中` / `串关 N 单 回报 +X.XX 单位` / `近 N 天 · … · N 单 M 中`),
**正则切分是错的**。所以 `highlightLines` 增 `parts: HighlightPart[]`,
每段可标 `big`(放大)或 `muted`(次级灰)。

**不变量:`parts` 拼起来逐字节等于 `value`**(有测试守着)。`value` 与 `main`
逐字保留,继续作为「文案必含原始计数」「不得出现裸百分比」两条既有断言的对象
——拆分不得让守卫守着一个页面上并不存在的字符串。

三个 kind 都给"头号数字"标 `big`(连中数 / 命中数 / 净回报),免得哪天换一个
kind 胜出时横条突然没有视觉重点。回报率**只加在 streak 分支**:parlay_return
本来就在展示回报(单位制),rate 分支保持命中率口径,两种单位不混。

### 放大数字的实现:不能照抄站内先例

`WinProbabilityBar.module.css` 用 `transform: scale()` 而非 `font-size`,注释
写明理由是"不参与 flex 宽度计算,不会把旁边两档挤走"——但那是**独占一个 flex
格**的数字。战绩条的数字夹在「近」和「单全中」中间,`scale` 不占位会压到相邻
的字。所以这里走 `font-size: 20px`,代价是必须自己把行盒摁住:
`line-height: 1` + `display: inline-block` 两条都不能省(`.recordStrip` 全块
没有 `line-height` 声明,继承 body 的 1.5,20px 字不摁住会把 12px 行盒撑到
30px)。实测撑高 **1px**(71→72)。字重上限 700——Oswald 只加载了 500/600/700。

### 排查中修掉的一个真缺陷:「回报 NaN%」

本地实测首次渲染出的是「回报 **NaN**%」。根因不是算式错,是缓存:
`/reco/highlight` 带 `s-maxage=300`、服务端 fetch 也是 `revalidate: 300`,
所以**每次部署后最长 5 分钟,新前端会真实收到不含 `net_units` 的旧缓存响应**。
DTO 上该字段是必填,但"契约必填"挡不住"缓存里的旧响应"。

修法:`net_units` 缺失或非有限值时**整段回报不渲染**,退回改版前的文案,
放大的连中数照常在。有回归测试守着(`tests/reco-highlight.test.ts`)。
凡是"新增一个必填字段、前端立刻拿它做除法"的改动,都要想一遍这 5 分钟窗口。

### 回报口径修正:改为「净单位 × 100%」(2026-09-04,站长选定)

上线当天站长问「近 5 单全中回报率只有 119% 吗」,澄清后改口径。

- **原口径 ROI** = 净利 ÷ 总投入 = 5.965 ÷ 5 = **119%**;
- **现口径** = 累计净利 ÷ **单注** = 5.965 ÷ 1 = **597%**。

站长举的例子:「一单 100 赢了 80,再一单又赢 80,累计 160,就是 160%」——
分母是**单注**不是总投入。这与站内既有的「净单位」(`app/page.tsx`、
`app/reco/page.tsx`、本文件 parlay 分支)是同一把尺子,只是换成百分比表达,
所以**后端一行没改**——`Streak.net_units` 本来就是这个值,前端只是从
`net/length` 改成 `net`。

**已如实告知站长的代价**:这个数随出单量增长,不只随水平增长。100 单每单
净赚 0.5 是 +5000%,5 单每单净赚 1.19 是 +597%,前者看着好 8 倍但每单其实
差一半。它是"累计战绩"不是"效率",不同时期不可比。站长知悉后仍选此口径。

**一个跨语言的坑**:`5.965 * 100 = 596.5` 恰好落在 .5 边界上,Python 的
`round` 是银行家舍入(→596)、JS 的 `Math.round` 是四舍五入(→597)。页面由
JS 渲染,线上显示 **597**。所以后端测试**不断言展示出来的百分比**,只断言
`net_units` 这个契约值;展示断言归 `frontend/tests/reco-highlight.test.ts`。
凡是后端测试想"顺手验一下前端会显示什么"的地方,都要想到这条。
