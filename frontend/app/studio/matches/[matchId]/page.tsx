"use client";

/**
 * /studio/matches/[matchId]:Creator Studio 编辑器(analyst/admin 专用)。
 *
 * - 载入草稿(URL ?draft=,缺省时取该场最新草稿,没有则新建 = 冻结当前数据快照);
 * - 六段式竖屏场景横向滚动预览(1080×1920 缩放),每张卡含数据截止 + 模型版本;
 * - 右侧编辑面板:标题 / 口播稿六段 / 支持证据 / 反向证据 / 不确定性 → overrides;
 * - 导出:PNG(html-to-image,1080×1920 与 1080×1350)+ TXT/JSON/SRT(服务端生成);
 * - 状态流转 draft → reviewed → published。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { toPng } from "html-to-image";
import { StudioGate } from "@/components/studio/StudioGate";
import { SceneCard } from "@/components/studio/SceneCards";
import {
  buildSubtitleCues,
  createDraft,
  downloadServerExport,
  exportFilename,
  fetchDraft,
  fetchDrafts,
  fmtUtc,
  requestExport,
  saveDraft,
  setDraftStatus,
  triggerDownload,
} from "@/components/studio/api";
import {
  SCENES,
  STATUS_ZH,
  type DraftDetail,
  type DraftOverrides,
  type DraftStatus,
  type EvidenceItem,
  type SceneId,
  type ScriptSection,
} from "@/components/studio/types";
import styles from "./editor.module.css";

const PREVIEW_SCALE = 0.24;

interface PngJob {
  scene: SceneId;
  width: 1080;
  height: 1920 | 1350;
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function ItemsEditor({
  heading,
  hint,
  items,
  onChange,
  newItemSide,
}: {
  heading: string;
  hint: string;
  items: EvidenceItem[];
  onChange: (items: EvidenceItem[]) => void;
  newItemSide: string;
}) {
  return (
    <div className={styles.editGroup}>
      <div className={styles.editGroupHead}>
        <span className={styles.editGroupTitle}>{heading}</span>
        <button
          type="button"
          className={styles.smallBtn}
          onClick={() =>
            onChange([...items, { side: newItemSide, kind: "manual", text: "" }])
          }
        >
          + 添加一条
        </button>
      </div>
      {items.length === 0 && <p className={styles.editHint}>{hint}</p>}
      {items.map((it, i) => (
        <div key={i} className={styles.itemRow}>
          <textarea
            className={styles.textarea}
            rows={2}
            value={it.text}
            onChange={(e) => {
              const next = items.slice();
              next[i] = { ...it, text: e.target.value };
              onChange(next);
            }}
          />
          <button
            type="button"
            className={styles.deleteBtn}
            aria-label={`删除第 ${i + 1} 条`}
            onClick={() => onChange(items.filter((_, j) => j !== i))}
          >
            删除
          </button>
        </div>
      ))}
    </div>
  );
}

function Editor({
  matchId,
  draftIdFromUrl,
}: {
  matchId: number;
  draftIdFromUrl: string | null;
}) {
  const router = useRouter();
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [sections, setSections] = useState<ScriptSection[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [counter, setCounter] = useState<EvidenceItem[]>([]);
  const [uncertainty, setUncertainty] = useState<EvidenceItem[]>([]);
  const [dirty, setDirty] = useState(false);

  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [statusBusy, setStatusBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const [activeScene, setActiveScene] = useState<SceneId>("hook");
  const [exportBusy, setExportBusy] = useState<string | null>(null);
  const [pngQueue, setPngQueue] = useState<PngJob[]>([]);
  const stageRef = useRef<HTMLDivElement>(null);
  const loadedIdRef = useRef<string | null>(null);
  /* 队列是否真正跑过任务:防止 setExportBusy 后、setPngQueue 前
     (requestExport 在途,队列仍为空)完成态 effect 提前触发。 */
  const pngRanRef = useRef(false);
  /* 本轮是否有任务失败:避免队列清空时用"完成"覆盖失败提示。 */
  const pngFailedRef = useRef(false);

  /* ── 载入草稿 ─────────────────────────────────────── */

  useEffect(() => {
    /* 无效 matchId 由渲染层直接判定,不进入加载流程 */
    if (!Number.isFinite(matchId)) return;
    if (draftIdFromUrl && loadedIdRef.current === draftIdFromUrl) return;
    let cancelled = false;
    setDraft(null);
    setLoadError(null);
    (async () => {
      try {
        let id = draftIdFromUrl;
        if (!id) {
          const list = await fetchDrafts();
          const mine = list.drafts
            .filter((d) => d.match_id === matchId)
            .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
          if (mine.length > 0) {
            id = mine[0].id;
          } else {
            const created = await createDraft(matchId);
            id = created.draft_id;
          }
        }
        const d = await fetchDraft(id);
        if (cancelled) return;
        if (d.match_id !== matchId) {
          setLoadError("草稿与当前比赛不匹配,请从 Studio 首页重新进入。");
          return;
        }
        loadedIdRef.current = d.id;
        setDraft(d);
        setTitle(d.overrides.title ?? d.title);
        setSections(d.overrides.script_sections ?? d.bundle.script_sections);
        setEvidence(d.overrides.evidence ?? d.bundle.evidence);
        setCounter(d.overrides.counter_evidence ?? d.bundle.counter_evidence);
        setUncertainty(d.overrides.uncertainty ?? d.bundle.uncertainty);
        setDirty(false);
        if (!draftIdFromUrl) {
          router.replace(`/studio/matches/${matchId}?draft=${d.id}`);
        }
      } catch (err) {
        if (!cancelled) setLoadError(errMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [matchId, draftIdFromUrl, router]);

  /* ── 保存 overrides ───────────────────────────────── */

  const doSave = useCallback(async (): Promise<boolean> => {
    if (!draft) return false;
    setSaving(true);
    setNotice(null);
    try {
      const overrides: DraftOverrides = {
        title,
        script_sections: sections,
        subtitle_cues: buildSubtitleCues(sections),
        evidence,
        counter_evidence: counter,
        uncertainty,
      };
      await saveDraft(draft.id, { title, overrides });
      setDraft((prev) => (prev ? { ...prev, title, overrides } : prev));
      setDirty(false);
      setSavedAt(new Date());
      return true;
    } catch (err) {
      setNotice(`保存失败:${errMessage(err)}`);
      return false;
    } finally {
      setSaving(false);
    }
  }, [draft, title, sections, evidence, counter, uncertainty]);

  /* ── 状态流转 ─────────────────────────────────────── */

  const changeStatus = async (status: DraftStatus) => {
    if (!draft) return;
    setStatusBusy(true);
    setNotice(null);
    try {
      await setDraftStatus(draft.id, status);
      setDraft((prev) => (prev ? { ...prev, status } : prev));
    } catch (err) {
      setNotice(`状态更新失败:${errMessage(err)}`);
    } finally {
      setStatusBusy(false);
    }
  };

  /* ── 服务端导出(TXT/JSON/SRT) ──────────────────── */

  const serverExport = async (kind: "txt" | "json" | "srt") => {
    if (!draft) return;
    setExportBusy(kind);
    setNotice(null);
    try {
      if (dirty) {
        const ok = await doSave();
        if (!ok) return;
      }
      const res = await requestExport(draft.id, kind);
      if (!res.download_url) throw new Error("后端未返回下载地址");
      await downloadServerExport(
        res.download_url,
        exportFilename(
          draft.match_id,
          kind,
          kind,
          res.model_version,
          res.data_cutoff_at,
        ),
      );
      setNotice(
        `${kind.toUpperCase()} 导出完成(数据截止 ${fmtUtc(res.data_cutoff_at)} · 模型版本 ${res.model_version ?? "无"})`,
      );
    } catch (err) {
      setNotice(`导出失败:${errMessage(err)}`);
    } finally {
      setExportBusy(null);
    }
  };

  /* ── PNG 导出(html-to-image,客户端 DOM→PNG) ───── */

  const pngExport = async (height: 1920 | 1350, all: boolean) => {
    if (!draft) return;
    const busyKey = `png_${height}${all ? "_all" : ""}`;
    setExportBusy(busyKey);
    setNotice(null);
    pngFailedRef.current = false;
    try {
      if (dirty) {
        const ok = await doSave();
        if (!ok) {
          setExportBusy(null);
          return;
        }
      }
      // 服务端登记导出审计(PNG 本体由前端 DOM 生成)
      await requestExport(
        draft.id,
        height === 1920 ? "png_1080x1920" : "png_1080x1350",
      );
      const scenes: SceneId[] = all
        ? SCENES.map((s) => s.id)
        : [activeScene];
      setPngQueue(scenes.map((scene) => ({ scene, width: 1080, height })));
    } catch (err) {
      setNotice(`导出失败:${errMessage(err)}`);
      setExportBusy(null);
    }
  };

  useEffect(() => {
    const job = pngQueue[0];
    if (!job || !draft) return;
    pngRanRef.current = true;
    let cancelled = false;
    (async () => {
      try {
        await document.fonts.ready;
        // 等 ECharts 完成渲染动画后再截图
        await new Promise((r) =>
          setTimeout(r, job.scene === "charts" ? 1400 : 500),
        );
        const node = stageRef.current;
        if (!node || cancelled) return;
        const dataUrl = await toPng(node, {
          width: job.width,
          height: job.height,
          pixelRatio: 1,
          backgroundColor: "#0a0806",
        });
        if (cancelled) return;
        triggerDownload(
          dataUrl,
          exportFilename(
            draft.match_id,
            `${job.scene}_${job.width}x${job.height}`,
            "png",
            draft.bundle.model_version,
            draft.bundle.data_cutoff_at,
          ),
        );
      } catch (err) {
        if (!cancelled) {
          pngFailedRef.current = true;
          setNotice(`PNG 导出失败:${errMessage(err)}`);
        }
      } finally {
        if (!cancelled) setPngQueue((q) => q.slice(1));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pngQueue, draft]);

  useEffect(() => {
    if (
      pngQueue.length === 0 &&
      exportBusy?.startsWith("png") &&
      pngRanRef.current
    ) {
      pngRanRef.current = false;
      setExportBusy(null);
      if (!pngFailedRef.current) {
        setNotice(
          "PNG 导出完成(画面内含数据截止时间与模型版本;请检查中文字体渲染)",
        );
      }
      pngFailedRef.current = false;
    }
  }, [pngQueue, exportBusy]);

  /* ── 渲染 ─────────────────────────────────────────── */

  if (!Number.isFinite(matchId)) {
    return (
      <main className={styles.page}>
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>比赛 ID 无效</div>
          <p className={styles.errorText}>请从 Studio 首页重新进入。</p>
          <Link href="/studio" className={styles.backLink}>
            返回 Studio 首页
          </Link>
        </div>
      </main>
    );
  }

  if (loadError) {
    return (
      <main className={styles.page}>
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>草稿加载失败</div>
          <p className={styles.errorText}>{loadError}</p>
          <Link href="/studio" className={styles.backLink}>
            返回 Studio 首页
          </Link>
        </div>
      </main>
    );
  }

  if (!draft) {
    return (
      <main className={styles.page}>
        <div className={styles.skeletonRow}>
          {SCENES.map((s) => (
            <div key={s.id} className={styles.skeletonCard} />
          ))}
        </div>
        <p className={styles.skeletonNote}>正在载入分析包与草稿…</p>
      </main>
    );
  }

  const bundle = draft.bundle;
  const m = bundle.match;
  const liveOverrides: DraftOverrides = {
    title,
    script_sections: sections,
    evidence,
    counter_evidence: counter,
    uncertainty,
  };
  const busy = exportBusy !== null || saving || statusBusy;
  const markDirty = () => setDirty(true);

  const updateSection = (id: string, text: string) => {
    setSections((prev) =>
      prev.map((s) => (s.id === id ? { ...s, text } : s)),
    );
    markDirty();
  };

  return (
    <main className={styles.page}>
      <div className={styles.topBar}>
        <Link href="/studio" className={styles.backLink}>
          ← Studio
        </Link>
        <h1 className={styles.pageTitle}>
          {m.home.name} vs {m.away.name}
        </h1>
        <span className={styles.statusPill} data-status={draft.status}>
          {STATUS_ZH[draft.status]}
        </span>
        <div className={styles.topActions}>
          {draft.status === "draft" && (
            <button
              type="button"
              className={styles.secondaryBtn}
              disabled={busy}
              onClick={() => changeStatus("reviewed")}
            >
              标记为已审阅
            </button>
          )}
          {draft.status === "reviewed" && (
            <>
              <button
                type="button"
                className={styles.secondaryBtn}
                disabled={busy}
                onClick={() => changeStatus("draft")}
              >
                退回草稿
              </button>
              <button
                type="button"
                className={styles.primaryBtn}
                disabled={busy}
                onClick={() => changeStatus("published")}
              >
                发布
              </button>
            </>
          )}
          {draft.status === "published" && (
            <button
              type="button"
              className={styles.secondaryBtn}
              disabled={busy}
              onClick={() => changeStatus("reviewed")}
            >
              退回已审阅
            </button>
          )}
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={busy || !dirty}
            onClick={doSave}
          >
            {saving ? "保存中…" : dirty ? "保存草稿" : "已保存"}
          </button>
        </div>
      </div>

      <div className={styles.metaLine}>
        数据截止:{fmtUtc(bundle.data_cutoff_at)} · 模型版本:
        {bundle.model_version ?? "无"} · 分析包生成于 {fmtUtc(bundle.built_at)}{" "}
        · 草稿更新于 {fmtUtc(draft.updated_at)}
        {savedAt && ` · 本次保存 ${savedAt.toLocaleTimeString("zh-CN")}`}
      </div>

      {notice && <div className={styles.notice}>{notice}</div>}

      <div className={styles.layout}>
        <div className={styles.previewCol}>
          <h2 className={styles.sectionTitle}>竖屏场景预览(1080×1920)</h2>
          <div className={styles.previewStrip}>
            {SCENES.map((sc) => (
              <div
                key={sc.id}
                role="button"
                tabIndex={0}
                aria-pressed={activeScene === sc.id}
                className={
                  activeScene === sc.id
                    ? styles.previewCardActive
                    : styles.previewCard
                }
                onClick={() => setActiveScene(sc.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setActiveScene(sc.id);
                  }
                }}
              >
                <span className={styles.previewLabel}>{sc.label}</span>
                <div
                  className={styles.previewViewport}
                  style={{
                    width: 1080 * PREVIEW_SCALE,
                    height: 1920 * PREVIEW_SCALE,
                  }}
                >
                  <div
                    className={styles.previewInner}
                    style={{ transform: `scale(${PREVIEW_SCALE})` }}
                  >
                    <SceneCard
                      bundle={bundle}
                      overrides={liveOverrides}
                      draftTitle={draft.title}
                      scene={sc.id}
                      height={1920}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className={styles.exportCard}>
            <h2 className={styles.sectionTitle}>导出</h2>
            <div className={styles.exportRow}>
              <button
                type="button"
                className={styles.exportBtn}
                disabled={busy}
                onClick={() => pngExport(1920, false)}
              >
                {exportBusy === "png_1920"
                  ? "生成中…"
                  : "PNG 当前场景 1080×1920"}
              </button>
              <button
                type="button"
                className={styles.exportBtn}
                disabled={busy}
                onClick={() => pngExport(1350, false)}
              >
                {exportBusy === "png_1350"
                  ? "生成中…"
                  : "PNG 当前场景 1080×1350"}
              </button>
              <button
                type="button"
                className={styles.exportBtn}
                disabled={busy}
                onClick={() => pngExport(1920, true)}
              >
                {exportBusy === "png_1920_all"
                  ? `生成中(剩 ${pngQueue.length} 张)…`
                  : "PNG 全部六张 1080×1920"}
              </button>
            </div>
            <div className={styles.exportRow}>
              <button
                type="button"
                className={styles.exportBtn}
                disabled={busy}
                onClick={() => serverExport("txt")}
              >
                {exportBusy === "txt" ? "生成中…" : "TXT 口播稿"}
              </button>
              <button
                type="button"
                className={styles.exportBtn}
                disabled={busy}
                onClick={() => serverExport("json")}
              >
                {exportBusy === "json" ? "生成中…" : "JSON(bundle+overrides)"}
              </button>
              <button
                type="button"
                className={styles.exportBtn}
                disabled={busy}
                onClick={() => serverExport("srt")}
              >
                {exportBusy === "srt" ? "生成中…" : "SRT 字幕"}
              </button>
            </div>
            <p className={styles.exportNote}>
              每种导出都携带数据截止时间与模型版本:PNG 在画面页脚、TXT
              在首行、JSON 在 bundle 字段、SRT 以文件名标注。导出前会自动保存
              未保存的编辑。
            </p>
          </div>
        </div>

        <aside className={styles.editCol}>
          <h2 className={styles.sectionTitle}>编辑面板</h2>

          <div className={styles.editGroup}>
            <label className={styles.editGroupTitle} htmlFor="draft-title">
              标题
            </label>
            <input
              id="draft-title"
              className={styles.input}
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                markDirty();
              }}
            />
          </div>

          <div className={styles.editGroup}>
            <span className={styles.editGroupTitle}>口播稿(六段)</span>
            {sections.map((s) => (
              <div key={s.id} className={styles.scriptRow}>
                <span className={styles.scriptLabel}>{s.title}</span>
                <textarea
                  className={styles.textarea}
                  rows={3}
                  value={s.text}
                  onChange={(e) => updateSection(s.id, e.target.value)}
                />
              </div>
            ))}
            <p className={styles.editHint}>
              修改口播稿会同步重建 SRT 字幕时间轴(按句号断句)。
            </p>
          </div>

          <ItemsEditor
            heading="支持证据"
            hint="暂无条目,可添加(请只写可核实的数据事实)。"
            items={evidence}
            onChange={(items) => {
              setEvidence(items);
              markDirty();
            }}
            newItemSide="home"
          />
          <ItemsEditor
            heading="反向证据"
            hint="暂无条目,可添加。"
            items={counter}
            onChange={(items) => {
              setCounter(items);
              markDirty();
            }}
            newItemSide="away"
          />
          <ItemsEditor
            heading="不确定性 / 风险"
            hint="暂无条目,可添加。"
            items={uncertainty}
            onChange={(items) => {
              setUncertainty(items);
              markDirty();
            }}
            newItemSide=""
          />

          <p className={styles.editHint}>
            概率、预期进球与图表来自冻结的分析包快照,不能在 Studio
            修改;需要新数据请回 Studio 首页重新创建草稿。
          </p>
        </aside>
      </div>

      {pngQueue[0] && (
        <div className={styles.exportStage} aria-hidden>
          <div ref={stageRef}>
            <SceneCard
              bundle={bundle}
              overrides={liveOverrides}
              draftTitle={draft.title}
              scene={pngQueue[0].scene}
              height={pngQueue[0].height}
            />
          </div>
        </div>
      )}
    </main>
  );
}

export default function StudioMatchPage({
  params,
  searchParams,
}: {
  params: Promise<{ matchId: string }>;
  searchParams: Promise<{ draft?: string }>;
}) {
  const { matchId } = use(params);
  const sp = use(searchParams);
  return (
    <StudioGate>
      <Editor
        matchId={Number(matchId)}
        draftIdFromUrl={sp.draft ?? null}
      />
    </StudioGate>
  );
}
