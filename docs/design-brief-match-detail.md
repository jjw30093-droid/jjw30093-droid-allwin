# 比赛详情页重新设计 · 素材包(喂给 Claude Design 用)

> 范围:`/matches/[matchId]`,CLAUDE.md 里标注的"★核心页"。真实覆盖**两种状态**——
> 未开赛/进行中(平铺单页)和已完赛(5 个 tab)。这两种状态用的是**同一批组件**,
> 只是有没有赛后事实数据决定走哪条路径,设计时两种都要覆盖,不能只画一种然后假设另一种"差不多"。
>
> 这个页面**没有任何概率/预测内容**——模型未经真实数据训练前,详情页不渲染任何胜平负概率
> (首页有概率条,这个页面没有,是站长明确拍板下架的,见下方红线 8)。不要把首页那套概率条
> 搬过来。

---

## 1. 设计系统入口

同首页那份简报的结论:参考 `DESIGN.md` + `brand/` 起步,但**配色不锁死**,只要求:
- 深浅主题切换,单份结构只换 CSS 变量;
- 胜/平/负、涨/跌这类语义色保留"可区分的独立色相"这个结构,具体色值随新方案定。

---

## 2. 两种状态的判定条件(不是看 `status` 字段这么简单)

真实判据是 `GET /api/v1/matches/{id}/report` 里 `available` 字段——lineup/events/shots/team_stats/player_stats **五张事实表任意一张有数据**就是 `true`。实践中这五张表只在完赛后由采集链路写入,所以效果上约等于"已完赛",但请按数据可用性理解,不要按 `status` 硬编码三态(未开赛/进行中/已完赛)——**进行中(`InPlay`)目前会退化成平铺态**(因为事实表通常还没写入),头部比分区仍显示 "VS" 而不是比分。这是现有实现的真实行为,这轮设计可以选择保留这个简化,也可以借机把"进行中"做成第三种真正独立的状态——如果选后者,请明确标注,因为对应的后端/前端都需要额外改动,不是纯设计稿能覆盖的。

---

## 3. 页面级共享结构(两种状态都有)

**上下文导航**(纯文字链接一行):`← 返回` / `同联赛上一场` / `同联赛下一场` / `查看本周其他比赛`。

**头部**(两种状态共用同一个头部组件):
- 一行:联赛中文名 + `· 第 N 轮`(round 为空则不写)+ 状态胶囊(未开赛/进行中/已完赛)+ 数据过期提示(仅 `sync_state==="STALE"` 时出现,红色调)
- 主客对阵:队徽(56px)+ 队名,中间未完赛显示 `VS`,已完赛显示比分(Oswald 数字)
- 关注按钮(`FollowButton`,收藏这场比赛)
- 日期行:赛季 · 比赛日 · 开球时间(北京时间)· 数据更新时间

已完赛且有事实报告时,头部下方是 **5 个 tab**(`射门`默认 / `统计` / `阵容` / `事件` / `总览`);无事实报告时头部下方直接平铺"总览"tab 的内容,没有 tab 栏。

---

## 4. 状态一:平铺态(未开赛/进行中)—— 6 个区块,真实数据

### 4.1 本场看点(QuickView,已完赛时不渲染)

真实响应(`GET /api/v1/matches/5868011`,阿拉维斯 vs 赫塔菲,2026-08-15 未开赛):

```json
{
  "match": { "status": "NotStarted", "home_score": null, "away_score": null, "kickoff_at_utc": "2026-08-15T17:30:00Z", "round": "1", "season": "2026/2027" },
  "reco_published": false
}
```

内容:
- 一行:有已发布精选则「本场有已发布的每日精选 →」链到 `/reco?tab=daily`;否则灰字「推荐待发布」
- 一句真实数据对比(不是模型预测,是近 5 场战绩现算):"阿拉维斯 近5场 2胜1平2负,场均入球 1.2;赫塔菲 近5场 1胜2平2负,场均入球 0.8"——双方都没有近期数据时如实收窄成「本场暂无历史交锋与近期数据,仅有赛程信息」,**不铺"暂无正式预测"这类模型语言**
- 底部锚点链接「查看详细数据 ↓」

**红线**:这里不是"模型结论区",是近 5 场真实比分算出来的一句话,不要设计成预测卡片的样子(没有置信度、没有百分比)。

### 4.2 数据倾向(市场卡,`MarketCardsSection`,唯一"这场比赛特有"的内容)

真实响应(`GET /api/v1/matches/5868011/markets`,截断):

```json
{
  "cards": [
    {
      "market": "yellow_cards", "label": "罚牌", "line": 3.5, "estimate": 5.8,
      "hit_rate": 0.6009, "sample_size": 213, "signal_grade": "★★", "lean": "over",
      "data_quality": "ok",
      "driver_factors": [
        { "key": "yellow_cards", "for": { "avg": 2.6, "n": 10 }, "against": { "avg": 2.6, "n": 10 } },
        { "key": "fouls", "for": { "avg": 13.4, "n": 10 }, "against": { "avg": 13.2, "n": 10 } }
      ]
    }
  ]
}
```

`market` 目前有罚牌(yellow_cards)、大小球(over_under 一类)等几种,每张卡结构一致:一个"线"(line)+ 一个"估计值"(estimate)+ 命中率(历史同类比赛的验证结果,不是这场的预测)+ 样本量 + 信号强度(★到★★★)+ 倾向方向(大/小)+ 数据质量标记 + 主客双方驱动因子的对比数据(折叠归因,点开才展开)。

**红线**:`hit_rate` 是"这类数据模式历史上兑现的比例",不是"这场比赛会怎样"的概率——文案不能写成"胜率 60%"这种误导表述。`driver_factors` 默认折叠,展开后是纯数据对比(不是因果解释)。

### 4.3 数据可视化(近期表现 + 图表)

左右两栏 `FormList`(主队/客队各近 5 场):每行 = 胜平负徽标(色块)+ 比分(Oswald)+ 主客 + 对手名 + 日期。真实样本见第 6 节(赫罗纳 vs 埃尔切那场的 `home_form`,格式相同)。

下方是 `analysis.chart_specs` 驱动的图表网格(`ChartWithSummary`,每个图自带标题+摘要文字),赛前主要是历史数据聚合类图表(具体图表类型由 `chart_specs[].type` 决定,当前未开赛比赛这个数组经常为空——`chart_specs.length===0` 时显示「该场比赛暂无可视化图表数据」,不要为空态画得比有数据时还花哨)。

### 4.4 赔率时间轴(`OddsTimeline`)

真实响应(`GET /api/v1/matches/5868011/odds`,截断):

```json
{
  "available": true, "tier": "delayed_summary", "coverage_tier": "full_timeline",
  "observation_count": 1, "display_mode": "current_odds",
  "snapshots": [
    { "market": "1x2", "company_name": "Macauslot", "market_phase": "pre_match",
      "observed_at": "2026-08-12T00:34:47Z",
      "payload": { "initial": { "home": 2.25, "draw": 2.8, "away": 3.21 }, "latest": { "home": 2.25, "draw": 2.8, "away": 3.21 } } }
  ]
}
```

匿名/免费用户是 `delayed_summary`(延迟摘要,当前赔率快照);付费用户 `tier` 会是完整时间线(多个公司、多个时间点的走势)。**这两档必须都设计**——免费态不能画成"只有一条横线",要诚实展示"这是延迟的当前快照,不是完整走势";付费态才展示真正的时间轴/折线。

### 4.5 关键变化(`CooccurrenceSection`,同期事件)

真实响应(这场是 `count: 0`):`{"count": 0, "items": null, "note": "暂无同期事件"}`——**组件整体不渲染**(不画空状态占位)。有数据时展示的是"阵容变化"和"赔率变化"在同一时间窗内被观测到的记录(不声称因果,文案是"同期检测到"而不是"因为...导致...")。设计稿需要覆盖"有共现记录"的样子,但拿不到真实的非空样本——这块可以按字段结构(时间点 + 变化类型 + 变化内容)合理排版,不要发明新字段。

### 4.6 数据来源与说明(默认折叠)

`<details>` 折叠面板:模型版本、数据更新时间、(已完赛时)赛后记录一行 + 免责声明。这是溯源信息,不是内容,折叠态本身就是设计决定,保持折叠。

---

## 5. 状态二:已完赛,5 个 tab

真实样本比赛:赫罗纳 1–1 埃尔切(match_id 4837489,西甲 2025/2026 第 38 轮)。「总览」tab 内容和第 4 节完全一样(只是 `finished=true` 时 QuickView 不渲染,头部比分区显示真实比分而不是 VS),这里只列另外 4 个 tab。

### 5.1 射门(默认 tab,`MatchShotsSection`)

真实样本(单次射门):
```json
{ "player_name": "罗德里格斯", "is_home": false, "minute": 39, "x": 95.93, "y": 43.3, "xg": 0.033, "xgot": 0.095, "situation": "SetPiece", "outcome": "Goal", "shot_type": "LeftFoot" }
```
这场 13 次射门。核心是一张射门位置图(球场半场坐标 x/y)+ 每次射门的 xG/结果(进球/被扑/偏出/中框)。**这是已有的专门数据可视化组件(球场坐标图),不是简单的卡片列表**——如果这轮想重画,需要明确这是要重新设计整个球场坐标图的视觉语言,工作量和"改个卡片样式"不是一个量级,建议单独排期,不要和其他 4 个 tab 混在同一份简报里一起交。

### 5.2 统计(`MatchStatsSection`)

真实样本(单队):`possession: 56.0, expected_goals: 0.58, total_shots: 10, shots_on_target: 7, big_chance: 1, passes: 416, accurate_passes: 343, tackles/interceptions/clearances/...`——双方对比表,每项一行,常见做法是左右对称横向条形图(主队条 vs 客队条)。字段有 20+ 项,当前是纯表格/条形对比,没有分组。

### 5.3 阵容(`MatchLineupSection`)

真实样本(单人):`{"name":"加萨尼加","shirt_number":"13","position_group":"GK","rating":6.2,"pitch_x":0.1,"pitch_y":0.5}`,每队 `formation`(如"4-3-3")+ 首发(带球场坐标 `pitch_x/pitch_y`)+ 替补。这也是坐标定位类可视化(球场阵型图),同射门图一样工作量较大,建议单独排期。

### 5.4 事件(`MatchEventsSection`)

真实样本:`{"event_type":"Card","minute":7,"is_home":false,"player_name":"桑加雷","card_type":"Yellow"}`——按分钟顺序的时间线列表(进球/黄牌/红牌/换人/半场/补时/VAR判罚),这个更接近传统列表/时间轴设计,和平铺态的信息密度接近,适合和其他区块一起这轮做。

---

## 6. 完整字段清单(两种状态都可能用到)

| 字段 | 来源 | 说明 |
|---|---|---|
| `home_form`/`away_form` | `/matches/{id}` | 近 5 场:对手、主客、比分、结果(W/D/L)、日期,数组可能为空 |
| `win_probability` | **无** | 这个页面不下发任何概率字段,不要画概率条 |
| `market_cards` | `/matches/{id}/markets` | 见 §4.2,赛前赛后都展示 |
| `odds` | `/matches/{id}/odds` | 免费延迟摘要 / 付费完整时间线两档 |
| `cooccurrence` | `/matches/{id}/cooccurrence` | 可能为空(`count:0` 时整块不渲染) |
| `report` | `/matches/{id}/report` | 已完赛才有,五张事实表 |
| `analysis.chart_specs` | 分析包 | 赛前经常为空数组 |

---

## 7. 硬性规则(和首页那份共用,这里重申与本页强相关的几条)

1. **本页完全不展示概率/预测**——首页有 Bet365 折算的胜平负概率条,这个页面没有,不要加。
2. **市场卡的 `hit_rate` 不是这场比赛的胜率**,是历史同类样本的验证命中率,文案要分清楚。
3. **赔率必须标注 `observed_at`**,免费/付费两档赔率覆盖都要设计,不能只画付费态。
4. **同期事件不声称因果**,`CooccurrenceSection` 无数据时整块不渲染,不画空槽。
5. **锁定联赛门禁**:未持有权限时整页替换成登录引导(`LeagueGateCard`),不是内容打码——这个页面本身也有匿名/免费/付费三档差异,和首页的锁定卡逻辑是同一套边界。
6. 深浅色单一 JSX、移动端砍列不缩字、图片自托管——同首页红线 4/5/6。
7. 已完赛 tab 栏默认选中"射门",不是"总览"(2026-08-12 刚调整过,如果这轮想改默认 tab,请明确写出来,这是一个真实的产品决定不是随意状态)。

---

## 8. 建议的排期方式

这个页面比首页复杂得多(6 个平铺区块 + 4 个额外 tab,其中射门图和阵容图是专门的坐标可视化组件)。建议分两轮喂给 Claude Design,不要一次性全塞:

- **第一轮**(这次):头部 + 平铺态 6 区块(含"总览"tab 复用同一套)+ 事件 tab(列表型,复杂度低)+ tab 栏整体视觉语言。
- **第二轮**(之后单独出简报):射门位置图 + 阵容坐标图 + 统计对比表,这三个是数据密度和交互复杂度都明显更高的可视化组件,和"页面布局"不是一类设计任务。

---

## 9. 现有可复用组件

| 组件 | 路径 | 作用 |
|---|---|---|
| `MatchTabs` | `frontend/components/matches/MatchTabs.tsx` | 5 个 tab 的切换栏 |
| `MarketCardsSection` | `frontend/components/matches/MarketCardsSection.tsx` | 市场卡,客户端拉取 `/markets` |
| `OddsTimeline` | `frontend/components/matches/OddsTimeline.tsx` | 赔率时间轴,免费/付费两档 |
| `CooccurrenceSection` | `frontend/components/matches/CooccurrenceSection.tsx` | 同期事件,无数据时不渲染 |
| `ChartWithSummary` | `frontend/components/matches/ChartWithSummary.tsx` | 图表 + 摘要文字包装 |
| `MatchShotsSection` | `frontend/components/matches/MatchShotsSection.tsx` | 射门位置图 |
| `MatchStatsSection` | `frontend/components/matches/MatchStatsSection.tsx` | 球队/球员统计对比 |
| `MatchLineupSection` | `frontend/components/matches/MatchLineupSection.tsx` | 阵容坐标图 |
| `MatchEventsSection` | `frontend/components/matches/MatchEventsSection.tsx` | 事件时间线 |
| `TeamBadge` / `LeagueBadge` / `LocalTime` | 同首页简报 | 通用展示组件 |

---

## 10. 交回来实现时怎么对接

同首页流程:定稿后 Export,发回来,我按 §4/§5 的真实字段和 §7 的硬性规则接上真实数据。如果设计稿给平铺态和已完赛态画出了明显不一致的视觉语言(比如头部换了完全不同的版式),需要确认是否要为此拆出两套头部组件,还是保持现有"同一头部,内部条件渲染"的做法。
