"""公开战绩(track record)只读查询。

正式样本口径(CLAUDE.md §9.1 永久资格不变量,详见 docs/prediction-integrity.md):
- 一条快照一旦满足 is_official=1 ∧ locked_at 非空 ∧ published_at < kickoff_at_utc,
  就永久属于公开正式样本集合;
- status='retracted'、superseded_by、后续修正版都不改变其公开资格与指标分母,
  撤回/取代只是展示与审计标注(防止用撤回或修正选择性美化战绩);
- 修正链的旧版与新版同时出现在 track record(superseded_by / correction_of 字段);
- 指标计算覆盖全部已结算(有 prediction_outcomes)的正式样本,含撤回与被取代版本。

禁止:track record 与 evaluation 查询不得使用 status 或 superseded_by
选择性排除已经成为正式样本的记录(CLAUDE.md §9.1)。

时间比较纪律:published_at/kickoff_at_utc 的先后判定使用 SQLite `julianday()`,不用
裸文本比较——register_snapshot 已把新写入的 exact kickoff 规范化为 UTC 'Z' 格式,
但 `julianday()` 的比较对混合时区偏移格式(如历史遗留的 '-05:00')同样正确,是双重防线,
不依赖任何一侧字符串恰好都是 Z 格式这一假设(CLAUDE.md §6.2.1)。

资格判据来自 backend.prediction_scope.official_sample_where(唯一真源),与 daily
manifest 共用,杜绝三处口径漂移。
"""

import sqlite3

from backend.prediction_scope import official_sample_where

# 永久资格判定:只看锁定与赛前发布事实,绝不看 status / superseded_by。
_OFFICIAL_WHERE = official_sample_where("s")


def official_samples(
    conn: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    model_version_id: str | None = None,
) -> dict:
    """分页返回全部正式样本(含未结算、撤回与被取代版本,全透明,不挑选)。

    correction_of:指向被本条修正的旧快照 id(即哪条快照的 superseded_by 指向本条);
    superseded_by:本条被哪条新快照取代。二者共同构成可查询的修正链。
    """
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
    superseded = conn.execute(
        f"SELECT COUNT(*) FROM prediction_snapshots s WHERE {where} AND s.superseded_by IS NOT NULL",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT s.id, s.match_id, s.kickoff_at_utc, s.model_version_id,
                   s.generated_at, s.published_at, s.locked_at, s.prediction_hash,
                   s.home_win, s.draw, s.away_win, s.confidence, s.status,
                   s.superseded_by,
                   (SELECT p.id FROM prediction_snapshots p
                     WHERE p.superseded_by = s.id LIMIT 1) AS correction_of,
                   o.home_goals, o.away_goals, o.outcome
            FROM prediction_snapshots s
            LEFT JOIN prediction_outcomes o ON o.match_id = s.match_id
            WHERE {where}
            ORDER BY s.kickoff_at_utc DESC, s.match_id, s.locked_at, s.id
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()
    return {
        "total": total,
        "retracted_count": retracted,
        "superseded_count": superseded,
        "samples": [dict(r) for r in rows],
    }


def evaluation_samples(
    conn: sqlite3.Connection, model_version_id: str | None = None
) -> list[tuple[tuple, str]]:
    """供指标计算:全部已结算的正式样本 → [(probs, outcome)]。

    含撤回与被取代版本——正式样本一旦进入公开集合,任何后续状态变化都不能把它
    从评估分母中移除(CLAUDE.md §9.1)。
    """
    params: list = []
    where = _OFFICIAL_WHERE
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
