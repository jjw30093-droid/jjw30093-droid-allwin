# 足球欧赢网 · Allwin — 品牌与设计系统(DESIGN.md)

> 单一事实源。allwin 前端所有页面的配色、字体、组件、间距一律以本文件为准。
> 可直接喂给 **Claude Design** / **Claude Code** 作为设计系统输入。
> 优先级:**用户当次指令 > 本文件 > 工具默认**。

---

## 0. 品牌一句话

**数据球 · 精炼纯金。** 高端足球数据 / 盘口分析平台,**金属金 × 暖黑**——厚重、精密、可信。
足球主体(金币)+ 数据柱 + 上扬箭头 = 「用数据赢」。

信息架构照搬 FotMob 的联赛页组织逻辑(积分榜 / 赛程 / 比赛详情 / 球员榜),
**视觉皮 100% 用本规范,绝不照搬 FotMob 的浅底蓝调。**

---

## 1. Logo 资源

已解压进本仓库 `brand/export/`,与本文件同属一个自包含品牌包(可整个 `brand/` + `DESIGN.md` 一起上传给 Claude Design)。

| 用途 | 文件 | 用在哪 |
|---|---|---|
| 主图标(金,矢量优先) | `brand/export/icon/logo-icon-gold.svg` / `-1024/512/256.png` | 深色底、页头图标、加载态 |
| 图标(深色单色) | `brand/export/icon/logo-icon-black.svg` / `-black-1024.png` | 浅色底 |
| 图标(反白) | `brand/export/icon/logo-icon-white.svg` / `-white-1024.png` | 彩色 / 深色照片底 |
| 网站图标 | `brand/export/favicon/app-icon-512.png` `favicon-180/32/16.png` | favicon(金 on 黑,圆角方) |
| 圆形徽标(含字) | `brand/export/logo-badge-1024.png` / `-512.png` | 社媒头像 / 圆形场景 |
| 横版组合 | `brand/export/logo-horizontal-gold.png`(深色底)/ `-black.png`(浅色底) | 页头 lockup |

**使用规则**
- 深色底(网站默认)→ 用 **gold** 或 **white** 版;浅色底 → 用 **black** / `horizontal-black`。
- 净空:四周留白 ≥ 图标高度的 50%。最小尺寸:图标 24px、横版高 20px。
- **禁止**:拉伸变形、改渐变方向、加描边/阴影、改配色、放在杂乱照片上、金色版用于浅色底(对比不足)。

---

## 2. 色彩 tokens

### 2.1 金 · 主强调(metallic gold)—— 克制使用
金是**强调色不是背景色**。只用于:wordmark、激活态、关键数字(积分/进球/赔率)、冠军高亮、图表重点。**不要大面积铺金**。

| token | hex | 说明 |
|---|---|---|
| `--gold-hi` | `#FFF1C6` | 高光(渐变起点) |
| `--gold-100` | `#F1CE78` | 亮金 |
| `--gold-300` | `#EDC163` | 中亮金 / 文字用金 |
| `--gold-500` | `#D49E33` | 主金(单色场景取此) |
| `--gold-700` | `#B77F20` | 深金 |
| `--gold-900` | `#875A13` | 暗金(渐变终点、浅底文字用金) |
| `--gold-grad` | `linear-gradient(150deg,#FFF1C6 0%,#F1CE78 24%,#D49E33 50%,#EDC163 63%,#B77F20 82%,#875A13 100%)` | 金属金渐变(wordmark / 激活 pill / 关键数字) |

### 2.2 暖黑 · 底与面(warm black surfaces)
| token | hex | 用途 |
|---|---|---|
| `--bg` | `#0A0806` | 页面画布 |
| `--bg-2` | `#100B07` | 次级底(表头带) |
| `--surface` | `#14100A` | 卡片 / 面板 |
| `--surface-2` | `#1A140C` | 抬起态 / hover / chip |
| `--border` | `#241C11` | 默认发丝线 |
| `--border-strong` | `#2E2413` | 强调分隔 |

### 2.3 暖中性 · 文字(中性灰偏金,不用纯灰)
| token | hex | 对比度(vs `--surface`) | 用途 |
|---|---|---|---|
| `--ink` | `#F3ECDC` | 16.1:1 | 主文字(暖白) |
| `--ink-2` | `#A79C87` | 7.0:1 | 次要文字 |
| `--ink-3` | `#948A75` | 5.6:1 | 提示 / 图例 / 脚注小字 |

> **可读性红线**:`--ink-3` 对暗底的对比度必须 ≥ 4.5:1(WCAG AA,常规字号)。
> 早期版本用过 `#6E6553`(仅 3.3:1,肉眼可验证"看不清"),已改为 `#948A75`。
> 任何新增的次要/提示文字色,新增前先按此算法核对,不要凭肉感调深。
> **用色原则**:`--ink-3` 只用于真正的辅助信息(图例、脚注、单位标注);
> 只要文字承载真实数据或需要用户读懂的内容,一律用 `--ink-2` 或更高对比度。

### 2.4 语义色(与金强调分离,去饱和,不与金争)
| token | hex | 用途 |
|---|---|---|
| `--win` | `#4E9A5B` | 胜 / 正净胜球 / 上涨 |
| `--loss` | `#C05437` | 负 / 负净胜球 / 下跌 |
| `--draw` | `#8A8069` | 平 |

### 2.5 功能色 · 联赛资格条(取自真实 FotMob `qual_color`,权威值)
| 含义 | hex |
|---|---|
| 欧冠区 | `#2AD572` |
| 欧战 / 杯赛欧战 | `#0046A7` |
| 欧协联附加 | `#02CCF0` |
| 降级区 | `#FF4646` |

> **权威来源**:这 4 个色值来自 `allwin.db` 的 `fact_league_table.qual_color`,是 FotMob 官方真实取值。
> 任何设计工具(含 Claude Design)自行"推断"出的资格色都不作数,一律以此表为准。

### 2.6 评分色标(球员/球队评分胶囊)
评分数字本身用 Oswald;胶囊底色按区间分档,用于球员评分榜、单场评分列。

| 区间 | 底色 token | hex |
|---|---|---|
| ≥ 7.5 | `--rate-elite` | `--gold-300 #EDC163`(金,复用金色强调) |
| 7.0–7.49 | `--rate-good` | `#3A331F`(暖深金调,`--surface-2` 之上一档) |
| 6.5–6.99 | `--rate-mid` | `--surface-2 #1A140C`(中性,不额外上色) |
| < 6.5 | `--rate-low` | `#2A2519`(冷暖灰,略降饱和) |

文字色:胶囊底为金时用 `#2A1B06`(深金底深字);其余用 `--ink`。

### 2.7 阴影 / 层次(移植自 miaomiaodi.cc,原设计系统缺失这一层)
此前完全没有阴影 token,卡片和顶栏只靠色差分层,层次感偏弱。补两档:

| token | 值 | 用途 |
|---|---|---|
| `--shadow-ambient-lift` | `0 1px 2px rgba(0,0,0,.05)` | 常规卡片,只做极轻的"抬起"暗示,不抢暖黑底色 |
| `--shadow-panel-strong` | `0 8px 32px rgba(0,0,0,.35)` | 顶栏毛玻璃 / 弹层等悬浮元素,暗色模式下阴影本身对比弱,靠这个值撑住悬浮感 |

搭配 §2.2 暖黑层级使用:面越"浮起"(顶栏、弹层)阴影越重,常规卡片只用 `--shadow-ambient-lift`。

配套一个可直接用的毛玻璃工具类(顶栏此前一直是"应该 sticky + 毛玻璃"的文字描述,没有落地写法,这次补上):
```css
.glass-panel {
  background: rgba(20,16,10,.86); /* ≈ --surface 的高不透明度版本,即顶栏说的"暖黑毛玻璃" */
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border);
  box-shadow: var(--shadow-panel-strong);
}
```

### 2.8 数据可视化扩展色阶(移植自 miaomiaodi.cc 的色阶结构,颜色重新取自本站色板)
评分色标(§2.6)已有;这里补两类 cc 有、allwin 目前没有的色阶结构——**只搬结构,不搬 cc 自己的粉紫/红绿色相**:

**热区色阶**(Phase 2「进球热区」用,现在不接):
| token | hex | 说明 |
|---|---|---|
| `--heat-low` | `#2A1F14` | 低密度(暖黑基调浅一档,不用 cc 的粉色) |
| `--heat-high` | `#D49E33`(= `--gold-500`) | 高密度,复用金色而不是新开色相,保持"数据重点=金"的一致性 |

**赔率涨跌色**(Phase 2 赔率 tab 用,🔒 MVP 不接入;现在只保留 token 名字占位,避免以后临时起名不一致):
| token | 占位值 | 说明 |
|---|---|---|
| `--odds-up` | = `--loss #C05437` | 赔率涨(概率降)——复用"客队/下跌"的语义色,不新开红色 |
| `--odds-down` | = `--win #4E9A5B` | 赔率跌(概率升)——复用"胜/上涨"的语义色 |
| `--odds-flat` | = `--ink-3 #948A75` | 不变 |

> cc 用粉紫热区 + 红绿赔率,是它自己 Synthetic Ether 配色体系的延伸;allwin 复用已有的金色/语义色,不为了抄结构而新增游离于主色板之外的色相。

### 2.9 浅色模式(可选,非默认;实现架构移植自 miaomiaodi.cc)
本品牌天生深色。如需浅色:底 `#F7F3EA`(暖米)、主文字 `#2A2118`(浓咖)、
文字用金取 `--gold-900 #875A13`(保证对比),金渐变仅用于大标题 / 徽标。**站点默认深色。**

**实现架构(移植自 cc——cc 这块此前也只有一句"可选"没给写法,是两边都补上的空白)**:
- 深浅色的所有 token 挂在同一批 CSS 变量名下,靠 `.light`/`.dark`(或 `[data-theme=]`)类切换变量*值*,**禁止 `isDark ? <A/> : <B/>` 这种整块 JSX 双分支**——cc 吃过真实的亏(2026-04-23 事故:双 JSX 分支改一套漏一套,用户原话"改了一套，另外一套也被改动了")。
- 组件内要做深浅差异时只用两种手段:①CSS variable(项目级 token、内联 style);②少量零散 class 才用条件类名。禁止条件渲染整段结构/文案/交互。
- 唯一例外:纯装饰层(如深色专属光晕、浅色专属纸纹),必须满足①只包裹无文字无交互的装饰元素 ②注释标注 ③外层结构仍是单份 JSX。
- `@media (prefers-reduced-motion: reduce)` 必须关闭所有装饰性动画(配方见 §4.4)。

---

## 3. 字体 typography

| 角色 | 字体 | 字重 |
|---|---|---|
| 中文 · 标题 / 品牌 | **Noto Sans SC(思源黑体)** | Black 900 |
| 中文 · 正文 | Noto Sans SC | Regular 400 / Medium 500 |
| 拉丁 & **所有数字** | **Oswald** | SemiBold 600(标签/数字)· 700(关键数字) |

- **数字气质是灵魂**:所有比分、赔率、积分、排名、净胜球一律 **Oswald + `tabular-nums`**。Oswald 的压缩体育感是盘口调性的关键。
- 回退栈:CJK `"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif`;拉丁 `"Oswald",system-ui,sans-serif`。
- **内嵌**:生产 / Artifact 的 CSP 禁外链字体 → 用 `@font-face` **内嵌 Oswald**(Latin 子集,约 30–50KB)。CJK 体积过大,用系统 PingFang SC 兜底即可。
- 排印规则:拉丁小标签 `text-transform:uppercase` + `letter-spacing:.08–.4em`;标题 `text-wrap:balance`;正文行宽 ≈ 40 全角字 / 65 拉丁字符。

### 字号阶梯
`11 · 12 · 13.5 · 14.5 · 16 · 19 · 22 · 30`(px)。品牌 wordmark 19、页面 H1 30、数字积分 16、表内数据 14。

> **未采用 cc(miaomiaodi.cc)的字体**:cc 用 Inter(标题/数字)+ Manrope(正文),是通用科技感英文站的选择。
> allwin"Oswald 压缩体育感是盘口调性的灵魂"是本站已确立的字体人格,两边气质不同、没有复用意义,**维持 Oswald + Noto Sans SC,不替换**。

---

## 4. 布局 & 组件

- **栅格**:内容最大宽 `1140px` 居中;主列 `1fr` + 侧栏 `296px`,`≤900px` 堆叠。
- **圆角**:卡片 `16px`、控件 `8–12px`、pill `999px`、monogram 圆形。
- **单侧边框不加圆角**(资格色条那种 `border-left` 保持直角)。

| 组件 | 规则 |
|---|---|
| 顶栏 | sticky + 暖黑毛玻璃 `rgba(12,9,6,.86)` + 底部 `--border` 发丝线;左 logo、中 nav、右赛季 chip;激活项 `--gold-hi` 文字 + 金渐变下划线 |
| 分段控件(总/主/客/近期/xG) | 容器 `--surface` + border;激活项 = 金渐变底 + 深字 `#2A1B06` + 700 |
| 数据表 | 行高 54px(标准)/ 74px(舒适)/ 50px(紧凑,三档可切换);`--border` 分隔;**左侧 3–4px 资格色条**(资格分区之间的行分隔线加粗为 `--border-strong`);队徽 chip;队名中文主 + 英文次;数字 Oswald tabular 右对齐;**积分列加重(全表唯一大号金色数字,视觉锚点)**;冠军行淡金浮层 + 金色积分 + 「冠军」小徽标 |
| 净胜球 / 进失球 | 进球(GF)`--win` 微上色、失球(GA)`--loss` 微上色;净胜球 `+` 用 `--win`、`-` 用 `--loss` |
| 卡片 | `--surface` + `--border` + 16px 圆角;标题用金 |
| 徽章 / pill(冠军·资格) | 底用资格色、文字取该色的深色档 |
| 交互态 | hover 抬一档面色(→`--surface-2`);focus 可见环;禁用降透明,不用纯灰 |
| **队徽 chip**(吸取 FotMob) | 33–40px 方圆角(`rx 9px`)或圆形;底色 = 该队品牌主色,文字 = 3 字母缩写(Oswald 700);浅底球队(如 Man City 天蓝、Wolves 橙黄)需单独指定深色文字覆盖默认白字。**过渡态**:占位阶段用纯色块+缩写;生产阶段替换为 `Team_ID → 真实队徽图`(经后端代理避免热链盗图) |
| **模块榜单卡**(吸取 FotMob 首屏) | 卡内展示 top-3(积分榜/评分榜/射手榜各一张),每行:排名 + 队徽或头像 + 姓名/队名 + 数值胶囊(积分用金字,评分用 §2.6 色标,进球数用 `--ink`);卡底部一行「全部 →」跳转全量页 |
| **联赛速读洞察条**(吸取 FootyStats) | 单行或可换行文字,`--surface` 底 + `--border`,字号 13.5px、`--ink-2`;内容由真实数据现算生成(如"冠军 X 分,领先第2名 Y 分 · 保级线 Z 分 · 榜首垫底差 W 分"),关键数字用 `--gold-300` 高亮,不是纯装饰而是真实统计 |
| **胜平负概率条**(吸取 FootyStats) | 单条横向三段堆叠条,宽度 = 真实百分比(主胜/平/客胜由 `dim_match` 全季比分现算);三段配色用**语义色**(`--win` 主胜 / `--draw` 平 / `--loss` 客胜的类比色,不占用资格色或金色);条上或条下标百分比数字 |
| **联赛概况卡**(吸取 FootyStats) | 一行内展示:球队数、赛季进度(x/y 轮已赛)、进度条、数据更新时间、来源脚注 |
| **赛程行**(吸取 FotMob) | 对称三栏:主队名+队徽右对齐 / 比分或时间居中(Oswald,未开赛显示开球时间、已完赛显示比分 + FT 角标)/ 队徽+客队名左对齐;日期分组头 `--ink-3` + `letter-spacing`;轮次切换器 `‹ 第 N 轮 ›` 居中 |

### 移动端规则(吸取 FotMob,首要交付物)
- 面向以手机为主的用户,字号、点按区域按 §3 字号阶梯**不再进一步缩小**,而是**主动砍列**。
- 积分榜窄屏(`<640px`)只保留:排名 / 队徽+队名 / 场 / 进-失 / 净胜球 / 积分,隐藏 胜/平/负 三列(可通过「查看更多」/横滑找回,不强制隐藏数据本身)。
- 顶栏窄屏收为:logo + 汉堡菜单(替代横排 6 项导航)。
- 宽屏(`≥900px`)展示全部列,不做精简。

- **动效**:克制。入场一次 `rise`(translateY+fade,cubic-bezier(.22,1,.36,1));**尊重 `prefers-reduced-motion`**;不堆特效(堆特效 = AI 味)。

---

## 4.1 信息架构(五页 MVP + Phase 2)

| 页面 | 内容 | 数据来源表 |
|---|---|---|
| 总览(首屏,模块化) | 迷你积分榜 top-5 + 速读洞察条 + 胜平负概率条 + 评分榜卡 + 射手榜卡 | `fact_league_table` / `dim_match` / `fact_season_player_stats` |
| 排名(积分榜) | 总/主/客/近期/xG 5 档 Tab,20 队全表,近 5 场战绩 | `fact_league_table`(5 个 `table_type`)+ `dim_match`(近 5 场现算) |
| 赛程 | 按轮次 / 按球队切换,380 场,日期分组 | `dim_match` |
| 球员榜 | 37 个统计维度(进球/助攻/评分/…) | `fact_season_player_stats` |
| 球队数据 | 球队维度统计(评分/场均进球/场均失球等) | `fact_team_match_stats` 聚合 |
| 比赛详情(★核心页) | 事件时间线 + 阵型阵容 + 射门图 + 球队数据对比 + 球员评分 | `dim_match` + `fact_match_events` + `fact_match_lineup` + `fact_shotmap` + `fact_team_match_stats` + `fact_player_match_stats` |
| *(Phase 2)* 联赛数据分析 | 大小球/BTTS 表、比分分布、进球时间分布(10/15 分钟柱)、球队场均射门/控球/角球/黄牌/xG | `dim_match` 现算 + `fact_team_match_stats.extra_json` |

---

## 4.2 卡片风格分级(移植自 miaomiaodi.cc card-elite / card-sub)
现有卡片规则(§4 组件表"卡片"行)只有一档。cc 有"主卡/次卡"两档,直接搬这个分级,颜色换成本站 token:

| 档位 | 规则 | 用在哪 |
|---|---|---|
| 主卡(elite) | `--surface` 底 + `--border` + `--shadow-ambient-lift` + 16px 圆角 | 榜单卡、概率卡这类主内容容器 |
| 次卡(sub) | `--bg-2` 底(比主卡更沉)+ `--border` + 无阴影或更轻阴影 + 10px 圆角 | 卡片内嵌的子分组、次要信息块,和主卡拉开一级层次 |

## 4.3 徽标 / pill 补充(移植自 miaomiaodi.cc badge-hot / badge-free)
在 §4 组件表"徽章 / pill"基础上,补两种运营/状态类小徽标(不是资格色/冠军徽标那类):

| 徽标 | 规则 | 用途 |
|---|---|---|
| 热门标 | 底色 `--gold-700`,白字 | 热门联赛/热门球队等运营强调位 |
| 免费标 | 透明底 + `--gold-300` 描边 + `--gold-300` 文字 | 标"这块内容免费"(和付费墙对照使用) |

## 4.4 入场动效实现(移植自 miaomiaodi.cc fade-up,是 §4"动效:克制"的具体配方)
DESIGN.md 原来只写了"入场一次 rise"一句话没给参数。cc 有现成、克制的实现,直接采用:

```css
@keyframes fade-up {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}
.fade-up { animation: fade-up .6s cubic-bezier(.22,1,.36,1) both; }
.fade-up-d1 { animation-delay: .08s; }
.fade-up-d2 { animation-delay: .16s; }
.fade-up-d3 { animation-delay: .24s; }
```

列表/卡片错峰入场时按 `.fade-up-d1/d2/d3…` 逐级加延迟,不要每个元素单独写延迟值。
`@media (prefers-reduced-motion: reduce)` 一律关闭(见 §2.9)。

---

## 5. 可直接粘贴的 CSS 变量(`:root`)

```css
:root{
  /* gold */
  --gold-hi:#FFF1C6; --gold-100:#F1CE78; --gold-300:#EDC163;
  --gold-500:#D49E33; --gold-700:#B77F20; --gold-900:#875A13;
  --gold-grad:linear-gradient(150deg,#FFF1C6 0%,#F1CE78 24%,#D49E33 50%,#EDC163 63%,#B77F20 82%,#875A13 100%);
  /* warm-black surfaces */
  --bg:#0A0806; --bg-2:#100B07; --surface:#14100A; --surface-2:#1A140C;
  --border:#241C11; --border-strong:#2E2413;
  /* warm neutrals */
  --ink:#F3ECDC; --ink-2:#A79C87; --ink-3:#948A75;
  /* semantic */
  --win:#4E9A5B; --loss:#C05437; --draw:#8A8069;
  /* league qualification (real FotMob qual_color) */
  --q-ucl:#2AD572; --q-uel:#0046A7; --q-uecl:#02CCF0; --q-releg:#FF4646;
  /* rating color scale */
  --rate-elite:#EDC163; --rate-good:#3A331F; --rate-mid:#1A140C; --rate-low:#2A2519;
  /* shadows(移植自 cc) */
  --shadow-ambient-lift:0 1px 2px rgba(0,0,0,.05);
  --shadow-panel-strong:0 8px 32px rgba(0,0,0,.35);
  /* heatmap scale(移植自 cc 结构,颜色用本站金色板,Phase 2 用) */
  --heat-low:#2A1F14; --heat-high:#D49E33;
  /* odds up/down(移植自 cc 结构,颜色复用语义色,Phase 2 用,MVP 不接) */
  --odds-up:#C05437; --odds-down:#4E9A5B; --odds-flat:#948A75;
  /* type */
  --font-cn:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
  --font-latin:"Oswald",system-ui,sans-serif;
  /* radius */
  --r-card:16px; --r-control:10px; --r-pill:999px;
}
```

---

## 6. 喂给 Claude Design 的用法

1. `claude.ai/design` 新建 project。
2. 上传 **本 `DESIGN.md`** + `brand/` 整个文件夹(自包含,两者一起拖进去即可)。
3. 提示语加一句:**「严格遵循 DESIGN.md 的 color / type / component tokens,深色为默认,金色只作强调。」**
4. 优化完 Export → **Standalone HTML** 或 **Handoff to Claude Code** 传回,接真实数据做成真页面。

> 参考实现:积分榜样稿(本仓库讨论中发布的 artifact)已按本规范落地,可作为组件与配色的活参照。

### 竞品 UI 参考(仅作组件/交互灵感,视觉不照搬)
- **FotMob**:干净的移动端体育数据体验 —— 真实队徽做锚点、左侧资格色条、吸顶 tab、模块化首屏(迷你榜单卡 + 全部→)、赛程行对称版式、大字号大点按区。**MVP 全部吸收**,见 §4「组件」表中标注「吸取 FotMob」的行。
- **FootyStats**:盘口/数据分析深度 —— 胜平负概率条、联赛速读洞察、进球时间分布、比分分布、球队场均数据表。**MVP 只轻量点缀**(速读洞察条 + 概率条 + 联赛概况卡);进球时间分布/比分分布等完整分析页排 **Phase 2**,数据全部来自现有 9 表 SQL 现算,非新数据源。

---

## 7. 设计系统来源与移植记录(2026-07-11,与 miaomiaodi.cc 合并)

miaomiaodi.cc(cc)前端**没有独立的"设计规范"文档**——它的前端 CLAUDE.md 全是工程纪律(组件替换保样式铁律、部署三必查、深浅模式编码铁律等),真正的视觉 token 系统在 `frontend/app/globals.css` 里(一套 dark-first + `.light` override 的 CSS 变量系统,含 Stitch "Synthetic Ether" 配色、卡片分级、玻璃面板、徽标、概率条、热区/评分/赔率色阶等)。本次移植读的是这份 `globals.css` + CLAUDE.md §14(深浅模式实现铁律),不是字面意义上的"CLAUDE.md 设计规范章节"。

**移植了什么(结构/模式,不是颜色本身)**:
- 阴影 / 层次 token(§2.7)——allwin 原来完全没有阴影系统。
- 毛玻璃工具类的具体写法(§2.7)——原来顶栏"暖黑毛玻璃"只有文字描述没有代码。
- 数据可视化扩展色阶结构(§2.8)——热区色阶、赔率涨跌色的"占位/预留"做法。
- 浅色模式实现架构 + 深浅模式编码铁律(§2.9)——单 JSX + CSS variable,禁止双 JSX 分支。
- 卡片二级分级:主卡/次卡(§4.2)。
- 运营类徽标:热门标/免费标(§4.3)。
- 入场动效的具体 CSS 配方(§4.4)——原来只有"入场一次 rise"一句话。

**剔除的世界杯/单赛事专属部分(不移植)**:
- 全部 `--wc-*` token(`wc-bg-elev`/`wc-pitch-bg`/`wc-purple`/`wc-green` 等)——绑定世界杯赛事自己的主题色和"球场背景"装饰,allwin 是长期多联赛站,不需要单赛事视觉。
- `--odds-up/down/flat` 的**颜色值**(cc 用红涨绿跌的独立色相)——只搬了 token 名字占位,真实颜色改成复用 allwin 已有的 `--loss`/`--win`,且 MVP 阶段不接(CLAUDE.md §7 已定真实赔率是 Phase 2)。
- `.hud-btn-*` 那套 clip-path 切角未来感按钮——cc 自己"Synthetic Ether"赛博朋克基调的产物,和 allwin"精炼金属金"的克制奢感调性直接冲突,不移植。
- cc 的主强调色(neon 青 `#5BA8F5` + 品红 `#E0719A`)——**没有采纳为 allwin 主色**,见下面冲突判断。
- cc 的字体(Inter + Manrope)——同样没有采纳,见下面判断。

**冲突判断,all-win 保留自己原有的**:
- **主强调色仍是金色,不换成 cc 的青/品红**。理由:①allwin 已经有一套产出完成的真实 logo 资源(`brand/export/`,金色矢量图标 + 多尺寸 PNG + favicon,16 个文件)绑定了金色品牌识别,换色会让这批资源作废;②cc 的青/品红本身是它"Synthetic Ether"赛博题材的专属配色,金色在 cc 里反而是"精英/世界杯"限定强调色——两边刚好角色对调,allwin 把"金"从 cc 的限定强调色升级成自己的主强调色,逻辑自洽,不是生搬硬套。
- **字体仍是 Oswald + Noto Sans SC,不换 Inter/Manrope**。理由:DESIGN.md 原文已把"Oswald 压缩体育感"称为"盘口调性的灵魂",这是 allwin 自己的字体人格判断,cc 的 Inter/Manrope 是通用科技站选择,两者没有可比性,换了等于丢了 allwin 自己的字体识别度。
- **间距系统两边都没有独立 token**(cc 是 Tailwind v4 默认间距,allwin 是手写 CSS Modules 里的具体像素值),没有可搬的具体内容,维持现状。

> 这次只改了这份文档,现有 5 个页面(排名/赛程/球队数据榜/球员榜/概率卡)的代码没有跟着改——上面列的新 token(阴影/热区/赔率占位/卡片分级/徽标/动效配方)目前只是"写进规范",还没有任何页面实际使用。下一步按计划先在排名页试点,再决定要不要批量套用。
