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
    4. step4_predict():复用 backend/models/predict_wdl_future.py,对全部
       status='NotStarted' 的 26/27 比赛重算预测。ρ/isotonic 校准器/联赛
       基准全部从 backend/models/artifacts/wdl_baseline_params.pkl 加载,
       不重新拟合——重算的只是 rolling 输入变了,模型参数本身不变。

用法:
    python backend/scheduler.py [--league-id 47] [--season 2026/2027]
    python backend/scheduler.py --skip-scrape   # 跳过真实爬取,只跑 2/3/4
                                                  (C3.2 模拟测试专用,验证
                                                  编排链本身,不依赖真实新
                                                  完赛比赛)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_connection
from fotmob_client import FotMobClient
from ingest.ingest_match import ingest_match
from silver.build_silver import build_silver
from models.features.build_match_features import build_match_features
from models.predict_wdl_future import main as predict_wdl_future_main

DEFAULT_LEAGUE_ID = 47
DEFAULT_SEASON = "2026/2027"


def step1_ingest_newly_finished(league_id: int, season: str) -> list:
    print(f"\n[1/4] 检查 League_ID={league_id} Season={season} 是否有场次已从 "
          f"NotStarted 变成 Finish ...")

    client = FotMobClient()
    ip_info = client.check_ip()
    print(f"  代理连通性检查: {ip_info}")
    if not ip_info.get("origin"):
        raise RuntimeError("代理连通性检查未拿到出口 IP,停止,不盲试后续抓取")

    conn = get_connection()
    try:
        not_started_ids = {
            r[0]
            for r in conn.execute(
                "SELECT Match_ID FROM dim_match WHERE League_ID=? AND Season=? AND status='NotStarted'",
                (league_id, season),
            ).fetchall()
        }
    finally:
        conn.close()
    print(f"  库里当前 status='NotStarted' 的场次: {len(not_started_ids)}")

    data = client.league_matches(league_id, season)
    raw = (data.get("fixtures", {}) or {}).get("allMatches") or []

    newly_finished = []
    for m in raw:
        mid = int(m["id"])
        if mid in not_started_ids and (m.get("status") or {}).get("finished"):
            newly_finished.append(mid)
    print(f"  FotMob 实时状态显示已完赛、但库里仍是 NotStarted 的场次: {len(newly_finished)}")

    # 遇到第一场失败就立即停止(不是"整批都试完再统一报错")——ingest_match()
    # 每场各自独立 commit,批内没有跨场次的事务包裹;continue 试完整批只会让
    # 更多场次在失败场次之前先落成 Finish,反而扩大"Bronze 已经比 Silver/
    # Gold 新、但链路提前中断"的窗口。真正的失败恢复手段是重跑本脚本——
    # ingest_match 幂等、newly_finished 每次都是重新按 NotStarted 现算的,
    # 已成功落库的场次下次不会重复处理,只会继续处理还没成功的那些。
    ok = []
    for mid in newly_finished:
        try:
            ingest_match(mid, league_id=league_id, season=season)
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


def step4_predict() -> None:
    print("\n[4/4] predict_wdl_future(全量重算所有 status='NotStarted' 的 26/27 预测) ...")
    predict_wdl_future_main()
    print("[4/4] 完成")


def run(league_id: int, season: str, skip_scrape: bool) -> None:
    if skip_scrape:
        print("\n[1/4] --skip-scrape:跳过真实爬取(C3.2 模拟测试用,验证编排链"
              "本身,不依赖真实新完赛比赛)")
    else:
        step1_ingest_newly_finished(league_id, season)

    step2_build_silver()
    step3_build_features()
    step4_predict()

    print("\n=== scheduler 全链路执行完成 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", type=int, default=DEFAULT_LEAGUE_ID)
    parser.add_argument("--season", type=str, default=DEFAULT_SEASON)
    parser.add_argument(
        "--skip-scrape", action="store_true",
        help="跳过第 1 步真实爬取,只跑 2/3/4(C3.2 模拟测试用)",
    )
    args = parser.parse_args()
    run(args.league_id, args.season, args.skip_scrape)
