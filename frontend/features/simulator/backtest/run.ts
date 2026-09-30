// Phase 2 回测与校准(本地 Node 运行,调用与页面同一套 engine.ts)。
// 口径:docs/simulator-model.md「Phase 2 回测与校准方法」。
// 用法:node run.mjs <ha-k|strength|w|rate-model|validate|focus|diag> --dir <.local-data/simulator/backtest> [--path A|B]
// 前瞻复检:node run.mjs forward --dir <.local-data/simulator/forward_2026-2027> --cal-file <calibration_v0.3.json> --path A|B

import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { FOCUS_LIST, focusError, prepareMatch, simulateMany, type Focus, type MatchConfig } from "../engine";
import { mulberry32 } from "../rng";
import type { LeagueParams, RateModel, ShotChannel, SimParams, StrengthModel } from "../types";
import { setupFor, simParamsFor, BASE_CAL, type Cal } from "./assemble";
import { brier3, calibrationSlope, goldenMax, mean, outcomeIndex, poisson1x2, poissonLogLik, timingBucket } from "./stats";
import type { Outcome, Prematch, Snapshot } from "./types";

const K_GRID = [3, 5, 8, 12];
const W_GRID = [0.5, 0.7, 0.9, 1.0];
const RUNS = 2000;
const TRAIN: [number, number] = [4, 19];
const VALID: [number, number] = [20, 38];
/** 前瞻复检:第 1–3 轮冷启动不评估(与 Phase 2 同口径),其余全部已完赛轮次 */
const FORWARD: [number, number] = [4, 99];
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
const calFile = args.includes("--cal-file") ? args[args.indexOf("--cal-file") + 1] : null;
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

/** v0.3 的校准链:a(合并 h、k)→ 强度回归 → b(w)→ rm(时段 / 比分状态 / 红牌回归);标准档 κ 关闭。 */
function calFromResults(upTo: "a" | "strength" | "b" | "rm"): Cal {
  const a = loadRes<{ k: number; h: Record<string, number> }>("step_a.json");
  let cal: Cal = { ...BASE_CAL, k: a.k, h: a.h };
  if (upTo === "a") return cal;
  cal = { ...cal, strength_model: loadRes<{ strength_model: StrengthModel }>("step_strength.json").strength_model };
  if (upTo === "strength") return cal;
  cal = { ...cal, w: loadRes<{ w: number }>("step_b.json").w };
  if (upTo === "b") return cal;
  return { ...cal, rate_model: loadRes<{ rate_model: RateModel }>("step_rm.json").rate_model };
}

/** 生产校准文件(scripts/simulator/calibration_v0.3.json)→ Cal;前瞻复检用,不做任何拟合。 */
function calFromFile(p: string): Cal {
  const c = readJson<{
    k_team: number;
    home_advantage: number;
    market_w: number;
    kappa: number;
    strength_model: StrengthModel;
    rate_model: RateModel;
  }>(p);
  return {
    k: c.k_team,
    h: null,
    home_advantage: c.home_advantage,
    w: c.market_w,
    kappa: c.kappa,
    strength_model: c.strength_model,
    rate_model: c.rate_model,
  };
}

// ------------------------------------------------------------------ a. HA 与 k
function stepA(rows: Row[]) {
  // v0.3 第 9 条:五大联赛合并估计一个 h;k 的选择规则不变
  const train = rows.filter((r) => inRange(r, TRAIN));
  const byK: Record<string, { ll: number; h: number; n: number; skipped: number }> = {};
  const dataByK: Record<string, { bH: number; bA: number; og: number; yH: number; yA: number }[]> = {};
  for (const k of K_GRID) {
    const get = paramsCache({ ...BASE_CAL, k }, { useMarket: false, hOverride: 1 });
    const data: { bH: number; bA: number; og: number; yH: number; yA: number }[] = [];
    let skipped = 0;
    for (const r of train) {
      const params = get(r.snap);
      const cfg = prep(r, params, false);
      if (!cfg) {
        skipped++;
        continue;
      }
      data.push({
        bH: cfg.teams[0].breakdown.lambdaModel,
        bA: cfg.teams[1].breakdown.lambdaModel,
        og: params.leagues[String(r.pm.league_id)].mu.owngoal,
        yH: r.out.score[0],
        yA: r.out.score[1],
      });
    }
    const f = (x: number) => data.reduce((s2, m) => s2 + poissonLogLik(m.yH, m.bH * x + m.og) + poissonLogLik(m.yA, m.bA / x + m.og), 0);
    const h = goldenMax(f, H_RANGE[0], H_RANGE[1]);
    byK[String(k)] = { ll: f(h), h, n: data.length, skipped };
    dataByK[String(k)] = data;
    console.log(`k=${k}: logLik=${f(h).toFixed(3)} n=${data.length} skipped=${skipped} h(合并)=${h.toFixed(4)}`);
  }
  const best = K_GRID.reduce((b, k) => (byK[String(k)].ll > byK[String(b)].ll ? k : b), K_GRID[0]);
  const data = dataByK[String(best)];
  const f = (x: number) => data.reduce((s2, m) => s2 + poissonLogLik(m.yH, m.bH * x + m.og) + poissonLogLik(m.yA, m.bA / x + m.og), 0);
  const hHat = byK[String(best)].h;
  const target = f(hHat) - 1.92;
  const solve = (lo: number, hi: number) => {
    for (let i = 0; i < 60; i++) {
      const mid = (lo + hi) / 2;
      if ((f(mid) - target) * (f(lo) - target) > 0) lo = mid;
      else hi = mid;
    }
    return (lo + hi) / 2;
  };
  const ci: [number, number] = [solve(0.5, hHat), solve(hHat, 2.5)];
  const leagues = [...new Set(train.map((r) => String(r.pm.league_id)))];
  save("step_a.json", { k: best, h: Object.fromEntries(leagues.map((l) => [l, hHat])), h_pooled: hHat, h_ci95: ci, two_log_h: 2 * Math.log(hHat), by_k: byK });
  console.log(`选定 k=${best};合并 h = ${hHat.toFixed(4)},95% CI [${ci[0].toFixed(4)}, ${ci[1].toFixed(4)}](${data.length} 场);2·log h = ${(2 * Math.log(hHat)).toFixed(4)}`);
}

// ------------------------------------------------------------------ v0.3 第 7 条:数据模型强度回归
function ols(X: number[][], y: number[]) {
  const p = X[0].length;
  const XtX = Array.from({ length: p }, (_, i) => Array.from({ length: p }, (_, j) => X.reduce((s2, r) => s2 + r[i] * r[j], 0)));
  const Xty = Array.from({ length: p }, (_, i) => X.reduce((s2, r, n) => s2 + r[i] * y[n], 0));
  const beta = solveLinear(XtX, Xty);
  const rss = X.reduce((s2, r, n) => s2 + (y[n] - r.reduce((a, v, i) => a + v * beta[i], 0)) ** 2, 0);
  const sigma2 = rss / (X.length - p);
  const inv = invert(XtX);
  const se = beta.map((_, i) => Math.sqrt(sigma2 * inv[i][i]));
  return { beta, se, rss, n: X.length, p };
}

function stepStrength(rows: Row[]) {
  const train = rows.filter((r) => inRange(r, TRAIN) && r.pm.crown?.market_lambda);
  const combos: { window: "10" | "20" | "38"; basis: "xg" | "goals" | "both"; k: 0 | 5 }[] = [];
  for (const window of ["10", "20", "38"] as const) for (const basis of ["xg", "goals", "both"] as const) for (const k of [0, 5] as const) combos.push({ window, basis, k });
  const results = [];
  let best: { rss: number; idx: number } = { rss: Infinity, idx: -1 };
  for (const [ci, c] of combos.entries()) {
    const X: number[][] = [];
    const y: number[] = [];
    let floorRows = 0;
    const ki = c.k === 0 ? 0 : 1;
    for (const r of train) {
      const L = r.snap.params.leagues[String(r.pm.league_id)];
      const mu = { xg: SHOT.reduce((s2, ch) => s2 + L.mu[ch], 0), goals: L.goals_per_team_match! };
      const teams = [r.snap.params.teams[String(r.pm.home_team_id)], r.snap.params.teams[String(r.pm.away_team_id)]];
      const lam = [r.pm.crown!.market_lambda!.home, r.pm.crown!.market_lambda!.away];
      for (const t of [0, 1]) {
        const own = teams[t].strength![c.window];
        const opp = teams[1 - t].strength![c.window];
        const row = [1];
        for (const basis of c.basis === "both" ? (["xg", "goals"] as const) : [c.basis]) {
          const a = own[basis].A[ki];
          const d = opp[basis].D[ki];
          if (a <= 0.05 + 1e-12 || d <= 0.05 + 1e-12) floorRows++;
          row.push(Math.log(a / mu[basis]), Math.log(d / mu[basis]));
        }
        row.push(t === 0 ? 1 : 0);
        X.push(row);
        y.push(Math.log(lam[t]));
      }
    }
    const fit = ols(X, y);
    const names = ["α", ...(c.basis === "both" ? ["γ_A(xG)", "γ_D(xG)", "γ_A(进球)", "γ_D(进球)"] : c.basis === "xg" ? ["γ_A(xG)", "γ_D(xG)"] : ["γ_A(进球)", "γ_D(进球)"]), "β_home"];
    const coef = names.map((name, i) => ({ name, coef: fit.beta[i], se: fit.se[i], ci95: [fit.beta[i] - 1.96 * fit.se[i], fit.beta[i] + 1.96 * fit.se[i]] }));
    results.push({ ...c, rss: fit.rss, n: fit.n, p: fit.p, floor_rows: floorRows, coefficients: coef });
    console.log(`窗口 ${c.window.padStart(2)} | ${c.basis.padEnd(5)} | k=${c.k} | RSS ${fit.rss.toFixed(4)} | n=${fit.n} p=${fit.p} | 下限行 ${floorRows}`);
    if (fit.rss < best.rss) best = { rss: fit.rss, idx: ci };
  }
  const chosen = results[best.idx];
  const b = chosen.coefficients.map((x) => x.coef);
  const gamma: StrengthModel["gamma"] = {};
  if (chosen.basis === "xg") Object.assign(gamma, { xg_A: b[1], xg_D: b[2] });
  else if (chosen.basis === "goals") Object.assign(gamma, { goals_A: b[1], goals_D: b[2] });
  else Object.assign(gamma, { xg_A: b[1], xg_D: b[2], goals_A: b[3], goals_D: b[4] });
  const strength_model: StrengthModel = { window: chosen.window, basis: chosen.basis, k: chosen.k, alpha: b[0], gamma, beta_home: b[b.length - 1] };
  save("step_strength.json", { strength_model, chosen, all: results });
  console.log(`选定:窗口 ${chosen.window} | ${chosen.basis} | k=${chosen.k}`);
  for (const x of chosen.coefficients) console.log(`  ${x.name.padEnd(10)} ${x.coef.toFixed(4)}  SE ${x.se.toFixed(4)}  95% CI [${x.ci95[0].toFixed(4)}, ${x.ci95[1].toFixed(4)}]`);
}

// ------------------------------------------------------------------ b. 市场权重 w
function stepB(rows: Row[]) {
  const cal = calFromResults("strength");
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

// ------------------------------------------------------------------ 实际比赛时间线(补时 = max(宣布, 观测))
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

/** v0.3 第 12 条:热门方 = 本路径模拟胜率(主胜或客胜)≥ 60% 的一方。 */
function favNotWinSelf(sims: SimRow[]) {
  const fav = sims.filter((s) => s.pH >= FAV_THRESHOLD || s.pA >= FAV_THRESHOLD);
  const side = (s: SimRow) => (s.pH >= FAV_THRESHOLD ? 0 : 2);
  return {
    sim: mean(fav.map((s) => (side(s) === 0 ? 1 - s.pH : 1 - s.pA))),
    act: mean(fav.map((s) => (outcomeIndex(s.r.out.score) === side(s) ? 0 : 1))),
    n: fav.length,
  };
}

/** v0.3 第 17 条:比例类闸门容差 = max(原容差, 1.96 × SE),SE = sqrt(p(1−p)/n),p 为实际比例。 */
function propTol(orig: number, p: number, n: number) {
  const se = Math.sqrt((p * (1 - p)) / n);
  return { se, tol: Math.max(orig, 1.96 * se) };
}

/** v0.3 第 17 条:计数类(Poisson)SE = sqrt(实际总数) / 场次;relative = true 时换算成相对误差。 */
function countTol(orig: number, total: number, n: number, relative: boolean) {
  const se = Math.sqrt(total) / n;
  const seUsed = relative ? se / (total / n) : se;
  return { se: seUsed, tol: Math.max(orig, 1.96 * seUsed) };
}

/** v0.3 第 15 条:Bet365 热门方子集上"模拟不胜 − Bet365 去水不胜"的均值与按场次 bootstrap 的 95% CI。 */
function bet365FavDiagnostic(sims: SimRow[]) {
  const d: number[] = [];
  for (const s of sims) {
    const f = favouriteOf(s.r.pm.match_id);
    if (f === null) continue;
    const b = bet365.get(s.r.pm.match_id)!;
    const simNot = f === 0 ? 1 - s.pH : 1 - s.pA;
    const bNot = f === 0 ? 1 - b[0] : 1 - b[2];
    d.push(simNot - bNot);
  }
  const rng = mulberry32(20260930);
  const boots: number[] = [];
  for (let b = 0; b < 10000; b++) {
    let sum = 0;
    for (let i = 0; i < d.length; i++) sum += d[Math.floor(rng() * d.length)];
    boots.push(sum / d.length);
  }
  boots.sort((x, y) => x - y);
  return { n: d.length, mean_diff: mean(d), ci95: [boots[Math.floor(0.025 * boots.length)], boots[Math.floor(0.975 * boots.length) - 1]] };
}

function bigMargin(sims: SimRow[]) {
  return {
    sim: mean(sims.map((s) => s.pBig)),
    act: mean(sims.map((s) => (Math.abs(s.r.out.score[0] - s.r.out.score[1]) >= 3 ? 1 : 0))),
    n: sims.length,
  };
}

// ------------------------------------------------------------------ 闸门(验证集)
function validate(rows: Row[], opts: { cal?: Cal; range?: [number, number]; tag?: "forward" } = {}) {
  // 路径 A:w 取训练集选出的值(有盘口时定锚);路径 B:w = 0(只用数据模型)
  const cal0 = opts.cal ?? calFromResults("rm");
  const cal = path === "B" ? { ...cal0, w: 0 } : cal0;
  const valid = rows.filter((r) => inRange(r, opts.range ?? VALID));
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
  const fav = favNotWinSelf(sims);
  const favB365 = favNotWin(sims);
  const drawT = propTol(0.03, drawAct, sims.length);
  const bigT = propTol(0.02, big.act, big.n);
  const favT = propTol(0.03, fav.act, fav.n);
  const redsSim = mean(sims.map((s) => s.reds));
  const redsAct = mean(sims.map((s) => s.r.out.reds.length));
  const pensSim = mean(sims.map((s) => s.pens));
  const pensAct = mean(sims.map((s) => s.r.out.penalty_attempts[0] + s.r.out.penalty_attempts[1]));
  const n = sims.length;
  const goalsT = countTol(0.15, goalsAct * n, n, false);
  const redsT = countTol(0.15, redsAct * n, n, true);
  const pensT = countTol(0.15, pensAct * n, n, true);
  const simB = [0, 0, 0, 0, 0, 0];
  const actB = [0, 0, 0, 0, 0, 0];
  for (const s of sims) {
    s.buckets.forEach((v, i) => (simB[i] += v));
    for (const g of s.r.out.goals) actB[timingBucket(g.minute)] += 1;
  }
  const simShare = simB.map((v) => v / simB.reduce((a, b) => a + b, 0));
  const actGoals = actB.reduce((a, b) => a + b, 0);
  const actShare = actB.map((v) => v / actGoals);
  const timeT = actShare.map((p) => propTol(0.02, p, actGoals));
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
    { name: "平局率相差 ≤ max(3pp, 1.96·SE)", value: { sim: pct(drawSim), act: pct(drawAct), diff_pp: pct(drawSim - drawAct), n: sims.length, se_pp: pct(drawT.se), tol_pp: pct(drawT.tol) }, pass: Math.abs(drawSim - drawAct) <= drawT.tol },
    { name: "场均总进球相差 ≤ max(0.15, 1.96·SE)", value: { sim: goalsSim, act: goalsAct, diff: goalsSim - goalsAct, n, total: goalsAct * n, se: goalsT.se, tol: goalsT.tol }, pass: Math.abs(goalsSim - goalsAct) <= goalsT.tol },
    { name: "净胜 ≥3 球比例相差 ≤ max(2pp, 1.96·SE)", value: { sim: pct(big.sim), act: pct(big.act), diff_pp: pct(big.sim - big.act), n: big.n, se_pp: pct(bigT.se), tol_pp: pct(bigT.tol) }, pass: Math.abs(big.sim - big.act) <= bigT.tol },
    { name: "热门方(本路径模拟胜率 ≥60%)不胜比例相差 ≤ max(3pp, 1.96·SE)", value: { sim: pct(fav.sim), act: pct(fav.act), diff_pp: pct(fav.sim - fav.act), n: fav.n, se_pp: pct(favT.se), tol_pp: pct(favT.tol) }, pass: Math.abs(fav.sim - fav.act) <= favT.tol },
    { name: "每场红牌数相对差 ≤ max(15%, 1.96·SE)", value: { sim: redsSim, act: redsAct, rel: redsSim / redsAct - 1, n, total: redsAct * n, se_rel: redsT.se, tol_rel: redsT.tol }, pass: Math.abs(redsSim / redsAct - 1) <= redsT.tol },
    { name: "每场点球数相对差 ≤ max(15%, 1.96·SE)", value: { sim: pensSim, act: pensAct, rel: pensSim / pensAct - 1, n, total: pensAct * n, se_rel: pensT.se, tol_rel: pensT.tol }, pass: Math.abs(pensSim / pensAct - 1) <= pensT.tol },
    { name: "进球时间分布逐段相差 ≤ max(2pp, 1.96·SE)", value: { sim: simShare.map(pct), act: actShare.map(pct), diff_pp: simShare.map((v, i) => pct(v - actShare[i])), goals: actGoals, se_pp: timeT.map((t) => pct(t.se)), tol_pp: timeT.map((t) => pct(t.tol)) }, pass: simShare.every((v, i) => Math.abs(v - actShare[i]) <= timeT[i].tol) },
    { name: "λ 较低一方 模拟 ÷ 期望 均值 ∈ [0.98, 1.02]", value: { low: mean(lowRatios), high: mean(highRatios), n: lowRatios.length }, pass: mean(lowRatios) >= 0.98 && mean(lowRatios) <= 1.02 },
  ];
  const drawCrown = mean(withCrown.map((s) => poisson1x2(s.r.pm.crown!.market_lambda!.home, s.r.pm.crown!.market_lambda!.away)[1]));
  const report = {
    diag_fav_bet365_selected: { sim: pct(favB365.sim), act: pct(favB365.act), diff_pp: pct(favB365.sim - favB365.act), n: favB365.n },
    diag_sim_minus_bet365_notwin_on_bet365_favs: bet365FavDiagnostic(sims),
    brier_vs_bet365: { sim: brierSimOnB365, bet365: brierB365, sim_minus_bet365: brierSimOnB365 - brierB365, n: withB365.length, bet365_mean_draw_p: drawB365 },
    brier_vs_crown_poisson_note: "附报:Crown AH+OU 经独立 Poisson 反推的胜平负,独立 Poisson 低估平局",
    brier_vs_crown_poisson: { sim: brierSimOnCrown, crown: brierCrown, sim_minus_crown: brierSimOnCrown - brierCrown, n: withCrown.length, crown_mean_draw_p: drawCrown },
    brier_sim_all: brierSim,
    fair_ah_vs_crown: { agree_share: agree, n: withAh.length, diff_distribution: Object.fromEntries(Object.entries(diffs).sort((a, b) => +a[0] - +b[0])) },
  };
  const rounds = [...new Set(sims.map((s) => `${s.r.pm.league_id}:${s.r.pm.round}`))].length;
  const lastDate = sims.reduce((d, s) => (s.r.pm.date > d ? s.r.pm.date : d), "");
  save(`${opts.tag ?? "validation"}_${path}.json`, { path, calibration: cal, n: sims.length, skipped, league_rounds: rounds, last_date: lastDate, gates, report, seconds: (Date.now() - t0) / 1000 });
  for (const g of gates) console.log(`${g.pass ? "通过" : "未通过"}  ${g.name}  ${JSON.stringify(g.value, (k, v) => (k === "table" ? undefined : typeof v === "number" ? +v.toFixed(4) : v))}`);
  console.log("如实报告:", JSON.stringify(report, (_k, v) => (typeof v === "number" ? +v.toFixed(4) : v)));
  const label = opts.tag === "forward" ? `前瞻复检(第 ${(opts.range ?? VALID)[0]} 轮起,截至 ${lastDate})` : "验证集";
  console.log(`路径 ${path}:${label} ${sims.length} 场,跳过 ${skipped};全部通过 = ${gates.every((g) => g.pass)}`);
}

// ------------------------------------------------------------------ 侧重点合理性
function focusTest(rows: Row[]) {
  const cal0 = calFromResults("rm");
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
  const get = paramsCache(BASE_CAL, { useMarket: true });
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
if (cmd !== "forward") console.log(`载入 ${rows.length} 场(训练 ${rows.filter((r) => inRange(r, TRAIN)).length},验证 ${rows.filter((r) => inRange(r, VALID)).length})`);
if (cmd === "ha-k") stepA(rows);
else if (cmd === "w") stepB(rows);
else if (cmd === "rate-model") stepRateModel(rows);
else if (cmd === "strength") stepStrength(rows);
else if (cmd === "validate") validate(rows);
else if (cmd === "forward") {
  if (!calFile) throw new Error("forward 需要 --cal-file");
  const byRound = (lo: number, hi: number) => rows.filter((r) => r.pm.round != null && r.pm.round >= lo && r.pm.round <= hi).length;
  console.log(`前瞻复检:已完赛 ${rows.length} 场;第 1–3 轮冷启动不评估 ${byRound(1, 3)} 场;无轮次 ${rows.filter((r) => r.pm.round == null).length} 场`);
  validate(rows, { cal: calFromFile(calFile), range: FORWARD, tag: "forward" });
}
else if (cmd === "focus") focusTest(rows);
else if (cmd === "diag") diagnose(rows);
else console.log("未知命令", cmd);
