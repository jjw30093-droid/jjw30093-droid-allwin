/**
 * Creator Studio 类型:analysis_bundle 与 content_drafts 的前端形状。
 *
 * 后端 /api/v1/studio/* 返回 dict(OpenAPI 里是 unknown),字段以
 * backend/studio/bundle.py 与 backend/api/routes_studio.py 为真源,
 * 这里手写对应 TS 形状(只在 Studio 内部使用,不与 lib/api-types.ts 漂移竞争)。
 */

import type { ChartSpec } from "@/components/charts/SpecCharts";

export interface BundleTeamRef {
  team_id: number | null;
  name: string;
  name_en?: string | null;
}

export interface BundleMatch {
  match_id: number;
  league_id: number;
  season: string;
  date_utc: string; // 日期粒度(dim_match 无开球时刻)
  round: string | null;
  status: string;
  home: BundleTeamRef;
  away: BundleTeamRef;
  home_score: number | null;
  away_score: number | null;
}

export interface EvidenceItem {
  side?: string;
  kind: string;
  text: string;
}

export interface ScriptSection {
  id: string;
  title: string;
  text: string;
}

export interface SubtitleCue {
  start: number;
  end: number;
  text: string;
}

export interface SourceNote {
  kind: string;
  text: string;
}

export interface PredictionPublic {
  top_outcome: "home" | "draw" | "away";
  top_probability: number;
}

export interface PredictionMember {
  home_probability: number;
  draw_probability: number;
  away_probability: number;
  expected_home_goals: number | null;
  expected_away_goals: number | null;
  status: string;
  prediction_hash: string;
}

export interface AnalysisBundle {
  bundle_version: string;
  built_at: string;
  bundle_hash: string;
  match: BundleMatch;
  data_cutoff_at: string | null;
  model_version: string | null;
  prediction_public: PredictionPublic | null;
  prediction_member: PredictionMember | null;
  evidence: EvidenceItem[];
  counter_evidence: EvidenceItem[];
  uncertainty: EvidenceItem[];
  odds_timeline: unknown[];
  cooccurring_events: unknown[];
  chart_specs: ChartSpec[];
  script_sections: ScriptSection[];
  subtitle_cues: SubtitleCue[];
  source_notes: SourceNote[];
}

/** 草稿 overrides:只覆盖可编辑文本,冻结的 bundle 本体不动。 */
export interface DraftOverrides {
  title?: string;
  script_sections?: ScriptSection[];
  subtitle_cues?: SubtitleCue[];
  evidence?: EvidenceItem[];
  counter_evidence?: EvidenceItem[];
  uncertainty?: EvidenceItem[];
}

export type DraftStatus = "draft" | "reviewed" | "published";

export interface DraftSummary {
  id: string;
  match_id: number;
  title: string;
  status: DraftStatus;
  created_at: string;
  updated_at: string;
}

export interface DraftDetail {
  id: string;
  match_id: number;
  title: string;
  status: DraftStatus;
  bundle: AnalysisBundle;
  overrides: DraftOverrides;
  created_at: string;
  updated_at: string;
}

export type ExportKind =
  | "png_1080x1920"
  | "png_1080x1350"
  | "txt"
  | "json"
  | "srt";

export interface ExportResult {
  job_id: string;
  side: "client" | "server";
  download_url?: string;
  data_cutoff_at: string | null;
  model_version: string | null;
}

/** 六段式竖屏场景(顺序即导出顺序)。 */
export const SCENES = [
  { id: "hook", label: "开场钩子" },
  { id: "probability", label: "核心概率" },
  { id: "evidence", label: "支持证据" },
  { id: "risk", label: "反向证据与风险" },
  { id: "charts", label: "数据可视化" },
  { id: "outro", label: "收尾与免责" },
] as const;

export type SceneId = (typeof SCENES)[number]["id"];

export const STATUS_ZH: Record<DraftStatus, string> = {
  draft: "草稿",
  reviewed: "已审阅",
  published: "已发布",
};

export const OUTCOME_ZH: Record<"home" | "draw" | "away", string> = {
  home: "主胜",
  draw: "平局",
  away: "客胜",
};
