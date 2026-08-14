"""「每日精选」推荐单写命令(admin 专用;每次修改留痕,CLAUDE.md §9.1 适用范围修订)。

与模型预测登记簿(commands/predictions.py)彻底分开:本模块允许编辑与结算修正,
但全部操作写 audit_logs,编辑累加 edit_count——"可修改"与"留痕"同时成立,
不伪装不可改。物理删除不提供:作废(void)保留可查。

结算口径(用户已确认,对齐 miaomiaodi.vip 归档的 命中/未中/走水 三态):
- 腿 result ∈ win(命中)/ lose(未中)/ push(走水);
- 单 result:任一腿 lose → lose(return_units=0);全部腿 push → push(return=1,
  1 单位本金退回);其余(≥1 win 且 0 lose)→ win,return = 有效赔率乘积
  (push 腿按 1.0 计入乘积);
- 注单恒 1 单位,不涉金额(§1 禁止仓位建议);净盈亏 = return_units - 1。
"""

import sqlite3
from dataclasses import dataclass

from backend.db.util import new_uuid, utc_now_iso

from .audit import write_audit


class RecoError(ValueError):
    pass


@dataclass(frozen=True)
class LegInput:
    match_desc: str
    market: str
    selection: str
    odds: float
    match_id: int | None = None


def _require_slip(conn: sqlite3.Connection, slip_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM reco_slips WHERE id=?", (slip_id,)).fetchone()
    if row is None:
        raise RecoError("推荐单不存在")
    return row


def _validate_legs(legs: list[LegInput]) -> None:
    if not legs:
        raise RecoError("至少需要一条腿")
    for leg in legs:
        if not leg.match_desc.strip() or not leg.selection.strip() or not leg.market.strip():
            raise RecoError("腿的 match_desc/market/selection 不能为空")
        if not (1.0 < leg.odds < 1000):
            raise RecoError(f"赔率非法: {leg.odds}")


def create_slip(
    conn: sqlite3.Connection,
    *,
    slip_date: str,
    title: str,
    legs: list[LegInput],
    note: str | None,
    actor: str,
) -> str:
    if not title.strip():
        raise RecoError("标题不能为空")
    _validate_legs(legs)
    now = utc_now_iso()
    slip_id = new_uuid()
    combo = "parlay" if len(legs) > 1 else "single"
    conn.execute(
        "INSERT INTO reco_slips (id, slip_date, title, note, combo_type, status,"
        " created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
        (slip_id, slip_date, title, note, combo, actor, now, now),
    )
    _insert_legs(conn, slip_id, legs, now)
    write_audit(conn, "reco.create", actor, target_type="reco_slip", target_id=slip_id,
                detail={"slip_date": slip_date, "title": title, "legs": len(legs)})
    return slip_id


def _insert_legs(conn, slip_id: str, legs: list[LegInput], now: str) -> None:
    for i, leg in enumerate(legs):
        conn.execute(
            "INSERT INTO reco_legs (id, slip_id, match_id, match_desc, market,"
            " selection, odds, sort_order, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_uuid(), slip_id, leg.match_id, leg.match_desc, leg.market,
             leg.selection, leg.odds, i, now),
        )


def edit_slip(
    conn: sqlite3.Connection,
    slip_id: str,
    *,
    actor: str,
    title: str | None = None,
    note: str | None = None,
    slip_date: str | None = None,
    legs: list[LegInput] | None = None,
) -> None:
    """编辑(draft/published 均可;settled/voided 拒绝——结算修正走 settle 重录)。

    留痕:edit_count+1 + audit(含修改前值摘要)。legs 传入即整组替换。
    """
    row = _require_slip(conn, slip_id)
    if row["status"] in ("settled", "voided"):
        raise RecoError(f"状态 {row['status']} 不允许编辑;结算修正请重新 settle")
    now = utc_now_iso()
    before = {"title": row["title"], "note": row["note"], "slip_date": row["slip_date"]}
    conn.execute(
        "UPDATE reco_slips SET title=COALESCE(?, title), note=COALESCE(?, note),"
        " slip_date=COALESCE(?, slip_date), combo_type=CASE WHEN ? THEN"
        " (CASE WHEN ? > 1 THEN 'parlay' ELSE 'single' END) ELSE combo_type END,"
        " updated_at=?, edit_count=edit_count+1 WHERE id=?",
        (title, note, slip_date, legs is not None, len(legs or []), now, slip_id),
    )
    if legs is not None:
        _validate_legs(legs)
        conn.execute("DELETE FROM reco_legs WHERE slip_id=?", (slip_id,))
        _insert_legs(conn, slip_id, legs, now)
    write_audit(conn, "reco.edit", actor, target_type="reco_slip", target_id=slip_id,
                detail={"before": before, "legs_replaced": legs is not None})


def publish_slip(conn: sqlite3.Connection, slip_id: str, *, actor: str) -> None:
    now = utc_now_iso()
    cur = conn.execute(
        "UPDATE reco_slips SET status='published', published_at=?, updated_at=?"
        " WHERE id=? AND status='draft'",
        (now, now, slip_id),
    )
    if cur.rowcount != 1:
        row = _require_slip(conn, slip_id)
        raise RecoError(f"仅 draft 可发布(当前 {row['status']})")
    write_audit(conn, "reco.publish", actor, target_type="reco_slip", target_id=slip_id)


def settle_slip(
    conn: sqlite3.Connection,
    slip_id: str,
    leg_results: dict[str, str],
    *,
    actor: str,
) -> dict:
    """逐腿录结果并汇总。published/settled 均可调用(settled 重录 = 结算修正,留痕)。"""
    row = _require_slip(conn, slip_id)
    if row["status"] not in ("published", "settled"):
        raise RecoError(f"仅 published/settled 可结算(当前 {row['status']})")
    legs = conn.execute(
        "SELECT id, odds FROM reco_legs WHERE slip_id=? ORDER BY sort_order", (slip_id,)
    ).fetchall()
    missing = [l["id"] for l in legs if l["id"] not in leg_results]
    if missing:
        raise RecoError(f"缺少腿结果: {missing}")
    for leg_id, res in leg_results.items():
        if res not in ("win", "lose", "push"):
            raise RecoError(f"非法腿结果 {res}")
        cur = conn.execute(
            "UPDATE reco_legs SET result=? WHERE id=? AND slip_id=?",
            (res, leg_id, slip_id),
        )
        if cur.rowcount != 1:
            raise RecoError(f"腿 {leg_id} 不属于该单")

    results = [leg_results[l["id"]] for l in legs]
    if any(r == "lose" for r in results):
        slip_result, ret = "lose", 0.0
    elif all(r == "push" for r in results):
        slip_result, ret = "push", 1.0
    else:
        ret = 1.0
        for l in legs:
            if leg_results[l["id"]] == "win":
                ret *= l["odds"]
        slip_result = "win"

    now = utc_now_iso()
    resettle = row["status"] == "settled"
    conn.execute(
        "UPDATE reco_slips SET status='settled', result=?, return_units=?,"
        " settled_at=?, updated_at=?, edit_count=edit_count + ?"
        " WHERE id=?",
        (slip_result, round(ret, 4), now, now, 1 if resettle else 0, slip_id),
    )
    write_audit(conn, "reco.settle", actor, target_type="reco_slip", target_id=slip_id,
                detail={"result": slip_result, "return_units": round(ret, 4),
                        "resettle": resettle,
                        "prev_result": row["result"] if resettle else None})
    return {"result": slip_result, "return_units": round(ret, 4)}


def void_slip(conn: sqlite3.Connection, slip_id: str, *, actor: str, reason: str) -> None:
    """作废:保留可查、战绩页单列展示,绝不物理删除(否则等价于删失败记录)。"""
    if not reason.strip():
        raise RecoError("作废必须填写原因")
    row = _require_slip(conn, slip_id)
    if row["status"] == "voided":
        raise RecoError("已是作废状态")
    now = utc_now_iso()
    conn.execute(
        "UPDATE reco_slips SET status='voided', updated_at=? WHERE id=?",
        (now, slip_id),
    )
    write_audit(conn, "reco.void", actor, target_type="reco_slip", target_id=slip_id,
                detail={"reason": reason, "prev_status": row["status"]})
