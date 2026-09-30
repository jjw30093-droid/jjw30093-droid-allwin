"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { Chip } from "@/components/ui/Chip";
import {
  FOCUS_LABEL,
  FOCUS_LIST,
  focusError,
  matchingFixture,
  prepareMatch,
  type Focus,
  type ManyResult,
  type MatchSetup,
  type SingleResult,
  type TeamSetup,
} from "@/features/simulator/engine";
import {
  channelDeltas,
  FIT_THRESHOLD_PP,
  formatDeltas,
  IMPACT_RUNS,
  impactJobs,
  summarizeImpact,
  type ImpactJob,
  type SideImpact,
} from "@/features/simulator/focusImpact";
import { lastLineupSetup } from "@/features/simulator/formation";
import { decodeResult, parseSetupQuery, parseTeamsQuery, resultToken, toTeamSetup } from "@/features/simulator/shareLink";
import { makeSnapshot, modelVersionOf, type ResultSnapshot } from "@/features/simulator/snapshot";
import type { SimParams } from "@/features/simulator/types";
import { useSimTeamColors } from "@/features/simulator/useSimTeamColors";
import type { FixtureIndexEntry } from "./loadParams";
import { LineupEditor } from "./LineupEditor";
import { MatchAnimation } from "./MatchAnimation";
import { ResultView } from "./ResultView";
import styles from "./simulator.module.css";

// 只开放 v0.3 校准过的五大联赛(docs/simulator-launch-plan.md §2)
const LEAGUE_NAME: Record<string, string> = { "47": "英超", "87": "西甲", "55": "意甲", "54": "德甲", "53": "法甲" };
const LEAGUE_ORDER = ["47", "87", "55", "54", "53"];
const DEFAULT_SEED = 20260929;
const RUNS = 1000;
const IMPACT_DEBOUNCE_MS = 300;

const pct1 = (x: number) => `${(x * 100).toFixed(1)}%`;
const signedPp = (pp: number) => `${pp > 0 ? "+" : pp < 0 ? "−" : "±"}${Math.abs(pp).toFixed(1)} 个百分点`;

type Phase = "setup" | "running" | "animating" | "result";

function teamsOf(params: SimParams, leagueId: string) {
  return Object.values(params.teams)
    .filter((t) => String(t.league_id) === leagueId)
    .sort((a, b) => (a.name_zh ?? "").localeCompare(b.name_zh ?? "", "zh"));
}

function pickDefaultPair(params: SimParams, leagueId: string): [number, number] {
  const ts = teamsOf(params, leagueId);
  const ars = ts.find((t) => t.team_id === 9825);
  const city = ts.find((t) => t.team_id === 8456);
  if (ars && city) return [ars.team_id, city.team_id];
  return [ts[0].team_id, ts[1].team_id];
}

// 参数按联赛下发(§3.2):params 只含当前联赛的球队/球员/赛程;切换联赛 = 跳转 /simulator?lg=<id>,服务端重新切片。
export function SimulatorClient({
  params,
  leagueId: leagueNum,
  fixtureIndex,
  paramsStale,
}: {
  params: SimParams;
  leagueId: number;
  fixtureIndex: FixtureIndexEntry[];
  paramsStale: boolean;
}) {
  const router = useRouter();
  const leagueId = String(leagueNum);
  const leagueIds = LEAGUE_ORDER.filter((id) => params.leagues[id]);
  const [pair, setPair] = useState<[number, number]>(() => pickDefaultPair(params, leagueId));
  const [home, setHome] = useState<TeamSetup>(() => lastLineupSetup(params, pair[0]));
  const [away, setAway] = useState<TeamSetup>(() => lastLineupSetup(params, pair[1]));
  const [useMarket, setUseMarket] = useState(true);
  const [fixtureChoice, setFixtureChoice] = useState<number | null>(null);
  const [chaos, setChaos] = useState(false);
  const [seed, setSeed] = useState(DEFAULT_SEED);
  const [selected, setSelected] = useState<{ side: 0 | 1; i: number } | null>(null);
  const [phase, setPhase] = useState<Phase>("setup");
  // shared = 来自分享链接的结果(原样展示,未重新计算)
  const [result, setResult] = useState<{ snap: ResultSnapshot; shared: boolean } | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const teamColors = useSimTeamColors();
  const colorVars = {
    "--sim-home": teamColors.surface[0],
    "--sim-away": teamColors.surface[1],
    "--sim-home-pitch": teamColors.pitch[0],
    "--sim-away-pitch": teamColors.pitch[1],
  } as CSSProperties;

  const setTeams = (h: number, a: number, fixtureId: number | null = null) => {
    setPair([h, a]);
    setHome(lastLineupSetup(params, h));
    setAway(lastLineupSetup(params, a));
    setFixtureChoice(fixtureId);
    setSelected(null);
  };

  const fixtures = useMemo(
    () =>
      [...fixtureIndex].sort((a, b) =>
        a.status === b.status ? b.kickoff_at_utc.localeCompare(a.kickoff_at_utc) : a.status === "未开赛" ? -1 : 1,
      ),
    [fixtureIndex],
  );
  const pairFixtures = matchingFixture(params, pair[0], pair[1]);
  const fixtureId = useMarket ? (pairFixtures.find((f) => f.match_id === fixtureChoice) ?? pairFixtures[0])?.match_id ?? null : null;

  const prepared = useMemo(
    () => prepareMatch(params, { leagueId: Number(leagueId), home, away, fixtureId, chaos }),
    [params, leagueId, home, away, fixtureId, chaos],
  );

  const run = useCallback(
    (runSeed: number) => {
      if (!prepared.ok) return;
      workerRef.current?.terminate();
      const w = new Worker(new URL("../../features/simulator/simulator.worker.ts", import.meta.url), { type: "module" });
      workerRef.current = w;
      setPhase("running");
      const config = prepared.config;
      const setupSnapshot: MatchSetup = { leagueId: Number(leagueId), home, away, fixtureId, chaos };
      w.onmessage = (e: MessageEvent<{ single: SingleResult; many: ManyResult }>) => {
        setResult({ snap: makeSnapshot(params, setupSnapshot, config, e.data.single, e.data.many), shared: false });
        setLinkError(null);
        // 新的模拟不再对应地址栏里的分享链接
        if (window.location.search !== `?lg=${leagueId}` || window.location.hash) {
          window.history.replaceState(null, "", `${window.location.pathname}?lg=${leagueId}`);
        }
        setPhase("animating");
        w.terminate();
        workerRef.current = null;
      };
      w.postMessage({ config, seed: runSeed, runs: RUNS });
    },
    [prepared, params, leagueId, home, away, fixtureId, chaos],
  );

  // 打开分享链接:查询参数恢复设定;# 片段里有结果时在浏览器端解压并原样展示(不重新计算)。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const shared = parseSetupQuery(window.location.search);
      const teamsOnly = shared ? null : parseTeamsQuery(window.location.search);
      const token = resultToken(window.location.hash);
      if (!shared && !teamsOnly && !token) return;
      const snap = token ? await decodeResult(token) : null;
      if (cancelled) return;
      const inLeague = (h: number, a: number, lg: number) =>
        lg === leagueNum && !!params.teams[String(h)] && !!params.teams[String(a)] && h !== a;
      if (teamsOnly && inLeague(teamsOnly.home, teamsOnly.away, teamsOnly.leagueId)) {
        // 只带两队(跨联赛点选真实比赛、第二次发版的比赛页入口):两队用各自最近一场首发
        setTeams(teamsOnly.home, teamsOnly.away, teamsOnly.fixtureId);
        setUseMarket(teamsOnly.fixtureId != null);
      }
      if (shared && inLeague(shared.home.teamId, shared.away.teamId, shared.leagueId)) {
        setPair([shared.home.teamId, shared.away.teamId]);
        setHome(toTeamSetup(params, shared.home));
        setAway(toTeamSetup(params, shared.away));
        setUseMarket(shared.fixtureId != null);
        setFixtureChoice(shared.fixtureId);
        setChaos(shared.chaos);
        setSeed(shared.seed);
      }
      if (snap) {
        setResult({ snap, shared: true });
        setPhase("result");
      } else if (token) {
        setLinkError("分享链接中的结果无法解析,只恢复了设定。");
      }
    })();
    return () => {
      cancelled = true;
    };
    // setTeams 只依赖 params(与本 effect 相同);只在挂载时解析一次地址栏
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, leagueNum]);
  const linkStale =
    result?.shared && (result.snap.paramsDate !== params.meta.generated_at || result.snap.modelVersion !== modelVersionOf(params));

  const finishAnimation = useCallback(() => setPhase("result"), []);

  // 侧重点的胜率净影响:排阵阶段在后台 worker 里算(同一种子、每个配置 1000 次),结果按设定 key 对齐,过期结果不展示。
  const impactSetup: MatchSetup = useMemo(
    () => ({ leagueId: Number(leagueId), home, away, fixtureId, chaos }),
    [leagueId, home, away, fixtureId, chaos],
  );
  const impactKey = useMemo(() => JSON.stringify([impactSetup, seed]), [impactSetup, seed]);
  const [impact, setImpact] = useState<{ key: string; sides: [SideImpact | null, SideImpact | null] } | null>(null);
  const impactWorkerRef = useRef<Worker | null>(null);
  useEffect(() => {
    if (phase !== "setup" || !prepared.ok) return;
    const timer = window.setTimeout(() => {
      impactWorkerRef.current?.terminate();
      const w = new Worker(new URL("../../features/simulator/simulator.worker.ts", import.meta.url), { type: "module" });
      impactWorkerRef.current = w;
      w.onmessage = (e: MessageEvent<{ results: { side: 0 | 1; kind: ImpactJob["kind"]; win: number }[] }>) => {
        setImpact({ key: impactKey, sides: summarizeImpact(e.data.results) });
        w.terminate();
        if (impactWorkerRef.current === w) impactWorkerRef.current = null;
      };
      w.postMessage({ kind: "impact", jobs: impactJobs(params, impactSetup), seed, runs: IMPACT_RUNS });
    }, IMPACT_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [phase, prepared.ok, params, impactSetup, impactKey, seed]);
  useEffect(() => () => impactWorkerRef.current?.terminate(), []);
  const impactNow = impact?.key === impactKey ? impact.sides : null;
  const cal = params.calibration;

  const toggleFocus = (side: 0 | 1, f: Focus) => {
    const cur = side === 0 ? home : away;
    const focuses = cur.focuses.includes(f) ? cur.focuses.filter((x) => x !== f) : [...cur.focuses, f];
    (side === 0 ? setHome : setAway)({ ...cur, focuses });
  };

  const teamList = teamsOf(params, leagueId);
  const names: [string, string] = [
    params.teams[String(home.teamId)].name_zh ?? "",
    params.teams[String(away.teamId)].name_zh ?? "",
  ];

  return (
    <main className={styles.page} style={colorVars}>
      <header className={styles.header}>
        <h1 className={styles.title}>比赛模拟器</h1>
        <span className={styles.badge} data-testid="uncalibrated-badge">{params.calibration ? "原型" : "未校准原型"}</span>
        <span className={styles.meta}>
          模型 {params.meta.effective_version ?? params.meta.model_version} · 参数导出于 {params.meta.generated_at} ·{" "}
          {params.calibration
            ? "进球率、主场系数与时段系数经 25/26 五大联赛回测校准;侧重点乘数未校准。结果不代表预测"
            : "全部参数为初始假设,尚未回测校准,结果不代表预测"}
        </span>
        {paramsStale ? (
          <span className={styles.hint} data-testid="params-stale">
            参数不是最新的(上次更新 {params.meta.generated_at}),每日更新可能延迟。
          </span>
        ) : null}
      </header>

      {linkError ? <p className={styles.error}>{linkError}</p> : null}

      {phase === "animating" && result ? (
        <MatchAnimation single={result.snap.single} names={[result.snap.teams[0].name, result.snap.teams[1].name]} onDone={finishAnimation} />
      ) : null}

      {phase === "result" && result?.shared ? (
        <section className={`${styles.card} ${styles.linkNotice}`} data-testid="shared-notice">
          <p style={{ margin: 0 }}>
            这是分享链接里的模拟结果,原样展示,未重新计算(模型 {result.snap.modelVersion} · 参数导出于 {result.snap.paramsDate} · 种子{" "}
            {result.snap.seed})。
          </p>
          {linkStale ? (
            <div className={styles.row} style={{ marginTop: 8 }} data-testid="params-updated">
              <span className={styles.hint} style={{ marginTop: 0 }}>
                参数已更新:当前参数导出于 {params.meta.generated_at}(模型 {modelVersionOf(params)})。
              </span>
              <button type="button" className={styles.primaryBtn} disabled={!prepared.ok} onClick={() => run(result.snap.seed)}>
                用最新参数重新模拟
              </button>
              {!prepared.ok ? <span className={styles.error}>{prepared.error}</span> : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {phase === "result" && result ? (
        <ResultView
          snap={result.snap}
          onRerun={() => {
            const s = Math.floor(Math.random() * 2 ** 31);
            setSeed(s);
            run(s);
          }}
          onBack={() => setPhase("setup")}
        />
      ) : null}

      {phase === "setup" || phase === "running" ? (
        <>
          <section className={styles.card}>
            <h2 className={styles.cardTitle}>选择对阵</h2>
            <div className={styles.row}>
              <label className={styles.field}>
                联赛
                <select
                  className={styles.select}
                  value={leagueId}
                  onChange={(e) => router.push(`/simulator?lg=${e.target.value}`)}
                >
                  {leagueIds.map((id) => (
                    <option key={id} value={id}>
                      {LEAGUE_NAME[id] ?? id}
                    </option>
                  ))}
                </select>
              </label>
              <label className={styles.field}>
                主队
                <select className={styles.select} value={pair[0]} onChange={(e) => setTeams(Number(e.target.value), pair[1])}>
                  {teamList.map((t) => (
                    <option key={t.team_id} value={t.team_id} disabled={t.team_id === pair[1]}>
                      {t.name_zh}
                    </option>
                  ))}
                </select>
              </label>
              <label className={styles.field}>
                客队
                <select className={styles.select} value={pair[1]} onChange={(e) => setTeams(pair[0], Number(e.target.value))}>
                  {teamList.map((t) => (
                    <option key={t.team_id} value={t.team_id} disabled={t.team_id === pair[0]}>
                      {t.name_zh}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className={styles.muted} style={{ marginTop: 12 }}>
              或选一场有 Crown 盘口的真实比赛(用于测试市场定锚):
            </p>
            <div className={styles.fixtureList}>
              {fixtures.map((f) => {
                const h = f.home_name;
                const a = f.away_name;
                const active = fixtureId === f.match_id;
                return (
                  <button
                    key={f.match_id}
                    type="button"
                    className={`${styles.fixtureBtn} ${active ? styles.fixtureBtnActive : ""}`}
                    onClick={() => {
                      if (f.league_id !== leagueNum) {
                        router.push(`/simulator?lg=${f.league_id}&h=${f.home_team_id}&a=${f.away_team_id}&fx=${f.match_id}`);
                        return;
                      }
                      setUseMarket(true);
                      setTeams(f.home_team_id, f.away_team_id, f.match_id);
                    }}
                  >
                    [{f.status}] {LEAGUE_NAME[String(f.league_id)]} {h} vs {a} · {f.kickoff_at_utc.slice(0, 10)} · 让 {f.ah_line ?? "—"} · 大小 {f.ou_line ?? "—"}
                    {f.final_score ? ` · 实际 ${f.final_score.join(":")}` : ""}
                  </button>
                );
              })}
            </div>
            {fixtureId != null && params.fixtures[String(fixtureId)]?.status !== "未开赛" ? (
              <p className={styles.hint} data-testid="postmatch-notice">
                本场参数包含赛后数据,仅供演示。
              </p>
            ) : null}
            {pairFixtures.length > 0 ? (
              <div className={styles.chips}>
                <Chip active={useMarket} onClick={() => setUseMarket(!useMarket)}>
                  {useMarket ? `已用 Crown 盘口定锚(w=${cal?.market_w ?? 1})` : "不用盘口定锚"}
                </Chip>
              </div>
            ) : null}
          </section>

          <section className={styles.card}>
            <h2 className={styles.cardTitle}>排阵</h2>
            <div className={styles.teams}>
              {([0, 1] as const).map((side) => {
                const setup = side === 0 ? home : away;
                return (
                  <LineupEditor
                    key={`${side}-${setup.teamId}`}
                    params={params}
                    sideLabel={side === 0 ? "主队" : "客队"}
                    setup={setup}
                    lastLineupDate={params.teams[String(setup.teamId)].last_lineup?.date ?? null}
                    selected={selected?.side === side ? selected.i : null}
                    onSelect={(i) => setSelected(i === null ? null : { side, i })}
                    onChange={side === 0 ? setHome : setAway}
                  />
                );
              })}
            </div>
          </section>

          <section className={styles.card}>
            <h2 className={styles.cardTitle}>侧重点(每队最多 2 个)</h2>
            <div className={styles.teams}>
              {([0, 1] as const).map((side) => {
                const setup = side === 0 ? home : away;
                const err = focusError(setup.focuses);
                const ch = err ? null : channelDeltas(params, impactSetup, side);
                const imp = impactNow?.[side] ?? null;
                return (
                  <div key={side} data-testid={`focus-side-${side}`}>
                    <div className={styles.teamName}>{names[side]}</div>
                    <div className={styles.chips}>
                      {FOCUS_LIST.map((f) => {
                        const gain = imp?.singlePp[f];
                        const fit = gain !== undefined && gain >= FIT_THRESHOLD_PP;
                        return (
                          <Chip key={f} active={setup.focuses.includes(f)} onClick={() => toggleFocus(side, f)}>
                            {FOCUS_LABEL[f]}
                            {fit ? (
                              <span className={styles.fitTag} data-testid="focus-fit-tag">
                                适合本场对手
                              </span>
                            ) : null}
                          </Chip>
                        );
                      })}
                    </div>
                    {err ? <p className={styles.hint}>{err}</p> : null}
                    {ch ? (
                      <div className={styles.focusEffect} data-testid="focus-channels">
                        <div>本队预期进球:{ch.own.length ? formatDeltas(ch.own) : "各渠道不变"}</div>
                        {ch.opp.length ? <div>对手预期进球:{formatDeltas(ch.opp)}</div> : null}
                      </div>
                    ) : null}
                    <div className={styles.focusEffect} data-testid="focus-winrate">
                      {!prepared.ok ? null : !imp ? (
                        <span className={styles.muted}>胜率影响计算中…</span>
                      ) : imp.selected != null ? (
                        <>
                          本队胜率 {pct1(imp.base)} → <strong>{pct1(imp.selected)}</strong>(
                          {signedPp((imp.selected - imp.base) * 100)})
                        </>
                      ) : (
                        <>未选侧重点:本队胜率 {pct1(imp.base)}</>
                      )}
                    </div>
                    <div className={styles.chips}>
                      <Chip
                        active={setup.shortRest}
                        onClick={() => (side === 0 ? setHome : setAway)({ ...setup, shortRest: !setup.shortRest })}
                      >
                        休息不足 3 天(假设设定)
                      </Chip>
                    </div>
                  </div>
                );
              })}
            </div>
            <p className={styles.muted} style={{ marginTop: 12 }}>
              胜率影响:同一种子各模拟 {IMPACT_RUNS} 次,与本队不选侧重点对比(对手侧重点保持当前设定);单一侧重点使本队胜率提升 ≥
              {FIT_THRESHOLD_PP} 个百分点时标注「适合本场对手」。侧重点乘数为 v0 设定值,未经数据校准,只通过了合理性测试。
            </p>
          </section>

          <section className={styles.card}>
            <h2 className={styles.cardTitle}>预期进球 λ</h2>
            {prepared.ok ? (
              <table className={styles.lambdaTable}>
                <thead>
                  <tr>
                    <th />
                    <th>{names[0]}</th>
                    <th>{names[1]}</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["数据模型 λ", (i: 0 | 1) => prepared.config.teams[i].breakdown.lambdaModel.toFixed(2)],
                      ["Crown 反推 λ", (i: 0 | 1) => prepared.config.teams[i].breakdown.lambdaMarket?.toFixed(2) ?? "—"],
                      ["基准 λ", (i: 0 | 1) => prepared.config.teams[i].breakdown.lambdaBase.toFixed(2)],
                      ["进攻比 r_att", (i: 0 | 1) => `×${prepared.config.teams[i].breakdown.rAtt.toFixed(3)}`],
                      ["对手 λ 乘 m_def", (i: 0 | 1) => `×${prepared.config.teams[i].breakdown.mDef.toFixed(3)}`],
                      ["对手进球率乘 m_gk", (i: 0 | 1) => `×${prepared.config.teams[i].breakdown.mGk.toFixed(3)}`],
                      ["最终 λ(含乌龙)", (i: 0 | 1) => prepared.config.teams[i].breakdown.lambdaFinal.toFixed(2)],
                      ["期望进球(含对方门将 m_gk)", (i: 0 | 1) => prepared.config.teams[i].breakdown.expectedGoals.toFixed(2)],
                    ] as [string, (i: 0 | 1) => string][]
                  ).map(([label, fn]) => (
                    <tr key={label}>
                      <td>{label}</td>
                      <td>{fn(0)}</td>
                      <td>{fn(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className={styles.error}>{prepared.error}</p>
            )}
            <div className={styles.row} style={{ marginTop: 16 }}>
              <Chip active={chaos} onClick={() => setChaos(!chaos)}>
                {chaos ? "混乱模式(κ=4)" : cal?.kappa ? `标准档(κ=${cal.kappa})` : "标准档(状态系数关闭)"}
              </Chip>
              <label className={styles.field} style={{ flex: "0 1 180px" }}>
                种子
                <input
                  className={styles.input}
                  inputMode="numeric"
                  value={seed}
                  onChange={(e) => setSeed(Number(e.target.value.replace(/\D/g, "")) || 0)}
                />
              </label>
              <button type="button" className={styles.secondaryBtn} onClick={() => setSeed(Math.floor(Math.random() * 2 ** 31))}>
                随机种子
              </button>
              <button
                type="button"
                className={styles.primaryBtn}
                disabled={!prepared.ok || phase === "running"}
                onClick={() => run(seed)}
                data-testid="simulate-btn"
              >
                {phase === "running" ? "模拟中…" : "开始模拟"}
              </button>
            </div>
          </section>
        </>
      ) : null}
    </main>
  );
}
