"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { FootballPitchBackground } from "@/components/matches/FootballPitchBackground";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import { commentLine, fullTimeLine, halfTimeLine, kickoffLine, liveStats } from "@/features/simulator/commentary";
import type { SimEvent, SingleResult } from "@/features/simulator/engine";
import { buildPlan, GOAL_HOLD_MS, NET_MARGIN, resolvedOrder, stageSize, tickAt, type StageOrientation } from "@/features/simulator/playback";
import { shotEnd } from "@/features/simulator/shotDetail";
import { drawFrame, readColors, type ShotAnim } from "./pitchCanvas";
import styles from "./simulator.module.css";

/** 计时器间隔:约 30 帧/秒。用 setInterval + 真实时钟(不依赖 requestAnimationFrame:隐藏标签页时 rAF 会停,动画会卡住) */
const FRAME_MS = 33;

export const CHANNEL_LABEL: Record<string, string> = {
  open: "运动战",
  counter: "反击",
  setpiece: "定位球",
  penalty: "点球",
  owngoal: "乌龙球",
  gk_error: "门将失误",
};

type Speed = 1 | 2;

interface View {
  tick: number;
  /** 已落定的事件数(按落定时间排序的前缀) */
  resolved: number;
  halfTime: boolean;
  fullTime: boolean;
  /** 正在展示射手卡片的进球(事件序号) */
  goalCard: number | null;
  flying: number;
}

export function MatchAnimation({
  single,
  names,
  crests = [null, null],
  onDone,
  mode = "inline",
}: {
  single: SingleResult;
  names: [string, string];
  /** 同源队徽地址;没有时显示队名首字 */
  crests?: [string | null, string | null];
  onDone: () => void;
  /** inline = 页面内(横版球场、速度与跳过按钮);record = 录屏模式(竖版球场、无按钮、文字直播只留最近几条) */
  mode?: "inline" | "record";
}) {
  const orientation: StageOrientation = mode === "record" ? "portrait" : "landscape";
  const plan = useMemo(() => buildPlan(single), [single]);
  const order = useMemo(() => resolvedOrder(plan), [plan]);
  const shots = useMemo<ShotAnim[]>(
    () =>
      single.events.flatMap((e, i) =>
        (e.kind === "shot" || e.kind === "goal") && e.sd
          ? [{ index: i, team: e.team, sd: e.sd, end: shotEnd(e.sd, single.seed, i), start: plan.eventStart[i] }]
          : [],
      ),
    [single, plan],
  );

  const [view, setView] = useState<View>({ tick: 0, resolved: 0, halfTime: false, fullTime: false, goalCard: null, flying: 0 });
  const [speed, setSpeed] = useState<Speed>(1);
  const speedRef = useRef<Speed>(1);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const doneRef = useRef(false);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  const changeSpeed = (s: Speed) => {
    speedRef.current = s;
    setSpeed(s);
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !stage || !ctx) return;
    let colors = readColors(stage);
    let pxPerM = 1;
    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const rect = stage.getBoundingClientRect();
      const { w } = stageSize(orientation);
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      pxPerM = (rect.width * dpr) / w;
      colors = readColors(stage);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(stage);

    let playMs = 0;
    let last = performance.now();
    let prev: View = { tick: -1, resolved: -1, halfTime: false, fullTime: false, goalCard: null, flying: -1 };
    const frame = () => {
      const now = performance.now();
      playMs += (now - last) * speedRef.current;
      last = now;
      const ms = Math.min(playMs, plan.total);
      const { flying } = drawFrame(ctx, orientation, pxPerM, ms, shots, colors);
      let resolved = prev.resolved < 0 ? 0 : prev.resolved;
      while (resolved < order.length && plan.eventResolved[order[resolved]] <= ms) resolved += 1;
      let goalCard: number | null = null;
      for (let k = resolved - 1; k >= 0; k--) {
        const i = order[k];
        if (single.events[i].kind !== "goal") continue;
        if (ms - plan.eventResolved[i] < GOAL_HOLD_MS) goalCard = i;
        break;
      }
      const next: View = {
        tick: tickAt(plan, ms),
        resolved,
        halfTime: plan.halfTimeAt !== null && ms >= plan.halfTimeAt,
        fullTime: ms >= plan.fullTimeAt,
        goalCard,
        flying: Math.min(flying, 1),
      };
      if (
        next.tick !== prev.tick ||
        next.resolved !== prev.resolved ||
        next.halfTime !== prev.halfTime ||
        next.fullTime !== prev.fullTime ||
        next.goalCard !== prev.goalCard ||
        next.flying !== prev.flying
      ) {
        prev = next;
        setView(next);
      }
      if (playMs >= plan.total && !doneRef.current) {
        doneRef.current = true;
        onDoneRef.current();
      }
    };
    frame();
    const id = window.setInterval(frame, FRAME_MS);
    return () => {
      window.clearInterval(id);
      ro.disconnect();
    };
  }, [plan, order, shots, orientation, single]);

  const resolvedEvents = useMemo(() => order.slice(0, view.resolved).map((i) => single.events[i]), [order, view.resolved, single]);
  const score = useMemo(() => {
    const s: [number, number] = [0, 0];
    for (const e of resolvedEvents) if (e.kind === "goal") s[e.team] += 1;
    return s;
  }, [resolvedEvents]);
  const stats = useMemo(() => liveStats(resolvedEvents, Infinity), [resolvedEvents]);

  const clock = view.fullTime
    ? "全场结束"
    : view.halfTime && view.tick < single.halfTimeTick
      ? "半场"
      : clockOf(view.tick, single.halfTimeTick, single.events);

  // 文字直播:最新在上;开场 / 半场 / 终场各一句
  const lines = useMemo(() => {
    const out: { key: string; clock: string; text: string; team: 0 | 1 | null; key2: boolean }[] = [];
    out.push({ key: "ko", clock: "0'", text: kickoffLine(single.seed, names), team: null, key2: false });
    const ht = single.halfTimeTick;
    let htDone = false;
    const htScore: [number, number] = [0, 0];
    for (let k = 0; k < view.resolved; k++) {
      const i = order[k];
      const e = single.events[i];
      if (!htDone && view.halfTime && e.tick >= ht) {
        out.push({ key: "ht", clock: "半场", text: halfTimeLine(single.seed, names, htScore), team: null, key2: true });
        htDone = true;
      }
      if (e.kind === "goal" && e.tick < ht) htScore[e.team] += 1;
      const text = commentLine(single.events, i, single.seed, names);
      if (text) out.push({ key: `e${i}`, clock: e.clock, text, team: e.team, key2: e.kind === "goal" || e.kind === "red" });
    }
    if (!htDone && view.halfTime) out.push({ key: "ht", clock: "半场", text: halfTimeLine(single.seed, names, htScore), team: null, key2: true });
    if (view.fullTime) out.push({ key: "ft", clock: "终场", text: fullTimeLine(single.seed, names, score), team: null, key2: true });
    return out.reverse();
  }, [single, names, order, view.resolved, view.halfTime, view.fullTime, score]);

  const card = view.goalCard !== null ? single.events[view.goalCard] : null;
  const { w, h } = stageSize(orientation);
  const inset = `${(NET_MARGIN / (orientation === "landscape" ? w : h)) * 100}%`;
  const record = mode === "record";

  return (
    <section
      className={`${record ? styles.recAnim : styles.card} ${card ? styles.animFlashing : ""}`}
      data-testid="sim-animation"
      data-flying={view.flying}
      data-goal-card={card ? "1" : "0"}
      data-full-time={view.fullTime ? "1" : "0"}
    >
      {card ? <div key={view.goalCard} className={styles.goalFlash} aria-hidden="true" /> : null}
      <div className={styles.scoreboard}>
        <span className={`${styles.sbTeam} ${styles.sbTeamCol}`} style={{ color: "var(--sim-home)" }}>
          <TeamBadge teamName={names[0]} crestUrl={crests[0]} size={40} eager />
          {names[0]}
        </span>
        <div>
          <div className={styles.sbScore}>
            <span key={`s${score[0]}-${score[1]}`} className={styles.scoreBump} data-testid="anim-score">
              {score[0]} : {score[1]}
            </span>
          </div>
          <div className={styles.clock}>{clock}</div>
          {record ? <div className={styles.recTag}>模拟</div> : null}
        </div>
        <span className={`${styles.sbTeam} ${styles.sbTeamCol}`} style={{ color: "var(--sim-away)" }}>
          <TeamBadge teamName={names[1]} crestUrl={crests[1]} size={40} eager />
          {names[1]}
        </span>
      </div>

      <dl className={styles.liveStats} data-testid="live-stats">
        {(
          [
            ["射门", stats.shots[0], stats.shots[1]],
            ["射正", stats.onTarget[0], stats.onTarget[1]],
            ["xG", stats.xg[0].toFixed(2), stats.xg[1].toFixed(2)],
          ] as [string, number | string, number | string][]
        ).map(([label, a, b]) => (
          <div key={label} className={styles.liveStatRow} data-testid={label === "xG" ? "live-xg" : undefined}>
            <dd>{a}</dd>
            <dt>{label}</dt>
            <dd>{b}</dd>
          </div>
        ))}
      </dl>

      <div
        ref={stageRef}
        className={`${styles.animStage} ${record ? styles.animStagePortrait : ""}`}
        style={{ aspectRatio: `${w} / ${h}` }}
      >
        <div
          className={styles.animPitchInner}
          style={orientation === "landscape" ? { left: inset, right: inset } : { top: inset, bottom: inset }}
        >
          <FootballPitchBackground orientation={orientation === "landscape" ? "landscape" : "portrait-full"} variant="neutral" />
        </div>
        <canvas ref={canvasRef} className={styles.animCanvas} aria-hidden="true" />
        {card ? (
          <div className={styles.scorerCard} role="status" data-testid="scorer-card">
            {card.playerId ? <PlayerAvatar playerId={card.playerId} playerName={card.playerName ?? ""} size={40} eager /> : null}
            <div>
              <div className={styles.scorerCardTitle}>{card.channel === "owngoal" ? "乌龙球!" : "进球!"}</div>
              <div className={styles.scorerCardName}>
                {card.playerName ?? names[card.team]} <span className={styles.muted}>{card.clock}</span>
              </div>
              <div className={styles.muted}>
                {card.channel === "owngoal" ? `${names[card.team]}受益` : `${names[card.team]} · ${CHANNEL_LABEL[card.channel ?? "open"]}`}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {!record ? (
        <p className={styles.muted}>
          <span style={{ color: "var(--sim-home)" }}>● {names[0]}</span> <span style={{ color: "var(--sim-away)" }}>● {names[1]}</span>
          ;带金边的大圆点 = 进球。射门位置与结果取自五大联赛真实射门。
        </p>
      ) : null}

      <ol className={`${styles.commentary} ${record ? styles.commentaryRecord : ""}`} data-testid="event-ticker" aria-label="文字直播" aria-live="polite">
        {(record ? lines.slice(0, 3) : lines).map((l) => (
          <li
            key={l.key}
            className={`${styles.commentRow} ${l.key2 ? styles.commentKey : ""} ${l.team === 0 ? styles.tickHome : l.team === 1 ? styles.tickAway : ""}`}
          >
            <span className={styles.tickClock}>{l.clock}</span>
            <span className={styles.commentText}>{l.text}</span>
          </li>
        ))}
      </ol>

      {!record ? (
        <div className={styles.animControls}>
          <div className={styles.speedGroup} role="group" aria-label="播放速度">
            {([1, 2] as Speed[]).map((s) => (
              <button
                key={s}
                type="button"
                className={`${styles.speedBtn} ${speed === s ? styles.speedBtnOn : ""}`}
                aria-pressed={speed === s}
                onClick={() => changeSpeed(s)}
                data-testid={`speed-${s}`}
              >
                {s} 倍
              </button>
            ))}
          </div>
          <button type="button" className={styles.secondaryBtn} onClick={onDone}>
            跳过动画
          </button>
        </div>
      ) : null}
    </section>
  );
}

function clockOf(tick: number, halfTimeTick: number, events: SimEvent[]): string {
  const hit = events.find((e) => e.tick === tick);
  if (hit) return hit.clock;
  const half = tick >= halfTimeTick ? 2 : 1;
  const m = half === 1 ? tick + 1 : 46 + (tick - halfTimeTick);
  return half === 1 ? `${Math.min(m, 45)}'` : `${Math.min(m, 90)}'`;
}
