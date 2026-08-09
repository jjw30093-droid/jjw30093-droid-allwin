"""Synchronize validated FotMob team crests into the local media cache."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from backend.db.connections import connect_ro
from backend.media.team_crests import PROVIDER, TeamCrestError, sync_team_crests


def team_ids_for_league(
    conn: sqlite3.Connection, league_id: int, season: str
) -> list[int]:
    rows = conn.execute(
        """
        SELECT Home_Team_ID AS team_id
        FROM dim_match WHERE League_ID=? AND Season=?
        UNION
        SELECT Away_Team_ID AS team_id
        FROM dim_match WHERE League_ID=? AND Season=?
        ORDER BY team_id
        """,
        (league_id, season, league_id, season),
    ).fetchall()
    team_ids = [row["team_id"] for row in rows]
    if not team_ids:
        raise TeamCrestError("league season has no teams")
    if any(
        isinstance(team_id, bool)
        or not isinstance(team_id, int)
        or team_id <= 0
        for team_id in team_ids
    ):
        raise TeamCrestError("league season contains invalid team id")
    return team_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync local FotMob team crests without fetching match data."
    )
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--provider", choices=[PROVIDER], default=PROVIDER)
    parser.add_argument("--max-requests", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.league_id <= 0 or not args.season.strip():
        print(json.dumps({"status": "FAILED", "reason": "invalid league selection"}))
        return 2
    try:
        with connect_ro("core") as conn:
            team_ids = team_ids_for_league(
                conn, args.league_id, args.season.strip()
            )
        result = sync_team_crests(
            team_ids,
            provider=args.provider,
            force=args.force,
            maximum_requests=args.max_requests,
            timeout=args.timeout,
            retries=args.retries,
        )
    except (sqlite3.Error, TeamCrestError):
        print(
            json.dumps(
                {"status": "FAILED", "reason": "team crest sync unavailable"}
            )
        )
        return 2
    result["league_id"] = args.league_id
    result["season"] = args.season.strip()
    result["status"] = "SUCCEEDED" if result["failed"] == 0 else "PARTIAL"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
