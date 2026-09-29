"use client";

import { useCallback, useMemo, useRef, useState, type CSSProperties } from "react";
import { Chip } from "@/components/ui/Chip";
import {
  FOCUS_LABEL,
  FOCUS_LIST,
  focusError,
  matchingFixture,
  prepareMatch,
  slotGroup,
  type Focus,
  type ManyResult,
  type MatchConfig,
  type MatchSetup,
  type SingleResult,
  type TeamSetup,
} from "@/features/simulator/engine";
import type { PosGroup, SimParams } from "@/features/simulator/types";
import { useSimTeamColors } from "@/features/simulator/useSimTeamColors";
import { LineupEditor } from "./LineupEditor";
import { MatchAnimation } from "./MatchAnimation";
import { ResultView } from "./ResultView";
import styles from "./simulator.module.css";

const LEAGUE_NAME: Record<string, string> = { "47": "英超", "87": "西甲" };
const DEFAULT_SEED = 20260929;
const RUNS = 1000;
const FALLBACK_FORMATION = "4-2-3-1";

type Phase = "setup" | "running" | "animating" | "result";

function gridXY(pid: number): { x: number; y: number } {
  const col = pid % 10;
  const row = Math.floor(pid / 10);
  return { x: Math.min(0.92, Math.max(0.08, (col - 1) / 8)), y: Math.min(0.9, row / 12) };
}

// 槽位分组只查 position_map(单一映射表);模板只提供坐标,查不到时按格子行列摆放。
function initialSetup(params: SimParams, teamId: number): TeamSetup {
  const team = params.teams[String(teamId)];
  const ll = team.last_lineup;
  const formation = ll?.formation ?? FALLBACK_FORMATION;
  const tpl = params.formations[formation];
  const slotOf = (positionId: number, playerId: string | null) => {
    const t = tpl?.slots.find((x) => x.position_id === positionId);
    const g = gridXY(positionId);
    return {
      positionId,
      group: slotGroup(params, formation, positionId) as PosGroup,
      playerId,
      x: t?.x ?? g.x,
      y: t?.y ?? g.y,
    };
  };
  return {
    teamId,
    formation,
    focuses: [],
    shortRest: false,
    slots: ll
      ? ll.starters.map((s) => slotOf(s.position_id, s.player_id))
      : (tpl?.slots ?? []).map((s) => slotOf(s.position_id, null)),
  };
}

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

export function SimulatorClient({ params }: { params: SimParams }) {
  const leagueIds = Object.keys(params.leagues);
  const [leagueId, setLeagueId] = useState(leagueIds.includes("47") ? "47" : leagueIds[0]);
  const [pair, setPair] = useState<[number, number]>(() => pickDefaultPair(params, leagueId));
  const [home, setHome] = useState<TeamSetup>(() => initialSetup(params, pair[0]));
  const [away, setAway] = useState<TeamSetup>(() => initialSetup(params, pair[1]));
  const [useMarket, setUseMarket] = useState(true);
  const [fixtureChoice, setFixtureChoice] = useState<number | null>(null);
  const [chaos, setChaos] = useState(false);
  const [seed, setSeed] = useState(DEFAULT_SEED);
  const [selected, setSelected] = useState<{ side: 0 | 1; i: number } | null>(null);
  const [phase, setPhase] = useState<Phase>("setup");
  const [result, setResult] = useState<{
    single: SingleResult;
    many: ManyResult;
    config: MatchConfig;
    setup: MatchSetup;
  } | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const teamColors = useSimTeamColors();
  const colorVars = {
    "--sim-home": teamColors.surface[0],
    "--sim-away": teamColors.surface[1],
    "--sim-home-pitch": teamColors.pitch[0],
    "--sim-away-pitch": teamColors.pitch[1],
  } as CSSProperties;

  const setTeams = (lid: string, h: number, a: number, fixtureId: number | null = null) => {
    setLeagueId(lid);
    setPair([h, a]);
    setHome(initialSetup(params, h));
    setAway(initialSetup(params, a));
    setFixtureChoice(fixtureId);
    setSelected(null);
  };

  const fixtures = useMemo(
    () =>
      Object.values(params.fixtures)
        .filter((f) => f.market_lambda)
        .sort((a, b) => (a.status === b.status ? b.kickoff_at_utc.localeCompare(a.kickoff_at_utc) : a.status === "未开赛" ? -1 : 1)),
    [params],
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
        setResult({ ...e.data, config, setup: setupSnapshot });
        setPhase("animating");
        w.terminate();
        workerRef.current = null;
      };
      w.postMessage({ config, seed: runSeed, runs: RUNS });
    },
    [prepared, leagueId, home, away, fixtureId, chaos],
  );

  const finishAnimation = useCallback(() => setPhase("result"), []);

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
        <span className={styles.badge} data-testid="uncalibrated-badge">未校准原型</span>
        <span className={styles.meta}>
          模型 {params.meta.model_version} · 参数导出于 {params.meta.generated_at} · 全部参数为初始假设,尚未回测校准,结果不代表预测
        </span>
      </header>

      {phase === "animating" && result ? (
        <MatchAnimation single={result.single} names={[result.config.teams[0].name, result.config.teams[1].name]} onDone={finishAnimation} />
      ) : null}

      {phase === "result" && result ? (
        <ResultView
          params={params}
          single={result.single}
          many={result.many}
          config={result.config}
          setup={result.setup}
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
                  onChange={(e) => {
                    const [h, a] = pickDefaultPair(params, e.target.value);
                    setTeams(e.target.value, h, a);
                  }}
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
                <select className={styles.select} value={pair[0]} onChange={(e) => setTeams(leagueId, Number(e.target.value), pair[1])}>
                  {teamList.map((t) => (
                    <option key={t.team_id} value={t.team_id} disabled={t.team_id === pair[1]}>
                      {t.name_zh}
                    </option>
                  ))}
                </select>
              </label>
              <label className={styles.field}>
                客队
                <select className={styles.select} value={pair[1]} onChange={(e) => setTeams(leagueId, pair[0], Number(e.target.value))}>
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
                const h = params.teams[String(f.home_team_id)]?.name_zh ?? f.home_team_id;
                const a = params.teams[String(f.away_team_id)]?.name_zh ?? f.away_team_id;
                const active = fixtureId === f.match_id;
                return (
                  <button
                    key={f.match_id}
                    type="button"
                    className={`${styles.fixtureBtn} ${active ? styles.fixtureBtnActive : ""}`}
                    onClick={() => {
                      setUseMarket(true);
                      setTeams(String(f.league_id), f.home_team_id, f.away_team_id, f.match_id);
                    }}
                  >
                    [{f.status}] {LEAGUE_NAME[String(f.league_id)]} {h} vs {a} · {f.kickoff_at_utc.slice(0, 10)} · 让 {f.ah?.line ?? "—"} · 大小 {f.ou?.line ?? "—"}
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
                  {useMarket ? "已用 Crown 盘口定锚(w=0.7)" : "不用盘口定锚"}
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
                return (
                  <div key={side}>
                    <div className={styles.teamName}>{names[side]}</div>
                    <div className={styles.chips}>
                      {FOCUS_LIST.map((f) => (
                        <Chip key={f} active={setup.focuses.includes(f)} onClick={() => toggleFocus(side, f)}>
                          {FOCUS_LABEL[f]}
                        </Chip>
                      ))}
                    </div>
                    {err ? <p className={styles.hint}>{err}</p> : null}
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
                {chaos ? "混乱模式(κ=4)" : "标准档(κ=12)"}
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
