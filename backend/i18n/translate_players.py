"""
translate_players.py — 批量把 dim_player 的英文名翻译成中文，写入 dim_player_i18n。

策略(详见 schema.py 里 DIM_PLAYER_I18N_COLUMNS 的注释):
    - 东亚球员(CHN/JPN/KOR/PRK)不在本脚本处理范围内——机器翻译无法从罗马字
      转写反推出真实汉字全名，必须走人工核对的种子数据(见 seed_curated.py)。
      本脚本运行前应该已经由 seed_curated.py 写入这些人，本脚本用
      --skip-existing(默认开)自动跳过。
    - 其余球员:调用 qwen-mt-plus 翻译全名，得到"名·姓"格式的中文全译名
      (存 name_zh)；再取"·"分隔的最后一段作为 name_zh_short——这是中文体育
      媒体日常只用姓氏的通用简称(如"马库斯·拉什福德"→"拉什福德")。
      没有"·"分隔符的(单词全名，常见于巴西/葡萄牙球员，如 Rodri/Casemiro)，
      name_zh_short 等于 name_zh 整段，不做切分。

用法:
    python backend/i18n/translate_players.py [--limit N] [--no-skip-existing]
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".env"))

import dashscope
from db import get_connection

dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]

MODEL = "qwen-mt-plus"
MAX_RETRIES = 5
RETRY_BASE_DELAY = 3.0
MAX_WORKERS = 2


def _is_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def translate_one(name_en: str) -> dict:
    """调 qwen-mt-plus 翻译一个球员英文名，返回 {name_zh, name_zh_short, needs_review}。"""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = dashscope.Generation.call(
                model=MODEL,
                messages=[{"role": "user", "content": name_en}],
                translation_options={"source_lang": "English", "target_lang": "Chinese"},
            )
            if resp.status_code == 200:
                name_zh = resp.output.choices[0].message.content.strip()
                if not name_zh or not _is_chinese(name_zh):
                    return {"name_zh": name_zh, "name_zh_short": name_zh, "needs_review": 1}
                parts = name_zh.split("·")
                name_zh_short = parts[-1] if len(parts) > 1 else name_zh
                return {"name_zh": name_zh, "name_zh_short": name_zh_short, "needs_review": 0}
            last_err = f"HTTP {resp.status_code}: {resp.message}"
            is_rate_limit = resp.status_code == 429 or "rate limit" in (resp.message or "").lower()
        except Exception as e:
            last_err = str(e)
            is_rate_limit = "429" in str(e) or "rate limit" in str(e).lower()
        if attempt < MAX_RETRIES:
            # 429(限流)比普通网络错误更需要"退一步"——指数退避而不是线性，
            # 且限流场景额外加码，避免几个线程同时重试又立刻撞回限流。
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            if is_rate_limit:
                delay *= 2
            time.sleep(delay)
    return {"name_zh": None, "name_zh_short": None, "needs_review": 1, "error": last_err}


def _existing_player_ids() -> set:
    """只把翻译真正成功(name_zh 非空)的算"已完成"——失败的行(限流/异常导致
    name_zh 为 NULL)不能被当成"跳过"，否则会被永久漏掉，重跑也补不回来。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT Player_ID FROM dim_player_i18n WHERE name_zh IS NOT NULL"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _pending_players() -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT Player_ID, Player_Name FROM dim_player WHERE Player_Name IS NOT NULL"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        conn.close()


def _write_result(player_id: str, name_en: str, result: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO dim_player_i18n
                (Player_ID, name_en, name_zh, name_zh_short, source, model, confidence, needs_review, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                player_id,
                name_en,
                result.get("name_zh"),
                result.get("name_zh_short"),
                "qwen-mt-plus",
                MODEL,
                None,
                result.get("needs_review", 1),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--skip-existing", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args()

    players = _pending_players()
    if args.skip_existing:
        done = _existing_player_ids()
        before = len(players)
        players = [(pid, name) for pid, name in players if pid not in done]
        print(f"跳过已翻译: {before - len(players)}/{before}")

    if args.limit:
        players = players[: args.limit]

    total = len(players)
    print(f"待翻译球员: {total}")

    ok, failed = 0, []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(translate_one, name): (pid, name) for pid, name in players
        }
        for fut in as_completed(futures):
            pid, name = futures[fut]
            result = fut.result()
            _write_result(pid, name, result)
            if result.get("name_zh"):
                ok += 1
                flag = " [需复核]" if result.get("needs_review") else ""
                print(f"{name} -> {result['name_zh']}(简称:{result['name_zh_short']}){flag}")
            else:
                failed.append((pid, name, result.get("error")))
                print(f"翻译失败: {name} ({result.get('error')})")

    print(f"\n=== 完成: {ok}/{total} 成功, {len(failed)} 失败 ===")
    if failed:
        print("失败列表:", failed)


if __name__ == "__main__":
    main()
