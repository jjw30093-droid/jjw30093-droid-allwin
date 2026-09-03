"""P0.2 migration 测试:空库初始化、重复执行幂等、checksum 漂移、失败回滚、锁定触发器。"""

import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.db import migrate

PLATFORM_TABLES = {
    "users", "auth_identities", "auth_sessions", "oauth_states", "device_login_requests",
    "account_links", "roles", "plans", "plan_entitlements", "subscriptions",
    "products", "model_versions", "prediction_runs", "prediction_snapshots",
    "prediction_outcomes", "prediction_evaluations", "prediction_manifests",
    "favorites", "content_drafts", "export_jobs", "job_runs", "audit_logs",
    "analytics_events", "schema_migrations", "pipeline_alerts",
}

ODDS_TABLES = {
    "dim_team_xref", "dim_team_alias", "dim_match_xref",
    "bronze_ng_odds_snap", "bronze_fm_lineup_snap", "bronze_fm_sideline_snap",
    "silver_odds_moves", "silver_event_moves", "gold_move_cooccurrence",
    "source_health", "schema_migrations", "fixture_sync_ledger", "poll_attempt_log",
}


def _tables(db_file):
    conn = sqlite3.connect(db_file)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


def test_platform_fresh_init(tmp_path):
    db = tmp_path / "platform.db"
    applied = migrate.apply_all("platform", db_file=db, quiet=True)
    assert applied >= 2
    assert PLATFORM_TABLES <= _tables(db)
    conn = sqlite3.connect(db)
    # 种子数据齐备
    assert conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0] == 3
    # 0010 起 5 个 plan(free/member/daily_picks 在售,pro/premium 下架保留行)
    assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 5
    assert {r[0] for r in conn.execute("SELECT id FROM plans WHERE is_active=1")} == {
        "free", "member", "daily_picks"}
    free = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='free'")}
    # 2026-08-11 权限矩阵互换(platform 0012):free 换成 top5 + european_cup,
    # 不再持有 league:lottery(见 backend/queries/leagues.py 同批注释)。
    assert free == {
        "league:epl", "league:top5", "league:european_cup",
        "prediction:top_probability", "odds:summary_delayed",
    }
    pro = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='pro'")}
    assert "prediction:full_wdl" in pro and free <= pro  # pro ⊇ free
    premium = {r[0] for r in conn.execute(
        "SELECT entitlement FROM plan_entitlements WHERE plan_id='premium'")}
    assert pro <= premium and "odds:raw" in premium
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] >= 4
    conn.close()


def test_odds_fresh_init(tmp_path):
    db = tmp_path / "odds.db"
    migrate.apply_all("odds", db_file=db, quiet=True)
    assert ODDS_TABLES <= _tables(db)


def test_odds_fresh_init_includes_lineup_type_column(tmp_path):
    """PIPELINE_REDESIGN_V2 P2:全新库也要有可索引的 lineup_type 列(不是只有
    升级场景才补)。"""
    db = tmp_path / "odds.db"
    migrate.apply_all("odds", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bronze_fm_lineup_snap)")}
    assert "lineup_type" in cols
    idx_names = {r[1] for r in conn.execute("PRAGMA index_list(bronze_fm_lineup_snap)")}
    assert "idx_fm_lineup_type" in idx_names
    conn.close()


def test_lineup_snap_type_column_and_backfill(tmp_path):
    """PIPELINE_REDESIGN_V2 P2:bronze_fm_lineup_snap.lineup_type 落成可索引列,
    从既有行的 payload_json 回填,不为无法确定的行编造默认值。

    真实分布(2026-08-17 实测,data/odds.db 228 行):payload 缺 lineup_type
    键=154、lastStarting11=57、predicted(source=enetpulse)=16、
    standard(source=null)=1。这里各取一条代表性行(payload_json 结构原样取自
    真实抓取样本)验证回填口径,并证明重跑不改已回填的值、不为缺失键的行
    编造默认值。
    """
    staged = tmp_path / "staged_migrations"
    staged.mkdir()
    src = migrate.MIGRATIONS_ROOT / "odds"
    pre_0009 = [(v, name, path) for v, name, path in migrate.migration_files(src) if v <= 8]
    assert pre_0009 and pre_0009[-1][0] == 8, "0008 必须存在,否则本升级场景前提不成立"
    for _, name, path in pre_0009:
        shutil.copyfile(path, staged / name)

    db = tmp_path / "odds.db"
    migrate.apply_all("odds", db_file=db, migrations_dir=staged, quiet=True)

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bronze_fm_lineup_snap)")}
    assert "lineup_type" not in cols  # 升级场景前提:0009 之前确实没有这列

    ROWS = [
        # (fotmob_match_id, payload_json, expected lineup_type after backfill)
        (9001,
         '{"away":{"formation":"3-4-3","starters":[],"subs":[],"team_id":2},'
         '"home":{"formation":"4-1-4-1","starters":[],"subs":[],"team_id":1}}',
         None),
        (9002,
         '{"away":{"formation":"3-4-2-1","starters":[],"subs":[],"team_id":2},'
         '"home":{"formation":"4-4-2","starters":[],"subs":[],"team_id":1},'
         '"lineup_type":"lastStarting11","source":"lastStartingLineups"}',
         "lastStarting11"),
        (9003,
         '{"away":{"formation":"4-5-1","starters":[],"subs":[],"team_id":2},'
         '"home":{"formation":"3-4-3","starters":[],"subs":[],"team_id":1},'
         '"lineup_type":"predicted","source":"enetpulse"}',
         "predicted"),
        (9004,
         '{"away":{"formation":null,"starters":[],"subs":[],"team_id":2},'
         '"home":{"formation":null,"starters":[],"subs":[],"team_id":1},'
         '"lineup_type":"standard","source":null}',
         "standard"),
    ]
    for mid, payload_json, _expected in ROWS:
        conn.execute(
            """INSERT INTO bronze_fm_lineup_snap
               (fotmob_match_id, payload_json, payload_hash, source_updated_at,
                observed_at, ingested_at, poll_run_id)
               VALUES (?, ?, ?, NULL, '2026-08-04T00:00:00Z', '2026-08-04T00:00:01Z', 'seed')""",
            (mid, payload_json, f"hash-{mid}"),
        )
    conn.commit()
    conn.close()

    applied = migrate.apply_all("odds", db_file=db, quiet=True)
    # 不带 migrations_dir 时从真实目录(非 staged 副本)接着应用——staged 只到
    # 0008,真实目录此时还有 0009(lineup_type)与 0010(PIPELINE_REDESIGN_V2 P4
    # 的 postmatch_retry_state,与本测试主题无关但同样待应用)两个未应用版本。
    assert applied == 2

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bronze_fm_lineup_snap)")}
    assert "lineup_type" in cols

    def _lineup_types():
        return {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT fotmob_match_id, lineup_type FROM bronze_fm_lineup_snap"
                " WHERE poll_run_id='seed'"
            )
        }

    expected = {mid: exp for mid, _, exp in ROWS}
    assert _lineup_types() == expected

    # 幂等性 1:直接重跑同一条回填 UPDATE(不经过 migration ledger)——已回填的
    # 行不能被改写,仍然缺失键的行继续是 NULL(不能第二次跑就冒出编造值),不报错。
    before = _lineup_types()
    conn.execute(
        "UPDATE bronze_fm_lineup_snap SET lineup_type = json_extract(payload_json, '$.lineup_type')"
        " WHERE lineup_type IS NULL"
    )
    conn.commit()
    assert _lineup_types() == before

    # 幂等性 2:migration runner 自身重跑(走 ledger)不重复应用、不报错。
    assert migrate.apply_all("odds", db_file=db, quiet=True) == 0
    assert _lineup_types() == before

    idx_names = {r[1] for r in conn.execute("PRAGMA index_list(bronze_fm_lineup_snap)")}
    assert "idx_fm_lineup_type" in idx_names
    conn.close()


def test_second_run_idempotent(tmp_path):
    db = tmp_path / "platform.db"
    first = migrate.apply_all("platform", db_file=db, quiet=True)
    assert first >= 2
    second = migrate.apply_all("platform", db_file=db, quiet=True)
    assert second == 0
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()
    assert n == first


def test_checksum_drift_detected(tmp_path):
    src = migrate.MIGRATIONS_ROOT / "platform"
    mdir = tmp_path / "migrations"
    shutil.copytree(src, mdir)
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=mdir, quiet=True)
    # 篡改已应用迁移
    f = mdir / "0002_seed.sql"
    f.write_text(f.read_text() + "\n-- tampered\n")
    with pytest.raises(RuntimeError, match="checksum"):
        migrate.apply_all("platform", db_file=db, migrations_dir=mdir, quiet=True)


def test_status_reports_checksum_drift_without_applying(tmp_path):
    """status() 必须能在不调用 apply_all() 的情况下发现漂移(restore_verify/
    ops_check 只想"检查",不想真的对被检查的库副本执行一次迁移)。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    st = migrate.status("platform", db_file=db)
    assert st["checksum_drift"] == []
    # 直接改写 schema_migrations 里的 checksum,模拟"记录的校验和与当前文件不符"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
    conn.commit()
    conn.close()
    st2 = migrate.status("platform", db_file=db)
    assert st2["checksum_drift"], "篡改 schema_migrations.checksum 后 status() 必须报告漂移"


def test_failed_migration_rolls_back(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    (mdir / "0001_good.sql").write_text("CREATE TABLE t1 (id INTEGER PRIMARY KEY);\n")
    (mdir / "0002_bad.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO no_such_table VALUES (1);\n"
    )
    db = tmp_path / "x.db"
    with pytest.raises(sqlite3.OperationalError):
        migrate.apply_all("platform", db_file=db, migrations_dir=mdir, quiet=True)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    conn.close()
    assert "t1" in tables
    assert "t2" not in tables          # 失败迁移的半成品被回滚
    assert versions == [1]             # 只记录了成功的 0001


def test_upgrade_on_copy_of_existing_db(tmp_path):
    """模拟"临时副本升级":先建库,复制一份,再对副本重跑,应幂等无损。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    copy = tmp_path / "platform_copy.db"
    shutil.copyfile(db, copy)
    assert migrate.apply_all("platform", db_file=copy, quiet=True) == 0
    conn = sqlite3.connect(copy)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def _seed_snapshot(conn, locked=False, official=False):
    conn.execute(
        "INSERT OR IGNORE INTO model_versions (id, algorithm, created_at)"
        " VALUES ('m1', 'dixon-coles', '2026-07-19T00:00:00Z')")
    conn.execute(
        """INSERT INTO prediction_snapshots
           (id, match_id, model_version_id, generated_at, locked_at, prediction_hash,
            home_win, draw, away_win, status, is_official, created_at)
           VALUES ('snap1', 1001, 'm1', '2026-07-19T00:00:00Z', ?, 'h',
                   0.5, 0.3, 0.2, ?, ?, '2026-07-19T00:00:00Z')""",
        ("2026-07-19T01:00:00Z" if locked else None,
         "locked" if locked else "draft",
         1 if official else 0))


def test_locked_snapshot_editable_but_not_deletable_at_db_layer(tmp_path):
    """migration 0007(2026-08-05):锁定行的 UPDATE 触发器已移除——概率/hash 等
    实质字段现在可以被直接 UPDATE(应用层唯一入口是 edit_snapshot,见
    tests/backend/test_predictions.py::TestEditSnapshot,这里只测 DB 层本身不再
    拦截)。DELETE 触发器不受影响,物理删除依然被拒绝。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    _seed_snapshot(conn, locked=True, official=True)
    conn.commit()
    # 改概率 → 现在允许(DB 层不再兜底,留痕是 edit_snapshot 的应用层职责)
    conn.execute("UPDATE prediction_snapshots SET home_win=0.9, draw=0.05, away_win=0.05 WHERE id='snap1'")
    # 改 hash → 同样允许
    conn.execute("UPDATE prediction_snapshots SET prediction_hash='forged' WHERE id='snap1'")
    row = conn.execute("SELECT home_win, prediction_hash FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == (0.9, "forged")
    # 物理删除 → 依然拒绝(trg_pred_snap_no_delete 未受本次改动影响)
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute("DELETE FROM prediction_snapshots WHERE id='snap1'")
    # 撤回状态 → 允许(status 从未受锁定触发器保护)
    conn.execute("UPDATE prediction_snapshots SET status='retracted' WHERE id='snap1'")
    row = conn.execute("SELECT status, home_win FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == ("retracted", 0.9)
    conn.close()


def test_unlocked_draft_is_editable(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    _seed_snapshot(conn, locked=False, official=False)
    conn.execute("UPDATE prediction_snapshots SET home_win=0.4, draw=0.35, away_win=0.25 WHERE id='snap1'")
    conn.execute("DELETE FROM prediction_snapshots WHERE id='snap1'")
    conn.commit()
    conn.close()


def test_probability_sum_check(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO model_versions (id, algorithm, created_at) VALUES ('m1','dc','2026-07-19T00:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO prediction_snapshots
               (id, match_id, model_version_id, generated_at, prediction_hash,
                home_win, draw, away_win, created_at)
               VALUES ('bad', 1, 'm1', '2026-07-19T00:00:00Z', 'h', 0.5, 0.3, 0.5, '2026-07-19T00:00:00Z')""")
    conn.close()


def test_lottery_entitlement_present_in_all_three_plans_fresh_db(tmp_path):
    """瑞典超接入(0004):league:lottery 必须在全新库的 free/pro/premium 三档都出现,
    不能只加在 free 上指望"继承"(plan_entitlements 是逐档全量枚举,见 0002_seed.sql)。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    plans = {r[0] for r in conn.execute(
        "SELECT plan_id FROM plan_entitlements WHERE entitlement='league:lottery'")}
    conn.close()
    # 0009/0010 起 member 基线与 daily_picks 物化并集亦含 league:lottery;
    # 0012(权限矩阵互换)把 league:lottery 从 free 移除,free 改持有
    # league:top5 + league:european_cup(见 0012_league_access_swap.sql)。
    assert plans == {"pro", "premium", "member", "daily_picks"}


def test_lottery_entitlement_upgrade_from_pre_0004_db_no_duplicates(tmp_path):
    """升级场景(0004 之前只跑到 0003 的既有库):升级后 league:lottery 正确补齐到
    三档且不重复;既有 plan_entitlements 行不受影响。"""
    staged = tmp_path / "staged_migrations"
    staged.mkdir()
    src = migrate.MIGRATIONS_ROOT / "platform"
    for name in ("0001_init.sql", "0002_seed.sql", "0003_snapshot_kickoff_provenance.sql"):
        shutil.copyfile(src / name, staged / name)
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=staged, quiet=True)
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM plan_entitlements").fetchone()[0]
    assert conn.execute(
        "SELECT COUNT(*) FROM plan_entitlements WHERE entitlement='league:lottery'"
    ).fetchone()[0] == 0
    conn.close()

    # 升级:指向真实(含 0004/0005)的迁移目录
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    after = conn.execute("SELECT COUNT(*) FROM plan_entitlements").fetchone()[0]
    # 0004 +3(league:lottery 三档);0009 +13(member 基线);
    # 0010 +16(member 的 reco:track_record 1 行 + daily_picks 物化并集 15 行);
    # 0012 +6/-1(free/pro/premium/member/daily_picks 各 +1 european_cup,
    # free -1 lottery)= 净 +5
    assert after == before + 3 + 13 + 16 + 6 - 1
    plans = {r[0] for r in conn.execute(
        "SELECT plan_id FROM plan_entitlements WHERE entitlement='league:lottery'")}
    assert plans == {"pro", "premium", "member", "daily_picks"}

    # 重复应用同一套完整迁移:不产生重复行(第三次幂等检查)
    migrate.apply_all("platform", db_file=db, quiet=True)
    dup_check = conn.execute("SELECT COUNT(*) FROM plan_entitlements").fetchone()[0]
    assert dup_check == after
    conn.close()


def test_model_league_scope_columns_and_league_id_editable(tmp_path):
    """瑞典超接入(0005):model_versions.applicable_league_ids /
    prediction_snapshots.league_id 列存在。此前(migration 0005)锁定后 league_id
    不可再改写(trg_pred_snap_locked_league_immutable);该触发器已被 migration
    0007(2026-08-05)移除——锁定行现在允许直接编辑,包括 league_id。"""
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    mv_cols = {r[1] for r in conn.execute("PRAGMA table_info(model_versions)")}
    snap_cols = {r[1] for r in conn.execute("PRAGMA table_info(prediction_snapshots)")}
    assert "applicable_league_ids" in mv_cols
    assert "league_id" in snap_cols

    _seed_snapshot(conn, locked=False, official=False)
    conn.execute("UPDATE prediction_snapshots SET league_id=47 WHERE id='snap1'")
    conn.execute(
        "UPDATE prediction_snapshots SET status='locked', locked_at='2026-07-19T01:00:00Z', is_official=1"
        " WHERE id='snap1'"
    )
    conn.commit()
    conn.execute("UPDATE prediction_snapshots SET league_id=999 WHERE id='snap1'")
    row = conn.execute("SELECT league_id FROM prediction_snapshots WHERE id='snap1'").fetchone()
    assert row == (999,)
    conn.close()


# ── reco 赔率合约 migration(0014_reco_odds_contract)──────────────────────
#
# 背景:reco_legs.odds 一直被 settle_slip 当十进制赔率直接相乘,但 NowGoal 的
# ou/ah/corners_ou 三个市场给的是港赔(数值经常 <1)。0014 给 reco_legs 加
# 溯源 + canonical_decimal_odds 字段,不改变任何既有行的语义——已结算行的
# canonical_decimal_odds 必须原样 backfill 成旧 odds 列的值(系统过去就是这样
# 用它的),不做 HK→decimal 重新解释。


def test_reco_odds_contract_columns_present_fresh_db(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")

    leg_cols = {r[1] for r in conn.execute("PRAGMA table_info(reco_legs)")}
    for col in (
        "source_odds", "odds_format", "canonical_decimal_odds", "provider",
        "company_id", "company_name", "snapshot_ref", "observed_at", "line",
        "side", "payload_hash", "entry_type",
    ):
        assert col in leg_cols, f"reco_legs 缺少新列 {col}"

    slip_cols = {r[1] for r in conn.execute("PRAGMA table_info(reco_slips)")}
    assert "settle_source" in slip_cols

    conn.execute(
        "INSERT INTO users (id, display_name, created_at, updated_at) VALUES"
        " ('u1', '测试用户', 'now', 'now')")

    # entry_type 非法值必须被 CHECK 拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reco_slips (id, slip_date, title, combo_type, status,"
            " created_by, created_at, updated_at) VALUES"
            " ('s-bad', '2026-08-16', 't', 'single', 'draft', 'u1', 'now', 'now')")
        conn.execute(
            "INSERT INTO reco_legs (id, slip_id, match_desc, market, selection,"
            " odds, entry_type, sort_order, created_at) VALUES"
            " ('l-bad', 's-bad', 'A vs B', '1x2', '主胜', 1.5, 'not_a_real_type', 0, 'now')")

    # odds_format 非法值必须被 CHECK 拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reco_legs (id, slip_id, match_desc, market, selection,"
            " odds, odds_format, sort_order, created_at) VALUES"
            " ('l-bad2', 's-bad', 'A vs B', '1x2', '主胜', 1.5, 'unknown', 0, 'now')")

    # half_win/half_loss 现在是 reco_legs.result 与 reco_slips.result 的合法值
    conn.execute(
        "INSERT INTO reco_slips (id, slip_date, title, combo_type, status, result,"
        " return_units, created_by, created_at, updated_at) VALUES"
        " ('s-ok', '2026-08-16', 't', 'single', 'settled', 'half_loss',"
        " 0.5, 'u1', 'now', 'now')")
    conn.execute(
        "INSERT INTO reco_legs (id, slip_id, match_desc, market, selection, odds,"
        " result, canonical_decimal_odds, entry_type, sort_order, created_at) VALUES"
        " ('l-ok', 's-ok', 'A vs B', '1x2', '主胜', 1.5, 'half_win', 1.5,"
        " 'legacy_manual', 0, 'now')")
    row = conn.execute(
        "SELECT result FROM reco_legs WHERE id='l-ok'").fetchone()
    assert row[0] == "half_win"
    conn.close()


def test_reco_odds_contract_backfill_preserves_existing_settled_rows(tmp_path):
    """升级场景:0013 之前建的库里已经有一张真实结算过的推荐单(旧 5 字段
    reco_legs schema)。升级到 0014 后,这些既有行的 result/return_units/odds
    一个字节都不能变;新 canonical_decimal_odds 必须直接照抄旧 odds 值
    (不做任何 HK→decimal 重新解释);odds_format 保持 NULL;entry_type 保持
    默认 legacy_manual(诚实标注"缺乏真实溯源",不是惩罚)。"""
    staged = tmp_path / "staged_migrations"
    staged.mkdir()
    src = migrate.MIGRATIONS_ROOT / "platform"
    pre_0014 = [
        (v, name, path)
        for v, name, path in migrate.migration_files(src)
        if v <= 13
    ]
    assert pre_0014 and pre_0014[-1][0] == 13, "0013 必须存在,否则本升级场景前提不成立"
    for _, name, path in pre_0014:
        shutil.copyfile(path, staged / name)

    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=staged, quiet=True)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO users (id, display_name, created_at, updated_at) VALUES"
        " ('u-legacy', '旧管理员', '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO reco_slips (id, slip_date, title, combo_type, status, result,"
        " return_units, published_at, settled_at, created_by, created_at, updated_at,"
        " edit_count) VALUES ('slip-legacy', '2026-07-01', '旧单', 'parlay', 'settled',"
        " 'win', 3.42, '2026-07-01T00:00:00Z', '2026-07-02T00:00:00Z', 'u-legacy',"
        " '2026-07-01T00:00:00Z', '2026-07-02T00:00:00Z', 0)")
    conn.execute(
        "INSERT INTO reco_legs (id, slip_id, match_id, match_desc, market, selection,"
        " odds, result, sort_order, created_at) VALUES"
        " ('leg-legacy-1', 'slip-legacy', 1001, 'A vs B', '1x2', '主胜', 1.9, 'win', 0,"
        " '2026-07-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO reco_legs (id, slip_id, match_id, match_desc, market, selection,"
        " odds, result, sort_order, created_at) VALUES"
        " ('leg-legacy-2', 'slip-legacy', 1002, 'C vs D', 'ou', '大2.5', 1.8, 'win', 1,"
        " '2026-07-01T00:00:00Z')")
    conn.commit()
    conn.close()

    # 升级:指向真实迁移目录(含 0014 及之后的全部迁移)
    applied = migrate.apply_all("platform", db_file=db, quiet=True)
    # 0014(reco 赔率合约)+ 0015(reco 按场授权)+ 0016(兑换码改为按场,
    # 2026-08-16)+ 0017(兑换码整体下架,2026-08-17)+ 0018(每日公推板块,
    # 2026-09)= 5 个新迁移。
    assert applied == 5

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    slip = conn.execute("SELECT * FROM reco_slips WHERE id='slip-legacy'").fetchone()
    assert slip["result"] == "win"
    assert slip["return_units"] == pytest.approx(3.42)

    legs = {r["id"]: r for r in conn.execute(
        "SELECT * FROM reco_legs WHERE slip_id='slip-legacy'").fetchall()}
    assert legs["leg-legacy-1"]["odds"] == pytest.approx(1.9)
    assert legs["leg-legacy-1"]["result"] == "win"
    assert legs["leg-legacy-1"]["canonical_decimal_odds"] == pytest.approx(1.9)
    assert legs["leg-legacy-1"]["odds_format"] is None
    assert legs["leg-legacy-1"]["entry_type"] == "legacy_manual"

    assert legs["leg-legacy-2"]["odds"] == pytest.approx(1.8)
    assert legs["leg-legacy-2"]["canonical_decimal_odds"] == pytest.approx(1.8)
    assert legs["leg-legacy-2"]["odds_format"] is None
    assert legs["leg-legacy-2"]["entry_type"] == "legacy_manual"

    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    # 幂等:再跑一次不应重复应用
    assert migrate.apply_all("platform", db_file=db, quiet=True) == 0


# ── 每日精选按场授权(0015_reco_access_grants)────────────────────────────
#
# 背景(2026-08-16,产品权限口径修正,经用户批准):取代旧的全局 reco:daily
# 布尔权益,授权必须按"用户 + 单条 slip"授予。本迁移只新增表,不改写/迁移
# 任何既有数据。


def test_reco_access_grants_table_fresh_db(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")

    cols = {r[1] for r in conn.execute("PRAGMA table_info(reco_access_grants)")}
    assert cols == {
        "id", "user_id", "slip_id", "status", "granted_at", "granted_by",
        "revoked_at", "revoked_by", "note", "created_at", "updated_at",
    }

    conn.execute(
        "INSERT INTO users (id, display_name, created_at, updated_at) VALUES"
        " ('u1', '管理员', 'now', 'now'), ('u2', '用户', 'now', 'now')")
    conn.execute(
        "INSERT INTO reco_slips (id, slip_date, title, combo_type, status,"
        " created_by, created_at, updated_at) VALUES"
        " ('s1', '2026-08-16', 't', 'single', 'draft', 'u1', 'now', 'now')")
    conn.execute(
        "INSERT INTO reco_access_grants (id, user_id, slip_id, status, granted_at,"
        " granted_by, created_at, updated_at) VALUES"
        " ('g1', 'u2', 's1', 'active', 'now', 'u1', 'now', 'now')")
    conn.commit()

    # status 非法值必须被 CHECK 拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reco_access_grants (id, user_id, slip_id, status,"
            " granted_at, granted_by, created_at, updated_at) VALUES"
            " ('g-bad', 'u2', 's1', 'not_a_real_status', 'now', 'u1', 'now', 'now')")

    # user_id/slip_id/granted_by 外键必须被强制
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reco_access_grants (id, user_id, slip_id, status,"
            " granted_at, granted_by, created_at, updated_at) VALUES"
            " ('g-bad2', 'no-such-user', 's1', 'active', 'now', 'u1', 'now', 'now')")

    # 同一用户对同一 slip 不能同时存在两条 active 授权(partial unique index)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reco_access_grants (id, user_id, slip_id, status,"
            " granted_at, granted_by, created_at, updated_at) VALUES"
            " ('g-dup', 'u2', 's1', 'active', 'now', 'u1', 'now', 'now')")

    # 撤销后允许对同一 (user_id, slip_id) 重新授予(新的 active 行)
    conn.execute("UPDATE reco_access_grants SET status='revoked' WHERE id='g1'")
    conn.execute(
        "INSERT INTO reco_access_grants (id, user_id, slip_id, status, granted_at,"
        " granted_by, created_at, updated_at) VALUES"
        " ('g2', 'u2', 's1', 'active', 'now', 'u1', 'now', 'now')")
    conn.commit()

    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    # 幂等:再跑一次不应重复应用
    assert migrate.apply_all("platform", db_file=db, quiet=True) == 0


# ── 兑换码整体下架(0017_drop_redeem_codes)────────────────────────────────
#
# 背景(站长明确决定,2026-08-17):兑换码(CDKEY)功能不是简化或降级,是
# 完整删除——redeem_codes 表连同后端命令/端点、前端组件一起移除。每日精选
# 的授权路径不受影响,继续保留且是唯一入口:管理员直接为"用户 + 单条 slip"
# 授权(0015_reco_access_grants.sql 建立的 reco_access_grants 表)。


def test_redeem_codes_table_dropped_fresh_db(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    assert "redeem_codes" not in _tables(db)
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_redeem_codes_table_dropped_upgrade_from_pre_0017_db(tmp_path):
    """升级场景:既有库已经跑到 0016(redeem_codes 表按新 slip_id schema
    存在,真实 data/platform.db 在本次改动前经只读查询确认该表为空)。升级到
    0017 后表消失,其它表不受影响,integrity_check 正常,没有任何表
    REFERENCES redeem_codes(否则 DROP TABLE 会因外键失败)。"""
    staged = tmp_path / "staged_migrations"
    staged.mkdir()
    src = migrate.MIGRATIONS_ROOT / "platform"
    pre_0017 = [
        (v, name, path)
        for v, name, path in migrate.migration_files(src)
        if v <= 16
    ]
    assert pre_0017 and pre_0017[-1][0] == 16, "0016 必须存在,否则本升级场景前提不成立"
    for _, name, path in pre_0017:
        shutil.copyfile(path, staged / name)

    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=staged, quiet=True)
    assert "redeem_codes" in _tables(db)

    applied = migrate.apply_all("platform", db_file=db, quiet=True)
    assert applied == 2  # 0017(兑换码下架)+ 0018(每日公推板块,2026-09)

    assert "redeem_codes" not in _tables(db)
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    # 幂等:再跑一次不应重复应用
    assert migrate.apply_all("platform", db_file=db, quiet=True) == 0


def test_cli_all_with_data_dir(tmp_path):
    """CLI 形态:python -m backend.db.migrate --all --data-dir <tmp> 三库全建。"""
    proc = subprocess.run(
        [sys.executable, "-m", "backend.db.migrate", "--all", "--data-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=str(migrate.PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "platform.db").exists()
    assert (tmp_path / "odds.db").exists()
    assert (tmp_path / "allwin.db").exists()  # core:仅 schema_migrations,现有库不受影响


# ── Schedule-state migration manifest identity closure ─────────────────────


def _write_manifest_file(directory: Path, name: str, sql: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(sql, encoding="utf-8")


@pytest.mark.parametrize(
    "names",
    [
        (),
        ("0002_second.sql",),
        ("0001_first.sql", "0003_third.sql"),
    ],
    ids=("empty-manifest", "missing-first-version", "missing-middle-version"),
)
def test_manifest_gap_is_rejected_before_database_creation(tmp_path, names):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in names:
        _write_manifest_file(
            migrations,
            name,
            f"CREATE TABLE t_{name[:4]} (id INTEGER PRIMARY KEY);\n",
        )
    db = tmp_path / "must-not-exist.db"

    with pytest.raises(ValueError, match="连续"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert not db.exists()


def test_duplicate_manifest_version_is_rejected_before_database_creation(tmp_path):
    migrations = tmp_path / "migrations"
    _write_manifest_file(
        migrations,
        "0001_first.sql",
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n",
    )
    _write_manifest_file(
        migrations,
        "0001_second.sql",
        "CREATE TABLE second_table (id INTEGER PRIMARY KEY);\n",
    )
    db = tmp_path / "must-not-exist.db"

    with pytest.raises(ValueError, match="重复版本号"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert not db.exists()


def test_ledger_filename_mismatch_is_rejected_without_database_change(tmp_path):
    migrations = tmp_path / "migrations"
    _write_manifest_file(
        migrations,
        "0001_first.sql",
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n",
    )
    db = tmp_path / "ledger-name.db"
    migrate.apply_all(
        "core",
        db_file=db,
        migrations_dir=migrations,
        quiet=True,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE schema_migrations SET name='0009_wrong.sql' WHERE version=1"
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()

    with pytest.raises(RuntimeError, match="identity"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert db.read_bytes() == before


def test_ledger_version_missing_from_manifest_is_rejected_without_change(tmp_path):
    migrations = tmp_path / "migrations"
    _write_manifest_file(
        migrations,
        "0001_first.sql",
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n",
    )
    db = tmp_path / "orphan-ledger.db"
    migrate.apply_all(
        "core",
        db_file=db,
        migrations_dir=migrations,
        quiet=True,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO schema_migrations "
        "(version, name, checksum, applied_at) "
        "VALUES (2, '0002_missing.sql', ?, '2026-07-26T00:00:00Z')",
        ("0" * 64,),
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()

    with pytest.raises(RuntimeError, match="manifest"):
        migrate.apply_all(
            "core",
            db_file=db,
            migrations_dir=migrations,
            quiet=True,
        )

    assert db.read_bytes() == before


# ── 每日公推板块(0018_reco_board)────────────────────────────────────────
#
# 背景(2026-09,产品新增,经用户批准):新增与「每日精选」并列的完全公开
# 板块「每日公推」,不需要登录/授权。两个板块共用 reco_slips/reco_legs,
# 只新增一个 board 归属字段,DEFAULT 'daily_pick' 保证历史数据零变化。


def test_reco_board_column_fresh_db(tmp_path):
    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, quiet=True)
    conn = sqlite3.connect(db)
    slip_cols = {r[1] for r in conn.execute("PRAGMA table_info(reco_slips)")}
    assert "board" in slip_cols
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    conn.execute(
        "INSERT INTO users (id, display_name, created_at, updated_at) VALUES"
        " ('u1', '测试用户', 'now', 'now')")
    conn.execute(
        "INSERT INTO reco_slips (id, slip_date, title, combo_type, status,"
        " created_by, created_at, updated_at) VALUES"
        " ('s1', '2026-09-01', 't', 'single', 'draft', 'u1', 'now', 'now')")
    assert conn.execute(
        "SELECT board FROM reco_slips WHERE id='s1'").fetchone()[0] == "daily_pick"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reco_slips (id, slip_date, title, combo_type, status,"
            " created_by, created_at, updated_at, board) VALUES"
            " ('s-bad', '2026-09-01', 't', 'single', 'draft', 'u1', 'now', 'now',"
            " 'not_a_real_board')")
    conn.close()


def test_reco_board_upgrade_from_pre_0018_db_defaults_existing_rows(tmp_path):
    """升级场景:既有库已经跑到 0017(reco_slips 里已有历史精选数据,真实
    data/platform.db 生产库在本次改动前经只读查询确认为 21 行,20 settled
    + 1 voided)。升级到 0018 后,全部历史行必须自动落在 board='daily_pick',
    现有按精选口径的一切查询结果不受影响。"""
    staged = tmp_path / "staged_migrations"
    staged.mkdir()
    src = migrate.MIGRATIONS_ROOT / "platform"
    pre_0018 = [
        (v, name, path)
        for v, name, path in migrate.migration_files(src)
        if v <= 17
    ]
    assert pre_0018 and pre_0018[-1][0] == 17, "0017 必须存在,否则本升级场景前提不成立"
    for _, name, path in pre_0018:
        shutil.copyfile(path, staged / name)

    db = tmp_path / "platform.db"
    migrate.apply_all("platform", db_file=db, migrations_dir=staged, quiet=True)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO users (id, display_name, created_at, updated_at) VALUES"
        " ('u1', '测试用户', 'now', 'now')")
    for i in range(3):
        conn.execute(
            "INSERT INTO reco_slips (id, slip_date, title, combo_type, status,"
            " created_by, created_at, updated_at) VALUES"
            f" ('s{i}', '2026-08-2{i}', 't{i}', 'single', 'settled', 'u1', 'now', 'now')")
    conn.commit()

    applied = migrate.apply_all("platform", db_file=db, quiet=True)
    assert applied == 1  # 只有 0018

    boards = {r[0] for r in conn.execute("SELECT board FROM reco_slips")}
    assert boards == {"daily_pick"}, "既有精选数据升级后必须全部归为 daily_pick,零变化"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
