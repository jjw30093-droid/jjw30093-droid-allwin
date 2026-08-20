"""FotMob 阵容 / 伤停快照只读查询(数据 tab 模块一:预计阵容 + 伤停)。

背景:bronze_fm_lineup_snap / bronze_fm_sideline_snap 早就在实时采集(2026-08-15
仍有新观测),但从未被任何页面消费——本模块是第一个消费方。两张表都直接以
fotmob_match_id 为键,不需要经过 NowGoal 那样的 xref 映射。

诚实纪律(CLAUDE.md §6.2 不伪装):
- `lineup_type` 绝大多数真实值是 "lastStarting11"(上一场首发,不是本场确认
  阵容)。调用方必须原样透传这个字段,不得翻译/改写成"首发"这类会被误读成
  官方名单的措辞——具体文案红线见 backend/providers/fotmob_snapshots.py 顶部
  注释与 docs/design-brief-match-detail-viz.md。
- 旧快照(2026-08-15 之前写入的行)payload 里没有 lineup_type/source 键,
  这里如实返回 None,不回填猜测值。
- 伤停名单为空列表时是"这次观测确实没有伤停"的合法结果,不是"没有数据"——
  但这个语义只有在真的存在一条快照时才成立;`fotmob_match_id`/`team_id`
  从未被观测过时同样返回空列表(实测 bronze_fm_lineup_snap 与
  bronze_fm_sideline_snap 100% 同步覆盖,且每场两队的伤停快照总是成对写入,
  见 backend/cli/poll_fotmob_snapshots.py 的采集耦合),调用方按"该场已开始
  采集"为前提使用本函数,不单独为"从未采集过"设计第三种状态。
- 阵容/伤停名单里的球员名此前直接透传 bronze 快照里的来源英文名,从没查过
  `dim_player_i18n`(该表在 core 库,本模块此前只接 conn_odds,没有查的
  能力)——match_report.py/league_stats.py/player_form.py 都已经在用
  `_player_i18n_map` 的"短中文名 > 全中文名 > 来源英文名"回退链,这里现在
  也接一份 conn_core 对齐,不维护第二套忘记查译名的展示逻辑。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.queries.league_stats import _player_i18n_map

_EMPTY_SIDE = {"team_id": None, "formation": None, "coach": None, "starters": [], "subs": []}


def _translate_players(players: list[dict], i18n: dict) -> list[dict]:
    out = []
    for p in players:
        p = dict(p)
        pid = p.get("id")
        if pid is not None:
            name_zh, name_zh_short = i18n.get(str(pid), (None, None))
            p["name"] = name_zh_short or name_zh or p.get("name")
        out.append(p)
    return out


def _translate_side(side: dict, i18n: dict) -> dict:
    side = dict(side)
    side["starters"] = _translate_players(side.get("starters") or [], i18n)
    side["subs"] = _translate_players(side.get("subs") or [], i18n)
    # coach 绝不经过 _translate_players:教练 id 与球员 id 的命名空间无法证明
    # 互斥,一次碰撞会把某位教练悄悄改名成某个球员(D3 钉住)。显式赋值(而不是
    # 依赖上面 dict(side) 的浅拷贝顺带带过来)是为了让旧快照(键缺失)与新快照
    # (键存在但值为 None)返回统一形状——都是 "coach": None,不回填猜测值。
    side["coach"] = side.get("coach")
    return side


def latest_lineup(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection, fotmob_match_id: int,
) -> dict[str, Any] | None:
    """单场最新一条阵容快照,归一为 {lineup_type, source, home, away}。

    无快照时返回 None(不是两个空侧)——调用方据此区分"这场还没开始被采集"和
    "采集了但阵容尚未公布"(后者 home/away 会是全 None 的合法空侧结构)。
    """
    try:
        row = conn_odds.execute(
            """SELECT payload_json, observed_at FROM bronze_fm_lineup_snap
                WHERE fotmob_match_id=? ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (fotmob_match_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    i18n = _player_i18n_map(conn_core)
    return {
        "lineup_type": payload.get("lineup_type"),
        "source": payload.get("source"),
        "observed_at": row["observed_at"],
        "home": _translate_side(payload.get("home") or dict(_EMPTY_SIDE), i18n),
        "away": _translate_side(payload.get("away") or dict(_EMPTY_SIDE), i18n),
    }


def latest_sidelined_for_team(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection,
    fotmob_match_id: int, team_id: int | None,
) -> list[dict[str, Any]]:
    """单队最新一条伤停快照里的名单。无快照或 team_id 为 None 时返回空列表。"""
    if team_id is None:
        return []
    try:
        row = conn_odds.execute(
            """SELECT payload_json FROM bronze_fm_sideline_snap
                WHERE fotmob_match_id=? AND fotmob_team_id=?
                ORDER BY observed_at DESC, id DESC LIMIT 1""",
            (fotmob_match_id, team_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    if row is None:
        return []
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return []
    i18n = _player_i18n_map(conn_core)
    return _translate_players(list(payload.get("sidelined") or []), i18n)
