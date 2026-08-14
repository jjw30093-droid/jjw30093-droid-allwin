"""tests/e2e/seed_e2e.py 永久回归测试(生产可靠性收口:E2E kickoff provenance)。

背景(本轮审计,真实执行确认,不是假设):修复前 seed_e2e.py 用
`f"{match['Date']}T12:00:00Z"` 拼出看似精确的开球时间,但从未设置
kickoff_precision="exact"/kickoff_source,导致 publish_snapshot 的
`_require_precise_kickoff` 正确拒绝(imprecise_kickoff),Playwright webServer
因而永远起不来。修复只改了 seed 脚本本身,`_require_precise_kickoff`/
`_require_pre_kickoff`/publish_snapshot/lock_snapshot/正式样本资格查询/
migration 触发器一行未动——这里的测试同时要证明"生产门禁没有被放宽"
(用一个刻意伪造的、缺失合法 provenance 的快照仍然会被拒绝)。

真实调用 `python -m tests.e2e.seed_e2e` 子进程(与 Playwright webServer 命令完全
一致,不是简化重写),只断言 data/e2e/platform.db 与真实 data/*.db 是否变化,
不 mock 任何门禁函数。
"""

import subprocess
import sys
from pathlib import Path

import pytest

from backend.db.util import normalize_exact_kickoff, parse_strict_utc

ROOT = Path(__file__).resolve().parents[2]
REAL_DB_NAMES = ("allwin.db", "platform.db", "odds.db")


def _real_db_snapshot() -> dict:
    snap = {}
    for name in REAL_DB_NAMES:
        p = ROOT / "data" / name
        st = p.stat()
        snap[name] = (st.st_size, st.st_mtime)
    return snap


def _run_seed() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tests.e2e.seed_e2e"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )


@pytest.mark.skipif(
    not (ROOT / "data" / "allwin.db").exists(),
    reason="需要真实 data/allwin.db(core 只读源)存在;CI/沙盒无此文件时跳过",
)
class TestE2eSeedProvenance:
    def test_seed_succeeds_and_is_repeatable(self):
        before = _real_db_snapshot()
        r1 = _run_seed()
        assert r1.returncode == 0, f"首次 seed 失败:\n{r1.stdout}\n{r1.stderr}"
        r2 = _run_seed()
        assert r2.returncode == 0, f"二次 seed(重复运行)失败:\n{r2.stdout}\n{r2.stderr}"
        after = _real_db_snapshot()
        assert before == after, "seed_e2e 不得改动真实 data/*.db(size,mtime 必须不变)"

    def test_seeded_snapshots_are_locked_official_with_verbatim_core_provenance(self):
        """2026-08-11 起种子改为:首页 featured 全部候选(北欧 67/59 前 3 场)
        + 一条隔离编辑目标,共 N 条快照(N 随真实赛程变化,≥2);kickoff 三元组
        不再合成,必须逐字段等于核心库 dim_match 的真实值(比旧的
        e2e_fixture 字符串断言更强:任何伪造/篡改来源都会与核心库对不上)。"""
        r = _run_seed()
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

        import sqlite3

        seed_info = (ROOT / "data" / "e2e" / "seed_info.txt").read_text("utf-8")

        def info(key: str) -> str:
            for line in seed_info.splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1]
            raise AssertionError(f"seed_info.txt 缺 {key}")

        featured_id = int(info("match_id"))
        edit_id = int(info("edit_match_id"))
        assert featured_id != edit_id, "编辑目标必须与主种子(48% 断言)物理隔离"

        conn = sqlite3.connect(f"file:{ROOT}/data/e2e/platform.db?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        core = sqlite3.connect(f"file:{ROOT}/data/e2e/allwin.db?mode=ro", uri=True)
        core.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM prediction_snapshots").fetchall()
            match_ids = {row["match_id"] for row in rows}
            assert featured_id in match_ids and edit_id in match_ids
            assert len(rows) >= 2, "至少应有主种子与编辑目标两条快照"

            for row in rows:
                assert row["status"] == "locked"
                assert row["is_official"] == 1
                assert row["kickoff_precision"] == "exact"

                core_row = core.execute(
                    "SELECT kickoff_at_utc, kickoff_precision, kickoff_source"
                    " FROM dim_match WHERE Match_ID=?",
                    (row["match_id"],),
                ).fetchone()
                assert core_row is not None
                for field in ("kickoff_at_utc", "kickoff_precision", "kickoff_source"):
                    assert row[field] == core_row[field], (
                        f"快照 {row['match_id']} 的 {field}={row[field]!r} 必须逐字"
                        f"等于核心库真实值 {core_row[field]!r},不得合成或冒充"
                    )

                # kickoff 必须真的是可严格解析、带显式时区的精确时刻。
                ko = row["kickoff_at_utc"]
                assert normalize_exact_kickoff(
                    ko, row["kickoff_precision"], row["kickoff_source"]
                ) == ko, "kickoff_at_utc 必须满足 normalize_exact_kickoff 的四项硬性条件"
                assert parse_strict_utc(ko) is not None

                # generated_at / published_at / locked_at 都必须严格早于 kickoff
                # (字符串比较安全,均为同一 'YYYY-MM-DDTHH:MM:SSZ' 定宽格式)。
                for field in ("generated_at", "published_at", "locked_at", "input_cutoff_at"):
                    value = row[field]
                    assert value is not None, f"{field} 不应为空"
                    assert value < ko, f"{field}={value!r} 必须严格早于 kickoff={ko!r}"

            # 编辑目标必须落在首页 7 天候选窗口之外(种子取 ≥ now+8d),
            # 否则 priority 排序会把被改成 0.6 的它顶成 featured。
            edit_row = next(row for row in rows if row["match_id"] == edit_id)
            featured_row = next(row for row in rows if row["match_id"] == featured_id)
            assert edit_row["kickoff_at_utc"] > featured_row["kickoff_at_utc"]
        finally:
            conn.close()
            core.close()

    def test_prediction_hash_matches_stable_formula_not_bypassed(self):
        r = _run_seed()
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"

        import sqlite3

        from backend.commands.predictions import prediction_hash_of

        conn = sqlite3.connect(f"file:{ROOT}/data/e2e/platform.db?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM prediction_snapshots").fetchone()
        finally:
            conn.close()

        expected = prediction_hash_of(
            row["match_id"], row["model_version_id"],
            (row["home_win"], row["draw"], row["away_win"]), row["generated_at"],
        )
        assert row["prediction_hash"] == expected, (
            "prediction_hash 必须来自唯一真源 prediction_hash_of,不得被种子脚本绕过"
        )

    def test_gate_is_not_weakened_forged_snapshot_without_provenance_still_rejected(self, monkeypatch):
        """反证:门禁函数本身没有被放宽——伪造一条缺 kickoff_precision/source 的
        草稿快照,publish_snapshot 必须仍然拒绝(与本轮之前完全一致的行为)。"""
        from datetime import date

        from backend.commands.predictions import (
            PredictionError,
            get_or_create_model_version,
            publish_snapshot,
            register_snapshot,
        )
        from backend.db.connections import connect_rw

        r = _run_seed()   # 先跑一次,确保 data/e2e 三库存在且已迁移
        assert r.returncode == 0

        monkeypatch.setenv("ALLWIN_DATA_DIR", str(ROOT / "data" / "e2e"))
        conn = connect_rw("platform")
        try:
            mv = get_or_create_model_version(conn, "gate-check-model", "dixon-coles")
            bad_id = register_snapshot(
                conn, match_id=999999901, kickoff_at_utc=f"{date.today().isoformat()}T12:00:00Z",
                model_version_id=mv, home_win=0.4, draw=0.3, away_win=0.3, status="draft",
            )
            with pytest.raises(PredictionError) as exc_info:
                publish_snapshot(conn, bad_id, actor=None)
            assert exc_info.value.reason == "imprecise_kickoff"
        finally:
            conn.close()
