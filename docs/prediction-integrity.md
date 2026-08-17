# 预测完整性(docs/prediction-integrity.md)

> 依据真实代码撰写:`backend/migrations/platform/0001_init.sql`(登记簿表与触发器)、
> `backend/migrations/platform/0007_editable_predictions.sql`(可编辑改动,2026-08-05)、
> `backend/commands/predictions.py`、`backend/queries/track_record.py`、
> `backend/cli/{import_gold_predictions,build_manifest,evaluate_predictions}.py`、
> `backend/api/routes_admin.py`(2026-08-05 核对)。
> 原则(CLAUDE.md §9):正式预测是公开账本,默认全量展示、不设起止日期筛选、不可
> 选择性删除或隐藏;物理删除始终禁止。内容(概率等)可以直接修正,但每次真正产生
> 变化的修正都强制留痕(操作者、原因、修正前后值、时间),不允许静默覆盖——见 §3。

> **本文件的适用范围只到 `prediction_snapshots` 一套模型预测登记簿**(2026-08-16,
> CLAUDE.md §9.1 修订确认)。付费板块「每日精选」(`reco_slips`/`reco_legs`,
> `backend/commands/reco.py`/`reco_auto_settle.py`/`reco_settlement_math.py`/
> `reco_odds_contract.py`)是独立建表的人工推荐板块,不是本文件描述的锁定/哈希/
> 永久公开资格登记簿:内容(方向、赔率、结果)允许管理员直接修改(`edit_slip`),
> 结算允许人工重录(`settle_slip` 对 `settled` 单重录 = 结算修正,同样留痕但不生成
> 新版本,不是本文件 §3 描述的"编辑留痕但记录本身不可变"或"supersede 生成新版本"
> 两种机制)。`reco_auto_settle`(`backend/worker/runner.py::_job_reco_auto_settle`,
> job_name `reco_auto_settle`)只结算「每日精选」推荐单,**不触碰、不复用**本文件描述
> 的 `prediction_snapshots`/`evaluate_predictions`/`build_manifest` 任何一段逻辑
> ——两套结算/评估口径物理隔离,不共享判定函数、不共享连接生命周期,详见
> `docs/current-state.md` §46。

## 1. 登记簿表结构(platform.db)

| 表 | 职责 |
|---|---|
| `model_versions` | 模型版本(id 如 `dc-baseline-1.M.2`,algorithm/params_json/train_range/metrics_json) |
| `prediction_runs` | 一次预测生成批次(triggered_by ∈ manual/worker/import,状态与场次数) |
| `prediction_snapshots` | 核心账本,见下 |
| `prediction_outcomes` | 赛后结果(match_id 主键:比分、outcome、settled_at、source='fotmob') |
| `prediction_evaluations` | 离线评估结果(scope_json 记口径、accuracy/brier/log_loss/rps/calibration_json) |
| `prediction_manifests` | 每日正式预测清单((manifest_date, version) 唯一、manifest_hash、s3_key/uploaded_at) |
| `prediction_snapshot_edits` | 修正记录(append-only,2026-08-05 新增),见 §3 |

`prediction_snapshots` 关键列:

```text
id(UUID) / run_id / match_id(FotMob) / kickoff_at_utc / model_version_id
kickoff_precision(exact|date_only|unknown) / kickoff_source  ← 赛前判断 provenance 冻结列
league_id(可空,2026-07-21 新增)                              ← 模型适用范围校验用,同级冻结列
generated_at / published_at / locked_at / input_cutoff_at
input_snapshot_hash / prediction_hash
home_win / draw / away_win / expected_home_goals / expected_away_goals
confidence / visibility(public|member|internal)
status(draft|published|locked|retracted|legacy_unverified)
is_official / superseded_by / created_at
last_edited_at / edit_count(可空/默认0,2026-08-05 新增)         ← 修正次数与最近时间
CHECK: 三概率非负,且 |home+draw+away − 1| < 0.001
```

`prediction_hash` = sha256(排序 JSON{match_id, model_version_id, 三概率 round6,
generated_at})——反映**当前**内容,不再是"发布后永不改变"的指纹:`edit_snapshot`
每次真正修改三概率之一都会重算这个 hash。要核对"某个时间点的内容是什么",查
`prediction_snapshot_edits` 的 before_json/after_json(见 §3),不能只看当前 hash。

## 2. 状态机

```text
draft ──publish──▶ published ──lock──▶ locked(is_official=1)
  │                    │                   │
  │                    └──retract──▶ retracted(状态标记,不删记录,不退出统计)
  │                    (locked 同样可 retract:官方样本的撤回在 track record 透明展示)
  └─ 导入且无法证明赛前生成的历史 ──▶ legacy_unverified(终态,永不 official)

任意状态、任意时刻(旁路于上面这条主状态机,不受其门禁约束):
  edit_snapshot —— 直接修正概率等字段,写 prediction_snapshot_edits 留痕,
                    不改变 status,可在 draft/published/locked/retracted/
                    开球前/开球后/结算前/结算后任意时刻调用

另一种修正方式:supersede —— 追加新快照并把旧快照 superseded_by 指向新 id,
      旧版保留可查;**只允许开球前创建**(post_kickoff 拒绝),旧版永不退出
      公开集合与评估分母。edit 与 supersede 是两种独立、并存的修正手段:
      edit 是"就地小修"(同一条记录,留修正历史),supersede 是"另起一个可追溯
      的新版本"(两条记录并存,适合大改或需要保留完整旧快照上下文的场景)。
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
  防止赛后用"另起新版本"这种方式绕开 edit 的留痕机制悄悄改写账本。
- `edit_snapshot`(2026-08-05 新增):**不做任何状态或时间门禁**,任意状态、任意
  时刻(含已锁定/已开球/已结算)都可调用——用户明确要求"随时可以更改"。只更新
  调用方显式传入的字段;若改了三概率之一,会与当前(未改的)另外两项合并后重新
  校验和为 1(容差 0.001)。`reason` 必填。真正产生变化时才写
  `prediction_snapshot_edits` 一行并让 `edit_count+1`/`last_edited_at` 更新;
  传入值与当前值完全相同(no-op)时不产生历史记录。
- publish/lock/retract/supersede/edit 全部写 `audit_logs`。

管理入口:`POST /api/v1/admin/predictions/{id}/publish|lock|retract|edit`、
`POST /api/v1/admin/predictions/publish-upcoming`(admin + CSRF)。

## 3. 锁定后的修正机制(2026-08-05 起:内容可编辑,删除仍然禁止)

**DB 触发器现状(migration 0001 + 0007):**

- ~~`trg_pred_snap_locked_immutable`~~ / ~~`trg_pred_snap_locked_provenance_immutable`~~ /
  ~~`trg_pred_snap_locked_league_immutable`~~ **已在 migration 0007 移除**。这意味着
  **DB 层不再对已锁定行的 UPDATE 兜底**——`home_win`/`draw`/`away_win` 等字段现在
  可以被直接 UPDATE(不管 `locked_at` 是否非空),绕过 service 层直接写 SQL **也不会
  被拦下**。`edit_snapshot` 是唯一负责在编辑时写 `prediction_snapshot_edits` 留痕的
  地方——不通过它直接 UPDATE `prediction_snapshots`,就是一次没有留痕的静默覆盖,
  这是应用层的责任,不再有数据库兜底。
- `trg_pred_snap_no_delete` **保留不动**:`locked_at IS NOT NULL OR is_official=1`
  的行 BEFORE DELETE → `RAISE(ABORT)`,物理删除依然被数据库拒绝——本次改动只涉及
  "内容可编辑",不涉及"可删除"。
- 新增 `trg_pred_snap_edits_no_update` / `trg_pred_snap_edits_no_delete`:
  `prediction_snapshot_edits` 本身是 append-only(仿 `team_style_profiles` 的写法),
  修正记录一旦写入就不能被再改或删除——这是"内容可编辑,但编辑历史不可编辑"的
  防篡改边界。

**service 层(`edit_snapshot`,见 §2)**:唯一的正式修正入口,校验概率合法性 +
写留痕 + 更新 `edit_count`/`last_edited_at` + 重算 `prediction_hash`。

## 4. 公开样本口径不变量(撤回/修正/编辑都不影响样本资格)

CLAUDE.md §9.1:一条快照一旦满足 `is_official=1 ∧ locked_at IS NOT NULL ∧
published_at < kickoff_at_utc`,就**永久**属于公开正式样本集合——这条判据只看
"是否曾经合格地锁定过",不看内容后来有没有被编辑过。

- `retract_snapshot` 只把 status 置 retracted + 写审计(含 reason),行仍在库中;
  track record 把撤回样本**计入 retracted_count 并完整展示**,撤回**不改变**其
  公开资格与评估分母——撤回只是公开说明和审计标注。
- `supersede` 设置 `superseded_by` 后,旧版**仍在公开列表、仍进评估分母**;
  修正链通过响应字段 `superseded_by`(旧→新)与 `correction_of`(新→旧)双向可查,
  新旧版本同时返回。
- `edit_snapshot` 直接修改内容,不改变 status/superseded_by,因此不影响样本
  资格判定本身;但 `evaluation_samples()`/`build_daily_manifest()` 都是**实时读
  当前行的 home_win/draw/away_win**(不是快照式的),编辑后:已经生成的历史
  `prediction_evaluations`/`prediction_manifests` 行内容不变(各自都是独立的
  append-only 表,不会被追溯修改),但**下一次**重新运行评估/manifest 会用编辑后
  的新值计算——这是允许编辑之后的合理结果,不是缺陷,只要 `edit_count`/
  `last_edited_at`/`prediction_snapshot_edits` 让这次内容变化可查。
- 任何能让已经公开的失败预测**从公开列表消失**、或**从评估分母消失**的实现
  都是 P0 数据完整性缺陷;track record 与 evaluation 查询**禁止**用 `status`
  或 `superseded_by` 过滤正式样本——**这条"不选择性隐藏样本"的规则不受
  §3 的可编辑性改动影响,依然是绝对要求**(CLAUDE.md §17)。物理删除同样
  依然绝对禁止(§3)。

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
  同样入清单,不只最新版;retract/supersede 不改变 entries 里的实质字段(三概率等),
  因此赛后 retract/supersede 不会改变已有 manifest 内容或 hash。**`edit_snapshot`
  会**:manifest 是生成时刻的快照(append-only,不追溯改写),编辑发生在 manifest
  生成**之后**不会让已有 manifest 变化(它就是历史时刻的真实记录),但会让"当前
  manifest"与"当前 `prediction_snapshots` 内容"出现差异,直到下一次
  `build_daily_manifest` 重新生成新版本;`prediction_snapshot_edits` 是查这段
  差异从何而来的地方。
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
| 状态机、赛前校验、撤回保留、导入口径 | 已验证(pytest `tests/backend/test_predictions.py`) |
| 锁定行 UPDATE 触发器已移除(可直接编辑)、DELETE 触发器仍生效(物理删除仍禁止) | 已验证(migration 0007 临时库 dry-run 手工验证 + pytest,见 §3) |
| `edit_snapshot`:任意状态/任意时刻可编辑、概率校验、留痕写入 `prediction_snapshot_edits`、no-op 不留痕、`edit_count`/`last_edited_at`/`prediction_hash` 正确更新 | 已验证(pytest `TestEditSnapshot`) |
| `prediction_snapshot_edits` 自身 append-only(no-update/no-delete 触发器) | 已验证 |
| 公开样本口径不变量:结算后撤回/被取代/编辑都不改变公开 total 与评估分母、修正链新旧版本同时可查(查询层 + API 层) | 已验证(pytest `TestPermanentEligibility` / `TestTrackRecordApiPermanence`) |
| kickoff provenance 门禁:date_only / 午夜占位(非 exact)/ 非法格式 / naive / 缺来源 均拒 publish/lock;真实 exact+来源可 publish;开球后拒;supersede 不绕过;bulk publish-upcoming 逐条失败 | 已验证(pytest `TestKickoffProvenanceIntegrity`) |
| legacy 导入不再伪造午夜;repair 工具 dry-run/幂等/不改 official-locked;import+repair 幂等;migration 前后 official/locked 集合不变 | 已验证(pytest `test_kickoff_provenance.py`,repair 在真实 platform.db 副本上实跑) |
| manifest 幂等与版本追加;manifest 覆盖修正链全部版本且不因赛后 retract 变 hash | 已验证(pytest) |
| S3 版本桶上传 / Object Lock(governance) | **UNVERIFIED**(未配 AWS 凭证) |
| 正式样本的真实公开战绩 | 尚无(26/27 未开赛,无任何 locked 样本;track record 如实显示空态) |
