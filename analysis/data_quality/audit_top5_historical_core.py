#!/usr/bin/env python3
"""五大联赛历史核心数据质量审计(只读)。

审计 data/allwin.db(生产核心库)与 data/verify_leagues.db(早期四联赛采集库)
在 League_ID ∈ {47,53,54,55,87} × Season ∈ {2020/2021..2025/2026} 范围内,
七张核心表(dim_match/dim_player/fact_shotmap/fact_player_match_stats/
fact_team_match_stats/fact_match_events/fact_match_lineup)的:

  - schema 对齐
  - 真实候选键粒度与重复率
  - verify → allwin 合并完整性(逐表逐联赛赛季)
  - 表/字段覆盖率(含 extra_json key 级别)
  - 核心完整性/唯一性/参照完整性异常
  - 各表领域规则异常(shotmap/player stats/team stats/events/lineup)
  - 跨表业务一致性(比分/xG 对账)
  - 英超对照下的历史高阶字段缺失趋势
  - 模型可用性分级(readiness)

绝对只读:两个输入库都以 `mode=ro`(+ `immutable=1` 当从只读快照读取时)URI 连接打开,
进程内从不执行 INSERT/UPDATE/DELETE/DDL。所有写操作只落在 --output-dir。

用法:
    python -m analysis.data_quality.audit_top5_historical_core \\
        --core-db /tmp/allwin-data-quality-snapshot/allwin.db \\
        --verify-db /tmp/allwin-data-quality-snapshot/verify_leagues.db \\
        --output-dir analysis/data_quality/output
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# ── 审计范围(固定,不接受用户覆盖——防止"改参数让报告好看") ──────────

LEAGUE_NAMES = {47: "EPL", 53: "Ligue1", 54: "Bundesliga", 55: "SerieA", 87: "LaLiga"}
ALL_LEAGUE_IDS = sorted(LEAGUE_NAMES)  # [47,53,54,55,87]
VERIFY_LEAGUE_IDS = {53, 54, 55, 87}   # verify_leagues.db 声称覆盖的四联赛
BENCHMARK_LEAGUE_ID = 47               # 英超,仅作同源同赛季比较基线,非绝对真值

SEASONS = [
    "2020/2021", "2021/2022", "2022/2023",
    "2023/2024", "2024/2025", "2025/2026",
]

CORE_TABLES = [
    "dim_match", "dim_player", "fact_shotmap", "fact_player_match_stats",
    "fact_team_match_stats", "fact_match_events", "fact_match_lineup",
]

FACT_TABLES = [t for t in CORE_TABLES if t not in ("dim_match", "dim_player")]

# NULL-safe 复合键归一化用的哨兵/分隔符,选用现实数据几乎不可能出现的控制字符
NULL_SENTINEL = "\x01__NULL__\x01"
KEY_SEP = "\x1f"

REPRESENTATIVE_LIMIT = 10  # 每类问题最多列出的代表性 ID 数


# ── 通用工具 ──────────────────────────────────────────────────────────

def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def connect_ro(path: Path, label: str) -> sqlite3.Connection:
    """强制只读连接;拒绝不存在或非文件的路径。"""
    if not path.exists() or not path.is_file():
        raise SystemExit(f"[{label}] 数据库不存在或不是文件: {path}")
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        raise SystemExit(f"[{label}] 无法以只读模式打开 {path}: {e}")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    # 探测:确认连接真的不可写(防御性验证,immutable+mode=ro 已经足够,这里再加一层)
    try:
        conn.execute("CREATE TEMP TABLE __ro_probe__ (x INTEGER)")
        conn.execute("DROP TABLE __ro_probe__")
    except sqlite3.OperationalError:
        pass  # query_only 拒绝临时表创建也可接受,说明确实只读
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def norm_key_expr(cols: list[str]) -> str:
    """NULL-safe 复合键表达式:区分 NULL / 空字符串 / 0 / '0'(IFNULL 只处理 NULL,
    不会把空字符串或 0 误吞)。"""
    parts = [f"IFNULL(CAST({c} AS TEXT), '{NULL_SENTINEL}')" for c in cols]
    return f" || '{KEY_SEP}' || ".join(parts)


def pct(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """除零安全的百分比;分母为 0/None 时返回 None(调用方须映射为 NOT_APPLICABLE,
    不得写成 0%)。"""
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, 4)


def safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if not denominator:
        return None
    return round(numerator / denominator, 6)


class CsvWriter:
    """确定性 CSV 写出:固定列顺序,行按 sort_key 排序,浮点数格式统一。"""

    def __init__(self, path: Path, fieldnames: list[str]):
        self.path = path
        self.fieldnames = fieldnames
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        missing = set(self.fieldnames) - set(row)
        if missing:
            raise ValueError(f"{self.path.name}: row missing fields {missing}")
        self.rows.append(row)

    def write(self, sort_keys: Optional[list[str]] = None) -> int:
        rows = self.rows
        if sort_keys:
            rows = sorted(
                rows,
                key=lambda r: tuple(
                    (r[k] is None, r[k]) if not isinstance(r[k], str) else (r[k] is None, r[k])
                    for k in sort_keys
                ),
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.fieldnames, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return len(rows)


@dataclass
class Finding:
    severity: str          # CRITICAL/HIGH/MEDIUM/LOW
    confidence: str        # HIGH/MEDIUM/LOW
    category: str
    table: str
    field: str
    league_id: Optional[int]
    season: Optional[str]
    description: str
    count: int
    denominator: int
    rate_pct: Optional[float]
    vs_benchmark_delta_pct: Optional[float]
    vs_prev_season_delta_pct: Optional[float]
    representative_ids: str
    downstream_risk: str
    likely_cause: str
    recommendation: str

    def league_name(self) -> str:
        return LEAGUE_NAMES.get(self.league_id, "ALL") if self.league_id else "ALL"


FINDING_FIELDS = [
    "severity", "confidence", "category", "table_name", "field_name",
    "league_id", "league_name", "season", "description", "count", "denominator",
    "rate_pct", "vs_benchmark_delta_pct", "vs_prev_season_delta_pct",
    "representative_ids", "downstream_risk", "likely_cause", "recommendation",
]


# ── 已知、有文档依据的数据源限制(不得误判为异常;引用自 backend/schema.py 注释,
#    并在本轮审计中用真实数据逐联赛复核过,见 docs/audits 报告 §5) ──────────

# fact_team_match_stats 半场拆分(Period IN FirstHalf/SecondHalf)覆盖率:
# FotMob 从约 2022/23 赛季中段起才逐步提供半场拆分,2023/24 起五大联赛基本全量。
# 早期赛季"只有 Period='All'"是文档化的数据源限制,不是采集/解析 bug。
KNOWN_HALFTIME_GAP_SEASONS = {"2020/2021", "2021/2022", "2022/2023"}

# touches_opp_box(fact_team_match_stats.extra_json)只在这两个赛季有意义覆盖,
# 见 backend/silver/build_silver.py TOUCHES_OPP_BOX_SEASONS 与 schema.py 注释。
TOUCHES_OPP_BOX_SEASONS = {"2024/2025", "2025/2026"}

# fact_player_match_stats 的 physical_metrics_* 六列:原始代码作者已在注释中说明
# "实测未在样本比赛中出现"(schema.py),本轮审计用真实数据把"疑似"升级为"确认"。
PHYSICAL_METRIC_COLUMNS = [
    "physical_metrics_topspeed", "physical_metrics_distance_covered",
    "physical_metrics_walking", "physical_metrics_running",
    "physical_metrics_sprinting", "physical_metrics_number_of_sprints",
]

GOALKEEPER_COLUMNS = [
    "saves", "goals_conceded", "expected_goals_on_target_faced", "goals_prevented",
    "keeper_diving_save", "saves_inside_box", "keeper_sweeper", "punches",
    "player_throws", "keeper_high_claim", "saved_penalties", "saved_penalties_in_shootout",
]

SHOT_RELATED_PLAYER_COLUMNS = [
    "goals", "expected_goals", "expected_goals_non_penalty",
    "expected_goals_on_target_variant", "ShotsOnTarget", "ShotsOffTarget",
    "shot_accuracy", "blocked_shots", "shots_woodwork", "big_chance_missed_title",
]

XG_XA_PLAYER_COLUMNS = ["expected_goals", "expected_assists", "xg_and_xa", "expected_goals_non_penalty"]

PASSING_DUEL_PLAYER_COLUMNS = [
    "accurate_passes", "chances_created", "passes_into_final_third", "accurate_crosses",
    "long_balls_accurate", "touches", "touches_opp_box", "dispossessed", "dribbles_succeeded",
    "matchstats.headers.tackles", "shot_blocks", "clearances", "headed_clearance",
    "interceptions", "recoveries", "dribbled_past", "ground_duels_won", "aerials_won",
    "duel_won", "duel_lost", "fouls", "was_fouled",
]

TEAM_STATS_EXTRA_JSON_KEYS_MODEL_USED = [
    "expected_goals", "total_shots", "ShotsOnTarget", "BallPossesion", "corners",
    "fouls", "yellow_cards", "red_cards", "expected_goals_non_penalty",
    "expected_goals_open_play", "expected_goals_set_play", "expected_goals_on_target",
]

# 恒为 JSON null 的"分类标题"key(FotMob 分类头被解析器误当成叶子字段收进
# extra_json,值恒 null)——本轮用真实数据确认,见 §5。不是"0% 覆盖异常"。
TEAM_STATS_PLACEHOLDER_KEYS = {"shots", "defense", "duels", "discipline"}


def finding_row(f: Finding) -> dict[str, Any]:
    return {
        "severity": f.severity,
        "confidence": f.confidence,
        "category": f.category,
        "table_name": f.table,
        "field_name": f.field,
        "league_id": f.league_id if f.league_id is not None else "",
        "league_name": f.league_name(),
        "season": f.season or "",
        "description": f.description,
        "count": f.count,
        "denominator": f.denominator,
        "rate_pct": "" if f.rate_pct is None else f.rate_pct,
        "vs_benchmark_delta_pct": "" if f.vs_benchmark_delta_pct is None else f.vs_benchmark_delta_pct,
        "vs_prev_season_delta_pct": "" if f.vs_prev_season_delta_pct is None else f.vs_prev_season_delta_pct,
        "representative_ids": f.representative_ids,
        "downstream_risk": f.downstream_risk,
        "likely_cause": f.likely_cause,
        "recommendation": f.recommendation,
    }


# ── Schema 内省 ───────────────────────────────────────────────────────

def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [
        {"cid": r["cid"], "name": r["name"], "type": r["type"], "notnull": r["notnull"],
         "dflt_value": r["dflt_value"], "pk": r["pk"]}
        for r in rows
    ]


def schema_snapshot(conn: sqlite3.Connection, tables: list[str]) -> dict[str, list[dict[str, Any]]]:
    return {t: table_columns(conn, t) for t in tables if table_exists(conn, t)}


def diff_schema(core_schema: dict, verify_schema: dict) -> list[dict[str, Any]]:
    """逐表对比列集合/类型/顺序;返回结构差异列表(供 markdown 报告直接使用)。"""
    diffs = []
    for table in CORE_TABLES:
        core_cols = core_schema.get(table, [])
        verify_cols = verify_schema.get(table, [])
        core_by_name = {c["name"]: c for c in core_cols}
        verify_by_name = {c["name"]: c for c in verify_cols}
        only_core = sorted(set(core_by_name) - set(verify_by_name))
        only_verify = sorted(set(verify_by_name) - set(core_by_name))
        type_mismatch = [
            {"column": n, "core_type": core_by_name[n]["type"], "verify_type": verify_by_name[n]["type"]}
            for n in sorted(set(core_by_name) & set(verify_by_name))
            if core_by_name[n]["type"] != verify_by_name[n]["type"]
        ]
        core_order = [c["name"] for c in core_cols]
        verify_order = [c["name"] for c in verify_cols]
        common_core_order = [c for c in core_order if c in verify_by_name]
        common_verify_order = [c for c in verify_order if c in core_by_name]
        order_diff = common_core_order != common_verify_order
        diffs.append({
            "table": table,
            "only_in_core": only_core,
            "only_in_verify": only_verify,
            "type_mismatches": type_mismatch,
            "column_order_differs": order_diff,
            "core_column_count": len(core_cols),
            "verify_column_count": len(verify_cols),
            "compatible": not only_verify and not type_mismatch,
        })
    return diffs


# ── 真实候选键粒度分析 ────────────────────────────────────────────────

GRAIN_CANDIDATES: dict[str, list[str]] = {
    "dim_match": ["Match_ID"],
    "dim_player": ["Player_ID"],
    "fact_shotmap": ["Match_ID", "Player_ID", "Team_ID", "Minute", "Period", "X_Coord", "Y_Coord", "xG"],
    "fact_player_match_stats": ["Match_ID", "Player_ID", "Team_ID"],
    "fact_team_match_stats": ["Match_ID", "Team_ID", "Period"],
    "fact_match_events": ["Match_ID", "event_index"],
    "fact_match_lineup": ["Match_ID", "Team_ID", "Player_ID"],
}


def grain_report(conn: sqlite3.Connection, table: str, league_filter_sql: str) -> dict[str, Any]:
    """在目标 League_ID/Season 范围内(通过 dim_match 限定),用 NULL-safe 复合键
    统计真实重复率,确认候选键是否可靠代表表粒度。"""
    cols = GRAIN_CANDIDATES[table]
    if table in ("dim_match", "dim_player"):
        base = table
        join = ""
        where = league_filter_sql if table == "dim_match" else "1=1"
        key_expr = norm_key_expr(cols)
    else:
        base = table
        join = f" JOIN dim_match m ON m.Match_ID = {table}.Match_ID"
        where = league_filter_sql
        # 与 dim_match join 后 Match_ID 在两边都存在,列名必须带表前缀消歧
        key_expr = norm_key_expr([f"{table}.{c}" for c in cols])

    total = conn.execute(f"SELECT COUNT(*) FROM {base}{join} WHERE {where}").fetchone()[0]
    distinct_k = conn.execute(
        f"SELECT COUNT(DISTINCT {key_expr}) FROM {base}{join} WHERE {where}"
    ).fetchone()[0]
    dup_groups = conn.execute(
        f"""SELECT COUNT(*) FROM (
              SELECT {key_expr} AS k FROM {base}{join} WHERE {where}
              GROUP BY k HAVING COUNT(*) > 1
            )"""
    ).fetchone()[0]

    reps: list[str] = []
    if dup_groups:
        rep_col = f"{table}.Match_ID" if table != "dim_player" else "Player_ID"
        rows = conn.execute(
            f"""SELECT {rep_col} AS rid, COUNT(*) n FROM {base}{join} WHERE {where}
                GROUP BY {key_expr} HAVING COUNT(*) > 1
                ORDER BY n DESC, rid LIMIT {REPRESENTATIVE_LIMIT}"""
        ).fetchall()
        reps = [str(r["rid"]) for r in rows]

    return {
        "table": table,
        "candidate_key": ",".join(cols),
        "total_rows": total,
        "distinct_keys": distinct_k,
        "duplicate_groups": dup_groups,
        "duplicate_row_rate_pct": pct(total - distinct_k, total),
        "representative_ids": ";".join(reps),
    }


# ── 字段覆盖率引擎 ────────────────────────────────────────────────────

@dataclass
class FieldSpec:
    table: str
    field: str                       # 报告用的字段名(extra_json 字段用 "extra_json.key" 形式)
    family: str                      # feature_family 分组
    expr: str                        # SQL 表达式(核心列名,或 json_extract(extra_json,'$.key'))
    applicable_sql: Optional[str] = None   # 额外的适用性谓词(如 is_goalkeeper=1),None=全部行适用
    season_restriction: Optional[frozenset] = None  # 非空时,不在此集合内的赛季标记 NOT_APPLICABLE
    is_numeric: bool = True
    period_all_only: bool = False    # team_match_stats 专用:是否只看 Period='All' 行
    applicability: str = "REQUIRED"  # REQUIRED / CONDITIONAL / OPTIONAL(§五B——family readiness 不能对三者一视同仁平均)


# 以下三组是用真实数据逐字段实测确认的"源头就只在事件发生时才提供该字段"
# (FotMob 对未触发对应事件的球员整段省略该统计子块,而不是补 0/NULL 占位)——
# 不是采集缺陷,是真实的事件条件性,机械按全体出场球员当分母会严重低估覆盖率
# (§六D)。实测证据见 docs/audits 报告 methodology 附录。
_GK_RARE_EVENT_COLUMNS = frozenset({"goals_prevented", "saved_penalties", "saved_penalties_in_shootout"})
_PASSING_RARE_EVENT_COLUMNS = frozenset({"accurate_crosses", "dribbles_succeeded", "headed_clearance", "was_fouled"})


def _player_field_specs() -> list[FieldSpec]:
    specs: list[FieldSpec] = []
    gk_pred = "is_goalkeeper = 1"
    outfield_pred = "(is_goalkeeper IS NULL OR is_goalkeeper = 0)"
    for c in GOALKEEPER_COLUMNS:
        applicability = "OPTIONAL" if c in _GK_RARE_EVENT_COLUMNS else "REQUIRED"
        specs.append(FieldSpec("fact_player_match_stats", c, "goalkeeper_advanced", f'"{c}"', applicable_sql=gk_pred,
                                applicability=applicability))
    for c in PHYSICAL_METRIC_COLUMNS:
        # 全量 0% 覆盖,成因未证实(疑似解析器 camelCase key 未做下划线别名,见 §五
        # physical_metrics_trace),标 OPTIONAL 避免拖累其余字段的 family 覆盖率判断。
        specs.append(FieldSpec("fact_player_match_stats", c, "physical", f'"{c}"', applicability="OPTIONAL"))
    for c in XG_XA_PLAYER_COLUMNS:
        # 实测(EPL 2020/2021)expected_goals/expected_assists/xg_and_xa/
        # expected_goals_non_penalty 非空率仅 51%-82%——源头只在球员本场有射门/
        # 关键传球触发时才提供该统计子块,不是全体出场球员的通用字段,标 OPTIONAL。
        specs.append(FieldSpec("fact_player_match_stats", c, "player_attacking_xg_xa", f'"{c}"',
                                applicable_sql=outfield_pred, applicability="OPTIONAL"))
    for c in SHOT_RELATED_PLAYER_COLUMNS:
        if c in XG_XA_PLAYER_COLUMNS:
            continue
        # 同上:除 goals(每名出场球员必有,哪怕是 0)外,ShotsOnTarget/ShotsOffTarget/
        # blocked_shots/shots_woodwork/big_chance_missed_title/
        # expected_goals_on_target_variant 实测均为事件条件性字段(20%-51%),
        # shot_accuracy 已确认 = ShotsOnTarget 的冗余计数——全部标 OPTIONAL。
        applicability = "REQUIRED" if c == "goals" else "OPTIONAL"
        specs.append(FieldSpec("fact_player_match_stats", c, "shot_xg", f'"{c}"', applicable_sql=outfield_pred,
                                applicability=applicability))
    for c in PASSING_DUEL_PLAYER_COLUMNS:
        applicability = "OPTIONAL" if c in _PASSING_RARE_EVENT_COLUMNS else "REQUIRED"
        specs.append(FieldSpec("fact_player_match_stats", c, "defensive_duel_passing", f'"{c}"',
                                applicable_sql=outfield_pred, applicability=applicability))
    # 通用出场字段(全体适用,不分门将/外场)
    for c in ["minutes_played", "rating_title", "is_captain", "shirt_number", "usual_position"]:
        specs.append(FieldSpec("fact_player_match_stats", c, "standard_match", f'"{c}"'))
    # position_id(具体比赛槽位)与 fact_match_lineup.position_id 是同一类字段,但
    # 这里统计的是「实际出场球员」(minutes_played>0 的行,fact_player_match_stats
    # 本身即已限定为出场球员)——独立复核确认即使限定到出场球员,覆盖率仍逐季
    # 下降(2022/23 81.1% -> 2025/26 70.9%),不是"未出场替补合法缺失"这一类问题,
    # 而是该字段自身覆盖率的真实下降;但 usual_position(通常位置,语义不同,不能
    # 等价替代具体槽位)在同样出场球员范围内保持 100%,足以作为跨赛季稳定替代。
    # 标 OPTIONAL:不应让这个仍在下降、成因未查的字段拖累 standard_match 的
    # REQUIRED 最低覆盖率判断;usual_position 才是训练时应优先使用的稳定字段。
    specs.append(FieldSpec("fact_player_match_stats", "position_id", "standard_match", '"position_id"',
                            applicability="OPTIONAL"))
    return specs


def _team_stats_extra_json_specs(all_keys: list[str]) -> list[FieldSpec]:
    specs = []
    for k in all_keys:
        family = "context"
        applicability = "OPTIONAL"  # "context" 是动态发现的次要统计,默认不计入 required 覆盖率
        if k in TEAM_STATS_EXTRA_JSON_KEYS_MODEL_USED:
            family = "shot_xg" if "expected_goals" in k or k in ("total_shots", "ShotsOnTarget") else "standard_match"
            applicability = "REQUIRED"
        if k in TEAM_STATS_PLACEHOLDER_KEYS:
            # 已用真实数据确认恒为 JSON null 的分类标题 key,不是"缺失",OPTIONAL 且不计入分母判读
            applicability = "OPTIONAL"
        restriction = frozenset(TOUCHES_OPP_BOX_SEASONS) if k == "touches_opp_box" else None
        specs.append(FieldSpec(
            "fact_team_match_stats", f"extra_json.{k}", family,
            f"json_extract(t.extra_json, '$.{k}')",
            season_restriction=restriction, period_all_only=True, applicability=applicability,
        ))
    return specs


def _shotmap_field_specs() -> list[FieldSpec]:
    required_cols = ["xG", "Situation", "Outcome", "Shot_Type", "Minute", "X_Coord", "Y_Coord"]
    specs = [FieldSpec("fact_shotmap", c, "shot_xg", f'"{c}"') for c in required_cols]
    # xGOT 只对射正/进球类 Outcome 有意义(§六A),分母改为 on-target 子集,
    # 并标 CONDITIONAL——不应像普通 REQUIRED 字段一样被计入 family 覆盖率均值。
    specs.append(FieldSpec(
        "fact_shotmap", "xGOT", "shot_xg", '"xGOT"',
        applicable_sql="Outcome IN ('Goal','AttemptSaved')", applicability="CONDITIONAL",
    ))
    return specs


def _events_field_specs() -> list[FieldSpec]:
    # event_type 全事件适用,是唯一稳定的 REQUIRED 字段;其余字段的适用性由具体
    # event_type 决定(§六A),分母改为对应事件子集,并标 CONDITIONAL。
    specs = [FieldSpec("fact_match_events", "event_type", "standard_match", '"event_type"')]
    specs.append(FieldSpec("fact_match_events", "card_type", "standard_match", '"card_type"',
                            applicable_sql="event_type='Card'", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "assist_player_id", "standard_match", '"assist_player_id"',
                            applicable_sql="event_type='Goal'", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "sub_in_player_id", "standard_match", '"sub_in_player_id"',
                            applicable_sql="event_type='Substitution'", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "sub_out_player_id", "standard_match", '"sub_out_player_id"',
                            applicable_sql="event_type='Substitution'", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "minutes_added", "standard_match", '"minutes_added"',
                            applicable_sql="event_type='AddedTime'", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "overload_time", "standard_match", '"overload_time"',
                            applicable_sql="event_type='AddedTime'", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "player_id", "standard_match", '"player_id"',
                            applicable_sql="event_type NOT IN ('Half','AddedTime')", applicability="CONDITIONAL"))
    specs.append(FieldSpec("fact_match_events", "event_id", "standard_match", '"event_id"', applicability="OPTIONAL"))
    return specs


def _lineup_field_specs() -> list[FieldSpec]:
    required_cols = [
        "formation", "shirt_number", "usual_position_id", "is_starter",
        "is_captain", "country_code",
    ]
    specs = [FieldSpec("fact_match_lineup", c, "standard_match", f'"{c}"') for c in required_cols]
    # position_id(具体这场比赛的阵型槽位)只对首发有意义——实测(独立复核确认)
    # 首发覆盖率全部联赛全部赛季恒为 100%,下降全部来自替补(2024/25 起替补
    # position_id 降到 0%,是来源自 2024/25 起对替补停供该字段的结构变化,不是
    # 首发/出场数据缺陷)。分母限定 is_starter=1,才是"这场比赛我们知道首发阵型
    # 槽位吗"的正确 REQUIRED 语义。
    specs.append(FieldSpec(
        "fact_match_lineup", "position_id", "standard_match", '"position_id"',
        applicable_sql="is_starter=1",
    ))
    # 替补的 position_id 单独作为观察指标输出(不同 field 名,不与上面的 starter-only
    # 指标共享 CSV 唯一键),标 OPTIONAL——只用于报告"替补席位置字段结构变化"的
    # 趋势,不计入 family 的 REQUIRED 最低覆盖率判断。
    specs.append(FieldSpec(
        "fact_match_lineup", "position_id_bench", "standard_match", '"position_id"',
        applicable_sql="is_starter=0", applicability="OPTIONAL",
    ))
    # market_value/rating 来自独立估值/评分来源,历史赛季有真实覆盖率抬升趋势
    # (不是核心阵容数据缺陷),标 OPTIONAL。sub_in_time/sub_out_time 需要跨表关联
    # 实际换人事件才能得到正确分母,由专门的 lineup_sub_time_coverage_rows() 计算,
    # 不在这里用全表行数当分母(未出场替补的 NULL 是合法值,不是缺失)。
    for c in ["market_value", "rating"]:
        specs.append(FieldSpec("fact_match_lineup", c, "standard_match", f'"{c}"', applicability="OPTIONAL"))
    return specs


def lineup_sub_time_coverage_rows(conn: sqlite3.Connection, league_ids: list[int]) -> list[dict[str, Any]]:
    """sub_in_time/sub_out_time 的正确适用分母:分别是「实际在 fact_match_events 里
    作为 Substitution 事件 sub_in_player_id/sub_out_player_id 出现的球员」在
    fact_match_lineup 里对应的行——不是全体替补名单(未出场替补的 NULL 是合法值,
    不是缺失)。按 League×Season 输出,格式与 field_coverage_rows() 一致,可直接
    合并进 field_cov_raw。"""
    out: list[dict[str, Any]] = []
    league_filter = f"m.League_ID IN ({','.join(str(x) for x in league_ids)})"
    for field, event_col in (("sub_in_time", "sub_in_player_id"), ("sub_out_time", "sub_out_player_id")):
        sql = f"""
            SELECT m.League_ID, m.Season,
                   COUNT(*) AS applicable_rows,
                   SUM(CASE WHEN l.{field} IS NOT NULL THEN 1 ELSE 0 END) AS non_null_rows,
                   SUM(CASE WHEN CAST(l.{field} AS TEXT) = '' THEN 1 ELSE 0 END) AS blank_string_rows,
                   SUM(CASE WHEN typeof(l.{field}) IN ('integer','real') AND l.{field} = 0 THEN 1 ELSE 0 END) AS zero_rows,
                   COUNT(DISTINCT CASE WHEN l.{field} IS NOT NULL THEN l.Match_ID END) AS covered_matches,
                   COUNT(DISTINCT l.Match_ID) AS total_matches_in_partition
            FROM fact_match_lineup l
            JOIN dim_match m ON m.Match_ID = l.Match_ID
            WHERE {league_filter}
              AND EXISTS (
                    SELECT 1 FROM fact_match_events e
                    WHERE e.Match_ID = l.Match_ID AND e.event_type = 'Substitution'
                      AND e.{event_col} = l.Player_ID
              )
            GROUP BY m.League_ID, m.Season
        """
        for r in conn.execute(sql).fetchall():
            out.append({
                "table": "fact_match_lineup", "field": field, "family": "standard_match",
                "league_id": r["League_ID"], "season": r["Season"],
                "applicable_rows": r["applicable_rows"], "non_null_rows": r["non_null_rows"],
                "blank_string_rows": r["blank_string_rows"], "zero_rows": r["zero_rows"],
                "covered_matches": r["covered_matches"], "total_matches_in_partition": r["total_matches_in_partition"],
                "not_applicable": False, "applicability": "CONDITIONAL",
            })
    return out


def discover_team_stats_extra_json_keys(conn: sqlite3.Connection, league_filter_sql: str) -> list[str]:
    # json_each 是表值函数,在 FROM 阶段就会对每一行求值;若直接跨表关联,
    # WHERE 里的 json_valid 过滤发生得太晚,遇到非法 JSON 仍会抛异常。
    # 必须先在子查询里过滤出合法 JSON,再对子查询结果做 json_each。
    rows = conn.execute(
        f"""SELECT DISTINCT je.key FROM (
                SELECT t.extra_json AS extra_json
                FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID = t.Match_ID
                WHERE t.extra_json IS NOT NULL AND json_valid(t.extra_json) = 1 AND {league_filter_sql}
            ) sub, json_each(sub.extra_json) je
            ORDER BY je.key"""
    ).fetchall()
    return [r["key"] for r in rows]


def field_coverage_rows(
    conn: sqlite3.Connection, spec: FieldSpec, league_filter_sql: str,
) -> list[dict[str, Any]]:
    """对单个 field spec,按 League_ID/Season 分组计算覆盖率统计。"""
    table = spec.table
    join = f" JOIN dim_match m ON m.Match_ID = {table}.Match_ID" if table != "fact_team_match_stats" else " JOIN dim_match m ON m.Match_ID = t.Match_ID"
    from_clause = f"{table} t{join}" if table == "fact_team_match_stats" else f"{table}{join}"
    tbl_alias = "t" if table == "fact_team_match_stats" else table

    where_parts = [league_filter_sql]
    if spec.applicable_sql:
        where_parts.append(spec.applicable_sql)
    if spec.period_all_only:
        where_parts.append("t.Period = 'All'")
    if "extra_json" in spec.expr:
        # json_extract 对非法 JSON 会抛异常而非返回 NULL;跳过非法 JSON 的行,
        # 该行已由 team_stats.invalid_json 异常检查单独计数,这里只保证不崩溃。
        where_parts.append("(t.extra_json IS NULL OR json_valid(t.extra_json) = 1)")
    where = " AND ".join(where_parts)

    expr = spec.expr
    sql = f"""
        SELECT m.League_ID, m.Season,
               COUNT(*) AS applicable_rows,
               SUM(CASE WHEN {expr} IS NOT NULL THEN 1 ELSE 0 END) AS non_null_rows,
               SUM(CASE WHEN CAST({expr} AS TEXT) = '' THEN 1 ELSE 0 END) AS blank_string_rows,
               SUM(CASE WHEN typeof({expr}) IN ('integer','real') AND {expr} = 0 THEN 1 ELSE 0 END) AS zero_rows,
               COUNT(DISTINCT CASE WHEN {expr} IS NOT NULL THEN {tbl_alias}.Match_ID END) AS covered_matches,
               COUNT(DISTINCT {tbl_alias}.Match_ID) AS total_matches_in_partition
        FROM {from_clause}
        WHERE {where}
        GROUP BY m.League_ID, m.Season
    """
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.OperationalError as e:
        raise RuntimeError(f"field_coverage query failed for {table}.{spec.field}: {e}\nSQL={sql}")

    out = []
    for r in rows:
        season = r["Season"]
        not_applicable = spec.season_restriction is not None and season not in spec.season_restriction
        out.append({
            "table": table, "field": spec.field, "family": spec.family,
            "league_id": r["League_ID"], "season": season,
            "applicable_rows": 0 if not_applicable else r["applicable_rows"],
            "non_null_rows": 0 if not_applicable else r["non_null_rows"],
            "blank_string_rows": 0 if not_applicable else r["blank_string_rows"],
            "zero_rows": 0 if not_applicable else r["zero_rows"],
            "covered_matches": 0 if not_applicable else r["covered_matches"],
            "total_matches_in_partition": r["total_matches_in_partition"],
            "not_applicable": not_applicable,
            "applicability": spec.applicability,
        })
    return out


# ── 分区(联赛×赛季)基础画像 ──────────────────────────────────────────

def percentiles(values: list[int], pcts: list[float]) -> dict[str, float]:
    if not values:
        return {f"p{int(p*100)}": 0.0 for p in pcts} | {"max": 0.0}
    s = sorted(values)
    n = len(s)
    out = {}
    for p in pcts:
        idx = min(n - 1, max(0, math.ceil(p * n) - 1)) if p > 0 else 0
        out[f"p{int(p*100)}"] = float(s[idx])
    out["max"] = float(s[-1])
    return out


def partition_summary_rows(conn: sqlite3.Connection, league_ids: list[int]) -> list[dict[str, Any]]:
    rows_out = []
    for lid in league_ids:
        for season in SEASONS:
            m = conn.execute(
                """SELECT COUNT(*) n,
                          SUM(CASE WHEN status='Finish' THEN 1 ELSE 0 END) n_finish,
                          SUM(CASE WHEN status='NotStarted' THEN 1 ELSE 0 END) n_notstarted,
                          SUM(CASE WHEN status='Cancelled' THEN 1 ELSE 0 END) n_cancelled,
                          SUM(CASE WHEN status NOT IN ('Finish','NotStarted','Cancelled') OR status IS NULL THEN 1 ELSE 0 END) n_other_status,
                          MIN(Date) min_date, MAX(Date) max_date,
                          SUM(CASE WHEN Home_Team_ID IS NULL THEN 1 ELSE 0 END) home_id_null,
                          SUM(CASE WHEN Away_Team_ID IS NULL THEN 1 ELSE 0 END) away_id_null,
                          SUM(CASE WHEN Home_Team_Name IS NULL OR TRIM(Home_Team_Name)='' THEN 1 ELSE 0 END) home_name_blank,
                          SUM(CASE WHEN Away_Team_Name IS NULL OR TRIM(Away_Team_Name)='' THEN 1 ELSE 0 END) away_name_blank,
                          SUM(CASE WHEN status='Finish' AND (home_score IS NULL OR away_score IS NULL) THEN 1 ELSE 0 END) finish_score_missing,
                          SUM(CASE WHEN home_score < 0 OR away_score < 0 THEN 1 ELSE 0 END) negative_score
                   FROM dim_match WHERE League_ID=? AND Season=?""",
                (lid, season),
            ).fetchone()
            n = m["n"] or 0
            if n == 0:
                continue
            teams = conn.execute(
                """SELECT COUNT(DISTINCT tid) FROM (
                     SELECT Home_Team_ID tid FROM dim_match WHERE League_ID=? AND Season=?
                     UNION
                     SELECT Away_Team_ID FROM dim_match WHERE League_ID=? AND Season=?
                   )""",
                (lid, season, lid, season),
            ).fetchone()[0]
            players = conn.execute(
                """SELECT COUNT(DISTINCT Player_ID) FROM fact_match_lineup l
                   JOIN dim_match dm ON dm.Match_ID = l.Match_ID
                   WHERE dm.League_ID=? AND dm.Season=?""",
                (lid, season),
            ).fetchone()[0]
            fact_counts = {}
            fact_covered = {}
            for t in FACT_TABLES:
                fc = conn.execute(
                    f"""SELECT COUNT(*) rows, COUNT(DISTINCT {t}.Match_ID) covered
                        FROM {t} JOIN dim_match m ON m.Match_ID = {t}.Match_ID
                        WHERE m.League_ID=? AND m.Season=?""",
                    (lid, season),
                ).fetchone()
                fact_counts[t] = fc["rows"]
                fact_covered[t] = fc["covered"]

            expected_round_robin = teams * (teams - 1) if teams else 0
            rows_out.append({
                "league_id": lid, "league_name": LEAGUE_NAMES.get(lid, str(lid)), "season": season,
                "match_count": n,
                "status_finish": m["n_finish"] or 0,
                "status_not_started": m["n_notstarted"] or 0,
                "status_cancelled": m["n_cancelled"] or 0,
                "status_other": m["n_other_status"] or 0,
                "distinct_teams": teams,
                "distinct_players_in_lineup": players,
                "earliest_date": m["min_date"] or "",
                "latest_date": m["max_date"] or "",
                "home_team_id_null": m["home_id_null"] or 0,
                "away_team_id_null": m["away_id_null"] or 0,
                "home_team_name_blank": m["home_name_blank"] or 0,
                "away_team_name_blank": m["away_name_blank"] or 0,
                "finish_score_missing": m["finish_score_missing"] or 0,
                "finish_score_missing_rate_pct": pct(m["finish_score_missing"] or 0, m["n_finish"] or 0),
                "negative_score_count": m["negative_score"] or 0,
                "fact_shotmap_rows": fact_counts["fact_shotmap"],
                "fact_player_match_stats_rows": fact_counts["fact_player_match_stats"],
                "fact_team_match_stats_rows": fact_counts["fact_team_match_stats"],
                "fact_match_events_rows": fact_counts["fact_match_events"],
                "fact_match_lineup_rows": fact_counts["fact_match_lineup"],
                "fact_shotmap_covered_matches": fact_covered["fact_shotmap"],
                "fact_player_match_stats_covered_matches": fact_covered["fact_player_match_stats"],
                "fact_team_match_stats_covered_matches": fact_covered["fact_team_match_stats"],
                "fact_match_events_covered_matches": fact_covered["fact_match_events"],
                "fact_match_lineup_covered_matches": fact_covered["fact_match_lineup"],
                "derived_team_count": teams,
                "derived_double_round_robin_expected": expected_round_robin,
                "match_count_vs_round_robin_delta": n - expected_round_robin,
                "volume_gap_note": (
                    "MATCHES_EXCEED_ROUND_ROBIN(可能含附加赛/资格赛口径差异,需人工复核)"
                    if expected_round_robin and n > expected_round_robin else
                    "MATCHES_BELOW_ROUND_ROBIN(可能反映真实赛制/推迟取消,不自动判定为缺失)"
                    if expected_round_robin and n < expected_round_robin else
                    "MATCHES_MATCH_ROUND_ROBIN"
                ),
            })
    return rows_out


# ── 表覆盖率(逐表逐联赛赛季,含每场行数分位数) ────────────────────────

def table_coverage_rows(conn: sqlite3.Connection, league_ids: list[int]) -> list[dict[str, Any]]:
    out = []
    league_filter = f"m.League_ID IN ({','.join('?' * len(league_ids))})"
    for lid in league_ids:
        for season in SEASONS:
            completed = conn.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=? AND status='Finish'",
                (lid, season),
            ).fetchone()[0]
            if completed == 0:
                continue
            for t in FACT_TABLES:
                per_match = conn.execute(
                    f"""SELECT {t}.Match_ID mid, COUNT(*) n FROM {t}
                        JOIN dim_match m ON m.Match_ID = {t}.Match_ID
                        WHERE m.League_ID=? AND m.Season=? AND m.status='Finish'
                        GROUP BY {t}.Match_ID""",
                    (lid, season),
                ).fetchall()
                counts = [r["n"] for r in per_match]
                covered = len(counts)
                total_rows = sum(counts)
                pcs = percentiles(counts, [0.0, 0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99])
                out.append({
                    "league_id": lid, "league_name": LEAGUE_NAMES.get(lid, str(lid)), "season": season,
                    "table_name": t,
                    "completed_match_count": completed,
                    "covered_match_count": covered,
                    "match_coverage_rate_pct": pct(covered, completed),
                    "total_rows": total_rows,
                    "rows_per_match_p0": pcs["p0"], "rows_per_match_p1": pcs["p1"],
                    "rows_per_match_p5": pcs["p5"], "rows_per_match_p25": pcs["p25"],
                    "rows_per_match_p50": pcs["p50"], "rows_per_match_p75": pcs["p75"],
                    "rows_per_match_p95": pcs["p95"], "rows_per_match_p99": pcs["p99"],
                    "rows_per_match_max": pcs["max"],
                })
    return out


# ── verify → allwin 合并完整性(多重集合流式哈希比较) ─────────────────

import hashlib


def _sha256_hex(s: Optional[str]) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _semantic_json_hash(raw: Optional[str]) -> str:
    """JSON 解析后按键排序重新序列化再哈希——原始文本不同但语义相同时应得到
    相同哈希(报告为 SEMANTICALLY_EQUAL 的序列化差异,不是内容丢失)。解析失败
    返回稳定的 INVALID_JSON 标记(不静默吞掉)。"""
    if raw is None:
        return "__NULL__"
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return "__INVALID_JSON__:" + _sha256_hex(raw)
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return _sha256_hex(canonical)


def _typed_tag(v: Any) -> str:
    """把 SQLite 传给 UDF 的原生类型值(int/float/str/bytes/None)转成保持类型区分的
    规范字符串——CAST(col AS TEXT) 会把 INTEGER 0 和 TEXT '0' 都变成 "0",丢失类型;
    这里改为直接接收未经 CAST 的原生 Python 值,用类型前缀严格区分
    NULL / INTEGER / REAL / TEXT / BLOB / 空字符串。"""
    if v is None:
        return "\x00N"
    if isinstance(v, bool):
        return "b:" + ("1" if v else "0")
    if isinstance(v, int):
        return "i:" + str(v)
    if isinstance(v, float):
        return "f:" + repr(v)
    if isinstance(v, bytes):
        return "y:" + v.hex()
    return "s:" + v


def _row_hash(*vals: Any) -> str:
    return _sha256_hex("\x1f".join(_typed_tag(v) for v in vals))


def _is_valid_json(raw: Optional[str]) -> bool:
    if raw is None:
        return True
    try:
        json.loads(raw)
        return True
    except (ValueError, TypeError):
        return False


def register_udfs(conn: sqlite3.Connection) -> None:
    conn.create_function("SHA256HEX", 1, _sha256_hex, deterministic=True)
    conn.create_function("SEMHASH", 1, _semantic_json_hash, deterministic=True)
    conn.create_function("ROWHASH", -1, _row_hash, deterministic=True)


def common_columns(core_schema: dict, verify_schema: dict, table: str) -> list[str]:
    core_names = {c["name"] for c in core_schema.get(table, [])}
    verify_names = {c["name"] for c in verify_schema.get(table, [])}
    verify_order = [c["name"] for c in verify_schema.get(table, [])]
    return [n for n in verify_order if n in core_names]


MISMATCH_TYPE_VALUES = (
    "NON_JSON_VALUE", "RAW_JSON_ONLY", "JSON_SEMANTIC", "INVALID_JSON",
    "NULL_VS_VALUE", "ROW_MULTIPLICITY",
)


def _classify_match_mismatch(
    core_conn: sqlite3.Connection, verify_conn: sqlite3.Connection,
    table: str, non_json_cols: list[str], has_extra_json: bool, mid: Any,
) -> str:
    """对单个存在差异的 Match_ID 给出最佳猜测的 mismatch_type(§三D 六选一)。
    只在代表性样本(至多 REPRESENTATIVE_LIMIT 个)上调用,不对全部差异逐行下钻。"""
    cols = non_json_cols + (["extra_json"] if has_extra_json else [])
    sel = ", ".join(f'"{c}"' for c in cols)
    v_rows = verify_conn.execute(f"SELECT {sel} FROM {table} WHERE Match_ID=?", (mid,)).fetchall()
    a_rows = core_conn.execute(f"SELECT {sel} FROM {table} WHERE Match_ID=?", (mid,)).fetchall()
    if len(v_rows) != len(a_rows):
        return "ROW_MULTIPLICITY"
    if not has_extra_json:
        return "NON_JSON_VALUE"
    v_json = [r[-1] for r in v_rows]
    a_json = [r[-1] for r in a_rows]
    if not all(_is_valid_json(x) for x in v_json) or not all(_is_valid_json(x) for x in a_json):
        return "INVALID_JSON"
    if {x is None for x in v_json} != {x is None for x in a_json}:
        return "NULL_VS_VALUE"
    v_nonjson = sorted(tuple(r[:-1]) for r in v_rows)
    a_nonjson = sorted(tuple(r[:-1]) for r in a_rows)
    if v_nonjson != a_nonjson:
        return "NON_JSON_VALUE"
    v_sem = sorted(_semantic_json_hash(x) for x in v_json)
    a_sem = sorted(_semantic_json_hash(x) for x in a_json)
    if v_sem != a_sem:
        return "JSON_SEMANTIC"
    return "RAW_JSON_ONLY"


def merge_parity_for_table(
    core_conn: sqlite3.Connection, verify_conn: sqlite3.Connection,
    table: str, cols: list[str], league_ids: list[int], schema_ok: bool,
) -> list[dict[str, Any]]:
    """真正的内容级多重集合比较(修复版,对全部表——含 dim_match——一视同仁):

      - raw 轨道:按 (Match_ID, ROWHASH(全部共同列,含 extra_json 原始文本)) 计数,
        保留重复次数(Counter,不是集合);两边完全相等 -> EXACT。
      - semantic 轨道(仅当表含 extra_json 列):按
        (Match_ID, ROWHASH(非 JSON 列), SEMHASH(canonical JSON)) 计数;
        raw 不等但 semantic 相等 -> SEMANTICALLY_EQUAL。
      - 否则 -> CONTENT_MISMATCH。

    ROWHASH 是接收原生 SQLite 类型值(未经 CAST AS TEXT)的 UDF,类型标签严格区分
    NULL / 空字符串 / INTEGER 0 / TEXT '0' / REAL,不会像字符串拼接那样把类型信息
    在比较前就丢掉。

    Match_ID 集合层面的整体缺失/多出(MISSING_FROM_ALLWIN / EXTRA_IN_ALLWIN)优先于
    内容分类,但 common 子集的内容比较结果记入 notes,不再用字符串拼接混入
    classification——分类值始终是六选一的干净枚举。
    """
    out: list[dict[str, Any]] = []
    has_extra_json = "extra_json" in cols
    non_json_cols = [c for c in cols if c != "extra_json"]
    raw_hash_expr = "ROWHASH(" + ", ".join(f'"{c}"' for c in cols) + ")"
    sem_hash_expr = "ROWHASH(" + ", ".join(f'"{c}"' for c in non_json_cols) + ")" if non_json_cols else "ROWHASH()"

    for lid in league_ids:
        for season in SEASONS:
            if not schema_ok:
                out.append(_parity_row(table, lid, season, "SCHEMA_INCOMPATIBLE",
                                        0, 0, 0, 0, 0, 0, "", 0, ""))
                continue

            v_ids = {r[0] for r in verify_conn.execute(
                "SELECT Match_ID FROM dim_match WHERE League_ID=? AND Season=?", (lid, season))}
            a_ids = {r[0] for r in core_conn.execute(
                "SELECT Match_ID FROM dim_match WHERE League_ID=? AND Season=?", (lid, season))}
            if not v_ids and not a_ids:
                continue
            only_verify = v_ids - a_ids
            only_allwin = a_ids - v_ids
            common_ids = v_ids & a_ids

            if not common_ids:
                status = "MISSING_FROM_ALLWIN" if only_verify else "EXTRA_IN_ALLWIN" if only_allwin else "EXACT"
                out.append(_parity_row(table, lid, season, status, 0, 0, len(only_verify), len(only_allwin), 0, 0, "",
                                        0, ""))
                continue

            placeholders = ",".join("?" * len(common_ids))
            id_list = list(common_ids)

            def fetch_hashes(conn: sqlite3.Connection) -> tuple[Counter, Counter]:
                sql = f"""SELECT Match_ID, {raw_hash_expr} rh
                          {(", " + sem_hash_expr + " nh, SEMHASH(extra_json) sh") if has_extra_json else ""}
                          FROM {table} WHERE Match_ID IN ({placeholders})"""
                cur = conn.execute(sql, id_list)
                counter: Counter = Counter()
                sem_counter: Counter = Counter()
                while True:
                    batch = cur.fetchmany(5000)
                    if not batch:
                        break
                    for r in batch:
                        counter[(r["Match_ID"], r["rh"])] += 1
                        if has_extra_json:
                            sem_counter[(r["Match_ID"], r["nh"], r["sh"])] += 1
                return counter, sem_counter

            v_counter, v_sem = fetch_hashes(verify_conn)
            a_counter, a_sem = fetch_hashes(core_conn)

            v_row_count = sum(v_counter.values())
            a_row_count = sum(a_counter.values())

            if v_counter == a_counter:
                content_classification = "EXACT"
            elif has_extra_json and v_sem == a_sem:
                content_classification = "SEMANTICALLY_EQUAL"
            else:
                content_classification = "CONTENT_MISMATCH"

            notes = ""
            if only_verify or only_allwin:
                # Match_ID 集合层面存在整体缺失/多出,优先于内容分类,但不丢弃 common
                # 子集的内容比较结果——记入 notes。
                classification = "MISSING_FROM_ALLWIN" if only_verify else "EXTRA_IN_ALLWIN"
                notes = f"common_subset_content={content_classification}"
            else:
                classification = content_classification

            mismatch_reps: list[str] = []
            mismatch_type_counts: Counter = Counter()
            mismatch_row_count = 0
            if content_classification == "SEMANTICALLY_EQUAL":
                mids = sorted({k[0] for k in (set(v_counter) ^ set(a_counter))})
                mismatch_row_count = len(mids)
                mismatch_reps = [str(x) for x in mids[:REPRESENTATIVE_LIMIT]]
                mismatch_type_counts["RAW_JSON_ONLY"] = mismatch_row_count
            elif content_classification == "CONTENT_MISMATCH":
                mids = sorted({k[0] for k in (set(v_counter) ^ set(a_counter))})
                mismatch_row_count = len(mids)
                mismatch_reps = [str(x) for x in mids[:REPRESENTATIVE_LIMIT]]
                for mid in mids[:REPRESENTATIVE_LIMIT]:
                    t = _classify_match_mismatch(core_conn, verify_conn, table, non_json_cols, has_extra_json, mid)
                    mismatch_type_counts[t] += 1
            elif only_verify:
                mismatch_reps = [str(x) for x in sorted(only_verify)[:REPRESENTATIVE_LIMIT]]

            mismatch_types_str = ";".join(f"{k}:{v}" for k, v in sorted(mismatch_type_counts.items()))

            out.append(_parity_row(
                table, lid, season, classification,
                v_row_count, a_row_count, len(only_verify), len(only_allwin), len(common_ids),
                abs(v_row_count - a_row_count), ";".join(mismatch_reps),
                mismatch_row_count, mismatch_types_str, notes,
            ))
    return out


def _parity_row(table, lid, season, classification, v_rows, a_rows, only_v, only_a,
                 common_matches, row_count_delta, representative_ids,
                 mismatch_row_count: int = 0, mismatch_types: str = "", notes: str = "") -> dict[str, Any]:
    return {
        "table_name": table, "league_id": lid, "league_name": LEAGUE_NAMES.get(lid, str(lid)),
        "season": season, "classification": classification,
        "verify_row_count": v_rows, "allwin_row_count": a_rows,
        "match_ids_only_in_verify": only_v, "match_ids_only_in_allwin": only_a,
        "common_match_ids": common_matches, "row_count_delta": row_count_delta,
        "mismatch_row_count": mismatch_row_count,
        "mismatch_types": mismatch_types,
        "notes": notes,
        "representative_ids": representative_ids,
    }


def _classify_name_diff(v_name: Optional[str], a_name: Optional[str]) -> str:
    """区分 dim_player 姓名差异是良性重音/大小写规范化,还是真实姓名形式变化
    (§六F)——不能笼统称"均为重音修复"。"""
    import unicodedata

    def strip_accents(s: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

    v, a = v_name or "", a_name or ""
    if strip_accents(v).casefold().strip() == strip_accents(a).casefold().strip():
        return "ACCENT_NORMALIZATION_ONLY"
    if " ".join(v.split()).casefold() == " ".join(a.split()).casefold():
        return "CASE_OR_WHITESPACE_ONLY"
    if sorted(v.casefold().split()) == sorted(a.casefold().split()):
        return "WORD_ORDER_ONLY"
    return "TRULY_DIFFERENT_NAME"


def dim_player_parity_rows(
    core_conn: sqlite3.Connection, verify_conn: sqlite3.Connection, league_ids: list[int],
) -> list[dict[str, Any]]:
    """dim_player 是全局表(允许存在其它联赛/球员),不能整体比较。改为比较:
    "四联赛事实表(lineup ∪ player_match_stats)实际引用的 Player_ID 集合"
    在两个 dim_player 里是否存在、Player_Name 是否一致。逐联赛赛季输出一行。"""
    out = []
    for lid in league_ids:
        for season in SEASONS:
            v_referenced = {
                r[0] for r in verify_conn.execute(
                    """SELECT DISTINCT Player_ID FROM (
                         SELECT l.Player_ID FROM fact_match_lineup l JOIN dim_match m ON m.Match_ID=l.Match_ID
                         WHERE m.League_ID=? AND m.Season=?
                         UNION
                         SELECT p.Player_ID FROM fact_player_match_stats p JOIN dim_match m ON m.Match_ID=p.Match_ID
                         WHERE m.League_ID=? AND m.Season=?
                       ) WHERE Player_ID IS NOT NULL""",
                    (lid, season, lid, season),
                )
            }
            if not v_referenced:
                continue
            placeholders = ",".join("?" * len(v_referenced))
            id_list = list(v_referenced)
            v_names = {r[0]: r[1] for r in verify_conn.execute(
                f"SELECT Player_ID, Player_Name FROM dim_player WHERE Player_ID IN ({placeholders})", id_list)}
            a_names = {r[0]: r[1] for r in core_conn.execute(
                f"SELECT Player_ID, Player_Name FROM dim_player WHERE Player_ID IN ({placeholders})", id_list)}

            # 先剔除"verify 自己的 fact 表引用、但连 verify 自己的 dim_player 都没有"的
            # 孤儿引用——这是 verify 源数据本身的既有缺陷,不是合并遗漏,不能算进
            # MISSING_FROM_ALLWIN(见 §八 orphan 检查,单独统计)。
            orphan_in_verify_source = sorted(v_referenced - set(v_names))
            present_in_verify_dim_player = v_referenced & set(v_names)

            missing_from_allwin = sorted(present_in_verify_dim_player - set(a_names))
            name_mismatch = sorted(
                pid for pid in (present_in_verify_dim_player & set(a_names))
                if v_names.get(pid) != a_names.get(pid)
            )
            name_blank_in_allwin = sorted(
                pid for pid in (present_in_verify_dim_player & set(a_names))
                if not (a_names.get(pid) or "").strip()
            )
            classification = (
                "MISSING_FROM_ALLWIN" if missing_from_allwin else
                "CONTENT_MISMATCH" if name_mismatch else
                "EXACT"
            )
            # 逐个差异分类(§六F):不能笼统称"均为重音修复"——真实姓名形式变化
            # (如昵称/全名互换)必须单独计数,不能和良性 Unicode 规范化混在一起。
            diff_buckets: Counter = Counter()
            for pid in name_mismatch:
                diff_buckets[_classify_name_diff(v_names.get(pid), a_names.get(pid))] += 1
            note = (
                f"orphan_in_verify_source(fact引用但连verify自己dim_player都没有)={len(orphan_in_verify_source)};"
                f"name_blank_in_allwin={len(name_blank_in_allwin)};"
                f"name_diff_breakdown={dict(diff_buckets)}"
            )
            out.append({
                "table_name": "dim_player", "league_id": lid, "league_name": LEAGUE_NAMES.get(lid, str(lid)),
                "season": season, "classification": classification,
                "verify_row_count": len(present_in_verify_dim_player), "allwin_row_count": len(a_names),
                "match_ids_only_in_verify": len(missing_from_allwin),
                "match_ids_only_in_allwin": 0,
                "common_match_ids": len(present_in_verify_dim_player & set(a_names)),
                "row_count_delta": len(name_mismatch),
                "mismatch_row_count": len(name_mismatch),
                "mismatch_types": ";".join(f"{k}:{v}" for k, v in sorted(diff_buckets.items())),
                "notes": note,
                "representative_ids": (
                    ";".join(str(x) for x in missing_from_allwin[:REPRESENTATIVE_LIMIT]) if missing_from_allwin else
                    ";".join(f"{pid}:{_classify_name_diff(v_names.get(pid), a_names.get(pid))}"
                              for pid in name_mismatch[:REPRESENTATIVE_LIMIT]) if name_mismatch else
                    ";".join(str(x) for x in orphan_in_verify_source[:REPRESENTATIVE_LIMIT])
                ),
            })
    return out


def dim_player_name_diff_summary(
    core_conn: sqlite3.Connection, verify_conn: sqlite3.Connection, league_ids: list[int],
) -> dict[str, Any]:
    """dim_player_parity_rows() 按 League×Season 输出的差异是"分区出现次数"
    (同一现实球员跨多个赛季会被计多次)。这里额外聚合出"唯一 Player_ID"口径,
    避免报告读者把 34+18 误读成 52 名不同球员(§四)。不重新实现 dim_player_parity_rows
    的比较逻辑,只是在其基础上按 Player_ID 去重统计。"""
    pid_types: dict[Any, set] = defaultdict(set)
    pid_partitions: dict[Any, set] = defaultdict(set)
    total_missing = total_blank = 0

    for lid in league_ids:
        for season in SEASONS:
            v_referenced = {
                r[0] for r in verify_conn.execute(
                    """SELECT DISTINCT Player_ID FROM (
                         SELECT l.Player_ID FROM fact_match_lineup l JOIN dim_match m ON m.Match_ID=l.Match_ID
                         WHERE m.League_ID=? AND m.Season=?
                         UNION
                         SELECT p.Player_ID FROM fact_player_match_stats p JOIN dim_match m ON m.Match_ID=p.Match_ID
                         WHERE m.League_ID=? AND m.Season=?
                       ) WHERE Player_ID IS NOT NULL""",
                    (lid, season, lid, season),
                )
            }
            if not v_referenced:
                continue
            placeholders = ",".join("?" * len(v_referenced))
            id_list = list(v_referenced)
            v_names = {r[0]: r[1] for r in verify_conn.execute(
                f"SELECT Player_ID, Player_Name FROM dim_player WHERE Player_ID IN ({placeholders})", id_list)}
            a_names = {r[0]: r[1] for r in core_conn.execute(
                f"SELECT Player_ID, Player_Name FROM dim_player WHERE Player_ID IN ({placeholders})", id_list)}
            present = v_referenced & set(v_names)
            for pid in present:
                if pid not in a_names:
                    total_missing += 1
                    continue
                if not (a_names.get(pid) or "").strip():
                    total_blank += 1
                    continue
                if v_names.get(pid) != a_names.get(pid):
                    t = _classify_name_diff(v_names.get(pid), a_names.get(pid))
                    pid_types[pid].add(t)
                    pid_partitions[pid].add((lid, season))

    accent_only_pids = {p for p, ts in pid_types.items() if ts <= {"ACCENT_NORMALIZATION_ONLY", "CASE_OR_WHITESPACE_ONLY", "WORD_ORDER_ONLY"}}
    truly_diff_pids = {p for p, ts in pid_types.items() if "TRULY_DIFFERENT_NAME" in ts}
    conflict_pids = {p for p, ts in pid_types.items() if len(ts) > 1}
    multi_partition_pids = {p for p, ps in pid_partitions.items() if len(ps) > 1}

    return {
        # 分区出现次数(34/18 这类数字)已经在 dim_player_parity_rows() 的每行
        # mismatch_types 里给出,调用方(main())对 merge_rows 汇总即可,这里不
        # 重复实现同一遍历口径,只补"唯一 Player_ID"去重口径。
        "unique_players": {
            "total": len(pid_types),
            "accent_only": len(accent_only_pids),
            "truly_different": len(truly_diff_pids),
            "conflict": len(conflict_pids),
            "appearing_in_multiple_partitions": len(multi_partition_pids),
        },
        "missing_from_allwin": total_missing,
        "blank_in_allwin": total_blank,
    }


def dim_player_partition_occurrences(dim_player_merge_rows: list[dict[str, Any]]) -> dict[str, int]:
    """从 dim_player_parity_rows() 已产出的 mismatch_types 字符串(如
    "ACCENT_NORMALIZATION_ONLY:3;TRULY_DIFFERENT_NAME:1")聚合出"按分区出现
    次数"口径(同一现实球员跨多赛季会被计多次)——不重新遍历/重新实现比较逻辑,
    只是对已有结果字符串做解析求和,与 dim_player_name_diff_summary() 的唯一
    球员口径互补,两者一起写入 summary JSON 才不会让读者把出现次数误当唯一人数。"""
    accent_types = {"ACCENT_NORMALIZATION_ONLY", "CASE_OR_WHITESPACE_ONLY", "WORD_ORDER_ONLY"}
    accent = truly_diff = 0
    for r in dim_player_merge_rows:
        for part in (r.get("mismatch_types") or "").split(";"):
            if ":" not in part:
                continue
            t, n = part.split(":", 1)
            try:
                n = int(n)
            except ValueError:
                continue
            if t in accent_types:
                accent += n
            elif t == "TRULY_DIFFERENT_NAME":
                truly_diff += n
    return {"accent_only": accent, "truly_different": truly_diff}


# ── 核心完整性 / 唯一性 / 参照完整性异常(§八) ──────────────────────────

def dim_match_anomalies(conn: sqlite3.Connection, db_label: str, league_filter_sql: str, league_id: Optional[int] = None, season: Optional[str] = None) -> list[Finding]:
    findings: list[Finding] = []

    def q(sql, params=()):
        return conn.execute(sql, params).fetchall()

    def rep_ids(sql, params=()):
        rows = q(sql + f" LIMIT {REPRESENTATIVE_LIMIT}", params)
        return ";".join(str(r[0]) for r in rows)

    total = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql}")[0][0]

    # 主客 Team_ID 为空或相同
    n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND (Home_Team_ID IS NULL OR Away_Team_ID IS NULL)")[0][0]
    if n:
        findings.append(Finding("CRITICAL", "HIGH", "dim_match.team_id_null", "dim_match", "Home_Team_ID/Away_Team_ID", league_id, season,
            f"[{db_label}] 主队或客队 Team_ID 为空", n, total, pct(n, total), None, None,
            rep_ids(f"SELECT Match_ID FROM dim_match WHERE {league_filter_sql} AND (Home_Team_ID IS NULL OR Away_Team_ID IS NULL)"),
            "无法参与球队维度聚合/模型特征构建", "抓取/解析失败或半页数据", "复核来源页面,必要时重抓"))

    n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND Home_Team_ID = Away_Team_ID")[0][0]
    if n:
        findings.append(Finding("CRITICAL", "HIGH", "dim_match.same_team", "dim_match", "Home_Team_ID=Away_Team_ID", league_id, season,
            f"[{db_label}] 主客队 Team_ID 相同", n, total, pct(n, total), None, None,
            rep_ids(f"SELECT Match_ID FROM dim_match WHERE {league_filter_sql} AND Home_Team_ID = Away_Team_ID"),
            "污染球队对阵/模型训练样本", "解析错位或来源数据错误", "人工复核这批 Match_ID"))

    # 主客队名为空/空白
    n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND (Home_Team_Name IS NULL OR TRIM(Home_Team_Name)='' OR Away_Team_Name IS NULL OR TRIM(Away_Team_Name)='')")[0][0]
    if n:
        findings.append(Finding("HIGH", "HIGH", "dim_match.team_name_blank", "dim_match", "Home_Team_Name/Away_Team_Name", league_id, season,
            f"[{db_label}] 主客队名为空/空白", n, total, pct(n, total), None, None,
            rep_ids(f"SELECT Match_ID FROM dim_match WHERE {league_filter_sql} AND (Home_Team_Name IS NULL OR TRIM(Home_Team_Name)='' OR Away_Team_Name IS NULL OR TRIM(Away_Team_Name)='')"),
            "展示层无法显示球队名", "解析字段缺失", "复核来源"))

    # 比分为负
    n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND (home_score < 0 OR away_score < 0)")[0][0]
    if n:
        findings.append(Finding("CRITICAL", "HIGH", "dim_match.negative_score", "dim_match", "home_score/away_score", league_id, season,
            f"[{db_label}] 比分为负数", n, total, pct(n, total), None, None,
            rep_ids(f"SELECT Match_ID FROM dim_match WHERE {league_filter_sql} AND (home_score < 0 OR away_score < 0)"),
            "污染比分相关全部下游(结算/训练标签)", "解析错误", "人工复核"))

    # 完赛但比分为空
    n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND status='Finish' AND (home_score IS NULL OR away_score IS NULL)")[0][0]
    total_finish = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND status='Finish'")[0][0]
    if n:
        findings.append(Finding("CRITICAL", "HIGH", "dim_match.finish_no_score", "dim_match", "home_score/away_score", league_id, season,
            f"[{db_label}] status=Finish 但比分为空", n, total_finish, pct(n, total_finish), None, None,
            rep_ids(f"SELECT Match_ID FROM dim_match WHERE {league_filter_sql} AND status='Finish' AND (home_score IS NULL OR away_score IS NULL)"),
            "训练标签缺失,若被静默当 0-0 会污染模型", "抓取时机早于比分回填,或解析失败", "重抓或标记不可用"))

    # 同联赛赛季同日期同主客队疑似重复比赛
    dup = q(
        f"""SELECT Home_Team_ID, Away_Team_ID, Date, COUNT(*) n, GROUP_CONCAT(Match_ID) ids
            FROM dim_match WHERE {league_filter_sql}
            GROUP BY League_ID, Season, Date, Home_Team_ID, Away_Team_ID HAVING COUNT(*) > 1
            LIMIT {REPRESENTATIVE_LIMIT}"""
    )
    if dup:
        total_dup = q(
            f"""SELECT COUNT(*) FROM (SELECT 1 FROM dim_match WHERE {league_filter_sql}
                GROUP BY League_ID, Season, Date, Home_Team_ID, Away_Team_ID HAVING COUNT(*) > 1)"""
        )[0][0]
        findings.append(Finding("HIGH", "MEDIUM", "dim_match.suspected_duplicate", "dim_match", "Date+Home+Away", league_id, season,
            f"[{db_label}] 同联赛赛季同日期同主客队出现多条比赛记录(疑似重复,也可能是真实数据如中断重赛)",
            total_dup, total, pct(total_dup, total), None, None,
            ";".join(r["ids"] for r in dup[:3]),
            "若为真重复会重复计入训练样本", "重复抓取/重复导入,或真实中断重赛(需人工区分)",
            "人工核对这批日期+对阵是否为同一场比赛"))

    # status 与比分矛盾:NotStarted/Cancelled 但已有比分
    n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND status IN ('NotStarted','Cancelled') AND (home_score IS NOT NULL OR away_score IS NOT NULL)")[0][0]
    if n:
        findings.append(Finding("MEDIUM", "MEDIUM", "dim_match.status_score_conflict", "dim_match", "status/home_score", league_id, season,
            f"[{db_label}] status=NotStarted/Cancelled 但比分非空", n, total, pct(n, total), None, None,
            rep_ids(f"SELECT Match_ID FROM dim_match WHERE {league_filter_sql} AND status IN ('NotStarted','Cancelled') AND (home_score IS NOT NULL OR away_score IS NOT NULL)"),
            "状态机语义矛盾,下游按 status 过滤会遗漏或误纳", "状态更新滞后于比分回填", "复核这批比赛真实状态"))

    # kickoff_at_utc 覆盖率(仅记录,不判定错误——历史限制,见文档)
    if "kickoff_at_utc" in [c["name"] for c in table_columns(conn, "dim_match")]:
        n = q(f"SELECT COUNT(*) FROM dim_match WHERE {league_filter_sql} AND kickoff_at_utc IS NOT NULL")[0][0]
        findings.append(Finding("LOW", "HIGH", "dim_match.kickoff_provenance_note", "dim_match", "kickoff_at_utc", league_id, season,
            f"[{db_label}] 精确开球时刻覆盖率(时间元数据限制,非比赛数据错误——历史数据入库早于 kickoff 精确化改造)",
            n, total, pct(n, total), None, None, "",
            "不影响比分/统计类模型特征,只影响需要精确赛前 cutoff 的场景", "历史批次入库时未采集精确 kickoff", "如需精确 kickoff 用于赛前特征,需按 CLAUDE.md §6.2.1 重新采集"))

    return findings


def dim_player_anomalies(conn: sqlite3.Connection, db_label: str) -> list[Finding]:
    findings = []
    total = conn.execute("SELECT COUNT(*) FROM dim_player").fetchone()[0]
    n = conn.execute(
        "SELECT COUNT(*) FROM dim_player WHERE Player_Name IS NULL OR TRIM(Player_Name)='' "
        "OR LOWER(TRIM(Player_Name)) IN ('unknown','n/a','na','null')"
    ).fetchone()[0]
    if n:
        reps = conn.execute(
            "SELECT Player_ID FROM dim_player WHERE Player_Name IS NULL OR TRIM(Player_Name)='' "
            f"OR LOWER(TRIM(Player_Name)) IN ('unknown','n/a','na','null') LIMIT {REPRESENTATIVE_LIMIT}"
        ).fetchall()
        findings.append(Finding("HIGH", "HIGH", "dim_player.name_blank_or_placeholder", "dim_player", "Player_Name", None, None,
            f"[{db_label}] 球员姓名为空/占位符(unknown/n a/null)", n, total, pct(n, total), None, None,
            ";".join(str(r[0]) for r in reps),
            "展示层显示异常;若参与球员级模型特征会成为无意义类别", "解析失败或来源缺失", "复核来源"))
    return findings


def orphan_and_referential_anomalies(
    conn: sqlite3.Connection, db_label: str, league_filter_sql: str,
    league_id: Optional[int] = None, season: Optional[str] = None,
) -> list[Finding]:
    """事实表 -> dim_match / dim_player 参照完整性,及 Team_ID 是否属于该场比赛主客队。"""
    findings = []
    for t in FACT_TABLES:
        # 孤儿:Match_ID 不在 dim_match(全局检查,不受 league_filter 限制,因为孤儿本身
        # 就意味着 join 不到 dim_match,league 信息无从谈起)
        orphan_n = conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE Match_ID NOT IN (SELECT Match_ID FROM dim_match)"
        ).fetchone()[0]
        total_n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if orphan_n:
            reps = conn.execute(
                f"SELECT DISTINCT Match_ID FROM {t} WHERE Match_ID NOT IN (SELECT Match_ID FROM dim_match) LIMIT {REPRESENTATIVE_LIMIT}"
            ).fetchall()
            findings.append(Finding("CRITICAL", "HIGH", "fact.orphan_match_id", t, "Match_ID", league_id, season,
                f"[{db_label}] {t} 存在无法连接 dim_match 的孤儿 Match_ID", orphan_n, total_n, pct(orphan_n, total_n), None, None,
                ";".join(str(r[0]) for r in reps),
                "该批数据无法归属到任何比赛,join 时会被静默丢弃或产生笛卡尔积风险", "先插事实表后删 dim_match,或删除顺序错误",
                "核对 ingest 流程删除/插入顺序"))

        if t in ("fact_player_match_stats", "fact_match_lineup"):
            # 必须经 dim_match join 用 league_filter_sql 限定到当前分区,否则(此函数
            # 现在按 League×Season 逐分区调用)每次都会重新算出整库的全局孤儿数,
            # 导致同一个全局数字在 30+ 个分区里被重复报告、严重度判断也全局污染。
            scoped_total_n = conn.execute(
                f"SELECT COUNT(*) FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID WHERE {league_filter_sql}"
            ).fetchone()[0]
            orphan_p = conn.execute(
                f"""SELECT COUNT(*) FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID
                    WHERE {league_filter_sql} AND f.Player_ID IS NOT NULL
                      AND f.Player_ID NOT IN (SELECT Player_ID FROM dim_player)"""
            ).fetchone()[0]
            if orphan_p:
                reps = conn.execute(
                    f"""SELECT DISTINCT f.Player_ID FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID
                        WHERE {league_filter_sql} AND f.Player_ID IS NOT NULL
                          AND f.Player_ID NOT IN (SELECT Player_ID FROM dim_player) LIMIT {REPRESENTATIVE_LIMIT}"""
                ).fetchall()
                unique_orphan = conn.execute(
                    f"""SELECT COUNT(DISTINCT f.Player_ID) FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID
                        WHERE {league_filter_sql} AND f.Player_ID IS NOT NULL
                          AND f.Player_ID NOT IN (SELECT Player_ID FROM dim_player)"""
                ).fetchone()[0]
                unique_total = conn.execute(
                    f"""SELECT COUNT(DISTINCT f.Player_ID) FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID
                        WHERE {league_filter_sql} AND f.Player_ID IS NOT NULL"""
                ).fetchone()[0]
                # 严重度取决于真实建模影响:孤儿是否包含首发/实际出场球员(同样限定到
                # 当前分区)。lineup 表可查 is_starter;是否有 minutes_played>0 需要
                # 关联 fact_player_match_stats(0 命中即说明这批孤儿从未真正出场)。
                severity = "HIGH"
                impact_note = ""
                if t == "fact_match_lineup":
                    starters = conn.execute(
                        f"""SELECT COUNT(*) FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID
                            WHERE {league_filter_sql} AND f.Player_ID IS NOT NULL
                              AND f.Player_ID NOT IN (SELECT Player_ID FROM dim_player) AND f.is_starter=1"""
                    ).fetchone()[0]
                    played = conn.execute(
                        f"""SELECT COUNT(*) FROM {t} l JOIN dim_match m ON m.Match_ID=l.Match_ID
                            WHERE {league_filter_sql} AND l.Player_ID IS NOT NULL
                              AND l.Player_ID NOT IN (SELECT Player_ID FROM dim_player)
                              AND EXISTS (SELECT 1 FROM fact_player_match_stats p
                                          WHERE p.Match_ID=l.Match_ID AND p.Player_ID=l.Player_ID AND p.minutes_played > 0)"""
                    ).fetchone()[0]
                    impact_note = f"其中首发={starters};有实际出场分钟数据={played}"
                    severity = "HIGH" if (starters > 0 or played > 0) else "LOW"
                findings.append(Finding(severity, "HIGH", "fact.orphan_player_id", t, "Player_ID", league_id, season,
                    f"[{db_label}] {t} 存在无法连接 dim_player 的孤儿 Player_ID(源数据既有缺陷,非本轮合并引入)。"
                    f"行占比={pct(orphan_p, scoped_total_n)}%;唯一球员占比={pct(unique_orphan, unique_total)}%({unique_orphan}/{unique_total})。"
                    + (impact_note or ""),
                    orphan_p, scoped_total_n, pct(orphan_p, scoped_total_n), None, None,
                    ";".join(str(r[0]) for r in reps),
                    "球员名无法展示;若做球员级聚合会丢失这些球员的贡献"
                    + ("(本分区经核实全部为未实际出场的替补名单,建模影响可忽略)" if severity == "LOW" else ""),
                    "dim_player 落库时机早于/漏于 fact 表",
                    "补写缺失的 dim_player 行(需要来源真实姓名,不得编造)"))

        # Team_ID 不是该比赛主队也不是客队(fact_match_events 没有 Team_ID 列,
        # 用 is_home 表达归属,单独在 events 领域规则里检查)
        if t != "fact_match_events":
            wrong_team = conn.execute(
                f"""SELECT COUNT(*) FROM {t} f JOIN dim_match m ON m.Match_ID = f.Match_ID
                    WHERE {league_filter_sql.replace('m.', 'm.')} AND f.Team_ID IS NOT NULL
                      AND f.Team_ID NOT IN (m.Home_Team_ID, m.Away_Team_ID)"""
            ).fetchone()[0]
            if wrong_team:
                reps = conn.execute(
                    f"""SELECT DISTINCT f.Match_ID FROM {t} f JOIN dim_match m ON m.Match_ID = f.Match_ID
                        WHERE {league_filter_sql} AND f.Team_ID IS NOT NULL
                          AND f.Team_ID NOT IN (m.Home_Team_ID, m.Away_Team_ID) LIMIT {REPRESENTATIVE_LIMIT}"""
                ).fetchall()
                scoped_total = conn.execute(
                    f"SELECT COUNT(*) FROM {t} f JOIN dim_match m ON m.Match_ID=f.Match_ID WHERE {league_filter_sql}"
                ).fetchone()[0]
                findings.append(Finding("CRITICAL", "HIGH", "fact.team_not_in_match", t, "Team_ID", league_id, season,
                    f"[{db_label}] {t} 的 Team_ID 不属于该场比赛的主队或客队(疑似串场)",
                    wrong_team, scoped_total, pct(wrong_team, scoped_total), None, None,
                    ";".join(str(r[0]) for r in reps),
                    "严重:可能把别的比赛的球队数据串进本场,直接污染模型特征", "Match_ID/Team_ID 解析错位",
                    "立即人工核对这批 Match_ID,评估是否需要重新采集"))
    return findings


# ── 各事实表领域规则(§九) ──────────────────────────────────────────────

def shotmap_anomalies(conn: sqlite3.Connection, db_label: str, league_filter_sql: str, league_id: Optional[int] = None, season: Optional[str] = None) -> list[Finding]:
    findings = []
    total = conn.execute(
        f"SELECT COUNT(*) FROM fact_shotmap f JOIN dim_match m ON m.Match_ID=f.Match_ID WHERE {league_filter_sql}"
    ).fetchone()[0]
    if not total:
        return findings

    def q(sql):
        return conn.execute(sql).fetchone()[0]

    def reps(sql):
        return ";".join(str(r[0]) for r in conn.execute(sql + f" LIMIT {REPRESENTATIVE_LIMIT}").fetchall())

    base = f"FROM fact_shotmap f JOIN dim_match m ON m.Match_ID=f.Match_ID WHERE {league_filter_sql}"

    # xG/xGOT 范围:负值或 >1(非有限值 SQLite 里已经不会以 REAL 存 NaN/Inf,but 仍检查)
    n = q(f"SELECT COUNT(*) {base} AND f.xG IS NOT NULL AND (f.xG < 0 OR f.xG > 1)")
    if n:
        findings.append(Finding("HIGH", "HIGH", "shotmap.xg_out_of_range", "fact_shotmap", "xG", league_id, season,
            f"[{db_label}] xG 超出 [0,1] 合理范围", n, total, pct(n, total), None, None,
            reps(f"SELECT DISTINCT f.Match_ID {base} AND f.xG IS NOT NULL AND (f.xG < 0 OR f.xG > 1)"),
            "污染 xG 类模型特征", "解析错位或来源异常值", "复核代表性 Match_ID"))

    n = q(f"SELECT COUNT(*) {base} AND f.xGOT IS NOT NULL AND f.xGOT < 0")
    if n:
        findings.append(Finding("MEDIUM", "MEDIUM", "shotmap.xgot_negative", "fact_shotmap", "xGOT", league_id, season,
            f"[{db_label}] xGOT 为负", n, total, pct(n, total), None, None,
            reps(f"SELECT DISTINCT f.Match_ID {base} AND f.xGOT IS NOT NULL AND f.xGOT < 0"), "污染 xGOT 特征", "解析异常", "复核"))

    # xGOT 的正确适用分母是「射正/进球」子集(Outcome IN Goal/AttemptSaved),已在
    # fact_shotmap.xGOT 的 FieldSpec 里用 applicable_sql 修正(CONDITIONAL,见
    # top5_field_coverage.csv),这里不再重复输出一条全射门分母的"覆盖率参考"——
    # 那样的全表分母口径已被证实是方法论错误,不能继续保留在异常表里(§六A/§八)。
    # 2024/2025 起 off-target 射门的 xGOT 由 NULL 变为字面 0(存储约定切换,不是新
    # 测量),按 on-target 专用分母核实其确未系统性下降。
    n_ontarget = q(f"SELECT COUNT(*) {base} AND f.Outcome IN ('Goal','AttemptSaved')")
    n_ontarget_positive = q(f"SELECT COUNT(*) {base} AND f.Outcome IN ('Goal','AttemptSaved') AND f.xGOT IS NOT NULL AND f.xGOT > 0")
    if n_ontarget and pct(n_ontarget_positive, n_ontarget) is not None and pct(n_ontarget_positive, n_ontarget) < 50.0:
        findings.append(Finding("MEDIUM", "HIGH", "shotmap.xgot_ontarget_coverage_low", "fact_shotmap", "xGOT", league_id, season,
            f"[{db_label}] 射正/进球类射门中 xGOT>0 的比例低于 50%(正确分母=射正+进球={n_ontarget})",
            n_ontarget_positive, n_ontarget, pct(n_ontarget_positive, n_ontarget), None, None, "",
            "若用于 xGOT 类特征,该分区可用样本可能不足", "该分区源数据未覆盖或该赛季 xGOT 采集尚未启用", "训练前按 first_ready_season 过滤该字段"))

    # 坐标契约已用真实数据确认为 105×68 米制球场(非 0-100 归一化):X∈[0.38,104.91]
    # Y∈[0.20,67.87],全量样本 0 行越出该范围。仍保留一条真正意义上的"越出 105×68"
    # 检测,只在真实越界时报警,不再用错误的 0-100 假设制造假异常(§八E)。
    coord_stats = conn.execute(
        f"SELECT MIN(f.X_Coord), MAX(f.X_Coord), MIN(f.Y_Coord), MAX(f.Y_Coord) {base}"
    ).fetchone()
    x_min, x_max, y_min, y_max = coord_stats
    if x_min is not None and (x_min < -0.5 or x_max > 105.5 or y_min < -0.5 or y_max > 68.5):
        n_bad = q(f"SELECT COUNT(*) {base} AND (f.X_Coord < -0.5 OR f.X_Coord > 105.5 OR f.Y_Coord < -0.5 OR f.Y_Coord > 68.5)")
        findings.append(Finding("MEDIUM", "MEDIUM", "shotmap.coord_out_of_pitch", "fact_shotmap", "X_Coord/Y_Coord", league_id, season,
            f"[{db_label}] 坐标超出 105×68 米制球场范围(观测 X:[{x_min},{x_max}] Y:[{y_min},{y_max}])",
            n_bad, total, pct(n_bad, total), None, None,
            reps(f"SELECT DISTINCT f.Match_ID {base} AND (f.X_Coord < -0.5 OR f.X_Coord > 105.5 OR f.Y_Coord < -0.5 OR f.Y_Coord > 68.5)"),
            "位置类特征会失真", "解析异常或来源坐标系统变更", "复核代表性 Match_ID"))

    # 每场射门数极端值(按 Match_ID 聚合后取分位数,供人工参考,不自动判错)
    per_match = [r[0] for r in conn.execute(f"SELECT COUNT(*) n {base} GROUP BY f.Match_ID").fetchall()]
    if per_match:
        pcs = percentiles(per_match, [0.01, 0.5, 0.99])
        extreme_lo = sum(1 for x in per_match if x < 3)
        extreme_hi = sum(1 for x in per_match if x > 40)
        if extreme_lo or extreme_hi:
            findings.append(Finding("LOW", "MEDIUM", "shotmap.per_match_extreme", "fact_shotmap", "shots_per_match", league_id, season,
                f"[{db_label}] 每场射门数极端(<3 或 >40),p1={pcs['p1']} p50={pcs['p50']} p99={pcs['p99']}",
                extreme_lo + extreme_hi, len(per_match), pct(extreme_lo + extreme_hi, len(per_match)), None, None, "",
                "极端场次若为解析截断会低估/高估射门类特征", "可能是真实比赛(少射门/加时点球大战多射门)或解析截断",
                "抽样人工核对极端场次"))

    # 进球射门与比分不一致(按场统计 Outcome='Goal' 数 vs dim_match 总进球,允许乌龙/点球大战导致的合理偏差)
    goal_check = conn.execute(
        f"""SELECT m.Match_ID, COUNT(*) shot_goals, m.home_score + m.away_score total_score
            FROM fact_shotmap f JOIN dim_match m ON m.Match_ID=f.Match_ID
            WHERE {league_filter_sql} AND f.Outcome='Goal' AND m.status='Finish'
            GROUP BY m.Match_ID"""
    ).fetchall()
    mismatch = [r for r in goal_check if r["shot_goals"] > (r["total_score"] or 0)]
    if mismatch:
        findings.append(Finding("MEDIUM", "MEDIUM", "shotmap.goal_count_exceeds_score", "fact_shotmap", "Outcome=Goal", league_id, season,
            f"[{db_label}] 该场 shotmap 里 Outcome=Goal 的射门数超过 dim_match 总比分(未排除乌龙/点球大战,仅供复核线索)",
            len(mismatch), len(goal_check), pct(len(mismatch), len(goal_check)), None, None,
            ";".join(str(r["Match_ID"]) for r in mismatch[:REPRESENTATIVE_LIMIT]),
            "若真实错位会误导「进球位置」类特征", "乌龙球计入对方比分但 shotmap 记在原队,或点球大战射门被计入", "逐场人工复核(需排除乌龙/点球大战语义)"))

    return findings


PLAYER_STATS_INTEGER_LIKE = [
    "goals", "assists", "ShotsOnTarget", "ShotsOffTarget", "blocked_shots", "shots_woodwork",
    "missed_penalty", "corners", "Offsides", "owngoal", "saves", "goals_conceded",
    "saved_penalties", "fouls", "was_fouled",
]


def player_stats_anomalies(conn: sqlite3.Connection, db_label: str, league_filter_sql: str, league_id: Optional[int] = None, season: Optional[str] = None) -> list[Finding]:
    findings = []
    total = conn.execute(
        f"SELECT COUNT(*) FROM fact_player_match_stats p JOIN dim_match m ON m.Match_ID=p.Match_ID WHERE {league_filter_sql}"
    ).fetchone()[0]
    if not total:
        return findings

    def q(sql):
        return conn.execute(sql).fetchone()[0]

    def reps(sql):
        return ";".join(f"{r[0]}/{r[1]}" for r in conn.execute(sql + f" LIMIT {REPRESENTATIVE_LIMIT}").fetchall())

    base = f"FROM fact_player_match_stats p JOIN dim_match m ON m.Match_ID=p.Match_ID WHERE {league_filter_sql}"

    # minutes_played 范围(0-120,允许加时;>130 视为异常)
    n = q(f"SELECT COUNT(*) {base} AND p.minutes_played IS NOT NULL AND (p.minutes_played < 0 OR p.minutes_played > 130)")
    if n:
        findings.append(Finding("HIGH", "HIGH", "player_stats.minutes_out_of_range", "fact_player_match_stats", "minutes_played", league_id, season,
            f"[{db_label}] minutes_played 超出 [0,130] 合理范围", n, total, pct(n, total), None, None,
            reps(f"SELECT p.Match_ID, p.Player_ID {base} AND p.minutes_played IS NOT NULL AND (p.minutes_played < 0 OR p.minutes_played > 130)"),
            "污染出场时间相关特征", "解析异常", "复核"))

    # rating 范围(FotMob 通常 0-10)
    n = q(f"SELECT COUNT(*) {base} AND p.rating_title IS NOT NULL AND (p.rating_title < 0 OR p.rating_title > 10)")
    if n:
        findings.append(Finding("MEDIUM", "MEDIUM", "player_stats.rating_out_of_range", "fact_player_match_stats", "rating_title", league_id, season,
            f"[{db_label}] rating_title 超出 [0,10]", n, total, pct(n, total), None, None,
            reps(f"SELECT p.Match_ID, p.Player_ID {base} AND p.rating_title IS NOT NULL AND (p.rating_title < 0 OR p.rating_title > 10)"),
            "污染评分类特征", "解析异常", "复核"))

    # 计数类字段为负
    for c in PLAYER_STATS_INTEGER_LIKE:
        n = q(f'SELECT COUNT(*) {base} AND "{c}" IS NOT NULL AND "{c}" < 0')
        if n:
            findings.append(Finding("HIGH", "HIGH", "player_stats.negative_count", "fact_player_match_stats", c, league_id, season,
                f"[{db_label}] {c} 出现负值", n, total, pct(n, total), None, None,
                reps(f'SELECT p.Match_ID, p.Player_ID {base} AND "{c}" IS NOT NULL AND "{c}" < 0'),
                "计数字段不应为负,直接污染对应特征", "解析异常", "复核"))

    # xG/xA 相关一致性:xg_and_xa vs expected_goals+expected_assists(允许浮点误差)
    n = q(f"""SELECT COUNT(*) {base} AND p.xg_and_xa IS NOT NULL AND p.expected_goals IS NOT NULL AND p.expected_assists IS NOT NULL
              AND ABS(p.xg_and_xa - (p.expected_goals + p.expected_assists)) > 0.02""")
    n_applicable = q(f"SELECT COUNT(*) {base} AND p.xg_and_xa IS NOT NULL AND p.expected_goals IS NOT NULL AND p.expected_assists IS NOT NULL")
    if n:
        findings.append(Finding("MEDIUM", "MEDIUM", "player_stats.xg_xa_sum_mismatch", "fact_player_match_stats", "xg_and_xa", league_id, season,
            f"[{db_label}] xg_and_xa 与 expected_goals+expected_assists 之和不一致(容差 0.02)",
            n, n_applicable, pct(n, n_applicable), None, None,
            reps(f"""SELECT p.Match_ID, p.Player_ID {base} AND p.xg_and_xa IS NOT NULL AND p.expected_goals IS NOT NULL
                     AND p.expected_assists IS NOT NULL AND ABS(p.xg_and_xa - (p.expected_goals + p.expected_assists)) > 0.02"""),
            "若用 xg_and_xa 直接做特征而不核对来源一致性,可能引入噪声", "来源本身独立提供三个字段,非本地计算,允许小幅四舍五入误差",
            "容差内视为正常,超容差部分人工抽样核对"))

    # non-penalty xG 明显大于总 xG
    n = q(f"SELECT COUNT(*) {base} AND p.expected_goals_non_penalty IS NOT NULL AND p.expected_goals IS NOT NULL AND p.expected_goals_non_penalty > p.expected_goals + 0.02")
    if n:
        findings.append(Finding("MEDIUM", "MEDIUM", "player_stats.npxg_exceeds_xg", "fact_player_match_stats", "expected_goals_non_penalty", league_id, season,
            f"[{db_label}] expected_goals_non_penalty 明显大于 expected_goals", n, total, pct(n, total), None, None,
            reps(f"SELECT p.Match_ID, p.Player_ID {base} AND p.expected_goals_non_penalty IS NOT NULL AND p.expected_goals IS NOT NULL AND p.expected_goals_non_penalty > p.expected_goals + 0.02"),
            "违反「非点球 xG <= 总 xG」的基本约束,污染 xG 类特征", "解析错位或来源数据异常", "复核"))

    # goals 明显大于 ShotsOnTarget(允许乌龙/点球等边缘情况,阈值给余量)
    n = q(f'SELECT COUNT(*) {base} AND p.goals IS NOT NULL AND p."ShotsOnTarget" IS NOT NULL AND p.goals > p."ShotsOnTarget" + 1')
    if n:
        findings.append(Finding("LOW", "MEDIUM", "player_stats.goals_exceed_shots_on_target", "fact_player_match_stats", "goals/ShotsOnTarget", league_id, season,
            f"[{db_label}] goals 明显超过 ShotsOnTarget(容差 1,可能因乌龙/助攻计入等)", n, total, pct(n, total), None, None,
            reps(f'SELECT p.Match_ID, p.Player_ID {base} AND p.goals IS NOT NULL AND p."ShotsOnTarget" IS NOT NULL AND p.goals > p."ShotsOnTarget" + 1'),
            "可能提示统计口径错位", "乌龙球/口径差异或真实解析问题", "抽样复核"))

    # shot_accuracy 已用真实数据确认 = ShotsOnTarget(逐行相等比例):不是独立的
    # "命中比例"字段,是冗余计数列。这里核实该恒等关系在每个分区是否仍然成立,
    # 只在真正出现不相等样本时才报警(不再声称"单位未定")。
    n_eq = q(f'SELECT COUNT(*) {base} AND p.shot_accuracy IS NOT NULL AND p."ShotsOnTarget" IS NOT NULL AND p.shot_accuracy = p."ShotsOnTarget"')
    n_have_both = q(f'SELECT COUNT(*) {base} AND p.shot_accuracy IS NOT NULL AND p."ShotsOnTarget" IS NOT NULL')
    if n_have_both and n_eq != n_have_both:
        findings.append(Finding("LOW", "MEDIUM", "player_stats.shot_accuracy_not_redundant", "fact_player_match_stats", "shot_accuracy", league_id, season,
            f"[{db_label}] shot_accuracy 与 ShotsOnTarget 不完全相等(该分区之前已确认二者恒等,此分区出现例外)",
            n_have_both - n_eq, n_have_both, pct(n_have_both - n_eq, n_have_both), None, None,
            reps(f'SELECT p.Match_ID, p.Player_ID {base} AND p.shot_accuracy IS NOT NULL AND p."ShotsOnTarget" IS NOT NULL AND p.shot_accuracy != p."ShotsOnTarget"'),
            "若该分区的 shot_accuracy 语义与其它分区不同,训练配方不能统一按冗余列处理", "解析口径在该分区发生变化,需人工复核", "抽样核对该分区样本"))

    # 门将/非门将字段错位使用检查(§九B):门将字段在非门将球员上出现非空值的比例
    for c in GOALKEEPER_COLUMNS[:4]:  # 抽样几个代表性 GK 字段,避免报告过长
        n = q(f'SELECT COUNT(*) {base} AND (p.is_goalkeeper IS NULL OR p.is_goalkeeper=0) AND "{c}" IS NOT NULL')
        n_outfield = q(f"SELECT COUNT(*) {base} AND (p.is_goalkeeper IS NULL OR p.is_goalkeeper=0)")
        if n and pct(n, n_outfield) and pct(n, n_outfield) > 1.0:
            findings.append(Finding("LOW", "MEDIUM", "player_stats.gk_field_on_outfield", "fact_player_match_stats", c, league_id, season,
                f"[{db_label}] 非门将球员上 {c} 非空的比例超过 1%(可能是角色判定问题或球员客串门将)",
                n, n_outfield, pct(n, n_outfield), None, None, "",
                "若门将专属字段被非门将球员大量填充,说明 is_goalkeeper 角色判定可能有误", "球员客串门将(真实规则允许)或角色字段解析错误",
                "抽样核对 is_goalkeeper 判定逻辑"))

    return findings


def team_stats_anomalies(conn: sqlite3.Connection, db_label: str, league_filter_sql: str, league_id: Optional[int] = None, season: Optional[str] = None) -> list[Finding]:
    findings = []
    total = conn.execute(
        f"SELECT COUNT(*) FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID WHERE {league_filter_sql}"
    ).fetchone()[0]
    if not total:
        return findings

    def q(sql):
        return conn.execute(sql).fetchone()[0]

    base = f"FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID WHERE {league_filter_sql}"

    # extra_json 合法 JSON 率
    n_invalid = q(f"SELECT COUNT(*) {base} AND t.extra_json IS NOT NULL AND json_valid(t.extra_json) = 0")
    n_has_json = q(f"SELECT COUNT(*) {base} AND t.extra_json IS NOT NULL")
    if n_invalid:
        findings.append(Finding("HIGH", "HIGH", "team_stats.invalid_json", "fact_team_match_stats", "extra_json", league_id, season,
            f"[{db_label}] extra_json 不是合法 JSON", n_invalid, n_has_json, pct(n_invalid, n_has_json), None, None, "",
            "该行全部 extra_json 派生特征失效", "序列化/写入异常", "复核并修复解析器"))

    # 每个 Period 恰好两队(Match_ID × Period 应恰有 2 行)
    bad = conn.execute(
        f"""SELECT COUNT(*) FROM (
              SELECT t.Match_ID, t.Period {base} GROUP BY t.Match_ID, t.Period HAVING COUNT(*) != 2
            )"""
    ).fetchone()[0]
    total_groups = conn.execute(f"SELECT COUNT(*) FROM (SELECT t.Match_ID, t.Period {base} GROUP BY t.Match_ID, t.Period)").fetchone()[0]
    if bad:
        reps = conn.execute(
            f"""SELECT t.Match_ID {base} GROUP BY t.Match_ID, t.Period HAVING COUNT(*) != 2 LIMIT {REPRESENTATIVE_LIMIT}"""
        ).fetchall()
        findings.append(Finding("CRITICAL", "HIGH", "team_stats.period_not_two_teams", "fact_team_match_stats", "Match_ID+Period", league_id, season,
            f"[{db_label}] 同一 Match_ID+Period 不是恰好两队(应为主客各一行)", bad, total_groups, pct(bad, total_groups), None, None,
            ";".join(str(r[0]) for r in reps),
            "该场次团队统计聚合会重复/缺失", "重复插入或部分写入", "复核 ingest 幂等性"))

    # Team_ID/is_home 与 dim_match 主客一致性(该表没有 is_home 列,用 Team_ID 对齐主客)
    # Goals 与 dim_match 比分对齐(仅 Period='All',仅完赛比赛)
    goal_mismatch = conn.execute(
        f"""SELECT t.Match_ID, t.Team_ID, t.Goals, m.home_score, m.away_score, m.Home_Team_ID
            FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID
            WHERE {league_filter_sql} AND t.Period='All' AND m.status='Finish' AND t.Goals IS NOT NULL"""
    ).fetchall()
    n_goal_checked = 0
    n_goal_bad = []
    for r in goal_mismatch:
        expected = r["home_score"] if r["Team_ID"] == r["Home_Team_ID"] else r["away_score"]
        if expected is None:
            continue
        n_goal_checked += 1
        if r["Goals"] != expected:
            n_goal_bad.append(r["Match_ID"])
    if n_goal_bad:
        findings.append(Finding("HIGH", "HIGH", "team_stats.goals_mismatch_dim_match", "fact_team_match_stats", "Goals", league_id, season,
            f"[{db_label}] Period='All' 的 Goals 与 dim_match 对应方比分不一致", len(n_goal_bad), n_goal_checked,
            pct(len(n_goal_bad), n_goal_checked), None, None, ";".join(str(x) for x in n_goal_bad[:REPRESENTATIVE_LIMIT]),
            "团队进球特征与真实比分脱节", "可能含加时/点球大战计入差异,或解析错位", "逐场核对(需排除点球大战语义)"))

    # BallPossesion 双方之和接近 100(容差 ±2,单位已确认 0-100,见研究记录)
    poss_rows = conn.execute(
        f"""SELECT t.Match_ID, SUM(json_extract(t.extra_json,'$.BallPossesion')) s, COUNT(*) n
            FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID
            WHERE {league_filter_sql} AND t.Period='All' AND json_valid(t.extra_json) = 1
                  AND json_extract(t.extra_json,'$.BallPossesion') IS NOT NULL
            GROUP BY t.Match_ID HAVING n=2"""
    ).fetchall()
    bad_poss = [r for r in poss_rows if r["s"] is not None and abs(r["s"] - 100) > 2]
    if bad_poss:
        findings.append(Finding("MEDIUM", "HIGH", "team_stats.possession_sum_not_100", "fact_team_match_stats", "extra_json.BallPossesion", league_id, season,
            f"[{db_label}] 双方 BallPossesion 之和偏离 100(容差 ±2)", len(bad_poss), len(poss_rows),
            pct(len(bad_poss), len(poss_rows)), None, None, ";".join(str(r["Match_ID"]) for r in bad_poss[:REPRESENTATIVE_LIMIT]),
            "控球率特征失真", "四舍五入误差累积或解析错位", "抽样核对偏差较大的场次"))

    # team xG(extra_json.expected_goals)与 shotmap 按队汇总 xG 的差值分布
    cmp_rows = conn.execute(
        f"""SELECT t.Match_ID, t.Team_ID, json_extract(t.extra_json,'$.expected_goals') team_xg,
                   (SELECT SUM(f.xG) FROM fact_shotmap f WHERE f.Match_ID=t.Match_ID AND f.Team_ID=t.Team_ID) shot_xg
            FROM fact_team_match_stats t JOIN dim_match m ON m.Match_ID=t.Match_ID
            WHERE {league_filter_sql} AND t.Period='All' AND json_valid(t.extra_json) = 1
                  AND json_extract(t.extra_json,'$.expected_goals') IS NOT NULL"""
    ).fetchall()
    diffs = [abs(r["team_xg"] - r["shot_xg"]) for r in cmp_rows if r["shot_xg"] is not None]
    n_over = sum(1 for d in diffs if d > 0.3)
    if diffs:
        pcs = percentiles([int(d * 1000) for d in diffs], [0.5, 0.95, 0.99])
        findings.append(Finding(
            "MEDIUM" if pct(n_over, len(diffs)) and pct(n_over, len(diffs)) > 5 else "LOW", "MEDIUM",
            "team_stats.team_xg_vs_shotmap_xg_delta", "fact_team_match_stats", "extra_json.expected_goals vs fact_shotmap.xG",
            league_id, season,
            f"[{db_label}] 球队级 team xG 与 shotmap 按队汇总 xG 差值(|diff|>0.3 视为较大),p50={pcs['p50']/1000:.3f} p95={pcs['p95']/1000:.3f} p99={pcs['p99']/1000:.3f}",
            n_over, len(diffs), pct(n_over, len(diffs)), None, None, "",
            "两路 xG 若系统性不一致,说明其中一路统计口径与另一路不同(如是否含点球/加时)", "两个数据源可能包含的射门范围口径不同(如点球大战)",
            "确认 team-level xG 与 shot-level xG 求和的口径差异后再决定用哪一路做特征"))

    return findings


def match_events_anomalies(conn: sqlite3.Connection, db_label: str, league_filter_sql: str, league_id: Optional[int] = None, season: Optional[str] = None) -> list[Finding]:
    findings = []
    total = conn.execute(
        f"SELECT COUNT(*) FROM fact_match_events e JOIN dim_match m ON m.Match_ID=e.Match_ID WHERE {league_filter_sql}"
    ).fetchone()[0]
    if not total:
        return findings

    def q(sql):
        return conn.execute(sql).fetchone()[0]

    def reps(sql):
        return ";".join(str(r[0]) for r in conn.execute(sql + f" LIMIT {REPRESENTATIVE_LIMIT}").fetchall())

    base = f"FROM fact_match_events e JOIN dim_match m ON m.Match_ID=e.Match_ID WHERE {league_filter_sql}"

    # event_id 重复(非空情况下)
    dup = conn.execute(
        f"""SELECT COUNT(*) FROM (SELECT e.event_id {base} AND e.event_id IS NOT NULL GROUP BY e.event_id HAVING COUNT(*)>1)"""
    ).fetchone()[0]
    if dup:
        findings.append(Finding("MEDIUM", "MEDIUM", "events.event_id_duplicate", "fact_match_events", "event_id", league_id, season,
            f"[{db_label}] 非空 event_id 出现重复", dup, total, pct(dup, total), None, None, "",
            "若 event_id 被当全局唯一键使用会冲突", "event_id 可能不是全局唯一(仅同场次内唯一),需核实语义", "核实 event_id 的唯一性范围"))

    # is_home 合法值(0/1,或结构性允许的空字符串——仅限 AddedTime/Half)
    bad_is_home = conn.execute(
        f"""SELECT COUNT(*) {base} AND e.is_home NOT IN (0,1) AND NOT (e.is_home IS NULL OR e.is_home = '')
              AND e.event_type NOT IN ('AddedTime','Half')"""
    ).fetchone()[0]
    if bad_is_home:
        findings.append(Finding("HIGH", "HIGH", "events.is_home_invalid", "fact_match_events", "is_home", league_id, season,
            f"[{db_label}] is_home 既非 0/1,也不是 AddedTime/Half 类允许的空值", bad_is_home, total, pct(bad_is_home, total), None, None,
            reps(f"SELECT e.Match_ID {base} AND e.is_home NOT IN (0,1) AND NOT (e.is_home IS NULL OR e.is_home='') AND e.event_type NOT IN ('AddedTime','Half')"),
            "归属方判定错误会污染事件时间线", "解析异常", "复核"))

    # minute 范围(允许伤停补时,给放宽上限)
    bad_minute = q(f"SELECT COUNT(*) {base} AND e.minute IS NOT NULL AND (e.minute < 0 OR e.minute > 130)")
    if bad_minute:
        findings.append(Finding("HIGH", "HIGH", "events.minute_out_of_range", "fact_match_events", "minute", league_id, season,
            f"[{db_label}] minute 超出 [0,130]", bad_minute, total, pct(bad_minute, total), None, None,
            reps(f"SELECT e.Match_ID {base} AND e.minute IS NOT NULL AND (e.minute < 0 OR e.minute > 130)"),
            "事件时间线错乱", "解析异常", "复核"))

    # 红黄牌字段与 event_type 矛盾:event_type='Card' 但 card_type 为空;非 Card 但 card_type 非空
    n1 = q(f"SELECT COUNT(*) {base} AND e.event_type='Card' AND (e.card_type IS NULL OR e.card_type='')")
    n1_total = q(f"SELECT COUNT(*) {base} AND e.event_type='Card'")
    if n1:
        findings.append(Finding("MEDIUM", "HIGH", "events.card_missing_card_type", "fact_match_events", "card_type", league_id, season,
            f"[{db_label}] event_type='Card' 但 card_type 为空", n1, n1_total, pct(n1, n1_total), None, None,
            reps(f"SELECT e.Match_ID {base} AND e.event_type='Card' AND (e.card_type IS NULL OR e.card_type='')"),
            "牌类事件缺细分类型", "解析缺失", "复核"))
    n2 = q(f"SELECT COUNT(*) {base} AND e.event_type != 'Card' AND e.card_type IS NOT NULL AND e.card_type != ''")
    if n2:
        findings.append(Finding("LOW", "MEDIUM", "events.card_type_on_non_card", "fact_match_events", "card_type", league_id, season,
            f"[{db_label}] event_type≠'Card' 但 card_type 非空", n2, total, pct(n2, total), None, None,
            reps(f"SELECT e.Match_ID {base} AND e.event_type != 'Card' AND e.card_type IS NOT NULL AND e.card_type != ''"),
            "语义矛盾,可能是列位错位", "解析异常", "复核"))

    # 事件比分不能倒退(按 Match_ID, event_index 排序后 home_score/away_score 应单调不减)
    rows = conn.execute(
        f"""SELECT e.Match_ID, e.event_index, e.home_score, e.away_score {base}
            AND e.home_score IS NOT NULL AND e.away_score IS NOT NULL ORDER BY e.Match_ID, e.event_index"""
    ).fetchall()
    regress_matches: set = set()
    last_mid = None
    last_h = last_a = -1
    for r in rows:
        if r["Match_ID"] != last_mid:
            last_mid = r["Match_ID"]
            last_h = last_a = -1
        if r["home_score"] < last_h or r["away_score"] < last_a:
            regress_matches.add(r["Match_ID"])
        last_h, last_a = r["home_score"], r["away_score"]
    if regress_matches:
        findings.append(Finding("HIGH", "MEDIUM", "events.score_regression", "fact_match_events", "home_score/away_score", league_id, season,
            f"[{db_label}] 事件时间线上比分出现倒退(按 event_index 排序)", len(regress_matches), total_finish_events(conn, league_filter_sql), None, None, None,
            ";".join(str(x) for x in sorted(regress_matches)[:REPRESENTATIVE_LIMIT]),
            "事件比分时间线不可信,下游任何「某分钟比分」类特征会出错", "event_index 排序未必等于真实时间顺序,或解析错位",
            "核实 event_index 是否严格按真实发生时间排序"))

    # 完赛比赛完全无事件的比例
    finish_total = q(f"SELECT COUNT(DISTINCT m.Match_ID) FROM dim_match m WHERE {league_filter_sql.replace('m.','m.')} AND m.status='Finish'")
    with_events = q(f"SELECT COUNT(DISTINCT e.Match_ID) {base} AND m.status='Finish'")
    no_events = finish_total - with_events
    if no_events:
        findings.append(Finding("MEDIUM", "HIGH", "events.finished_match_no_events", "fact_match_events", "(table)", league_id, season,
            f"[{db_label}] 完赛比赛完全没有 events 记录", no_events, finish_total, pct(no_events, finish_total), None, None, "",
            "无法构建进球时间分布/事件类特征", "抓取失败或该场未提供事件数据", "见 field/table coverage 按赛季拆分,判断是否为特定赛季系统性缺失"))

    return findings


def total_finish_events(conn: sqlite3.Connection, league_filter_sql: str) -> int:
    return conn.execute(
        f"SELECT COUNT(DISTINCT m.Match_ID) FROM dim_match m WHERE {league_filter_sql} AND m.status='Finish'"
    ).fetchone()[0]


def lineup_anomalies(conn: sqlite3.Connection, db_label: str, league_filter_sql: str, league_id: Optional[int] = None, season: Optional[str] = None) -> list[Finding]:
    findings = []
    total = conn.execute(
        f"SELECT COUNT(*) FROM fact_match_lineup l JOIN dim_match m ON m.Match_ID=l.Match_ID WHERE {league_filter_sql}"
    ).fetchone()[0]
    if not total:
        return findings

    def q(sql):
        return conn.execute(sql).fetchone()[0]

    def reps(sql):
        return ";".join(str(r[0]) for r in conn.execute(sql + f" LIMIT {REPRESENTATIVE_LIMIT}").fetchall())

    base = f"FROM fact_match_lineup l JOIN dim_match m ON m.Match_ID=l.Match_ID WHERE {league_filter_sql}"

    # is_home 与 dim_match 主客关系一致
    bad = q(f"""SELECT COUNT(*) {base} AND ((l.is_home=1 AND l.Team_ID != m.Home_Team_ID) OR (l.is_home=0 AND l.Team_ID != m.Away_Team_ID))""")
    if bad:
        findings.append(Finding("CRITICAL", "HIGH", "lineup.is_home_team_mismatch", "fact_match_lineup", "is_home/Team_ID", league_id, season,
            f"[{db_label}] is_home 与该 Team_ID 是否为该场主/客队矛盾", bad, total, pct(bad, total), None, None,
            reps(f"SELECT l.Match_ID {base} AND ((l.is_home=1 AND l.Team_ID != m.Home_Team_ID) OR (l.is_home=0 AND l.Team_ID != m.Away_Team_ID))"),
            "阵容归属方向错误,污染主客相关特征", "解析错位", "复核"))

    # 每队首发人数分布(正常应为 11,非 11 需结合来源缺失判断,只报告分布不强判错误)
    starters = conn.execute(
        f"""SELECT l.Match_ID, l.Team_ID, SUM(CASE WHEN l.is_starter=1 THEN 1 ELSE 0 END) n
            {base} GROUP BY l.Match_ID, l.Team_ID"""
    ).fetchall()
    non_eleven = [r for r in starters if r["n"] != 11]
    if non_eleven:
        rate = pct(len(non_eleven), len(starters))
        sev = "MEDIUM" if rate and rate > 5 else "LOW"
        findings.append(Finding(sev, "MEDIUM", "lineup.starter_count_not_eleven", "fact_match_lineup", "is_starter", league_id, season,
            f"[{db_label}] 每队首发人数≠11(可能为红牌提前/来源缺失,不自动判错)", len(non_eleven), len(starters),
            rate, None, None, ";".join(f"{r['Match_ID']}/{r['Team_ID']}" for r in non_eleven[:REPRESENTATIVE_LIMIT]),
            "首发阵型类特征可能不完整", "来源阵容数据缺失或真实红牌提前", "抽样核对"))

    # 每队队长数量(正常应恰为 1)
    captains = conn.execute(f"""SELECT l.Match_ID, l.Team_ID, SUM(CASE WHEN l.is_captain=1 THEN 1 ELSE 0 END) n {base} GROUP BY l.Match_ID, l.Team_ID""").fetchall()
    non_one_captain = [r for r in captains if r["n"] != 1]
    if non_one_captain:
        findings.append(Finding("LOW", "MEDIUM", "lineup.captain_count_not_one", "fact_match_lineup", "is_captain", league_id, season,
            f"[{db_label}] 每队队长数量≠1", len(non_one_captain), len(captains), pct(len(non_one_captain), len(captains)), None, None,
            ";".join(f"{r['Match_ID']}/{r['Team_ID']}" for r in non_one_captain[:REPRESENTATIVE_LIMIT]),
            "队长类特征不可靠(如有臂章转移未捕获)", "来源缺失或临场换人后队长转移未同步", "抽样核对"))

    # 同一球员同时属于两队(同场次)
    both_teams = conn.execute(
        f"""SELECT l.Match_ID, l.Player_ID, COUNT(DISTINCT l.Team_ID) nt {base} GROUP BY l.Match_ID, l.Player_ID HAVING nt > 1"""
    ).fetchall()
    if both_teams:
        findings.append(Finding("CRITICAL", "HIGH", "lineup.player_both_teams", "fact_match_lineup", "Player_ID/Team_ID", league_id, season,
            f"[{db_label}] 同一球员同场次同时属于两队", len(both_teams), total, None, None, None,
            ";".join(f"{r['Match_ID']}/{r['Player_ID']}" for r in both_teams[:REPRESENTATIVE_LIMIT]),
            "严重的球队归属错乱", "解析错位", "立即人工核对"))

    # sub_in_time/sub_out_time 先后关系(有值时 sub_in <= sub_out)
    bad_sub = q(f"SELECT COUNT(*) {base} AND l.sub_in_time IS NOT NULL AND l.sub_out_time IS NOT NULL AND l.sub_in_time > l.sub_out_time")
    if bad_sub:
        findings.append(Finding("MEDIUM", "MEDIUM", "lineup.sub_in_after_sub_out", "fact_match_lineup", "sub_in_time/sub_out_time", league_id, season,
            f"[{db_label}] sub_in_time 晚于 sub_out_time", bad_sub, total, pct(bad_sub, total), None, None,
            reps(f"SELECT l.Match_ID {base} AND l.sub_in_time IS NOT NULL AND l.sub_out_time IS NOT NULL AND l.sub_in_time > l.sub_out_time"),
            "换人时间特征不可信", "解析错位", "复核"))

    return findings


# ── 跨表业务一致性(§十) ──────────────────────────────────────────────

def cross_table_consistency_rows(conn: sqlite3.Connection, league_ids: list[int]) -> list[dict[str, Any]]:
    """按联赛赛季计算 §十 要求的各类"完赛比赛中存在 X 数据的比例"及比分对账口径。"""
    out = []
    for lid in league_ids:
        for season in SEASONS:
            finished = conn.execute(
                "SELECT COUNT(*) FROM dim_match WHERE League_ID=? AND Season=? AND status='Finish'",
                (lid, season),
            ).fetchone()[0]
            if not finished:
                continue

            def covered(table, extra_where=""):
                return conn.execute(
                    f"""SELECT COUNT(DISTINCT dm.Match_ID) FROM dim_match dm
                        JOIN {table} f ON f.Match_ID = dm.Match_ID
                        WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish' {extra_where}""",
                    (lid, season),
                ).fetchone()[0]

            has_team = covered("fact_team_match_stats")
            has_player = covered("fact_player_match_stats")
            has_shot = covered("fact_shotmap")
            has_events = covered("fact_match_events")
            has_lineup = covered("fact_match_lineup")

            all_five = conn.execute(
                """SELECT COUNT(*) FROM dim_match dm WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'
                     AND EXISTS (SELECT 1 FROM fact_team_match_stats t WHERE t.Match_ID=dm.Match_ID)
                     AND EXISTS (SELECT 1 FROM fact_player_match_stats p WHERE p.Match_ID=dm.Match_ID)
                     AND EXISTS (SELECT 1 FROM fact_shotmap s WHERE s.Match_ID=dm.Match_ID)
                     AND EXISTS (SELECT 1 FROM fact_match_events e WHERE e.Match_ID=dm.Match_ID)
                     AND EXISTS (SELECT 1 FROM fact_match_lineup l WHERE l.Match_ID=dm.Match_ID)""",
                (lid, season),
            ).fetchone()[0]

            both_teams_stats = conn.execute(
                """SELECT COUNT(*) FROM (
                     SELECT dm.Match_ID FROM dim_match dm JOIN fact_team_match_stats t ON t.Match_ID=dm.Match_ID
                     WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish' AND t.Period='All'
                     GROUP BY dm.Match_ID HAVING COUNT(DISTINCT t.Team_ID)=2)""",
                (lid, season),
            ).fetchone()[0]
            both_lineup = conn.execute(
                """SELECT COUNT(*) FROM (
                     SELECT dm.Match_ID FROM dim_match dm JOIN fact_match_lineup l ON l.Match_ID=dm.Match_ID
                     WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'
                     GROUP BY dm.Match_ID HAVING COUNT(DISTINCT l.Team_ID)=2)""",
                (lid, season),
            ).fetchone()[0]
            lineup_22 = conn.execute(
                """SELECT COUNT(*) FROM (
                     SELECT dm.Match_ID FROM dim_match dm JOIN fact_match_lineup l ON l.Match_ID=dm.Match_ID
                     WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish' AND l.is_starter=1
                     GROUP BY dm.Match_ID HAVING COUNT(*)=22)""",
                (lid, season),
            ).fetchone()[0]

            # player stats 与 lineup 出场人员匹配率(有 minutes_played>0 的球员应在 lineup 里)
            stats_matches_lineup = conn.execute(
                """SELECT COUNT(*) FROM fact_player_match_stats p JOIN dim_match dm ON dm.Match_ID=p.Match_ID
                   WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish' AND p.minutes_played > 0
                     AND EXISTS (SELECT 1 FROM fact_match_lineup l WHERE l.Match_ID=p.Match_ID AND l.Player_ID=p.Player_ID)""",
                (lid, season),
            ).fetchone()[0]
            stats_played_total = conn.execute(
                """SELECT COUNT(*) FROM fact_player_match_stats p JOIN dim_match dm ON dm.Match_ID=p.Match_ID
                   WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish' AND p.minutes_played > 0""",
                (lid, season),
            ).fetchone()[0]

            # 比分一致率:team stats Goals(Period=All) / player goals 汇总 / shotmap goal 汇总 / event goal 汇总
            def score_consistency(sql_home, sql_away):
                rows = conn.execute(
                    f"""SELECT dm.Match_ID, dm.home_score, dm.away_score, ({sql_home}) h, ({sql_away}) a
                        FROM dim_match dm WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'""",
                    (lid, season),
                ).fetchall()
                checked = [r for r in rows if r["h"] is not None and r["a"] is not None]
                matched = [r for r in checked if r["h"] == r["home_score"] and r["a"] == r["away_score"]]
                return len(matched), len(checked)

            ts_h = "SELECT Goals FROM fact_team_match_stats WHERE Match_ID=dm.Match_ID AND Period='All' AND Team_ID=dm.Home_Team_ID"
            ts_a = "SELECT Goals FROM fact_team_match_stats WHERE Match_ID=dm.Match_ID AND Period='All' AND Team_ID=dm.Away_Team_ID"
            ts_match, ts_checked = score_consistency(ts_h, ts_a)

            pg_h = "SELECT SUM(goals) FROM fact_player_match_stats WHERE Match_ID=dm.Match_ID AND Team_ID=dm.Home_Team_ID"
            pg_a = "SELECT SUM(goals) FROM fact_player_match_stats WHERE Match_ID=dm.Match_ID AND Team_ID=dm.Away_Team_ID"
            pg_match, pg_checked = score_consistency(pg_h, pg_a)

            sg_h = "SELECT COUNT(*) FROM fact_shotmap WHERE Match_ID=dm.Match_ID AND Team_ID=dm.Home_Team_ID AND Outcome='Goal'"
            sg_a = "SELECT COUNT(*) FROM fact_shotmap WHERE Match_ID=dm.Match_ID AND Team_ID=dm.Away_Team_ID AND Outcome='Goal'"
            sg_match, sg_checked = score_consistency(sg_h, sg_a)

            eg_h = "SELECT COUNT(*) FROM fact_match_events WHERE Match_ID=dm.Match_ID AND event_type='Goal' AND is_home=1"
            eg_a = "SELECT COUNT(*) FROM fact_match_events WHERE Match_ID=dm.Match_ID AND event_type='Goal' AND is_home=0"
            eg_match, eg_checked = score_consistency(eg_h, eg_a)

            # 乌龙球对账(§六B):player-goals 路径的比分不符,逐场检验"主队比分==
            # 主队球员进球+客队乌龙球"(且反向)是否成立——不能只因联赛间同步就断言
            # 乌龙球解释了差异,必须逐场验证再统计比例。
            og_rows = conn.execute(
                """SELECT dm.Match_ID, dm.home_score, dm.away_score,
                       (SELECT COALESCE(SUM(goals),0) FROM fact_player_match_stats p
                        WHERE p.Match_ID=dm.Match_ID AND p.Team_ID=dm.Home_Team_ID) hpg,
                       (SELECT COALESCE(SUM(goals),0) FROM fact_player_match_stats p
                        WHERE p.Match_ID=dm.Match_ID AND p.Team_ID=dm.Away_Team_ID) apg,
                       (SELECT COALESCE(SUM(owngoal),0) FROM fact_player_match_stats p
                        WHERE p.Match_ID=dm.Match_ID AND p.Team_ID=dm.Home_Team_ID) hog,
                       (SELECT COALESCE(SUM(owngoal),0) FROM fact_player_match_stats p
                        WHERE p.Match_ID=dm.Match_ID AND p.Team_ID=dm.Away_Team_ID) aog,
                       (SELECT COUNT(*) FROM fact_player_match_stats p WHERE p.Match_ID=dm.Match_ID) npg
                FROM dim_match dm WHERE dm.League_ID=? AND dm.Season=? AND dm.status='Finish'
                  AND dm.home_score IS NOT NULL AND dm.away_score IS NOT NULL""",
                (lid, season),
            ).fetchall()
            og_mismatch = 0
            og_explained = 0
            og_unexplained_ids: list[str] = []
            for r in og_rows:
                if r["npg"] == 0:
                    continue
                if r["hpg"] == r["home_score"] and r["apg"] == r["away_score"]:
                    continue
                og_mismatch += 1
                if r["hpg"] + r["aog"] == r["home_score"] and r["apg"] + r["hog"] == r["away_score"]:
                    og_explained += 1
                elif len(og_unexplained_ids) < REPRESENTATIVE_LIMIT:
                    og_unexplained_ids.append(str(r["Match_ID"]))

            out.append({
                "league_id": lid, "league_name": LEAGUE_NAMES.get(lid, str(lid)), "season": season,
                "finished_matches": finished,
                "has_team_stats_rate_pct": pct(has_team, finished),
                "has_player_stats_rate_pct": pct(has_player, finished),
                "has_shotmap_rate_pct": pct(has_shot, finished),
                "has_events_rate_pct": pct(has_events, finished),
                "has_lineup_rate_pct": pct(has_lineup, finished),
                "has_all_five_rate_pct": pct(all_five, finished),
                "both_teams_stats_complete_rate_pct": pct(both_teams_stats, finished),
                "both_teams_lineup_complete_rate_pct": pct(both_lineup, finished),
                "lineup_22_starters_rate_pct": pct(lineup_22, finished),
                "player_stats_matches_lineup_rate_pct": pct(stats_matches_lineup, stats_played_total),
                "team_stats_goals_match_dim_match_rate_pct": pct(ts_match, ts_checked),
                "team_stats_goals_checked": ts_checked,
                "player_goals_sum_match_dim_match_rate_pct": pct(pg_match, pg_checked),
                "player_goals_checked": pg_checked,
                "shotmap_goal_sum_match_dim_match_rate_pct": pct(sg_match, sg_checked),
                "shotmap_goal_checked": sg_checked,
                "event_goal_sum_match_dim_match_rate_pct": pct(eg_match, eg_checked),
                "event_goal_checked": eg_checked,
                "player_goals_mismatch_count": og_mismatch,
                "player_goals_mismatch_explained_by_owngoal_count": og_explained,
                "player_goals_mismatch_explained_by_owngoal_rate_pct": pct(og_explained, og_mismatch),
                "player_goals_mismatch_unexplained_count": og_mismatch - og_explained,
                "player_goals_mismatch_unexplained_representative_ids": ";".join(og_unexplained_ids),
                "note": "比分口径未剔除点球大战/加时特殊场次,不一致场次需人工复核而非机械判错(见 §九/§十 说明);乌龙球解释比例见 player_goals_mismatch_explained_by_owngoal_rate_pct,逐场验证而非按同步性推断因果",
            })
    return out


# ── 模型可用性分级(§十三) ────────────────────────────────────────────

READY_THRESHOLD_PCT = 90.0
READY_WITH_FILTERS_THRESHOLD_PCT = 60.0
NOT_READY_BELOW_PCT = 5.0

# 每个 feature_family 的 match-coverage 锚点字段(§五A):锚点字段代表"这场比赛
# 是否有该 family 可用数据"的单一、有明确业务含义的指标,不是全部字段的平均/最大值。
# 选取标准:该 family 里最基础、覆盖率最应接近 100% 的字段(球队/球员/门将角色专属
# 判定已通过 applicable_sql 限定分母)。physical 已用真实数据确认全 0%,不需要锚点
# (直接报告 0);context 是动态发现的次要统计字段集合,无统一锚点,留空并在 reason
# 里说明——不得伪造一个不存在的 match coverage 数字。
FAMILY_ANCHOR_FIELD: dict[str, tuple[str, str]] = {
    "standard_match": ("fact_match_lineup", "is_starter"),
    "shot_xg": ("fact_shotmap", "xG"),
    "player_attacking_xg_xa": ("fact_player_match_stats", "expected_goals"),
    "defensive_duel_passing": ("fact_player_match_stats", "accurate_passes"),
    "goalkeeper_advanced": ("fact_player_match_stats", "saves"),
}


def feature_readiness_rows(
    field_cov: list[dict[str, Any]],
    anomalies: list[Finding],
    league_ids: list[int],
    families: list[str],
    completed_by_league_season: dict[tuple[int, str], int],
) -> list[dict[str, Any]]:
    """League_ID × Season × feature_family 粒度(§五修复版)。

    covered_match_count 用该 family 的锚点字段(FAMILY_ANCHOR_FIELD)在同一
    League×Season 下的 covered_matches,不再用「全部字段/全部赛季的 max」这种
    跨粒度写法。applicable_record_coverage_rate_pct 是该分区全部字段(REQUIRED+
    CONDITIONAL+OPTIONAL)的整体均值,仅供参考;minimum_required_field_coverage_pct
    才是决定 readiness 的主指标——只看 REQUIRED 字段里覆盖率最差的一个,CONDITIONAL/
    OPTIONAL 字段的合法稀疏值不会拖累它。"""
    out: list[dict[str, Any]] = []
    anomaly_by_partition: dict[tuple, dict[str, int]] = defaultdict(lambda: {"CRITICAL": 0, "HIGH": 0})
    for f in anomalies:
        if f.league_id is not None and f.season is not None and f.severity in ("CRITICAL", "HIGH"):
            anomaly_by_partition[(f.league_id, f.season)][f.severity] += 1

    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in field_cov:
        by_key[(r["league_id"], r["season"], r["family"])].append(r)

    for lid in league_ids:
        for season in SEASONS:
            completed = completed_by_league_season.get((lid, season), 0)
            for family in families:
                rows = by_key.get((lid, season, family), [])
                applicable_rows = [r for r in rows if not r["not_applicable"]]
                crit_hi = anomaly_by_partition.get((lid, season), {"CRITICAL": 0, "HIGH": 0})

                if not applicable_rows:
                    out.append(_readiness_row(
                        lid=lid, season=season, family=family, completed=completed,
                        covered="", covered_rate="",
                        applicable_record_count="", non_null_record_count="", overall_rate="",
                        required_field_count=0, optional_field_count=0, min_required_rate="",
                        critical_anomaly_count=crit_hi["CRITICAL"], high_anomaly_count=crit_hi["HIGH"],
                        readiness="NOT_APPLICABLE",
                        reason="该 family 在此 League×Season 全部字段均为 NOT_APPLICABLE(受限赛季,非缺失)",
                    ))
                    continue

                required_rows = [r for r in applicable_rows if r["applicability"] == "REQUIRED"]
                optional_rows = [r for r in applicable_rows if r["applicability"] != "REQUIRED"]

                applicable_record_count = sum(r["applicable_rows"] for r in applicable_rows)
                non_null_record_count = sum(r["non_null_rows"] for r in applicable_rows)
                overall_rate = pct(non_null_record_count, applicable_record_count)

                anchor = FAMILY_ANCHOR_FIELD.get(family)
                if anchor:
                    anchor_rows = [r for r in rows if (r["table"], r["field"]) == anchor and not r["not_applicable"]]
                    covered = anchor_rows[0]["covered_matches"] if anchor_rows else 0
                    covered_rate = pct(covered, completed)
                elif family == "physical":
                    covered, covered_rate = 0, 0.0
                else:
                    covered, covered_rate = "", ""

                if required_rows:
                    required_rates = [pct(r["non_null_rows"], r["applicable_rows"]) or 0.0
                                       for r in required_rows if r["applicable_rows"] > 0]
                    min_required_rate = min(required_rates) if required_rates else 0.0
                else:
                    min_required_rate = None

                readiness, reason = _classify_readiness(
                    family, required_rows, min_required_rate, overall_rate, covered_rate,
                )

                out.append(_readiness_row(
                    lid, season, family, completed, covered, covered_rate,
                    applicable_record_count, non_null_record_count, overall_rate,
                    len(required_rows), len(optional_rows),
                    "" if min_required_rate is None else round(min_required_rate, 4),
                    crit_hi["CRITICAL"], crit_hi["HIGH"], readiness, reason,
                ))
    return out


def _classify_readiness(
    family: str, required_rows: list[dict], min_required_rate: Optional[float],
    overall_rate: Optional[float], covered_rate: Any,
) -> tuple[str, str]:
    if not required_rows:
        # 没有 REQUIRED 字段可下钻(如 context 是动态发现的次要统计集合)——
        # 不伪造 match coverage,以整体字段均值参考,但明确降级说明依据薄弱。
        rate = overall_rate or 0.0
        if family == "physical":
            # 全量 0% 已用真实数据确认;成因未证实为"来源从不提供",疑似解析器
            # camelCase key 未做下划线别名(见 backend/fotmob_client.py 注释与
            # 报告 methodology 附录),本轮不修复 parser,仅如实标注成因未验证。
            return "NOT_READY", (
                f"全量 {rate:.1f}% 覆盖(已用真实数据确认);成因 UNVERIFIED——疑似解析器 camelCase key "
                "未做下划线别名,未证实为来源从不提供,本轮未修改 backend/fotmob_client.py,"
                "建议后续单独做 payload→parser 字段映射审计"
            )
        if rate >= READY_WITH_FILTERS_THRESHOLD_PCT:
            return "READY_WITH_FILTERS", f"该 family 无 REQUIRED 字段可下钻,以整体字段均值 {rate:.1f}% 参考,建议按字段精细过滤"
        return "NOT_READY", f"该 family 无 REQUIRED 字段可下钻,整体字段均值仅 {rate:.1f}%"

    rate = min_required_rate if min_required_rate is not None else 0.0
    if rate >= READY_THRESHOLD_PCT:
        return "READY", f"全部 {len(required_rows)} 个 REQUIRED 字段覆盖率均 >= {READY_THRESHOLD_PCT}%(最低 {rate:.1f}%)"
    if rate >= READY_WITH_FILTERS_THRESHOLD_PCT:
        return "READY_WITH_FILTERS", f"REQUIRED 字段最低覆盖率 {rate:.1f}%,处于中等区间,建议按字段/赛季过滤后使用"
    if rate <= NOT_READY_BELOW_PCT:
        return "NOT_READY", f"REQUIRED 字段最低覆盖率仅 {rate:.1f}%,当前不足以支撑该 family 的训练特征"
    return "NOT_READY", f"REQUIRED 字段最低覆盖率 {rate:.1f}%,低于可用阈值"


def _readiness_row(
    lid, season, family, completed, covered, covered_rate,
    applicable_record_count, non_null_record_count, overall_rate,
    required_field_count, optional_field_count, min_required_rate,
    critical_anomaly_count, high_anomaly_count, readiness, reason,
) -> dict[str, Any]:
    return {
        "league_id": lid, "league_name": LEAGUE_NAMES.get(lid, str(lid)), "season": season,
        "feature_family": family,
        "completed_match_count": completed,
        "covered_match_count": covered,
        "match_coverage_rate_pct": covered_rate,
        "applicable_record_count": applicable_record_count,
        "non_null_record_count": non_null_record_count,
        "applicable_record_coverage_rate_pct": "" if overall_rate is None else overall_rate,
        "required_field_count": required_field_count,
        "optional_field_count": optional_field_count,
        "minimum_required_field_coverage_pct": min_required_rate,
        "critical_anomaly_count": critical_anomaly_count,
        "high_anomaly_count": high_anomaly_count,
        "readiness": readiness,
        "reason": reason,
    }


def first_ready_season_summary(readiness_rows: list[dict[str, Any]]) -> dict[tuple[int, str], str]:
    """报告叙事用的辅助汇总(不进 CSV):对每个 League×family,找出「从该赛季起
    一直到最新赛季,readiness 均为 READY 或 READY_WITH_FILTERS」的最早赛季。
    不能代替逐赛季 readiness 判断,只用于报告里回答"最早安全可用赛季"。"""
    by_league_family: dict[tuple, dict[str, str]] = defaultdict(dict)
    for r in readiness_rows:
        by_league_family[(r["league_id"], r["feature_family"])][r["season"]] = r["readiness"]

    out: dict[tuple[int, str], str] = {}
    ok_states = {"READY", "READY_WITH_FILTERS"}
    for (lid, family), by_season in by_league_family.items():
        first = ""
        for s in SEASONS:
            if s not in by_season:
                continue
            if all(by_season.get(later) in ok_states for later in SEASONS[SEASONS.index(s):] if later in by_season):
                first = s
                break
        out[(lid, family)] = first
    return out


# ── 主流程 ────────────────────────────────────────────────────────────

def all_field_specs(conn: sqlite3.Connection, league_filter_sql: str) -> list[FieldSpec]:
    specs = _player_field_specs() + _shotmap_field_specs() + _events_field_specs() + _lineup_field_specs()
    keys = discover_team_stats_extra_json_keys(conn, league_filter_sql)
    specs += _team_stats_extra_json_specs(keys)
    return specs


def compute_field_coverage(conn: sqlite3.Connection, league_ids: list[int]) -> list[dict[str, Any]]:
    league_filter_sql = f"m.League_ID IN ({','.join(str(x) for x in league_ids)})"
    specs = all_field_specs(conn, league_filter_sql)
    rows: list[dict[str, Any]] = []
    for spec in specs:
        rows.extend(field_coverage_rows(conn, spec, league_filter_sql))
    rows.extend(lineup_sub_time_coverage_rows(conn, league_ids))
    return rows


def enrich_field_coverage_with_deltas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补齐 vs 英超同赛季差值、vs 本联赛上一赛季差值、首末出现赛季、is_all_null/is_all_zero。"""
    by_table_field: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_table_field[(r["table"], r["field"])].append(r)

    out = []
    for (table, field), group in by_table_field.items():
        by_ls = {(r["league_id"], r["season"]): r for r in group}
        seasons_present = sorted({r["season"] for r in group if not r["not_applicable"] and r["applicable_rows"] > 0})
        first_season = seasons_present[0] if seasons_present else ""
        last_season = seasons_present[-1] if seasons_present else ""

        for r in group:
            rate = pct(r["non_null_rows"], r["applicable_rows"])
            bench = by_ls.get((BENCHMARK_LEAGUE_ID, r["season"]))
            bench_rate = pct(bench["non_null_rows"], bench["applicable_rows"]) if bench and not bench["not_applicable"] and bench["applicable_rows"] else None
            delta_bench = (rate - bench_rate) if (rate is not None and bench_rate is not None and r["league_id"] != BENCHMARK_LEAGUE_ID) else None

            prev_idx = SEASONS.index(r["season"]) - 1 if r["season"] in SEASONS else -1
            prev_rate = None
            if prev_idx >= 0:
                prev = by_ls.get((r["league_id"], SEASONS[prev_idx]))
                if prev and not prev["not_applicable"] and prev["applicable_rows"]:
                    prev_rate = pct(prev["non_null_rows"], prev["applicable_rows"])
            delta_prev = (rate - prev_rate) if (rate is not None and prev_rate is not None) else None

            all_null = (not r["not_applicable"]) and r["applicable_rows"] > 0 and r["non_null_rows"] == 0
            all_zero = (not r["not_applicable"]) and r["non_null_rows"] > 0 and r["zero_rows"] == r["non_null_rows"]

            out.append({
                "table_name": table, "field_name": field, "family": r["family"],
                "applicability": r.get("applicability", "REQUIRED"),
                "league_id": r["league_id"], "league_name": LEAGUE_NAMES.get(r["league_id"], str(r["league_id"])),
                "season": r["season"],
                "applicable_records": r["applicable_rows"],
                "non_null_records": r["non_null_rows"],
                "non_null_rate_pct": "" if r["not_applicable"] else (rate if rate is not None else ""),
                "blank_string_records": r["blank_string_rows"],
                "zero_records": r["zero_rows"],
                "covered_matches": r["covered_matches"],
                "total_matches_in_partition": r["total_matches_in_partition"],
                "match_coverage_rate_pct": "" if r["not_applicable"] else pct(r["covered_matches"], r["total_matches_in_partition"]),
                "vs_benchmark_epl_same_season_delta_pct": "" if delta_bench is None else round(delta_bench, 4),
                "vs_prev_season_same_league_delta_pct": "" if delta_prev is None else round(delta_prev, 4),
                "first_season_seen": first_season,
                "last_season_seen": last_season,
                "all_null_flag": all_null,
                "all_zero_flag": all_zero,
                "status": "NOT_APPLICABLE" if r["not_applicable"] else ("OK" if r["applicable_rows"] else "NO_APPLICABLE_RECORDS"),
            })
    return out


FIELD_COVERAGE_FIELDNAMES = [
    "table_name", "field_name", "family", "applicability", "league_id", "league_name", "season",
    "applicable_records", "non_null_records", "non_null_rate_pct", "blank_string_records",
    "zero_records", "covered_matches", "total_matches_in_partition", "match_coverage_rate_pct",
    "vs_benchmark_epl_same_season_delta_pct", "vs_prev_season_same_league_delta_pct",
    "first_season_seen", "last_season_seen", "all_null_flag", "all_zero_flag", "status",
]

PARTITION_SUMMARY_FIELDNAMES = [
    "league_id", "league_name", "season", "match_count", "status_finish", "status_not_started",
    "status_cancelled", "status_other", "distinct_teams", "distinct_players_in_lineup",
    "earliest_date", "latest_date", "home_team_id_null", "away_team_id_null",
    "home_team_name_blank", "away_team_name_blank", "finish_score_missing",
    "finish_score_missing_rate_pct", "negative_score_count",
    "fact_shotmap_rows", "fact_player_match_stats_rows", "fact_team_match_stats_rows",
    "fact_match_events_rows", "fact_match_lineup_rows",
    "fact_shotmap_covered_matches", "fact_player_match_stats_covered_matches",
    "fact_team_match_stats_covered_matches", "fact_match_events_covered_matches",
    "fact_match_lineup_covered_matches", "derived_team_count",
    "derived_double_round_robin_expected", "match_count_vs_round_robin_delta", "volume_gap_note",
]

TABLE_COVERAGE_FIELDNAMES = [
    "league_id", "league_name", "season", "table_name", "completed_match_count",
    "covered_match_count", "match_coverage_rate_pct", "total_rows",
    "rows_per_match_p0", "rows_per_match_p1", "rows_per_match_p5", "rows_per_match_p25",
    "rows_per_match_p50", "rows_per_match_p75", "rows_per_match_p95", "rows_per_match_p99",
    "rows_per_match_max",
]

MERGE_PARITY_FIELDNAMES = [
    "table_name", "league_id", "league_name", "season", "classification",
    "verify_row_count", "allwin_row_count", "match_ids_only_in_verify",
    "match_ids_only_in_allwin", "common_match_ids", "row_count_delta",
    "mismatch_row_count", "mismatch_types", "notes", "representative_ids",
]

FEATURE_READINESS_FIELDNAMES = [
    "league_id", "league_name", "season", "feature_family",
    "completed_match_count", "covered_match_count", "match_coverage_rate_pct",
    "applicable_record_count", "non_null_record_count", "applicable_record_coverage_rate_pct",
    "required_field_count", "optional_field_count", "minimum_required_field_coverage_pct",
    "critical_anomaly_count", "high_anomaly_count", "readiness", "reason",
]

FEATURE_FAMILIES = [
    "standard_match", "shot_xg", "player_attacking_xg_xa", "defensive_duel_passing",
    "goalkeeper_advanced", "physical", "context",
]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--core-db", required=True, type=Path, help="生产核心库路径(只读快照,如 allwin.db)")
    p.add_argument("--verify-db", required=True, type=Path, help="早期四联赛采集/验证库路径(只读快照)")
    p.add_argument("--output-dir", required=True, type=Path, help="CSV/JSON 输出目录")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        core = connect_ro(args.core_db, "core")
        verify = connect_ro(args.verify_db, "verify")
    except SystemExit as e:
        eprint(str(e))
        return 1

    register_udfs(core)
    register_udfs(verify)

    eprint("[1/9] schema 内省与对比…")
    core_schema = schema_snapshot(core, CORE_TABLES)
    verify_schema = schema_snapshot(verify, CORE_TABLES)
    schema_diffs = diff_schema(core_schema, verify_schema)

    eprint("[2/9] 真实候选键粒度分析…")
    league_filter_core_all = f"League_ID IN ({','.join(str(x) for x in ALL_LEAGUE_IDS)})"
    league_filter_verify_all = f"League_ID IN ({','.join(str(x) for x in VERIFY_LEAGUE_IDS)})"
    grain_rows = []
    for t in CORE_TABLES:
        grain_rows.append({"db": "core", **grain_report(core, t, league_filter_core_all)})
        grain_rows.append({"db": "verify", **grain_report(verify, t, league_filter_verify_all)})

    eprint("[3/9] 联赛×赛季基础画像…")
    partition_rows = partition_summary_rows(core, ALL_LEAGUE_IDS)
    completed_by_league_season = {
        (r["league_id"], r["season"]): r["status_finish"] for r in partition_rows
    }

    eprint("[4/9] 表覆盖率(逐表逐联赛赛季)…")
    table_cov_rows = table_coverage_rows(core, ALL_LEAGUE_IDS)

    eprint("[5/9] verify → allwin 合并完整性(流式多重集合哈希)…")
    merge_rows: list[dict[str, Any]] = []
    dim_match_cols = common_columns(core_schema, verify_schema, "dim_match")
    dim_match_ok = not any(d["table"] == "dim_match" and not d["compatible"] for d in schema_diffs)
    merge_rows.extend(merge_parity_for_table(core, verify, "dim_match", dim_match_cols, sorted(VERIFY_LEAGUE_IDS), dim_match_ok))
    dim_player_rows = dim_player_parity_rows(core, verify, sorted(VERIFY_LEAGUE_IDS))
    merge_rows.extend(dim_player_rows)
    dim_player_occurrences = dim_player_partition_occurrences(dim_player_rows)
    dim_player_unique = dim_player_name_diff_summary(core, verify, sorted(VERIFY_LEAGUE_IDS))
    for t in FACT_TABLES:
        cols = common_columns(core_schema, verify_schema, t)
        ok = not any(d["table"] == t and not d["compatible"] for d in schema_diffs)
        merge_rows.extend(merge_parity_for_table(core, verify, t, cols, sorted(VERIFY_LEAGUE_IDS), ok))

    eprint("[6/9] 字段覆盖率(五大联赛全量字段)…")
    field_cov_raw = compute_field_coverage(core, ALL_LEAGUE_IDS)
    field_cov_enriched = enrich_field_coverage_with_deltas(field_cov_raw)

    eprint("[7/9] 核心完整性/唯一性/参照完整性 + 领域规则异常检测(core db, 逐联赛赛季)…")
    anomalies: list[Finding] = []
    anomalies += dim_player_anomalies(core, "core")  # dim_player 是全局表,没有 League/Season 维度
    existing_partitions = {(r["league_id"], r["season"]) for r in partition_rows}
    for lid in ALL_LEAGUE_IDS:
        for season in SEASONS:
            if (lid, season) not in existing_partitions:
                continue
            bare_filter = f"League_ID={lid} AND Season='{season}'"
            m_filter = f"m.League_ID={lid} AND m.Season='{season}'"
            anomalies += dim_match_anomalies(core, "core", bare_filter, lid, season)
            anomalies += orphan_and_referential_anomalies(core, "core", m_filter, lid, season)
            anomalies += shotmap_anomalies(core, "core", m_filter, lid, season)
            anomalies += player_stats_anomalies(core, "core", m_filter, lid, season)
            anomalies += team_stats_anomalies(core, "core", m_filter, lid, season)
            anomalies += match_events_anomalies(core, "core", m_filter, lid, season)
            anomalies += lineup_anomalies(core, "core", m_filter, lid, season)
    # 同一套规则额外在 verify 源库上跑一遍(仅四联赛),交叉验证"异常是否由合并引入"
    for lid in sorted(VERIFY_LEAGUE_IDS):
        for season in SEASONS:
            bare_filter = f"League_ID={lid} AND Season='{season}'"
            m_filter = f"m.League_ID={lid} AND m.Season='{season}'"
            anomalies += dim_match_anomalies(verify, "verify", bare_filter, lid, season)
            anomalies += orphan_and_referential_anomalies(verify, "verify", m_filter, lid, season)

    eprint("[8/9] 跨表业务一致性…")
    cross_rows = cross_table_consistency_rows(core, ALL_LEAGUE_IDS)

    eprint("[9/9] 模型可用性分级…")
    readiness_rows = feature_readiness_rows(
        field_cov_raw, [f for f in anomalies if f.table.startswith("fact_") or f.table == "dim_match"],
        ALL_LEAGUE_IDS, FEATURE_FAMILIES, completed_by_league_season,
    )

    # ── 写出 CSV ──
    def write_csv(name: str, fieldnames: list[str], rows: list[dict], sort_keys: list[str]) -> int:
        w = CsvWriter(out_dir / name, fieldnames)
        for r in rows:
            w.add(r)
        return w.write(sort_keys=sort_keys)

    n_partition = write_csv("top5_partition_summary.csv", PARTITION_SUMMARY_FIELDNAMES, partition_rows, ["league_id", "season"])
    n_table_cov = write_csv("top5_table_coverage.csv", TABLE_COVERAGE_FIELDNAMES, table_cov_rows, ["league_id", "season", "table_name"])
    n_field_cov = write_csv("top5_field_coverage.csv", FIELD_COVERAGE_FIELDNAMES, field_cov_enriched, ["table_name", "field_name", "league_id", "season"])
    n_anomalies = write_csv(
        "top5_anomalies.csv", FINDING_FIELDS, [finding_row(f) for f in anomalies],
        ["severity", "table_name", "field_name", "league_id", "season"],
    )
    n_readiness = write_csv("top5_feature_readiness.csv", FEATURE_READINESS_FIELDNAMES, readiness_rows, ["league_id", "season", "feature_family"])
    n_merge = write_csv("verify_merge_parity.csv", MERGE_PARITY_FIELDNAMES, merge_rows, ["table_name", "league_id", "season"])

    severity_counts = Counter(f.severity for f in anomalies)
    classification_counts = Counter(r["classification"] for r in merge_rows)

    summary = {
        "generated_by": "analysis/data_quality/audit_top5_historical_core.py",
        "audit_method_version": 3,
        "output_schema_version": 3,
        "scope": {
            "league_ids": ALL_LEAGUE_IDS, "league_names": LEAGUE_NAMES,
            "verify_league_ids": sorted(VERIFY_LEAGUE_IDS), "benchmark_league_id": BENCHMARK_LEAGUE_ID,
            "seasons": SEASONS, "core_tables": CORE_TABLES,
        },
        "input_databases": {
            "core_db": str(args.core_db), "verify_db": str(args.verify_db),
        },
        "schema_diff_summary": [
            {"table": d["table"], "compatible": d["compatible"], "only_in_core": d["only_in_core"],
             "only_in_verify": d["only_in_verify"], "type_mismatch_count": len(d["type_mismatches"]),
             "column_order_differs": d["column_order_differs"]}
            for d in schema_diffs
        ],
        "grain_summary": [
            {"db": g["db"], "table": g["table"], "duplicate_groups": g["duplicate_groups"],
             "duplicate_row_rate_pct": g["duplicate_row_rate_pct"]}
            for g in grain_rows
        ],
        "merge_parity_classification_counts": dict(classification_counts),
        "anomaly_severity_counts": dict(severity_counts),
        "dim_player_name_differences": {
            "partition_occurrences": dim_player_occurrences,
            "unique_players": dim_player_unique["unique_players"],
            "missing_from_allwin": dim_player_unique["missing_from_allwin"],
            "blank_in_allwin": dim_player_unique["blank_in_allwin"],
        },
        "cross_table_consistency": cross_rows,
        "row_counts": {
            "top5_partition_summary.csv": n_partition,
            "top5_table_coverage.csv": n_table_cov,
            "top5_field_coverage.csv": n_field_cov,
            "top5_anomalies.csv": n_anomalies,
            "top5_feature_readiness.csv": n_readiness,
            "verify_merge_parity.csv": n_merge,
        },
    }
    (out_dir / "top5_quality_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )

    eprint(f"完成。输出目录: {out_dir}")
    eprint(f"  severity counts: {dict(severity_counts)}")
    eprint(f"  merge classification counts: {dict(classification_counts)}")

    core.close()
    verify.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
