"use client";

/**
 * /studio:Creator Studio 首页(analyst/admin 专用)。
 * 左:近期未开赛比赛(GET /api/v1/matches?status=upcoming),点击创建/打开草稿;
 * 右:我的草稿列表(状态/更新时间/进入编辑)。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  clientFetch,
  type MatchListResponse,
  type MatchSummary,
} from "@/lib/api-v1";
import { StudioGate } from "@/components/studio/StudioGate";
import { createDraft, fetchDrafts, fmtUtc } from "@/components/studio/api";
import { STATUS_ZH, type DraftSummary } from "@/components/studio/types";
import styles from "./studio.module.css";

type Loadable<T> =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; data: T };

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function MatchList({
  matches,
  drafts,
  onCreate,
  creatingId,
}: {
  matches: MatchSummary[];
  drafts: DraftSummary[];
  onCreate: (m: MatchSummary) => void;
  creatingId: number | null;
}) {
  const router = useRouter();
  if (matches.length === 0) {
    return (
      <p className={styles.empty}>
        暂无未开赛的比赛数据(以当前权限可见联赛为准)。
      </p>
    );
  }
  return (
    <ul className={styles.matchList}>
      {matches.map((m) => {
        const existing = drafts.find((d) => d.match_id === m.match_id);
        return (
          <li key={m.match_id} className={styles.matchRow}>
            <div className={styles.matchInfo}>
              <span className={styles.matchTeams}>
                {m.home.name} <span className={styles.vs}>vs</span>{" "}
                {m.away.name}
              </span>
              <span className={styles.matchMeta}>
                {m.season}
                {m.round ? ` · 第 ${m.round} 轮` : ""} · 比赛日 {m.date_utc}
                (UTC)
              </span>
            </div>
            {existing ? (
              <button
                type="button"
                className={styles.openBtn}
                onClick={() =>
                  router.push(
                    `/studio/matches/${m.match_id}?draft=${existing.id}`,
                  )
                }
              >
                打开草稿
              </button>
            ) : (
              <button
                type="button"
                className={styles.createBtn}
                disabled={creatingId !== null}
                onClick={() => onCreate(m)}
              >
                {creatingId === m.match_id ? "创建中…" : "创建草稿"}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function DraftList({ drafts }: { drafts: DraftSummary[] }) {
  if (drafts.length === 0) {
    return <p className={styles.empty}>还没有草稿,从左侧选择一场比赛开始。</p>;
  }
  return (
    <ul className={styles.draftList}>
      {drafts.map((d) => (
        <li key={d.id} className={styles.draftRow}>
          <div className={styles.draftInfo}>
            <span className={styles.draftTitle}>{d.title}</span>
            <span className={styles.draftMeta}>
              <span className={styles.statusPill} data-status={d.status}>
                {STATUS_ZH[d.status]}
              </span>
              更新于 {fmtUtc(d.updated_at)}
            </span>
          </div>
          <Link
            href={`/studio/matches/${d.match_id}?draft=${d.id}`}
            className={styles.openBtn}
          >
            进入编辑
          </Link>
        </li>
      ))}
    </ul>
  );
}

function StudioHome() {
  const router = useRouter();
  const [matches, setMatches] = useState<Loadable<MatchSummary[]>>({
    phase: "loading",
  });
  const [drafts, setDrafts] = useState<Loadable<DraftSummary[]>>({
    phase: "loading",
  });
  const [creatingId, setCreatingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchAll = useCallback(() => {
    clientFetch<MatchListResponse>("/api/v1/matches?status=upcoming&limit=50")
      .then((res) => setMatches({ phase: "ready", data: res.matches }))
      .catch((err) =>
        setMatches({ phase: "error", message: errMessage(err) }),
      );
    fetchDrafts()
      .then((res) => setDrafts({ phase: "ready", data: res.drafts }))
      .catch((err) => setDrafts({ phase: "error", message: errMessage(err) }));
  }, []);

  const reload = () => {
    setMatches({ phase: "loading" });
    setDrafts({ phase: "loading" });
    fetchAll();
  };

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleCreate = async (m: MatchSummary) => {
    setActionError(null);
    setCreatingId(m.match_id);
    try {
      const res = await createDraft(m.match_id);
      router.push(`/studio/matches/${m.match_id}?draft=${res.draft_id}`);
    } catch (err) {
      setActionError(`创建草稿失败:${errMessage(err)}`);
      setCreatingId(null);
    }
  };

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Creator Studio</h1>
        <span className={styles.headerNote}>
          同一份分析数据驱动网页、竖屏图卡与口播稿
        </span>
      </div>
      {actionError && <div className={styles.actionError}>{actionError}</div>}
      <div className={styles.columns}>
        <section className={styles.col}>
          <h2 className={styles.colTitle}>近期比赛</h2>
          <div className={styles.card}>
            {matches.phase === "loading" && (
              <div className={styles.skeleton}>加载比赛列表…</div>
            )}
            {matches.phase === "error" && (
              <div className={styles.errorBox}>
                <div className={styles.errorTitle}>比赛列表加载失败</div>
                <p className={styles.errorText}>{matches.message}</p>
                <button
                  type="button"
                  className={styles.retryBtn}
                  onClick={reload}
                >
                  重试
                </button>
              </div>
            )}
            {matches.phase === "ready" && (
              <MatchList
                matches={matches.data}
                drafts={drafts.phase === "ready" ? drafts.data : []}
                onCreate={handleCreate}
                creatingId={creatingId}
              />
            )}
          </div>
        </section>
        <section className={styles.col}>
          <h2 className={styles.colTitle}>我的草稿</h2>
          <div className={styles.card}>
            {drafts.phase === "loading" && (
              <div className={styles.skeleton}>加载草稿列表…</div>
            )}
            {drafts.phase === "error" && (
              <div className={styles.errorBox}>
                <div className={styles.errorTitle}>草稿列表加载失败</div>
                <p className={styles.errorText}>{drafts.message}</p>
                <button
                  type="button"
                  className={styles.retryBtn}
                  onClick={reload}
                >
                  重试
                </button>
              </div>
            )}
            {drafts.phase === "ready" && <DraftList drafts={drafts.data} />}
          </div>
        </section>
      </div>
    </main>
  );
}

export default function StudioPage() {
  return (
    <StudioGate>
      <StudioHome />
    </StudioGate>
  );
}
