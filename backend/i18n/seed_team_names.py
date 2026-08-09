"""seed_team_names.py —— 通用球队中文译名 seeder,artifact 驱动、fail-closed。

2026-08-08 新增,替代 `backend/i18n/seed_allsvenskan_teams.py`(该模块自己的
docstring 承认"单人经验判断……没有走三票核验",source 也如实标注
`allsvenskan_onboarding_single_pass_not_3vote_verified`,且从未在生产库跑过——
留着一个能往 `dim_team_i18n` 写未核验译名的可执行模块是活隐患,已删除
连同其测试;本模块把它的**测试意图**(不得冒用更高验证等级的 source 标签)
接管过来,见下方 `_validate_source_and_methods`)。

用法(先跑 --dry-run 检查门禁结果,确认无误再 --live):
    python -m backend.i18n.seed_team_names \\
        --artifact runtime/research/team-i18n-nordic/final-results.json \\
        --league-id 59 --league-id 67 --season 2026 \\
        --source qwen_max_websearch_verified --dry-run
    python -m backend.i18n.seed_team_names \\
        --artifact runtime/research/team-i18n-nordic/final-results.json \\
        --league-id 59 --league-id 67 --season 2026 \\
        --source qwen_max_websearch_verified --live

artifact 格式:JSON 数组,每行 `{team_id, name_en, name_zh, method, reasoning}`
(与 `runtime/research/team-i18n-jka/final-results.json` 同 schema)。

fail-closed 门禁(任一不满足,整批拒绝写入,不部分写入):
1. `--source qwen_max_websearch_verified` 时,artifact 每一行的 `method`
   必须在 `DOUBLE_VERIFIED_METHODS` 白名单内(qwen 翻译 + 独立 WebSearch
   交叉核对达成一致,或 WebSearch 纠正 qwen 的结果)——不接受"只跑了 qwen
   没有独立验证"的行冒充这个 source 标签。
2. `workflow_verified` 永远不可写(那是 `seed_curated.py` 三票工作流专用的
   更高等级,本模块不生产这个等级的证据)。
3. `name_zh` 必须非空、含至少一个 CJK 字符、且 != `name_en`
   (排除"翻译失败退化成原文"这种 data-plan §2 记录过的旧坑)。
4. 每个 `team_id` 必须在给定 `--league-id`/`--season` 组合的 `dim_match`
   里真实出现过(主队或客队)——不接受孤儿 team_id。
5. 批内 `name_zh` 不重复;且不与 `dim_team_i18n` 里**其它** `Team_ID`
   已有的 `name_zh` 撞名(把"零撞名"从事后声称的结论变成事前门禁)。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from backend.db.connections import connect_ro, connect_rw
from backend.db.util import utc_now_iso

DOUBLE_VERIFIED_METHODS = frozenset({
    "qwen_websearch_agree",
    "websearch_override",
    "websearch_confirmed_upgrade",
    "no_established_name_own_judgment",  # 双重核验后确认"确实没有通用译名",
                                          # 仍然是核验过的结论,不是跳过核验
})

_CJK_RE = re.compile(r"[一-鿿]")


class SeedGateError(Exception):
    """任一 fail-closed 门禁未通过,拒绝写入(整批,不部分写入)。"""


def load_artifact(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SeedGateError(f"artifact {path} 不是 JSON 数组")
    return rows


def _team_ids_in_scope(core_db_path, league_ids: list[int], season: str) -> set[int]:
    conn = connect_ro("core") if core_db_path is None else sqlite3.connect(
        f"file:{core_db_path}?mode=ro", uri=True)
    placeholders = ",".join("?" for _ in league_ids)
    rows = conn.execute(
        f"""SELECT DISTINCT Home_Team_ID FROM dim_match
              WHERE League_ID IN ({placeholders}) AND Season=?
            UNION
            SELECT DISTINCT Away_Team_ID FROM dim_match
              WHERE League_ID IN ({placeholders}) AND Season=?""",
        (*league_ids, season, *league_ids, season),
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def validate(rows: list[dict], *, source: str, in_scope_ids: set[int],
            existing_name_zh: dict[int, str]) -> None:
    if source == "workflow_verified":
        raise SeedGateError(
            "workflow_verified 是 seed_curated.py 三票工作流专用的更高验证等级,"
            "本模块不产出这个等级的证据,拒绝写入"
        )

    # existing_name_zh 是 {team_id: name_zh};撞名检查要反过来按 name_zh 查,
    # 建一次反向索引(2026-08-08 修复:原实现直接 .get(name_zh) 等于用
    # name_zh 当 team_id 查,恒找不到,门禁形同虚设——已用测试钉死回归)。
    existing_by_name: dict[str, int] = {}
    for tid, name in existing_name_zh.items():
        existing_by_name.setdefault(name, tid)

    seen_ids: set[int] = set()
    seen_names: dict[str, int] = {}
    for row in rows:
        team_id = row.get("team_id")
        name_en = row.get("name_en")
        name_zh = row.get("name_zh")
        method = row.get("method")

        if team_id is None or not isinstance(team_id, int):
            raise SeedGateError(f"行缺少合法 team_id: {row!r}")
        if team_id in seen_ids:
            raise SeedGateError(f"team_id={team_id} 在 artifact 内重复")
        seen_ids.add(team_id)

        if team_id not in in_scope_ids:
            raise SeedGateError(
                f"team_id={team_id}({name_en})不在给定 league-id/season 的 "
                "dim_match 里出现过,孤儿 team_id,拒绝写入"
            )

        if not name_zh or not _CJK_RE.search(name_zh):
            raise SeedGateError(f"team_id={team_id} 的 name_zh={name_zh!r} 为空或不含中文字符")
        if name_zh == name_en:
            raise SeedGateError(
                f"team_id={team_id} 的 name_zh 与 name_en 相同(翻译退化成原文),拒绝写入"
            )

        if source == "qwen_max_websearch_verified" and method not in DOUBLE_VERIFIED_METHODS:
            raise SeedGateError(
                f"team_id={team_id} 的 method={method!r} 不在双重验证白名单 "
                f"{sorted(DOUBLE_VERIFIED_METHODS)} 内,不得冒用 "
                "qwen_max_websearch_verified 这个 source 标签"
            )

        if name_zh in seen_names:
            raise SeedGateError(
                f"批内撞名:team_id={team_id} 与 team_id={seen_names[name_zh]} "
                f"都用 name_zh={name_zh!r}"
            )
        seen_names[name_zh] = team_id

        collide_id = existing_by_name.get(name_zh)
        if collide_id is not None and collide_id != team_id:
            raise SeedGateError(
                f"team_id={team_id} 的 name_zh={name_zh!r} 与已有 "
                f"dim_team_i18n.Team_ID={collide_id} 撞名"
            )


def seed(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    now = utc_now_iso()
    n = 0
    for row in rows:
        conn.execute(
            "INSERT OR REPLACE INTO dim_team_i18n (Team_ID, name_en, name_zh, source, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (row["team_id"], row["name_en"], row["name_zh"], source, now),
        )
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--league-id", type=int, action="append", required=True)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--source", type=str, default="qwen_max_websearch_verified")
    parser.add_argument("--core-db", type=Path, default=None, help="仅测试用")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    rows = load_artifact(args.artifact)
    in_scope_ids = _team_ids_in_scope(args.core_db, args.league_id, args.season)

    if args.core_db is not None:
        econn = sqlite3.connect(f"file:{args.core_db}?mode=ro", uri=True)
    else:
        econn = connect_ro("core")
    existing_name_zh = dict(econn.execute("SELECT Team_ID, name_zh FROM dim_team_i18n").fetchall())
    econn.close()

    try:
        validate(rows, source=args.source, in_scope_ids=in_scope_ids,
                 existing_name_zh=existing_name_zh)
    except SeedGateError as e:
        print(f"FAILED(fail-closed): {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps({
            "mode": "DRY_RUN", "rows": len(rows), "league_ids": args.league_id,
            "season": args.season, "source": args.source, "gates": "all passed",
        }, ensure_ascii=False, indent=1))
        return 0

    if args.core_db is not None:
        conn = sqlite3.connect(str(args.core_db))
    else:
        conn = connect_rw("core")
    try:
        n = seed(conn, rows, args.source)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({
        "mode": "LIVE", "rows_written": n, "league_ids": args.league_id,
        "season": args.season, "source": args.source,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
