# 赛前手机端数据可视化 Phase 0–3 实施与自验报告

> 本文件是开发方自己的实施记录与自验结果,**不是独立复核报告**——所有结论
> 都是同一次会话里写代码、写测试、跑测试的同一批人产出,没有第三方复核。
> 状态只能写 `READY_FOR_INDEPENDENT_RE_REVIEW`,不得自称"已通过独立复核"。

范围:四大联赛(英超 47 / 西甲 87 / 德甲 54 / 意甲 55)即将开赛的第 1 轮,
39 场,39/39 有精确开球时间。完整方案见
`/Users/wanglujun/.claude/plans/sorted-gathering-tower.md`
(PREMATCH_MOBILE_DATA_VISUALIZATION_V2)。当前状态汇总见
`docs/current-state.md` §43/§44/§45,本文件只做交叉核对与结论摘录。

**本文件覆盖三轮工作**:§1–§5 是首版 Phase 0–3(2026-08-15);§6 是第一次
验收返工(2026-08-16,针对首版验收发现的 8 项真实缺陷);§7 是第二次
验收返工(2026-08-16,独立复核第二轮确认 §6 仍有 2 个 P1 未真正解决)。
每一轮报告都曾把完成度写得比实际更完整,后一轮如实记录纠正过程,不重写
历史,只标注哪些结论被推翻——§6 推翻了 §1–§5 的部分结论,§7 又推翻了
§6 的部分结论(Matchup normalization、Fallback comparability)。

## 1. Phase 0 四个正确性缺陷——全部修复,均有 RED→GREEN 永久测试

| 缺陷 | 修复文件 | 回归测试 |
| --- | --- | --- |
| 风格象限方向语义未传播 | `team_style_preview.py`、`TeamStyleQuadrant.tsx` | `test_team_style_preview.py::test_direction_semantics_propagated_and_quadrant_labels_correct` |
| 部分 xG 被补 0 重新归一化 | `MatchDataModules.tsx` | `attack-source-card.test.tsx` |
| 门将 xGOT 的 NULL 当 0 | `player_form.py` | `test_player_form.py`(3 条) |
| 时间边界用 Date、排序无 tiebreaker | `match_preview.py`、`team_style_preview.py` | `test_team_style_preview.py`(2 条新增) |

## 2. Phase 1 方法论——三项按时间切分的样本外回测,结论如实采纳/拒绝

### 2.1 窗口长度(`window-length-validation-v1.json`)

**口径限定**:这是一次按 `CUTOFF=2025-01-01` 切分的样本外回测,验证目标
只取 cutoff 之后,N=5/N=10 两组窗口都要求凑满历史场次才纳入比较(不是
同一数据集调参选最优)。**这是"当前这一次回测支持 N=10"的结论,不是
独立最终验证,也不代表 N=10 是所有场景下的最优窗口长度**——换一批切分
时间点或更长历史区间重跑,结论可能变化。

| 联赛 | 样本 | corr(N=5) | corr(N=10) | 本次回测结论 |
| --- | ---: | ---: | ---: | --- |
| 英超 | 1123 | 0.2493 | 0.2982 | N=10 不弱于 N=5 |
| 西甲 | 1136 | 0.3984 | 0.4133 | N=10 不弱于 N=5 |
| 德甲 | 924 | 0.3747 | 0.4047 | N=10 不弱于 N=5 |
| 意甲 | 1142 | 0.2752 | 0.3221 | N=10 不弱于 N=5 |

四联赛在这一次回测里都支持 N=10 → 当前默认值采用 N=10。

### 2.2 对手强度校正(`opponent-adjustment-validation-v1.json`)—— DEFERRED,未接入产品

方法:PIT-safe(排除目标比赛及之后),升班马/小样本收缩
(`w=n/(n+8)`),进攻/防守/比例三类分别验证。

| 类别 | 四联赛结论 | 判定 |
| --- | --- | --- |
| 进攻(创造 xG,按对手场均让出校正) | 全部提升 | 方法论验证通过,**但未接入任何图表** |
| 防守(讓出 xG,按对手场均创造校正,方向相反) | 全部提升 | 方法论验证通过,**但未接入任何图表** |
| 比例(控球率) | 全部四联赛都变差 | **不采用**,生产模块不提供该函数 |

`backend/queries/opponent_adjust.py` 有函数实现和永久测试,但截至本报告,
四张核心图**没有一张调用它**——展示的都是未校正窗口均值。这是首版报告
措辞不够清楚的地方(写成"已验证并落地",容易被读成"已经在生产使用"),
验收返工已在模块 docstring 里补上明确的 DEFERRED 状态说明。

### 2.3 主客场百分位基准(`venue_baseline.py`)—— 部分接入

真实数据冒烟:Brighton 英超主场创造 xG,百分位 45(样本 22 队)。

`league_percentile()`(球队级主客场百分位,面向"高于联赛 X% 的球队"这类
措辞)本身**DEFERRED,未被任何图表调用**。但验收返工二新增的
`matchup.py::league_situation_baseline()`(图4"关键对位"排序的基准计算)
复用了本模块的 `_league_team_ids`/`_lookback_floor`/`_COMPATIBLE_TIERS`
(mixed 档位排除规则)——**这部分底层基础设施已经在生产路径上**,只是
面向球队整体的百分位函数还没有直接消费者。

## 3. Phase 2 四张核心图(首版)——真实第 1 轮比赛浏览器验证

| 图 | 数据齐全态验证比赛 | 升班马降级态验证比赛 |
| --- | --- | --- |
| 进攻转化链 | 塞维利亚 vs 巴列卡诺(5868019) | 阿森纳 vs 考文垂(5795363) |
| 控球与场面控制 | 同上 | 同上 |
| 防守承压与限制能力 | 塞维利亚 vs 巴列卡诺 | 阿森纳 vs 考文垂 |
| 本场攻防对位 | 塞维利亚 vs 巴列卡诺、拜仁 vs 斯图加特(5881143) | 阿森纳 vs 考文垂、埃尔沃斯堡 vs 勒沃库森(5881146) |

验证方式:`get_page_text`/`javascript_tool` 提取真实渲染文本核对数字与
后端 `/api/v1/matches/{id}/preview` 响应一致;控制台无报错。

## 4. 首版测试与构建(真实命令与退出码,2026-08-15)

```bash
cd /Users/wanglujun/projects/all-win && .venv/bin/python -m pytest tests/backend --ignore=tests/backend/test_backup_restore.py -q
# exit=0, 1497 passed, 5 skipped, 0 failed
```
```bash
cd /Users/wanglujun/projects/all-win/frontend && npx tsc --noEmit && npx eslint . && npx vitest run && npm run build
# 全部干净;vitest 27 files / 160 tests passed
```
```bash
cd /Users/wanglujun/projects/all-win/frontend && CI=1 npx playwright test
# 33 passed(28 既有 + 5 新增)
```

## 5. 首版已知缺口(2026-08-15 版本的记录,§6 是后续处理结果)

1. Phase 1.4/1.5 的对手校正与主客场百分位**未接入四张核心图**。
2. 第二梯队三个模块未实现。
3. 文案生成是每张图各自的规则函数,不是共享规则引擎。
4. 正文字号沿用既有设计系统 11.5–13px 惯例,未按方案字面"≥14px"重做。

**首版报告本身存在的问题(验收返工发现,不是又一个功能缺口,是报告
诚实度问题)**:把"方法论验证通过"和"已接入产品"混着写(§2.2/2.3),
本文件标题原为"独立复核报告"但实际只是同一批人的自验,窗口长度结论
写成确定性的"更优"而非回测口径限定的"不弱于"。§6 记录纠正过程。

## 6. 验收返工(2026-08-16):8 项真实缺陷逐项处理

| # | 缺陷 | 处理 |
| --- | --- | --- |
| 1 | 进攻转化链只有产量,没有转化率 | 新增 `shots_per_100_box_touches`/`shot_on_target_rate`/`xg_per_shot`/`xgot_per_sot`,同场配对相除,分母为 0 给 None,`volume_keys`/`conversion_keys` 显式分组 |
| 2 | 关键对位用不同类别原始 xG 排序,运动战天然占优 | 新增 `league_situation_baseline()`,只有进攻方产出、防守方让出都高于同联赛同场景基准才合格,排序改用相对基准的超出比例(量纲无关) |
| 3 | mixed/partial/full 被直接比较 | 新增 `comparability.ts::tiersComparable()`,四张图 summary 函数在 tier 不兼容时输出"样本口径不同,暂不作高低判断";`venue_baseline.py` 的分布计算排除 mixed |
| 4 | 新图正文大量 10.5–12.5px | 正文/标签/摘要提到 14px,关键数值提到 18px,次要说明提到 12px;新增 Playwright computed-style 检查 |
| 5 | 页面仍展示近期胜平负卡 | 删除 `FormList`、`formSummary`、QuickView 的近 N 场摘要,只保留推荐发布状态提示 |
| 6 | opponent adjustment/percentile 被写成"已完成" | 两个模块 docstring 补充明确 DEFERRED 状态说明(见 §2.2/2.3) |
| 7 | 施工方文档自称"独立复核报告" | 本文件改名+改标题为"实施与自验报告",页首加粗声明不是独立复核 |
| 8 | 窗口长度声称"已验证最优" | `window.py` 注释与本文件 §2.1 改为"当前时间切分回测支持 N=10",不再用"验证 N=10 优于/更优"这类确定性措辞 |

全部 8 项均先写 RED 永久测试(记录旧实现的真实失败),确认失败后再实施
修复,修复后确认 GREEN。详细的测试列表、真实数据验证结果、逐项
VALIDATED/FIX_REQUIRED 结论见 `docs/current-state.md` §44。

**§6 的自我评估过于乐观**:§6 把"关键对位排序改用相对基准的超出比例"
和"tier 不兼容不生成比较结论"都写成 VALIDATED 已完成,但独立复核第二轮
(§7)用真实数据(比赛 5868022)和更严格的组合测试证明这两项都还有真实
缺陷——前端仍然靠 `own_xg_pg ?? own_shots_pg` 猜量纲,`tiersComparable()`
仍然把 venue_full/venue_partial 和 mixed/mixed 当同一口径。§44.6 的
Matchup normalization / Fallback comparability 两行 VALIDATED 结论已在
`docs/current-state.md` §44.6 原表中标注推翻。

## 7. 第二次验收返工(2026-08-16):独立复核第二轮,2 个 P1 修复

| # | 缺陷 | 处理 |
| --- | --- | --- |
| 1 | 非 box 类型量纲错配:`own_xg_pg ?? own_shots_pg` 在 xG 不完整时把射门次数当 xG 用去跟 xG 基准比,真实复现(比赛 5868022/球队 10205,运动战 own_shots_pg=6.1 > own_baseline_avg=0.696 恒成立) | 后端 `team_matchup_profile()` 新增显式 `comparison_metric`/`own_comparison_value`/`conceded_comparison_value`/`own_baseline_value`/`conceded_baseline_value`/`comparison_complete` 字段,替换含糊的 `own_baseline_avg`/`baseline_available`;前端只读这些字段,不再有任何 `??` 量纲猜测;真实比赛复现验证通过 |
| 2 | tier 可比性判定过宽:venue_full/venue_partial 视为同一口径、mixed/mixed 视为同一口径 | `comparability.ts::tiersComparable()` 收紧为只有精确同一个 tier(venue_full-venue_full 或 venue_partial-venue_partial)才可比;`league_situation_baseline()` 新增必填 `tier` 参数,只用精确同 tier 的参考队,不再"只要不是 mixed/unavailable 就算兼容" |
| 3(文案) | `MatchupSection.tsx` JSX 里出现字面量 `**同时**`,浏览器直接显示两个星号 | 删除多余的 markdown 星号,只保留正常中文"同时" |
| 4(文案) | tier 不兼容时退回笼统的"可比数据不足",不说明真正原因 | 改为显式输出 `INCOMPARABLE_NOTE`("样本口径不同,暂不作高低判断。"),与其余三张图统一 |
| 5(文档) | 性能只写"较贵",没有实际测量值 | 用 `sqlite3.Connection.set_trace_callback` 对真实比赛 5868019 实测:core SELECT=493、odds SELECT=3、冷≈648ms、热≈197~200ms、响应体≈20.3KB,写入 `docs/current-state.md` §44.4,与独立复核报告给出的对照测量(499/3/784ms/201ms/18.7KB)量级一致 |
| 6(文档) | §44.6 把 Matchup normalization / Fallback comparability 写成 VALIDATED,但两项仍有真实缺陷 | §44.3/§44.6 原文标注推翻并指向 `docs/current-state.md` §45;§45.6 给出取代后的逐项结论 |

两个 P1 均先写 RED 永久测试记录旧实现真实失败(前端 `matchup-section.test.tsx`
用真实比赛 5868022 的数字形状构造反例、`comparability.test.ts` 覆盖全部
tier 组合、后端 `test_matchup.py::TestLeagueSituationBaseline` 用极端值
构造 venue_full/venue_partial 混淆反例),确认失败后再实施修复,修复后
确认 GREEN。真实数据验证(比赛 5868022,只读 `mode=ro`,未修改真实数据库)、
完整测试列表(collected=1507/passed=1506/failed=0/skipped=1)、最终逐项
VALIDATED/FIX_REQUIRED 结论见 `docs/current-state.md` §45。

状态:**READY_FOR_INDEPENDENT_RE_REVIEW**。不得自行输出
`PREMATCH_MOBILE_DATA_VISUALIZATION_V2_ACCEPTED`。
