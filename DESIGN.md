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

### 2.7 浅色模式(可选,非默认)
本品牌天生深色。如需浅色:底 `#F7F3EA`(暖米)、主文字 `#2A2118`(浓咖)、
文字用金取 `--gold-900 #875A13`(保证对比),金渐变仅用于大标题 / 徽标。**站点默认深色。**

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
