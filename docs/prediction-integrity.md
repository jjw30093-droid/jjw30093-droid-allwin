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
kickoff_precision(exact|date_only|unknown) / kickoff_source  ← 赛前判断 provenance 冻结列
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
  │                    └──retract──▶ retracted(状态标记,不删记录,不退出统计)
  │                    (locked 同样可 retract:官方样本的撤回在 track record 透明展示)
  └─ 导入且无法证明赛前生成的历史 ──▶ legacy_unverified(终态,永不 official)
修正:supersede —— 追加新快照并把旧快照 superseded_by 指向新 id,旧版保留可查;
      **只允许开球前创建**(post_kickoff 拒绝),旧版永不退出公开集合与评估分母
```

服务层规则(`commands/predictions.py`):

- `register_snapshot`:三概率之和容差校验;`legacy_unverified` 禁止 is_official。
- `publish_snapshot`:仅 draft 可发布,且必须**早于开球**。
- `lock_snapshot`:仅 published 可锁定,且必须早于开球;锁定即 `is_official=1`。
- **精确 kickoff 来源门禁(CLAUDE.md §6.2.1;P0-A 收口)**:publish/lock 的精确性
  资格由快照冻结的 **provenance** 判定,**不再依赖字符串是否含 `'T'` 或是否等于午夜**。
  快照 register 时冻结两列:
  - `kickoff_precision` ∈ `exact` / `date_only` / `unknown`;
  - `kickoff_source`:精确开球时间的可追溯来源(如 `fotmob:fixtures` /
    `fotmob:match_details`),date_only/unknown 时可为 legacy 标识或空。

  唯一真源判定 `backend.db.util.is_exact_kickoff(ko, precision, source)`——四者缺一即拒:
  `precision == 'exact'`、`source` 非空、`kickoff_at_utc` **带显式时区**、可严格解析为
  tz-aware UTC。因此以下一律 `imprecise_kickoff` / `naive_kickoff` / `no_kickoff_source`
  拒绝 publish/lock:date_only、unknown、`<date>T00:00:00Z` 午夜占位(precision 非 exact)、
  非法时间、naive datetime(无时区)、缺来源。该规则只约束新 publish/lock/supersede,
  历史已锁定样本由触发器保证不可改写。
  真实存在的 UTC 午夜开球(precision=exact + 有来源)不受影响,可正常 publish——
  精确性看 provenance,不看时刻是否等于午夜。
- `supersede_snapshot`:必须**早于开球**;开球后创建修正版被拒绝(`post_kickoff`),
  防止赛后用"修正"改写公开账本。
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

## 4. 永久资格不变量(撤回/修正不退出统计)

CLAUDE.md §9.1:一条快照一旦满足 `is_official=1 ∧ locked_at IS NOT NULL ∧
published_at < kickoff_at_utc`,就**永久**属于公开正式样本集合。

- `retract_snapshot` 只把 status 置 retracted + 写审计(含 reason),行仍在库中;
  track record 把撤回样本**计入 retracted_count 并完整展示**,撤回**不改变**其
  公开资格与评估分母——撤回只是公开说明和审计标注。
- `supersede` 设置 `superseded_by` 后,旧版**仍在公开列表、仍进评估分母**;
  修正链通过响应字段 `superseded_by`(旧→新)与 `correction_of`(新→旧)双向可查,
  新旧版本同时返回。
- 任何能让已经公开的失败预测退出指标分母的实现都是 P0 数据完整性缺陷;
  track record 与 evaluation 查询**禁止**用 `status` 或 `superseded_by` 过滤正式样本。

## 5. 赛后结算与正式口径

- `settle_outcomes`(`evaluate_predictions` CLI 与 worker `postmatch_settle` 调用):
  对有登记快照且未结算的 match,从 core dim_match(status='Finish',比分非空)写
  `prediction_outcomes`(INSERT OR IGNORE 幂等)。
- 正式样本口径唯一真源:`backend/prediction_scope.py::official_sample_where`,
  **track record、evaluation、daily manifest 三处共用同一 SQL 判据**(历史上 manifest 只查
  `is_official` 导致口径漂移,已收口):
  `is_official=1 AND locked_at IS NOT NULL AND published_at IS NOT NULL
   AND kickoff_at_utc IS NOT NULL AND julianday(published_at) < julianday(kickoff_at_utc)`
  ——**不含**任何 status / superseded_by 条件(§4 不变量)。用 `julianday()` 而非裸文本比较:
  文本比较对混合时区偏移格式(如 kickoff `...-05:00` vs published `...Z`)会给出错误结论。
- 指标(`evaluation_samples`)计算**全部**已结算正式样本,含撤回与被取代版本;
  评估 scope_json 记 `includes_retracted / includes_superseded = true`。
  `/api/v1/track-record` 公开全部正式样本(含 retracted_count / superseded_count
  透明计数),默认不筛选。

## 6. 每日 manifest 与 S3

`build_daily_manifest`(CLI:`python -m backend.cli.build_manifest [--date YYYY-MM-DD]`;
worker `metrics_rebuild` 每轮也会跑):

- 取当日(UTC,按 published_at 前 10 位)全部**正式合格**快照(资格判据 = §5 的
  `official_sample_where`,与 track record/evaluation 完全一致:official ∧ 曾锁定 ∧ 有精确
  开球 ∧ julianday(published) < julianday(kickoff))的
  {id, match_id, model_version_id, generated_at, published_at, locked_at,
  input_snapshot_hash, prediction_hash, 三概率},按 id 排序生成排序 JSON →
  `manifest_hash` = sha256。**开球后发布 / 未锁定的 official 不进 manifest**(与 track record
  排除的样本一致),回归测试同时构造合格与不合格记录并比较三集合。
- **覆盖修正链全部正式版本**:被取代(superseded_by 非空)与已撤回的正式快照
  同样入清单,不只最新版;entries 只含锁定即不可变的实质字段(不含 status /
  superseded_by),因此赛后 retract/supersede 不会改变已有 manifest 内容或 hash。
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

**kickoff provenance(P0-A 收口)**:导入不再把 `Date` 拼成 `<date>T00:00:00Z` 伪装精确
时间,也**绝不为通过精确性检查而拼造来源**(曾经的 `core:dim_match` 占位是伪造 provenance,
已移除)。每条快照的开球时间经唯一真源 `normalize_exact_kickoff` 按 canonical 比赛
(`dim_match`)的真实 provenance 决定:
- canonical `kickoff_precision='exact'` **且有非空真实来源**且时间可解析 → 原样 exact 透传,
  来源承接 canonical 的 `kickoff_source`(如 `fotmob:fixtures`);
- canonical 只有自然日(`date_only`)→ `kickoff_at_utc=NULL`,精度 `date_only`,来源标
  `legacy:gold_wdl_predictions:date_only`,如实表示"只掌握到自然日";
- canonical 精度未知、缺来源、或声称 exact 但无法解析/无来源 → **不伪造来源**,降级为
  `kickoff_at_utc=NULL` + `kickoff_precision='unknown'` + 来源
  `legacy:gold_wdl_predictions:unknown`。
当前真实库 dim_match 均无精确 kickoff → 全部 760 行导入为 date_only,不可 publish。

**历史占位修复工具**:`python -m backend.cli.repair_kickoff_provenance [--dry-run]`——把旧版
导入产生的**未锁定、非 official**午夜占位(`<date>T00:00:00Z` + precision 非 exact +
来自 Gold 导入模型)安全重分类为 `kickoff_at_utc=NULL` + `kickoff_precision='date_only'` +
`kickoff_source='legacy:gold_wdl_predictions:date_only'`。只修未锁定行,幂等(修复后不再
匹配占位形状),修复前后断言 official/locked 集合数量不变;支持 --dry-run。

其余行为:注册 `model_versions('dc-baseline-1.M.2')`(metrics_json 里明确写
对照是历史频率基线、市场基线 UNVERIFIED);概率导入前重新归一防浮点残差;
幂等——同 (match_id, model_version) 已有快照则跳过;triggered_by='import' 记入
prediction_runs。

## 8. 验证状态

| 项 | 状态 |
|---|---|
| 状态机、赛前校验、触发器不可改/不可删、撤回保留、导入口径 | 已验证(pytest `tests/backend/test_predictions.py`) |
| 永久资格不变量:结算后撤回/被取代不改变公开 total 与评估分母、修正链新旧版本同时可查(查询层 + API 层) | 已验证(pytest `TestPermanentEligibility` / `TestTrackRecordApiPermanence`) |
| kickoff provenance 门禁:date_only / 午夜占位(非 exact)/ 非法格式 / naive / 缺来源 均拒 publish/lock;真实 exact+来源可 publish;开球后拒;supersede 不绕过;bulk publish-upcoming 逐条失败 | 已验证(pytest `TestKickoffProvenanceIntegrity`) |
| legacy 导入不再伪造午夜;repair 工具 dry-run/幂等/不改 official-locked;import+repair 幂等;migration 前后 official/locked 集合不变 | 已验证(pytest `test_kickoff_provenance.py`,repair 在真实 platform.db 副本上实跑) |
| manifest 幂等与版本追加;manifest 覆盖修正链全部版本且不因赛后 retract 变 hash | 已验证(pytest) |
| S3 版本桶上传 / Object Lock(governance) | **UNVERIFIED**(未配 AWS 凭证) |
| 正式样本的真实公开战绩 | 尚无(26/27 未开赛,无任何 locked 样本;track record 如实显示空态) |
