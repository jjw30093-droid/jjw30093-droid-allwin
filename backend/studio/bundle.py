"""analysis_bundle:同一份版本化分析数据驱动比赛详情页与 Creator Studio(CLAUDE.md §12)。

诚实原则:
- 所有证据/反向证据/不确定性条目都由真实数据模板化生成,数据不足时明确写入
  uncertainty,不编造;
- 概率只来自登记簿的已发布快照;无快照时 bundle 不含预测,Studio 显示空态;
- 文案不使用因果/收益承诺表述(同期事件/时间共现;不出现"必胜/稳赚"等)。
"""

import json
import sqlite3

from backend.db.util import sha256_hex, utc_now_iso
from backend.queries import matches as q_matches
from backend.queries.predictions import current_public_snapshot

BUNDLE_VERSION = "1"


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


def _fmt(x, nd=2):
    return None if x is None else round(float(x), nd)


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

    snap = current_public_snapshot(conn_platform, match_id)
    model_version = snap["model_version_id"] if snap else None
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

    if hs["played"] < 5 or as_["played"] < 5:
        uncertainty.append({"kind": "short_history",
                            "text": "至少一方近期样本不足 5 场,战绩类证据可靠性有限"})
    if snap is not None and snap["confidence"] == "low":
        uncertainty.append({"kind": "model_confidence",
                            "text": "模型对该场信心较低(如升班马缺乏历史数据),概率仅供参考"})
    if snap is not None and snap["draw"] >= 0.28:
        counter_evidence.append({"side": "draw", "kind": "draw_risk",
                                 "text": f"平局概率不低({round(snap['draw']*100)}%),分胜负的判断有相当不确定性"})
    uncertainty.append({"kind": "kickoff_precision",
                        "text": "开球时间目前只精确到比赛日(UTC),数据截止口径按比赛日 00:00 保守处理"})

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
    cooccurring_events: list[dict] = []
    if conn_odds is not None:
        try:
            xref = conn_odds.execute(
                "SELECT * FROM dim_match_xref WHERE fotmob_match_id=? AND review_status IN ('auto_ok','confirmed')",
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

    top_txt = ""
    if prediction_member:
        top_txt = (f"模型给出的概率是:主胜 {round(prediction_member['home_probability']*100)}%、"
                   f"平局 {round(prediction_member['draw_probability']*100)}%、"
                   f"客胜 {round(prediction_member['away_probability']*100)}%")

    script_sections = [
        {"id": "hook", "title": "开场", "text": f"{home['name']} 对 {away['name']},这场比赛模型怎么看?"},
        {"id": "context", "title": "背景",
         "text": f"{match['season']} 赛季第 {match['round'] or '?'} 轮,{match['date_utc']} 进行,{home['name']}坐镇主场。"},
        {"id": "data", "title": "数据",
         "text": f"{home['name']}近{hs['played']}场 {hs['w']}胜{hs['d']}平{hs['l']}负,进 {hs['goals_for']} 失 {hs['goals_against']};"
                 f"{away['name']}近{as_['played']}场 {as_['w']}胜{as_['d']}平{as_['l']}负,进 {as_['goals_for']} 失 {as_['goals_against']}。"},
        {"id": "probability", "title": "模型概率",
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
                     "home_name": home["name"], "away_name": away["name"]},
        })
    chart_specs.append({
        "id": "form_compare", "type": "form_compare", "title": "近期战绩对比",
        "data": {"home_name": home["name"], "away_name": away["name"],
                 "home": [f["result"] for f in home_form], "away": [f["result"] for f in away_form]},
    })
    if feat is not None:
        chart_specs.append({
            "id": "xg_compare", "type": "xg_compare", "title": "滚动 xG 对比(近10场)",
            "data": {"home_name": home["name"], "away_name": away["name"],
                     "home_xg_for": _fmt(feat["home_xg_for_l10"]), "home_xg_against": _fmt(feat["home_xg_against_l10"]),
                     "away_xg_for": _fmt(feat["away_xg_for_l10"]), "away_xg_against": _fmt(feat["away_xg_against_l10"])},
        })

    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "built_at": utc_now_iso(),
        "match": match,
        "data_cutoff_at": (snap["input_cutoff_at"] or snap["generated_at"]) if snap else None,
        "model_version": model_version,
        "prediction_public": prediction_public,
        "prediction_member": prediction_member,
        "evidence": evidence,
        "counter_evidence": counter_evidence,
        "uncertainty": uncertainty,
        "odds_timeline": odds_timeline,
        "cooccurring_events": cooccurring_events,
        "chart_specs": chart_specs,
        "script_sections": script_sections,
        "subtitle_cues": subtitle_cues,
        "source_notes": [
            {"kind": "data_source", "text": "比赛与统计数据来源:FotMob(自建 Bronze 层)"},
            {"kind": "model", "text": f"模型版本:{model_version or '无'};概率来自预测登记簿的已发布快照"},
            {"kind": "limitation", "text": "赔率信息(如有)为系统观察到的快照时间序列,只展示同期事件,不声称因果"},
        ],
    }
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
