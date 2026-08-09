/**
 * Studio 专用 API 封装:全部经 lib/api-v1 的 clientFetch
 * (带 credentials + CSRF;会话 cookie Path=/api/v1,只能浏览器端调)。
 */

import { API_BASE, clientFetch, type GetJson, type PostJson } from "@/lib/api-v1";
import { formatBeijingDateTime } from "@/components/matches/zh";
import type {
  DraftDetail,
  DraftOverrides,
  DraftStatus,
  DraftSummary,
  ExportKind,
  ExportResult,
  StudioProfileId,
  ScriptSection,
  SubtitleCue,
} from "./types";

/** 列表响应 = 生成类型,drafts 项收窄为 DraftSummary(status 联合)。 */
type DraftsResp = Omit<GetJson<"/api/v1/studio/drafts">, "drafts"> & {
  drafts: DraftSummary[];
};

export const fetchDrafts = () => clientFetch<DraftsResp>("/api/v1/studio/drafts");

export const createDraft = (matchId: number, title = "") =>
  clientFetch<PostJson<"/api/v1/studio/drafts">>("/api/v1/studio/drafts", {
    method: "POST",
    body: { match_id: matchId, title },
  });

export const fetchDraft = (draftId: string) =>
  clientFetch<DraftDetail>(`/api/v1/studio/drafts/${draftId}`);

export const saveDraft = (
  draftId: string,
  body: { title?: string | null; overrides?: DraftOverrides },
) =>
  clientFetch<PostJson<"/api/v1/studio/drafts/{draft_id}">>(
    `/api/v1/studio/drafts/${draftId}`,
    { method: "POST", body },
  );

export const setDraftStatus = (draftId: string, status: DraftStatus) =>
  clientFetch<PostJson<"/api/v1/studio/drafts/{draft_id}/status">>(
    `/api/v1/studio/drafts/${draftId}/status`,
    { method: "POST", body: { status } },
  );

export const requestExport = (
  draftId: string,
  kind: ExportKind,
  profile: StudioProfileId,
) =>
  clientFetch<ExportResult>(`/api/v1/studio/drafts/${draftId}/export`, {
    method: "POST",
    body: { kind, profile },
  });

/** 服务端生成的导出文件(TXT/JSON/SRT):带 cookie 拉 blob 后客户端下载。 */
export async function downloadServerExport(
  downloadUrl: string,
  filename: string,
): Promise<void> {
  const res = await fetch(`${API_BASE}${downloadUrl}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`导出下载失败(HTTP ${res.status})`);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  try {
    triggerDownload(href, filename);
  } finally {
    setTimeout(() => URL.revokeObjectURL(href), 10_000);
  }
}

export function triggerDownload(href: string, filename: string): void {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ── 展示与文件名工具 ─────────────────────────────────── */

/** UTC ISO → 北京时间展示(函数名沿用 fmtUtc 未改,行为已修正——调用方
 * SafeSceneCards.tsx 一直明确标注"北京时间 {fmtUtc(...)}",但此前实现用
 * 的是浏览器本地时区,标签与实际展示的时区不一致;导出内容会烧录进图片/
 * 字幕面向公开发布,标错时区是真实缺陷,不是风格选择)。
 * 纯日期(比赛日,没有精确 kickoff)如实标注,不编造具体时刻。 */
export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "未知";
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return `${iso}(比赛日,UTC)`;
  return formatBeijingDateTime(iso) ?? iso;
}

/** 导出文件名统一携带 cutoff + 模型版本(SRT 内容本体不便携带,靠文件名与界面提示)。 */
export function exportFilename(
  matchId: number,
  part: string,
  ext: string,
  modelVersion: string | null | undefined,
  dataCutoffAt: string | null | undefined,
  profileId = "internal-full-v1",
  sourceHash?: string | null,
): string {
  const clean = (s: string) => s.replace(/[^0-9A-Za-z_.-]+/g, "-");
  const model = modelVersion ? clean(modelVersion) : "no-model";
  const cutoff = dataCutoffAt ? clean(dataCutoffAt.slice(0, 10)) : "no-cutoff";
  const profile = clean(profileId);
  const source = sourceHash ? `_${clean(sourceHash.slice(0, 12))}` : "";
  return `allwin_${profile}_${matchId}_${part}_cutoff-${cutoff}_${model}${source}.${ext}`;
}

/**
 * 由(编辑后的)口播稿重建字幕 cue,算法与 backend/studio/bundle.py 一致:
 * 按句号断句,时长 = clamp(字数 × 0.18s, 2s, 6s)。
 */
export function buildSubtitleCues(sections: ScriptSection[]): SubtitleCue[] {
  const cues: SubtitleCue[] = [];
  let t = 0;
  for (const sec of sections) {
    const sentences = sec.text
      .replace(/。/g, "。|")
      .split("|")
      .map((s) => s.trim())
      .filter(Boolean);
    for (const sentence of sentences) {
      const dur = Math.max(2.0, Math.min(6.0, sentence.length * 0.18));
      cues.push({
        start: Math.round(t * 10) / 10,
        end: Math.round((t + dur) * 10) / 10,
        text: sentence,
      });
      t += dur;
    }
  }
  return cues;
}
