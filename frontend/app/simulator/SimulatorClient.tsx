"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { Chip } from "@/components/ui/Chip";
import { FOCUS_LABEL, matchingFixture, prepareMatch, type ManyResult, type MatchSetup, type SingleResult, type TeamSetup } from "@/features/simulator/engine";
import { IMPACT_RUNS, impactJobs, summarizeImpact, type ImpactJob, type SideImpact } from "@/features/simulator/focusImpact";
import { lastLineupSetup } from "@/features/simulator/formation";
import { decodeResult, parseSetupQuery, parseTeamsQuery, resultToken, toTeamSetup } from "@/features/simulator/shareLink";
import type { BadgeMode } from "@/features/simulator/slotBadge";
import { ensureShotDetails, makeSnapshot, modelVersionOf, type ResultSnapshot } from "@/features/simulator/snapshot";
import type { SimParams } from "@/features/simulator/types";
import { useSimTeamColors } from "@/features/simulator/useSimTeamColors";
import { emptySlots, lineupIssue, marketStatus, pairWithAway, pairWithHome, type WizardStep } from "@/features/simulator/wizard";
import { Fold } from "./Fold";
import { LineupPitchCard } from "./LineupPitchCard";
import type { FixtureIndexEntry } from "./loadParams";
import { MatchAnimation } from "./MatchAnimation";
import { RecordStage } from "./RecordStage";
import { ResultView } from "./ResultView";
import { TeamFocusCard } from "./TeamFocusCard";
import { WizardSteps } from "./WizardSteps";
import styles from "./simulator.module.css";

// 只开放 v0.3 校准过的五大联赛(docs/simulator-launch-plan.md §2)
const LEAGUE_NAME: Record<string, string> = { "47": "英超", "87": "西甲", "55": "意甲", "54": "德甲", "53": "法甲" };
const LEAGUE_ORDER = ["47", "87", "55", "54", "53"];
const DEFAULT_SEED = 20260929;
const RUNS = 1000;
const IMPACT_DEBOUNCE_MS = 300;

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
// 三步向导(参照 FotMob Lineup Builder):第 1 步主队 → 第 2 步客队(页底开始模拟)→ 动画 → 结果。step 不进 URL(URL 是分享链接的语义)。
export function SimulatorClient({
  params,
  leagueId: leagueNum,
  fixtureIndex,
  paramsStale,
  initialStep,
}: {
  params: SimParams;
  leagueId: number;
  fixtureIndex: FixtureIndexEntry[];
  paramsStale: boolean;
  initialStep: WizardStep;
}) {
  const router = useRouter();
  const leagueId = String(leagueNum);
  const leagueIds = LEAGUE_ORDER.filter((id) => params.leagues[id]);
  const teamList = useMemo(() => teamsOf(params, leagueId), [params, leagueId]);
  const teamIds = useMemo(() => teamList.map((t) => t.team_id), [teamList]);
  const [pair, setPair] = useState<[number, number]>(() => pickDefaultPair(params, leagueId));
  const [home, setHome] = useState<TeamSetup>(() => lastLineupSetup(params, pair[0]));
  const [away, setAway] = useState<TeamSetup>(() => lastLineupSetup(params, pair[1]));
  const [useMarket, setUseMarket] = useState(true);
  const [fixtureChoice, setFixtureChoice] = useState<number | null>(null);
  const [chaos, setChaos] = useState(false);
  const [recordMode, setRecordMode] = useState(false);
  const [seed, setSeed] = useState(DEFAULT_SEED);
  const [step, setStep] = useState<WizardStep>(initialStep);
  const [badge, setBadge] = useState<BadgeMode>("position");
  const [showIssue, setShowIssue] = useState(false);
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

  const goStep = useCallback((s: WizardStep) => {
    setStep(s);
    setShowIssue(false);
    window.scrollTo({ top: 0 });
  }, []);

  // 换一边的球队只重置这一边;撞队时把另一边换成列表里第一支不同的队(另一边已编辑的阵容因此丢失,属预期)
  const setHomeTeam = (h: number) => {
    const next = pairWithHome(pair, h, teamIds);
    setPair(next);
    setHome(lastLineupSetup(params, next[0], { focuses: home.focuses, shortRest: home.shortRest }));
    if (next[1] !== pair[1]) setAway(lastLineupSetup(params, next[1]));
    setFixtureChoice(null);
  };
  const setAwayTeam = (a: number) => {
    const next = pairWithAway(pair, a, teamIds);
    setPair(next);
    setAway(lastLineupSetup(params, next[1], { focuses: away.focuses, shortRest: away.shortRest }));
    if (next[0] !== pair[0]) setHome(lastLineupSetup(params, next[0]));
    setFixtureChoice(null);
  };
  const setBothTeams = (h: number, a: number, fixtureId: number | null = null) => {
    setPair([h, a]);
    setHome(lastLineupSetup(params, h));
    setAway(lastLineupSetup(params, a));
    setFixtureChoice(fixtureId);
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
        window.scrollTo({ top: 0 });
        w.terminate();
        workerRef.current = null;
      };
      w.postMessage({ config, seed: runSeed, runs: RUNS });
    },
    [prepared, params, leagueId, home, away, fixtureId, chaos],
  );

  // 打开分享链接:查询参数恢复设定(进第 2 步);# 片段里有结果时在浏览器端解压并原样展示(不重新计算)。
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
        setBothTeams(teamsOnly.home, teamsOnly.away, teamsOnly.fixtureId);
        setUseMarket(teamsOnly.fixtureId != null);
        setStep(2);
      }
      if (shared && inLeague(shared.home.teamId, shared.away.teamId, shared.leagueId)) {
        setPair([shared.home.teamId, shared.away.teamId]);
        setHome(toTeamSetup(params, shared.home));
        setAway(toTeamSetup(params, shared.away));
        setUseMarket(shared.fixtureId != null);
        setFixtureChoice(shared.fixtureId);
        setChaos(shared.chaos);
        setSeed(shared.seed);
        setStep(2);
      }
      if (snap) {
        setResult({ snap: ensureShotDetails(snap, params), shared: true });
        setPhase("result");
      } else if (token) {
        setLinkError("分享链接中的结果无法解析,只恢复了设定。");
      }
    })();
    return () => {
      cancelled = true;
    };
    // setBothTeams 只依赖 params(与本 effect 相同);只在挂载时解析一次地址栏
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

  const names: [string, string] = [
    params.teams[String(home.teamId)].name_zh ?? "",
    params.teams[String(away.teamId)].name_zh ?? "",
  ];
  const homeIssue = lineupIssue(params, home);
  const awayIssue = lineupIssue(params, away);
  const fmtFocuses = (t: TeamSetup) => (t.focuses.length ? t.focuses.map((f) => FOCUS_LABEL[f]).join("、") : "未选");

  const tryNext = () => {
    if (homeIssue) {
      setShowIssue(true);
      return;
    }
    goStep(2);
  };

  return (
    <main className={styles.page} style={colorVars}>
      <header className={styles.header}>
        <h1 className={styles.title}>比赛模拟器</h1>
        <span className={styles.badge} data-testid="uncalibrated-badge">{params.calibration ? "原型" : "未校准原型"}</span>
        {/* 技术细节(λ、模拟编号、模型版本与校准范围等)按站长要求不在前端展示 */}
        <span className={styles.meta} data-testid="sim-meta">
          目前支持五大联赛,结果不代表预测
        </span>
        {paramsStale ? (
          <span className={styles.hint} data-testid="params-stale">
            参数不是最新的(上次更新 {params.meta.generated_at}),每日更新可能延迟。
          </span>
        ) : null}
      </header>

      {linkError ? <p className={styles.error}>{linkError}</p> : null}

      {phase === "animating" && result ? (
        recordMode ? (
          <RecordStage
            snap={result.snap}
            crests={crestsOf(params, result.snap.teams[0].teamId, result.snap.teams[1].teamId)}
            onExit={finishAnimation}
          />
        ) : (
          <MatchAnimation
            single={result.snap.single}
            names={[result.snap.teams[0].name, result.snap.teams[1].name]}
            crests={crestsOf(params, result.snap.teams[0].teamId, result.snap.teams[1].teamId)}
            realShots={Boolean(params.shot_samples)}
            onDone={finishAnimation}
          />
        )
      ) : null}

      {phase === "result" && result?.shared ? (
        <section className={`${styles.card} ${styles.linkNotice}`} data-testid="shared-notice">
          <p style={{ margin: 0 }}>这是朋友分享的一次模拟结果。想自己试试?点下面的「改阵容」或「再模拟一次」。</p>
          {linkStale ? (
            <div className={styles.row} style={{ marginTop: 8 }} data-testid="params-updated">
              <span className={styles.hint} style={{ marginTop: 0 }}>
                球队数据已更新,重新模拟结果可能不同。
              </span>
              <button type="button" className={styles.primaryBtn} disabled={!prepared.ok} onClick={() => run(result.snap.seed)}>
                用最新数据重新模拟
              </button>
              {!prepared.ok ? <span className={styles.error}>{prepared.error}</span> : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {phase === "result" && result ? (
        <ResultView
          snap={result.snap}
          crests={crestsOf(params, result.snap.teams[0].teamId, result.snap.teams[1].teamId)}
          onRerun={() => {
            const s = Math.floor(Math.random() * 2 ** 31);
            setSeed(s);
            run(s);
          }}
          onBack={() => {
            setPhase("setup");
            goStep(2);
          }}
        />
      ) : null}

      {phase === "setup" || phase === "running" ? (
        <>
          <WizardSteps step={step} canGoStep2={!homeIssue} onGo={goStep} />

          {step === 1 ? (
            <>
              <div className={styles.row}>
                <label className={styles.field} style={{ flex: "0 1 220px" }}>
                  联赛
                  <select className={styles.select} value={leagueId} onChange={(e) => router.push(`/simulator?lg=${e.target.value}`)} data-testid="league-select">
                    {leagueIds.map((id) => (
                      <option key={id} value={id}>
                        {LEAGUE_NAME[id] ?? id}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <Fold title="快捷选择:近期真实比赛" hint="手动选两队也会自动匹配盘口" testId="fixture-fold">
                <div className={styles.fixtureList}>
                  {fixtures.map((f) => (
                    <button
                      key={f.match_id}
                      type="button"
                      className={styles.fixtureBtn}
                      onClick={() => {
                        if (f.league_id !== leagueNum) {
                          router.push(`/simulator?lg=${f.league_id}&h=${f.home_team_id}&a=${f.away_team_id}&fx=${f.match_id}`);
                          return;
                        }
                        setUseMarket(true);
                        setBothTeams(f.home_team_id, f.away_team_id, f.match_id);
                        goStep(2);
                      }}
                    >
                      [{f.status}] {LEAGUE_NAME[String(f.league_id)]} {f.home_name} vs {f.away_name} · {f.kickoff_at_utc.slice(0, 10)} · 让 {f.ah_line ?? "—"} · 大小 {f.ou_line ?? "—"}
                      {f.final_score ? ` · 实际 ${f.final_score.join(":")}` : ""}
                    </button>
                  ))}
                </div>
              </Fold>

              <div className={styles.wizardGrid}>
                <div>
                  <LineupPitchCard
                    key={`home-${home.teamId}`}
                    params={params}
                    sideLabel="主队"
                    setup={home}
                    teams={teamList}
                    disabledTeamId={away.teamId}
                    badge={badge}
                    onBadge={setBadge}
                    flaggedSlots={showIssue ? emptySlots(home) : []}
                    onChange={setHome}
                    onTeamChange={setHomeTeam}
                  />
                </div>
                <div>
                  <TeamFocusCard
                    params={params}
                    side={0}
                    setup={home}
                    opponentName={names[1]}
                    impactSetup={impactSetup}
                    impact={impactNow?.[0] ?? null}
                    preparedOk={prepared.ok}
                    onChange={setHome}
                  />
                  <div className={styles.row}>
                    <button type="button" className={styles.primaryBtn} onClick={tryNext} data-testid="next-step">
                      下一步:选择客队 →
                    </button>
                  </div>
                  {showIssue && homeIssue ? (
                    <p className={styles.error} data-testid="lineup-issue">
                      {homeIssue}
                    </p>
                  ) : null}
                </div>
              </div>
            </>
          ) : null}

          {step === 2 ? (
            <>
              <section className={`${styles.card} ${styles.summaryStrip}`} data-testid="home-summary">
                <span className={styles.summaryMain}>
                  <TeamBadge teamName={names[0]} crestUrl={params.teams[String(home.teamId)]?.crest_url} size={28} eager />
                  <span>
                    <strong style={{ color: "var(--sim-home)" }}>{names[0]}</strong> · {home.formation} · {home.focuses.length ? `侧重点 ${fmtFocuses(home)}` : "未选侧重点"}
                  </span>
                </span>
                <button type="button" className={styles.linkBtn} onClick={() => goStep(1)} data-testid="edit-home">
                  修改
                </button>
              </section>

              <div className={styles.wizardGrid}>
                <div>
                  <LineupPitchCard
                    key={`away-${away.teamId}`}
                    params={params}
                    sideLabel="客队"
                    setup={away}
                    teams={teamList}
                    disabledTeamId={home.teamId}
                    badge={badge}
                    onBadge={setBadge}
                    flaggedSlots={showIssue ? emptySlots(away) : []}
                    onChange={setAway}
                    onTeamChange={setAwayTeam}
                  />
                </div>
                <div>
                  <TeamFocusCard
                    params={params}
                    side={1}
                    setup={away}
                    opponentName={names[0]}
                    impactSetup={impactSetup}
                    impact={impactNow?.[1] ?? null}
                    preparedOk={prepared.ok}
                    onChange={setAway}
                  />

                  <section className={styles.card}>
                    <h2 className={styles.cardTitle}>预计进球</h2>
                    {prepared.ok ? (
                      <p className={styles.bigLine} data-testid="setup-expected">
                        {names[0]} {prepared.config.teams[0].breakdown.expectedGoals.toFixed(1)} :{" "}
                        {prepared.config.teams[1].breakdown.expectedGoals.toFixed(1)} {names[1]}
                      </p>
                    ) : (
                      <p className={styles.error}>{awayIssue ?? homeIssue ?? prepared.error}</p>
                    )}
                    <p className={styles.muted} style={{ fontSize: 13, margin: "4px 0 0" }} data-testid="market-status">
                      {marketStatus(params, fixtureId, pairFixtures.length > 0)}
                    </p>
                    {fixtureId != null && params.fixtures[String(fixtureId)]?.status !== "未开赛" ? (
                      <p className={styles.hint} data-testid="postmatch-notice">
                        本场参数包含赛后数据,仅供演示。
                      </p>
                    ) : null}
                    <div className={styles.chips}>
                      {pairFixtures.length > 0 ? (
                        <Chip active={useMarket} onClick={() => setUseMarket(!useMarket)}>
                          {useMarket ? "已参考市场数据" : "不参考市场数据"}
                        </Chip>
                      ) : null}
                      <Chip active={chaos} onClick={() => setChaos(!chaos)}>
                        {chaos ? "随机强度:混乱模式" : "随机强度:标准"}
                      </Chip>
                      <Chip active={recordMode} onClick={() => setRecordMode(!recordMode)} testId="record-mode">
                        {recordMode ? "录屏模式:开" : "录屏模式:关"}
                      </Chip>
                    </div>
                    <div className={styles.row} style={{ marginTop: 16 }}>
                      <button type="button" className={styles.secondaryBtn} onClick={() => goStep(1)}>
                        ← 上一步
                      </button>
                      <button
                        type="button"
                        className={styles.primaryBtn}
                        disabled={!prepared.ok || phase === "running"}
                        onClick={() => {
                          if (awayIssue) {
                            setShowIssue(true);
                            return;
                          }
                          // 录屏模式:在点击这一刻请求全屏(浏览器只允许在用户操作里请求);不支持时照样铺满视口
                          if (recordMode) document.documentElement.requestFullscreen?.().catch(() => {});
                          run(seed);
                        }}
                        data-testid="simulate-btn"
                      >
                        {phase === "running" ? "模拟中…" : "开始模拟"}
                      </button>
                    </div>
                  </section>

                  <p className={styles.muted} data-testid="position-note">
                    球员位置由系统推断,可能不准。
                  </p>
                </div>
              </div>
            </>
          ) : null}
        </>
      ) : null}
    </main>
  );
}

// 队徽取自参数文件(每日导出时解析的同源地址);分享链接打开的比赛不在本联赛参数里时显示队名首字
function crestsOf(params: SimParams, home: number, away: number): [string | null, string | null] {
  return [params.teams[String(home)]?.crest_url ?? null, params.teams[String(away)]?.crest_url ?? null];
}
