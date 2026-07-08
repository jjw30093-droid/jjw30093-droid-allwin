"""
seed_curated.py — 把人工核对/多方核验过的中文译名种子数据写入 i18n 表。

覆盖三类高风险条目,不走批量机器翻译(理由见 schema.py 里 DIM_PLAYER_I18N_COLUMNS
的注释):
    1. 28 支英超球队(dim_team_i18n)
    2. 东亚球员(CHN/JPN/KOR/PRK,罗马字转写无法机器反推真实汉字全名)
    3. 知名球星简称(用多票核验锁定最广泛认知的译名,不依赖单次机器翻译的偶然一致)

数据来源:allwin-i18n-curation workflow(三票独立核验),CURATED_DATA 常量里
的 verify_agree/verify_total 就是那次核验的真实票数,不是随手编的。
一律 INSERT OR REPLACE,可重复运行。

用法:
    python backend/i18n/seed_curated.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection


def _player_id_by_name(conn, name_en: str) -> list:
    rows = conn.execute(
        "SELECT Player_ID FROM dim_player WHERE Player_Name = ?", (name_en,)
    ).fetchall()
    return [r[0] for r in rows]


def seed_teams(conn, entries: list) -> int:
    n = 0
    for t in entries:
        conn.execute(
            """
            INSERT OR REPLACE INTO dim_team_i18n (Team_ID, name_en, name_zh, source, updated_at)
            VALUES (?, ?, ?, 'workflow_verified', datetime('now'))
            """,
            (t["team_id"], t["name_en"], t["name_zh"]),
        )
        n += 1
    return n


def seed_east_asian(conn, entries: list) -> int:
    n = 0
    for p in entries:
        agree, total = p.get("verify_agree", 0), p.get("verify_total", 3)
        needs_review = 1 if agree < total else 0
        conn.execute(
            """
            INSERT OR REPLACE INTO dim_player_i18n
                (Player_ID, name_en, name_zh, name_zh_short, source, model, confidence, needs_review, updated_at)
            VALUES (?, ?, ?, ?, 'workflow_verified_east_asian', NULL, NULL, ?, datetime('now'))
            """,
            (p["player_id"], p["name_en"], p["name_zh"], p["name_zh"], needs_review),
        )
        n += 1
    return n


def seed_stars(conn, entries: list) -> tuple:
    n, unmatched = 0, []
    for s in entries:
        agree, total = s.get("verify_short_agree", 0), s.get("verify_short_total", 3)
        needs_review = 1 if agree < total else 0
        pids = _player_id_by_name(conn, s["name_en"])
        if not pids:
            unmatched.append(s["name_en"])
            continue
        for pid in pids:
            conn.execute(
                """
                INSERT OR REPLACE INTO dim_player_i18n
                    (Player_ID, name_en, name_zh, name_zh_short, source, model, confidence, needs_review, updated_at)
                VALUES (?, ?, ?, ?, 'workflow_verified_star', NULL, NULL, ?, datetime('now'))
                """,
                (pid, s["name_en"], s["name_zh_full"], s["name_zh_short"], needs_review),
            )
            n += 1
    return n, unmatched


def run(curated_data: dict) -> None:
    conn = get_connection()
    try:
        n_teams = seed_teams(conn, curated_data["teamVerified"])
        n_ea = seed_east_asian(conn, curated_data["eastAsianVerified"])
        n_stars, unmatched = seed_stars(conn, curated_data["starVerified"])
        conn.commit()
    finally:
        conn.close()

    print(f"球队写入: {n_teams}")
    print(f"东亚球员写入: {n_ea}")
    print(f"知名球星写入: {n_stars}")
    if unmatched:
        print(f"在 dim_player 里找不到对应 Player_ID 的球星名(跳过): {unmatched}")

    low_conf = []
    conn = get_connection()
    try:
        low_conf = conn.execute(
            "SELECT Player_ID, name_en, name_zh FROM dim_player_i18n WHERE needs_review = 1"
        ).fetchall()
    finally:
        conn.close()
    print(f"\n标记待复核(needs_review=1)的条目: {len(low_conf)}")
    for row in low_conf:
        print(" ", row)


if __name__ == "__main__":
    import json

    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "curated_data.json")
    if not os.path.exists(data_path):
        print(f"未找到 {data_path}，请先把 workflow 输出保存到这个文件。")
        raise SystemExit(1)
    with open(data_path, encoding="utf-8") as f:
        run(json.load(f))
