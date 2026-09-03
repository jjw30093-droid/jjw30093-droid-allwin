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
