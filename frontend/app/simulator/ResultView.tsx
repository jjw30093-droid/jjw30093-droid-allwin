"use client";

import { XgRaceChart } from "@/components/matches/XgRaceChart";
import { FOCUS_LABEL } from "@/features/simulator/engine";
import { ahText, type ResultSnapshot } from "@/features/simulator/snapshot";
import { verdictOf } from "@/features/simulator/verdict";
import { cumulativeXg, toReportShots } from "@/features/simulator/xg";
import { Fold } from "./Fold";
import { SharePanel } from "./SharePanel";
import styles from "./simulator.module.css";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

// 只读快照:本页刚模拟的结果与分享链接打开的结果走同一套展示,保证分享出去的内容与本页一致。
// 两层:默认显示(比分、进球者、一句话解读、胜平负、xG 赛跑、分享)→ "更多数据"(默认收起)。
// "技术细节"(模拟编号、λ、市场参考让球/大小球、模型版本等)按站长要求不在前端展示。
// 面向用户的文字不出现任何博彩公司名称,统一称"市场参考"。
export function ResultView({ snap, onRerun, onBack }: { snap: ResultSnapshot; onRerun: () => void; onBack: () => void }) {
  const { single, many } = snap;
  const names: [string, string] = [snap.teams[0].name, snap.teams[1].name];
  const goals = single.events.filter((e) => e.kind === "goal");
  const reds = single.events.filter((e) => e.kind === "red");
  const teamIds: [number, number] = [snap.teams[0].teamId, snap.teams[1].teamId];
  const shots = toReportShots(single.events, teamIds, single.halfTimeTick);
  const xgTotal = cumulativeXg(single.events);
  const verdict = verdictOf(snap);
  const expected = snap.teams.map((t) => t.expectedGoals);
  return (
    <div data-testid="sim-result">
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>本次模拟</h2>
        <div className={styles.scoreboard}>
          <span className={styles.sbTeam} style={{ color: "var(--sim-home)" }}>{names[0]}</span>
          <div className={styles.sbScore}>
            {single.score[0]} : {single.score[1]}
          </div>
          <span className={styles.sbTeam} style={{ color: "var(--sim-away)" }}>{names[1]}</span>
        </div>
        <ul className={styles.feed}>
          {goals.map((g, i) => (
            <li key={i}>
              {g.clock} {names[g.team]} {g.playerName ?? ""}
              {g.channel === "owngoal" ? "(乌龙)" : g.channel === "penalty" ? "(点球)" : ""}
            </li>
          ))}
          {reds.map((r, i) => (
            <li key={`r${i}`}>
              {r.clock} {names[r.team]} 红牌 {r.playerName ?? ""}
            </li>
          ))}
          {goals.length === 0 ? <li className={styles.muted}>本场没有进球</li> : null}
        </ul>
        <p className={styles.verdict} data-testid="sim-verdict" data-kind={verdict.kind}>
          {verdict.text}
        </p>
        {/* 技术细节已不在前端展示;用了已完赛比赛的参数必须如实告知(分享链接打开的人看不到排阵页的提示) */}
        {snap.market?.finalScore ? (
          <p className={styles.hint} data-testid="sim-postmatch">
            本场参数包含赛后数据(实际比分 {snap.market.finalScore.join(" : ")}),仅供演示。
          </p>
        ) : null}
      </section>

      <section className={styles.card}>
        <h2 className={styles.cardTitle}>{many.runs} 次模拟 · 胜平负</h2>
        <div className={styles.wdl} role="img" aria-label={`${names[0]}胜 ${pct(many.pHome)},平 ${pct(many.pDraw)},${names[1]}胜 ${pct(many.pAway)}`}>
          <span className={styles.wdlHome} style={{ width: pct(many.pHome) }} />
          <span className={styles.wdlDraw} style={{ width: pct(many.pDraw) }} />
          <span className={styles.wdlAway} style={{ width: pct(many.pAway) }} />
        </div>
        <div className={styles.wdlLegend}>
          <span>
            {names[0]}胜 {pct(many.pHome)}
          </span>
          <span>平 {pct(many.pDraw)}</span>
          <span>
            {names[1]}胜 {pct(many.pAway)}
          </span>
        </div>
      </section>

      <section className={styles.card}>
        <h2 className={styles.cardTitle}>xG 赛跑</h2>
        <p className={styles.muted} style={{ marginTop: 0 }}>
          累计 xG:{names[0]} {xgTotal[0].toFixed(2)},{names[1]} {xgTotal[1].toFixed(2)}
        </p>
        {/* 不传比分:该组件的文字摘要把比分写成"实际比分",不适用于模拟 */}
        <XgRaceChart
          shots={shots}
          homeName={names[0]}
          awayName={names[1]}
          stoppage={{ firstHalf: single.stoppage[0], secondHalf: single.stoppage[1] }}
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
          <h3 className={styles.subTitle}>球员至少进 1 球的概率</h3>
          <ol className={styles.list}>
            {many.scorerProb.map((s) => (
              <li key={`${s.team}:${s.playerId}`}>
                <span>
                  {s.name}{" "}
                  <span className={styles.muted} style={{ color: s.team === 0 ? "var(--sim-home)" : "var(--sim-away)" }}>
                    {names[s.team]}
                  </span>
                </span>
                <span>{pct(s.p)}</span>
              </li>
            ))}
          </ol>
          <p className={styles.muted}>不含乌龙球。</p>
        </div>
        <div className={styles.subsection} data-testid="sim-settings">
          <h3 className={styles.subTitle}>本场设定</h3>
          {snap.teams.map((t, i) => (
            <p key={i} className={styles.settingsLine}>
              <span style={{ color: i === 0 ? "var(--sim-home)" : "var(--sim-away)", fontWeight: 700 }}>{names[i]}</span> {t.formation} ·
              侧重点:{t.focuses.length ? t.focuses.map((f) => FOCUS_LABEL[f]).join("、") : "无"}
              {t.shortRest ? " · 休息不足 3 天(假设设定)" : ""}
            </p>
          ))}
        </div>
      </Fold>

      <SharePanel snap={snap} />

      <div className={styles.row} style={{ marginBottom: "var(--sp-4)" }}>
        <button type="button" className={styles.primaryBtn} onClick={onRerun}>
          再模拟一次
        </button>
        <button type="button" className={styles.secondaryBtn} onClick={onBack}>
          回到排阵
        </button>
      </div>

    </div>
  );
}
