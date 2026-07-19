# 预测完整性(docs/prediction-integrity.md)

> 依据真实代码撰写:`backend/migrations/platform/0001_init.sql`(登记簿表与触发器)、
> `backend/commands/predictions.py`、`backend/queries/track_record.py`、
> `backend/cli/{import_gold_predictions,build_manifest,evaluate_predictions}.py`、
> `backend/api/routes_admin.py`(2026-07-19 核对)。
> 原则(CLAUDE.md §9):正式预测是不可覆盖的公开账本,只能追加,不能改写历史。

## 1. 登记簿表结构(platform.db)

| 表 | 职责 |
|---|---|
| `model_versions` | 模型版本(id 如 `dc-baseline-1.M.2`,algorithm/params_json/train_range/metrics_json) |
| `prediction_runs` | 一次预测生成批次(triggered_by ∈ manual/worker/import,状态与场次数) |
| `prediction_snapshots` | 核心账本,见下 |
| `prediction_outcomes` | 赛后结果(match_id 主键:比分、outcome、settled_at、source='fotmob') |
| `prediction_evaluations` | 离线评估结果(scope_json 记口径、accuracy/brier/log_loss/rps/calibration_json) |
| `prediction_manifests` | 每日正式预测清单((manifest_date, version) 唯一、manifest_hash、s3_key/uploaded_at) |

`prediction_snapshots` 关键列:

```text
id(UUID) / run_id / match_id(FotMob) / kickoff_at_utc / model_version_id
generated_at / published_at / locked_at / input_cutoff_at
input_snapshot_hash / prediction_hash
home_win / draw / away_win / expected_home_goals / expected_away_goals
confidence / visibility(public|member|internal)
status(draft|published|locked|retracted|legacy_unverified)
is_official / superseded_by / created_at
CHECK: 三概率非负,且 |home+draw+away − 1| < 0.001
```

`prediction_hash` = sha256(排序 JSON{match_id, model_version_id, 三概率 round6,
generated_at})——发布后可由第三方核对内容未被改动。

## 2. 状态机

```text
draft ──publish──▶ published ──lock──▶ locked(is_official=1)
  │                    │                   │
  │                    └──retract──▶ retracted(状态标记,不删记录)
  │                    (locked 同样可 retract:官方样本的撤回在 track record 透明展示)
  └─ 导入且无法证明赛前生成的历史 ──▶ legacy_unverified(终态,永不 official)
修正:supersede —— 追加新快照并把旧快照 superseded_by 指向新 id,旧版保留可查
```

服务层规则(`commands/predictions.py`):

- `register_snapshot`:三概率之和容差校验;`legacy_unverified` 禁止 is_official。
- `publish_snapshot`:仅 draft 可发布,且必须**早于开球**。
- `lock_snapshot`:仅 published 可锁定,且必须早于开球;锁定即 `is_official=1`。
- **kickoff 保守口径**:core dim_match 只有比赛日期,`kickoff_at_utc` 取
  `<date>T00:00:00Z` 下界——发布/锁定必须早于比赛日 00:00 UTC 才算赛前,宁严勿松。
- publish/lock/retract/supersede 全部写 `audit_logs`。

管理入口:`POST /api/v1/admin/predictions/{id}/publish|lock|retract`、
`POST /api/v1/admin/predictions/publish-upcoming`(admin + CSRF)。

## 3. 锁定后双层不可改

**第一层(DB 触发器,migration 0001,绕过 service 直接写 SQL 也拦得住):**

- `trg_pred_snap_locked_immutable`:`locked_at IS NOT NULL` 时,BEFORE UPDATE OF
  实质字段(match_id、model_version_id、run_id、generated_at、input_cutoff_at、
  input_snapshot_hash、prediction_hash、三概率、期望进球、published_at、locked_at、
  kickoff_at_utc、is_official、visibility)→ `RAISE(ABORT)`。
  status 与 superseded_by 不在列表内——撤回与修正链接因此仍然可行。
- `trg_pred_snap_no_delete`:`locked_at IS NOT NULL OR is_official=1` 的行
  BEFORE DELETE → `RAISE(ABORT)`,物理删除被数据库拒绝。

**第二层(service 状态机)**:上节的前置状态与赛前校验;修正必须走
`supersede_snapshot` 追加新版本,不 UPDATE 概率。

## 4. 撤回不删除

`retract_snapshot` 只把 status 置 retracted + 写审计(含 reason),行仍在库中;
track record 查询(`queries/track_record.py`)把官方样本的撤回**计入
retracted_count 并完整展示**,防止用撤回选择性美化战绩。

## 5. 赛后结算与正式口径

- `settle_outcomes`(`evaluate_predictions` CLI 与 worker `postmatch_settle` 调用):
  对有登记快照且未结算的 match,从 core dim_match(status='Finish',比分非空)写
  `prediction_outcomes`(INSERT OR IGNORE 幂等)。
- 正式样本口径(`_OFFICIAL_WHERE`):
  `is_official=1 AND status IN ('locked','retracted') AND locked_at IS NOT NULL
   AND published_at IS NOT NULL AND kickoff_at_utc IS NOT NULL
   AND published_at < kickoff_at_utc AND superseded_by IS NULL`。
  指标只算其中已结算的非撤回样本;`/api/v1/track-record` 公开全部正式样本,默认不筛选。

## 6. 每日 manifest 与 S3

`build_daily_manifest`(CLI:`python -m backend.cli.build_manifest [--date YYYY-MM-DD]`;
worker `metrics_rebuild` 每轮也会跑):

- 取当日(UTC,按 published_at 前 10 位)全部 official 快照的
  {id, match_id, model_version_id, generated_at, published_at, locked_at,
  input_snapshot_hash, prediction_hash, 三概率},按 id 排序生成排序 JSON →
  `manifest_hash` = sha256。
- 幂等:内容与最新版本相同 → 不新增;变化 → version+1 **追加**,旧版本保留
  ((manifest_date, version) 唯一约束)。
- S3:表预留 `s3_key/uploaded_at`;上传由部署侧(`deploy/scripts/backup_sqlite.sh`,
  manifest 导出与库备份分目录)在配置了 `S3_BACKUP_BUCKET` + AWS 凭证时执行。
  **本机未配置凭证,S3 上传与 Object Lock 均 UNVERIFIED**;未配置时只落本地,
  不声称已上传。

## 7. legacy 导入规则(import_gold_predictions)

背景事实(`docs/current-state.md` §1 审计):core `gold_wdl_predictions` 760 行
只有批次 `updated_at`(全部 2026-07-09),无 generated_at/模型版本列,且整季
DELETE+INSERT 可重写——它是模型当前产物,不是账本。

`python -m backend.cli.import_gold_predictions [--dry-run]` 的口径:

| 赛季 | 判定 | 导入状态 | 理由 |
|---|---|---|---|
| 2025/2026(380 行) | 比赛 2025-08→2026-05 已全部完赛,写入时间 2026-07-09 在赛后 | `legacy_unverified` + `visibility='internal'`,永不 official | 无法证明赛前生成 → 按 CLAUDE.md §9.1 只能作回测参考,**绝不冒充正式赛前战绩** |
| 2026/2027(380 行) | 比赛 2026-08 之后开球,写入早于全部开球 | `draft` + `visibility='public'` | 可证明生成早于开球;但仍需管理员在开球前 publish + lock 后才成为正式样本进 track record |

其余行为:注册 `model_versions('dc-baseline-1.M.2')`(metrics_json 里明确写
对照是历史频率基线、市场基线 UNVERIFIED);概率导入前重新归一防浮点残差;
幂等——同 (match_id, model_version) 已有快照则跳过;triggered_by='import' 记入
prediction_runs。

## 8. 验证状态

| 项 | 状态 |
|---|---|
| 状态机、赛前校验、触发器不可改/不可删、撤回保留、导入口径 | 已验证(pytest `tests/backend/test_predictions.py`) |
| manifest 幂等与版本追加 | 已验证(pytest) |
| S3 版本桶上传 / Object Lock(governance) | **UNVERIFIED**(未配 AWS 凭证) |
| 正式样本的真实公开战绩 | 尚无(26/27 未开赛,无任何 locked 样本;track record 如实显示空态) |
