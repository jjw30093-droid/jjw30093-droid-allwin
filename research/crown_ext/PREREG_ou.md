# PREREG OU — 五大联赛 Crown 大小球(OU)信号研究(预注册)

状态:**草案,待站长审核**。审过之前不跑任何特征与目标的关系分析。审过之后只允许追加"修订记录"。

日期:2026-09-28。代码:`research/crown_ext/`。数据只读(`mode=ro`);产物在仓库外
`~/research_out/crown_ext/`。继承 `research/ah_signals/PREREG.md` 的锁定口径:样本、映射、冷启动、
窗口(8 场 EWMA,半衰期 4 场;5/10 场敏感性)、对手调整、NULL 三分法、去留规则、水位过滤、截断测试。

---

## 1. 样本

- 只用 25/26,`Match_Round>3`,有 Crown OU 收盘线(Phase 1:1749/1752 有收盘线)→ 候选 ≈1605;
  滚动特征需 ≥3 场严格更早比赛。26/27 完全不接触。
- Crown OU 已通过水位格式核验(`docs/data-sources.md` §2.8);单一主盘口(无多线时点)。
- N<100 的分组标"样本不足,不可靠"。

## 2. 目标(均已在 Phase 1 定义并验证结算)

| 目标 | 定义 | 用途 |
|---|---|---|
| **主目标 `r_over`** | 按 Crown 收盘 OU 线与收盘大球水位 w 下注大球 1 单位的实际盈亏:`s = settle_over(总进球, T)`,`r_over = s·w`(s>0)或 `s`(s≤0),含赢半/输半/走水 | 主检验 |
| 次目标 `g_excess = goals − λ_close` | `λ_close` 由 `p_eff(Poisson(λ), T) = 去水大球概率` 单 Poisson 反推(`ou_skew_check.py`,截断 0..15) | 次检验 |
| 不用 `margin_ou` | Phase 1 验证:其正均值(+0.129)来自总进球右偏(理论 +0.165,配对差 CI 含 0),不是市场偏差 | — |

## 3. 检验模型

- **主检验(每个特征一条)**:`r_over ~ feature`,HC1 稳健 SE,双侧 p;报告系数、SE、t、p、q、
  每 1 SD 特征对应的 `r_over` 变化(= ROI 百分点/SD)及其 95% CI、效应上限(CI 绝对值上界)、
  与收盘线 T 的相关系数、方向是否与预期一致。
- **次检验**:`g_excess ~ feature`,同上,报告"球/SD"。
- 特征取 **双方之和**(主队值 + 客队值),8 场 EWMA;敏感性:5/10 场、对手调整版(两队各自调整后求和)、
  仅上半场版(能从队级 `extra_json` 构造的),不进 FDR。
- 探索性(不进 FDR,注明"Phase 1 已观察"):德甲、2.75 档、西甲、≥3 档买小等 Phase 1 分组。

## 4. O 确认族(≤12 个;当前 12 个,O11/O12 为提议,请审)

`for` = 本队,`against` = 对手;单场值先按队计算,再对本场主客两队分别做 8 场 EWMA,最后**相加**。

| # | 特征(双方之和) | 单场值(每队) | 来源字段 | 预期方向 | 理由 |
|---|---|---|---|---|---|
| O1 | 滚动 npxG(进攻)之和 | `expected_goals_non_penalty(for)` | team extra_json | + | 两队进攻创造质量高 → 总进球多 |
| O2 | 滚动 npxGA(防守)之和 | `expected_goals_non_penalty(against)` | team extra_json | + | 两队防守漏机会多 → 总进球多 |
| O3 | 滚动运气残差之和(攻守合计) | `(Goals_for − xG_for) + (Goals_against − xG_against)` | team `Goals` + `expected_goals` | **−** | 市场按实际进球定价,超额进球/失球应均值回归 |
| O4 | 滚动定位球 xG 之和 | `expected_goals_set_play(for)` | team extra_json | + | 定位球威胁是被低估的进球来源(先验弱) |
| O5 | 滚动大机会之和 | `big_chance(for)` | team extra_json | + | 高质量机会数量 |
| O6 | 滚动射门数之和 | `total_shots(for)` | team extra_json | + | 射门量(对照:预期被 xG 类吸收) |
| O7 | 滚动角球之和 | `corners(for)` | team extra_json | 双侧 | 施压频率与总进球关系不明 |
| O8 | 滚动红牌率之和 | `red_cards(for)`(每场红牌数) | team extra_json | + | 红牌后总进球通常上升(11 人一方增加的进球多于 10 人一方减少的);先验弱 |
| O9 | 滚动传中占比之和(代理) | `accurate_crosses(for) / accurate_passes(for)` | team extra_json | 双侧 | 风格变量;传中依赖与总进球关系不明 |
| O10 | 滚动实际总进球之和 | `Goals_for + Goals_against`(本队比赛的总进球) | team `Goals` | **−** | 检验市场是否过度依赖实际进球:若过度依赖,近期高进球的对阵盘口偏高 → 买大亏 |
| O11(提议) | 滚动 xGOT 之和 | `expected_goals_on_target(for)` | team extra_json | + | 射正质量比 xG 更接近实际进球(主研究 B2 的 OU 版本) |
| O12(提议) | 滚动禁区触球之和 | `touches_opp_box(for)` | team extra_json | + | 比赛"进入禁区"的频率,代表开放程度/节奏 |

**去留规则(写死,只看字段核验)**:O4(`expected_goals_set_play`)、O8(`red_cards`)、O9(`accurate_crosses`、
`accurate_passes`)、O11(`expected_goals_on_target`)、O12(`touches_opp_box`)若在 Phase 2 核验中
key 缺失+null 率 >5% 或无法构造 → 移出,BH 按剩余数计。主研究 Phase 2 已核验这些 key 在 Period=All
上缺失率均为 0.0%(`red_cards`、`expected_goals_set_play`、`total_shots` 本次需补核,同一规则);
`red_cards` 的 0 值是真实 0。

## 5. T 族(OU 版本;各自独立 BH,分母 = O 族剩余数)

| 子族 | 目标 | 回归 | 方向 |
|---|---|---|---|
| T-a_ou | `r_over_T24` = 按 T-24h Crown OU 线与 T-24h 大球水位结算的大球盈亏 | `r_over_T24 ~ feature` | 同 §4 |
| T-b_ou | `Δline_ou = (close_T − T24_T)/0.25`(档) | `Δline_ou ~ line_T24 + feature` | 同 §4(特征利大 → 线上调) |

T-24h 线 = `observed_at ≤ kickoff−24h` 最后一条(25/26 缺失预计 0);T-b_ou 通过 BH 的特征另报按方向在
T-24h 下注的描述性 ROI(T-24h 线+水位 / 收盘线+收盘水位)、保本胜率与 CLV(定义同 PREREG_phase4)。

## 6. 多重比较与稳健性

- O 族、T-a_ou、T-b_ou **各自独立** BH,q=0.10。
- 5 条稳健性条件(通过 BH 的特征逐条报告;不适用标 n/a):
  1. 5/10 场窗口同号;2. 对手调整版同号;3. **Bet365 OU 收盘线与水位**下重算目标同号(Bet365 OU
  25/26 有收盘 1739/1752);4. ≥4/5 联赛同号;5. 上下半季(轮 ≤19 / >19)同号。
- 报告用语只写数字、N、CI、p、q;不显著的特征给效应上限(ROI 百分点/SD)。

## 7. 功效

按 `r_over` 的实际 SD 与各特征实际 N 计算:每 1 SD 特征可检出的最小效应
`MDE = (z_{α/2} + z_{0.80}) · SD(r_over) / √N`,以 ROI 百分点/SD 表示(α=0.05 与 BH 最严档 0.10/12)。
Phase 1 全买大盈亏的 SD 约 0.95(赢 ≈ +0.94、输 −1 的两点分布)→ N≈1600 时 MDE ≈ 2.5×0.95/40 ≈
**6.6 个 ROI 百分点/SD**(BH 最严档 ≈ 8.1)。Phase 2 用实际值重算并贴出。

## 8. 字段核验、闸门与截断(与主研究 Phase 2 / Phase 4 相同)

- Phase 2:NULL 三分法(含 `red_cards`、`expected_goals_set_play`、`total_shots` 补核)、split-half 持续性
  (奇/偶轮,r<0.3 标低持续性)、按比赛日分组的全量截断测试(浮点严格相等,覆盖 O 族全部变体与 T 族目标)。
- Phase 3(O 族)/Phase 4(T 族)按闸门顺序:闸门 1 覆盖报告(可用 N、缺失原因)→ 闸门 2 截断 → 闸门 3
  恒等式(`r_over` 与 `settle_over` 逐场一致;`r_over_T24` 同)→ 才跑回归。任一闸门不通过即停。
- 每阶段 `git status`、只提交任务文件、`wip:` 前缀;原始 stdout 全文贴出。

## 9. 修订记录

- 2026-09-28 初稿(待审)。
