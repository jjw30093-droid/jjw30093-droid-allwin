"""质量门(数据管道重建:每条规则配一个可执行检查)。

链上最后一步(DEFAULT_CHAIN 尾,失败不 cascade 任何真活):对三库做只读体检,
违反的门通过 backend.notify 发告警(先落 pipeline_alerts 再推,配额/去重由
notify 统一管理)。门的"发现问题"不等于本任务失败——任务失败语义留给
"检查本身跑不动"(崩溃),避免质量门 CRITICAL 又触发一条 pipeline_step_failure
的重复告警。

门清单(与计划一致;每门的判据在各 _gate_* 函数 docstring):
  G1  fixtures_window_empty       赛季期内 7 天窗口 0 场            CRITICAL
  G2  league_coverage_regression  最近一次赛程同步被反退化门禁拒写   CRITICAL
  G3  kickoff_precision           7 天窗口内非 exact kickoff > 0    WARNING
  G4  entity_resolution_degraded  逐联赛 pollable/in_window          <60% WARN / ==0 CRITICAL
  G5  odds_coverage               未来 24h 有 pre_match 快照占比      <50% WARNING
  G6  closing_coverage            完赛场 T-15min 内收盘快照占比       <80% WARN / <70% CRITICAL
  G7  score_regression            NotStarted 却带比分(清列泄漏兜底)  >0 CRITICAL
  G8  company_scope               近 24h 出现目标外公司 cid          CRITICAL
  G9  source_waf_blocked          近 1h source_health 命中 WAF       CRITICAL
  G10 box_shot_geometry_drift     坐标法禁区内射门数 vs 官方计数漂移  >5% WARNING
  G11 xref_unmapped_upcoming      168h 内不可采比赛(全局聚合,非按联赛)
                                   距开球≤48h 仍不可采 CRITICAL / 更远 WARNING
  G12 season_label_drift          Season 与制度表推导不一致:超出 878 存量基线
                                   的新增/未登记联赛行 CRITICAL;features 交叉
                                   漂移新增 WARNING(CLAUDE.md §6.3)
  G13 unknown_enum_value          enum 型列出现 known_values.py 登记外取值 WARNING
  G14 extra_json_unknown_key      球队统计 extra_json 出现白名单外新键 WARNING
  G15 fixture_round_gap           数字轮次出现整轮空缺(中途接入联赛的历史场次
                                   漏采,CLAUDE.md §6.3)                CRITICAL

数据不足时(联赛未同步过、窗口内场次太少、尚无完赛样本)如实记 skipped,
不猜、不误报——前身项目教训:季外联赛误报会让告警在两周内被当成噪音关掉。

用法:
  python -m backend.cli.pipeline_gates            # 文本输出,退出码 0/1/2
  python -m backend.cli.pipeline_gates --json
  python -m backend.cli.pipeline_gates --no-notify  # 只检查不发告警(手工排查)
"""

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone

from backend.cli.poll_nowgoal import DISCOVERY_WINDOW_HOURS
from backend.db.connections import connect_ro
from backend.db.util import utc_now_iso
from backend.ingest.poll_windows import upcoming_precise_matches
from backend.providers.nowgoal import DEFAULT_TARGET_CIDS
from backend.queries.leagues import LEAGUE_META

OK, WARNING, CRITICAL = "OK", "WARNING", "CRITICAL"
_RANK = {OK: 0, WARNING: 1, CRITICAL: 2}

POLLABLE_STATUSES = ("auto_ok", "confirmed")
CLOSING_WINDOW_SECONDS = 900         # T-15min 收盘覆盖判据
G4_MIN_MATCHES = 3                   # 窗口内 ≥3 场才判实体解析率
G5_MIN_MATCHES = 3
G6_MIN_MATCHES = 5
G11_NEAR_HOURS = 48                  # 距开球 ≤48h 仍不可采 → CRITICAL,其余 → WARNING

# G10:标准 FIFA 禁区几何(与 backend/queries/matchup.py 的 _BOX_X_MIN/
# _BOX_Y_MIN/_BOX_Y_MAX 同一套常量),已用 25,984 个队场样本对官方
# shots_inside_box 计数校验:完全相等 97.97%(2026-08-23)。这里只对近期
# 有坐标数据的队场重跑同一校验,坐标系一旦漂移(采集端换了坐标约定、
# 球场朝向反了)能在质量门里被抓到,不必等用户发现禁区内 xG 数字离谱。
G10_BOX_X_MIN = 88.5
G10_BOX_Y_MIN = 13.84
G10_BOX_Y_MAX = 54.16
G10_MIN_TEAM_MATCHES = 20            # 少于这个样本数不判(coord/official 都可能是空联赛窗口的噪音)
# 全量历史基线是 97.97% 完全相等,即稳态下约 2.03% 队场天然不相等(坐标
# 缺失行、压线球四舍五入)——这不是异常,是这套坐标法本身的已知误差率。
# 2026-08-23 部署当天用生产近 30 天窗口实测复核:370 队场、2.16% 不相等,
# 与全量基线一致,证明这就是正常波动。阈值定得比这更贴近(比如误设成 1%,
# 我曾经这样设过)会让健康状态天天报 WARNING,两周内被当噪音关掉——门槛
# 定为基线的约 2.5 倍(5%),只在真正的坐标系漂移(球场朝向反了/换了坐标
# 约定,那种漂移会把不相等率推到远高于个位数)时才触发。
G10_MAX_MISMATCH_RATE = 0.05

# G15:整轮消失的判据(2026-08-27,巴甲 268 缺 215/380 场事故——赛程同步只写
# 未完赛行,中途接入的联赛历史已完赛场次永久漏采,见
# backend/cli/backfill_fixtures.py 头注释)。行数/最大轮次太小时数据本身还
# 不足以判断"轮次消失"还是"赛季刚开始",跳过不误报。
G15_MIN_ROWS = 20
G15_MIN_MAX_ROUND = 5
# 已知例外(联赛真实存在结构性轮次缺口,不是数据丢失):今天为空,保留位置
# 供将来发现真实赛制特例时登记,不要把这当成放宽阈值的入口。
G15_EXEMPT: set[tuple[int, str]] = set()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _league_name(lid: int) -> str:
    meta = LEAGUE_META.get(lid)
    return f"{meta['name_zh']}({lid})" if meta else str(lid)


def _latest_ledger_by_league(conn_odds) -> dict[int, dict]:
    rows = conn_odds.execute(
        """SELECT l.* FROM fixture_sync_ledger l
           JOIN (SELECT league_id, MAX(id) AS max_id FROM fixture_sync_ledger
                 GROUP BY league_id) t ON t.max_id = l.id"""
    ).fetchall()
    return {int(r["league_id"]): dict(r) for r in rows}


# ── 各门 ─────────────────────────────────────────────────────────────


def _gate_fixtures_window(conn_core, ledger_by_league, now_iso) -> dict:
    """G1:最近一次同步 verdict='written' 且 horizon7_rows>0 的联赛,core 里
    7 天窗口内却查不到 NotStarted 场次 → 写入声明与真实数据不一致,CRITICAL。
    未同步过 / off_season 的联赛如实 skipped,不误报。"""
    hi = _iso(_parse_iso(now_iso) + timedelta(days=7))
    violations, skipped = [], []
    for lid in LEAGUE_META:
        ledger = ledger_by_league.get(lid)
        if ledger is None:
            skipped.append({"league_id": lid, "reason": "not_synced_yet"})
            continue
        if ledger["verdict"] != "written" or not ledger["horizon7_rows"]:
            skipped.append({"league_id": lid, "reason": f"ledger_{ledger['verdict']}"})
            continue
        n = conn_core.execute(
            """SELECT COUNT(*) FROM dim_match
               WHERE League_ID=? AND status='NotStarted'
                 AND julianday(COALESCE(kickoff_at_utc, Date)) > julianday(?)
                 AND julianday(COALESCE(kickoff_at_utc, Date)) <= julianday(?)""",
            (lid, now_iso, hi),
        ).fetchone()[0]
        if n == 0:
            violations.append({"league_id": lid, "ledger_horizon7": ledger["horizon7_rows"]})
    level = CRITICAL if violations else OK
    return {"gate": "fixtures_window_empty", "level": level,
            "violations": violations, "skipped_leagues": len(skipped)}


def _gate_coverage_regression(ledger_by_league) -> dict:
    """G2:最近一次同步被 G-A/G-B/G-C 门禁拒写(refused_regression/refused_downgrade)
    ——写入端已经保护了旧数据,这里负责让人知道"有联赛的新数据被拒了"。"""
    violations = [
        {"league_id": lid, "verdict": ledger["verdict"],
         "detail": (ledger.get("detail") or "")[:120]}
        for lid, ledger in ledger_by_league.items()
        if ledger["verdict"] in ("refused_regression", "refused_downgrade")
    ]
    return {"gate": "league_coverage_regression",
            "level": CRITICAL if violations else OK, "violations": violations}


def _gate_kickoff_precision(conn_core, now_iso) -> dict:
    """G3:7 天窗口内非 exact kickoff 的 NotStarted 行数——这些行被
    upcoming_precise_matches 静默排除出轮询,不数出来等于悄悄少抓(事故 #3)。"""
    hi = _iso(_parse_iso(now_iso) + timedelta(days=7))
    rows = conn_core.execute(
        """SELECT League_ID, COUNT(*) AS n FROM dim_match
           WHERE status='NotStarted'
             AND julianday(COALESCE(kickoff_at_utc, Date)) > julianday(?)
             AND julianday(COALESCE(kickoff_at_utc, Date)) <= julianday(?)
             AND (kickoff_precision IS NULL OR kickoff_precision <> 'exact')
           GROUP BY League_ID""",
        (now_iso, hi),
    ).fetchall()
    by_league = {int(r["League_ID"]): r["n"] for r in rows}
    total = sum(by_league.values())
    return {"gate": "kickoff_precision", "level": WARNING if total else OK,
            "non_exact_in_window": total, "by_league": by_league}


def _gate_entity_resolution(conn_core, conn_odds, now_iso) -> dict:
    """G4:逐联赛 pollable/in_window(72h 窗口 ≥3 场才判)。==0 → 整个联赛
    零赔率,CRITICAL 并点名;<60% → WARNING。"""
    candidates = upcoming_precise_matches(conn_core, now_iso)
    by_league: dict[int, list[int]] = {}
    for c in candidates:
        by_league.setdefault(int(c["League_ID"]), []).append(int(c["Match_ID"]))
    pollable_ids = {
        int(r["fotmob_match_id"])
        for r in conn_odds.execute(
            "SELECT fotmob_match_id FROM dim_match_xref "
            f"WHERE provider='nowgoal' AND review_status IN ({','.join('?' for _ in POLLABLE_STATUSES)})",
            POLLABLE_STATUSES,
        )
    }
    worst, entries = OK, []
    for lid, mids in sorted(by_league.items()):
        if len(mids) < G4_MIN_MATCHES:
            continue
        pollable = sum(1 for m in mids if m in pollable_ids)
        ratio = pollable / len(mids)
        level = OK
        if pollable == 0:
            level = CRITICAL
        elif ratio < 0.6:
            level = WARNING
        if level != OK:
            entries.append({"league_id": lid, "league": _league_name(lid),
                            "in_window": len(mids), "pollable": pollable,
                            "ratio": round(ratio, 3), "level": level})
        if _RANK[level] > _RANK[worst]:
            worst = level
    return {"gate": "entity_resolution_degraded", "level": worst, "violations": entries}


def _gate_xref_unmapped_upcoming(conn_core, conn_odds, now_iso) -> dict:
    """G11:全局聚合版"还有多少快开球的比赛抓不到赔率"(2026-08-24 新增,修复
    36 场比赛零赔率事故暴露的 G4 三个结构性盲点)——

    - **窗口对齐 168h**(`poll_nowgoal.DISCOVERY_WINDOW_HOURS`),不是 G4 的 72h
      默认窗口:轮询器在 168h 内就会发现候选并尝试映射,72h 窗口看不到这部分;
    - **全局聚合,不按联赛拆分**:那次事故是 36 场摊在 16 个联赛,每联赛约 2 场,
      被 G4_MIN_MATCHES=3 逐个跳过,全局合计的严重程度反而被拆没了;
    - **距开球 ≤48h 仍不可采直接 CRITICAL**:WARNING 会被 notify 的
      WARNING_DAILY_MAX=2 + 24h 去重压掉,等价于没有告警——这正是那次事故里
      resolve_entities.py 每天算出数字却没人看到的重演。48h 之外的尾部仍值得
      记录,降级为 WARNING。
    """
    candidates = upcoming_precise_matches(conn_core, now_iso, window_hours=DISCOVERY_WINDOW_HOURS)
    if not candidates:
        return {"gate": "xref_unmapped_upcoming", "level": OK, "detail": "no_candidates"}
    pollable_ids = {
        int(r["fotmob_match_id"])
        for r in conn_odds.execute(
            "SELECT fotmob_match_id FROM dim_match_xref "
            f"WHERE provider='nowgoal' AND review_status IN ({','.join('?' for _ in POLLABLE_STATUSES)})",
            POLLABLE_STATUSES,
        )
    }
    now_dt = _parse_iso(now_iso)
    near, far = [], []
    for c in candidates:
        mid = int(c["Match_ID"])
        if mid in pollable_ids:
            continue
        hours_to_kickoff = (_parse_iso(c["kickoff_at_utc"]) - now_dt).total_seconds() / 3600
        entry = {"match_id": mid, "league_id": int(c["League_ID"]),
                 "league": _league_name(int(c["League_ID"])),
                 "hours_to_kickoff": round(hours_to_kickoff, 1)}
        (near if hours_to_kickoff <= G11_NEAR_HOURS else far).append(entry)

    if near:
        level = CRITICAL
    elif far:
        level = WARNING
    else:
        level = OK
    samples = sorted(near or far, key=lambda e: e["hours_to_kickoff"])[:10]
    return {"gate": "xref_unmapped_upcoming", "level": level,
            "near_48h_unpollable": len(near), "far_unpollable": len(far), "samples": samples}


def _xref_pollable_map(conn_odds) -> dict[int, str]:
    """fotmob_match_id → provider_match_id(仅 pollable 状态)。"""
    return {
        int(r["fotmob_match_id"]): str(r["provider_match_id"])
        for r in conn_odds.execute(
            "SELECT fotmob_match_id, provider_match_id FROM dim_match_xref "
            f"WHERE provider='nowgoal' AND review_status IN ({','.join('?' for _ in POLLABLE_STATUSES)})",
            POLLABLE_STATUSES,
        )
    }


def _gate_odds_coverage(conn_core, conn_odds, now_iso) -> dict:
    """G5:未来 24h 内(精确 kickoff)比赛有 ≥1 条 pre_match 快照的占比;
    <50% → WARNING。样本 <3 场时 skipped(不足以判定)。"""
    candidates = [
        c for c in upcoming_precise_matches(conn_core, now_iso, window_hours=24)
    ]
    if len(candidates) < G5_MIN_MATCHES:
        return {"gate": "odds_coverage", "level": OK, "skipped": True,
                "matches_in_24h": len(candidates)}
    xref = _xref_pollable_map(conn_odds)
    covered = 0
    for c in candidates:
        pid = xref.get(int(c["Match_ID"]))
        if pid is None:
            continue
        row = conn_odds.execute(
            "SELECT 1 FROM bronze_ng_odds_snap WHERE provider_match_id=? "
            "AND market_phase='pre_match' LIMIT 1", (pid,),
        ).fetchone()
        if row:
            covered += 1
    ratio = covered / len(candidates)
    return {"gate": "odds_coverage", "level": WARNING if ratio < 0.5 else OK,
            "matches_in_24h": len(candidates), "covered": covered,
            "ratio": round(ratio, 3)}


def _gate_closing_coverage(conn_core, conn_odds, now_iso) -> dict:
    """G6:近 48h 完赛且曾有 pre_match 快照的比赛中,存在"距开球 ≤15min 的
    pre_match 快照"的占比;<80% WARN、<70% CRITICAL;同时报
    kickoff − FINAL.observed_at 的中位数。样本 <5 场 skipped。"""
    lo = _iso(_parse_iso(now_iso) - timedelta(hours=48))
    finished = conn_core.execute(
        """SELECT Match_ID, kickoff_at_utc FROM dim_match
           WHERE status='Finish' AND kickoff_precision='exact'
             AND kickoff_at_utc IS NOT NULL
             AND julianday(kickoff_at_utc) >= julianday(?)
             AND julianday(kickoff_at_utc) <= julianday(?)""",
        (lo, now_iso),
    ).fetchall()
    xref = _xref_pollable_map(conn_odds)
    sampled, closed, gaps = 0, 0, []
    for m in finished:
        pid = xref.get(int(m["Match_ID"]))
        if pid is None:
            continue
        row = conn_odds.execute(
            """SELECT MAX(observed_at) AS final_at FROM bronze_ng_odds_snap
               WHERE provider_match_id=? AND market_phase='pre_match'
                 AND julianday(observed_at) < julianday(?)""",
            (pid, m["kickoff_at_utc"]),
        ).fetchone()
        if not row or not row["final_at"]:
            continue      # 从未有过赛前快照的场次不计入收盘覆盖分母
        sampled += 1
        gap = (_parse_iso(m["kickoff_at_utc"]) - _parse_iso(row["final_at"])).total_seconds()
        gaps.append(gap)
        if gap <= CLOSING_WINDOW_SECONDS:
            closed += 1
    if sampled < G6_MIN_MATCHES:
        return {"gate": "closing_coverage", "level": OK, "skipped": True,
                "sampled": sampled}
    ratio = closed / sampled
    level = OK
    if ratio < 0.7:
        level = CRITICAL
    elif ratio < 0.8:
        level = WARNING
    return {"gate": "closing_coverage", "level": level, "sampled": sampled,
            "closed_within_15min": closed, "ratio": round(ratio, 3),
            "median_gap_seconds": round(statistics.median(gaps), 1)}


def _gate_score_regression(conn_core) -> dict:
    """G7:status='NotStarted' 却有非空比分——INSERT OR REPLACE 清列/降级泄漏的
    兜底探测(写入端 G-B/G-C 已拒绝,这里是纵深防御)。"""
    n = conn_core.execute(
        "SELECT COUNT(*) FROM dim_match WHERE status='NotStarted' "
        "AND (home_score IS NOT NULL OR away_score IS NOT NULL)"
    ).fetchone()[0]
    return {"gate": "score_regression", "level": CRITICAL if n else OK,
            "notstarted_with_score": n}


def _gate_company_scope(conn_odds, now_iso) -> dict:
    """G8:近 24h 快照出现目标三家(Bet365/澳门/皇冠)之外的 cid——
    "静默换公司回退又回来了"的可执行证据。"""
    lo = _iso(_parse_iso(now_iso) - timedelta(hours=24))
    cids = {
        str(r["company_id"])
        for r in conn_odds.execute(
            "SELECT DISTINCT company_id FROM bronze_ng_odds_snap "
            "WHERE julianday(observed_at) >= julianday(?)", (lo,),
        )
    }
    extras = sorted(cids - set(DEFAULT_TARGET_CIDS))
    return {"gate": "company_scope", "level": CRITICAL if extras else OK,
            "allowed": list(DEFAULT_TARGET_CIDS), "unexpected_cids": extras}


def _gate_source_waf(conn_odds, now_iso) -> dict:
    """G9:近 1h source_health 命中 WAF(错误摘要含 WAFBlockedError/WAF 拦截)。"""
    lo = _iso(_parse_iso(now_iso) - timedelta(hours=1))
    n = conn_odds.execute(
        """SELECT COUNT(*) FROM source_health
           WHERE ok=0 AND julianday(checked_at) >= julianday(?)
             AND (error_summary LIKE '%WAFBlocked%' OR error_summary LIKE '%WAF 拦截%')""",
        (lo,),
    ).fetchone()[0]
    return {"gate": "source_waf_blocked", "level": CRITICAL if n else OK,
            "waf_hits_last_hour": n}


def _gate_box_shot_geometry(conn_core, now_iso) -> dict:
    """G10:近 30 天内被重新采集过(fact_shotmap 有坐标)的完赛队场,用坐标法
    (标准 FIFA 禁区几何)聚合出的禁区内射门数,与官方
    fact_team_match_stats.extra_json.shots_inside_box 计数比对——两者本该
    在绝大多数队场上完全相等(基线 97.97%,见 backend/queries/matchup.py
    模块 docstring)。样本不足 G10_MIN_TEAM_MATCHES 时判 skipped,不在小
    样本上误报。"""
    lo = _iso(_parse_iso(now_iso) - timedelta(days=30))
    try:
        conn_core.execute("SELECT 1 FROM fact_shotmap LIMIT 1")
        conn_core.execute("SELECT 1 FROM fact_team_match_stats LIMIT 1")
    except Exception:  # noqa: BLE001 — 表尚不存在(测试用只跑过 migration 的空库),如实 skipped
        return {"gate": "box_shot_geometry_drift", "level": OK, "detail": "skipped_no_table",
                "team_matches": 0, "min_required": G10_MIN_TEAM_MATCHES}
    rows = conn_core.execute(
        f"""
        WITH recent AS (
          SELECT Match_ID FROM dim_match
           WHERE status='Finish' AND COALESCE(kickoff_at_utc, Date) >= ?
        ),
        coord AS (
          SELECT s.Match_ID, s.Team_ID,
                 COUNT(*) coord_n
            FROM fact_shotmap s JOIN recent r ON r.Match_ID = s.Match_ID
           WHERE s.X_Coord IS NOT NULL AND s.Y_Coord IS NOT NULL
             AND s.X_Coord >= {G10_BOX_X_MIN}
             AND s.Y_Coord BETWEEN {G10_BOX_Y_MIN} AND {G10_BOX_Y_MAX}
           GROUP BY s.Match_ID, s.Team_ID
        ),
        official AS (
          SELECT Match_ID, Team_ID,
                 CAST(json_extract(extra_json, '$.shots_inside_box') AS REAL) off_n
            FROM fact_team_match_stats
           WHERE Period='All' AND Match_ID IN (SELECT Match_ID FROM recent)
        )
        SELECT c.coord_n, o.off_n
          FROM coord c JOIN official o
            ON o.Match_ID = c.Match_ID AND o.Team_ID = c.Team_ID
         WHERE o.off_n IS NOT NULL
        """,
        (lo,),
    ).fetchall()
    n = len(rows)
    if n < G10_MIN_TEAM_MATCHES:
        return {"gate": "box_shot_geometry_drift", "level": OK, "detail": "skipped_insufficient_sample",
                "team_matches": n, "min_required": G10_MIN_TEAM_MATCHES}
    mismatched = sum(1 for r in rows if r["coord_n"] != r["off_n"])
    rate = mismatched / n
    return {
        "gate": "box_shot_geometry_drift",
        "level": WARNING if rate > G10_MAX_MISMATCH_RATE else OK,
        "team_matches": n, "mismatched": mismatched,
        "mismatch_rate": round(rate, 4), "threshold": G10_MAX_MISMATCH_RATE,
    }


# G12 基线(2026-08-25 上线时生产实测,SQL 与门内推导完全一致):
#   season_drift=878(2026-08-25 事故的全部存量,站长决定"只报不改",清单见
#   backend/cli/season_audit.py)、features_drift=77(int_match_features 与
#   dim_match 的赛季不一致,且已证实是 dim_match 侧错)。门只对**超出基线的
#   新增**告警——上线当天就按存量刷 CRITICAL 会立刻把告警变噪音(G10 注释
#   与模块 docstring 的既有教训)。存量清零后应把基线改回 0。
G12_BASELINE_SEASON_DRIFT = 878
G12_BASELINE_FEATURES_DRIFT = 77


def _gate_season_label_drift(conn_core) -> dict:
    """G12:dim_match.Season 与制度表推导不一致的行数(CLAUDE.md §6.3)。

    0011 触发器挡新写入;本门是纵深防御——盯"触发器被绕过/未部署/制度表被
    改坏"造成的**新增**漂移,以及未登记联赛的行。制度表缺失(migration 未
    应用)时如实 skipped。
    """
    from backend.season_regime import derived_season_sql

    try:
        drift = conn_core.execute(
            "SELECT COUNT(*) FROM dim_match m"
            " WHERE m.Season IS NOT NULL AND m.Date IS NOT NULL"
            f" AND m.Season <> {derived_season_sql('m.Date', 'm.League_ID')}"
        ).fetchone()[0]
        unregistered = conn_core.execute(
            "SELECT COUNT(*) FROM dim_match m WHERE m.League_ID IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM dim_league_season_regime r"
            "                  WHERE r.league_id = m.League_ID)"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return {"gate": "season_label_drift", "level": OK,
                "detail": "skipped_regime_table_missing"}
    try:
        features_drift = conn_core.execute(
            "SELECT COUNT(*) FROM int_match_features f"
            " JOIN dim_match m ON m.Match_ID = f.match_id"
            " WHERE f.season IS NOT NULL AND m.Season IS NOT NULL"
            "   AND f.season <> m.Season"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        features_drift = None
    new_drift = max(0, drift - G12_BASELINE_SEASON_DRIFT)
    new_features = (
        max(0, features_drift - G12_BASELINE_FEATURES_DRIFT)
        if features_drift is not None else 0
    )
    level = OK
    if new_features > 0:
        level = WARNING
    if new_drift > 0 or unregistered > 0:
        level = CRITICAL
    return {
        "gate": "season_label_drift", "level": level,
        "season_drift": drift, "baseline": G12_BASELINE_SEASON_DRIFT,
        "new_drift": new_drift, "unregistered_league_rows": unregistered,
        "features_drift": features_drift,
        "features_baseline": G12_BASELINE_FEATURES_DRIFT,
    }


def _gate_unknown_enum_value(conn_core) -> dict:
    """G13:enum 型列出现登记外取值(backend/known_values.py 是唯一登记处)。

    只告警不拒写(来源新增枚举不该变成采集事故);表/列在当前库不存在时
    如实跳过该项。这一门补的是 FotMob `Unknown` 枚举成员的"被看见"半边
    ——DTO 层继续用 str 保证"不崩"(schemas.py:851 的既有立场不变)。
    """
    from backend.known_values import ENUM_REGISTRY

    unknown: dict[str, list] = {}
    checked = 0
    for table, column, is_known in ENUM_REGISTRY:
        try:
            rows = conn_core.execute(
                f'SELECT DISTINCT "{column}" FROM {table}'
                f' WHERE "{column}" IS NOT NULL'
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        checked += 1
        bad = sorted(str(r[0]) for r in rows if not is_known(r[0]))
        if bad:
            unknown[f"{table}.{column}"] = bad[:10]
    return {
        "gate": "unknown_enum_value",
        "level": WARNING if unknown else OK,
        "columns_checked": checked, "unknown": unknown,
    }


G14_WINDOW_DAYS = 90  # 只扫近 90 天的比赛:控制 json_each 展开成本,又足以在新键到达后 3 个月内持续可见


def _gate_extra_json_unknown_key(conn_core, now_iso) -> dict:
    """G14:fact_team_match_stats.extra_json 出现白名单外的新键。

    正是这套告警此前缺失,让球队级 physical_metrics_* 在库里躺了数月、任何
    读路径都看不见(match_report.TEAM_STAT_KEYS 是读取侧唯一投影,来源新增
    键"不会自动出现在响应里"是该白名单文档写明的行为——本门把"也没人知道
    它来了"这半个问题补上)。白名单 = TEAM_STAT_KEYS ∪ 已知未投影集合
    (backend/known_values.py)。
    """
    from backend.known_values import TEAM_EXTRA_JSON_KNOWN_UNPROJECTED
    from backend.queries.match_report import TEAM_STAT_KEYS

    lo = _iso(_parse_iso(now_iso) - timedelta(days=G14_WINDOW_DAYS))[:10]
    allowed = set(TEAM_STAT_KEYS) | set(TEAM_EXTRA_JSON_KNOWN_UNPROJECTED)
    try:
        rows = conn_core.execute(
            "SELECT DISTINCT j.key FROM fact_team_match_stats t"
            " JOIN dim_match m ON m.Match_ID = t.Match_ID, json_each(t.extra_json) j"
            " WHERE t.Period='All' AND m.Date >= ?",
            (lo,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"gate": "extra_json_unknown_key", "level": OK,
                "detail": "skipped_table_missing"}
    unknown = sorted(r[0] for r in rows if r[0] not in allowed)
    return {
        "gate": "extra_json_unknown_key",
        "level": WARNING if unknown else OK,
        "window_days": G14_WINDOW_DAYS, "unknown_keys": unknown[:20],
        "unknown_count": len(unknown),
    }


def _gate_fixture_round_gap(conn_core) -> dict:
    """G15:按 (League_ID, Season) 取数字轮次(GLOB '[0-9]*',排除 final/bronze/
    1-4 这类季后赛/分组制标签),expected=1..max(present)。任何整轮为空即
    CRITICAL——不设 WARNING 分支,因为 max_round 本身来自 present 集合,
    "缺最后一轮"在构造上不可能发生,内部整轮为空只可能是真实数据丢失。

    只遍历 LEAGUE_META(与 fixtures_window_empty 等其它门同一惯例)——孤儿
    联赛(不在 LEAGUE_META 的旧联赛,如 86/110/140/146)天然被排除,不需要
    第二份黑名单,详见 backend/cli/backfill_fixtures.py 头注释。

    LEAGUE_META 17 个生产联赛全量校准过(2026-08-27):268/57/61 命中真实缺口,其余全部
    缺 0 轮,含非数字轮次的日职联(223)/澳超(113)/分组制韩K联(9080)零误报。
    "轮次不满员"(数量少于该赛季众数)被明确否决过——澳超季后赛赛制会让
    27/28 轮判定为"不满员",做成信号会在两周内被当噪音关掉(同 G10 注释里
    记过的教训)。
    """
    rows = conn_core.execute(
        """SELECT League_ID, Season, Match_Round FROM dim_match
           WHERE League_ID IN ({}) AND Match_Round GLOB '[0-9]*'""".format(
            ",".join(str(lid) for lid in LEAGUE_META)
        )
    ).fetchall()
    by_key: dict[tuple[int, str], set[int]] = {}
    for r in rows:
        # GLOB '[0-9]*' 只挡开头,"1/4"/"1-2"这类混合标签开头是数字仍会漏进来,
        # 必须严格 isdigit() 才当真正的数字轮次(否则 int() 直接崩,被外层
        # 通用 gate_error 兜底吞成误导性的 WARNING,而不是这里该有的 OK/CRITICAL)。
        if not r["Match_Round"].isdigit():
            continue
        by_key.setdefault((int(r["League_ID"]), r["Season"]), set()).add(int(r["Match_Round"]))

    violations = []
    for (lid, season), rounds in sorted(by_key.items()):
        if (lid, season) in G15_EXEMPT:
            continue
        if len(rounds) < G15_MIN_ROWS:
            continue
        max_round = max(rounds)
        if max_round < G15_MIN_MAX_ROUND:
            continue
        missing = sorted(set(range(1, max_round + 1)) - rounds)
        if missing:
            violations.append({
                "league_id": lid, "league": _league_name(lid), "season": season,
                "max_round": max_round, "rows_present": len(rounds),
                "missing_rounds": missing[:15], "missing_count": len(missing),
            })
    return {"gate": "fixture_round_gap", "level": CRITICAL if violations else OK,
            "violations": violations}


# ── 汇总与告警 ───────────────────────────────────────────────────────

# 门 → notify 的 source(P0 白名单来源见 backend/notify.P0_ALERT_SOURCES;
# 不在白名单的门用自己的 gate 名作 source,级别以门为准)。
_GATE_ALERT_SOURCE = {
    "fixtures_window_empty": "fixtures_window_empty",
    "league_coverage_regression": "league_coverage_regression",
    "entity_resolution_degraded": "entity_resolution_degraded",
    "source_waf_blocked": "source_waf_blocked",
    "kickoff_precision": "kickoff_precision",
    "odds_coverage": "odds_coverage",
    "closing_coverage": "closing_coverage",
    "score_regression": "score_regression",
    "company_scope": "company_scope",
    "box_shot_geometry_drift": "box_shot_geometry_drift",
    "xref_unmapped_upcoming": "xref_unmapped_upcoming",
    "season_label_drift": "season_label_drift",
    "unknown_enum_value": "unknown_enum_value",
    "extra_json_unknown_key": "extra_json_unknown_key",
    "fixture_round_gap": "fixture_round_gap",
}


def run(now_iso: str | None = None, notify_alerts: bool = True) -> dict:
    now = now_iso or utc_now_iso()
    conn_core = connect_ro("core")
    conn_odds = connect_ro("odds")
    gates: list[dict] = []
    try:
        ledger = _latest_ledger_by_league(conn_odds)
        checks = (
            ("fixtures_window_empty", lambda: _gate_fixtures_window(conn_core, ledger, now)),
            ("league_coverage_regression", lambda: _gate_coverage_regression(ledger)),
            ("kickoff_precision", lambda: _gate_kickoff_precision(conn_core, now)),
            ("entity_resolution_degraded", lambda: _gate_entity_resolution(conn_core, conn_odds, now)),
            ("xref_unmapped_upcoming", lambda: _gate_xref_unmapped_upcoming(conn_core, conn_odds, now)),
            ("odds_coverage", lambda: _gate_odds_coverage(conn_core, conn_odds, now)),
            ("closing_coverage", lambda: _gate_closing_coverage(conn_core, conn_odds, now)),
            ("score_regression", lambda: _gate_score_regression(conn_core)),
            ("company_scope", lambda: _gate_company_scope(conn_odds, now)),
            ("source_waf_blocked", lambda: _gate_source_waf(conn_odds, now)),
            ("box_shot_geometry_drift", lambda: _gate_box_shot_geometry(conn_core, now)),
            ("season_label_drift", lambda: _gate_season_label_drift(conn_core)),
            ("unknown_enum_value", lambda: _gate_unknown_enum_value(conn_core)),
            ("extra_json_unknown_key", lambda: _gate_extra_json_unknown_key(conn_core, now)),
            ("fixture_round_gap", lambda: _gate_fixture_round_gap(conn_core)),
        )
        for gate_name, check in checks:
            try:
                gates.append(check())
            except Exception as exc:  # noqa: BLE001 — 单门崩溃如实报 gate_error,不掩盖
                gates.append({"gate": gate_name, "level": WARNING,
                              "detail": "gate_error", "error": f"{type(exc).__name__}"})
    finally:
        conn_core.close()
        conn_odds.close()

    overall = OK
    for g in gates:
        if _RANK[g["level"]] > _RANK[overall]:
            overall = g["level"]

    if notify_alerts:
        from backend import notify as notify_mod

        for g in gates:
            if g["level"] == OK:
                continue
            source = _GATE_ALERT_SOURCE.get(g["gate"], g["gate"])
            body = json.dumps({k: v for k, v in g.items() if k != "gate"},
                              ensure_ascii=False, default=str)[:1500]
            g["alert"] = notify_mod.notify(
                level=g["level"], source=source,
                title=f"质量门 {g['gate']} {g['level']}",
                body=body, dedup_key=f"gate:{g['gate']}", now_iso=now,
            )

    return {"checked_at": now, "level": overall, "gates": gates}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="质量门检查(只读;违反的门经 notify 发告警)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-notify", action="store_true", help="只检查不发告警(手工排查)")
    ap.add_argument("--now", default=None)
    args = ap.parse_args(argv)

    report = run(now_iso=args.now, notify_alerts=not args.no_notify)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"pipeline_gates: {report['level']} @ {report['checked_at']}")
        for g in report["gates"]:
            mark = "  " if g["level"] == OK else "! "
            print(f"{mark}{g['gate']}: {g['level']}")
    return _RANK[report["level"]]


if __name__ == "__main__":
    sys.exit(main())
