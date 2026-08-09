"""bronze_kbisai_odds_point 落库(本轮任务 §8c)。

与 backend/ingest/odds_snapshots.py(nowgoal,只比对"最近一条"做 hash-diff)
是完全不同的幂等策略——kbisai 的每一行都是一个独立的历史观测点,幂等靠
migrations/odds/0003 的 UNIQUE 键(含 point_hash + dup_ordinal)保证,不是
"跟上一条比对"。

写入纪律:每行一个 SAVEPOINT,按 sqlite3.IntegrityError 的信息分类:
- UNIQUE 违规 → 'duplicate'(幂等重跑的正常结果,不是错误);
- 其它(CHECK/NOT NULL 等)违规 → 'rejected_constraint'(结构性异常,必须让
  调用方看见,不能被 INSERT OR IGNORE 那种一刀切的写法悄悄吞掉——那正是
  bronze_ng_odds_snap 的 ingest_odds_records 没有的保护)。
一批里出现 rejected_constraint 不回滚已经成功的行(部分真实数据总比全丢好),
但调用方(CLI)必须把 rejected_constraint>0 当成整轮失败上报,不能悄悄放过。
"""

import sqlite3

_COLUMNS = (
    "provider", "provider_match_id", "fotmob_match_id", "market", "source_market",
    "company_id", "company_name", "handicap_line", "odds_home_or_over", "odds_draw",
    "odds_away_or_under", "closed_flag", "source_status_id", "going_time", "score",
    "market_phase", "source_updated_at", "observed_at", "ingested_at", "poll_run_id",
    "raw_point_json", "point_hash", "dup_ordinal",
)
_INSERT_SQL = (
    f"INSERT INTO bronze_kbisai_odds_point ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)})"
)


def ingest_kbisai_odds_points(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """写入一批 canonical 行(parse_match_all_odds_points 的输出)。

    整批在一个事务内执行(BEGIN IMMEDIATE ... COMMIT),事务级失败(如连接/
    磁盘错误)整体回滚;单行的 IntegrityError 只回滚到该行的 SAVEPOINT,
    不影响批次里其它行。

    返回 {"inserted": N, "duplicate": N, "rejected_constraint": N,
          "rejected_samples": [{"index","error","row"}...]}
    (rejected_samples 最多保留前 20 条,避免整批结构性错误时把日志撑爆)。
    """
    counts = {"inserted": 0, "duplicate": 0, "rejected_constraint": 0}
    rejected_samples: list[dict] = []

    conn.execute("BEGIN IMMEDIATE")
    try:
        for index, row in enumerate(rows):
            savepoint = f"kbisai_odds_point_{index}"
            conn.execute(f"SAVEPOINT {savepoint}")
            try:
                conn.execute(_INSERT_SQL, tuple(row[c] for c in _COLUMNS))
            except sqlite3.IntegrityError as exc:
                conn.execute(f"ROLLBACK TO {savepoint}")
                message = str(exc)
                if "UNIQUE constraint failed" in message:
                    counts["duplicate"] += 1
                else:
                    counts["rejected_constraint"] += 1
                    if len(rejected_samples) < 20:
                        rejected_samples.append(
                            {"index": index, "error": message,
                             "provider_match_id": row.get("provider_match_id"),
                             "market": row.get("market"), "company_id": row.get("company_id")}
                        )
            else:
                counts["inserted"] += 1
            finally:
                conn.execute(f"RELEASE {savepoint}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    counts["rejected_samples"] = rejected_samples
    return counts
