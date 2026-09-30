"use client";

import { useEffect, useState } from "react";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import type { ResultSnapshot } from "@/features/simulator/snapshot";
import { verdictOf } from "@/features/simulator/verdict";
import { MatchAnimation } from "./MatchAnimation";
import styles from "./simulator.module.css";

/**
 * 录屏模式:铺满屏幕的竖屏画面(9:16),盖住导航栏,播放时不显示任何按钮,
 * 只有比分、实时统计、竖版球场与文字直播;结束后显示结果卡,这时才出现"退出录屏"。Esc 随时退出。
 */
export function RecordStage({
  snap,
  crests,
  onExit,
}: {
  snap: ResultSnapshot;
  crests: [string | null, string | null];
  onExit: () => void;
}) {
  const [done, setDone] = useState(false);
  const names: [string, string] = [snap.teams[0].name, snap.teams[1].name];

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onExit();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    };
  }, [onExit]);

  return (
    <div className={styles.recOverlay} data-testid="record-stage" role="dialog" aria-label="录屏模式">
      <div className={styles.recFrame}>
        {!done ? (
          <MatchAnimation mode="record" single={snap.single} names={names} crests={crests} onDone={() => setDone(true)} />
        ) : (
          <RecordResultCard snap={snap} crests={crests} onExit={onExit} />
        )}
      </div>
    </div>
  );
}

function RecordResultCard({ snap, crests, onExit }: { snap: ResultSnapshot; crests: [string | null, string | null]; onExit: () => void }) {
  const names: [string, string] = [snap.teams[0].name, snap.teams[1].name];
  const goals = snap.single.events.filter((e) => e.kind === "goal");
  const verdict = verdictOf(snap);
  return (
    <div className={styles.recResult} data-testid="record-result">
      <div className={styles.recResultTop}>
        {([0, 1] as const).map((t) => (
          <div key={t} className={styles.rhTeam} style={{ order: t === 0 ? 0 : 2 }}>
            <TeamBadge teamName={names[t]} crestUrl={crests[t]} size={56} eager />
            <span className={styles.rhName} style={{ color: t === 0 ? "var(--sim-home)" : "var(--sim-away)" }}>
              {names[t]}
            </span>
          </div>
        ))}
        <div className={styles.rhScore} style={{ order: 1 }}>
          <span className={styles.recResultScore}>
            {snap.single.score[0]} - {snap.single.score[1]}
          </span>
          <span className={styles.rhTag}>模拟 · 全场结束</span>
        </div>
      </div>
      {goals.length ? (
        <div className={styles.rhEvents}>
          {([0, 1] as const).map((t) => (
            <ul key={t} className={`${styles.rhEventList} ${t === 1 ? styles.rhEventListAway : ""}`}>
              {goals
                .filter((g) => g.team === t)
                .map((g, i) => (
                  <li key={i} className={styles.rhEvent}>
                    {g.playerId ? <PlayerAvatar playerId={g.playerId} playerName={g.playerName ?? ""} size={28} eager /> : null}
                    <span className={styles.rhEventName}>
                      {g.playerName ?? ""}
                      {g.channel === "owngoal" ? "(乌龙)" : g.channel === "penalty" ? "(点球)" : ""}
                    </span>
                    <span className={styles.rhEventMin}>{g.clock}</span>
                  </li>
                ))}
            </ul>
          ))}
        </div>
      ) : (
        <p className={styles.rhNoGoal}>本场没有进球</p>
      )}
      <p className={styles.verdict}>{verdict.text}</p>
      <p className={styles.recBrand}>喵弟数据研究室 · miaomiaodi.vip/simulator · 模拟比赛,非真实结果</p>
      <button type="button" className={styles.recExit} onClick={onExit} data-testid="record-exit">
        退出录屏
      </button>
    </div>
  );
}
