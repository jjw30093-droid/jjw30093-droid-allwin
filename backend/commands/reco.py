"""「每日精选」推荐单写命令(admin 专用;每次修改留痕,CLAUDE.md §9.1 适用范围修订)。

与模型预测登记簿(commands/predictions.py)彻底分开:本模块允许编辑与结算修正,
但全部操作写 audit_logs,编辑累加 edit_count——"可修改"与"留痕"同时成立,
不伪装不可改。物理删除不提供:作废(void)保留可查。

赔率合约(2026-08-16,修复已用真实数据确认的财务结算 bug——见
backend/commands/reco_odds_contract.py 顶部注释与 backend/migrations/platform/
0014_reco_odds_contract.sql):每条腿分两类来源:

- provenance_bound:绑定了真实 match_id + 真实市场报价(source_odds +
  odds_format + snapshot_ref 齐全)。canonical_decimal_odds 由
  reco_odds_contract.to_canonical_decimal(source_odds, odds_format) 算出,
  不直接抄 source_odds(NowGoal 的 ou/ah/corners_ou 是港赔,数值常 <1,和
  十进制赔率是不同的数字含义)。
- legacy_manual:缺 match_id 或缺溯源字段的手写内容。canonical_decimal_odds
  直接等于调用方传入的 odds(向后兼容旧的手打十进制赔率入口),诚实标注
  "缺乏真实溯源",不是惩罚——手写内容本身仍然可以存在。

settle_slip() 的结算乘法只认 canonical_decimal_odds,不再使用 odds 列
(odds 列继续保留展示用途,provenance_bound 腿的 odds 列由 canonical_decimal_odds
回填,以满足既有 CHECK(odds > 1.0)且面向用户展示的是有意义的十进制数字)。

结算口径(用户已确认,对齐 miaomiaodi.vip 归档的 命中/未中/走水 三态,
2026-08-16 扩展四分之一盘口半赢半输 half_win/half_loss):
- 腿 result ∈ win(命中)/ lose(未中)/ push(走水)/ half_win(半赢)/
  half_loss(半输);
- 单腿"有效回报乘数"(标准亚洲盘口串关算法):
    win        -> canonical_decimal_odds
    half_win   -> 1.0 + (canonical_decimal_odds - 1.0) / 2.0(半仓赢,半仓走水)
    push       -> 1.0(整仓走水)
    half_loss  -> 0.5(半仓本金退回,半仓告负;**不是**整单判负——四分之一盘口
                  半输和整盘 lose 是两回事,half_loss 腿仍按 0.5 继续参与连乘)
- 单 result 判定:
    任一腿 lose      -> 整单 lose,return=0(不变,lose 规则不受其它腿
                        half_win/half_loss 影响);
    全部腿 push       -> 整单 push,return=1.0(不变);
    其余情况(无整仓 lose,至少一腿不是纯 push)
                      -> return = 全部腿"有效回报乘数"连乘积(push 腿按 1.0
                         计入,不再是只有 win 腿才乘)。整单标签按 return 相对
                         1.0 的大小判定:return>1.0 记 win(净赚,含"部分靠
                         half_win/half_loss 拉低但仍净正"的组合);
                         return<1.0 记 half_loss(净亏但不是整仓亏光,如单腿
                         half_loss 的注单);return==1.0 记 push(净不赚不亏,
                         如 win×half_loss×push 恰好回本的组合)——不把这类
                         净亏组合标成"win",对齐 CLAUDE.md 公开战绩诚实纪律。
                         这个判定只在"其余情况"分支内生效,不影响上面两条
                         已经写死的 lose/push 分支。
- 注单恒 1 单位,不涉金额(§1 禁止仓位建议);净盈亏 = return_units - 1。
"""

import sqlite3
from dataclasses import dataclass

from backend.db.util import new_uuid, utc_now_iso

from .audit import write_audit
from .reco_odds_contract import to_canonical_decimal


class RecoError(ValueError):
    pass


@dataclass(frozen=True)
class LegInput:
    match_desc: str
    market: str
    selection: str
    odds: float
    match_id: int | None = None
    # ── 赔率合约溯源(全部可选;齐全时判定 provenance_bound,见模块 docstring) ──
    source_odds: float | None = None
    odds_format: str | None = None
    provider: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    snapshot_ref: str | None = None
    observed_at: str | None = None
    line: float | None = None
    side: str | None = None
    payload_hash: str | None = None


_LEG_RESULTS = ("win", "lose", "push", "half_win", "half_loss")


def _resolve_leg(leg: LegInput) -> tuple[str, float]:
    """LegInput -> (entry_type, canonical_decimal_odds)。

    provenance_bound 的最低门槛是 match_id + source_odds + odds_format +
    snapshot_ref 全部非空——差一个都退回 legacy_manual(不能自称有真实溯源)。
    """
    has_provenance = (
        leg.match_id is not None
        and leg.source_odds is not None
        and leg.odds_format is not None
        and leg.snapshot_ref is not None
    )
    if has_provenance:
        canonical = to_canonical_decimal(leg.source_odds, leg.odds_format)
        return "provenance_bound", canonical
    return "legacy_manual", leg.odds


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
        entry_type, canonical = _resolve_leg(leg)
        if entry_type == "legacy_manual":
            if not (1.0 < leg.odds < 1000):
                raise RecoError(f"赔率非法: {leg.odds}")
        else:
            if leg.odds_format == "hk":
                if not (leg.source_odds > 0):
                    raise RecoError(f"港盘 source_odds 非法(必须 >0): {leg.source_odds}")
            elif leg.odds_format == "decimal":
                if not (leg.source_odds > 1.0):
                    raise RecoError(f"十进制 source_odds 非法(必须 >1.0): {leg.source_odds}")
            else:
                raise RecoError(f"非法 odds_format: {leg.odds_format}")
            # 最终防线:无论上面怎么算,canonical_decimal_odds 必须 >1.0 才能
            # 交给 settle_slip 做乘法(CLAUDE.md:宁可 fail-closed)。
            if not (canonical > 1.0):
                raise RecoError(f"canonical_decimal_odds 计算结果非法: {canonical}")


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
        entry_type, canonical = _resolve_leg(leg)
        conn.execute(
            "INSERT INTO reco_legs (id, slip_id, match_id, match_desc, market,"
            " selection, odds, source_odds, odds_format, canonical_decimal_odds,"
            " provider, company_id, company_name, snapshot_ref, observed_at, line,"
            " side, payload_hash, entry_type, sort_order, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_uuid(), slip_id, leg.match_id, leg.match_desc, leg.market,
             leg.selection, canonical, leg.source_odds, leg.odds_format, canonical,
             leg.provider, leg.company_id, leg.company_name, leg.snapshot_ref,
             leg.observed_at, leg.line, leg.side, leg.payload_hash, entry_type, i, now),
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


def require_provenance_bound_legs(conn: sqlite3.Connection, slip_id: str) -> None:
    """draft→published 转换前置校验(admin HTTP 写面专用,2026-08-16 新增)。

    这张单的每一条腿都必须是 entry_type='provenance_bound'(真实 match_id +
    真实盘口溯源),任何一条腿是 legacy_manual 就拒绝发布——防止无真实依据的
    手写内容进入付费/公开面,失败信息点名具体是第几条、哪场比赛/哪个选项。

    只在这一次 draft→published 转换生效,不追溯校验历史发布过的单:本函数
    只在 status 仍是 'draft' 时才检查(否则直接返回,交给 publish_slip() 自己
    的状态守卫产出既有"仅 draft 可发布"错误,不喧宾夺主)。

    刻意不写进 publish_slip() 本身——publish_slip() 是 command 层的原语,继续
    对"published + legacy_manual"这种历史/内部场景保持无感知(reco_auto_settle
    的测试需要直接用 command 层 publish 出这种老单形态来验证自动结算的跳过
    逻辑)。这条硬性校验只挂在 admin HTTP 写面
    backend/api/routes_reco.py::admin_publish_slip,由它在调用 publish_slip()
    之前先调用本函数。
    """
    row = conn.execute("SELECT status FROM reco_slips WHERE id=?", (slip_id,)).fetchone()
    if row is None or row["status"] != "draft":
        return
    legs = conn.execute(
        "SELECT match_desc, selection, entry_type FROM reco_legs"
        " WHERE slip_id=? ORDER BY sort_order",
        (slip_id,),
    ).fetchall()
    offending = [
        f"第{i + 1}条({leg['match_desc']} / {leg['selection']})"
        for i, leg in enumerate(legs)
        if leg["entry_type"] != "provenance_bound"
    ]
    if offending:
        raise RecoError(
            "以下腿缺乏真实盘口溯源(entry_type=legacy_manual),不允许发布,"
            "请从真实比赛/真实盘口重新选择后再发布: " + "; ".join(offending)
        )


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


def _leg_return_multiplier(result: str, canonical_decimal_odds: float) -> float:
    """单腿"有效回报乘数"。lose 不在这里处理——lose 直接让整单判负,
    见 settle_slip 主逻辑(§模块 docstring 的结算口径)。"""
    if result == "win":
        return canonical_decimal_odds
    if result == "half_win":
        return 1.0 + (canonical_decimal_odds - 1.0) / 2.0
    if result == "push":
        return 1.0
    if result == "half_loss":
        return 0.5
    raise RecoError(f"非法腿结果 {result}")


def settle_slip(
    conn: sqlite3.Connection,
    slip_id: str,
    leg_results: dict[str, str],
    *,
    actor: str | None,
    actor_type: str = "admin",
) -> dict:
    """逐腿录结果并汇总。published/settled 均可调用(settled 重录 = 结算修正,留痕)。

    actor_type(2026-08-16,新增,默认 'admin' 向后兼容):audit_logs.actor_type
    CHECK 只接受 admin/system/user。人工 HTTP 结算继续传 actor=真实管理员
    user_id、actor_type 用默认值 'admin'。自动结算任务(reco_auto_settle)必须
    传 actor_type='system';此时 actor 必须是 None——audit_logs.actor_user_id
    是 users(id) 的真实外键,不能塞字面量 "system" 之类的占位字符串
    (真实 sqlite3 + PRAGMA foreign_keys=ON 会直接拒绝这种 INSERT)。
    """
    row = _require_slip(conn, slip_id)
    if row["status"] not in ("published", "settled"):
        raise RecoError(f"仅 published/settled 可结算(当前 {row['status']})")
    legs = conn.execute(
        "SELECT id, canonical_decimal_odds FROM reco_legs WHERE slip_id=?"
        " ORDER BY sort_order", (slip_id,)
    ).fetchall()
    missing = [l["id"] for l in legs if l["id"] not in leg_results]
    if missing:
        raise RecoError(f"缺少腿结果: {missing}")
    for leg_id, res in leg_results.items():
        if res not in _LEG_RESULTS:
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
            canonical = l["canonical_decimal_odds"]
            if canonical is None:
                raise RecoError(f"腿 {l['id']} 缺少 canonical_decimal_odds,无法结算")
            ret *= _leg_return_multiplier(leg_results[l["id"]], canonical)
        if ret > 1.0 + 1e-9:
            slip_result = "win"
        elif ret < 1.0 - 1e-9:
            slip_result = "half_loss"
        else:
            slip_result = "push"

    now = utc_now_iso()
    resettle = row["status"] == "settled"
    conn.execute(
        "UPDATE reco_slips SET status='settled', result=?, return_units=?,"
        " settled_at=?, updated_at=?, edit_count=edit_count + ?, settle_source='manual'"
        " WHERE id=?",
        (slip_result, round(ret, 4), now, now, 1 if resettle else 0, slip_id),
    )
    write_audit(conn, "reco.settle", actor, actor_type=actor_type,
                target_type="reco_slip", target_id=slip_id,
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
