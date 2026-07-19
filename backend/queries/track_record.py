"""公开战绩(track record)只读查询。

正式样本口径(CLAUDE.md §9.1,详见 docs/prediction-integrity.md):
- is_official = 1 且曾锁定(locked;撤回的官方样本仍完整展示并计入撤回统计,
  防止用"撤回"选择性美化战绩);
- published_at 严格早于 kickoff_at_utc(赛前);
- superseded_by IS NULL(修正链只取最新版,旧版保留可查);
- 指标计算基于已结算(有 prediction_outcomes)的非撤回样本。
"""

import sqlite3

_OFFICIAL_WHERE = """
    s.is_official = 1
    AND s.status IN ('locked', 'retracted')
    AND s.locked_at IS NOT NULL
    AND s.published_at IS NOT NULL
    AND s.kickoff_at_utc IS NOT NULL
    AND s.published_at < s.kickoff_at_utc
    AND s.superseded_by IS NULL
"""


def official_samples(
    conn: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    model_version_id: str | None = None,
) -> dict:
    """分页返回全部正式样本(含未结算与撤回,全透明,不挑选)。"""
    params: list = []
    where = _OFFICIAL_WHERE
    if model_version_id:
        where += " AND s.model_version_id = ?"
        params.append(model_version_id)
    total = conn.execute(
        f"SELECT COUNT(*) FROM prediction_snapshots s WHERE {where}", params
    ).fetchone()[0]
    retracted = conn.execute(
        f"SELECT COUNT(*) FROM prediction_snapshots s WHERE {where} AND s.status='retracted'",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT s.id, s.match_id, s.kickoff_at_utc, s.model_version_id,
                   s.generated_at, s.published_at, s.locked_at, s.prediction_hash,
                   s.home_win, s.draw, s.away_win, s.confidence, s.status,
                   o.home_goals, o.away_goals, o.outcome
            FROM prediction_snapshots s
            LEFT JOIN prediction_outcomes o ON o.match_id = s.match_id
            WHERE {where}
            ORDER BY s.kickoff_at_utc DESC, s.match_id
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    return {
        "total": total,
        "retracted_count": retracted,
        "samples": [dict(r) for r in rows],
    }


def evaluation_samples(
    conn: sqlite3.Connection, model_version_id: str | None = None
) -> list[tuple[tuple, str]]:
    """供指标计算:正式、非撤回、已结算样本 → [(probs, outcome)]。"""
    params: list = []
    where = _OFFICIAL_WHERE + " AND s.status = 'locked'"
    if model_version_id:
        where += " AND s.model_version_id = ?"
        params.append(model_version_id)
    rows = conn.execute(
        f"""SELECT s.home_win, s.draw, s.away_win, o.outcome
            FROM prediction_snapshots s
            JOIN prediction_outcomes o ON o.match_id = s.match_id
            WHERE {where}""",
        params,
    ).fetchall()
    return [((r["home_win"], r["draw"], r["away_win"]), r["outcome"]) for r in rows]


def latest_evaluation(conn: sqlite3.Connection, official_only: bool = True):
    """API 读最新一次离线评估结果(不在请求内现算)。"""
    rows = conn.execute(
        "SELECT * FROM prediction_evaluations ORDER BY evaluated_at DESC, id DESC LIMIT 20"
    ).fetchall()
    for r in rows:
        import json

        scope = json.loads(r["scope_json"] or "{}")
        if not official_only or scope.get("official_only"):
            return dict(r)
    return None
