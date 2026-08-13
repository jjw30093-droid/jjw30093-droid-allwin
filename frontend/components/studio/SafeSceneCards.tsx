"use client";

import Image from "next/image";
import { SpecChart, type ChartSpec } from "@/components/charts/SpecCharts";
import { fmtUtc } from "./api";
import type {
  DouyinSafeProfile,
  SafeMetricPair,
  SafeSceneId,
} from "./types";
import styles from "./SafeSceneCards.module.css";

function TeamMark({
  name,
  crestUrl,
  side,
}: {
  name: string;
  crestUrl?: string | null;
  side: "home" | "away";
}) {
  return (
    <div className={styles.teamMark} data-side={side}>
      <span className={styles.crest}>
        {crestUrl ? (
          <Image src={crestUrl} alt={`${name}队徽`} width={132} height={132} unoptimized />
        ) : (
          <span className={styles.crestFallback}>{Array.from(name).slice(0, 2)}</span>
        )}
      </span>
      <strong>{name}</strong>
      <span>{side === "home" ? "主队" : "客队"}</span>
    </div>
  );
}

function MetricRow({
  metric,
  homeName,
  awayName,
}: {
  metric: SafeMetricPair;
  homeName: string;
  awayName: string;
}) {
  const maximum = Math.max(metric.home.value, metric.away.value, 0.0001);
  return (
    <div className={styles.metric}>
      <div className={styles.metricHead}>
        <strong>{metric.label}</strong>
        <span>{metric.direction === "lower" ? "越低越好" : "赛季数据"}</span>
      </div>
      <div className={styles.metricValues}>
        <div className={styles.metricSide}>
          <span>{homeName}</span>
          <strong>{metric.home.display}</strong>
          <em>联赛第 {metric.home.rank}</em>
        </div>
        <div className={styles.metricSide} data-align="right">
          <span>{awayName}</span>
          <strong>{metric.away.display}</strong>
          <em>联赛第 {metric.away.rank}</em>
        </div>
      </div>
      <div className={styles.barPair} aria-hidden>
        <span>
          <i style={{ width: `${Math.max(7, metric.home.value / maximum * 100)}%` }} />
        </span>
        <span>
          <i style={{ width: `${Math.max(7, metric.away.value / maximum * 100)}%` }} />
        </span>
      </div>
    </div>
  );
}

function FormStrip({
  label,
  values,
}: {
  label: string;
  values: string[];
}) {
  return (
    <div className={styles.formLine}>
      <strong>{label}</strong>
      <div>
        {values.slice(0, 5).map((value, index) => (
          <span key={`${value}-${index}`} data-result={value}>
            {value}
          </span>
        ))}
        {values.length === 0 && <em>数据不足</em>}
      </div>
    </div>
  );
}

function CompactCrest({
  name,
  crestUrl,
}: {
  name: string;
  crestUrl?: string | null;
}) {
  return crestUrl ? (
    <Image src={crestUrl} alt="" width={52} height={52} unoptimized />
  ) : (
    <span className={styles.compactFallback}>{Array.from(name).slice(0, 1)}</span>
  );
}

export function SafeSceneCard({
  profile,
  scene,
  height,
  chartSpecs = [],
}: {
  profile: DouyinSafeProfile;
  scene: SafeSceneId;
  height: 1920 | 1350;
  /**
   * 来自 analysis_bundle 的图表规格。安全版此前**一张图都没有**,只有纯 CSS
   * 双色条 —— 实测导出的 PNG 下方约 25% 是纯空白,而站长要的是"数据可视化
   * 做得更用心"。这里给"禁区威胁"场景补一张真实射门分布图(空间即解释)。
   *
   * 只取 shot_map_explorer:它不含任何概率/赔率字段,不会破坏
   * backend/studio/team_style.py::assert_safe_content 守的抖音过审边界。
   */
  chartSpecs?: ChartSpec[];
}) {
  const data = profile.scenes.find((item) => item.id === scene);
  if (!data) return null;
  const { match } = profile;
  const isCover = scene === "cover";
  const isSummary = scene === "summary";
  // 禁区威胁场景补射门图;其余场景保持原样(指标对比条信息密度已经够)
  const shotSpec =
    scene === "threat"
      ? chartSpecs.find((s) => s.type === "shot_map_explorer") ?? null
      : null;
  return (
    <article
      className={styles.card}
      data-height={height}
      data-testid={`safe-scene-${scene}`}
      style={{ width: 1080, height }}
    >
      <div className={styles.safeRight} aria-hidden />
      <header className={styles.header}>
        <div className={styles.brandLine}>
          <strong>欧赢</strong>
          <span>ALLWIN · MATCH LAB</span>
          <b>{data.index}</b>
        </div>
        <div className={styles.contextLine}>
          <span>{match.league_name}</span>
          <span>{match.season}{match.round ? ` · 第${match.round}轮` : ""}</span>
          <span>北京时间 {fmtUtc(match.kickoff_at_utc)}</span>
        </div>
      </header>

      <main className={styles.body}>
        <p className={styles.eyebrow}>{data.label}</p>
        <h1>{data.title}</h1>

        {isCover ? (
          <div className={styles.coverBody}>
            <div className={styles.teams}>
              <TeamMark
                name={match.home.name}
                crestUrl={match.home.crest_url}
                side="home"
              />
              <span className={styles.versus}>VS</span>
              <TeamMark
                name={match.away.name}
                crestUrl={match.away.crest_url}
                side="away"
              />
            </div>
            <div className={styles.coverRule}>
              <span />
              <p>控球组织 · 禁区威胁 · 边路定位球 · 无球压力</p>
            </div>
          </div>
        ) : isSummary ? (
          <div className={styles.summaryBody}>
            <div className={styles.formBox}>
              <span className={styles.blockLabel}>最近五场</span>
              <FormStrip label={match.home.name} values={data.recent_form?.home ?? []} />
              <FormStrip label={match.away.name} values={data.recent_form?.away ?? []} />
            </div>
            <ol className={styles.conclusionList}>
              {data.conclusions.slice(0, 3).map((text, index) => (
                <li key={text}>
                  <span>0{index + 1}</span>
                  <p>{text}</p>
                </li>
              ))}
            </ol>
            <div className={styles.riskBox}>
              <span>数据提示</span>
              {(data.risk_notes ?? []).slice(0, 2).map((text) => <p key={text}>{text}</p>)}
            </div>
            <div className={styles.cta}>{data.cta}</div>
          </div>
        ) : (
          <div className={styles.analysisBody}>
            <div className={styles.versusMini}>
              <span>
                <CompactCrest name={match.home.name} crestUrl={match.home.crest_url} />
                {match.home.name}
              </span>
              <b>对比</b>
              <span>
                <CompactCrest name={match.away.name} crestUrl={match.away.crest_url} />
                {match.away.name}
              </span>
            </div>
            <div className={styles.metricList}>
              {/* 有射门图时指标压到 2 条,给图留出版面;否则保持 3 条 */}
              {data.metrics.slice(0, shotSpec ? 2 : 3).map((metric) => (
                <MetricRow
                  key={metric.key}
                  metric={metric}
                  homeName={match.home.name}
                  awayName={match.away.name}
                />
              ))}
              {data.metrics.length === 0 && (
                <p className={styles.missing}>这组指标暂不可用，未以 0 填充。</p>
              )}
            </div>
            {shotSpec && (
              <div className={styles.chartBox}>
                <span className={styles.chartCaption}>近 5 场射门落点</span>
                {/* export 模式:大字号、关动画、隐藏筛选控件 */}
                <SpecChart
                  spec={shotSpec}
                  height={height >= 1600 ? 560 : 400}
                  mode="export"
                />
              </div>
            )}
            {data.conclusions[0] && (
              <p className={styles.takeaway}><span>观察</span>{data.conclusions[0]}</p>
            )}
          </div>
        )}
      </main>

      <footer className={styles.footer}>
        <div>
          <span>数据截止 {fmtUtc(profile.data_cutoff_at)}</span>
          <span>赛季统计 · 场均值按已赛场次换算</span>
        </div>
        <code>{profile.source_hash.slice(0, 12)}</code>
      </footer>
      <div className={styles.safeBottom} aria-hidden />
    </article>
  );
}
