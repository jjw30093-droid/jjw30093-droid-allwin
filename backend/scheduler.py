"""
scheduler.py — 概率卡更新链路编排(ROADMAP.md Phase C3,选项 1)。

手动触发的一条命令,把"爬完场 → silver → features → 重算预测"串成一条链路。
不是定时任务(cron/systemd 常驻不在这次范围内,见 CLAUDE.md 相关约定)。

四步,任一步失败立即停止,不用半成品数据继续跑下一步:
    1. step1_ingest_newly_finished():对比 dim_match 里 Season=2026/2027 且
       status='NotStarted' 的场次和 FotMob 实时赛程状态,把已经"从
       NotStarted 变成 Finish"的场次,用 ingest_match()——与历史赛季完全
       同一条全量抓取路径(比分 + xG/射门等 team stats + 事件 + 阵容)——
       重新落库。只落"比分"不落 xG 的话,Silver/特征层的 rolling 会漏掉
       这场(它们都是从 fact_team_match_stats 取 xG,不是从 dim_match)。
    2. step2_build_silver():复用 backend/silver/build_silver.py,新完赛进
       Silver 聚合。
    3. step3_build_features():复用 backend/models/features/build_match_features.py,
       rolling 重算(新结果改变各队近况;lag 逻辑——每场只用"该场之前"已完赛
       的最近 N 场——继承自 1.M.1,本文件不重新实现)。

2026-08-25:第 4 步(WDL 模型重算预测)已随 WDL 模型/正式预测登记簿一并废弃
(胜率改由 bet365 赔率直接派生);本文件保留前三步,它们是 fotmob_incremental_multi
等生产采集路径实际复用的落库/Silver/特征链路,与已废弃的模型预测无关。

用法:
    python backend/scheduler.py --season 2026/2027 [--league-id 47]
    python backend/scheduler.py --skip-scrape   # 跳过真实爬取,只跑 2/3/4
                                                  (C3.2 模拟测试专用,验证
                                                  编排链本身,不依赖真实新
                                                  完赛比赛)
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection
from fotmob_client import FotMobClient
from ingest.ingest_match import ingest_match
from silver.build_silver import build_silver
from models.features.build_match_features import build_match_features

from backend.db.util import utc_now_iso
from backend.ingest.poll_windows import (
    POSTMATCH_STALE_THRESHOLD_HOURS,
    league_stale_unresolved_match_ids,
)

DEFAULT_LEAGUE_ID = 47
# DEFAULT_SEASON 已删除(2026-08-25,CLAUDE.md §6.3):标识赛季/时间分区的
# 参数不得有"当下合理、未来注定过期"的字面量默认值。手动 CLI 的 --season
# 改为必填;生产调用方(fotmob_incremental_multi)本来就按库里真实存在的
# 赛季逐个传入,不受影响。


def step1_ingest_newly_finished(league_id: int, season: str) -> list:
    print(f"\n[1/4] 检查 League_ID={league_id} Season={season} 是否有场次已从 "
          f"NotStarted 变成 Finish ...")

    client = FotMobClient()
    ip_info = client.check_ip()
    print(f"  代理连通性检查: {ip_info}")
    if not ip_info.get("origin"):
        raise RuntimeError("代理连通性检查未拿到出口 IP,停止,不盲试后续抓取")

    # 2026-08-24:候选判据不能只认 status='NotStarted'——赛程同步(schedule_
    # sync_multi)撞上正在进行的比赛会把 dim_match.status 写成 'InPlay',一旦
    # 变成 InPlay 就再也不是 'NotStarted',这里原来的精确匹配会把它永久漏掉:
    # 生产实测 21 场比赛卡在 InPlay、开球早已过去数天,shots/player_stats/
    # momentum 全部零数据,且没有任何路径能再捞回来。改成与
    # backend/ingest/poll_windows.py 的 league_stale_unresolved_match_ids 同一
    # 判据(status != 'Finish' 且已过精确开球 + 阈值)——不再要求"曾经是
    # NotStarted",只要求"该结束却还没结束"。这不改变下面"是否真的重新落库"
    # 的判定:仍然只在 FotMob 实时状态确认 finished 才会 ingest,这里只是扩大
    # 了"要不要去问 FotMob"的候选池。
    #
    # league_stale_unresolved_match_ids 只按 League_ID 判定(它是"这个联赛该不
    # 该触发一轮"的信号,不区分赛季),这里额外交一次 Season——不能让别的赛季
    # 里同样卡住的比赛,被拿着本次调用的 season 字符串误标进 ingest_match()
    # (season 会直接覆盖 dim_match.Season,见 ingest_match.py 的 dim_kwargs)。
    conn = get_connection()
    # get_connection()(legacy backend/db.py 连接)不像 backend.db.connections
    # 的 connect_ro/connect_rw 那样默认设 row_factory——league_stale_
    # unresolved_match_ids 用列名取值(r["kickoff_at_utc"]),裸元组会直接
    # TypeError。sqlite3.Row 同时兼容下面 r[0] 的位置取法,设置了不影响本函数
    # 其余查询。
    conn.row_factory = sqlite3.Row
    try:
        stale_ids = league_stale_unresolved_match_ids(
            conn, league_id, utc_now_iso(), threshold_hours=POSTMATCH_STALE_THRESHOLD_HOURS
        )
        season_ids = {
            r[0]
            for r in conn.execute(
                "SELECT Match_ID FROM dim_match WHERE League_ID=? AND Season=? AND status != 'Finish'",
                (league_id, season),
            ).fetchall()
        }
    finally:
        conn.close()
    candidate_ids = stale_ids & season_ids
    print(f"  库里当前候选(status≠Finish 且已过开球+{POSTMATCH_STALE_THRESHOLD_HOURS}h,"
          f"Season={season})的场次: {len(candidate_ids)}")

    data = client.league_matches(league_id, season)
    raw = (data.get("fixtures", {}) or {}).get("allMatches") or []

    newly_finished = []
    for m in raw:
        mid = int(m["id"])
        if mid in candidate_ids and (m.get("status") or {}).get("finished"):
            newly_finished.append(mid)
    print(f"  FotMob 实时状态显示已完赛、但库里仍未落库为 Finish 的场次: {len(newly_finished)}")

    # 遇到第一场失败就立即停止(不是"整批都试完再统一报错")——ingest_match()
    # 每场各自独立 commit,批内没有跨场次的事务包裹;continue 试完整批只会让
    # 更多场次在失败场次之前先落成 Finish,反而扩大"Bronze 已经比 Silver/
    # Gold 新、但链路提前中断"的窗口。真正的失败恢复手段是重跑本脚本——
    # ingest_match 幂等、newly_finished 每次都是重新按 NotStarted 现算的,
    # 已成功落库的场次下次不会重复处理,只会继续处理还没成功的那些。
    ok = []
    for mid in newly_finished:
        try:
            # 2026-08-25 起不传 season(CLAUDE.md §6.3):Season 由赛程同步独占,
            # ingest_match 不再接受/覆盖赛季。season 参数仍用于上面的候选筛选
            # (把本次调用限定在一个赛季内),但不再流向任何写路径。
            ingest_match(mid, league_id=league_id)
            ok.append(mid)
        except Exception as e:
            raise RuntimeError(
                f"match_id={mid} 落库失败,立即停止(已成功: {ok}): {e}"
            ) from e

    print(f"[1/4] 完成: {len(ok)} 场重新落库为 Finish(比分 + xG 等 team stats 全量抓取)")
    return ok


def step2_build_silver() -> None:
    print("\n[2/4] build_silver ...")
    build_silver()
    print("[2/4] 完成")


def step3_build_features() -> None:
    print("\n[3/4] build_match_features ...")
    build_match_features()
    print("[3/4] 完成")


def run(league_id: int, season: str, skip_scrape: bool) -> None:
    if skip_scrape:
        print("\n[1/3] --skip-scrape:跳过真实爬取(C3.2 模拟测试用,验证编排链"
              "本身,不依赖真实新完赛比赛)")
    else:
        step1_ingest_newly_finished(league_id, season)

    step2_build_silver()
    step3_build_features()

    print("\n=== scheduler 全链路执行完成 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", type=int, default=DEFAULT_LEAGUE_ID)
    parser.add_argument("--season", type=str, required=True)
    parser.add_argument(
        "--skip-scrape", action="store_true",
        help="跳过第 1 步真实爬取,只跑 2/3/4(C3.2 模拟测试用)",
    )
    args = parser.parse_args()
    run(args.league_id, args.season, args.skip_scrape)
