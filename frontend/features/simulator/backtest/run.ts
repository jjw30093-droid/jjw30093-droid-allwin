// Phase 2 回测与校准(本地 Node 运行,调用与页面同一套 engine.ts)。
// 口径:docs/simulator-model.md「Phase 2 回测与校准方法」。
// 用法:node run.mjs <ha-k|w|empirical|kappa|validate|focus> --dir <.local-data/simulator/backtest>

import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { FOCUS_LIST, focusError, prepareMatch, simulateMany, type Focus, type MatchConfig } from "../engine";
import { mulberry32 } from "../rng";
import type { LeagueParams, RateModel, ShotChannel, SimParams, StateMultipliers } from "../types";
import { setupFor, simParamsFor, V02_CAL, type Cal } from "./assemble";
import { brier3, calibrationSlope, goldenMax, mean, outcomeIndex, poisson1x2, poissonLogLik, timingBucket } from "./stats";
import type { Outcome, Prematch, Snapshot } from "./types";

const K_GRID = [3, 5, 8, 12];
const W_GRID = [0.5, 0.7, 0.9, 1.0];
/** 0 = 关闭状态系数(v0.3 第 2 条) */
const KAPPA_GRID = [6, 8, 12, 16, 24, 32, 48, 0];
const RUNS = 2000;
const TRAIN: [number, number] = [4, 19];
const VALID: [number, number] = [20, 38];
const H_RANGE: [number, number] = [0.8, 1.5];
const SHOT: ShotChannel[] = ["open", "counter", "setpiece", "penalty"];

interface Row {
  pm: Prematch;
  snap: Snapshot;
  out: Outcome;
}

const args = process.argv.slice(2);
const cmd = args[0];
const path: "A" | "B" = args.includes("--path") ? (args[args.indexOf("--path") + 1] as "A" | "B") : "A";
const dir = args[args.indexOf("--dir") + 1];
const resDir = join(dir, "results");
mkdirSync(resDir, { recursive: true });

function readJson<T>(p: string): T {
  return JSON.parse(readFileSync(p, "utf8")) as T;
}
function save(name: string, obj: unknown) {
  writeFileSync(join(resDir, name), JSON.stringify(obj, null, 1));
}
function loadRes<T>(name: string): T {
  return readJson<T>(join(resDir, name));
}

/** Bet365 1x2 收盘去水概率 [主, 平, 客](P2.6b)。 */
const bet365: Map<number, [number, number, number]> = new Map(
  Object.entries(readJson<{ matches: Record<string, { p: [number, number, number] }> }>(join(dir, "bet365_1x2.json")).matches).map(
    ([k, v]) => [Number(k), v.p],
  ),
);
const FAV_THRESHOLD = 0.6;

/** 热门方:Bet365 去水胜率 ≥ 60% 的一方(0 主 / 2 客);没有则 null。 */
function favouriteOf(matchId: number): 0 | 2 | null {
  const p = bet365.get(matchId);
  if (!p) return null;
  if (p[0] >= FAV_THRESHOLD) return 0;
  if (p[2] >= FAV_THRESHOLD) return 2;
  return null;
}

function loadRows(): Row[] {
  const outcomes = new Map(readJson<Outcome[]>(join(dir, "outcomes.json")).map((o) => [o.match_id, o]));
  const rows: Row[] = [];
  for (const f of readdirSync(join(dir, "snapshots")).sort()) {
    const snap = readJson<Snapshot>(join(dir, "snapshots", f));
    for (const pm of snap.prematch) {
      const out = outcomes.get(pm.match_id);
      if (out) rows.push({ pm, snap, out });
    }
  }
  return rows;
}

const inRange = (r: Row, [lo, hi]: [number, number]) => r.pm.round != null && r.pm.round >= lo && r.pm.round <= hi;

/** 同一快照日的 SimParams 只拼一次。 */
function paramsCache(cal: Cal, opts: { useMarket: boolean; hOverride?: number }) {
  const cache = new Map<Snapshot, SimParams>();
  return (s: Snapshot) => {
    let p = cache.get(s);
    if (!p) {
      p = simParamsFor(s, cal, opts);
      cache.set(s, p);
    }
    return p;
  };
}

function prep(r: Row, params: SimParams, useMarket: boolean): MatchConfig | null {
  const setup = setupFor(r.pm, { useMarket });
  if (!setup) return null;
  const res = prepareMatch(params, setup);
  return res.ok ? res.config : null;
}

/** v0.3 的校准链:a(h、k)→ b(w)→ rm(Poisson 回归)→ d(κ)。 */
function calFromResults(upTo: "a" | "b" | "rm" | "d"): Cal {
  const a = loadRes<{ k: number; h: Record<string, number> }>("step_a.json");
  let cal: Cal = { ...V02_CAL, k: a.k, h: a.h };
  if (upTo === "a") return cal;
  cal = { ...cal, w: loadRes<{ w: number }>("step_b.json").w };
  if (upTo === "b") return cal;
  cal = { ...cal, rate_model: loadRes<{ rate_model: RateModel }>("step_rm.json").rate_model };
  if (upTo === "rm") return cal;
  return { ...cal, kappa: loadRes<{ kappa: number }>("step_d.json").kappa };
}

// ------------------------------------------------------------------ a. HA 与 k
function stepA(rows: Row[]) {
  const train = rows.filter((r) => inRange(r, TRAIN));
  const byK: Record<string, { ll: number; h: Record<string, number>; n: number; skipped: number }> = {};
  const dataByK: Record<string, Record<string, { bH: number; bA: number; og: number; yH: number; yA: number }[]>> = {};
  for (const k of K_GRID) {
    const get = paramsCache({ ...V02_CAL, k }, { useMarket: false, hOverride: 1 });
    const data: Record<string, { bH: number; bA: number; og: number; yH: number; yA: number }[]> = {};
    let skipped = 0;
    for (const r of train) {
      const params = get(r.snap);
      const cfg = prep(r, params, false);
      if (!cfg) {
        skipped++;
        continue;
      }
      const og = params.leagues[String(r.pm.league_id)].mu.owngoal;
      (data[r.pm.league_id] ??= []).push({
        bH: cfg.teams[0].breakdown.lambdaModel,
        bA: cfg.teams[1].breakdown.lambdaModel,
        og,
        yH: r.out.score[0],
        yA: r.out.score[1],
      });
    }
    const h: Record<string, number> = {};
    let ll = 0;
    let n = 0;
    for (const [lid, ms] of Object.entries(data)) {
      const f = (x: number) => ms.reduce((s, m) => s + poissonLogLik(m.yH, m.bH * x + m.og) + poissonLogLik(m.yA, m.bA / x + m.og), 0);
      h[lid] = goldenMax(f, H_RANGE[0], H_RANGE[1]);
      ll += f(h[lid]);
      n += ms.length;
    }
    byK[String(k)] = { ll, h, n, skipped };
    dataByK[String(k)] = data;
    console.log(`k=${k}: logLik=${ll.toFixed(3)} n=${n} skipped=${skipped} h=${JSON.stringify(Object.fromEntries(Object.entries(h).map(([l, v]) => [l, +v.toFixed(4)])))}`);
  }
  const best = K_GRID.reduce((b, k) => (byK[String(k)].ll > byK[String(b)].ll ? k : b), K_GRID[0]);
  // h_L 的 95% 置信区间:剖面似然,对数似然比最优值低 1.92 处(二分)
  const ci: Record<string, [number, number]> = {};
  for (const [lid, ms] of Object.entries(dataByK[String(best)])) {
    const f = (x: number) => ms.reduce((s2, m) => s2 + poissonLogLik(m.yH, m.bH * x + m.og) + poissonLogLik(m.yA, m.bA / x + m.og), 0);
    const hHat = byK[String(best)].h[lid];
    const target = f(hHat) - 1.92;
    const solve = (lo: number, hi: number) => {
      for (let i = 0; i < 60; i++) {
        const mid = (lo + hi) / 2;
        if ((f(mid) - target) * (f(lo) - target) > 0) lo = mid;
        else hi = mid;
      }
      return (lo + hi) / 2;
    };
    ci[lid] = [solve(0.5, hHat), solve(hHat, 2.5)];
    console.log(`h[${lid}] = ${hHat.toFixed(4)},95% CI [${ci[lid][0].toFixed(4)}, ${ci[lid][1].toFixed(4)}](${ms.length} 场)`);
  }
  save("step_a.json", { k: best, h: byK[String(best)].h, h_ci95: ci, by_k: byK });
  console.log(`选定 k=${best}`);
}

// ------------------------------------------------------------------ b. 市场权重 w
function stepB(rows: Row[]) {
  const cal = calFromResults("a");
  const get = paramsCache(cal, { useMarket: false });
  const items: { mH: number; mA: number; kH: number; kA: number; og: number; o: 0 | 1 | 2 }[] = [];
  let skipped = 0;
  for (const r of rows.filter((x) => inRange(x, TRAIN) && x.pm.crown?.market_lambda)) {
    const params = get(r.snap);
    const cfg = prep(r, params, false);
    if (!cfg) {
      skipped++;
      continue;
    }
    const ml = r.pm.crown!.market_lambda!;
    items.push({
      mH: cfg.teams[0].breakdown.lambdaModel,
      mA: cfg.teams[1].breakdown.lambdaModel,
      kH: ml.home,
      kA: ml.away,
      og: params.leagues[String(r.pm.league_id)].mu.owngoal,
      o: outcomeIndex(r.out.score),
    });
  }
  const byW: Record<string, number> = {};
  for (const w of W_GRID) {
    byW[String(w)] = mean(items.map((it) => {
      const lh = w * (it.kH - it.og) + (1 - w) * it.mH + it.og;
      const la = w * (it.kA - it.og) + (1 - w) * it.mA + it.og;
      return brier3(poisson1x2(lh, la), it.o);
    }));
    console.log(`w=${w}: Brier=${byW[String(w)].toFixed(5)} (n=${items.length})`);
  }
  const best = W_GRID.reduce((b, w) => (byW[String(w)] < byW[String(b)] ? w : b), W_GRID[0]);
  save("step_b.json", { w: best, brier_by_w: byW, n: items.length, skipped });
  console.log(`选定 w=${best}`);
}

// ------------------------------------------------------------------ c. 红牌与比分状态(经验值)
function matchTimeline(out: Outcome, league: LeagueParams) {
  const s1 = Math.max(out.stoppage_announced[0] ?? Math.round(league.stoppage_mean.first_half), out.stoppage_observed_max[0]);
  const s2 = Math.max(out.stoppage_announced[1] ?? Math.round(league.stoppage_mean.second_half), out.stoppage_observed_max[1]);
  const buckets: number[] = [];
  for (let m = 1; m <= 45 + s1; m++) buckets.push(timingBucket(Math.min(m, 45)));
  for (let m = 46; m <= 90 + s2; m++) buckets.push(timingBucket(Math.min(m, 90)));
  const idx = (minute: number, added: number) => {
    if (minute <= 45) return (minute < 45 ? Math.max(minute, 1) : 45 + Math.min(added, s1)) - 1;
    const base = 45 + s1;
    if (minute < 90) return base + (minute - 45) - 1;
    return base + 45 + Math.min(Math.max(added, minute - 90), s2) - 1;
  };
  return { buckets, idx };
}

type StateKey = "0" | "+1" | "-1" | "<=-2" | ">=+2";
const stateKey = (d: number): StateKey => (d === 0 ? "0" : d === 1 ? "+1" : d === -1 ? "-1" : d <= -2 ? "<=-2" : ">=+2");

function stepC(rows: Row[]) {
  const cal = calFromResults("b");
  const get = paramsCache(cal, { useMarket: true });
  const keys: StateKey[] = ["0", "+1", "-1", "<=-2", ">=+2"];
  const E: Record<StateKey, Record<ShotChannel, number>> = Object.fromEntries(keys.map((k) => [k, { open: 0, counter: 0, setpiece: 0, penalty: 0 }])) as never;
  const G: Record<StateKey, Record<string, number>> = Object.fromEntries(keys.map((k) => [k, {}])) as never;
  const red = { ownGb: 0, ownEb: 0, ownGa: 0, ownEa: 0, oppGb: 0, oppEb: 0, oppGa: 0, oppEa: 0, events: 0, skipped: 0 };
  let used = 0;
  let skipped = 0;
  for (const r of rows.filter((x) => inRange(x, TRAIN))) {
    const params = get(r.snap);
    const cfg = prep(r, params, true);
    if (!cfg) {
      skipped++;
      continue;
    }
    used++;
    const { buckets, idx } = matchTimeline(r.out, params.leagues[String(r.pm.league_id)]);
    const timing = cfg.timing;
    const sumT = buckets.reduce((s, b) => s + timing[b], 0);
    const lam = [cfg.teams[0].lambda, cfg.teams[1].lambda];
    const exp = (tick: number, team: number, c: ShotChannel) => (lam[team][c] * timing[buckets[tick]]) / sumT;
    const goals = r.out.goals.map((g, i) => ({ ...g, t: Math.min(idx(g.minute, g.added), buckets.length - 1), i })).sort((a, b) => a.t - b.t || a.i - b.i);
    const reds = r.out.reds.map((x) => ({ ...x, t: Math.min(idx(x.minute, x.added), buckets.length - 1) }));
    const firstRed = [0, 1].map((team) => Math.min(...reds.filter((x) => x.team === team).map((x) => x.t), Infinity));
    const anyRed = Math.min(firstRed[0], firstRed[1]);

    // 比分状态(双方都未吃红牌的分钟)
    const score = [0, 0];
    let gi = 0;
    for (let t = 0; t < buckets.length; t++) {
      if (t < anyRed) {
        for (const team of [0, 1]) {
          const k = stateKey(score[team] - score[1 - team]);
          for (const c of SHOT) E[k][c] += exp(t, team, c);
        }
      }
      while (gi < goals.length && goals[gi].t === t) {
        const g = goals[gi++];
        if (!g.own_goal && t < anyRed) {
          const k = stateKey(score[g.team] - score[1 - g.team]);
          G[k][g.channel] = (G[k][g.channel] ?? 0) + 1;
          G[k].all = (G[k].all ?? 0) + 1;
        }
        score[g.team] += 1;
      }
    }

    // 红牌(常规时间的第一张;对方已先吃红牌的事件跳过;对方后吃红牌时窗口截止)
    for (const team of [0, 1]) {
      const first = r.out.reds.filter((x) => x.team === team).sort((a, b) => a.minute - b.minute)[0];
      if (!first) continue;
      if (first.added !== 0 || first.minute > 90) continue;
      const t0 = firstRed[team];
      const other = firstRed[1 - team];
      if (other <= t0) {
        red.skipped++;
        continue;
      }
      const end = Math.min(other, buckets.length);
      red.events++;
      const o = 1 - team;
      for (let t = 0; t < end; t++) {
        const eo = SHOT.reduce((s, c) => s + exp(t, team, c), 0);
        const ep = SHOT.reduce((s, c) => s + exp(t, o, c), 0);
        if (t < t0) {
          red.ownEb += eo;
          red.oppEb += ep;
        } else {
          red.ownEa += eo;
          red.oppEa += ep;
        }
      }
      for (const g of goals) {
        if (g.own_goal || g.t >= end) continue;
        const after = g.t >= t0;
        if (g.team === team) {
          if (after) red.ownGa++;
          else red.ownGb++;
        } else if (after) red.oppGa++;
        else red.oppGb++;
      }
    }
  }
  const allE = (k: StateKey) => SHOT.reduce((s, c) => s + E[k][c], 0);
  const rate = (k: StateKey, c: ShotChannel | "all") => (G[k][c] ?? 0) / (c === "all" ? allE(k) : E[k][c]);
  const ratio = (k: StateKey, c: ShotChannel | "all") => rate(k, c) / rate("0", c);
  const state: StateMultipliers = {
    trail1_open: ratio("-1", "open"),
    trail1_counter: ratio("-1", "counter"),
    lead1_all: ratio("+1", "all"),
    trail2_all: ratio("<=-2", "all"),
    lead2_all: ratio(">=+2", "all"),
  };
  const red_own = red.ownGa / red.ownEa / (red.ownGb / red.ownEb);
  const red_opp = red.oppGa / red.oppEa / (red.oppGb / red.oppEb);
  const detail = Object.fromEntries(keys.map((k) => [k, {
    expected: { ...Object.fromEntries(SHOT.map((c) => [c, +E[k][c].toFixed(2)])), all: +allE(k).toFixed(2) },
    goals: G[k],
  }]));
  save("step_c.json", { state, red_own, red_opp, red_counts: red, state_detail: detail, matches_used: used, skipped });
  console.log(JSON.stringify({ state, red_own, red_opp, red_counts: red, matches_used: used, skipped }, null, 1));
}

// ------------------------------------------------------------------ v0.3:时段 / 比分状态 / 红牌 Poisson 回归
const RM_NAMES = [
  "时段 1–15", "时段 16–30", "时段 31–45+", "时段 46–60", "时段 61–75",
  "75前 落后≥2", "75前 落后1", "75前 领先1", "75前 领先≥2",
  "75后 持平", "75后 落后1", "75后 落后≥2", "75后 领先1", "75后 领先≥2",
  "本队少一人", "对手少一人",
];

function solveLinear(A: number[][], b: number[]): number[] {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    [M[c], M[piv]] = [M[piv], M[c]];
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / row[i]);
}

function invert(A: number[][]): number[][] {
  const n = A.length;
  const cols = Array.from({ length: n }, (_, j) => solveLinear(A, Array.from({ length: n }, (_, i) => (i === j ? 1 : 0))));
  return Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => cols[j][i]));
}

function stepRateModel(rows: Row[]) {
  const cal = calFromResults("b");
  const get = paramsCache(cal, { useMarket: true });
  // 协变量组合聚合:key -> {x(16 维 0/1), y, E=Σ λ/90}
  const cells = new Map<string, { x: number[]; y: number; e: number }>();
  let used = 0;
  let skipped = 0;
  let minutes = 0;
  for (const r of rows.filter((x) => inRange(x, TRAIN))) {
    const params = get(r.snap);
    const cfg = prep(r, params, true);
    if (!cfg) {
      skipped++;
      continue;
    }
    used++;
    const { buckets, idx } = matchTimeline(r.out, params.leagues[String(r.pm.league_id)]);
    const lam = [cfg.teams[0].breakdown.expectedGoals, cfg.teams[1].breakdown.expectedGoals];
    const goalsAt = new Map<number, (0 | 1)[]>();
    for (const g of r.out.goals) {
      const t = Math.min(idx(g.minute, g.added), buckets.length - 1);
      goalsAt.set(t, [...(goalsAt.get(t) ?? []), g.team]);
    }
    const firstRed = [0, 1].map((team) =>
      Math.min(...r.out.reds.filter((x) => x.team === team).map((x) => Math.min(idx(x.minute, x.added), buckets.length - 1)), Infinity),
    );
    const score = [0, 0];
    for (let t = 0; t < buckets.length; t++) {
      const scored = goalsAt.get(t) ?? [];
      for (const team of [0, 1] as const) {
        const d = score[team] - score[1 - team];
        const x = new Array(16).fill(0);
        const late = buckets[t] === 5;
        if (!late) {
          x[buckets[t]] = 1;
          if (d <= -2) x[5] = 1;
          else if (d === -1) x[6] = 1;
          else if (d === 1) x[7] = 1;
          else if (d >= 2) x[8] = 1;
        } else {
          x[d === 0 ? 9 : d === -1 ? 10 : d <= -2 ? 11 : d === 1 ? 12 : 13] = 1;
        }
        if (firstRed[team] <= t) x[14] = 1;
        if (firstRed[1 - team] <= t) x[15] = 1;
        const key = x.join("");
        const cell = cells.get(key) ?? { x, y: 0, e: 0 };
        cell.y += scored.filter((s2) => s2 === team).length;
        cell.e += lam[team] / 90;
        cells.set(key, cell);
        minutes++;
      }
      for (const team of scored) score[team] += 1;
    }
  }
  // Newton-Raphson
  const list = [...cells.values()];
  let beta = new Array(16).fill(0);
  for (let iter = 0; iter < 100; iter++) {
    const g = new Array(16).fill(0);
    const H = Array.from({ length: 16 }, () => new Array(16).fill(0));
    for (const c of list) {
      const mu = c.e * Math.exp(c.x.reduce((s2, v, i) => s2 + v * beta[i], 0));
      for (let i = 0; i < 16; i++) {
        if (!c.x[i]) continue;
        g[i] += c.y - mu;
        for (let j = 0; j < 16; j++) if (c.x[j]) H[i][j] += mu;
      }
    }
    const step = solveLinear(H, g);
    beta = beta.map((b, i) => b + step[i]);
    if (Math.max(...step.map(Math.abs)) < 1e-12) break;
  }
  const H = Array.from({ length: 16 }, () => new Array(16).fill(0));
  let yTot = 0;
  let eTot = 0;
  for (const c of list) {
    const mu = c.e * Math.exp(c.x.reduce((s2, v, i) => s2 + v * beta[i], 0));
    yTot += c.y;
    eTot += c.e;
    for (let i = 0; i < 16; i++) for (let j = 0; j < 16; j++) if (c.x[i] && c.x[j]) H[i][j] += mu;
  }
  const cov = invert(H);
  const coef = RM_NAMES.map((name, i) => {
    const se = Math.sqrt(cov[i][i]);
    return { name, coef: beta[i], se, ci95: [beta[i] - 1.96 * se, beta[i] + 1.96 * se], mult: Math.exp(beta[i]) };
  });
  const drawDiff = beta[9] - beta[4];
  const drawSe = Math.sqrt(cov[9][9] + cov[4][4] - 2 * cov[9][4]);
  const rate_model: RateModel = {
    periods: [beta[0], beta[1], beta[2], beta[3], beta[4]],
    early_state: { trail2: beta[5], trail1: beta[6], lead1: beta[7], lead2: beta[8] },
    late: { level: beta[9], trail1: beta[10], trail2: beta[11], lead1: beta[12], lead2: beta[13] },
    red_own_down: beta[14],
    red_opp_down: beta[15],
  };
  const drawMechanism = { diff: drawDiff, se: drawSe, ci95: [drawDiff - 1.96 * drawSe, drawDiff + 1.96 * drawSe], mult: Math.exp(drawDiff) };
  save("step_rm.json", { rate_model, coefficients: coef, draw_mechanism: drawMechanism, matches: used, skipped, team_minutes: minutes, goals: yTot, expected_offset_sum: eTot, cells: list.length });
  console.log(`训练集 ${used} 场(跳过 ${skipped}),球队×分钟 ${minutes} 行,进球 ${yTot},Σλ/90 = ${eTot.toFixed(1)},协变量组合 ${list.length}`);
  for (const c of coef) console.log(`${c.name.padEnd(12)} 系数 ${c.coef.toFixed(4)}  SE ${c.se.toFixed(4)}  95% CI [${c.ci95[0].toFixed(4)}, ${c.ci95[1].toFixed(4)}]  乘数 ${c.mult.toFixed(3)}`);
  console.log(`平局机制读数(75后 持平 − 时段 61–75):${drawDiff.toFixed(4)},SE ${drawSe.toFixed(4)},95% CI [${drawMechanism.ci95[0].toFixed(4)}, ${drawMechanism.ci95[1].toFixed(4)}],乘数 ${drawMechanism.mult.toFixed(3)}`);
}

// ------------------------------------------------------------------ 模拟评估公用
interface SimRow {
  r: Row;
  cfg: MatchConfig;
  pH: number;
  pD: number;
  pA: number;
  meanGoals: [number, number];
  expected: [number, number];
  lambdaFinal: [number, number];
  pBig: number;
  reds: number;
  pens: number;
  buckets: number[];
  fairAh: number;
}

function simulateRows(rows: Row[], cal: Cal, useMarket = true): { sims: SimRow[]; skipped: number } {
  const get = paramsCache(cal, { useMarket });
  const sims: SimRow[] = [];
  let skipped = 0;
  for (const r of rows) {
    const cfg = prep(r, get(r.snap), useMarket);
    if (!cfg) {
      skipped++;
      continue;
    }
    const m = simulateMany(cfg, r.pm.match_id, RUNS, [0, 0]);
    sims.push({
      r,
      cfg,
      pH: m.pHome,
      pD: m.pDraw,
      pA: m.pAway,
      meanGoals: m.meanGoals,
      expected: [cfg.teams[0].breakdown.expectedGoals, cfg.teams[1].breakdown.expectedGoals],
      lambdaFinal: [cfg.teams[0].breakdown.lambdaFinal, cfg.teams[1].breakdown.lambdaFinal],
      pBig: m.pBigMargin,
      reds: m.meanReds,
      pens: m.meanPenalties,
      buckets: m.goalBuckets,
      fairAh: m.fairAhLine,
    });
  }
  return { sims, skipped };
}

function favNotWin(sims: SimRow[]) {
  const fav = sims.filter((s) => favouriteOf(s.r.pm.match_id) !== null);
  const sim = mean(fav.map((s) => (favouriteOf(s.r.pm.match_id) === 0 ? 1 - s.pH : 1 - s.pA)));
  const act = mean(fav.map((s) => (outcomeIndex(s.r.out.score) === favouriteOf(s.r.pm.match_id) ? 0 : 1)));
  return { sim, act, n: fav.length };
}

function bigMargin(sims: SimRow[]) {
  return {
    sim: mean(sims.map((s) => s.pBig)),
    act: mean(sims.map((s) => (Math.abs(s.r.out.score[0] - s.r.out.score[1]) >= 3 ? 1 : 0))),
    n: sims.length,
  };
}

// ------------------------------------------------------------------ d. κ
function stepD(rows: Row[]) {
  const base = calFromResults("rm");
  const train = rows.filter((r) => inRange(r, TRAIN));
  const byK: Record<string, unknown> = {};
  let best = { kappa: KAPPA_GRID[0], sse: Infinity };
  for (const kappa of KAPPA_GRID) {
    const t0 = Date.now();
    const { sims, skipped } = simulateRows(train, { ...base, kappa });
    const big = bigMargin(sims);
    const fav = favNotWin(sims);
    const sse = (big.sim - big.act) ** 2 + (fav.sim - fav.act) ** 2;
    byK[String(kappa)] = { big, fav, sse, n: sims.length, skipped };
    console.log(`κ=${kappa === 0 ? "关闭" : kappa}: 净胜≥3 模拟 ${(big.sim * 100).toFixed(2)}% / 实际 ${(big.act * 100).toFixed(2)}%;热门不胜 模拟 ${(fav.sim * 100).toFixed(2)}% / 实际 ${(fav.act * 100).toFixed(2)}% (n=${fav.n});SSE=${sse.toExponential(3)};${((Date.now() - t0) / 1000).toFixed(0)}s`);
    if (sse < best.sse) best = { kappa, sse };
  }
  save("step_d.json", { kappa: best.kappa, by_kappa: byK });
  console.log(`选定 κ=${best.kappa === 0 ? "关闭" : best.kappa}`);
}

// ------------------------------------------------------------------ 闸门(验证集)
function validate(rows: Row[]) {
  // 路径 A:w 取训练集选出的值(有盘口时定锚);路径 B:w = 0(只用数据模型)
  const cal0 = calFromResults("d");
  const cal = path === "B" ? { ...cal0, w: 0 } : cal0;
  const valid = rows.filter((r) => inRange(r, VALID));
  const t0 = Date.now();
  const { sims, skipped } = simulateRows(valid, cal, path === "A");
  const pct = (x: number) => +(x * 100).toFixed(2);

  // Brier:模拟器 vs 简单 Poisson 基准(两队滚动 xG)
  const brierSim = mean(sims.map((s) => brier3([s.pH, s.pD, s.pA], outcomeIndex(s.r.out.score))));
  const baseRows = sims.filter((s) => {
    const th = s.r.snap.params.teams[String(s.r.pm.home_team_id)].xg10;
    const ta = s.r.snap.params.teams[String(s.r.pm.away_team_id)].xg10;
    return th.for != null && th.against != null && ta.for != null && ta.against != null;
  });
  const brierBase = mean(baseRows.map((s) => {
    const th = s.r.snap.params.teams[String(s.r.pm.home_team_id)].xg10;
    const ta = s.r.snap.params.teams[String(s.r.pm.away_team_id)].xg10;
    const lh = (th.for! + ta.against!) / 2;
    const la = (ta.for! + th.against!) / 2;
    return brier3(poisson1x2(lh, la), outcomeIndex(s.r.out.score));
  }));
  const brierSimOnBase = mean(baseRows.map((s) => brier3([s.pH, s.pD, s.pA], outcomeIndex(s.r.out.score))));

  // 校准斜率
  const pairs = sims.flatMap((s) => {
    const o = outcomeIndex(s.r.out.score);
    return [{ p: s.pH, y: o === 0 ? 1 : 0 }, { p: s.pD, y: o === 1 ? 1 : 0 }, { p: s.pA, y: o === 2 ? 1 : 0 }];
  });
  const cs = calibrationSlope(pairs, 10);

  const drawSim = mean(sims.map((s) => s.pD));
  const drawAct = mean(sims.map((s) => (s.r.out.score[0] === s.r.out.score[1] ? 1 : 0)));
  const goalsSim = mean(sims.map((s) => s.meanGoals[0] + s.meanGoals[1]));
  const goalsAct = mean(sims.map((s) => s.r.out.score[0] + s.r.out.score[1]));
  const big = bigMargin(sims);
  const fav = favNotWin(sims);
  const redsSim = mean(sims.map((s) => s.reds));
  const redsAct = mean(sims.map((s) => s.r.out.reds.length));
  const pensSim = mean(sims.map((s) => s.pens));
  const pensAct = mean(sims.map((s) => s.r.out.penalty_attempts[0] + s.r.out.penalty_attempts[1]));
  const simB = [0, 0, 0, 0, 0, 0];
  const actB = [0, 0, 0, 0, 0, 0];
  for (const s of sims) {
    s.buckets.forEach((v, i) => (simB[i] += v));
    for (const g of s.r.out.goals) actB[timingBucket(g.minute)] += 1;
  }
  const simShare = simB.map((v) => v / simB.reduce((a, b) => a + b, 0));
  const actShare = actB.map((v) => v / actB.reduce((a, b) => a + b, 0));
  const lowRatios = sims.map((s) => {
    const lo = s.lambdaFinal[0] <= s.lambdaFinal[1] ? 0 : 1;
    return s.meanGoals[lo] / s.expected[lo];
  });
  const highRatios = sims.map((s) => {
    const hi = s.lambdaFinal[0] <= s.lambdaFinal[1] ? 1 : 0;
    return s.meanGoals[hi] / s.expected[hi];
  });

  // 如实报告:与市场的 Brier 差距(基准 Bet365 1x2 收盘去水);附报 Crown AH+OU 反推版;公平让球线一致率
  const withB365 = sims.filter((s) => bet365.has(s.r.pm.match_id));
  const brierB365 = mean(withB365.map((s) => brier3(bet365.get(s.r.pm.match_id)!, outcomeIndex(s.r.out.score))));
  const brierSimOnB365 = mean(withB365.map((s) => brier3([s.pH, s.pD, s.pA], outcomeIndex(s.r.out.score))));
  const drawB365 = mean(withB365.map((s) => bet365.get(s.r.pm.match_id)![1]));
  const withCrown = sims.filter((s) => s.r.pm.crown?.market_lambda);
  const brierCrown = mean(withCrown.map((s) => brier3(poisson1x2(s.r.pm.crown!.market_lambda!.home, s.r.pm.crown!.market_lambda!.away), outcomeIndex(s.r.out.score))));
  const brierSimOnCrown = mean(withCrown.map((s) => brier3([s.pH, s.pD, s.pA], outcomeIndex(s.r.out.score))));
  const withAh = sims.filter((s) => s.r.pm.crown?.ah);
  const diffs: Record<string, number> = {};
  for (const s of withAh) {
    const d = +(s.fairAh - s.r.pm.crown!.ah!.line).toFixed(2);
    diffs[String(d)] = (diffs[String(d)] ?? 0) + 1;
  }
  const agree = withAh.filter((s) => Math.abs(s.fairAh - s.r.pm.crown!.ah!.line) < 1e-9).length / withAh.length;

  const gates = [
    { name: "Brier 不差于简单 Poisson 基准", value: { sim: brierSimOnBase, baseline: brierBase, n: baseRows.length }, pass: brierSimOnBase <= brierBase },
    { name: "校准斜率 ∈ [0.8, 1.2]", value: { slope: cs.slope, intercept: cs.intercept, table: cs.table }, pass: cs.slope >= 0.8 && cs.slope <= 1.2 },
    { name: "平局率相差 ≤ 3pp", value: { sim: pct(drawSim), act: pct(drawAct), diff_pp: pct(drawSim - drawAct) }, pass: Math.abs(drawSim - drawAct) <= 0.03 },
    { name: "场均总进球相差 ≤ 0.15", value: { sim: goalsSim, act: goalsAct, diff: goalsSim - goalsAct }, pass: Math.abs(goalsSim - goalsAct) <= 0.15 },
    { name: "净胜 ≥3 球比例相差 ≤ 2pp", value: { sim: pct(big.sim), act: pct(big.act), diff_pp: pct(big.sim - big.act), n: big.n }, pass: Math.abs(big.sim - big.act) <= 0.02 },
    { name: "热门方不胜比例相差 ≤ 3pp", value: { sim: pct(fav.sim), act: pct(fav.act), diff_pp: pct(fav.sim - fav.act), n: fav.n }, pass: Math.abs(fav.sim - fav.act) <= 0.03 },
    { name: "每场红牌数相对差 ≤ 15%", value: { sim: redsSim, act: redsAct, rel: redsSim / redsAct - 1 }, pass: Math.abs(redsSim / redsAct - 1) <= 0.15 },
    { name: "每场点球数相对差 ≤ 15%", value: { sim: pensSim, act: pensAct, rel: pensSim / pensAct - 1 }, pass: Math.abs(pensSim / pensAct - 1) <= 0.15 },
    { name: "进球时间分布逐段相差 ≤ 2pp", value: { sim: simShare.map(pct), act: actShare.map(pct), diff_pp: simShare.map((v, i) => pct(v - actShare[i])) }, pass: simShare.every((v, i) => Math.abs(v - actShare[i]) <= 0.02) },
    { name: "λ 较低一方 模拟 ÷ 期望 均值 ∈ [0.98, 1.02]", value: { low: mean(lowRatios), high: mean(highRatios), n: lowRatios.length }, pass: mean(lowRatios) >= 0.98 && mean(lowRatios) <= 1.02 },
  ];
  const drawCrown = mean(withCrown.map((s) => poisson1x2(s.r.pm.crown!.market_lambda!.home, s.r.pm.crown!.market_lambda!.away)[1]));
  const report = {
    brier_vs_bet365: { sim: brierSimOnB365, bet365: brierB365, sim_minus_bet365: brierSimOnB365 - brierB365, n: withB365.length, bet365_mean_draw_p: drawB365 },
    brier_vs_crown_poisson_note: "附报:Crown AH+OU 经独立 Poisson 反推的胜平负,独立 Poisson 低估平局",
    brier_vs_crown_poisson: { sim: brierSimOnCrown, crown: brierCrown, sim_minus_crown: brierSimOnCrown - brierCrown, n: withCrown.length, crown_mean_draw_p: drawCrown },
    brier_sim_all: brierSim,
    fair_ah_vs_crown: { agree_share: agree, n: withAh.length, diff_distribution: Object.fromEntries(Object.entries(diffs).sort((a, b) => +a[0] - +b[0])) },
  };
  save(`validation_${path}.json`, { path, calibration: cal, n: sims.length, skipped, gates, report, seconds: (Date.now() - t0) / 1000 });
  for (const g of gates) console.log(`${g.pass ? "通过" : "未通过"}  ${g.name}  ${JSON.stringify(g.value, (k, v) => (k === "table" ? undefined : typeof v === "number" ? +v.toFixed(4) : v))}`);
  console.log("如实报告:", JSON.stringify(report, (_k, v) => (typeof v === "number" ? +v.toFixed(4) : v)));
  console.log(`路径 ${path}:验证集 ${sims.length} 场,跳过 ${skipped};全部通过 = ${gates.every((g) => g.pass)}`);
}

// ------------------------------------------------------------------ 侧重点合理性
function focusTest(rows: Row[]) {
  const cal0 = calFromResults("d");
  const useMarket = path === "A";
  const cal = useMarket ? cal0 : { ...cal0, w: 0 };
  const get = paramsCache(cal, { useMarket });
  const valid = rows.filter((r) => inRange(r, VALID) && setupFor(r.pm, { useMarket }));
  const rng = mulberry32(20260930);
  const pool = [...valid];
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  const combos: Focus[][] = FOCUS_LIST.map((f) => [f]);
  for (let i = 0; i < FOCUS_LIST.length; i++) {
    for (let j = i + 1; j < FOCUS_LIST.length; j++) {
      const c = [FOCUS_LIST[i], FOCUS_LIST[j]];
      if (!focusError(c)) combos.push(c);
    }
  }
  const picked: Row[] = [];
  for (const r of pool) {
    if (picked.length === 50) break;
    if (prep(r, get(r.snap), useMarket)) picked.push(r);
  }
  const deltas: Record<string, number[]> = Object.fromEntries(combos.map((c) => [c.join("+"), []]));
  for (const r of picked) {
    const params = get(r.snap);
    const setup = setupFor(r.pm, { useMarket })!;
    const base = prepareMatch(params, setup);
    if (!base.ok) continue;
    const p0 = simulateMany(base.config, r.pm.match_id, RUNS, [0, 0]).pHome;
    for (const c of combos) {
      const res = prepareMatch(params, { ...setup, home: { ...setup.home, focuses: c } });
      if (!res.ok) continue;
      deltas[c.join("+")].push(simulateMany(res.config, r.pm.match_id, RUNS, [0, 0]).pHome - p0);
    }
  }
  const rowsOut = combos.map((c) => {
    const d = deltas[c.join("+")];
    const m = mean(d);
    const single = c.length === 1;
    return {
      focus: c.join("+"),
      single,
      mean_pp: +(m * 100).toFixed(2),
      min_pp: +(Math.min(...d) * 100).toFixed(2),
      max_pp: +(Math.max(...d) * 100).toFixed(2),
      decreased_in: d.filter((x) => x < 0).length,
      n: d.length,
      pass: single ? Math.abs(m) <= 0.06 && d.some((x) => x < 0) : Math.abs(m) <= 0.1,
    };
  });
  save(`focus_test_${path}.json`, { path, matches: picked.map((r) => r.pm.match_id), results: rowsOut });
  for (const r of rowsOut) console.log(`${r.pass ? "通过" : "未通过"}  ${r.focus.padEnd(22)} 平均 ${r.mean_pp}pp  [${r.min_pp}, ${r.max_pp}]  下降 ${r.decreased_in}/${r.n}`);
  console.log(`全部通过 = ${rowsOut.every((r) => r.pass)}`);
}

// ------------------------------------------------------------------ 诊断:跳过原因
function diagnose(rows: Row[]) {
  const get = paramsCache(V02_CAL, { useMarket: true });
  const reasons: Record<string, number> = {};
  const examples: Record<string, string[]> = {};
  for (const r of rows.filter((x) => inRange(x, TRAIN) || inRange(x, VALID))) {
    const setup = setupFor(r.pm, { useMarket: true });
    let reason = "ok";
    if (!setup) {
      const l = r.pm.lineups;
      reason = !l.home || !l.away ? "首发缺失或位置无法解码" : "setup 失败";
    } else {
      const res = prepareMatch(get(r.snap), setup);
      if (!res.ok) reason = res.error.replace(/^[^ ]+ /, "").replace(/\d+(-\d+)+/, "<阵型>");
    }
    reasons[reason] = (reasons[reason] ?? 0) + 1;
    if (reason !== "ok") {
      const list = (examples[reason] ??= []);
      if (list.length < 5) list.push(`${r.pm.match_id}(第${r.pm.round}轮)`);
    }
  }
  console.log(JSON.stringify({ reasons, examples }, null, 1));
}

const rows = loadRows();
console.log(`载入 ${rows.length} 场(训练 ${rows.filter((r) => inRange(r, TRAIN)).length},验证 ${rows.filter((r) => inRange(r, VALID)).length})`);
if (cmd === "ha-k") stepA(rows);
else if (cmd === "w") stepB(rows);
else if (cmd === "empirical") stepC(rows);
else if (cmd === "rate-model") stepRateModel(rows);
else if (cmd === "kappa") stepD(rows);
else if (cmd === "validate") validate(rows);
else if (cmd === "focus") focusTest(rows);
else if (cmd === "diag") diagnose(rows);
else console.log("未知命令", cmd);
