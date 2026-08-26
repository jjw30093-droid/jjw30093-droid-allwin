"""
ingest_league.py — 批量枚举 + 落库某赛季全部已完赛比赛，并回填联赛级新表
(fact_league_table、fact_season_player_stats)。

用法:
    python backend/ingest/ingest_league.py --season 2026/2027 [--league-id 47]
        [--limit N] [--only-finished/--no-only-finished]
        [--match-ids 4813735,4813736] [--sleep 2.5] [--sleep-jitter 1.5]
        [--skip-season-tables] [--force-season-tables]
        [--cooldown-window 10] [--cooldown-fail-rate 0.8] [--cooldown-seconds 90]

幂等策略:
    - 逐场调用现有 ingest_match()（其内部按 Match_ID 先删后插），整批可反复重跑。
    - 联赛级新表 fact_league_table / fact_season_player_stats 按
      (League_ID, Season) 先 DELETE 再整体 INSERT，同样可反复重跑。
    - --skip-existing 按 League_ID 过滤 dim_match 里已落库的 Match_ID；FotMob 的
      match_id 全局唯一（不同赛季不会撞号），故可安全用于多赛季回填的续跑场景。

多赛季支持:
    - --season 本身是必填参数（2026-08-25 起不再有默认值——真实事故：旧默认
      "2025/2026" 曾在一次 --match-ids 单场补采时因操作者漏带 --season 被
      静默使用，见 CLAUDE.md §6.3）。2026-08-25 二次收口后 ingest_match()/
      parse_match_dim() 已彻底删除 season 形参——Season 改为赛程同步独占的
      派生列，明细抓取（本脚本对每场比赛的调用）不再具备写它的能力；--season
      只用于本文件自己的 league_matches() 请求参数与 ingest_season_tables()
      的季级表回填，不再透传进 ingest_match()。
    - 历史赛季回填需逐季分别调用本脚本（--season 不支持一次传多个）。

可靠性 / 续爬机制:
    - PID 锁文件（/tmp/allwin_ingest_league_{league_id}.lock）：同一 league_id
      同时只允许一个实例跑，防止重复请求代理、并发写库冲突、日志互相覆盖。
    - 失败率熔断（--cooldown-window/--cooldown-fail-rate/--cooldown-seconds）：
      最近 N 场失败率过高时主动冷却，而非原速率把整季请求额度空耗在代理池坏时段。
    - ingest_match() 内置数据完整性闸门：关键字段缺失（疑似结构漂移/半页数据）
      直接拒绝写入、计入失败列表，不会静默产出残缺行。
"""

import argparse
import os
import random
import sys
import time
from collections import deque
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection
from db.util import normalize_utc_iso
from fotmob_client import FotMobClient, derive_match_status
from ingest_match import ingest_match, _insert_many, _rows_with_extra_json
from ingest_future_fixtures import upsert_fixture_row
from season_identity import verify_season_echo
from schema import LEAGUE_TABLE_CORE_COLUMNS, SEASON_PLAYER_STATS_CORE_COLUMNS


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在，只是不属于当前用户
    return True


def _acquire_lock(league_id: int) -> str:
    """
    防止同一 league_id 被两个 ingest_league.py 实例同时跑——之前真实撞过：
    忘记杀掉旧进程就起了新的，两边并发写同一份日志文件导致字节级损坏，
    grep 完全失效，排查了很久。用 PID 锁文件挡住这个场景。
    """
    lock_path = f"/tmp/allwin_ingest_league_{league_id}.lock"
    if os.path.exists(lock_path):
        try:
            old_pid = int(open(lock_path).read().strip())
        except (ValueError, OSError):
            old_pid = None
        if old_pid and _pid_alive(old_pid):
            print(f"检测到已有 ingest_league.py 在跑(league_id={league_id}, pid={old_pid})，"
                  f"为避免重复请求代理 / 并发写库冲突而拒绝启动。"
                  f"若确认是异常退出遗留的陈旧锁，删除 {lock_path} 后重试。")
            sys.exit(1)
        print(f"发现陈旧锁文件(pid={old_pid} 已不存在)，视为上次异常退出遗留，自动接管")
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    return lock_path


def _release_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except OSError:
        pass


def _existing_match_ids(league_id: int) -> set:
    """已经在 dim_match 里落库过的 Match_ID 集合（同一 League_ID 下）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT Match_ID FROM dim_match WHERE League_ID = ?", (league_id,)
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _season_tables_done(league_id: int, season: str) -> bool:
    """
    该 (League_ID, Season) 的季级表是否已落库过。用于多赛季整体重跑时跳过已完成
    赛季的 league_matches() + 37 个统计维度重复抓取（这部分只在整季结束后触发一次，
    不像逐场比赛那样有 --skip-existing 逐条把关，容易在断点重跑时被白白重做）。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM fact_league_table WHERE League_ID=? AND Season=?",
            (league_id, season),
        ).fetchone()
        return bool(row and row[0] > 0)
    finally:
        conn.close()


def _league_matches_with_retry(
    client: FotMobClient,
    league_id: int,
    season_param: str,
    attempts: int = 5,
    base_delay: float = 5.0,
) -> dict:
    """
    league_matches() 是整个批量脚本能否启动、以及季级新表能否回填的单点依赖调用，
    比逐场请求更值得多试几次再放弃——_get() 内部已有 3 次重试，但连续 3 次都撞上
    代理抖动也并非没有可能，一旦在这里失败会直接拖垮整个批次，因此再包一层独立重试。
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return client.league_matches(league_id, season_param)
        except Exception as e:
            last_err = e
            print(f"league_matches() 第 {i}/{attempts} 次尝试失败: {e}")
            if i < attempts:
                time.sleep(base_delay + random.uniform(0, base_delay))
    raise last_err


def enumerate_fixtures(client: FotMobClient, league_id: int, season: str) -> list:
    """
    枚举某赛季全部比赛，返回 [{"match_id", "round", "utc", "finished", "cancelled",
    "status", "home_id", "home_name", "away_id", "away_name"}, ...]。
    优先读 fixtures.allMatches[]（含完整 status 字段），缺失则回退
    overview.leagueOverviewMatches[]。按 match_id 去重。

    2026-08-25 起带赛季回声校验(CLAUDE.md §6.3):此前本脚本把 --season 同时
    用作请求参数和存储标签、全程不校验响应——正是错标事故的所在路径。现在
    响应的 details.id/selectedSeason 与请求不一致直接抛 SeasonIdentityError,
    一行都不返回。home/away 字段是给赛程骨架落库用的(见 seed_fixture_skeletons)。
    """
    season_param = quote(season, safe="")
    data = _league_matches_with_retry(client, league_id, season_param)
    verify_season_echo(data, league_id, season)

    raw = (data.get("fixtures", {}) or {}).get("allMatches") or []
    if not raw:
        raw = (data.get("overview", {}) or {}).get("leagueOverviewMatches") or []

    out = {}
    for m in raw:
        mid_raw = m.get("id")
        if mid_raw is None:
            continue
        st = m.get("status", {}) or {}
        home = m.get("home") or {}
        away = m.get("away") or {}
        mid = int(mid_raw)
        out[mid] = {
            "match_id": mid,
            "round": m.get("round"),
            "utc": st.get("utcTime"),
            "finished": bool(st.get("finished")),
            "cancelled": bool(st.get("cancelled")),
            "status": derive_match_status(st),
            "home_id": int(home["id"]) if home.get("id") is not None else None,
            "home_name": home.get("name"),
            "away_id": int(away["id"]) if away.get("id") is not None else None,
            "away_name": away.get("name"),
        }
    return list(out.values())


def seed_fixture_skeletons(league_id: int, season: str, fixtures: list) -> int:
    """把枚举到的比赛先按"赛程同步拥有的列"落成 dim_match 骨架行。

    2026-08-25 架构(CLAUDE.md §6.3):Season 由赛程同步独占,ingest_match 不再
    产生赛季、且对不存在的行 fail closed——历史赛季批量回填因此必须先落骨架
    (Season 用上面回声校验过的 --season 值),再逐场补明细。走
    upsert_fixture_row(列作用域),不碰裁判/天气/比分等其它路径拥有的列。
    """
    conn = get_connection()
    n = 0
    try:
        for f in fixtures:
            if f.get("home_id") is None or f.get("away_id") is None:
                continue  # 无法构成合法骨架的行,交给逐场明细路径自己失败报告
            utc = f.get("utc")
            date = _utc_to_date(utc)
            kickoff_at_utc = normalize_utc_iso(utc)
            if kickoff_at_utc is not None:
                kickoff_precision, kickoff_source = "exact", "fotmob:fixtures"
            elif date is not None:
                kickoff_precision, kickoff_source = "date_only", None
            else:
                kickoff_precision, kickoff_source = "unknown", None
            upsert_fixture_row(conn, {
                "Match_ID": f["match_id"],
                "Season": season,
                "League_ID": league_id,
                "Date": date,
                "Home_Team_ID": f["home_id"],
                "Away_Team_ID": f["away_id"],
                "Home_Team_Name": f.get("home_name"),
                "Away_Team_Name": f.get("away_name"),
                "status": f.get("status"),
                "Match_Round": f.get("round"),
                "kickoff_at_utc": kickoff_at_utc,
                "kickoff_precision": kickoff_precision,
                "kickoff_source": kickoff_source,
            })
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def _utc_to_date(utc):
    """ISO 时间串（如 "2026-05-19T18:30:00Z"）转 "YYYY-MM-DD"，非 ISO 格式返回 None。"""
    if isinstance(utc, str) and len(utc) >= 10 and utc[4] == "-" and utc[7] == "-":
        return utc[:10]
    return None


def ingest_season_tables(client: FotMobClient, league_id: int, season: str) -> None:
    """用同一份 league_matches() 响应回填 fact_league_table / fact_season_player_stats。

    2026-08-25 起带赛季回声校验(CLAUDE.md §6.3):这条路径此前是全仓最大的
    无校验缺口——同一个 --season 字符串既当请求参数又当存储标签,来源静默
    回退赛季时会整季错标(英超 fact_league_table 里那 60 行 Season='2024'
    幽灵赛季就是同类产物)。
    """
    season_param = quote(season, safe="")
    data = _league_matches_with_retry(client, league_id, season_param)
    verify_season_echo(data, league_id, season)

    table_rows = client.parse_league_table(data, league_id, season)
    player_rows = client.parse_season_player_stats(data, league_id, season)

    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM fact_league_table WHERE League_ID=? AND Season=?",
            (league_id, season),
        )
        _insert_many(
            conn,
            "fact_league_table",
            LEAGUE_TABLE_CORE_COLUMNS + [("extra_json", "TEXT")],
            _rows_with_extra_json(table_rows),
        )

        conn.execute(
            "DELETE FROM fact_season_player_stats WHERE League_ID=? AND Season=?",
            (league_id, season),
        )
        _insert_many(
            conn,
            "fact_season_player_stats",
            SEASON_PLAYER_STATS_CORE_COLUMNS + [("extra_json", "TEXT")],
            _rows_with_extra_json(player_rows),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"季级新表落库完成: fact_league_table={len(table_rows)}, "
          f"fact_season_player_stats={len(player_rows)}")


def ingest_matches_sequential(
    targets: list,
    *,
    league_id: int,
    sleep: float = 0.3,
    sleep_jitter: float = 0.5,
    cooldown_window: int = 10,
    cooldown_fail_rate: float = 0.8,
    cooldown_seconds: float = 90.0,
) -> tuple[list, list]:
    """逐场调用 ingest_match(),按失败率熔断——提取自 _run()(2026-08-27,供
    backend/cli/backfill_fixtures.py 复用),行为不变、只是不再打印汇总/重试
    命令(不同调用方想要的收尾文案不同,交回调用方自己拼)。

    targets 每项需要 {"match_id", "utc"}(utc 用于取 date,可为 None)。
    返回 (ok_match_ids, fail_match_ids)。
    """
    ok: list = []
    fail: list = []
    recent = deque(maxlen=cooldown_window)
    total = len(targets)
    for i, f in enumerate(targets, 1):
        mid = f["match_id"]
        date = _utc_to_date(f.get("utc"))
        try:
            ingest_match(mid, league_id=league_id, date=date)
            ok.append(mid)
            recent.append(True)
        except Exception as e:
            print(f"match_id={mid} 失败: {e}")
            fail.append(mid)
            recent.append(False)

        # 失败率熔断：最近 N 场里坏得太狠，大概率是代理池进入了坏时段
        # （之前诊断过 0~30% 成功率的情况），与其原速率把整季请求额度都
        # 空耗在这个坏窗口上，不如先冷却一下，给轮换池换到健康节点的机会。
        if len(recent) == recent.maxlen:
            fail_rate = recent.count(False) / len(recent)
            if fail_rate >= cooldown_fail_rate:
                print(f"最近 {len(recent)} 场失败率 {fail_rate:.0%} "
                      f"≥ {cooldown_fail_rate:.0%}，疑似代理池进入坏时段，"
                      f"冷却 {cooldown_seconds:.0f}s 后继续")
                time.sleep(cooldown_seconds)
                recent.clear()

        if i < total:
            time.sleep(sleep + random.uniform(0, sleep_jitter))
    return ok, fail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", type=int, default=47)
    # 2026-08-25:曾经默认 "2025/2026"——真实事故,操作者用 --match-ids 单独
    # 补采某几场时忘了带 --season,静默用旧赛季覆盖了 5 场已经是新赛季的英超
    # 揭幕战(详见 CLAUDE.md §6.3)。--season 现在必须显式传入,不给默认值。
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--only-finished", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument(
        "--match-ids", type=str, default=None,
        help="逗号分隔的 match_id 列表，跳过枚举，仅重试指定场次",
    )
    # thordata 住宅代理是轮换会话（每次请求基本换一个出口 IP），不需要靠长间隔
    # 规避封禁；改成 <1s 的短间隔。约定：如果这个短间隔导致 403/404 频繁出现，
    # 说明短间隔本身触发了限流/风控（不同于普通的 TLS/超时类瞬时网络抖动），
    # 应恢复回 2.5/1.5 的旧值，而不是继续调更短。
    parser.add_argument("--sleep", type=float, default=0.3)
    parser.add_argument("--sleep-jitter", type=float, default=0.5)
    parser.add_argument("--skip-season-tables", action="store_true")
    parser.add_argument(
        "--skip-existing", action=argparse.BooleanOptionalAction, default=True,
        help="跳过 dim_match 里已经落库过的场次，把重试额度留给真正缺的场次"
        "（--match-ids 显式重试模式不受此项影响）",
    )
    parser.add_argument(
        "--force-season-tables", action="store_true",
        help="即使该 (league_id, season) 的季级表已存在也强制重新抓取；"
        "默认不加此项时，多赛季整体重跑会跳过已完成赛季的季级表重复请求",
    )
    parser.add_argument(
        "--cooldown-window", type=int, default=10,
        help="失败率熔断的滑动窗口大小（最近 N 场）",
    )
    parser.add_argument(
        "--cooldown-fail-rate", type=float, default=0.8,
        help="滑动窗口内失败率达到此比例即触发冷却，而不是原速率把整季请求"
        "额度空耗在代理池的坏时段上",
    )
    parser.add_argument(
        "--cooldown-seconds", type=float, default=90.0,
        help="触发熔断后的冷却时长（秒）",
    )
    args = parser.parse_args()

    lock_path = _acquire_lock(args.league_id)
    try:
        _run(args)
    finally:
        _release_lock(lock_path)


def _run(args) -> None:
    client = FotMobClient()

    if args.match_ids:
        targets = [
            {"match_id": int(x), "utc": None}
            for x in args.match_ids.split(",") if x.strip()
        ]
    else:
        fixtures = enumerate_fixtures(client, args.league_id, args.season)
        # 先落赛程骨架(Season 已回声校验),再逐场补明细——ingest_match 对
        # 不存在的行 fail closed(2026-08-25,CLAUDE.md §6.3)。
        seeded = seed_fixture_skeletons(args.league_id, args.season, fixtures)
        print(f"赛程骨架落库: {seeded}/{len(fixtures)} 行(Season 经回声校验)")
        if args.only_finished:
            fixtures = [f for f in fixtures if f["finished"] and not f["cancelled"]]
        fixtures.sort(key=lambda f: (f["utc"] or ""))
        if args.skip_existing:
            done = _existing_match_ids(args.league_id)
            before = len(fixtures)
            fixtures = [f for f in fixtures if f["match_id"] not in done]
            print(f"跳过已落库场次: {before - len(fixtures)}/{before}"
                  f"（已在 dim_match 里，本次不重复请求）")
        targets = fixtures[: args.limit] if args.limit else fixtures

    total = len(targets)
    print(f"共 {total} 场待落库(league_id={args.league_id}, season={args.season})")

    # 不再传 season(2026-08-25,CLAUDE.md §6.3):Season 已由上面的骨架落库
    # (回声校验)写入,明细抓取不碰赛季。
    ok, fail = ingest_matches_sequential(
        targets,
        league_id=args.league_id,
        sleep=args.sleep,
        sleep_jitter=args.sleep_jitter,
        cooldown_window=args.cooldown_window,
        cooldown_fail_rate=args.cooldown_fail_rate,
        cooldown_seconds=args.cooldown_seconds,
    )

    print(f"\n=== 逐场落库汇总: {len(ok)}/{total} 成功, {len(fail)} 失败 ===")
    if fail:
        fail_str = ",".join(str(x) for x in fail)
        print(f"失败 match_ids: {fail_str}")
        print(f"重试命令: python backend/ingest/ingest_league.py --match-ids {fail_str} "
              f"--season {args.season}")

    if not args.skip_season_tables:
        if not args.force_season_tables and _season_tables_done(args.league_id, args.season):
            print(f"季级新表已存在(league_id={args.league_id}, season={args.season})，"
                  f"跳过重复抓取（如需强制刷新加 --force-season-tables）")
        else:
            ingest_season_tables(client, args.league_id, args.season)


if __name__ == "__main__":
    main()
