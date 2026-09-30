"""模拟器动画的"射门样本库":从五大联赛最近两个赛季的真实射门里,按渠道 × xG 分箱各留若干脚。

动画里每一脚模拟射门,从同渠道、同 xG 箱里抽一脚真实射门,用它的位置、射门方式;
没进的射门用它的真实结果(扑救 / 封堵 / 偏出 / 门框)。模拟本身(比分、xG、谁射门)不受影响。

数据口径(2026-09-30 生产库只读核查,见 docs/data-sources.md「fact_shotmap」):
- 坐标:X_Coord 0–105(米,进攻方向 = X 增大,球门在 X=105),Y_Coord 0–68;
  Y < 34 = 进攻方的右路,Y > 34 = 左路(用萨卡/萨拉赫/亚马尔 vs 维尼修斯/姆巴佩的射门均值核对过)。
- Outcome 只有 Goal / AttemptSaved / Miss / Post 四种,"AttemptSaved"同时包含门将扑救与后卫封堵,
  二者只能靠 Is_Blocked 区分;Is_Blocked 在 2025-12-06 之前的比赛里全部为 NULL(列后加、未回填)。
  为避免扑救/封堵比例失真,只用 Is_Blocked 非空的射门。
- xG 为 NULL 的射门全部是乌龙球(记在"打进自家球门"一方名下),Is_Own_Goal=1 同样排除;乌龙球动画单独处理。
- Goal_Crossed_Y/Z:球越过门线的位置(Y 与 Y_Coord 同一坐标系,Z = 离地高度,米),门框 = Y 30.34–37.66、Z ≤ 2.44;
  Blocked_X/Y:封堵位置。两者基本只在 2026/27 有值(约 90%),缺失时记 null,前端按结果补一个示意终点。

输出(写进参数文件顶层 shot_samples,五个联赛共用一份,每个联赛页面各下发一次):
  {"version": 1, "fields": [...], "source": {...},
   "channels": {"open": {"edges": [21 个分箱边界], "bins": [[样本, ...] × 20]}, "counter": ..., "setpiece": ...,
                "penalty": {"edges": [0, 1], "bins": [[样本, ...]]}}}
  样本 = [x, y, 射门方式, 结果] 或 [x, y, 射门方式, 结果, a, b]:
    射门方式 0 右脚 / 1 左脚 / 2 头球 / 3 其它部位;结果 0 进球 / 1 扑救 / 2 封堵 / 3 偏出 / 4 门框;
    结果 = 封堵时 (a, b) = 封堵点 (Blocked_X, Blocked_Y);否则 (a, b) = 越过门线的 (Goal_Crossed_Y, Goal_Crossed_Z)。

用法(只读):python scripts/simulator/shot_samples.py --data-dir /opt/allwin/shared/data --out /tmp/shot_samples.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_right
from collections import Counter
from pathlib import Path

VERSION = 1
FIELDS = ["x", "y", "type", "result", "a", "b"]
TYPE_CODE = {"RightFoot": 0, "LeftFoot": 1, "Header": 2, "OtherBodyParts": 3}
RESULT_GOAL, RESULT_SAVED, RESULT_BLOCKED, RESULT_MISS, RESULT_POST = 0, 1, 2, 3, 4
# 与 params_core.SITUATION_CHANNEL 同一口径(此处不 import,避免把参数计算的依赖带进来;测试断言两者一致)
SITUATION_CHANNEL = {
    "RegularPlay": "open",
    "IndividualPlay": "open",
    "FastBreak": "counter",
    "FromCorner": "setpiece",
    "SetPiece": "setpiece",
    "FreeKick": "setpiece",
    "ThrowInSetPiece": "setpiece",
    "Penalty": "penalty",
}
N_BINS = 20
# 每箱保留的样本数:按各渠道真实射门量分配,整个库控制在 60KB 以内
PER_BIN = {"open": 40, "counter": 20, "setpiece": 30}
PENALTY_SAMPLES = 80


def result_code(outcome: str, is_blocked: int | None) -> int | None:
    if outcome == "Goal":
        return RESULT_GOAL
    if outcome == "Post":
        return RESULT_POST
    if outcome == "Miss":
        return RESULT_MISS
    if outcome == "AttemptSaved":
        if is_blocked is None:
            return None
        return RESULT_BLOCKED if is_blocked else RESULT_SAVED
    return None


def load_shots(core: sqlite3.Connection, leagues, seasons) -> list[dict]:
    q_leagues = ",".join(str(int(x)) for x in leagues)
    q_seasons = ",".join("?" * len(seasons))
    rows = core.execute(
        f"""SELECT f.Shot_ID, f.X_Coord, f.Y_Coord, f.xG, f.Situation, f.Shot_Type, f.Outcome, f.Is_Blocked,
                   f.Goal_Crossed_Y, f.Goal_Crossed_Z, f.Blocked_X, f.Blocked_Y
              FROM fact_shotmap f JOIN dim_match m ON m.Match_ID = f.Match_ID
             WHERE m.League_ID IN ({q_leagues}) AND m.Season IN ({q_seasons})
               AND f.xG IS NOT NULL AND COALESCE(f.Is_Own_Goal, 0) = 0 AND f.Is_Blocked IS NOT NULL
               AND f.X_Coord BETWEEN 0 AND 105 AND f.Y_Coord BETWEEN 0 AND 68""",
        tuple(seasons),
    ).fetchall()
    out = []
    for r in rows:
        (shot_id, x, y, xg, situation, shot_type, outcome, blocked, gy, gz, bx, by) = tuple(r)
        channel = SITUATION_CHANNEL.get(situation)
        res = result_code(outcome, blocked)
        if channel is None or res is None or shot_type not in TYPE_CODE:
            continue
        out.append({"id": int(shot_id or 0), "x": float(x), "y": float(y), "xg": float(xg), "channel": channel,
                    "type": TYPE_CODE[shot_type], "result": res, "gy": gy, "gz": gz, "bx": bx, "by": by})
    return out


def _mix(shot_id: int) -> int:
    # 确定性"打乱":同一份数据每天导出选出同一批样本
    return (shot_id * 2654435761) & 0xFFFFFFFF


def _encode(s: dict) -> list:
    row = [round(s["x"], 1), round(s["y"], 1), s["type"], s["result"]]
    if s["result"] == RESULT_BLOCKED:
        if s["bx"] is not None and s["by"] is not None:
            row += [round(float(s["bx"]), 1), round(float(s["by"]), 1)]
    elif s["gy"] is not None and s["gz"] is not None:
        row += [round(float(s["gy"]), 2), round(float(s["gz"]), 2)]
    return row


def _has_endpoint(s: dict) -> bool:
    if s["result"] == RESULT_BLOCKED:
        return s["bx"] is not None and s["by"] is not None
    return s["gy"] is not None and s["gz"] is not None


def _pick(members: list[dict], k: int) -> list[dict]:
    # 有真实终点(越线点/封堵点)的优先,其余补齐;各自内部按确定性打乱顺序取
    ordered = sorted(members, key=lambda s: (not _has_endpoint(s), _mix(s["id"])))
    return ordered[:k]


def quantile_edges(values: list[float], n_bins: int) -> list[float]:
    xs = sorted(values)
    if not xs:
        return [0.0] * (n_bins + 1)
    inner = [xs[min(len(xs) - 1, (i * len(xs)) // n_bins)] for i in range(1, n_bins)]
    return [0.0] + [round(v, 4) for v in inner] + [1.0]


def bin_index(edges: list[float], xg: float) -> int:
    # edges[0]=0、edges[-1]=1;落在第 i 箱 ⇔ edges[i] ≤ xg < edges[i+1](与前端 shotDetail.ts::binOf 同一口径)
    return max(0, min(len(edges) - 2, bisect_right(edges, xg) - 1))


def build_shot_samples(shots: list[dict], leagues, seasons) -> dict:
    channels: dict = {}
    for ch, k in PER_BIN.items():
        members = [s for s in shots if s["channel"] == ch]
        edges = quantile_edges([s["xg"] for s in members], N_BINS)
        bins: list[list[dict]] = [[] for _ in range(N_BINS)]
        for s in members:
            bins[bin_index(edges, s["xg"])].append(s)
        channels[ch] = {"edges": edges, "bins": [[_encode(s) for s in _pick(b, k)] for b in bins]}
    pens = [s for s in shots if s["channel"] == "penalty"]
    channels["penalty"] = {"edges": [0.0, 1.0], "bins": [[_encode(s) for s in _pick(pens, PENALTY_SAMPLES)]]}
    return {
        "version": VERSION,
        "fields": FIELDS,
        "source": {
            "leagues": sorted(int(x) for x in leagues),
            "seasons": list(seasons),
            "eligible_shots": len(shots),
            "by_channel": dict(Counter(s["channel"] for s in shots)),
            "note": "仅 Is_Blocked 非空(2025-12-06 起)的非乌龙射门;五个联赛合并分箱",
        },
        "channels": channels,
    }


def summarize(lib: dict) -> dict:
    size = len(json.dumps(lib, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    per_channel = {}
    for ch, c in lib["channels"].items():
        samples = [s for b in c["bins"] for s in b]
        per_channel[ch] = {
            "samples": len(samples),
            "empty_bins": sum(1 for b in c["bins"] if not b),
            "with_endpoint": sum(1 for s in samples if len(s) == 6),
            "results": dict(Counter(("goal", "saved", "blocked", "miss", "post")[s[3]] for s in samples)),
        }
    return {"bytes": size, "channels": per_channel, "source": lib["source"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from params_core import CALIBRATED_LEAGUES  # noqa: E402

    core = sqlite3.connect(f"file:{Path(args.data_dir) / 'allwin.db'}?mode=ro", uri=True)
    core.execute("PRAGMA query_only=ON")
    seasons = ("2025/2026", "2026/2027")
    lib = build_shot_samples(load_shots(core, CALIBRATED_LEAGUES, seasons), CALIBRATED_LEAGUES, seasons)
    Path(args.out).write_text(json.dumps(lib, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps(summarize(lib), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
