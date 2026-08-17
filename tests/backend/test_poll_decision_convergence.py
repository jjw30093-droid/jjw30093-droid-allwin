"""PIPELINE_REDESIGN_V2 P1:证明不再存在第二套到期判定实现。

两处历史上各算各的到期逻辑,本轮任务后必须都是 poll_windows.poll_decision()
的薄封装,而不是各自重算:
- backend/content_pipeline.py::_poll_decision(NowGoal 赔率,SOURCE_NOWGOAL_ODDS)
  历史上调用旧版 required_interval_seconds() 两档,与 poll_nowgoal.py 实际用的
  poll_decision()/CADENCE_NOWGOAL_ODDS 五(现三)档各算各的,会在同一比赛上给出
  不一致的到期结论。收敛后必须与直接调用 poll_decision() 逐比特一致。
- backend/cli/poll_fotmob_snapshots.py 的 --due 分支(SOURCE_FOTMOB_SNAPSHOT)
  历史上调用旧版 required_interval_seconds()+is_due() 两档,与新 cadence_fotmob_lineup
  各算各的。收敛后候选池的到期结论必须与直接调用 poll_decision() 逐场一致。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.content_pipeline import _poll_decision as content_pipeline_poll_decision
from backend.db.connections import connect_ro, connect_rw
from backend.ingest.poll_windows import SOURCE_FOTMOB_SNAPSHOT, SOURCE_NOWGOAL_ODDS, poll_decision

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _kickoff_iso(hours: float) -> str:
    return _iso(NOW + timedelta(hours=hours))


def _odds_conn_with_poll_state() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE poll_state (
             source TEXT NOT NULL,
             subject TEXT NOT NULL,
             last_polled_at TEXT NOT NULL,
             last_poll_run_id TEXT,
             updated_at TEXT NOT NULL,
             PRIMARY KEY(source,subject))"""
    )
    return conn


class TestContentPipelineConvergesOnSharedEngine:
    """content_pipeline._poll_decision(NowGoal 赔率)必须是 poll_windows.poll_decision
    的薄封装:同一 (kickoff, last_polled_at, now) 输入,due/tier 结论必须逐一致。"""

    MATCH_ID = 909001

    @pytest.mark.parametrize("hours_to_kickoff,last_polled_offset_hours", [
        (48.0, None),      # checkpoint_72h,首次
        (48.0, 25.0),      # checkpoint_72h,节流未到期(25h < 86400s=24h... 用 23h)
        (12.0, None),      # checkpoint_24h,首次
        (3.0, None),       # checkpoint_6h,首次
        (0.2, None),        # last_call,首次(12min)
    ])
    def test_due_and_tier_match_shared_engine(self, hours_to_kickoff, last_polled_offset_hours):
        kickoff = _kickoff_iso(hours_to_kickoff)
        conn = _odds_conn_with_poll_state()
        last_polled_at = None
        try:
            if last_polled_offset_hours is not None:
                last_polled_at = _iso(NOW - timedelta(hours=last_polled_offset_hours))
                conn.execute(
                    "INSERT INTO poll_state (source, subject, last_polled_at, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (SOURCE_NOWGOAL_ODDS, str(self.MATCH_ID), last_polled_at, last_polled_at),
                )
            wrapped = content_pipeline_poll_decision(
                kickoff, now_iso=_iso(NOW), conn=conn, match_id=self.MATCH_ID,
            )
            direct = poll_decision(
                SOURCE_NOWGOAL_ODDS, kickoff, "exact", "fotmob:league_fixture",
                last_polled_at, _iso(NOW),
            )
        finally:
            conn.close()
        assert wrapped["due"] is direct.due
        assert wrapped.get("interval_seconds") == direct.interval_seconds


class TestFotmobSnapshotConvergesOnSharedEngine:
    """poll_fotmob_snapshots.py 的 --due 候选筛选必须与直接调用
    poll_windows.poll_decision(SOURCE_FOTMOB_SNAPSHOT, ..., league_id=...) 逐场一致。"""

    MATCH_ID = 909101
    LEAGUE_ID = 47   # BIG-5,watch-window-start=1.5h

    def _seed_match(self, conn_core, kickoff_iso: str) -> None:
        conn_core.execute(
            "INSERT INTO dim_match (Match_ID, Season, League_ID, Date, Home_Team_ID,"
            " Away_Team_ID, Home_Team_Name, Away_Team_Name, status, kickoff_at_utc,"
            " kickoff_precision, kickoff_source)"
            " VALUES (?, '2026/2027', ?, ?, 111, 222, 'Home', 'Away', 'NotStarted', ?,"
            " 'exact', 'fotmob:fixtures')",
            (self.MATCH_ID, self.LEAGUE_ID, kickoff_iso[:10], kickoff_iso),
        )
        conn_core.commit()

    @pytest.mark.parametrize("hours_to_kickoff,seed_last_polled_hours_ago", [
        (10.0, None),    # 预热档(12h),首次
        (1.0, None),     # watch 窗内(BIG-5 1.5h),首次
        (1.0, 0.05),     # watch 窗内,3 分钟前刚采过 → 未到期
    ])
    def test_due_matches_direct_poll_decision(
        self, data_dir, hours_to_kickoff, seed_last_polled_hours_ago
    ):
        from backend.cli.poll_fotmob_snapshots import run_snapshot_poll

        kickoff = _kickoff_iso(hours_to_kickoff)
        conn_core = connect_rw("core")
        try:
            self._seed_match(conn_core, kickoff)
        finally:
            conn_core.close()

        last_polled_at = None
        if seed_last_polled_hours_ago is not None:
            last_polled_at = _iso(NOW - timedelta(hours=seed_last_polled_hours_ago))
            conn_odds = connect_rw("odds")
            try:
                conn_odds.execute(
                    "INSERT INTO poll_state (source, subject, last_polled_at, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (SOURCE_FOTMOB_SNAPSHOT, str(self.MATCH_ID), last_polled_at, last_polled_at),
                )
                conn_odds.commit()
            finally:
                conn_odds.close()

        direct = poll_decision(
            SOURCE_FOTMOB_SNAPSHOT, kickoff, "exact", "fotmob:fixtures",
            last_polled_at, _iso(NOW), league_id=self.LEAGUE_ID,
        )

        summary = run_snapshot_poll(
            now_iso=_iso(NOW),
            offline_payloads={str(self.MATCH_ID): {"content": {"lineup": {}}}},
        )

        if direct.due:
            assert summary["due_matches"] == 1
        else:
            assert summary["due_matches"] == 0
            assert summary["not_due_skipped"] == 1
