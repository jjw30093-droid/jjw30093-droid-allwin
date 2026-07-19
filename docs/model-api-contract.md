# 模型与 API 契约(docs/model-api-contract.md)

> 依据真实代码撰写:`backend/models/{features/build_match_features,build_wdl_baseline,predict_wdl_future}.py`、
> `backend/eval/metrics.py`、`backend/api/{schemas,routes_public}.py`、
> `backend/queries/{predictions,track_record}.py`(2026-07-19 核对)。

## 1. 模型:dc-baseline-1.M.2

登记的模型版本 id:`dc-baseline-1.M.2`(`backend/cli/import_gold_predictions.py`)。
算法:Dixon-Coles 泊松比分矩阵 + 按类 isotonic 校准。纯 scipy/scikit-learn,
无 LightGBM/神经网络。

### 1.1 输入(每场 4 个特征)

来自 core `int_match_features`(由 `models/features/build_match_features.py` 构建):

| 特征 | 含义 |
|---|---|
| `home_xg_for_l10` | 主队近 10 场滚动 xG(进攻) |
| `home_xg_against_l10` | 主队近 10 场滚动被 xG(防守;取同场对手的 expected_goals) |
| `away_xg_for_l10` | 客队近 10 场滚动 xG |
| `away_xg_against_l10` | 客队近 10 场滚动被 xG |

特征口径:

- 只用 `dim_match.status='Finish'` 场次;xG 取 `fact_team_match_stats(Period='All')`
  extra_json 的 `expected_goals`。
- 每队按 (match_date, match_id) 正序,rolling 前先 `shift(1)` 把当场移出窗口——
  特征只含"该场之前"的历史,不泄露当场结果;允许跨赛季取最近 N 场。
- 另有 `sample_weight`(时间衰减,DECAY_RATE=0.0015)用于训练加权;
  `*_n_matches_*` 记录 rolling 实际样本数供下游判断可信度。
- 全表 DELETE+INSERT 幂等重建。

### 1.2 计算流程

1. **λ 估计**(`compute_lambda`):
   `λ_home = μ_home × (home_xg_for_l10 / league_avg_xg_for) × (away_xg_against_l10 / league_avg_xg_against)`,
   客队对称。μ 与联赛均值只用 train 段拟合。任一特征缺失(如升班马无历史)→
   回退 `λ = μ` 并置 `lambda_*_is_fallback=1`。
2. **比分矩阵**(`build_score_matrix`):独立 Poisson 外积覆盖 0..10 球,
   对 (0,0)/(0,1)/(1,0)/(1,1) 四格乘 Dixon-Coles τ 修正后整体归一。
   ρ 在 train 段以加权对数似然拟合(bounds (-0.2, 0.1)),**固化值 ρ=-0.005274**。
3. **WDL**(`matrix_to_wdl`):下三角=主胜、迹=平、上三角=客胜。
4. **校准**(`fit_isotonic_calibrators`/`apply_calibration`):one-vs-rest,
   每类一个单变量 IsotonicRegression(train 段拟合,sample_weight 加权);
   校准后三概率各自独立映射、不再天然归一,**应用时重新归一化**。
5. **工件**:`backend/models/artifacts/wdl_baseline_params.pkl`
   (train_seasons/baselines/rho/calibrators;gitignored,唯一副本,pickle 对
   sklearn 版本敏感)。`predict_wdl_future.py` 加载工件对 `Season='2026/2027' AND
   status='NotStarted'` 出预测,不重新拟合;升班马走 λ=μ 兜底并标
   `confidence='low'`(reason=insufficient_history / promoted_no_history)。

### 1.3 输出

`gold_wdl_predictions`(core;模型当前产物,整季 DELETE+INSERT,可重写):
`match_id, league_id, season, lambda_home/away, lambda_*_is_fallback,
p_home/p_draw/p_away, calibrated, updated_at, confidence, reason`。

正式对外的账本是 platform.db 预测登记簿(经 `import_gold_predictions` 导入,
规则见 `docs/prediction-integrity.md`);`expected_home_goals/expected_away_goals`
即 λ_home/λ_away。

## 2. 评估指标(backend/eval/metrics.py)

约定:概率向量按 (home, draw, away) 排列,outcome ∈ {home, draw, away}。
全部离线运行(CLI/worker),不进在线 FastAPI 请求。

| 指标 | 公式 |
|---|---|
| Accuracy | mean( argmax(p) == outcome ) |
| Brier(多分类) | mean( Σ_k (p_k − y_k)² ),y 为 one-hot |
| Log Loss | mean( −log max(p_true, 1e-15) ) |
| RPS | mean( Σ_{k=1..K−1} (CDF_p(k) − CDF_y(k))² / (K−1) ),K=3(与训练脚本内基线同一定义) |
| Calibration | one-vs-rest,每类 10 桶,报 bin/avg_pred/freq/n |

`evaluate_all()` 汇总以上全部 + sample_size。

### 2.1 两套口径,不得混用

1. **研发期回测(walk-forward)**:train=2020/21–2024/25,test=2025/26(英超);
   所有拟合只用 train 段。**test RPS = 0.2143**,对照为**历史主/平/客频率基线
   RPS = 0.2281**。
   - **该对照不是博彩公司收盘赔率共识。** 在完成可复现的收盘赔率评估之前,
     不得声称"模型优于/劣于收盘盘口"。
   - 市场基线状态:**UNVERIFIED**(`/api/v1/model/metrics` 响应里也如此标注)。
     未来若做,必须固定并记录:公司集合、去水公式、缺失规则、聚合方式、
     配对样本、RPS 公式(CLAUDE.md §9.2)。
2. **正式战绩评估**:`python -m backend.cli.evaluate_predictions`——先
   `settle_outcomes` 结算,再只对**正式口径样本**(is_official=1 + locked +
   published_at < kickoff + 非撤回 + 非被取代 + 已结算)算指标,写入
   `prediction_evaluations`(scope_json 记口径)。无正式样本时如实报 0,不写入。

## 3. /api/v1 预测相关端点与 free/pro DTO 差异

门禁真源在服务端(`AuthContext.has(entitlement)`);免费响应**受限字段物理不存在**
(不同 Pydantic DTO 类,不是 null 占位,更不是 CSS 遮挡)。所有随 entitlement
变化的响应 `Cache-Control: private, no-store`。

### 3.1 `GET /api/v1/matches/{id}/prediction`

外层 `PredictionResponse`:`{match_id, available, reason?, prediction?}`
(无已发布快照 / 已撤回时 available=false + 诚实 reason)。

| | 匿名 / free(`prediction:top_probability`) | pro / premium(`prediction:full_wdl`) |
|---|---|---|
| DTO | `PredictionFreeDTO` | `PredictionFullDTO` |
| tier | `"free"` | `"full"` |
| 字段 | `top_outcome`、`top_probability`(round 2) | `top_outcome`、`home/draw/away_probability`(round 4)、`expected_home_goals`、`expected_away_goals`、`prediction_hash` |
| 共同 meta | `PredictionMeta`:model_version_id / generated_at / published_at / locked_at / input_cutoff_at / status(published·locked·retracted) / confidence | 同左 |

数据源口径(`queries/predictions.current_public_snapshot`):visibility='public'、
status ∈ (published, locked, retracted)、superseded_by IS NULL 的最新一条;
draft 与 legacy_unverified **永不对外**。

### 3.2 `GET /api/v1/matches/{id}/analysis`(analysis_bundle 投影)

同一份 bundle 按 entitlement 投影(与 Studio 共用生成逻辑):

- 无 `prediction:full_wdl`:`prediction_member=null`、剔除 probability_bar 图表、
  口播稿概率段收敛为最高一项;
- 无 `odds:history_full`:`odds_timeline=[]`;
- 无 `report:deep`:`cooccurring_events=[]`(保留 `cooccurrence_count` 计数)。

### 3.3 `GET /api/v1/matches/{id}/odds`

- free / pro:`tier="delayed_summary"`——每 (公司, 市场) 仅最新一条,且
  observed_at ≥ 1 小时前(延迟摘要);
- premium(`odds:history_full`):`tier="full"` 完整快照时间线;
- 无已验证映射(xref 非 auto_ok/confirmed)→ available=false + reason。

### 3.4 `GET /api/v1/matches/{id}/cooccurrence`

- 无 `report:deep`:只有 `count`(items=null);
- 有:items 明细(odds move + event move + 窗口/时间差),文案不用因果词。

### 3.5 公开(不随身份变化,可共享缓存)

- `GET /api/v1/track-record`:official 全量样本(含撤回,透明,不挑选)+
  最新正式评估指标;`s-maxage=300`。
- `GET /api/v1/model/metrics`:模型版本(params/dev_metrics)+ 正式评估 +
  `market_baseline: {status: "UNVERIFIED", ...}`;`s-maxage=300`。

### 3.6 联赛门禁

`league:epl`(free 含英超 47);`league:top5`(pro/premium 含 53/54/55/87)。
未命中 → 匿名 401 / 已登录 403,`detail.code="membership_required"`。

## 4. 类型生成链

Pydantic(`backend/api/schemas.py`)为单一真源 →
`python -m backend.cli.export_openapi`(写 `frontend/lib/openapi.json`)→
`cd frontend && npm run gen:api`(openapi-typescript 生成 `lib/api-types.ts`)。
不手写第二套相互漂移的 schema。
