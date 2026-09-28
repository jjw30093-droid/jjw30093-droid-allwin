# PREREG H-M2 — 前瞻检验:反向跟随 Crown 收盘前线变(新数据,独立于主研究)

状态:**定稿(站长审定修改后,2026-09-28)。** 定稿 commit 时间戳(UTC)= **2026-09-28T07:20:00Z**
(`HM2_CUTOFF_UTC`,commit 的 author/committer 时间即此值)。此后本文件只允许追加"修订记录";
规则常量冻结,任何改动 = 新假设、新编号。记录/评估脚本启动时校验本文件与自身的哈希(§5.4)。

## 1. 假设

**H1(单侧)**:在 Crown 亚洲让球主盘口于 T-24h 到收盘(kickoff−10min)之间发生变动的比赛中,
按收盘线、Crown 收盘水位押线移动的反方向、平注 1 单位,**每注平均盈亏 > 0**。
**H0**:每注平均盈亏 ≤ 0。

来源:主研究 Phase 4 的 M2(`margin ~ closing_line + M2`,coef −0.166/档,q=0.051)与 M2 诊断中
未预注册的反向下注描述(25/26,N=567,ROI +4.06%,bootstrap 95% CI [−3.34, +11.23])。M2 未通过
主研究的水位感知检验(证据不足,未证实),本假设只能用**尚未发生的比赛**重新检验;25/26 与
定稿前的 26/27 比赛一律不计入。

## 2. 下注规则(冻结)

| 项 | 定义 |
|---|---|
| 市场/公司 | NowGoal `bronze_ng_odds_snap`,`market='ah'`,`company_id='3'`(Crown);只有一条主盘口,不做选线 |
| 水位过滤 | `home+away ∉ [1.60, 2.10]` 或单边 `<0.30` 的 snap 不参与取点(同 PREREG §6.3) |
| T-24h 线 | `observed_at ≤ kickoff−24h` 的最后一条;首条 snap 晚于 kickoff−24h → **不下注**(`no_t24`) |
| 收盘线 | `observed_at ≤ kickoff−10min` 的最后一条;距 kickoff >120 分钟 → **不下注**(`no_close`) |
| 触发 | `Δline = closing_line − line_T24 ≠ 0`(库内符号:`line>0` = 主让;Δline>0 = 线向主队方向移动);`Δline = 0` → 不下注(`no_move`) |
| 方向 | `Δline > 0` → 押**客**;`Δline < 0` → 押**主** |
| 价格 | 收盘线 + 收盘 snap 的该方向 Crown 水位(港式赔率 w),平注 1 单位 |
| 结算 | `settle_home(home−away, closing_line)`(已与 `reco_settlement_math` 逐场核对);押客取负;盈亏 = `s·w`(s>0)或 `s`(s≤0),赢半/输半 ±0.5,走水 0 |
| 每场 | 至多 1 注;只计 `status='Finish'`、`kickoff_precision='exact'`、xref `auto_ok/confirmed` 的比赛 |

Δline 幅度不加权、不设门槛、不按联赛/半季筛选(这些在 25/26 描述里都看过,不得进规则)。

## 3. 样本(冻结,按时间划定)

- 只计入五大联赛(47/53/54/55/87)、赛季 26/27 及以后、**`kickoff_at_utc > 2026-09-28T07:20:00Z`**
  的比赛;不再按轮次划定。定稿时各联赛下一轮首场开球为 2026-10-09/10,西甲第 6 轮延期场
  (2026-10-21)因开球晚于定稿时间**计入**。
- 25/26 全部与定稿时间之前开球的 26/27 比赛不计入;杯赛/欧战无 Crown 数据,不计入。

## 4. 评估(冻结)

- **触发点**:累计**下注数**(有结算结果的注)达到 **1600** 时做且仅做一次检验,只对按 kickoff
  排序的前 1600 注。期间只记录计数;记录脚本不输出命中率/ROI;评估脚本在 N<1600 时拒绝运行,
  无覆盖开关;评估只允许运行一次(`hm2_state.json` 记 `evaluated_at`)。
- **主检验(以此为准)**:单侧 t 检验,H1: 每注平均盈亏 > 0,α=0.05;盈亏按实际结算(含赢半、
  输半、走水,走水计 0 且**计入分母**)。同时报告 bootstrap(10000 次,seed 20260928)的单侧
  p 值与 95% CI,仅作参照。t 检验 p ≤ 0.05 → "H-M2 在前瞻样本中获得支持(α=0.05,单侧)";
  否则 → "未获支持"。只有这两种结论。
- **次要报告**(不参与判定):命中率 vs 保本胜率的精确二项单侧检验(命中 = 盈亏>0,失手 =
  <0,走水剔除;`p0` = 各注 `1/(1+w_i)` 均值);按联赛/方向(押主/押客)拆分的 N 与 ROI;Δline
  幅度分布;不下注原因计数。
- **功效(按主检验事先算清)**:每注盈亏的 SD 取 25/26 反向下注描述的 0.885(由其 bootstrap
  95% CI 半宽 7.285%/1.96×√567 反推)。N=1600:SE = 0.885/40 = 0.0221;单侧 α=0.05、80% 功效可
  检出的最小 ROI = (1.645+0.842)×0.0221 ≈ **+5.5%**;真实 ROI 为 +4% 时功效 ≈ Φ(0.04/0.0221 −
  1.645) = Φ(0.16) ≈ **56%**。评估脚本用实际 SD 重算并贴出。
- **到达 1600 注的预期时间**:25/26 Δline≠0 的比例 35.3% → 每季约 620 注;26/27 定稿后剩余
  约 1550 场 ≈ 545 注;累计 1600 注预计要到 **2028/29 赛季中段**。

## 5. 实现(只读、仓库外记录、零写入生产库)

### 5.1 代码与数据

- `research/ah_signals/hm2_log.py`(记录)、`research/ah_signals/hm2_evaluate.py`(评估);经 git
  push → 服务器 `git pull`;数据库只经 `common.open_ro()`(`mode=ro` + `PRAGMA query_only`)。
- 记录目录 `/home/ubuntu/research_out/ah_signals/hm2/`(仓库外):
  - `hm2_bets.csv`:每场一行(含不下注场次及原因):`logged_at, match_id, league_id, round,
    kickoff_at_utc, home, away, t24_at, line_t24, t24_home_w, t24_away_w, close_at, closing_line,
    close_home_w, close_away_w, dline, side, water_used, home_score, away_score, settle, payoff, reason`;
  - `hm2_state.json`:冻结哈希(§5.4)、累计场次/下注数/不下注原因计数、最近运行时间、git sha、
    `evaluated_at`;
  - `hm2_incidents.csv`(可选,站长手写):数据源变化的时间区间与说明(§6)。
  - 幂等:按 `match_id` 去重,已记录的行永不改写;只追加新的 `status='Finish'` 比赛。
- 记录时机:完赛后补记。决策字段只取 `observed_at < kickoff` 的快照;`bronze_ng_odds_snap` 为
  hash 去重的追加表,`observed_at` 是本站首次观察到变化的时间,赛后回看与开球前实时读取得到
  同一条"最后一条",因此补记与前瞻记录等价。

### 5.2 数据保留核验(2026-09-28,实施前确认)

- 代码:`backend/` 与 `deploy/` 中没有任何对 `bronze_ng_odds_snap` 的 DELETE/清理;唯一的追加表
  保留策略是 `backend/worker/runner.py:181` 清理 30 天前的 `poll_attempt_log`(与本记录无关)。
  `backend/cli/purge_stale_xref.py` 是人工 CLI(未挂定时器,`docs/current-state.md` 记为"未执行"),
  且只删 `review_status='needs_review'` 的 xref,本记录只用 `auto_ok/confirmed`。
- systemd:仓库 `deploy/systemd/` 与服务器 `systemctl list-timers` 的 16 个定时器中没有清理赔率
  快照的任务;`allwin-maintenance` 只跑 `entity_resolution`。
- crontab:ubuntu 用户 crontab 为空;`/etc/cron.d` 只有 `certbot`、`e2scrub_all`;root crontab
  无法在无密码 sudo 下读取,标 `UNVERIFIED`。
- 实证:`bronze_ng_odds_snap` 最早 `observed_at` = 2020-08-09,共 1,292,537 行,未见过清理。
- 结论:没有保留期限;每天 05:00 UTC 运行一次完全足够(即使每周一次也够)。

### 5.3 运行方式(站长选定 (b))

ubuntu 用户级 crontab,每天 05:00 UTC:
```
0 5 * * * cd /opt/allwin/source && ALLWIN_DATA_DIR=/opt/allwin/shared/data nice -n 19 /usr/bin/python3 research/ah_signals/hm2_log.py --data-dir /opt/allwin/shared/data --out-dir /home/ubuntu/research_out/ah_signals/hm2 >> /home/ubuntu/research_out/ah_signals/hm2/cron.log 2>&1
```
运行说明见 `docs/deployment-aws-cloudflare.md`(运维章节"研究纸面记录 H-M2")。记录脚本 stdout
只打印:本次新增场次、累计场次、累计下注数、各不下注原因计数;**不打印命中率/ROI**。

### 5.4 规则冻结自检

`hm2_log.py` 与 `hm2_evaluate.py` 启动时计算 `hm2_log.py`、`hm2_evaluate.py`、`PREREG_hm2.md`
三个文件的 SHA-256;首次运行(`hm2_state.json` 不存在)把三者写入 `hm2_state.json` 作为定稿哈希,
此后每次运行与之比对,任一不一致 → 拒绝运行,退出码 4 并打印不一致的文件。评估完成后追加
修订记录会改变本文件哈希,这是预期的:评估只运行一次,之后记录脚本也应停止(`evaluated_at`
存在时记录脚本同样拒绝运行)。

## 6. 数据源变化条款(冻结)

Crown 数据中断、字段格式变化、轮询频率变化时:**只记录变化时间与受影响场次,不修改规则**;
受影响场次标记为"不下注(数据源变化)"(`reason=data_source_change`),不计入下注数。判定方式:
- 自动:某个开球日(UTC 日期)内五大联赛比赛里,有 Crown 收盘 snap 的比例 <50%,或该日 Crown 全部
  snap 都被水位过滤拒绝 → 该日全部比赛标 `data_source_change`;
- 人工:站长在 `hm2_incidents.csv` 写入 `start_utc,end_utc,note`,区间内开球的比赛标
  `data_source_change`。
- 变化时间与受影响场次数在评估时一并报告。

## 7. 风险与如实声明

- 本假设来自事后观察,先验弱;前瞻检验的意义正是把它和"看过的数据"隔开。
- 记录脚本不产生任何下注行为,只是纸面记录(paper log)。
- 26/27 是 5 分钟实时轮询,收盘 snap 结构性 ≥10 分钟,与规则一致;25/26 回填数据的更密快照
  不影响本检验(不计入)。

## 8. 修订记录

- 2026-09-28 方案草案。
- 2026-09-28 定稿(站长审定修改):样本改为按时间划定(定稿 commit 时间戳);主检验改为每注
  平均盈亏 >0 的单侧 t 检验(bootstrap 10000 次并报,以 t 为准),二项检验降为次要;功效按主检验
  重算;门槛 1600 注只看一次;运行方式 (b) 用户级 crontab 05:00 UTC;数据保留核验结论;规则冻结
  哈希自检;数据源变化条款;crontab 与运行说明写进 docs。
