"""模拟器参数文件的读取与按联赛切片(登录用户取非英超联赛参数用)。

2026-10-02 登录门禁:未登录只能用英超模拟器。英超页由 Next 服务端直接读文件
渲染(frontend/app/simulator/loadParams.ts);其他联赛的参数(含赔率反推的
market_lambda、让球/大小球盘口线)必须经 FastAPI 校验登录后才下发——会话
Cookie 的 Path 是 /api/v1,Next 页面拿不到登录态。

切片规则与 loadParams.ts 的 sliceForLeague / fixtureIndex / leagueOf 一一对应
(tests/backend/test_simulator_params.py 与 frontend/tests/simulator-load-params.test.ts
用同一组用例断言)。改其中一边必须同步另一边。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

log = logging.getLogger("allwin.simulator_params")

CALIBRATED_LEAGUES = (47, 87, 55, 54, 53)
DEFAULT_LEAGUE = 47

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _looks_valid(p: dict) -> bool:
    return bool(
        isinstance(p, dict)
        and (p.get("meta") or {}).get("generated_at")
        and p.get("leagues")
        and any(str(lg) in p["leagues"] for lg in CALIBRATED_LEAGUES)
        and p.get("teams")
        and p.get("players")
        and p.get("position_map")
    )


def _load_file(path: Path) -> dict | None:
    try:
        real = path.resolve(strict=True)
        mtime = real.stat().st_mtime
        with _lock:
            hit = _cache.get(str(real))
            if hit and hit[0] == mtime:
                return hit[1]
        params = json.loads(real.read_text(encoding="utf-8"))
        if not _looks_valid(params):
            log.error("simulator params invalid: %s", real.name)
            return None
        with _lock:
            _cache[str(real)] = (mtime, params)
        return params
    except (OSError, ValueError):
        return None


def load_params(env: dict | None = None) -> dict | None:
    """与 loadParams.ts 同序:SIMULATOR_PARAMS_PATH 单文件 → DIR/current.json →
    DIR 下按日期从新到旧的 simulator_params_YYYYMMDD.json → None。"""
    env = os.environ if env is None else env
    single = env.get("SIMULATOR_PARAMS_PATH")
    if single:
        return _load_file(Path(single))
    d = env.get("SIMULATOR_PARAMS_DIR")
    if not d:
        return None
    base = Path(d)
    current = _load_file(base / "current.json")
    if current:
        return current
    try:
        names = sorted(
            (n for n in os.listdir(base) if re.fullmatch(r"simulator_params_\d{8}\.json", n)),
            reverse=True,
        )
    except OSError:
        names = []
    for n in names:
        p = _load_file(base / n)
        if p:
            return p
    return None


def calibrated_available(params: dict) -> list[int]:
    return [lg for lg in CALIBRATED_LEAGUES if str(lg) in params["leagues"]]


def slice_for_league(params: dict, league_id: int) -> dict:
    """只保留该联赛的球队、球员、赛程;联赛级参数只留五大联赛,其余原样。"""
    teams = {k: t for k, t in params["teams"].items() if t.get("league_id") == league_id}
    ids: set[str] = set()
    for t in teams.values():
        ids.update(t.get("squad") or [])
        for s in ((t.get("last_lineup") or {}).get("starters") or []):
            ids.add(s["player_id"])
    players = {k: v for k, v in params["players"].items() if k in ids}
    fixtures = {k: f for k, f in (params.get("fixtures") or {}).items() if f.get("league_id") == league_id}
    leagues = {k: v for k, v in params["leagues"].items() if int(k) in CALIBRATED_LEAGUES}
    return {**params, "leagues": leagues, "teams": teams, "players": players, "fixtures": fixtures}


def fixture_index(params: dict, league_ids: set[int] | None = None) -> list[dict]:
    """有盘口反推 λ 的真实比赛小索引。`league_ids` 给定时只含这些联赛
    (未登录只给英超——盘口线本身就是赔率数据)。"""
    teams = params["teams"]

    def name(tid: int) -> str:
        return (teams.get(str(tid)) or {}).get("name_zh") or str(tid)

    out = []
    for f in (params.get("fixtures") or {}).values():
        lg = f.get("league_id")
        if not f.get("market_lambda") or lg not in CALIBRATED_LEAGUES:
            continue
        if league_ids is not None and lg not in league_ids:
            continue
        out.append({
            "match_id": f["match_id"],
            "league_id": lg,
            "home_team_id": f["home_team_id"],
            "away_team_id": f["away_team_id"],
            "home_name": name(f["home_team_id"]),
            "away_name": name(f["away_team_id"]),
            "kickoff_at_utc": f["kickoff_at_utc"],
            "status": f["status"],
            "ah_line": (f.get("ah") or {}).get("line"),
            "ou_line": (f.get("ou") or {}).get("line"),
            "final_score": f.get("final_score"),
        })
    return out
