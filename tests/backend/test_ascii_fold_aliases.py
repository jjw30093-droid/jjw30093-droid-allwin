"""变音符 ASCII 折叠别名 + 跨联赛候选过滤(数据管道重建 Phase 3,全离线)。

变音符是新联赛(荷甲/葡超/巴甲/欧协联)出赔率的头号障碍:NowGoal 发 ASCII,
FotMob 发带变音符原文,auto-seed 别名保留变音符 → 新联赛整批 needs_review → 零赔率。
本测试钉住:折叠别名生成、撞名 fail-closed、不改 _norm 语义、跨联赛占位过滤。
"""

import pytest

from backend.db.connections import connect_rw
from backend.ingest.entity_resolution import (
    _ascii_fold,
    _candidate_matches,
    seed_ascii_fold_aliases,
)
from backend.db.connections import connect_ro
from .coreseed import seed_core_schema


@pytest.fixture
def odds(data_dir):
    from backend.db import migrate
    migrate.apply_all("odds", quiet=True)


class TestAsciiFold:
    def test_fold_strips_diacritics(self):
        assert _ascii_fold("Häcken") == "hacken"
        assert _ascii_fold("Mjällby") == "mjallby"
        assert _ascii_fold("Deportivo A Coruña") == "deportivo a coruna"
        assert _ascii_fold("PSV") == "psv"        # 无变音符不变

    def test_seed_adds_folded_alias(self, odds):
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (8428, 'häcken', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        out = seed_ascii_fold_aliases(conn)
        assert out["added"] == 1
        # NowGoal 发 'Hacken' → _norm='hacken' 现在能命中
        got = {r[0] for r in conn.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias='hacken'")}
        conn.close()
        assert got == {8428}

    def test_collision_rejected_fail_closed(self, odds):
        """两个不同球队的变音符别名折叠到同一 ASCII 串 → 整串拒绝,不写歧义别名。"""
        conn = connect_rw("odds")
        # 假想:'Müller FC'(队1)与 'Muller FC'... 构造真实撞名:两队都带变音符折叠成同串
        conn.executemany(
            "INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
            " VALUES (?, ?, 'dim_match', '2026-01-01T00:00:00Z')",
            [(111, "fóo"), (222, "fõo")])   # 都折叠成 'foo'
        conn.commit()
        out = seed_ascii_fold_aliases(conn)
        assert out["added"] == 0
        assert ("foo", [111, 222]) in out["rejected"]
        n = conn.execute("SELECT COUNT(*) FROM dim_team_alias WHERE alias='foo'").fetchone()[0]
        conn.close()
        assert n == 0    # 歧义别名绝不落库

    def test_idempotent(self, odds):
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (8428, 'häcken', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        seed_ascii_fold_aliases(conn)
        out2 = seed_ascii_fold_aliases(conn)   # 重跑
        conn.close()
        assert out2["added"] == 0

    def test_does_not_touch_non_diacritic_aliases(self, odds):
        conn = connect_rw("odds")
        conn.execute("INSERT INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                     " VALUES (100, 'arsenal', 'dim_match', '2026-01-01T00:00:00Z')")
        conn.commit()
        out = seed_ascii_fold_aliases(conn)
        n = conn.execute("SELECT COUNT(*) FROM dim_team_alias WHERE canonical_team_id=100").fetchone()[0]
        conn.close()
        assert out["added"] == 0 and n == 1   # 无变音符,不生成冗余行


class TestCrossLeagueCandidateFilter:
    def test_only_registered_leagues_are_candidates(self, data_dir):
        """NowGoal 全球日程不得让未登记联赛的比赛进入候选(防永久 needs_review 占位)。"""
        from backend.db import migrate
        migrate.apply_all("odds", quiet=True)
        conn_core = connect_rw("core")
        seed_core_schema(conn_core)
        # 47(英超,已登记)+ 9999(未登记)同日各一场
        conn_core.executemany(
            "INSERT INTO dim_match (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,"
            "Home_Team_Name,Away_Team_Name,status) VALUES (?,?,?,?,?,?,?,?,'NotStarted')",
            [(1, "2026/2027", 47, "2026-08-12", 100, 200, "H", "A"),
             (2, "2026", 9999, "2026-08-12", 300, 400, "X", "Y")])
        conn_core.commit()
        conn_core.close()
        conn_odds = connect_ro("odds")
        conn_core_ro = connect_ro("core")
        cands = _candidate_matches(conn_odds, conn_core_ro, "2026-08-12")
        conn_odds.close(); conn_core_ro.close()
        ids = {int(r["Match_ID"]) for r in cands}
        assert 1 in ids       # 英超 47 已登记 → 候选
        assert 2 not in ids   # 未登记 9999 → 排除,不会占位
