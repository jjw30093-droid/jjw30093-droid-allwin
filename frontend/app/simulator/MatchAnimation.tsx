"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { FootballPitchBackground } from "@/components/matches/FootballPitchBackground";
import type { SimEvent, SingleResult } from "@/features/simulator/engine";
import { mulberry32 } from "@/features/simulator/rng";
import { cumulativeXg } from "@/features/simulator/xg";
import styles from "./simulator.module.css";

const DURATION_MS = 15000;
const GOAL_POP_MS = 1600;
const MOMENTUM_WINDOW = 8;

export const CHANNEL_LABEL: Record<string, string> = {
  open: "运动战",
  counter: "反击",
  setpiece: "定位球",
  penalty: "点球",
  owngoal: "乌龙球",
  gk_error: "门将失误",
};

function eventLabel(e: SimEvent): string {
  if (e.kind === "red") return "红牌";
  if (e.kind === "injury") return "伤病换人";
  if (e.channel === "owngoal") return "乌龙球";
  if (e.channel === "penalty") return e.kind === "goal" ? "点球命中" : "点球未进";
  if (e.channel === "gk_error") return e.kind === "goal" ? "门将失误·进球" : "门将失误·射门";
  const ch = CHANNEL_LABEL[e.channel ?? "open"];
  return e.kind === "goal" ? `进球·${ch}` : `射门·${ch} xG ${(e.xg ?? 0).toFixed(2)}`;
}

// 射门点只是示意位置(数据里没有射门坐标),按事件序号取确定性伪随机,同一种子画面一致。
function shotXY(e: SimEvent, i: number, seed: number): { left: number; top: number } {
  const r = mulberry32((seed + i * 7919) >>> 0);
  let x: number;
  let y: number;
  if (e.channel === "penalty") {
    x = 94;
    y = 34;
  } else if (e.channel === "owngoal") {
    x = 101;
    y = 30 + r() * 8;
  } else {
    const close = Math.min(e.xg ?? 0.1, 0.6) / 0.6;
    x = 105 - (4 + (1 - close) * 19 + r() * 3);
    y = 34 + (r() - 0.5) * (40 - close * 22);
  }
  if (e.team === 1) x = 105 - x;
  return { left: (x / 105) * 100, top: (y / 68) * 100 };
}

export function MatchAnimation({
  single,
  names,
  onDone,
}: {
  single: SingleResult;
  names: [string, string];
  onDone: () => void;
}) {
  const [tick, setTick] = useState(0);
  const startRef = useRef<number | null>(null);
  const doneRef = useRef(false);

  // 按墙钟推进(不用 requestAnimationFrame:页面在后台时 rAF 会暂停,动画永远播不完)。
  useEffect(() => {
    startRef.current = performance.now();
    const id = setInterval(() => {
      const t = Math.min(1, (performance.now() - (startRef.current ?? 0)) / DURATION_MS);
      setTick(Math.floor(t * single.totalTicks));
      if (t >= 1 && !doneRef.current) {
        doneRef.current = true;
        clearInterval(id);
        setTimeout(onDone, 800);
      }
    }, 50);
    return () => clearInterval(id);
  }, [single, onDone]);

  const shown = useMemo(() => single.events.filter((e) => e.tick <= tick), [single, tick]);
  const score = useMemo(() => {
    const s: [number, number] = [0, 0];
    for (const e of shown) if (e.kind === "goal") s[e.team] += 1;
    return s;
  }, [shown]);

  const clock = useMemo(() => {
    const last = [...shown].reverse().find((e) => e.tick === tick);
    if (last) return last.clock;
    const half = tick >= single.halfTimeTick ? 2 : 1;
    const m = half === 1 ? tick + 1 : 46 + (tick - single.halfTimeTick);
    return half === 1 ? `${Math.min(m, 45)}'` : `${Math.min(m, 90)}'`;
  }, [shown, tick, single.halfTimeTick]);

  const momentum = useMemo(() => {
    let h = 0;
    let a = 0;
    for (const e of shown) {
      if (e.tick < tick - MOMENTUM_WINDOW || e.xg === undefined) continue;
      if (e.team === 0) h += e.xg;
      else a += e.xg;
    }
    return h + a > 0 ? h / (h + a) : 0.5;
  }, [shown, tick]);

  // 弹窗列出窗口内的全部进球(最新在上):两球相隔很近时后一个不会把前一个顶掉。
  const popTicks = Math.ceil((GOAL_POP_MS / DURATION_MS) * single.totalTicks);
  const popGoals = shown.filter((e) => e.kind === "goal" && tick - e.tick <= popTicks).reverse();
  const lastGoal = popGoals[0];
  const xg = useMemo(() => cumulativeXg(single.events, tick), [single, tick]);
  const ticker = [...shown].reverse();

  return (
    <section className={styles.card} data-testid="sim-animation">
      <div className={styles.scoreboard}>
        <span className={styles.sbTeam}>{names[0]}</span>
        <div>
          <div className={styles.sbScore}>
            {score[0]} : {score[1]}
          </div>
          <div className={styles.clock}>{tick >= single.totalTicks ? "全场结束" : clock}</div>
          <div className={styles.liveXg} data-testid="live-xg">
            xG {xg[0].toFixed(2)} : {xg[1].toFixed(2)}
          </div>
        </div>
        <span className={styles.sbTeam}>{names[1]}</span>
      </div>
      <div className={styles.momentum} aria-label={`势头 主队 ${Math.round(momentum * 100)}%`}>
        <span className={styles.momHome} style={{ width: `${momentum * 100}%` }} />
        <span className={styles.momAway} style={{ width: `${(1 - momentum) * 100}%` }} />
      </div>
      <div className={styles.animPitch}>
        <FootballPitchBackground orientation="landscape" variant="neutral" />
        {shown
          .map((e, i) => ({ e, i }))
          .filter(({ e }) => e.kind === "shot" || e.kind === "goal")
          .map(({ e, i }) => {
            const { left, top } = shotXY(e, i, single.seed);
            return (
              <span
                key={i}
                className={`${styles.shot} ${e.team === 0 ? styles.shotHome : styles.shotAway} ${e.kind === "goal" ? styles.shotGoal : ""}`}
                style={{ left: `${left}%`, top: `${top}%` }}
              />
            );
          })}
        <span className={styles.pitchNote}>射门位置为示意</span>
        {lastGoal ? (
          <div className={styles.goalPop} role="status">
            <div className={styles.goalPopTitle}>{lastGoal.channel === "owngoal" ? "乌龙球!" : "进球!"}</div>
            {popGoals.map((g, i) => (
              <div key={i}>
                {names[g.team]} · {g.clock} · {g.playerName ?? ""}
                {g.channel === "owngoal" ? "(乌龙)" : ` · ${CHANNEL_LABEL[g.channel ?? "open"]}`}
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <p className={styles.muted}>青绿 = {names[0]},蓝 = {names[1]},金色 = 进球。</p>
      <ol className={styles.ticker} data-testid="event-ticker" aria-label="比赛事件">
        {ticker.length === 0 ? <li className={styles.muted}>比赛开始</li> : null}
        {ticker.map((e, i) => {
          const key = e.kind === "goal" || e.kind === "red" || e.channel === "penalty" || e.channel === "owngoal";
          return (
            <li key={`${e.tick}-${i}`} className={`${styles.tickRow} ${key ? styles.tickKey : ""}`}>
              <span className={styles.tickClock}>{e.clock}</span>
              <span className={styles.tickTeam}>{names[e.team]}</span>
              <span className={styles.tickType}>{eventLabel(e)}</span>
              <span className={styles.tickPlayer}>{e.playerName ?? ""}</span>
            </li>
          );
        })}
      </ol>
      <div className={styles.row} style={{ justifyContent: "flex-end" }}>
        <button type="button" className={styles.secondaryBtn} onClick={onDone}>
          跳过动画
        </button>
      </div>
    </section>
  );
}
