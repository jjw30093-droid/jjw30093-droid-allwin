"""E2E 种子:构建 data/e2e 独立测试数据目录(绝不触碰真实 data/*.db sidecar)。

- allwin.db 在确认源 WAL 为空后复制到隔离目录(E2E API 不再打开真实 core);
- platform/odds 每次重建并迁移;
- 种子内容:
  * admin:密码登录账号 e2e-admin / e2e-password-123;
  * 登录用户(member 基线):预绑定 mock 微信身份(openid mock-openid-user-1);
  * 一批已发布+锁定的正式预测(48%/27%/25%,生成时间早于开球,口径合法),
    覆盖首页 featured 选择的全部候选,保证匿名/会员用例断言的 48% 出现在
    首页 featured 卡上(选场逻辑见 _pick_seed_matches);
  * 一条独立的"编辑目标"正式预测,种在首页 7 天候选窗口之外的比赛上,
    专供 admin-predictions-edit.spec.ts 修改(该用例会把概率改成 0.6/0.25/0.15;
    历史教训:它按字母序先于 anonymous/auth 跑,曾直接改掉唯一种子快照,
    导致后续所有 48% 断言失败——编辑目标必须与 48% 种子物理隔离)。

kickoff provenance(不放宽任何生产门禁 —— CLAUDE.md §6.2.1):
`_require_precise_kickoff`/`normalize_exact_kickoff`/publish_snapshot/lock_snapshot/
正式样本资格查询/migration 触发器均未改动一行。种子比赛全部来自
status=upcoming&window=7d 同源查询或显式 kickoff_at_utc IS NOT NULL 过滤,
kickoff 时间与来源(如 fotmob:fixtures)直接取自核心库真实字段,不再合成;
若选中比赛意外缺精确开球时间,种子必须明确失败,不得补 00:00/15:00 冒充。

用法:.venv/bin/python -m tests.e2e.seed_e2e
"""

import json
import os
import shutil
import sys
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
E2E_DIR = ROOT / "data" / "e2e"

ADMIN_USER = "e2e-admin"
ADMIN_PASSWORD = "e2e-password-123"
MOCK_MEMBER_OPENID = "mock-openid-user-1"   # MockWechatProvider 固定 code=mock-user-1


def _anon_league_ids(platform_conn) -> set[int]:
    """匿名(free)可见联赛集合——与 API 路由完全同源地推导。"""
    from backend.auth.entitlements import resolve_entitlements
    from backend.queries.leagues import accessible_league_ids

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _, anon_entitlements = resolve_entitlements(platform_conn, None, now_iso)
    return accessible_league_ids(anon_entitlements)


def _kickoff_key(match: dict) -> tuple[str, int]:
    return (match["kickoff_at_utc"] or match["date_utc"], match["match_id"])


def _pick_seed_matches(core_conn, anon_leagues: set[int]):
    """复刻首页 featured 选择,返回 (featured, 需要种预测的比赛列表)。

    首页(frontend/app/page.tsx + lib/homepage.ts::selectHomepageMatches)的
    现实规则:主列表 = /api/v1/matches?status=upcoming&window=7d&limit=8
    (匿名联赛);有公开概率的比赛优先,其次按开球时间。只给主列表第一场
    种预测即可保证它成为 featured(该场概率最高、天然排最前)。

    2026-08-07 J 联赛开幕一次性置顶(selectFeaturedOverride)与其北欧回退
    (瑞典超 67/挪超 59)已随事件过期从 lib/homepage.ts 删除(见该文件改动
    历史);67/59 在 2026-08-11 权限矩阵互换后也已不在 anon_leagues 内,
    这里不再需要复刻那段逻辑。

    主列表为空时返回 (None, []),调用方必须明确失败,不能静默放宽条件。
    """
    from backend.queries.matches import list_matches

    now = datetime.now(timezone.utc)
    main = list_matches(
        core_conn, set(anon_leagues), status="upcoming", window="7d", now=now, limit=8
    )["matches"]
    if not main:
        return None, []
    return main[0], main[:1]


def _pick_edit_target(core_conn, anon_leagues: set[int], exclude_ids: set[int]):
    """给 admin-predictions-edit.spec.ts 单独挑一场窗口外的未开赛比赛。

    必须在首页 7 天候选窗口之外(留 1 天缓冲取 ≥ now+8d):编辑用例会把这条
    快照改成 0.6/0.25/0.15,若它进入首页候选,priority 排序可能把它顶成
    featured,破坏匿名用例的 48% 断言。要求 kickoff_at_utc 非空,保证
    publish/lock 的精确开球门禁使用真实来源时间,无需合成。
    """
    league_placeholders = ",".join("?" for _ in sorted(anon_leagues))
    exclude_clause = ""
    if exclude_ids:
        exclude_clause = (
            f" AND Match_ID NOT IN ({','.join('?' for _ in exclude_ids)})"
        )
    row = core_conn.execute(
        f"SELECT Match_ID FROM dim_match"
        f" WHERE League_ID IN ({league_placeholders}) AND status='NotStarted'"
        f"   AND kickoff_at_utc IS NOT NULL"
        f"   AND julianday(kickoff_at_utc) >= julianday('now', '+8 days')"
        f"{exclude_clause}"
        f" ORDER BY julianday(kickoff_at_utc), Match_ID LIMIT 1",
        [*sorted(anon_leagues), *sorted(exclude_ids)],
    ).fetchone()
    return row["Match_ID"] if row is not None else None


def _e2e_style_profile(match: dict, cutoff: str) -> dict:
    """Studio UI-only synthetic style rows; never presented as provider data."""

    def team(side: str, values: dict[str, float]) -> dict:
        ref = match[side]
        labels = {
            "possession": ("控球率", "%", "higher"),
            "accurate_passes": ("准确传球", "次/场", "higher"),
            "final_third_wins": ("前场夺回", "次/场", "higher"),
            "xg_per_match": ("xG", "/场", "higher"),
            "shots_on_target": ("射正", "次/场", "higher"),
            "box_touches_per_match": ("禁区触球", "次/场", "higher"),
            "corners_per_match": ("角球", "次/场", "higher"),
            "accurate_crosses": ("成功传中", "次/场", "higher"),
            "set_piece_goals": ("定位球进球", "球", "higher"),
            "xga_per_match": ("xGA", "/场", "lower"),
            "tackles": ("抢断", "次/场", "higher"),
            "clearances": ("解围", "次/场", "higher"),
        }
        metrics = {}
        for index, (key, value) in enumerate(values.items(), 1):
            label, unit, direction = labels[key]
            decimals = 2 if key in {"xg_per_match", "xga_per_match"} else 1
            display = (
                f"{int(value)}{unit}"
                if key == "set_piece_goals"
                else f"{value:.{decimals}f}{unit}"
            )
            metrics[key] = {
                "key": key,
                "label": label,
                "value": value,
                "display": display,
                "unit": unit,
                "rank": index,
                "rank_total": 20,
                "direction": direction,
                "source_stat": f"e2e_{key}",
                "source_value": value,
                "conversion": "direct",
            }
        return {
            "team_id": ref["team_id"],
            "name": ref["name"],
            "provider_name": ref["name"],
            "crest_url": ref.get("crest_url"),
            "played": 10,
            "metrics": metrics,
            "missing_metrics": [],
        }

    home_values = {
        "possession": 54.1, "accurate_passes": 430.2, "final_third_wins": 4.1,
        "xg_per_match": 1.82, "shots_on_target": 5.8, "box_touches_per_match": 31.4,
        "corners_per_match": 6.2, "accurate_crosses": 5.9, "set_piece_goals": 4,
        "xga_per_match": 1.12, "tackles": 15.8, "clearances": 20.1,
    }
    away_values = {
        "possession": 47.3, "accurate_passes": 351.4, "final_third_wins": 3.2,
        "xg_per_match": 1.31, "shots_on_target": 4.2, "box_touches_per_match": 23.8,
        "corners_per_match": 4.7, "accurate_crosses": 4.8, "set_piece_goals": 6,
        "xga_per_match": 1.46, "tackles": 17.2, "clearances": 27.5,
    }
    from backend.queries.leagues import LEAGUE_META

    league_meta = LEAGUE_META.get(match["league_id"]) or {}
    digest = hashlib.sha256(f"e2e:{match['match_id']}".encode()).hexdigest()
    return {
        "profile_version": "team-style-v1",
        "match_id": match["match_id"],
        "league_id": match["league_id"],
        "league_name_zh": league_meta.get("name_zh", f"联赛 {match['league_id']}"),
        "season": match["season"],
        "data_cutoff_at": cutoff,
        "source_hash": digest,
        "teams": {
            "home": team("home", home_values),
            "away": team("away", away_values),
        },
        "recent_form": {
            "home": ["W", "D", "W", "W", "L"],
            "away": ["L", "D", "W", "L", "D"],
        },
    }


# E2E 专用 Bet365 1x2 赔率(首页/列表页胜平负概率条的唯一数据来源)。
#
# 真实的 bronze_ng_odds_snap 每次种子重建都清空(见 main() 顶部),从未有过
# 数据——这也是"匿名用例断言赔率待采集"这条旧断言曾经成立的原因。要覆盖
# WinProbabilityBar,必须自己造一条能通过 backend/queries/odds.py::
# latest_1x2_by_match 全部筛选条件的快照:市场=1x2、pre_match、
# company_id∈('8','281')、xref review_status∈(auto_ok,confirmed)、
# 去水后 overround 落在 [1.01,1.35]。
#
# 赔率刻意选成 1.50/4.60/7.50——去水结果 66%/21%/13%,不落在种子模型概率
# 48/27/25 附近(anonymous.spec.ts 用 27%/25% 出现次数为 0 守"页面不泄漏
# 模型概率",市场概率条如果凑巧撞上这两个数字会制造假阳性)。
_E2E_ODDS_HOME, _E2E_ODDS_DRAW, _E2E_ODDS_AWAY = 1.50, 4.60, 7.50


def _seed_win_probability_odds(match_id: int) -> None:
    from backend.db.connections import connect_rw, tx
    from backend.db.util import utc_now_iso

    now = utc_now_iso()
    # 早于 now-1h:匿名请求走 1 小时延迟策略(与 /matches/{id}/odds 同一条
    # 纪律),observed_at 必须早于该门槛才会被匿名口径看到,不能卡在临界上。
    observed_at = (
        (datetime.now(timezone.utc) - timedelta(hours=2))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    provider_match_id = f"e2e-{match_id}"
    payload = {"home": _E2E_ODDS_HOME, "draw": _E2E_ODDS_DRAW, "away": _E2E_ODDS_AWAY}
    payload_json = json.dumps(payload, sort_keys=True)

    conn = connect_rw("odds")
    try:
        with tx(conn):
            conn.execute(
                """INSERT INTO dim_match_xref
                   (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                    confidence, verified, method, review_status, created_at, updated_at)
                   VALUES (?, 'nowgoal', ?, 0, 1.0, 1, 'auto', 'auto_ok', ?, ?)""",
                (match_id, provider_match_id, now, now),
            )
            conn.execute(
                """INSERT INTO bronze_ng_odds_snap
                   (provider_match_id, market, company_id, company_name, market_phase,
                    payload_json, payload_hash, observed_at, ingested_at, poll_run_id)
                   VALUES (?, '1x2', '8', 'Bet365', 'pre_match', ?, ?, ?, ?, NULL)""",
                (
                    provider_match_id,
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                    observed_at,
                    now,
                ),
            )
    finally:
        conn.close()


def main() -> int:
    E2E_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("platform.db", "odds.db"):
        for suffix in ("", "-wal", "-shm"):
            p = E2E_DIR / f"{name}{suffix}"
            if p.exists():
                p.unlink()
    core_source = ROOT / "data" / "allwin.db"
    core_source_wal = ROOT / "data" / "allwin.db-wal"
    if core_source_wal.exists() and core_source_wal.stat().st_size > 0:
        print(
            "真实 core WAL 非空,拒绝生成可能不一致的 E2E 副本",
            file=sys.stderr,
        )
        return 1
    core_copy = E2E_DIR / "allwin.db"
    if core_copy.is_symlink() or core_copy.exists():
        core_copy.unlink()
    shutil.copyfile(core_source, core_copy)
    core_copy.chmod(0o600)

    os.environ["ALLWIN_DATA_DIR"] = str(E2E_DIR)

    from backend.db import migrate
    migrate.apply_all("platform", quiet=True)
    migrate.apply_all("odds", quiet=True)

    from backend.auth import service
    from backend.cli.create_admin import create_admin
    from backend.commands import predictions as pred
    from backend.db.connections import connect_ro, connect_rw, tx
    from backend.eval.calibrate_markets import persist as persist_calibration
    from backend.eval.calibrate_markets import run as run_calibration
    from backend.queries.matches import match_by_id
    from backend.studio.team_style import record_team_style_profile

    # 赛前市场卡(/matches/{id}/markets)靠 market_calibration 查表给"数据倾向"。
    # platform.db 每次重建为空表,core 是真实数据的完整拷贝——用同一套离线
    # 标定跑一遍,E2E 断言看到的就是和 dev 环境一致的真实历史命中率,不是
    # 编出来的测试桩数据。
    calibration_results = run_calibration(["yellow_cards", "goals", "corners"])
    if calibration_results:
        calib_conn = connect_rw("platform")
        persist_calibration(calib_conn, calibration_results)
        calib_conn.close()

    conn = connect_rw("platform")
    core = connect_ro("core")
    try:
        admin_id = create_admin(conn, ADMIN_USER, ADMIN_PASSWORD, reset=True)

        with tx(conn):
            member_id = service.get_or_create_user_by_identity(
                conn,
                provider="wechat_oa",
                provider_app_id="mock-app",
                provider_subject=MOCK_MEMBER_OPENID,
                display_name="E2E会员",
            )
            # 三段可见性(CLAUDE.md §8):登录即 member 基线,足球数据无需订阅;
            # pro/premium 已下架(0009),不再发放。付费板块 plan 落地后如需
            # E2E 覆盖付费内容,再在这里 grant 对应 plan。
            _ = admin_id  # granted_by 目前无订阅可挂

        # 选场并登记→发布→锁定正式预测(48%/27%/25%),覆盖首页 featured 的全部
        # 候选;kickoff 直接取核心库真实字段,不合成(见 _pick_seed_matches)。
        anon_leagues = _anon_league_ids(conn)
        featured, targets = _pick_seed_matches(core, anon_leagues)
        if featured is None:
            print(
                "核心库 7 天窗口内没有任何匿名可见联赛的未开赛比赛,"
                "无法构造首页 featured 种子(不会静默放宽窗口或联赛条件)",
                file=sys.stderr,
            )
            return 1
        edit_match_id = _pick_edit_target(
            core, anon_leagues, {m["match_id"] for m in targets},
        )
        if edit_match_id is None:
            print(
                "核心库没有 8 天之后、带精确开球时间的未开赛比赛,无法构造"
                "admin-predictions-edit 专用的隔离编辑目标",
                file=sys.stderr,
            )
            return 1

        def register_locked(match_id: int) -> tuple[str, str, str, str]:
            row = core.execute(
                "SELECT League_ID, kickoff_at_utc, kickoff_precision, kickoff_source"
                " FROM dim_match WHERE Match_ID=?",
                (match_id,),
            ).fetchone()
            if row is None or not row["kickoff_at_utc"]:
                raise RuntimeError(
                    f"E2E 种子比赛 {match_id} 缺精确开球时间"
                    "(窗口同源查询应已保证,不合成时间兜底)"
                )
            generated_at = pred.utc_now_iso()
            with tx(conn):
                snap_id = pred.register_snapshot(
                    conn,
                    match_id=match_id,
                    kickoff_at_utc=row["kickoff_at_utc"],
                    kickoff_precision=row["kickoff_precision"],
                    kickoff_source=row["kickoff_source"],
                    league_id=row["League_ID"],
                    model_version_id=mv,
                    home_win=0.48, draw=0.27, away_win=0.25,
                    confidence="normal",
                    status="draft",
                    generated_at=generated_at,
                    input_cutoff_at=generated_at,
                )
            with tx(conn):
                pred.publish_snapshot(conn, snap_id, actor=admin_id)
            with tx(conn):
                pred.lock_snapshot(conn, snap_id, actor=admin_id)
            return snap_id, row["kickoff_at_utc"], row["kickoff_source"], generated_at

        # E2E 隔离库中的 model_versions 行由种子新建(不是导入真实训练产物);
        # publish 门禁要求带 league_id 的快照其模型显式声明适用联赛,这里如实
        # 声明"本合成模型行适用于本次种子实际覆盖的联赛"——只写进 data/e2e,
        # 不影响真实库中 dc-baseline-1.M.2 仅限英超的声明。
        seed_match_ids = [m["match_id"] for m in targets] + [edit_match_id]
        seed_league_ids = sorted(
            {
                core.execute(
                    "SELECT League_ID FROM dim_match WHERE Match_ID=?", (mid,)
                ).fetchone()["League_ID"]
                for mid in seed_match_ids
            }
        )
        with tx(conn):
            mv = pred.get_or_create_model_version(
                conn,
                "dc-baseline-1.M.2",
                "dixon-coles+isotonic",
                applicable_league_ids=seed_league_ids,
            )
        snap_id = kickoff_utc = kickoff_source = generated_at = None
        for target in targets:
            target_snap_id, target_kickoff, target_source, target_generated = (
                register_locked(target["match_id"])
            )
            if target["match_id"] == featured["match_id"]:
                snap_id = target_snap_id
                kickoff_utc = target_kickoff
                kickoff_source = target_source
                generated_at = target_generated
        assert snap_id is not None, "featured 必须在种子目标集合内"
        edit_snap_id, _, _, _ = register_locked(edit_match_id)

        # 首页重点卡 + /matches 列表页的胜平负概率条:只需要 featured 这一场
        # 有真实可去水的赔率快照(见 _seed_win_probability_odds 顶部注释)。
        _seed_win_probability_odds(featured["match_id"])

        style_match = match_by_id(core, featured["match_id"])
        if style_match is None:
            raise RuntimeError("E2E style match missing")
        with tx(conn):
            record_team_style_profile(
                conn,
                _e2e_style_profile(style_match, generated_at),
            )

        # 顺序敏感:helpers.ts 用行首锚定正则解析;edit_* 行含 match_id=/snapshot_id=
        # 子串,主种子行必须在前。
        (E2E_DIR / "seed_info.txt").write_text(
            f"match_id={featured['match_id']}\n"
            f"snapshot_id={snap_id}\n"
            f"kickoff_at_utc={kickoff_utc}\n"
            f"kickoff_source={kickoff_source}\n"
            f"edit_match_id={edit_match_id}\n"
            f"edit_snapshot_id={edit_snap_id}\n",
            encoding="utf-8",
        )
        print(
            f"e2e seed ok: featured={featured['match_id']} snapshot={snap_id}"
            f" targets={[m['match_id'] for m in targets]}"
            f" edit={edit_match_id} member={member_id}"
        )
        return 0
    finally:
        conn.close()
        core.close()


if __name__ == "__main__":
    sys.exit(main())
