/**
 * /api/v1 中 OpenAPI 未类型化(后端返回 dict)或尚未进入 lib/openapi.json 的
 * 端点响应类型。字段与 backend/api/routes_public.py、backend/studio/bundle.py
 * 逐字段对齐;openapi.json 重新生成并覆盖这些端点后,应迁回 lib/api-types.ts。
 */

import type { MatchSummary } from "@/lib/api-v1";

/* ── GET /api/v1/matches/{id}/analysis(公开投影的 analysis_bundle) ── */

export interface EvidenceItem {
  side: string; // home / away / draw
  kind: string; // form / xg / draw_risk / ...
  text: string;
}

export interface UncertaintyItem {
  kind: string;
  text: string;
}

export interface SourceNote {
  kind: string;
  text: string;
}

export interface ScriptSection {
  id: string;
  title: string;
  text: string;
}

export interface AnalysisChartSpec {
  id: string;
  type: string;
  title: string;
  data: Record<string, unknown>;
}

export interface AnalysisBundle {
  bundle_version: string;
  built_at: string;
  match: MatchSummary;
  data_cutoff_at: string | null;
  model_version: string | null;
  prediction_public: {
    top_outcome: "home" | "draw" | "away";
    top_probability: number;
  } | null;
  /** 匿名/Free 请求时后端置为 null,受限字段不下发 */
  prediction_member: Record<string, unknown> | null;
  evidence: EvidenceItem[];
  counter_evidence: EvidenceItem[];
  uncertainty: UncertaintyItem[];
  /** 仅 odds:history_full;匿名为空数组 */
  odds_timeline: unknown[];
  /** 仅 report:deep;匿名为空数组 */
  cooccurring_events: unknown[];
  cooccurrence_count: number;
  chart_specs: AnalysisChartSpec[];
  script_sections: ScriptSection[];
  source_notes: SourceNote[];
  bundle_hash: string;
}

/* ── GET /api/v1/matches/{id}/odds ─────────────────────────── */

/** 1x2: home/draw/away;ah: home/line/away;ou: over/line/under */
export type OddsGroup = Record<string, number>;

export interface OddsSnapshot {
  market: string; // 1x2 / ah / ou
  company_id: string;
  company_name: string;
  market_phase: string; // pre_match / in_play / unknown
  payload: { initial: OddsGroup | null; latest: OddsGroup | null };
  source_updated_at: string | null;
  observed_at: string;
}

export interface OddsResponse {
  match_id: number;
  available: boolean;
  reason?: string;
  tier?: "full" | "delayed_summary";
  home_away_inverted?: boolean;
  snapshots?: OddsSnapshot[];
}

/* ── GET /api/v1/matches/{id}/cooccurrence ─────────────────── */

export interface CooccurrenceItem {
  window_seconds: number;
  delta_seconds: number;
  computed_at: string;
  market: string;
  company_id: string;
  field: string;
  prev_value: number | string | null;
  new_value: number | string | null;
  odds_moved_at: string;
  event_type: string; // lineup_change / sideline_change
  detail_json: string | null;
  event_moved_at: string;
}

export interface CooccurrenceResponse {
  match_id: number;
  count: number;
  /** 匿名/非 report:deep 为 null(只给计数) */
  items: CooccurrenceItem[] | null;
  note?: string;
}

/* ── GET /api/v1/model/metrics ─────────────────────────────── */

export interface ModelVersionInfo {
  id: string;
  algorithm: string;
  description: string;
  trained_at: string | null;
  train_range: string | null;
  created_at: string;
  params: Record<string, unknown>;
  dev_metrics: Record<string, unknown>;
}

export interface OfficialEvaluation {
  sample_size: number;
  accuracy: number | null;
  brier: number | null;
  log_loss: number | null;
  rps: number | null;
  calibration: unknown[];
  evaluated_at: string | null;
}

export interface ModelMetricsResponse {
  model_versions: ModelVersionInfo[];
  official_evaluation: OfficialEvaluation | null;
  official_evaluation_note: string | null;
  market_baseline: { status: string; note: string };
}

/* ── GET /api/v1/products ──────────────────────────────────── */

export interface PlanInfo {
  id: string;
  name_zh: string;
  description: string;
  rank: number;
  entitlements: string[];
}

export interface ProductInfo {
  id: string;
  plan_id: string;
  name_zh: string;
  description: string;
  duration_days: number;
  price_cents: number;
  currency: string;
  sort_order: number;
}

export interface ProductsResponse {
  plans: PlanInfo[];
  products: ProductInfo[];
}
