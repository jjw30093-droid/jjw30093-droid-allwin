"""backend/cli/resolve_entities.py(2026-08-24 起接入跨源归一化 + 人工校正表 +
待审核积压告警)。全离线,告警经 NOTIFY_ENABLED=0 落库验证,不发网络。
"""

import pytest

from backend.cli.resolve_entities import run
from backend.db.connections import connect_ro, connect_rw

from .coreseed import seed_core_schema


@pytest.fixture(autouse=True)
def _notify_disabled(monkeypatch):
    monkeypatch.setenv("NOTIFY_ENABLED", "0")
    monkeypatch.delenv("SERVERCHAN_SENDKEY", raising=False)


@pytest.fixture(autouse=True)
def _core_schema(data_dir):
    conn = connect_rw("core")
    seed_core_schema(conn)
    conn.commit()
    conn.close()


def _alert_rows():
    conn = connect_ro("platform")
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM pipeline_alerts ORDER BY id")]
    finally:
        conn.close()


class TestResolveEntitiesSeeding:
    def test_seeds_manual_overrides(self, data_dir):
        result = run()
        assert result["manual_override_added"] == 10   # provider_alias_overrides.py 当前 10 条
        conn = connect_ro("odds")
        got = {r[0] for r in conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='wolves'")}
        conn.close()
        assert got == {8602}

    def test_idempotent_across_all_three_seed_functions(self, data_dir):
        run()
        result2 = run()
        assert result2["ascii_fold_added"] == 0
        assert result2["canonical_form_added"] == 0
        assert result2["manual_override_added"] == 0


class TestResolveEntitiesBacklogAlert:
    def test_needs_review_backlog_fires_notify(self, data_dir):
        conn = connect_rw("odds")
        conn.execute(
            "INSERT INTO dim_match_xref (fotmob_match_id, provider, provider_match_id,"
            " review_status, created_at, updated_at)"
            " VALUES (9001, 'nowgoal', 't1', 'needs_review', '2026-08-17T00:00:00Z',"
            " '2026-08-17T00:00:00Z')"
        )
        conn.commit()
        conn.close()

        result = run()
        assert result["xref_by_status"]["needs_review"] == 1
        assert result["notify_result"] is not None
        assert result["notify_result"]["persisted"] is True
        rows = _alert_rows()
        assert any(r["source"] == "resolve_entities_backlog" for r in rows)

    def test_no_backlog_no_notify(self, data_dir):
        result = run()
        assert result["xref_by_status"] == {}
        assert result["notify_result"] is None
        assert _alert_rows() == []
