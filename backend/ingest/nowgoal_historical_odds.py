"""把 `nowgoal_archive.all_points_from_mix_history()`/`all_points_from_euro_history()`
拿到的完整历史变化序列,转成可以直接写 `bronze_ng_odds_snap` 的记录,并落库。

跟 `backend/cli/ingest_nowgoal_historical_odds.py`(消费一次性 AWS 回填产物的
JSONL shard,那批 shard 已经找不到了,2026-09-22 核实 runtime/research/ 下
不存在)是两条不同来源,但**刻意复用同一份"归一到 canonical 主客视角"的
`normalize_for_inversion()`**(来自 `backend/providers/nowgoal.py`,和生产
实时轮询同一个实现),不允许出现第二套 AH 反转规则各写各的、慢慢漂移。

跟生产实时轮询(`backend/ingest/odds_snapshots.py::ingest_odds_records()`)的
关键差异——为什么这里不直接调那个函数,见该函数写入的三个字段语义:
- `source_updated_at`:生产实时轮询恒 NULL(NowGoal 不声明单次变化的来源时间)。
  这里不是 NULL——archive 的 mix_history/euro_history 本身带着每个变化点的
  来源时间戳(`mt`/`TimeShow`),理应写进这个字段,不该因为写入路径不同就
  丢掉这份信息。
- `observed_at`:生产实时轮询 = 我们自己轮询到这次变化的真实时刻。这里刻意
  设成跟 `source_updated_at` 相同的历史时间戳,不是"现在"(回填任务实际
  运行的时刻)——CLAUDE.md §6.2 禁止的是编造一个并不真正知道的时间,这里
  是来源真实声明的时间,用"现在"反而会把整条历史轨迹的时间信息全部抹平,
  是伪装,不是老实。
- `ingested_at`:如实是回填任务真正运行的时刻。
"""

from __future__ import annotations

import sqlite3

from backend.db.util import sha256_hex, utc_now_iso
from backend.providers.nowgoal import canonical_payload_json, normalize_for_inversion

COMPANY_NAMES = {"8": "Bet365", "3": "Crown", "1": "Macauslot"}

# resolve_and_gate() 的 evidence_kind 取值 -> confidence,与
# backend/cli/ingest_nowgoal_historical_odds.py::_EVIDENCE_CONFIDENCE 同一套
# 取值,不另起一套标准。resolve_and_gate 比那边多一层"精确 kickoff 门禁"
# (见 ingest_nowgoal_season_odds.py::resolve_and_gate 文档),没有理由给更低
# 的置信度,所以直接复用同一张表,不因为来源模块不同就编出不同数字。
_EVIDENCE_CONFIDENCE = {"id": 0.95, "name": 0.75}
_DEFAULT_CONFIDENCE = 0.85


def upsert_xref_from_resolution(conn_odds: sqlite3.Connection, resolved_auto_ok_rows: list[dict]) -> int:
    """把 `resolve_and_gate()` 里 `status==STATUS_AUTO_OK` 的行写进 dim_match_xref。

    2026-09-22 真实事故:`backfill_nowgoal_odds_full_history.py` 最初直接把
    resolve_and_gate() 的结果拿去抓赔率、写 bronze_ng_odds_snap,却从没有
    持久化过这份映射本身——resolve_and_gate 是纯函数,不发生任何数据库写入。
    结果是五大联赛 25/26 赛季共 1752 场比赛的赔率数据完整落库了,但
    dim_match_xref 里一行都没有,下游任何靠这张表 join 的分析代码
    (analyze.py/ah_study.py 等)完全找不到这批数据,等于白跑。

    `review_status` 统一写 'auto_ok'(resolve_and_gate 的门禁已经比一般的
    entity_resolution 更严格,不需要人工复核);`verified` 恒 0(自动解析,
    不是人工确认,不能谎称已验证,见 CLAUDE.md §6.1)。
    """
    now = utc_now_iso()
    n = 0
    for row in resolved_auto_ok_rows:
        confidence = _EVIDENCE_CONFIDENCE.get(row.get("evidence_kind"), _DEFAULT_CONFIDENCE)
        conn_odds.execute(
            """
            INSERT INTO dim_match_xref
                (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                 confidence, verified, method, kickoff_diff_seconds, review_status,
                 created_at, updated_at)
            VALUES (?, 'nowgoal', ?, ?, ?, 0, 'auto', ?, 'auto_ok', ?, ?)
            ON CONFLICT(provider, provider_match_id) DO UPDATE SET
                fotmob_match_id=excluded.fotmob_match_id,
                home_away_inverted=excluded.home_away_inverted,
                confidence=excluded.confidence,
                kickoff_diff_seconds=excluded.kickoff_diff_seconds,
                review_status=excluded.review_status,
                updated_at=excluded.updated_at
            """,
            (
                row["match_id"], row["titan_id"], int(row["direction"] == "inverted"),
                confidence, row.get("kickoff_diff_seconds"), now, now,
            ),
        )
        n += 1
    return n

_AH_FIELDS = ("home", "line", "away")
_OU_FIELDS = ("over", "line", "under")
_X12_FIELDS = ("home", "draw", "away")
_MARKET_FIELDS = {"ah": _AH_FIELDS, "ou": _OU_FIELDS, "1x2": _X12_FIELDS}


def historical_snap_records(
    ah_points: list[dict], ou_points: list[dict], x12_points: list[dict],
    *, company_id: str, inverted: bool,
) -> list[dict]:
    """把三个市场各自的完整赛前点序列,归一到 canonical 主客视角后,合并成
    `ingest_historical_odds()` 能直接消费的记录列表。

    每条记录的 payload 形状是 `{"initial": {...}, "latest": {...}}`——
    "initial" 恒等于该序列第一个点(来源标注的开盘,同样要归一),"latest"
    是这一条记录自己的值——和实时轮询落库的 payload 形状完全一致,下游
    分析代码(`analyze.py`/`ah_study.py` 等,读 payload.initial/latest)
    不用因为数据来源不同而改。
    """
    company_name = COMPANY_NAMES.get(company_id, "")
    out: list[dict] = []
    for market, points in (("ah", ah_points), ("ou", ou_points), ("1x2", x12_points)):
        if not points:
            continue
        fields = _MARKET_FIELDS[market]
        opening_raw = {k: points[0][k] for k in fields}
        opening = normalize_for_inversion({"market": market, "initial": opening_raw, "latest": None}, inverted)["initial"]
        for p in points:
            raw = {k: p[k] for k in fields}
            latest = normalize_for_inversion({"market": market, "initial": None, "latest": raw}, inverted)["latest"]
            out.append({
                "market": market, "company_id": company_id, "company_name": company_name,
                "observed_at": p["observed_at"], "initial": opening, "latest": latest,
            })
    return out


def ingest_historical_odds(
    conn_odds: sqlite3.Connection,
    provider_match_id: str,
    records: list[dict],
    poll_run_id: str,
) -> dict:
    """把 historical_snap_records() 的输出写入 bronze_ng_odds_snap。

    去重口径与 `backend/cli/ingest_nowgoal_historical_odds.py::ingest_bronze()`
    一致(存在同一个 (provider_match_id, market, company_id, observed_at) 就
    跳过,不是"跟上一条比 hash"),因为历史点本来就是无序补写、可能重复调用
    同一场比赛去补另一个市场,不能假设"上一条"就是同一批次写入的。
    """
    inserted = skipped = 0
    now = utc_now_iso()
    for record in records:
        existing = conn_odds.execute(
            """SELECT 1 FROM bronze_ng_odds_snap
               WHERE provider_match_id=? AND market=? AND company_id=? AND observed_at=?""",
            (provider_match_id, record["market"], record["company_id"], record["observed_at"]),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        payload = {"initial": record["initial"], "latest": record["latest"]}
        payload_json = canonical_payload_json(payload)
        payload_hash = sha256_hex(payload_json)
        conn_odds.execute(
            """INSERT INTO bronze_ng_odds_snap
               (provider_match_id, market, company_id, company_name, market_phase,
                payload_json, payload_hash, source_updated_at, observed_at,
                ingested_at, poll_run_id)
               VALUES (?, ?, ?, ?, 'pre_match', ?, ?, ?, ?, ?, ?)""",
            (
                provider_match_id, record["market"], record["company_id"],
                record.get("company_name", ""), payload_json, payload_hash,
                record["observed_at"], record["observed_at"], now, poll_run_id,
            ),
        )
        inserted += 1
    return {"inserted": inserted, "skipped": skipped}
