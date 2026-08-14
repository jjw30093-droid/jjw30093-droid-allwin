"""实体解析:NowGoal 队名/比赛 → FotMob canonical ID(写 odds.db,core 只读)。

- seed_team_aliases:从 allwin.db(core,只读)的 dim_match 队名 + dim_team_i18n
  中英文名,种 dim_team_alias(canonical_team_id = FotMob Team_ID)。幂等。
- resolve_match:对 NowGoal 日程行做主客队匹配(正/反两个方向),写 dim_match_xref。
  **候选召回**用别名 ∪ dim_team_xref 直查的并集给分(历史 provider xref 帮助召回),
  但**最终 auto_ok 身份证明只认 alias-only 名称/方向验证**:并集足以召回,不足以确认。
  高置信(≥0.9 且方向唯一)且通过下述全部门禁 → 'auto_ok',否则 'needs_review';
  绝不静默 verified=1(verified 只能由管理员 confirm 置位)。
- kickoff 差值校验(CLAUDE.md §6.1 / §6.2.1;P0-B 收口):auto_ok 要求**双边都有可
  验证的精确开球时刻**——core 侧必须通过统一验证器 `normalize_exact_kickoff`
  (kickoff_precision=='exact' ∧ 有非空真实来源 ∧ 时间带显式时区可解析),NowGoal 侧
  kickoff 必须真实包含时间部分(`_parse_nowgoal_wall_clock`,拒绝把纯日期当午夜),
  |diff| ≤ 30 分钟。任一侧不满足 → kickoff_diff_seconds 记 NULL 且强制 needs_review
  (不再因 diff is None 放行;也不再因 core 侧缺来源而静默放行)。NowGoal 日程按
  timezone=8 请求决定**返回哪个北京日期的日程**(用于 `date=` 查询参数选日,
  已用真实数据验证:FotMob kickoff→Asia/Shanghai 换算出的北京日期确实能查到目标场次),
  但**行内 kickoff 字段本身是 UTC,不需要再转换**——2026-07-21 用真实比赛
  交叉验证发现:Match_ID=5107547(Västerås SK vs Örgryte,FotMob 真实
  kickoff_at_utc='2026-07-24T17:00:00Z')对应的 NowGoal titan_id=2912218 行内
  kickoff 原始值为 '2026-07-24 17:00:00'——与 UTC 时刻逐位相同,而不是北京时间
  (北京时间应为次日 '2026-07-25 01:00:00')。此前假设"-8h 转 UTC"未经真实比对,
  仅按请求参数名推测,现有真实证据(confidence=1.0 身份唯一匹配 + kickoff_diff
  恰为 -28800 秒即整 8 小时,不是随机噪声)判定该假设有误,已改正。
- team xref 冲突显式化 + 事务一致:比赛 xref 的占用检查与 team xref 冲突检查
  都在同一个 `BEGIN IMMEDIATE` 事务内完成(消除检查-写入之间的竞态窗口)。冲突则
  整场降级 needs_review 且不写任何 team xref;相同映射幂等复用,不覆盖既有
  verified/manual。
- 新映射 auto_ok 必须**同时**满足(而非二选一):(1) 本轮 home_name/away_name 经
  dim_team_alias 独立、唯一解析到候选比赛预期 canonical(alias-only,历史 provider xref
  不单独充当身份证明——否则错误队名会被历史 xref 掩盖);(2) 主客方向唯一;(3) kickoff
  provenance 合格且差值 ≤30 分钟;(4) provider_home_id/provider_away_id 存在且互异、pairs
  内部自洽;(5) provider ID 对应既有 team xref 不存在时允许本次严格通过后创建、已存在时
  必须与预期 canonical 完全一致;(6) 比赛 xref 与 team xref 无冲突。任一不满足 →
  needs_review、verified=0、不写任何 dim_team_xref、返回明确 validation_errors
  (provider_team_id_missing / provider_team_ids_not_distinct / provider_team_internal_conflict
  / home_team_name_mismatch / away_team_name_mismatch),绝不靠 INSERT OR IGNORE 吞半映射。
- existing auto_ok 每次重新遇到都在**单一 BEGIN IMMEDIATE 事务**内重新验证(match xref
  权威状态、alias、dim_team_xref 读取与最终保留/降级判定同事务;保持 auto_ok 的成功路径
  也在事务内做最终复核,不提前 return):(1) 本轮队名 alias-only 唯一匹配预期方向;
  (2) kickoff 仍合格;(3) provider ID 存在、互异;(4) 两个 provider ID 对应的 dim_team_xref
  行**真实存在**且 (5) 都指向按 home_away_inverted 计算的预期 canonical。"查不到行"
  (`provider_team_xref_missing`)与"存在但 canonical 冲突"(team_conflicts)都降级但用不同
  信号区分。重验证**只允许保留 auto_ok 或原子降级 needs_review**,绝不静默创建/补写/覆盖
  team xref;confirmed/verified=1/rejected 的既有映射永不被自动改写或复活;needs_review 不
  被静默升级。
"""

import re
import sqlite3
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from backend.db.connections import tx
from backend.db.util import normalize_exact_kickoff, parse_strict_utc, utc_now_iso

PROVIDER = "nowgoal"
AUTO_OK_THRESHOLD = 0.9
KICKOFF_TOLERANCE_SECONDS = 1800   # ±30 分钟

# NowGoal 墙上时间专用格式:必须真实含时间部分('YYYY-MM-DD HH:MM[:SS]'),
# 纯日期('YYYY-MM-DD')不匹配——避免 datetime.fromisoformat 把纯日期悄悄当成当天午夜。
_NOWGOAL_WALL_CLOCK_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}(:\d{2})?$")


def _norm(name: str | None) -> str:
    """别名归一:小写 + 压缩空白(中文名不受影响)。"""
    if not name:
        return ""
    return " ".join(str(name).lower().split())


def seed_team_aliases(conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection) -> int:
    """从 core 种别名(INSERT OR IGNORE 幂等),返回本次新增条数。

    覆盖 dim_match 全部联赛的队名(不硬编码英超)+ i18n 中英文名。
    """
    aliases: set[tuple[int, str, str]] = set()

    for home_col, away_col in (("Home_Team_ID", "Home_Team_Name"), ("Away_Team_ID", "Away_Team_Name")):
        for row in conn_core.execute(
            f"SELECT DISTINCT {home_col} AS tid, {away_col} AS name FROM dim_match"
            " WHERE tid IS NOT NULL AND name IS NOT NULL",
        ):
            alias = _norm(row["name"])
            if alias:
                aliases.add((int(row["tid"]), alias, "dim_match"))

    for row in conn_core.execute(
        "SELECT Team_ID, name_en, name_zh FROM dim_team_i18n WHERE Team_ID IS NOT NULL"
    ):
        for name in (row["name_en"], row["name_zh"]):
            alias = _norm(name)
            if alias:
                aliases.add((int(row["Team_ID"]), alias, "dim_team_i18n"))

    now = utc_now_iso()
    added = 0
    with tx(conn_odds):
        for team_id, alias, source in sorted(aliases):
            cur = conn_odds.execute(
                "INSERT OR IGNORE INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                " VALUES (?, ?, ?, ?)",
                (team_id, alias, source, now),
            )
            added += cur.rowcount
    return added


def _ascii_fold(text: str) -> str:
    """去变音符折叠:NFKD 分解后剔除组合记号,再走 _norm(小写+压空白)。

    'Häcken'→'hacken'、'Mjällby'→'mjallby'、'Deportivo A Coruña'→'deportivo a coruna'。
    NowGoal 发 ASCII 队名,FotMob 发带变音符原文;auto-seed 的别名保留变音符(经 _norm),
    因此需要**额外**播一份折叠形式,否则新联赛(荷甲/葡超/巴甲/欧协联)整批落 needs_review。
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _norm(stripped)


def seed_ascii_fold_aliases(conn_odds: sqlite3.Connection) -> dict:
    """给带变音符的既有别名补一份 ASCII 折叠别名(source='ascii_fold'),fail-closed。

    撞名审计:若某折叠串映射到 ≥2 个不同 canonical_team_id,**整串拒绝**(不写),
    列入 rejected 供人工复核——绝不制造歧义别名(把"零撞名"从事后声称变成事前门禁)。
    只对"折叠后与原串不同"(即真的含变音符/兼容字符)的别名生成,幂等 INSERT OR IGNORE。
    返回 {added, rejected:[(fold, [team_ids])], candidates}。
    """
    rows = conn_odds.execute(
        "SELECT canonical_team_id, alias FROM dim_team_alias"
    ).fetchall()
    # 已存在的别名串(避免把已有 ASCII 串当成"新折叠"重复写)
    existing_aliases = {r["alias"] for r in rows}
    # 折叠串 → 该串对应的 canonical 集合
    fold_to_ids: dict[str, set[int]] = {}
    for r in rows:
        folded = _ascii_fold(r["alias"])
        if not folded or folded == r["alias"]:
            continue                     # 无变音符,折叠无意义
        fold_to_ids.setdefault(folded, set()).add(int(r["canonical_team_id"]))

    now = utc_now_iso()
    added = 0
    rejected = []
    with tx(conn_odds):
        for folded, ids in sorted(fold_to_ids.items()):
            if len(ids) >= 2:
                rejected.append((folded, sorted(ids)))
                continue                 # 撞名:整串拒绝,不写
            if folded in existing_aliases:
                # 该 ASCII 串已作为某别名存在——只有当它指向同一 canonical 才安全;
                # 指向不同则同样是撞名风险,交撞名审计(上面的 len>=2 已覆盖跨串情形,
                # 这里防"折叠串恰好等于另一队的既有 ASCII 别名")
                other = _alias_team_ids(conn_odds, folded)
                if other and other != ids:
                    rejected.append((folded, sorted(ids | other)))
                    continue
            (team_id,) = tuple(ids)
            cur = conn_odds.execute(
                "INSERT OR IGNORE INTO dim_team_alias (canonical_team_id, alias, source, created_at)"
                " VALUES (?, ?, 'ascii_fold', ?)",
                (team_id, folded, now),
            )
            added += cur.rowcount
    return {"added": added, "rejected": rejected, "candidates": len(fold_to_ids)}


def _alias_team_ids(conn_odds: sqlite3.Connection, name: str) -> set[int]:
    alias = _norm(name)
    if not alias:
        return set()
    return {
        int(r[0])
        for r in conn_odds.execute(
            "SELECT canonical_team_id FROM dim_team_alias WHERE alias=?", (alias,)
        )
    }


def _team_xref_ids(conn_odds: sqlite3.Connection, provider_team_id) -> set[int]:
    """dim_team_xref 直查:此前 auto_ok 解析确认过的 NowGoal 球队 id → canonical。"""
    if not provider_team_id:
        return set()
    return {
        int(r[0])
        for r in conn_odds.execute(
            "SELECT canonical_team_id FROM dim_team_xref WHERE provider=? AND provider_team_id=?",
            (PROVIDER, str(provider_team_id)),
        )
    }


def _team_xref_pairs(schedule_row: dict, cand: sqlite3.Row, inverted: int) -> list[tuple[str, int]]:
    """本次映射蕴含的 (provider_team_id, canonical_team_id) 对(已按主客反转换算)。"""
    core_home, core_away = int(cand["Home_Team_ID"]), int(cand["Away_Team_ID"])
    pairs: list[tuple[str, int]] = []
    ph, pa = schedule_row.get("provider_home_id"), schedule_row.get("provider_away_id")
    if ph:
        pairs.append((str(ph), core_away if inverted else core_home))
    if pa:
        pairs.append((str(pa), core_home if inverted else core_away))
    return pairs


def _expected_canonicals(cand: sqlite3.Row, inverted: int) -> tuple[int, int]:
    """按主客反转,返回 (NowGoal 主队预期 canonical, NowGoal 客队预期 canonical)。

    inverted=0:NG 主 → canonical 主,NG 客 → canonical 客;
    inverted=1:NG 主 → canonical 客,NG 客 → canonical 主。
    """
    core_home, core_away = int(cand["Home_Team_ID"]), int(cand["Away_Team_ID"])
    if inverted:
        return core_away, core_home
    return core_home, core_away


def _name_resolves_to(conn_odds: sqlite3.Connection, name, expected_canonical: int) -> bool:
    """队名经 dim_team_alias 是否**唯一且明确**解析到预期 canonical。

    只用别名(不掺入 provider team xref):重验证要检验的正是本次 schedule_row 的
    队名本身,若掺入 provider ID 的历史 xref,队名被改错也会被旧 xref 掩盖(反例 B)。
    要求解析结果恰好为 {expected}:既保证命中预期,又排除命中对手 / 多 canonical 歧义。
    """
    if not name:
        return False
    return _alias_team_ids(conn_odds, name) == {int(expected_canonical)}


def _new_pair_validation_errors(
    schedule_row: dict, pairs: list[tuple[str, int]]
) -> list[str]:
    """新映射 auto_ok 前对 provider team 身份的完整性/自洽校验(不触库)。

    返回 validation_errors(空表示通过):
      - provider_team_id_missing:缺主队或客队 provider ID(会造成半映射);
      - provider_team_ids_not_distinct:主客 provider ID 相同;
      - provider_team_internal_conflict:同一 provider ID 指向多个 canonical,
        或主客两条 pair 折叠到同一个 canonical。
    """
    errors: list[str] = []
    ph = schedule_row.get("provider_home_id")
    pa = schedule_row.get("provider_away_id")
    if not ph or not pa:
        errors.append("provider_team_id_missing")
        return errors                      # 身份不全,后续 pair 校验无意义
    if str(ph) == str(pa):
        errors.append("provider_team_ids_not_distinct")
    by_provider: dict[str, set[int]] = {}
    for pid, canon in pairs:
        by_provider.setdefault(pid, set()).add(int(canon))
    if any(len(cs) > 1 for cs in by_provider.values()):
        errors.append("provider_team_internal_conflict")
    canons = [int(canon) for _, canon in pairs]
    if len(pairs) == 2 and len(set(canons)) != len(canons):
        errors.append("provider_team_internal_conflict")
    return list(dict.fromkeys(errors))     # 去重保序


def _team_xref_conflicts(conn_odds: sqlite3.Connection, pairs: list[tuple[str, int]]) -> list[dict]:
    """检查本次映射与既有 dim_team_xref 是否存在语义冲突(同 provider_team_id → 不同 canonical)。

    返回冲突明细列表(空表示无冲突)。相同 canonical 视为幂等复用,不算冲突,
    也不需要改写既有行(既有可能是 verified/manual,绝不覆盖)。
    """
    conflicts: list[dict] = []
    for provider_team_id, canonical_team_id in pairs:
        row = conn_odds.execute(
            "SELECT canonical_team_id, verified, method FROM dim_team_xref"
            " WHERE provider=? AND provider_team_id=?",
            (PROVIDER, provider_team_id),
        ).fetchone()
        if row is not None and int(row["canonical_team_id"]) != int(canonical_team_id):
            conflicts.append({
                "provider_team_id": provider_team_id,
                "existing_canonical": int(row["canonical_team_id"]),
                "proposed_canonical": int(canonical_team_id),
                "existing_verified": int(row["verified"]),
                "existing_method": row["method"],
            })
    return conflicts


def _team_xref_existence(
    conn_odds: sqlite3.Connection, pairs: list[tuple[str, int]]
) -> tuple[list[str], list[dict]]:
    """existing auto_ok 重验证专用:每个 (provider_team_id, 预期 canonical) 的
    dim_team_xref 行必须**真实存在且指向预期 canonical**。

    返回 (missing_provider_ids, conflicts):
      - missing:该 provider ID 在 dim_team_xref 查不到行——映射已不完整(既有行被删、
        或本轮换成从未映射的新 provider ID)。existing auto_ok 重验证**不允许自动补写**,
        只能降级 needs_review(与"存在但 canonical 冲突"用不同 validation error 区分);
      - conflicts:行存在但 canonical 与预期不符(结构同 `_team_xref_conflicts` 明细)。

    与 `_team_xref_conflicts`(只查冲突、缺行视为可创建,供**新映射**)语义相反:
    新映射允许缺行后创建;既有 auto_ok 缺行即视为映射被破坏,必须降级。
    """
    missing: list[str] = []
    conflicts: list[dict] = []
    for provider_team_id, canonical_team_id in pairs:
        row = conn_odds.execute(
            "SELECT canonical_team_id, verified, method FROM dim_team_xref"
            " WHERE provider=? AND provider_team_id=?",
            (PROVIDER, provider_team_id),
        ).fetchone()
        if row is None:
            missing.append(provider_team_id)
        elif int(row["canonical_team_id"]) != int(canonical_team_id):
            conflicts.append({
                "provider_team_id": provider_team_id,
                "existing_canonical": int(row["canonical_team_id"]),
                "proposed_canonical": int(canonical_team_id),
                "existing_verified": int(row["verified"]),
                "existing_method": row["method"],
            })
    return missing, conflicts


def _record_team_xrefs(conn_odds: sqlite3.Connection, pairs: list[tuple[str, int]],
                       confidence: float, now: str) -> None:
    """写 dim_team_xref(调用前必须已确认无冲突)。

    幂等:同 (provider, provider_team_id) 且 canonical 相同 → INSERT OR IGNORE 无副作用,
    不覆盖既有 verified/manual 行(相同 canonical 本就无需改写)。
    """
    for provider_team_id, canonical_team_id in pairs:
        conn_odds.execute(
            """INSERT OR IGNORE INTO dim_team_xref
               (provider, provider_team_id, canonical_team_id, confidence, verified,
                method, created_at, updated_at)
               VALUES (?, ?, ?, ?, 0, 'auto', ?, ?)""",
            (PROVIDER, provider_team_id, canonical_team_id, confidence, now, now),
        )


def _candidate_matches(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection, date_str: str
) -> list[sqlite3.Row]:
    """候选:core dim_match 同日期 ±1 天(NowGoal 时区偏移)且未被本 provider 映射。"""
    day = date_cls.fromisoformat(date_str)
    lo = (day - timedelta(days=1)).isoformat()
    hi = (day + timedelta(days=1)).isoformat()
    mapped = {
        int(r[0])
        for r in conn_odds.execute(
            "SELECT fotmob_match_id FROM dim_match_xref WHERE provider=?", (PROVIDER,)
        )
    }
    # 联赛白名单(只用 FotMob 侧范围,不需要 NowGoal 联赛 id):NowGoal 日程是全球的
    # (单日约 988 场),不限联赛会让 16 个已登记联赛的候选池与 UNIQUE(provider,
    # fotmob_match_id) 占位风险显著上升,而被占住的行会永久冻结在 needs_review
    # (resolve_match 不再重评估)。限定候选到 LEAGUE_META 已登记联赛,安全且零成本。
    from backend.queries.leagues import LEAGUE_META

    league_ids = tuple(LEAGUE_META)
    placeholders = ",".join("?" for _ in league_ids)
    rows = conn_core.execute(
        f"""SELECT Match_ID, Date, kickoff_at_utc, kickoff_precision, kickoff_source,
                  Home_Team_ID, Away_Team_ID, Home_Team_Name, Away_Team_Name
           FROM dim_match WHERE Date BETWEEN ? AND ?
             AND League_ID IN ({placeholders})""",
        (lo, hi, *league_ids),
    ).fetchall()
    return [r for r in rows if int(r["Match_ID"]) not in mapped]


def _row_get(row, key, default=None):
    """兼容取列:某些测试布景的 dim_match 可能没有 provenance 列。"""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _parse_nowgoal_wall_clock(value) -> datetime | None:
    """NowGoal 日程 kickoff(北京墙上时间)专用解析:必须真实包含时间部分。

    纯日期(如 '2026-08-21',没有 ' HH:MM' 后缀)返回不可验证(None)——不得被
    datetime.fromisoformat 的"纯日期合法输入"特性静默当成当天 00:00 使用。
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not _NOWGOAL_WALL_CLOCK_RE.match(s):
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T", 1))
    except ValueError:
        return None


def _kickoff_diff_seconds(schedule_row: dict, cand: sqlite3.Row) -> int | None:
    """NowGoal 行 kickoff 与 core **精确** kickoff 的差值秒数。

    充要条件(缺一即 None,交由上层强制 needs_review,不编造 §6.2.1):
      - core 侧通过统一验证器 normalize_exact_kickoff(exact + 真实非空来源 + 可解析);
      - NowGoal 侧 kickoff 真实包含时间部分且可解析(_parse_nowgoal_wall_clock,
        拒绝把纯日期悄悄当成午夜)。

    NowGoal 行内 kickoff 字段本身就是 UTC(见模块 docstring 的真实交叉验证记录,
    2026-07-21:Match_ID=5107547 与 titan_id=2912218 逐位一致),不做时区转换;
    若响应本身带显式时区,直接转 UTC 比较。
    """
    core_normalized = normalize_exact_kickoff(
        _row_get(cand, "kickoff_at_utc"),
        _row_get(cand, "kickoff_precision"),
        _row_get(cand, "kickoff_source"),
    )
    if core_normalized is None:
        return None
    core_dt = parse_strict_utc(core_normalized)
    if core_dt is None:
        return None
    ng_local = _parse_nowgoal_wall_clock(schedule_row.get("kickoff"))
    if ng_local is None:
        return None
    ng_utc = ng_local.replace(tzinfo=timezone.utc) if ng_local.tzinfo is None else ng_local.astimezone(timezone.utc)
    return int((ng_utc - core_dt).total_seconds())


def _existing_result(existing, review_status=None, team_conflicts=None,
                     validation_errors=None) -> dict:
    return {
        "resolved": True,
        "created": False,
        "xref_id": existing["id"],
        "fotmob_match_id": existing["fotmob_match_id"],
        "confidence": existing["confidence"],
        "home_away_inverted": existing["home_away_inverted"],
        "review_status": review_status if review_status is not None else existing["review_status"],
        "team_conflicts": team_conflicts or [],
        "validation_errors": validation_errors or [],
    }


def _revalidate_existing_auto_ok(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection,
    schedule_row: dict, existing: sqlite3.Row,
) -> dict:
    """已有 auto_ok 映射,每次重新遇到该日程行都重新验证,不满足则原子降级 needs_review。

    重新检查(全部基于**本次 schedule_row**,不盲信历史结论):
      - core kickoff provenance + NowGoal kickoff 差值(≤30 分钟);
      - 主客方向:按 home_away_inverted 计算 NowGoal 主客队各自预期 canonical,要求
        本次 home_name/away_name 经 dim_team_alias 明确、唯一解析到预期 canonical
        (不匹配对手、无多 canonical 歧义)——纯换名或主客名互换都会在此暴露;
      - provider team ID 存在、互异;
      - 两个 provider ID 对应的 dim_team_xref 行**真实存在且指向预期 canonical**:
        查不到行 → `provider_team_xref_missing`(既有映射被删、或本轮换成从未映射的新
        provider ID);行在但 canonical 不符 → team_conflicts。二者都降级但用不同信号区分。

    **事务边界(单一 BEGIN IMMEDIATE)**:match xref 当前权威状态、alias、dim_team_xref 的
    读取,以及"保持 auto_ok 还是原子降级"的最终判定,全部在同一个事务内完成——**保持
    auto_ok 的成功路径也在事务内做最终复核,不在事务开始前提前 return**。core 库全程只读
    (在持有 odds 事务时读取)。重验证**只允许保留 auto_ok 或降级 needs_review**,绝不静默
    创建、补写或覆盖任何 team xref;verified=1 / 期间被人工改动的映射不被覆盖。
    """
    now = utc_now_iso()
    with tx(conn_odds):
        # 事务内重查 match xref 权威状态(可能已被并发操作改动)
        fresh = conn_odds.execute(
            "SELECT id, fotmob_match_id, confidence, home_away_inverted, review_status, verified"
            " FROM dim_match_xref WHERE id=?",
            (existing["id"],),
        ).fetchone()
        if fresh is None:
            return _existing_result(existing)
        # 期间被人工 confirm/verify 或已被降级 → 按当前权威状态返回,不再自动改动
        if fresh["review_status"] != "auto_ok" or fresh["verified"] != 0:
            return _existing_result(fresh)

        cand = conn_core.execute(
            """SELECT Match_ID, Home_Team_ID, Away_Team_ID, kickoff_at_utc, kickoff_precision,
                      kickoff_source FROM dim_match WHERE Match_ID=?""",
            (int(fresh["fotmob_match_id"]),),
        ).fetchone()

        validation_errors: list[str] = []
        team_conflicts: list[dict] = []
        kickoff_diff = None
        if cand is None:
            validation_errors.append("canonical_match_missing")
        else:
            inverted = int(fresh["home_away_inverted"])
            exp_home, exp_away = _expected_canonicals(cand, inverted)

            kickoff_diff = _kickoff_diff_seconds(schedule_row, cand)
            if kickoff_diff is None or abs(kickoff_diff) > KICKOFF_TOLERANCE_SECONDS:
                validation_errors.append("kickoff_mismatch_or_imprecise")

            ph, pa = schedule_row.get("provider_home_id"), schedule_row.get("provider_away_id")
            if not ph or not pa:
                validation_errors.append("provider_team_id_missing")
            elif str(ph) == str(pa):
                validation_errors.append("provider_team_ids_not_distinct")

            # 队名/方向重验证(alias-only,独立于 provider xref)
            if not _name_resolves_to(conn_odds, schedule_row.get("home_name"), exp_home):
                validation_errors.append("home_team_name_mismatch")
            if not _name_resolves_to(conn_odds, schedule_row.get("away_name"), exp_away):
                validation_errors.append("away_team_name_mismatch")

            # 既有 team xref 必须真实存在且指向预期 canonical(不允许自动补写/覆盖)
            pairs: list[tuple[str, int]] = []
            if ph:
                pairs.append((str(ph), exp_home))
            if pa:
                pairs.append((str(pa), exp_away))
            missing, team_conflicts = _team_xref_existence(conn_odds, pairs)
            if missing:
                validation_errors.append("provider_team_xref_missing")

        if not validation_errors and not team_conflicts:
            return _existing_result(fresh, review_status="auto_ok")

        # 原子降级(仍在同一事务内;已确认 fresh 为 auto_ok+verified=0)
        conn_odds.execute(
            """UPDATE dim_match_xref SET review_status='needs_review',
               kickoff_diff_seconds=?, updated_at=?
               WHERE id=? AND review_status='auto_ok' AND verified=0""",
            (kickoff_diff, now, fresh["id"]),
        )
        return _existing_result(fresh, review_status="needs_review",
                                team_conflicts=team_conflicts, validation_errors=validation_errors)


def resolve_match(
    conn_odds: sqlite3.Connection, conn_core: sqlite3.Connection, schedule_row: dict
) -> dict:
    """解析一行 NowGoal 日程 → dim_match_xref。

    schedule_row 需含 titan_id / home_name / away_name / date(YYYY-MM-DD,查询日)。
    返回 {resolved, created, xref_id, fotmob_match_id, confidence,
          home_away_inverted, review_status}。无任何候选得分时不写 xref。
    """
    titan_id = str(schedule_row["titan_id"])

    existing = conn_odds.execute(
        "SELECT * FROM dim_match_xref WHERE provider=? AND provider_match_id=?",
        (PROVIDER, titan_id),
    ).fetchone()
    if existing is not None:
        # confirmed / verified=1 / rejected:保留人工结果或既定拒绝,自动流程绝不覆盖或复活;
        # needs_review:不得静默升级,原样返回;
        # auto_ok:每次重新遇到都必须重新验证,不满足则原子降级(不能只信任历史结论)。
        if existing["review_status"] in ("confirmed", "rejected") or existing["verified"] == 1:
            return _existing_result(existing)
        if existing["review_status"] == "needs_review":
            return _existing_result(existing)
        if existing["review_status"] == "auto_ok":
            return _revalidate_existing_auto_ok(conn_odds, conn_core, schedule_row, existing)
        return _existing_result(existing)

    # 别名解析 ∪ dim_team_xref 直查(已确认过的 provider 球队 id 优先复用)
    home_ids = _alias_team_ids(conn_odds, schedule_row["home_name"]) | _team_xref_ids(
        conn_odds, schedule_row.get("provider_home_id")
    )
    away_ids = _alias_team_ids(conn_odds, schedule_row["away_name"]) | _team_xref_ids(
        conn_odds, schedule_row.get("provider_away_id")
    )

    # (score, match_id, inverted) 全部得分组合;每边命中记 0.5
    scored: list[tuple[float, int, int]] = []
    cand_by_id: dict[int, sqlite3.Row] = {}
    for cand in _candidate_matches(conn_odds, conn_core, schedule_row["date"]):
        h, a = int(cand["Home_Team_ID"]), int(cand["Away_Team_ID"])
        cand_by_id[int(cand["Match_ID"])] = cand
        fwd = 0.5 * (h in home_ids) + 0.5 * (a in away_ids)
        inv = 0.5 * (h in away_ids) + 0.5 * (a in home_ids)
        if fwd > 0:
            scored.append((fwd, int(cand["Match_ID"]), 0))
        if inv > 0:
            scored.append((inv, int(cand["Match_ID"]), 1))

    if not scored:
        return {
            "resolved": False,
            "created": False,
            "xref_id": None,
            "fotmob_match_id": None,
            "confidence": 0.0,
            "home_away_inverted": 0,
            "review_status": None,
            "team_conflicts": [],
            "validation_errors": [],
        }

    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    best_score, best_match_id, best_inverted = scored[0]
    ties = [t for t in scored if t[0] == best_score]
    unambiguous = len(ties) == 1
    best_cand = cand_by_id.get(best_match_id)

    # kickoff 差值校验:auto_ok 要求双边可解析的精确时刻(diff 非 None)且 |diff| ≤ 容差。
    # diff is None(任一侧缺精确 kickoff / 无法解析)→ 不满足 auto_ok(不再放行)。
    kickoff_diff = _kickoff_diff_seconds(schedule_row, best_cand) if best_cand is not None else None
    kickoff_ok = kickoff_diff is not None and abs(kickoff_diff) <= KICKOFF_TOLERANCE_SECONDS

    tentative_auto_ok = (
        best_score >= AUTO_OK_THRESHOLD and unambiguous and kickoff_ok and best_cand is not None
    )
    # 球队级 xref 候选对(纯计算,不触库;实际冲突检查必须在事务内重做,见下方 3.3)。
    team_pairs = _team_xref_pairs(schedule_row, best_cand, best_inverted) if tentative_auto_ok else []

    now = utc_now_iso()
    with tx(conn_odds):
        # UNIQUE(provider, fotmob_match_id):该 FotMob 场次已被其他 titan_id 占用时降级人工
        occupied = conn_odds.execute(
            "SELECT id FROM dim_match_xref WHERE provider=? AND fotmob_match_id=?",
            (PROVIDER, best_match_id),
        ).fetchone()
        if occupied is not None:
            return {
                "resolved": False,
                "created": False,
                "xref_id": None,
                "fotmob_match_id": best_match_id,
                "confidence": best_score,
                "home_away_inverted": best_inverted,
                "review_status": "conflict_existing_xref",
                "team_conflicts": [],
                "validation_errors": [],
            }

        # provider team 身份完整性(3.1/3.2):缺失/相同/内部矛盾都不得 auto_ok,
        # 且绝不能靠 INSERT OR IGNORE 把半映射写成"看起来成功"。纯计算,先于 DB 冲突检查。
        pair_errors = _new_pair_validation_errors(schedule_row, team_pairs) if tentative_auto_ok else []
        # alias-only 名称/方向验证(最终身份证明):scored 用别名 ∪ provider xref 做候选**召回**,
        # 但历史 provider xref 不得**单独**充当身份证明——否则错误队名会被历史 xref 掩盖(反例 C)。
        # 最终 auto_ok 必须由本轮 home_name/away_name 经 dim_team_alias 唯一解析到预期 canonical;
        # 名称未知/错误/多 canonical 歧义 → needs_review。事务内读取 alias,与最终判定同一事务。
        name_errors: list[str] = []
        if tentative_auto_ok and best_cand is not None:
            exp_home, exp_away = _expected_canonicals(best_cand, best_inverted)
            if not _name_resolves_to(conn_odds, schedule_row.get("home_name"), exp_home):
                name_errors.append("home_team_name_mismatch")
            if not _name_resolves_to(conn_odds, schedule_row.get("away_name"), exp_away):
                name_errors.append("away_team_name_mismatch")
        # team xref 冲突检查:必须在同一 BEGIN IMMEDIATE 事务内重新检查(3.3 事务一致性),
        # 消除"检查时无冲突,写入前被其他事务改成冲突"的竞态窗口;不能只信赖事务外的预检查。
        # 新映射语义:缺行允许在本次严格通过后创建,故只查冲突(_team_xref_conflicts),不查 missing。
        team_conflicts = _team_xref_conflicts(conn_odds, team_pairs) if team_pairs else []
        validation_errors = list(dict.fromkeys(pair_errors + name_errors))
        auto_ok = tentative_auto_ok and not team_conflicts and not validation_errors
        review_status = "auto_ok" if auto_ok else "needs_review"

        cur = conn_odds.execute(
            """INSERT INTO dim_match_xref
               (fotmob_match_id, provider, provider_match_id, home_away_inverted,
                confidence, verified, method, kickoff_diff_seconds, review_status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, 'auto', ?, ?, ?, ?)""",
            (best_match_id, PROVIDER, titan_id, best_inverted, best_score,
             kickoff_diff, review_status, now, now),
        )
        xref_id = cur.lastrowid
        # 只有确认 auto_ok(无 team 冲突、无 pair 内部矛盾)才登记球队级 xref;否则一行 team
        # xref 都不写 → 杜绝"比赛 auto_ok / team xref 未写"或"比赛 needs_review / team xref
        # 已写"或"重复 provider ID 只写一行"的半映射。
        if auto_ok:
            _record_team_xrefs(conn_odds, team_pairs, best_score, now)

    return {
        "resolved": True,
        "created": True,
        "xref_id": xref_id,
        "fotmob_match_id": best_match_id,
        "confidence": best_score,
        "home_away_inverted": best_inverted,
        "review_status": review_status,
        "team_conflicts": team_conflicts,
        "validation_errors": validation_errors,
    }
