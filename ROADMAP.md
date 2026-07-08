# ROADMAP — 欧赢 / allwin

> 执行路线图。架构约束见 `CLAUDE.md`(架构宪法);本文件只管"什么时候做什么、什么算做完"。
> 当前阶段:英超 MVP。

---

## 当前进度
| 层 | 状态 |
|---|---|
| Bronze(9 表,近 5 赛季英超,2280 场) | ✅ 完成,跨表一致性验证过 |
| i18n 中文映射 | ⬜ 未开始 |
| Silver 聚合 | ⬜ 未开始 |
| Gold 模型(WDL) | ⬜ 未开始 |
| Serving(FastAPI) | ⬜ 未开始 |
| Frontend | ⬜ 未开始 |
| Auth / 付费门禁 | ⬜ 未开始 |

一句话:地基(Bronze)实了,往上盖四层。没有需要推倒的东西。

---

## Phase 1 — 英超 MVP(当前)

### 主线:数据 → 免费展示(串行,不得跳序 — 见 CLAUDE.md §12)
| 步 | 做什么 | DoD(验收) |
|---|---|---|
| **1.0 结构收尾** | 覆盖新 CLAUDE.md;`ingest_*.py` 挪入 `backend/ingest/`(让 AI 改 import,别手挪);补 `ROADMAP.md`;清理 `.claude/launch.json`;commit(`wip:`) | ingest 挪后冒烟落库仍通(贴真实 stdout);`git status` 无 `.env`/`*.db` |
| **1.1 中文映射** | 建映射表 + 灌英超 20 队 + 现有球员中文名 + 术语词典(护城河,最先做) | DB 能按 team_id/player_id 返中文名;覆盖率报告贴真实数字 |
| **1.2 Silver 聚合** | `backend/silver/`:把"现算 SQL"升级成持久化派生表——联赛/球队/球员榜 + over/under、比分分布、分钟桶、BTTS、clean sheet、主场优势(全从 Bronze 算) | 聚合脚本跑通;抽查几张榜的数字与 Bronze 手算一致(贴 stdout) |
| **1.3 FastAPI serving** | `backend/api_server.py`:读 Silver(+后续 Gold),暴露免费层 API;前端只读 API,不直连 DB | 关键 endpoint `curl` 通,返正确中文数据 |
| **1.4 前端免费公开层** | `frontend/` Next.js;`app/(public)`:排名/赛程/结果/数据榜,server components,可索引;照 DESIGN.md | `npm run build` 0 error;渲染真实中文数据无缺失 |

**竖切一刀**(想早点看到成果):1.1 + 1.2 完成后,先只做**排名榜一个页面**打通 DB→Silver→API→前端全链路,验证架构跑得通,再回来批量做 1.3/1.4 其余页面。

### 并行线:模型(依赖 Bronze,不依赖前端,可与主线同时推)
| 步 | 做什么 | DoD |
|---|---|---|
| **1.M WDL 模型** | `backend/models/`:5 赛季 xG 训练 WDL(**不需赔率当特征**)+ 按联赛校准 | walk-forward(train 旧赛季 / test 最近赛季):准确率 + log-loss + 校准曲线;校准达标才可公开,否则标 beta |

### 收尾:需模型 + serving + 前端就位
| 步 | 做什么 | DoD |
|---|---|---|
| **1.5 概率卡** | 前端付费区渲染 WDL 概率卡(校准达标公开,否则 beta) | 概率卡走服务端 gate,未订阅返预览 |
| **1.6 付费墙骨架** | 登录 + entitlement 校验(FastAPI,订阅制);付费内容占位,后填 | 付费数据服务端 gate,DevTools 看不到明文;订阅态判断正确 |

**MVP 完成 = 免费公开层上线(SEO 引擎)+ WDL 概率卡(校准达标)+ 付费墙骨架就位。**

---

## Phase 2 — 赔率 + 付费内容
1. 移植 vip 赔率管道(`odds_fetcher` / `titan_odds_spider` / `nowgoal` + `map_fotmob_to_nowgoal`),接英超,回填 5 赛季历史赔率
2. 前端加赔率 tab
3. 模型加"市场基准"(de-margined)校准对比
4. 填充付费内容:深度聚合 + 研报(移植 `report_generator` / `qwen_client`)

## Phase 3 — 五大联赛
1. 西甲 / 意甲 / 德甲 / 法甲:复用管道,league 扩容
2. 各联赛中文映射补齐 + 分别校准
3. OU 校准达标后上线

## Phase 4 — 亚洲 / 欧战 / 小联赛 + 盘口深化 + AI
1. J联赛 / K联赛 / 亚冠 + 欧冠 / 欧联 / 欧协联 + 荷 / 瑞 / 挪 / 葡(小联赛校准不过只出数据、不出概率)
2. 波胆 / BTTS / AH 标 beta,验证够再转正
3. AI 问答(千问)三档:A 意图+模板 → B 受限 NL2SQL(准确率达标才放)→ C 赔率复合查询(需 Phase 2 的赔率历史)

---

## 关键执行风险(架构护栏见 CLAUDE.md §3/§4/§8/§10)
| 风险 | 缓解 |
|---|---|
| 跳序先做前端 → 三层一变全返工 | 守 §12 顺序;想看成果走竖切一刀 |
| 中文映射拖到最后 → 前端返工 | 列为 1.1 最先做 |
| 模型校准不过硬发概率 → 砸招牌 | walk-forward 达标才公开,否则 beta |
| 缺赔率就不敢训模型 | 模型不需赔率训练;赔率 benchmark 是 Phase 2 锦上添花 |
| frontend/backend 双套重复(vip 教训) | 单一真源,CLAUDE.md §6 |
| FotMob 反爬改版 | 爬虫需实跑验证,代理保活 |

---

## 下一步
Phase 1 → **1.0 结构收尾** → **1.1 中文映射**开工。
