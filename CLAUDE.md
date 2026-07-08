# CLAUDE.md — 欧赢 / allwin

> 本文件是本项目的架构宪法 + 当前进度记录。动手前必读。
> 标 🔒 的架构级条款,未经我明确同意不得改动。
> 每次大改动前,先对照本文件的 🔒 条款检查改动是否造成架构漂移。

---

## 0. 项目定位

- 欧赢 allwin:接替世界杯站、面向公众的免费足球数据站,内含付费会员层。
- 与 miaomiaodi.cc / miaomiaodi.vip 完全独立:独立目录、独立 git 仓库。
- 当前阶段:英超单联赛 MVP。已爬好最近 5 个赛季英超数据,本地跑,成熟后上服务器。

---

## 1. 🔒 技术栈(不得擅自更换框架)

- 前端:Next.js App Router,版本 ≥15.2.3(修 CVE-2025-29927,不得降级)
- 后端:FastAPI(Python)
- DB:SQLite + WAL 模式
- 数据源:FotMob 爬取(curl_cffi + ThorData 住宅代理),复用 `fotmob_client.py`
- 模型:LightGBM / Poisson
- 部署(上服务器后):Cloudflare → Nginx → pm2(frontend + backend)
- 禁止未经同意引入:Postgres / Supabase / Prisma / Clerk / 其他 auth SaaS / 其他前端框架 / 微服务。要换先问我。

---

## 2. 🔒 数据架构:一个源,三层派生(+ 中文映射维度)

```
FotMob scraper → Bronze → Silver → Gold → FastAPI serving → Next.js
                    │
                    └── i18n 中文映射维度(服务所有上层)
```

- **Bronze/raw**(FotMob 原始落库,当前已有 9 张表):
  `dim_match` / `dim_player` / `fact_shotmap` / `fact_player_match_stats` / `fact_team_match_stats` / `fact_match_events` / `fact_match_lineup` / `fact_league_table` / `fact_season_player_stats`
- **i18n 中文映射维度**(新建,数据层护城河):新建映射表(如 `dim_team_i18n` / `dim_player_i18n`,或在现有 dim 表加中文列)+ 术语词典。把 FotMob 的 team_id / player_id 映射到中文名。**它是数据层的事,不依赖上层;一旦建好,Silver 聚合和前端展示自动带中文。**
- **Silver/聚合**(新建):联赛·球队·球员榜 + FootyStats 式统计(over/under、比分分布、分钟桶、BTTS、clean sheet、主场优势)。**全部从 Bronze 计算,不额外爬、不买数据。** 独立聚合脚本 + 持久化派生表,不是"现算 SQL"。
- **Gold/model**(新建):WDL 概率卡,用 5 赛季 xG 训练 + 按联赛校准。
- **Serving**(新建):FastAPI 读 Silver/Gold。**前端只读 API,不直连 DB。**

原则:一个数据源(FotMob),上层全是派生。FootyStats 式聚合是算出来的,概率卡是自己模型算的,都不是爬来的。

---

## 3. 🔒 免费 / 付费边界(同时决定变现和合规)

- **免费公开**(SEO 引擎,server components,必须可索引):排名 / 赛程 / 结果 / xG·xGOT 数据榜 / 球员榜
- **付费会员**(登录后 gate):over/under · 比分分布 · 分钟级进球(FootyStats 式)+ WDL 概率卡 + 研报
- 依据:博彩相邻内容既是最值钱付费点,又是要挡在公开 SEO 外的合规敏感内容。门禁一步同时解决变现 + 合规。

---

## 4. 🔒 门禁安全(不可妥协)

- 服务端门禁:付费数据只在服务端按 entitlement 校验后返回,**永不下发到客户端再用 CSS 隐藏**。
- 校验落在**数据访问层**(每个取数点都校验),不得只依赖 Next.js middleware(CVE-2025-29927:middleware-only 可被 x-middleware-subrequest 头绕过)。
- 付费内容页可对未订阅用户返回预览/骨架(可索引 + 引导),真实数值服务端 gate。

---

## 5. 🔒 Auth / 订阅 / 支付

- Auth + entitlement 逻辑放 **FastAPI**(复用 miaomiaodi.vip 的门禁模式)。
- 会员是**订阅制**(状态 + 到期),不是按次解锁。entitlement 按 subscription 有效期判断。
- 支付渠道:hupijiao / Alipay / WeChat(CNY)+ Stripe(海外)。
- 禁止 Clerk 及美国托管 auth(面向中国用户,墙 + 延迟)。

---

## 6. 🔒 目录结构(单一真源,禁止 frontend/backend 双套重复代码)

```
allwin/
├── backend/                    # FastAPI
│   ├── fotmob_client.py        # 抓取 + DB-ready 解析（已有）
│   ├── db.py                   # 集中 DB 路径(Path(__file__).resolve().parent.parent 动态定位)+ WAL（已有）
│   ├── schema.py               # 表结构定义（已有）
│   ├── init_db.py              # 建库（已有）
│   ├── ingest/                 # 落库脚本子目录  ← 现状偏差:ingest_match.py/ingest_league.py 还摊在 backend/ 根,待挪入
│   ├── scripts/                # 一次性修补脚本(fix_event_scores.py 等)
│   ├── i18n/                   # ← 新建:中文映射逻辑
│   ├── silver/                 # ← 新建:聚合层
│   ├── models/                 # ← 新建:WDL 训练 + 校准
│   ├── api_server.py           # ← 新建:serving + auth/entitlement
│   └── scheduler.py            # ← 新建:滚动更新
├── frontend/                   # ← 整个新建:Next.js App Router
│   ├── app/(public)/           #   免费 SEO 层,server components
│   ├── app/(member)/           #   登录后 gated 区
│   └── public/brand/           #   ← brand/ 挪到这里(前端引用 logo)
├── data/allwin.db
├── .env / .env.example / .gitignore
├── CLAUDE.md
├── ROADMAP.md
└── DESIGN.md                   # 品牌设计系统(配色/字体/组件规范),与 CLAUDE.md 平级作为设计宪法
```

- DB 路径已符合规范(动态定位,不写死绝对路径)。
- 未经同意不得再造第二套目录或把同一逻辑复制成 frontend/backend 两份(vip 的双套重复是维护地狱,不重蹈)。

---

## 7. 🔒 MVP 范围(做什么 / 明确不做)

**做**:
- 免费展示:排名 + 赛程 + 结果 + 球队/球员数据榜
- 全中文映射(队名/球员名/术语)+ 中文搜索
- WDL 概率卡(校准达标再公开,否则标 beta)
- 付费墙骨架(登录 + entitlement 检查,付费内容后填)

**不做(后续阶段,别提前建)**:
- 赔率 tab / 真实赔率采集 / 多联赛 / OU·波胆·AH / AI 问答 / 研报

---

## 8. 模型说明(避免误判)

- WDL 模型输入是球队 xG / 进球 / 近况 / 主客场,**不需要赔率当特征**。5 赛季 xG 即可训练。
- 赔率仅用于:①未来做"市场基准"校准对比 ②Phase 4 赔率复合查询。都不是 MVP、不是训练前提。
- 验证用 walk-forward(train 旧赛季 / test 最近赛季):报准确率 + log-loss + 校准曲线,校准可信才公开概率,否则标 beta。

---

## 9. 安全红线(不可妥协)

- 凭证(ThorData 代理 / PAT / JWT_SECRET 等)只进 `.env`,绝不硬编码进 `.py`、不进 git、不写进命令行或对话。
- `.env` / `*.db` / 模型文件 / `.pem` / `.venv` / `__pycache__` / `node_modules` 一律进 `.gitignore`。commit 前跑 `git status` 肉眼确认无敏感文件。
- 不上传 / 不索取私钥。

---

## 10. 验证守则

- 前端改动:`npm run build` 0 error → `pm2 restart` → 提醒 Cloudflare purge + 隐私模式核验。
- 后端改动:相关 API `curl` / `pytest` 通过才算完。类型检查通过 ≠ 完成。
- 上服务器部署三必查:`pm2 describe` 的 exec cwd / `nginx -T` 的 proxy_pass / `curl` 域名指纹,三路指向同一目录才算已部署。
- 验证只贴终端真实 stdout,不写"已完成/已同步"摘要。

---

## 11. 当前状态(审计结论,新会话进来必读)

- **只完成了 Bronze 层**(爬虫 + 9 张原始表),质量过关:2280 场比赛,跨表一致性验证通过。
- **Silver / Gold / Serving / Frontend,以及贯穿的 Auth / 付费门禁,全部空白**——不是做错,是还没排到。
- 待处理的结构项:
  - `ingest_match.py` / `ingest_league.py` 待挪入 `backend/ingest/` 子目录
  - `ROADMAP.md` 缺失,待补(内容我另存)
  - `.claude/launch.json` 指向临时会话路径,清理掉(非正式配置)
  - 本次会话改动待 commit(message 加 `wip:` 前缀)

---

## 12. 🔒 任务顺序(往上盖四层的顺序,不得跳序)

**中文映射 → Silver → FastAPI serving → 前端**

1. **中文映射**:唯一真护城河,且是数据层的事、不依赖上层。先做,后面 Silver + 前端自动带中文,避免返工。
2. **Silver**:把"现算 SQL"升级成正式聚合表。前端数据榜/统计全靠它。
3. **FastAPI serving**:Silver 有了才有东西可 serve。前端与数据之间的唯一通道。
4. **前端**:前三层就位后,照 DESIGN.md 接 API 一次做对。

**禁止先做前端**:现有样稿是写死假数据("阿森纳"是手写的),在无中文映射/无 Silver/无 API 时扑前端 = 空中楼阁,三层一变全返工。

**竖切一刀选项**(想早点看到成果时):中文映射 + Silver 完成后,可先做**一个页面**(如排名榜)打通「DB→Silver→API→前端」全链路验证架构,再批量做其余页面。比横向一层层盖更早暴露问题。

---

## 13. 模型使用约定(合理分配算力)

| 环节 | 用哪档模型 |
|---|---|
| 架构自检 / 重构评估 / 对照 🔒 条款查漂移 | 强模型(Opus / Fable 级) |
| 复杂逻辑设计(数据分层、Silver 聚合边界、模型校准、walk-forward 评估) | 强模型 |
| 上线前对抗性 QA(adversarial prompt chains) | 强模型 |
| 日常前端组件 / 样式微调 / CRUD | 一般模型(Sonnet/Haiku)即可 |
| 批量重复改动(改路径、改 import) | 一般模型即可 |

原则:强模型留给"需要跨文件推理和架构判断"的活;日常写页面调样式不用纠结模型。架构纪律(本文件)比模型强弱更决定成败。

---

## 14. 遗留物归属(审计发现,已纳入宪法)

- `DESIGN.md`(设计系统):**保留**,放项目根,作为设计宪法(§6 已列)。
- `brand/`(logo 资源):**保留**,建 frontend 后挪到 `frontend/public/brand/`;在此之前先留根目录。
- `fact_league_table` / `fact_season_player_stats`:**是 Bronze 合法成员**(FotMob 原始榜单),已补进 §2 Bronze 清单。
- `.claude/launch.json`:临时路径,清理。
