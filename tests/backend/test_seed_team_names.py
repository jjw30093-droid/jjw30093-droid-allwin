"""backend/i18n/seed_team_names.py(2026-08-08 通用 artifact 驱动 seeder,
替代已删除的 backend/i18n/seed_allsvenskan_teams.py)的 fail-closed 门禁测试。

覆盖:workflow_verified 永远拒绝、method 不在双重验证白名单时拒绝、
name_zh 为空/等于 name_en 时拒绝、孤儿 team_id 拒绝、批内撞名拒绝、
与既有行撞名拒绝、正常路径写入成功且幂等。
"""

from __future__ import annotations

import json

import pytest

from backend.db.connections import connect_rw
from backend.i18n.seed_team_names import SeedGateError, main, validate

from .coreseed import seed_core_schema


def _mk_core(conn, league_id=67, season="2026", team_ids=(100, 200)):
    seed_core_schema(conn)
    for i, tid in enumerate(team_ids):
        conn.execute(
            "INSERT INTO dim_match (Match_ID, League_ID, Season, Date, Home_Team_ID, "
            "Away_Team_ID, status) VALUES (?, ?, ?, '2026-03-01', ?, ?, 'Finish')",
            (i + 1, league_id, season, tid, team_ids[(i + 1) % len(team_ids)]),
        )
    conn.commit()


def _write_artifact(tmp_path, rows):
    p = tmp_path / "artifact.json"
    p.write_text(json.dumps(rows, ensure_ascii=False))
    return p


def _row(team_id, name_en, name_zh, method="qwen_websearch_agree"):
    return {"team_id": team_id, "name_en": name_en, "name_zh": name_zh,
            "method": method, "reasoning": "test"}


class TestFailClosedGates:
    def test_workflow_verified_always_rejected(self):
        with pytest.raises(SeedGateError, match="workflow_verified"):
            validate([], source="workflow_verified", in_scope_ids=set(), existing_name_zh={})

    def test_method_outside_whitelist_rejected(self):
        rows = [_row(100, "Team A", "队伍甲", method="just_qwen_no_verification")]
        with pytest.raises(SeedGateError, match="白名单"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})

    def test_all_whitelisted_methods_accepted(self):
        for method in ("qwen_websearch_agree", "websearch_override",
                      "websearch_confirmed_upgrade", "no_established_name_own_judgment"):
            rows = [_row(100, "Team A", "队伍甲", method=method)]
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})  # 不抛错

    def test_empty_name_zh_rejected(self):
        rows = [_row(100, "Team A", "")]
        with pytest.raises(SeedGateError, match="为空"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})

    def test_name_zh_without_cjk_rejected(self):
        rows = [_row(100, "Team A", "Team A Zh")]
        with pytest.raises(SeedGateError, match="不含中文"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})

    def test_name_zh_equals_name_en_rejected(self):
        # name_en 本身已是中文(如未来接入队名原文即中文的联赛)时,
        # name_zh 直接抄 name_en 属于翻译退化,必须单独拦——这条检查在
        # "name_en 是英文" 的常见情形下会被更前面的"不含中文"门禁先拦住,
        # 所以这里构造 name_en 本身含 CJK 的场景来触发这一条。
        rows = [_row(100, "队伍甲", "队伍甲")]
        with pytest.raises(SeedGateError, match="退化成原文"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})

    def test_orphan_team_id_rejected(self):
        rows = [_row(999, "Ghost Team", "幽灵队")]
        with pytest.raises(SeedGateError, match="孤儿"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})

    def test_duplicate_team_id_in_artifact_rejected(self):
        rows = [_row(100, "Team A", "队伍甲"), _row(100, "Team A", "队伍甲二")]
        with pytest.raises(SeedGateError, match="重复"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={})

    def test_within_batch_name_collision_rejected(self):
        rows = [_row(100, "Team A", "同名队"), _row(200, "Team B", "同名队")]
        with pytest.raises(SeedGateError, match="批内撞名"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100, 200}, existing_name_zh={})

    def test_collision_with_existing_i18n_rejected(self):
        rows = [_row(100, "Team A", "阿森纳")]
        with pytest.raises(SeedGateError, match="撞名"):
            validate(rows, source="qwen_max_websearch_verified",
                     in_scope_ids={100}, existing_name_zh={9825: "阿森纳"})

    def test_same_team_id_reusing_own_existing_name_is_fine(self):
        """重跑同一批 artifact(team_id 相同、name_zh 相同)不应被误判为撞名。"""
        rows = [_row(100, "Team A", "队伍甲")]
        validate(rows, source="qwen_max_websearch_verified",
                 in_scope_ids={100}, existing_name_zh={100: "队伍甲"})  # 不抛错


class TestEndToEndCli:
    def test_dry_run_writes_nothing(self, tmp_path, data_dir):
        conn = connect_rw("core")
        _mk_core(conn, team_ids=(100, 200))
        conn.close()

        artifact = _write_artifact(tmp_path, [
            _row(100, "Team A", "队伍甲"), _row(200, "Team B", "队伍乙"),
        ])
        rc = main(["--artifact", str(artifact), "--league-id", "67",
                  "--season", "2026", "--dry-run"])
        assert rc == 0
        conn2 = connect_rw("core")
        n = conn2.execute("SELECT COUNT(*) FROM dim_team_i18n").fetchone()[0]
        conn2.close()
        assert n == 0

    def test_live_writes_and_is_idempotent(self, tmp_path, data_dir):
        conn = connect_rw("core")
        _mk_core(conn, team_ids=(100, 200))
        conn.close()

        artifact = _write_artifact(tmp_path, [
            _row(100, "Team A", "队伍甲"), _row(200, "Team B", "队伍乙"),
        ])
        for _ in range(2):  # 幂等:重跑不产生重复行
            rc = main(["--artifact", str(artifact), "--league-id", "67",
                      "--season", "2026", "--live"])
            assert rc == 0
        conn2 = connect_rw("core")
        rows = {r["Team_ID"]: (r["name_zh"], r["source"])
               for r in conn2.execute("SELECT Team_ID, name_zh, source FROM dim_team_i18n")}
        conn2.close()
        assert rows == {100: ("队伍甲", "qwen_max_websearch_verified"),
                        200: ("队伍乙", "qwen_max_websearch_verified")}

    def test_gate_failure_exits_nonzero_and_writes_nothing(self, tmp_path, data_dir):
        conn = connect_rw("core")
        _mk_core(conn, team_ids=(100, 200))
        conn.close()

        artifact = _write_artifact(tmp_path, [
            _row(100, "Team A", "队伍甲"), _row(999, "Ghost", "幽灵队"),  # 孤儿 id
        ])
        rc = main(["--artifact", str(artifact), "--league-id", "67",
                  "--season", "2026", "--live"])
        assert rc == 1
        conn2 = connect_rw("core")
        n = conn2.execute("SELECT COUNT(*) FROM dim_team_i18n").fetchone()[0]
        conn2.close()
        assert n == 0  # 整批拒绝,不是部分写入

    def test_does_not_touch_other_teams(self, tmp_path, data_dir):
        conn = connect_rw("core")
        _mk_core(conn, team_ids=(100, 200))
        conn.execute(
            "INSERT INTO dim_team_i18n (Team_ID, name_en, name_zh, source, updated_at)"
            " VALUES (9825, 'Arsenal', '阿森纳', 'workflow_verified', '2026-01-01T00:00:00Z')")
        conn.commit()
        conn.close()

        artifact = _write_artifact(tmp_path, [_row(100, "Team A", "队伍甲")])
        rc = main(["--artifact", str(artifact), "--league-id", "67",
                  "--season", "2026", "--live"])
        assert rc == 0
        conn2 = connect_rw("core")
        row = conn2.execute(
            "SELECT name_zh, source FROM dim_team_i18n WHERE Team_ID=9825").fetchone()
        conn2.close()
        assert row["name_zh"] == "阿森纳"
        assert row["source"] == "workflow_verified"
