"use client";

import { XgRaceChart } from "@/components/matches/XgRaceChart";
import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { TeamBadge } from "@/components/teams/TeamBadge";
import type { SimEvent } from "@/features/simulator/engine";
import { FOCUS_LABEL } from "@/features/simulator/engine";
import { ahText, type ResultSnapshot } from "@/features/simulator/snapshot";
import { cumulativeXg, toReportShots } from "@/features/simulator/xg";
import { Fold } from "./Fold";
import { SharePanel } from "./SharePanel";
import styles from "./simulator.module.css";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const pct0 = (x: number) => `${Math.round(x * 100)}%`;

// 只读快照:本页刚模拟的结果与分享链接打开的结果走同一套展示,保证分享出去的内容与本页一致。
// 版式参照 FotMob 比赛页头:队徽 + 大比分,进球者(头像 + 分钟)列在各自球队下方;
// 一句话解读与胜平负放进同一张卡;然后是操作、分享、xG 走势;其余数据收进"更多数据"。
// "技术细节"(模拟编号、λ、市场参考让球/大小球、模型版本等)按站长要求不在前端展示。
// 面向用户的文字不出现任何博彩公司名称。
export function ResultView({
  snap,
  crests,
  onRerun,
  onReplay,
  onBack,
}: {
  snap: ResultSnapshot;
  /** 同源队徽地址(来自参数文件);没有时显示队名首字 */
  crests: [string | null, string | null];
  onRerun: () => void;
  onReplay: () => void;
  onBack: () => void;
}) {
  const { single, many } = snap;
  const names: [string, string] = [snap.teams[0].name, snap.teams[1].name];
  const teamIds: [number, number] = [snap.teams[0].teamId, snap.teams[1].teamId];
  const shots = toReportShots(single.events, teamIds, single.halfTimeTick);
  const xgTotal = cumulativeXg(single.events);
  const expected = snap.teams.map((t) => t.expectedGoals);
  // 进球/红牌按"记在哪一队名下"分两列(乌龙记在受益方,与比分一致)
  const sideEvents = ([0, 1] as const).map((t) => single.events.filter((e) => e.team === t && (e.kind === "goal" || e.kind === "red")));
  const noGoals = single.score[0] + single.score[1] === 0;
  return (
    <div data-testid="sim-result">
      <section className={`${styles.card} ${styles.resultHero}`}>
        <div className={styles.rhTop}>
          {([0, 1] as const).map((t) => (
            <div key={t} className={styles.rhTeam} style={{ order: t === 0 ? 0 : 2 }}>
              <TeamBadge teamName={names[t]} crestUrl={crests[t]} size={56} eager />
              <span className={styles.rhName} style={{ color: t === 0 ? "var(--sim-home)" : "var(--sim-away)" }}>
                {names[t]}
              </span>
            </div>
          ))}
          <div className={styles.rhScore} style={{ order: 1 }}>
            <span className={styles.rhScoreNum} data-testid="sim-score">
              {single.score[0]} - {single.score[1]}
            </span>
          </div>
        </div>

        {noGoals && !sideEvents[0].length && !sideEvents[1].length ? (
          <p className={styles.rhNoGoal}>本场没有进球</p>
        ) : (
          <div className={styles.rhEvents} data-testid="sim-events">
            {([0, 1] as const).map((t) => (
              <ul key={t} className={`${styles.rhEventList} ${t === 1 ? styles.rhEventListAway : ""}`}>
                {sideEvents[t].map((e, i) => (
                  <li key={i} className={styles.rhEvent}>
                    <EventAvatar snap={snap} e={e} />
                    <span className={styles.rhEventName}>
                      {e.playerName ?? ""}
                      {e.kind === "red" ? <span className={styles.redCard} aria-label="红牌" /> : null}
                      {e.channel === "owngoal" ? "(乌龙)" : e.channel === "penalty" ? "(点球)" : ""}
                    </span>
                    <span className={styles.rhEventMin}>{e.clock}</span>
                  </li>
                ))}
              </ul>
            ))}
          </div>
        )}

        {/* 用了已完赛比赛的参数必须如实告知(分享链接打开的人看不到排阵页的提示) */}
        {snap.market?.finalScore ? (
          <p className={styles.hint} data-testid="sim-postmatch">
            本场参数包含赛后数据(实际比分 {snap.market.finalScore.join(" : ")}),仅供演示。
          </p>
        ) : null}

        <div className={styles.rhWdl}>
          <p className={styles.rhWdlTitle}>每 {many.runs} 次模拟的胜平负分布</p>
          <div className={styles.wdl} role="img" aria-label={`${names[0]}胜 ${pct(many.pHome)},平 ${pct(many.pDraw)},${names[1]}胜 ${pct(many.pAway)}`}>
            <span className={styles.wdlHome} style={{ width: pct(many.pHome) }}>
              {many.pHome >= 0.12 ? pct0(many.pHome) : ""}
            </span>
            <span className={styles.wdlDraw} style={{ width: pct(many.pDraw) }}>
              {many.pDraw >= 0.12 ? pct0(many.pDraw) : ""}
            </span>
            <span className={styles.wdlAway} style={{ width: pct(many.pAway) }}>
              {many.pAway >= 0.12 ? pct0(many.pAway) : ""}
            </span>
          </div>
          <div className={styles.wdlLegend}>
            <span>{names[0]}胜</span>
            <span>平</span>
            <span>{names[1]}胜</span>
          </div>
        </div>
      </section>

      <div className={styles.resultActions}>
        <button type="button" className={styles.primaryBtn} onClick={onRerun} data-testid="sim-rerun">
          再来一次
        </button>
        <button type="button" className={styles.secondaryBtn} onClick={onReplay} data-testid="sim-replay">
          重播本场
        </button>
        <button type="button" className={styles.secondaryBtn} onClick={onBack} data-testid="sim-back">
          改阵容
        </button>
      </div>

      <SharePanel snap={snap} />

      <section className={styles.card}>
        <h2 className={styles.cardTitle}>xG 走势</h2>
        <p className={styles.muted} style={{ marginTop: 0 }} data-testid="sim-xg-line">
          {names[0]} {xgTotal[0].toFixed(2)} : {xgTotal[1].toFixed(2)} {names[1]}
        </p>
        {/* 不传比分:该组件的文字摘要把比分写成"实际比分",不适用于模拟;长解释只留给读屏 */}
        <XgRaceChart
          shots={shots}
          homeName={names[0]}
          awayName={names[1]}
          stoppage={{ firstHalf: single.stoppage[0], secondHalf: single.stoppage[1] }}
          height={220}
          showSummary={false}
        />
      </section>

      <Fold title="更多数据" testId="sim-more">
        <div className={styles.subsection}>
          <p className={styles.bigLine} data-testid="sim-expected">
            预计进球 {names[0]} {expected[0].toFixed(1)} : {expected[1].toFixed(1)} {names[1]}
          </p>
          <p className={styles.bigLine} style={{ marginTop: 8 }} data-testid="sim-lines">
            模拟让球：{ahText(many.fairAhLine)} ｜ 模拟大小球：{many.fairOuLine}
          </p>
        </div>
        <div className={styles.subsection}>
          <h3 className={styles.subTitle}>最常见的 5 个比分</h3>
          <ol className={styles.list}>
            {many.topScores.map((s) => (
              <li key={s.score}>
                <span>{s.score.replace("-", " : ")}</span>
                <span>{pct(s.p)}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className={styles.subsection}>
          <h3 className={styles.subTitle}>谁最可能进球</h3>
          <ol className={styles.list}>
            {many.scorerProb.slice(0, 10).map((s) => (
              <li key={`${s.team}:${s.playerId}`}>
                <span className={styles.scorerRow}>
                  <PlayerAvatar playerId={s.playerId} playerName={s.name} shirtNumber={numOf(snap, s.playerId)} size={28} />
                  {s.name}
                  <span className={styles.muted} style={{ color: s.team === 0 ? "var(--sim-home)" : "var(--sim-away)" }}>
                    {names[s.team]}
                  </span>
                </span>
                <span>{pct(s.p)}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className={styles.subsection} data-testid="sim-settings">
          <h3 className={styles.subTitle}>本场设定</h3>
          {snap.teams.map((t, i) => (
            <p key={i} className={styles.settingsLine}>
              <span style={{ color: i === 0 ? "var(--sim-home)" : "var(--sim-away)", fontWeight: 700 }}>{names[i]}</span> {t.formation} ·
              侧重点:{t.focuses.length ? t.focuses.map((f) => FOCUS_LABEL[f]).join("、") : "无"}
              {t.shortRest ? " · 休息不足 3 天" : ""}
            </p>
          ))}
        </div>
      </Fold>
    </div>
  );
}

function numOf(snap: ResultSnapshot, playerId: string | null | undefined): string | null {
  if (!playerId) return null;
  for (const t of snap.teams) {
    const hit = t.lineup.find((x) => x.playerId === playerId);
    if (hit) return hit.num;
  }
  return null;
}

function EventAvatar({ snap, e }: { snap: ResultSnapshot; e: SimEvent }) {
  if (!e.playerId) return <span className={styles.rhEventDot} aria-hidden="true" />;
  return <PlayerAvatar playerId={e.playerId} playerName={e.playerName ?? ""} shirtNumber={numOf(snap, e.playerId)} size={28} />;
}
