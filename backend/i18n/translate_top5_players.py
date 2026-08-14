"""
translate_top5_players.py — 五大联赛球员中文名两阶段处理:

阶段一(full):沿用 translate_players.py 的 qwen-mt-plus 直译逻辑,给五大联赛
(英超47/西甲87/意甲55/德甲54/法甲53)里还没有 name_zh 的球员补全"名·姓"全译名。
范围来自 fact_player_match_stats JOIN dim_match(真实出场记录,不是某几项
榜单 top-N),比 fact_season_player_stats 覆盖更全。

阶段二(nickname):qwen-mt-plus 是窄任务翻译模型,只会做字面直译,产生不出
"B费""恩佐"这类中文互联网球迷圈的约定俗成简称——那不是翻译,是球迷文化
惯例(常用于区分同姓球星,如国际米兰/切尔西各有一位"费尔南德斯")。
这一步改用通用对话模型(qwen-plus),按队分批(~25人/批)把球员英文全名 +
阶段一/既有的直译全名一起喂给模型,要求:
    - 知名球星有公认简称的,给出该简称;
    - 不确定/不知名的,直接沿用姓氏译名作为简称(不编造)。
模型返回按 Player_ID 键控的 JSON,写回 dim_player_i18n.name_zh_short,
source 标记为 qwen-plus-nickname 以区别阶段一的字面翻译,便于后续人工抽查。

用法:
    python -m backend.i18n.translate_top5_players --phase full [--limit N]
    python -m backend.i18n.translate_top5_players --phase nickname [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"
))

import dashscope
from db import get_connection

from i18n.translate_players import translate_one  # 阶段一复用同一套直译+重试逻辑

dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]

NICKNAME_MODEL = "qwen-plus"
TOP5_LEAGUE_IDS = (47, 87, 55, 54, 53)
BATCH_SIZE = 25
MAX_RETRIES = 5
RETRY_BASE_DELAY = 3.0


def _top5_players(conn) -> list[tuple[str, str, int, str]]:
    """(Player_ID, Player_Name, Team_ID, Team_Name) —— 真实出场记录去重,
    每人取其出现过的第一个 Team_ID/Team_Name 仅作分批与上下文提示用。"""
    rows = conn.execute(
        f"""
        SELECT s.Player_ID, MIN(p.Player_Name) AS Player_Name,
               MIN(s.Team_ID) AS Team_ID
        FROM fact_player_match_stats s
        JOIN dim_match m ON m.Match_ID = s.Match_ID
        JOIN dim_player p ON p.Player_ID = s.Player_ID
        WHERE m.League_ID IN ({",".join("?" for _ in TOP5_LEAGUE_IDS)})
          AND p.Player_Name IS NOT NULL
        GROUP BY s.Player_ID
        """,
        TOP5_LEAGUE_IDS,
    ).fetchall()
    team_names = {
        tid: (name_zh or name_en)
        for tid, name_en, name_zh in conn.execute(
            "SELECT Team_ID, name_en, name_zh FROM dim_team_i18n"
        ).fetchall()
    }
    out = []
    for pid, name, team_id in rows:
        out.append((pid, name, team_id, team_names.get(team_id, "")))
    return out


# ── 阶段一:补全缺失的 name_zh(直译,复用 translate_players.py) ──────────


def run_full(limit: int | None) -> None:
    conn = get_connection()
    try:
        players = _top5_players(conn)
        done = {
            r[0]
            for r in conn.execute(
                "SELECT Player_ID FROM dim_player_i18n WHERE name_zh IS NOT NULL"
            ).fetchall()
        }
    finally:
        conn.close()

    pending = [(pid, name) for pid, name, _, _ in players if pid not in done]
    if limit:
        pending = pending[:limit]
    total = len(pending)
    print(f"五大联赛待直译球员: {total}")

    ok, failed = 0, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(translate_one, name): (pid, name) for pid, name in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            pid, name = futures[fut]
            result = fut.result()
            conn = get_connection()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO dim_player_i18n
                       (Player_ID, name_en, name_zh, name_zh_short, source, model, confidence, needs_review, updated_at)
                       VALUES (?, ?, ?, ?, 'qwen-mt-plus', 'qwen-mt-plus', NULL, ?, datetime('now'))""",
                    (pid, name, result.get("name_zh"), result.get("name_zh_short"), result.get("needs_review", 1)),
                )
                conn.commit()
            finally:
                conn.close()
            if result.get("name_zh"):
                ok += 1
            else:
                failed.append((pid, name, result.get("error")))
            if i % 200 == 0 or i == total:
                print(f"进度 {i}/{total}(成功 {ok})")

    print(f"=== 阶段一完成: {ok}/{total} 成功, {len(failed)} 失败 ===")
    if failed:
        print("失败样例(前20):", failed[:20])


# ── 阶段二:按队分批,请通用模型给出中文互联网通用简称 ────────────────


NICKNAME_PROMPT = """你是中国大陆中文足球媒体/懂球帝/微博球迷圈的编辑,熟悉中文互联网对球员的约定俗成简称。

下面是一支球队球员名单,每人给出:英文全名、机器直译的中文全名(格式"名·姓")。
请为每个人给出该联赛球迷圈里**实际最常用**的中文简称(name_zh_short):

规则:
1. 如果该球员知名、中文球迷圈有公认惯用简称,给出该简称(常见规律:同姓氏球星
   为区分彼此会用"名首字+姓"如"B费"/"K罗",或者干脆用名字如"恩佐"/"罗德里";
   顶级巨星常用单字/双字昵称)。
2. 如果不确定该球员是否知名、或想不出公认简称,直接返回给定的直译全名里
   "·"后面的姓氏部分作为简称——不要编造你不确定的简称。
3. 只输出 JSON 数组,不要任何解释文字,格式:
   [{{"id": "球员ID原样返回", "short": "简称"}}, ...]

球员名单:
{roster}
"""


def _format_roster(batch: list[tuple[str, str, str]]) -> str:
    lines = []
    for pid, name_en, name_zh in batch:
        lines.append(f'- id={pid} 英文名="{name_en}" 直译="{name_zh or name_en}"')
    return "\n".join(lines)


def nickname_batch(batch: list[tuple[str, str, str]]) -> dict[str, dict]:
    """一批(Player_ID, name_en, name_zh) -> {Player_ID: {"short":..., "needs_review":...}}。"""
    prompt = NICKNAME_PROMPT.format(roster=_format_roster(batch))
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = dashscope.Generation.call(
                model=NICKNAME_MODEL,
                messages=[{"role": "user", "content": prompt}],
                result_format="message",
            )
            if resp.status_code == 200:
                content = resp.output.choices[0].message.content.strip()
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:].strip()
                items = json.loads(content)
                out = {}
                for item in items:
                    pid = str(item.get("id", "")).strip()
                    short = (item.get("short") or "").strip()
                    if pid and short:
                        out[pid] = {"short": short}
                return out
            last_err = f"HTTP {resp.status_code}: {resp.message}"
            is_rate_limit = resp.status_code == 429
        except Exception as e:
            last_err = str(e)
            is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            if is_rate_limit:
                delay *= 2
            time.sleep(delay)
    print(f"批次失败(放弃): {last_err}", file=sys.stderr)
    return {}


def run_nickname(limit: int | None) -> None:
    conn = get_connection()
    try:
        players = _top5_players(conn)
        name_zh_by_id = {
            pid: name_zh
            for pid, name_zh in conn.execute(
                "SELECT Player_ID, name_zh FROM dim_player_i18n WHERE name_zh IS NOT NULL"
            ).fetchall()
        }
    finally:
        conn.close()

    # 按队分组分批(球队上下文对模型识别"是否知名"有帮助;同队一起出现的
    # 撞姓球员也更容易被模型正确区分该给谁"B费"式简称)。
    by_team: dict[int, list[tuple[str, str, str]]] = {}
    for pid, name_en, team_id, _team_name in players:
        name_zh = name_zh_by_id.get(pid)
        if not name_zh:
            continue  # 阶段一还没跑或翻译失败:阶段二依赖直译全名做兜底,先跳过
        by_team.setdefault(team_id, []).append((pid, name_en, name_zh))

    batches: list[list[tuple[str, str, str]]] = []
    for team_id, roster in by_team.items():
        for i in range(0, len(roster), BATCH_SIZE):
            batches.append(roster[i : i + BATCH_SIZE])

    if limit:
        # limit 按球员数近似截断批次,方便先跑小样本检查质量
        kept, count = [], 0
        for b in batches:
            kept.append(b)
            count += len(b)
            if count >= limit:
                break
        batches = kept

    total_players = sum(len(b) for b in batches)
    print(f"五大联赛待生成简称球员: {total_players}(共 {len(batches)} 批,每批≤{BATCH_SIZE})")

    updated = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(nickname_batch, b): b for b in batches}
        for i, fut in enumerate(as_completed(futures), 1):
            batch = futures[fut]
            result = fut.result()
            conn = get_connection()
            try:
                for pid, name_en, name_zh in batch:
                    short = result.get(pid, {}).get("short")
                    if not short:
                        continue
                    conn.execute(
                        """UPDATE dim_player_i18n
                           SET name_zh_short=?, source='qwen-plus-nickname',
                               model=?, updated_at=datetime('now')
                           WHERE Player_ID=?""",
                        (short, NICKNAME_MODEL, pid),
                    )
                    updated += 1
                conn.commit()
            finally:
                conn.close()
            if i % 20 == 0 or i == len(batches):
                print(f"批次进度 {i}/{len(batches)}(已更新 {updated})")

    print(f"=== 阶段二完成: {updated}/{total_players} 已写入简称 ===")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["full", "nickname"], required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.phase == "full":
        run_full(args.limit)
    else:
        run_nickname(args.limit)


if __name__ == "__main__":
    main()
