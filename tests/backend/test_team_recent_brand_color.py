"""比赛详情第二级取色:team_recent_brand_color(2026-09-26,第四批 B)。

本场 FotMob 配色缺失(2026-08-24 之前的老比赛没有回填)时,前端退到"该队最近一场
有配色的比赛"的代表色。语义同 team_brand_color_map:配对级、深浅两个变体都齐且是合法
十六进制才认,不跨主题借用;没有则 None(前端退到兜底组合)。
"""

from backend.db.connections import connect_rw
from backend.queries import matches as q_matches
from backend.queries.teams import team_recent_brand_color


def _ins(conn, mid, date, home, away, hl=None, hd=None, al=None, ad=None):
    conn.execute(
        """INSERT INTO dim_match
           (Match_ID, Season, League_ID, Date, Home_Team_ID, Away_Team_ID,
            Home_Team_Name, Away_Team_Name, status,
            Home_Team_Color_Light, Home_Team_Color_Dark, Away_Team_Color_Light, Away_Team_Color_Dark,
            kickoff_at_utc, kickoff_precision, kickoff_source)
           VALUES (?, '2026', 59, ?, ?, ?, 'H', 'A', 'Finish', ?, ?, ?, ?, ?, 'exact', 'fotmob:fixtures')""",
        (mid, date, home, away, hl, hd, al, ad, f"{date}T15:00:00Z"),
    )


def test_picks_most_recent_colored_match_home_or_away(data_dir):
    conn = connect_rw("core")
    try:
        _ins(conn, 1, "2026-08-01", 10, 20, hl="#111111", hd="#222222", al="#333333", ad="#444444")
        _ins(conn, 2, "2026-09-01", 30, 10, hl="#aaaaaa", hd="#bbbbbb", al="#123456", ad="#654321")
        conn.commit()
        # 球队 10 最近一场是 match 2(客队)
        assert team_recent_brand_color(conn, 10) == {"light": "#123456", "dark": "#654321"}
        # 球队 20 只在 match 1 出现(客队)
        assert team_recent_brand_color(conn, 20) == {"light": "#333333", "dark": "#444444"}
    finally:
        conn.close()


def test_none_when_no_colored_match_or_team_missing(data_dir):
    conn = connect_rw("core")
    try:
        _ins(conn, 1, "2026-08-01", 10, 20)  # 全空
        conn.commit()
        assert team_recent_brand_color(conn, 10) is None
        assert team_recent_brand_color(conn, 999) is None
        assert team_recent_brand_color(conn, None) is None
    finally:
        conn.close()


def test_requires_both_variants_and_valid_hex_no_cross_theme_borrowing(data_dir):
    conn = connect_rw("core")
    try:
        # 最近一场只有浅色变体 → 不认,退到更早那场两个变体都齐的
        _ins(conn, 1, "2026-08-01", 10, 20, hl="#101010", hd="#202020")
        _ins(conn, 2, "2026-09-01", 10, 20, hl="#f0f0f0", hd=None)
        # 更近一场:值不是合法十六进制 → 不认
        _ins(conn, 3, "2026-09-10", 10, 20, hl="red", hd="rgba(1,2,3,0.5)")
        conn.commit()
        assert team_recent_brand_color(conn, 10) == {"light": "#101010", "dark": "#202020"}
    finally:
        conn.close()


def test_match_by_id_exposes_brand_colors_for_both_teams(data_dir):
    conn = connect_rw("core")
    try:
        # 本场(match 3)没有配色,但两队各自更早有过
        _ins(conn, 1, "2026-08-01", 10, 20, hl="#111111", hd="#222222", al="#333333", ad="#444444")
        _ins(conn, 3, "2026-09-10", 10, 20)
        conn.commit()
        m = q_matches.match_by_id(conn, 3)
        assert m["home_team_color"] is None and m["away_team_color"] is None
        assert m["home_team_brand_color"] == {"light": "#111111", "dark": "#222222"}
        assert m["away_team_brand_color"] == {"light": "#333333", "dark": "#444444"}
    finally:
        conn.close()
