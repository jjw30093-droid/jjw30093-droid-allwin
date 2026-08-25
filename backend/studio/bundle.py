"""analysis_bundle:同一份版本化分析数据驱动比赛详情页与 Creator Studio(CLAUDE.md §12)。

诚实原则:
- 所有证据/反向证据/不确定性条目都由真实数据模板化生成,数据不足时明确写入
  uncertainty,不编造;
- 概率只来自登记簿的已发布快照;无快照时 bundle 不含预测,Studio 显示空态;
- 文案不使用因果/收益承诺表述(同期事件/时间共现;不出现"必胜/稳赚"等)。
"""

import json
import sqlite3
from datetime import datetime, timezone

from backend.db.util import normalize_exact_kickoff, sha256_hex, utc_now_iso
from backend.queries import matches as q_matches
from backend.queries.odds import legacy_summary_points
from backend.studio.team_style import (
    DOUYIN_SAFE_PROFILE,
    build_douyin_safe_profile,
    latest_team_style_profile,
)

BUNDLE_VERSION = "1"

# probability_source 只在内部流转使用 MARKET_BASELINE/MODEL/UNAVAILABLE 这三个
# 英文常量;任何拼进用户可见文案的地方都必须先查这张表换成中文,不能把原始
# 枚举值原样输出(CLAUDE.md §11.2 明确禁止内部枚举值直接出现在用户界面)。
PROBABILITY_SOURCE_LABELS = {
    "MARKET_BASELINE": "市场基线",
    "MODEL": "模型",
    "UNAVAILABLE": "暂无",
}


def _form_summary(form: list[dict]) -> dict:
    w = sum(1 for f in form if f["result"] == "W")
    d = sum(1 for f in form if f["result"] == "D")
    l = sum(1 for f in form if f["result"] == "L")
    gf = sum(f["goals_for"] for f in form)
    ga = sum(f["goals_against"] for f in form)
    return {"played": len(form), "w": w, "d": d, "l": l, "goals_for": gf, "goals_against": ga}


def _features_row(conn_core: sqlite3.Connection, match_id: int):
    try:
        return conn_core.execute(
            "SELECT * FROM int_match_features WHERE match_id=?", (match_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _season_xg_row(
    conn_core: sqlite3.Connection, league_id: int, season: str, team_id: int
):
    try:
        return conn_core.execute(
            """SELECT played,xg,xg_conceded,x_points FROM fact_league_table
               WHERE League_ID=? AND Season=? AND table_type='xg' AND Team_ID=?
               LIMIT 1""",
            (league_id, season, team_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _rest_days(
    conn_core: sqlite3.Connection, team_id: int, kickoff_at_utc: str | None
) -> float | None:
    if not kickoff_at_utc:
        return None
    try:
        target = datetime.fromisoformat(kickoff_at_utc.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
        row = conn_core.execute(
            """SELECT kickoff_at_utc FROM dim_match
               WHERE status='Finish' AND kickoff_at_utc < ?
                 AND (Home_Team_ID=? OR Away_Team_ID=?)
               ORDER BY kickoff_at_utc DESC LIMIT 1""",
            (kickoff_at_utc, team_id, team_id),
        ).fetchone()
        if row is None or not row["kickoff_at_utc"]:
            return None
        previous = datetime.fromisoformat(row["kickoff_at_utc"].replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
        return round((target - previous).total_seconds() / 86400, 1)
    except (ValueError, TypeError, sqlite3.OperationalError):
        return None


def _fmt(x, nd=2):
    return None if x is None else round(float(x), nd)


def _kickoff_uncertainty(conn_core: sqlite3.Connection, match_id: int) -> dict | None:
    """按**完整 provenance**(kickoff_at_utc + precision + source)判定是否需要开球精度提示。

    唯一真源 normalize_exact_kickoff:只有 exact + 非空来源 + 显式时区 + 合法时间 才算
    可信精确,返回 None(不提示)。只看 kickoff_precision 字段是不够的——exact + 缺来源
    / naive / 非法时间 / 纯日期冒充 都必须提示(否则 Studio 会把不可信的开球时间当真)。

    文案按精度如实区分,不统一谎称"只精确到比赛日":
      - date_only:确实只知道比赛日 → "只精确到比赛日";
      - 其余(unknown / 缺来源 / naive / 非法 / 纯日期冒充 exact)→ 更通用的
        "缺少可验证的精确开球时间"。

    独立查询 dim_match(不改动 queries/matches.py 的公开返回结构),只服务 bundle 内部
    诚实提示,不影响 /api/v1/matches/{id} 的 API 契约。
    """
    try:
        row = conn_core.execute(
            "SELECT kickoff_at_utc, kickoff_precision, kickoff_source FROM dim_match WHERE Match_ID=?",
            (match_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    ko = row["kickoff_at_utc"] if row else None
    precision = row["kickoff_precision"] if row else None
    source = row["kickoff_source"] if row else None
    if normalize_exact_kickoff(ko, precision, source) is not None:
        return None
    if precision == "date_only":
        text = "该场开球时间只精确到比赛日(UTC),赛前判定与数据截止口径按比赛日保守处理"
    else:
        text = "该场缺少可验证的精确开球时间,赛前判定与数据截止口径按比赛日保守处理"
    return {"kind": "kickoff_precision", "text": text}


def build_analysis_bundle(
    conn_core: sqlite3.Connection,
    conn_platform: sqlite3.Connection,
    conn_odds: sqlite3.Connection | None,
    match_id: int,
) -> dict | None:
    match = q_matches.match_by_id(conn_core, match_id)
    if match is None:
        return None
    home, away = match["home"], match["away"]
    home_form = q_matches.recent_form(conn_core, home["team_id"], match["date_utc"])
    away_form = q_matches.recent_form(conn_core, away["team_id"], match["date_utc"])
    hs, as_ = _form_summary(home_form), _form_summary(away_form)

    # 2026-08-25:WDL 模型与正式预测登记簿已废弃(胜率改由 bet365 赔率直接
    # 派生,见 CLAUDE.md 决策);analysis bundle 不再查询 prediction_snapshots,
    # snap 恒为 None,下方 prediction_public/prediction_member 恒为 None。
    snap = None
    model_version = snap["model_version_id"] if snap else None
    model_row = (
        conn_platform.execute(
            "SELECT algorithm FROM model_versions WHERE id=?", (model_version,)
        ).fetchone()
        if model_version
        else None
    )
    probability_source = (
        "MARKET_BASELINE"
        if model_row is not None and model_row["algorithm"] == "market_baseline"
        else ("MODEL" if snap is not None else "UNAVAILABLE")
    )
    feat = _features_row(conn_core, match_id)

    evidence: list[dict] = []
    counter_evidence: list[dict] = []
    uncertainty: list[dict] = []

    if hs["played"] >= 3:
        if hs["w"] >= max(2, hs["played"] - 2):
            evidence.append({"side": "home", "kind": "form",
                             "text": f"{home['name']}近{hs['played']}场 {hs['w']}胜{hs['d']}平{hs['l']}负,近期战绩占优"})
        elif hs["l"] >= max(2, hs["played"] - 2):
            counter_evidence.append({"side": "home", "kind": "form",
                                     "text": f"{home['name']}近{hs['played']}场仅 {hs['w']}胜,状态存疑"})
    if as_["played"] >= 3:
        if as_["w"] >= max(2, as_["played"] - 2):
            counter_evidence.append({"side": "away", "kind": "form",
                                     "text": f"{away['name']}近{as_['played']}场 {as_['w']}胜{as_['d']}平{as_['l']}负,不容小视"})
        elif as_["l"] >= max(2, as_["played"] - 2):
            evidence.append({"side": "away", "kind": "form",
                             "text": f"{away['name']}近{as_['played']}场 {as_['l']}负,客队近期状态低迷"})

    if feat is not None:
        hxg, axg = feat["home_xg_for_l10"], feat["away_xg_for_l10"]
        if hxg is not None and axg is not None:
            txt = f"近10场滚动 xG:{home['name']} {_fmt(hxg)} vs {away['name']} {_fmt(axg)}"
            (evidence if hxg >= axg else counter_evidence).append(
                {"side": "home" if hxg >= axg else "away", "kind": "xg", "text": txt}
            )
    else:
        uncertainty.append({"kind": "features_missing",
                            "text": "该场比赛暂无赛前特征数据(滚动 xG 等),分析主要依赖近期战绩"})

    home_xg = _season_xg_row(conn_core, match["league_id"], match["season"], home["team_id"])
    away_xg = _season_xg_row(conn_core, match["league_id"], match["season"], away["team_id"])
    if (
        home_xg is not None
        and away_xg is not None
        and home_xg["xg"] is not None
        and away_xg["xg"] is not None
    ):
        evidence.append(
            {
                "side": "home" if home_xg["xg"] >= away_xg["xg"] else "away",
                "kind": "season_xg",
                "text": (
                    f"当前赛季累计 xG:{home['name']} {_fmt(home_xg['xg'])}"
                    f"（xGA {_fmt(home_xg['xg_conceded'])}） vs "
                    f"{away['name']} {_fmt(away_xg['xg'])}"
                    f"（xGA {_fmt(away_xg['xg_conceded'])}）"
                ),
            }
        )
    else:
        uncertainty.append(
            {"kind": "season_xg_unavailable", "text": "当前赛季 xG/xGA 数据不可用，未以 0 填充"}
        )

    home_rest = _rest_days(conn_core, home["team_id"], match.get("kickoff_at_utc"))
    away_rest = _rest_days(conn_core, away["team_id"], match.get("kickoff_at_utc"))
    if home_rest is not None and away_rest is not None:
        evidence.append(
            {
                "side": "home" if home_rest >= away_rest else "away",
                "kind": "rest",
                "text": f"开球前休息时间:{home['name']} {home_rest} 天，{away['name']} {away_rest} 天",
            }
        )

    if hs["played"] < 5 or as_["played"] < 5:
        uncertainty.append({"kind": "short_history",
                            "text": "至少一方近期样本不足 5 场,战绩类证据可靠性有限"})
    if snap is not None and snap["confidence"] == "low":
        uncertainty.append({"kind": "model_confidence",
                            "text": "模型对该场信心较低(如升班马缺乏历史数据),概率仅供参考"})
    if snap is not None and snap["draw"] >= 0.28:
        counter_evidence.append({"side": "draw", "kind": "draw_risk",
                                 "text": f"平局概率不低({round(snap['draw']*100)}%),分胜负的判断有相当不确定性"})
    # 数据质量提示(§6.2.1):按完整 provenance 判定(normalize_exact_kickoff),
    # 不能只看 kickoff_precision 字段——exact + 缺来源 / naive / 非法时间也必须提示。
    ko_uncertainty = _kickoff_uncertainty(conn_core, match_id)
    if ko_uncertainty is not None:
        uncertainty.append(ko_uncertainty)

    prediction_public = prediction_member = None
    if snap is not None and snap["status"] in ("published", "locked"):
        probs = {"home": snap["home_win"], "draw": snap["draw"], "away": snap["away_win"]}
        top = max(probs, key=probs.get)
        prediction_public = {"top_outcome": top, "top_probability": round(probs[top], 2)}
        prediction_member = {
            "home_probability": round(snap["home_win"], 4),
            "draw_probability": round(snap["draw"], 4),
            "away_probability": round(snap["away_win"], 4),
            "expected_home_goals": _fmt(snap["expected_home_goals"]),
            "expected_away_goals": _fmt(snap["expected_away_goals"]),
            "status": snap["status"],
            "prediction_hash": snap["prediction_hash"],
        }

    odds_timeline: list[dict] = []
    odds_summary_points: list[dict] = []
    cooccurring_events: list[dict] = []
    if conn_odds is not None:
        try:
            # provider='nowgoal':下游 bronze_ng_odds_snap 是 nowgoal 形状的表,
            # 无 provider 列;不过滤的话同一场比赛若同时有 kbisai xref,
            # .fetchone() 可能拿到错误 provider 的行(见 routes_public.py 同款注释)。
            xref = conn_odds.execute(
                "SELECT * FROM dim_match_xref WHERE fotmob_match_id=? AND provider='nowgoal'"
                " AND review_status IN ('auto_ok','confirmed')",
                (match_id,),
            ).fetchone()
            if xref:
                for r in conn_odds.execute(
                    """SELECT market, company_name, payload_json, observed_at FROM bronze_ng_odds_snap
                       WHERE provider_match_id=? ORDER BY observed_at""",
                    (xref["provider_match_id"],),
                ):
                    odds_timeline.append(
                        {"market": r["market"], "company": r["company_name"],
                         "observed_at": r["observed_at"], "payload": json.loads(r["payload_json"])}
                    )
                for r in conn_odds.execute(
                    """SELECT c.delta_seconds, om.market, om.field, om.prev_value, om.new_value,
                              om.moved_at, em.event_type, em.detail_json
                       FROM gold_move_cooccurrence c
                       JOIN silver_odds_moves om ON om.id=c.odds_move_id
                       JOIN silver_event_moves em ON em.id=c.event_move_id
                       WHERE c.fotmob_match_id=? ORDER BY om.moved_at""",
                    (match_id,),
                ):
                    cooccurring_events.append(dict(r))
        except sqlite3.OperationalError:
            pass
        if not odds_timeline:
            # 完整时间线缺席时回退旧资产两点摘要(审计 B6:此前 bundle 只认
            # xref→bronze_ng_odds_snap 一条路,对 8,336 场 legacy-only 比赛
            # 返回空 odds_timeline,而同一场 /odds 端点能给出真实点位)。
            # 两点摘要无观测时间戳,进独立的 odds_summary_points,
            # 绝不混入 odds_timeline(§6.2:不伪造 observed_at)。
            # 2026-08-16 起公开路由不再做任何权益投影,bundle 生成的内容
            # 直接下发(除"每日精选"外全站比赛内容全部免费)。
            odds_summary_points = legacy_summary_points(conn_odds, match_id)

    odds_coverage_tier = (
        "full_timeline"
        if odds_timeline
        else "open_close_only"
        if odds_summary_points
        else "none"
    )

    top_txt = ""
    if prediction_member:
        source_label = "市场 1X2 去水基线" if probability_source == "MARKET_BASELINE" else "模型"
        top_txt = (f"{source_label}概率是:主胜 {round(prediction_member['home_probability']*100)}%、"
                   f"平局 {round(prediction_member['draw_probability']*100)}%、"
                   f"客胜 {round(prediction_member['away_probability']*100)}%")

    probability_title = "市场基线" if probability_source == "MARKET_BASELINE" else "模型概率"
    script_sections = [
        {
            "id": "hook",
            "title": "开场",
            "text": f"{home['name']} 对 {away['name']},这场比赛的{probability_title}怎么看?",
        },
        {"id": "context", "title": "背景",
         "text": f"{match['season']} 赛季第 {match['round'] or '?'} 轮,{match['date_utc']} 进行,{home['name']}坐镇主场。"},
        {"id": "data", "title": "数据",
         "text": f"{home['name']}近{hs['played']}场 {hs['w']}胜{hs['d']}平{hs['l']}负,进 {hs['goals_for']} 失 {hs['goals_against']};"
                 f"{away['name']}近{as_['played']}场 {as_['w']}胜{as_['d']}平{as_['l']}负,进 {as_['goals_for']} 失 {as_['goals_against']}。"},
        {"id": "probability", "title": probability_title,
         "text": top_txt or "该场比赛暂无已发布的模型概率。"},
        {"id": "risk", "title": "风险与反向证据",
         "text": "。".join(c["text"] for c in (counter_evidence + uncertainty)[:3]) or "本场无特别突出的反向证据。"},
        {"id": "outro", "title": "结尾",
         "text": "以上是赛前数据视角,概率不是定论,比赛存在不确定性。完整方法与历史记录见网站「模型与战绩」页。"},
    ]
    subtitle_cues = []
    t = 0.0
    for sec in script_sections:
        for sentence in filter(None, (s.strip() for s in sec["text"].replace("。", "。|").split("|"))):
            dur = max(2.0, min(6.0, len(sentence) * 0.18))
            subtitle_cues.append({"start": round(t, 1), "end": round(t + dur, 1), "text": sentence})
            t += dur

    chart_specs = []
    if prediction_member:
        chart_specs.append({
            "id": "prob_bar", "type": "probability_bar", "title": "胜平负概率",
            "data": {"home": prediction_member["home_probability"],
                     "draw": prediction_member["draw_probability"],
                     "away": prediction_member["away_probability"],
                     "probability_source": probability_source,
                     "home_name": home["name"], "away_name": away["name"]},
        })
    if feat is not None:
        chart_specs.append({
            "id": "xg_compare", "type": "xg_compare", "title": "滚动 xG 对比(近10场)",
            "data": {"home_name": home["name"], "away_name": away["name"],
                     "home_xg_for": _fmt(feat["home_xg_for_l10"]), "home_xg_against": _fmt(feat["home_xg_against_l10"]),
                     "away_xg_for": _fmt(feat["away_xg_for_l10"]), "away_xg_against": _fmt(feat["away_xg_against_l10"])},
        })
    shot_map = q_matches.recent_shot_map_spec(conn_core, match_id, window=5)
    if shot_map is not None:
        chart_specs.append({
            "id": "recent_shot_map", "type": "shot_map_explorer", "title": "最近 5 场射门分布",
            "data": shot_map,
        })

    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "built_at": utc_now_iso(),
        "match": match,
        "data_cutoff_at": (snap["input_cutoff_at"] or snap["generated_at"]) if snap else None,
        "model_version": model_version,
        "probability_source": probability_source,
        "prediction_public": prediction_public,
        "prediction_member": prediction_member,
        "evidence": evidence,
        "counter_evidence": counter_evidence,
        "uncertainty": uncertainty,
        "odds_timeline": odds_timeline,
        "odds_coverage_tier": odds_coverage_tier,
        "odds_summary_points": odds_summary_points or None,
        "cooccurring_events": cooccurring_events,
        "chart_specs": chart_specs,
        "script_sections": script_sections,
        "subtitle_cues": subtitle_cues,
        "source_notes": [
            {"kind": "data_source", "text": "比赛与统计数据来源:FotMob(自建 Bronze 层)"},
            {
                "kind": "probability_source",
                # 三态各自独立成句,不能用二元 else 兜底——UNAVAILABLE 落进
                # "概率来自已发布快照"分支曾经是自相矛盾的真 bug(既说
                # UNAVAILABLE 又说有快照,2026-08-12 审计发现)。
                # 标签一律查 PROBABILITY_SOURCE_LABELS,不直接拼原始枚举值
                # (生产实测发现 UNAVAILABLE 原样显示给了用户,2026-08-23 修复)。
                "text": (
                    f"概率来源：{PROBABILITY_SOURCE_LABELS[probability_source]}；"
                    f"版本：{model_version or '无'}。"
                    + {
                        "MARKET_BASELINE": "该值是 1X2 赔率去水结果，不是自有模型输出",
                        "MODEL": "概率来自预测登记簿的已发布快照",
                        "UNAVAILABLE": "这场比赛暂无已发布的预测快照，不展示概率",
                    }[probability_source]
                ),
            },
            {"kind": "limitation", "text": "赔率信息(如有)为系统观察到的快照时间序列,只展示同期事件,不声称因果"},
        ],
    }
    team_style_profile = latest_team_style_profile(conn_platform, match_id)
    bundle["team_style_profile"] = team_style_profile
    bundle["social_profiles"] = {}
    if team_style_profile is not None:
        bundle["social_profiles"][DOUYIN_SAFE_PROFILE] = build_douyin_safe_profile(
            team_style_profile,
            match,
            uncertainty,
        )
    canonical = json.dumps({k: v for k, v in bundle.items() if k != "built_at"},
                           sort_keys=True, ensure_ascii=False)
    bundle["bundle_hash"] = sha256_hex(canonical)
    return bundle


def render_txt(bundle: dict, overrides: dict) -> str:
    sections = overrides.get("script_sections") or bundle["script_sections"]
    lines = [f"# {overrides.get('title') or bundle['match']['home']['name'] + ' vs ' + bundle['match']['away']['name']}"]
    lines.append(f"数据截止:{bundle['data_cutoff_at'] or '未知'} | 模型版本:{bundle['model_version'] or '无'}")
    for sec in sections:
        lines.append(f"\n## {sec['title']}\n{sec['text']}")
    return "\n".join(lines) + "\n"


def render_srt(bundle: dict, overrides: dict) -> str:
    cues = overrides.get("subtitle_cues") or bundle["subtitle_cues"]

    def ts(sec: float) -> str:
        h = int(sec // 3600)
        m = int(sec % 3600 // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out = []
    for i, cue in enumerate(cues, 1):
        out.append(f"{i}\n{ts(cue['start'])} --> {ts(cue['end'])}\n{cue['text']}\n")
    return "\n".join(out)
