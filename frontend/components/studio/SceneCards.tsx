"use client";

/**
 * 六段式竖屏场景渲染器:同一份 DOM 同时用于
 * ① 编辑器里的缩放预览(外层 transform: scale)
 * ② html-to-image 的 PNG 导出(按 1080×1920 / 1080×1350 逻辑像素布局)。
 *
 * 数据全部来自冻结的 analysis_bundle + 草稿 overrides,不在这里另写解释逻辑
 * (宪法 §12);每张卡固定渲染 数据截止时间 + 模型版本,导出 PNG 自然携带。
 */

import { SpecChart } from "@/components/charts/SpecCharts";
import { fmtUtc } from "./api";
import type {
  AnalysisBundle,
  DraftOverrides,
  EvidenceItem,
  SceneId,
} from "./types";
import { OUTCOME_ZH, SCENES } from "./types";
import styles from "./SceneCards.module.css";

/**
 * 卡片选图优先级(视觉冲击力,非 bundle 构造顺序)。
 * 射门图是空间型图表,位置本身即解释,对短视频观众最有说服力,排第一。
 */
const CARD_CHART_PRIORITY = [
  "shot_map_explorer",
  "xg_compare",
  "probability_bar",
];

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function EvidenceList({
  items,
  tone,
  emptyText,
}: {
  items: EvidenceItem[];
  tone: "gold" | "loss" | "draw";
  emptyText: string;
}) {
  if (items.length === 0) {
    return <p className={styles.emptyText}>{emptyText}</p>;
  }
  return (
    <ul className={styles.evList}>
      {items.map((it, i) => (
        <li key={`${it.kind}-${i}`} className={styles.evItem} data-tone={tone}>
          {it.text}
        </li>
      ))}
    </ul>
  );
}

export function SceneCard({
  bundle,
  overrides,
  draftTitle,
  scene,
  height,
}: {
  bundle: AnalysisBundle;
  overrides: DraftOverrides;
  draftTitle: string;
  scene: SceneId;
  height: 1920 | 1350;
}) {
  const m = bundle.match;
  const sections = overrides.script_sections ?? bundle.script_sections;
  const evidence = overrides.evidence ?? bundle.evidence;
  const counter = overrides.counter_evidence ?? bundle.counter_evidence;
  const uncertainty = overrides.uncertainty ?? bundle.uncertainty;
  const sec = (id: string) =>
    sections.find((x) => x.id === id)?.text ?? "";
  const title =
    overrides.title || draftTitle || `${m.home.name} vs ${m.away.name}`;
  const label = SCENES.find((s) => s.id === scene)?.label ?? scene;
  const pm = bundle.prediction_member;
  const pp = bundle.prediction_public;
  const probabilityLabel =
    bundle.probability_source === "MARKET_BASELINE" ? "市场去水基线" : "模型";

  // 卡片选图按"视觉冲击力"排序,不按 bundle 的构造顺序截断。
  // 旧实现 chart_specs.slice(0, maxCharts) 配上 bundle.py 的固定顺序
  // (prob_bar → xg_compare → recent_shot_map)导致
  // 射门图永远排最后、永远进不了卡片 —— 而它恰恰是最有说服力的一张。
  const chartSpecs = [...bundle.chart_specs]
    .sort(
      (a, b) =>
        (CARD_CHART_PRIORITY.indexOf(a.type) + 1 || 99) -
        (CARD_CHART_PRIORITY.indexOf(b.type) + 1 || 99),
    )
    // 每张卡最多 2 张图(1920)/ 1 张(1350)。旧的 3 张会溢出 .body 可用高度
    // (3×(图316+padding56+标题44+摘要40)+gap64 ≈ 1432 > 可用 ≈1420),
    // 第三张被 overflow:hidden 裁掉。少而大 > 多而挤。
    .slice(0, height >= 1600 ? 2 : 1);
  const chartHeight = Math.max(
    320,
    Math.floor((height - 640) / Math.max(1, chartSpecs.length)) - 170,
  );

  let body: React.ReactNode;
  if (scene === "hook") {
    body = (
      <>
        <div className={styles.hookTitle}>{title}</div>
        <p className={styles.hookText}>{sec("hook")}</p>
        <p className={styles.sceneTextMuted}>{sec("context")}</p>
      </>
    );
  } else if (scene === "probability") {
    body = pm ? (
      <>
        <div className={styles.probGrid}>
          <div className={styles.probCell} data-tone="win">
            <div className={styles.probNum}>{pct(pm.home_probability)}</div>
            <div className={styles.probLabel}>主胜</div>
          </div>
          <div className={styles.probCell} data-tone="draw">
            <div className={styles.probNum}>{pct(pm.draw_probability)}</div>
            <div className={styles.probLabel}>平局</div>
          </div>
          <div className={styles.probCell} data-tone="loss">
            <div className={styles.probNum}>{pct(pm.away_probability)}</div>
            <div className={styles.probLabel}>客胜</div>
          </div>
        </div>
        <div className={styles.probBar} aria-hidden>
          <span
            className={styles.segWin}
            style={{ width: `${pm.home_probability * 100}%` }}
          />
          <span
            className={styles.segDraw}
            style={{ width: `${pm.draw_probability * 100}%` }}
          />
          <span
            className={styles.segLoss}
            style={{ width: `${pm.away_probability * 100}%` }}
          />
        </div>
        {pm.expected_home_goals != null && pm.expected_away_goals != null && (
          <div className={styles.expGoals}>
            预期进球 {m.home.name} {pm.expected_home_goals} -{" "}
            {pm.expected_away_goals} {m.away.name}
          </div>
        )}
        <p className={styles.sceneText}>{sec("probability")}</p>
        <p className={styles.probNote}>{probabilityLabel}概率是赛前估计,不是定论。</p>
      </>
    ) : pp ? (
      <>
        <div className={styles.topOnly}>
          <div className={styles.probLabel}>{probabilityLabel}最高概率结果</div>
          <div className={styles.probNumBig}>
            {OUTCOME_ZH[pp.top_outcome]} {pct(pp.top_probability)}
          </div>
        </div>
        <p className={styles.sceneText}>{sec("probability")}</p>
      </>
    ) : (
      <p className={styles.emptyText}>
        该场比赛暂无可靠概率,此场景导出前请确认是否保留。
      </p>
    );
  } else if (scene === "evidence") {
    body = (
      <>
        <p className={styles.sceneTextMuted}>{sec("data")}</p>
        <EvidenceList
          items={evidence}
          tone="gold"
          emptyText="本场暂无自动生成的支持证据(数据不足),可在编辑面板手动补充。"
        />
      </>
    );
  } else if (scene === "risk") {
    body = (
      <>
        <div className={styles.subHeading}>反向证据</div>
        <EvidenceList
          items={counter}
          tone="loss"
          emptyText="本场无特别突出的反向证据。"
        />
        <div className={styles.subHeading}>不确定性</div>
        <EvidenceList
          items={uncertainty}
          tone="draw"
          emptyText="暂无额外的不确定性说明。"
        />
      </>
    );
  } else if (scene === "charts") {
    body =
      chartSpecs.length === 0 ? (
        <p className={styles.emptyText}>
          该场比赛暂无可视化数据(chart_specs 为空)。
        </p>
      ) : (
        chartSpecs.map((spec) => (
          <div key={spec.id} className={styles.chartBlock}>
            <div className={styles.chartTitle}>{spec.title}</div>
            {/* 卡片是 1080px 宽的静态导出物:大字号、关动画、隐藏交互控件 */}
            <SpecChart spec={spec} height={chartHeight} mode="export" />
          </div>
        ))
      );
  } else {
    body = (
      <>
        <p className={styles.hookText}>{sec("outro")}</p>
        <ul className={styles.noteList}>
          {bundle.source_notes.map((n, i) => (
            <li key={`${n.kind}-${i}`} className={styles.noteItem}>
              {n.text}
            </li>
          ))}
        </ul>
        <p className={styles.disclaimer}>
          本内容为赛前数据分析,不构成任何投注建议;比赛存在不确定性。
        </p>
        <p className={styles.hashLine}>
          分析包 {bundle.bundle_hash.slice(0, 12)} · 生成于{" "}
          {fmtUtc(bundle.built_at)}
        </p>
      </>
    );
  }

  return (
    <div className={styles.card} style={{ width: 1080, height }}>
      <header className={styles.head}>
        <div className={styles.brandRow}>
          <span className={styles.brand}>欧赢</span>
          <span className={styles.brandLatin}>ALLWIN</span>
          <span className={styles.sceneTag}>{label}</span>
        </div>
        <div className={styles.matchRow}>
          <span className={styles.teamName}>{m.home.name}</span>
          <span className={styles.vs}>VS</span>
          <span className={styles.teamName}>{m.away.name}</span>
        </div>
        <div className={styles.metaRow}>
          {m.season} 赛季{m.round ? ` · 第 ${m.round} 轮` : ""} · 比赛日{" "}
          {m.date_utc}(UTC)
        </div>
      </header>
      <div className={styles.body}>{body}</div>
      <footer className={styles.foot}>
        <span>数据截止:{fmtUtc(bundle.data_cutoff_at)}</span>
        <span>概率来源:{bundle.probability_source} · 版本:{bundle.model_version ?? "无"}</span>
      </footer>
    </div>
  );
}
