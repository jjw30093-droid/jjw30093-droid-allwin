"use client";

import { XgRaceChart } from "@/components/matches/XgRaceChart";
import { FOCUS_LABEL, rarityTag, type ManyResult, type MatchConfig, type MatchSetup, type SingleResult } from "@/features/simulator/engine";
import type { SimParams } from "@/features/simulator/types";
import { cumulativeXg, toReportShots } from "@/features/simulator/xg";
import { displayName, POS_LABEL } from "./LineupEditor";
import { CHANNEL_LABEL } from "./MatchAnimation";
import styles from "./simulator.module.css";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

// 库内口径:line > 0 = 主让(research/ah_signals/common.py 顶部注释)。
function ahText(line: number): string {
  if (Math.abs(line) < 1e-9) return "平手 0";
  return line > 0 ? `主让 ${line}` : `主受让 ${Math.abs(line)}`;
}

const OUTCOME_LABEL = { H: "主胜", D: "平局", A: "客胜" } as const;

export function ResultView({
  params,
  single,
  many,
  config,
  setup,
  onRerun,
  onBack,
}: {
  params: SimParams;
  single: SingleResult;
  many: ManyResult;
  config: MatchConfig;
  setup: MatchSetup;
  onRerun: () => void;
  onBack: () => void;
}) {
  const names: [string, string] = [config.teams[0].name, config.teams[1].name];
  const goals = single.events.filter((e) => e.kind === "goal");
  const reds = single.events.filter((e) => e.kind === "red");
  const teamIds: [number, number] = [config.teams[0].teamId, config.teams[1].teamId];
  const shots = toReportShots(single.events, teamIds, single.halfTimeTick);
  const xgTotal = cumulativeXg(single.events);
  const tag = rarityTag(many.upset.scoreCount, many.runs);
  return (
    <div data-testid="sim-result">
      <section className={styles.card}>
        <h2 className={styles.cardTitle}>本次模拟</h2>
        <div className={styles.scoreboard}>
          <span className={styles.sbTeam}>{names[0]}</span>
          <div className={styles.sbScore}>
            {single.score[0]} : {single.score[1]}
          </div>
          <span className={styles.sbTeam}>{names[1]}</span>
        </div>
        <ul className={styles.feed}>
          {goals.map((g, i) => (
            <li key={i}>
              {g.clock} {names[g.team]} {g.playerName ?? ""}
              {g.channel === "owngoal" ? "(乌龙)" : `(${CHANNEL_LABEL[g.channel ?? "open"]}${g.xg !== undefined ? `,xG ${g.xg.toFixed(2)}` : ""})`}
            </li>
          ))}
          {reds.map((r, i) => (
            <li key={`r${i}`}>
              {r.clock} {names[r.team]} 红牌 {r.playerName ?? ""}
            </li>
          ))}
          {goals.length === 0 ? <li className={styles.muted}>本场没有进球</li> : null}
        </ul>
        <p className={styles.muted}>
          种子 {single.seed} · 状态系数 ε = {single.epsilon.map((x) => x.toFixed(2)).join(" / ")}(同一种子复现同一结果)
        </p>
      </section>

      <section className={styles.card}>
        <h2 className={styles.cardTitle}>模拟 xG 赛跑</h2>
        <p className={styles.muted} style={{ marginTop: 0 }}>
          本次模拟比分 {single.score.join(" : ")},累计 xG:{names[0]} {xgTotal[0].toFixed(2)},{names[1]} {xgTotal[1].toFixed(2)};阶梯线,圆点为进球,补时并入第 45 / 90 分钟。
        </p>
        {/* 不传比分:该组件的文字摘要把比分写成"实际比分",不适用于模拟 */}
        <XgRaceChart shots={shots} homeName={names[0]} awayName={names[1]} />
      </section>

      <section className={styles.card} data-testid="sim-settings">
        <h2 className={styles.cardTitle}>本场设定</h2>
        <div className={styles.teams}>
          {([setup.home, setup.away] as const).map((t, i) => (
            <div key={i}>
              <div className={styles.teamName}>
                {names[i]} · {t.formation}
              </div>
              <p className={styles.settingsLine}>
                侧重点:{t.focuses.length ? t.focuses.map((f) => FOCUS_LABEL[f]).join("、") : "无"}
                {t.shortRest ? " · 休息不足 3 天(假设设定)" : ""}
              </p>
              <p className={styles.settingsLine}>
                首发:
                {t.slots
                  .map((sl) => {
                    const p = sl.playerId ? params.players[sl.playerId] : null;
                    return `${p ? displayName(p) : "空位"}(${POS_LABEL[sl.group]})`;
                  })
                  .join("、")}
              </p>
            </div>
          ))}
        </div>
        <p className={styles.settingsLine}>
          随机强度:{setup.chaos ? "混乱模式(κ=4)" : "标准档(κ=12)"} · 种子 {single.seed} · 补时 {single.stoppage[0]} / {single.stoppage[1]} 分钟
          {config.market ? ` · Crown 盘口定锚(${config.market.status})` : " · 未用盘口定锚"}
        </p>
      </section>

      <div className={styles.resultGrid}>
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>{many.runs} 次模拟 · 胜平负</h2>
          <div className={styles.wdl} role="img" aria-label={`主胜 ${pct(many.pHome)},平 ${pct(many.pDraw)},客胜 ${pct(many.pAway)}`}>
            <span className={styles.wdlHome} style={{ width: pct(many.pHome) }} />
            <span className={styles.wdlDraw} style={{ width: pct(many.pDraw) }} />
            <span className={styles.wdlAway} style={{ width: pct(many.pAway) }} />
          </div>
          <div className={styles.wdlLegend}>
            <span>主胜 {pct(many.pHome)}</span>
            <span>平 {pct(many.pDraw)}</span>
            <span>客胜 {pct(many.pAway)}</span>
          </div>
          <h3 className={styles.cardTitle} style={{ marginTop: 16, fontSize: "var(--fs-h3)" }}>
            最常见的比分
          </h3>
          <ol className={styles.list}>
            {many.topScores.map((s) => (
              <li key={s.score}>
                <span>{s.score.replace("-", " : ")}</span>
                <span>{pct(s.p)}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className={styles.card}>
          <h2 className={styles.cardTitle}>模拟盘口</h2>
          <dl className={styles.kv}>
            <dt>模拟公平让球线</dt>
            <dd>{ahText(many.fairAhLine)}</dd>
            <dt>模拟公平大小球线</dt>
            <dd>{many.fairOuLine}</dd>
            <dt>模拟场均进球</dt>
            <dd>
              {many.meanGoals[0].toFixed(2)} / {many.meanGoals[1].toFixed(2)}
            </dd>
            <dt>期望进球(含对方门将、乌龙)</dt>
            <dd>
              {config.teams[0].breakdown.expectedGoals.toFixed(2)} / {config.teams[1].breakdown.expectedGoals.toFixed(2)}
            </dd>
            {many.crown ? (
              <>
                <dt>Crown {many.crown.ahLine != null ? ahText(many.crown.ahLine) : "让球 —"}</dt>
                <dd>主队赢盘 p_eff {many.crown.pEffAhHome != null ? pct(many.crown.pEffAhHome) : "—"}</dd>
                <dt>Crown 大小 {many.crown.ouLine ?? "—"}</dt>
                <dd>大球 p_eff {many.crown.pEffOver != null ? pct(many.crown.pEffOver) : "—"}</dd>
              </>
            ) : null}
          </dl>
          {config.market ? (
            <p className={styles.muted}>
              市场定锚:Crown {config.market.status}
              {config.market.finalScore ? `,实际比分 ${config.market.finalScore.join(" : ")};本场参数包含赛后数据,仅供演示` : ""}
            </p>
          ) : (
            <p className={styles.muted}>本对阵没有 Crown 盘口,λ 只用数据模型。</p>
          )}
          <h3 className={styles.cardTitle} style={{ marginTop: 16, fontSize: "var(--fs-h3)" }}>
            爆冷指数
          </h3>
          <p style={{ fontSize: 14, margin: 0 }}>
            <span className={styles.rarity} data-testid="rarity-tag">
              {tag}
            </span>{" "}
            本次比分 {single.score.join(" : ")} 在 {many.runs} 次中出现 {many.upset.scoreCount} 次;本次结果({OUTCOME_LABEL[many.upset.outcome]})占 {pct(many.upset.outcomeShare)}。
          </p>
        </section>

        <section className={styles.card}>
          <h2 className={styles.cardTitle}>球员至少进 1 球的概率</h2>
          <ol className={styles.list}>
            {many.scorerProb.map((s) => (
              <li key={`${s.team}:${s.playerId}`}>
                <span>
                  {s.name} <span className={styles.muted}>{names[s.team]}</span>
                </span>
                <span>{pct(s.p)}</span>
              </li>
            ))}
          </ol>
          <p className={styles.muted}>不含乌龙球。</p>
        </section>
      </div>

      <div className={styles.row}>
        <button type="button" className={styles.primaryBtn} onClick={onRerun}>
          换个种子再模拟
        </button>
        <button type="button" className={styles.secondaryBtn} onClick={onBack}>
          回到排阵
        </button>
      </div>
    </div>
  );
}
