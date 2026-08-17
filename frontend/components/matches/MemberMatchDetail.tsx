"use client";

/**
 * 比赛详情客户端加载器。
 *
 * 会话 cookie Path=/api/v1,Next 服务端读不到——app/matches/[matchId]/page.tsx
 * 的服务端匿名取数(serverGetOptional)在 401/403/404 时统一返回 null。
 * 2026-08-16 权限口径修正后,/api/v1/matches/{id} 对任何人恒 200,不会再
 * 返回 401/403,`detail===null` 现在只可能是比赛真的不存在(404)或服务端
 * 取数失败——此前"401/403 → LeagueGateCard 登录门禁卡片"这条分支已是死
 * 代码(此前的扫码登录门禁卡片、真实赛事信息透传等逻辑随之一并移除),
 * 简化为浏览器重新请求一次以三分:
 * - 拿到数据 → 并行补拉 analysis/report/preview/related,渲染与 SSR 完全
 *   相同的 MatchDetailBody(公共 HTML 外壳不因登录态变化,宪法 §10.2);
 * - 404 → 比赛不存在的诚实说明;
 * - 其他错误(含理论上不应再出现的 401/403)→ 统一归入可重试错误态。
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  clientFetch,
  type MatchDetailResponse,
  type MatchListResponse,
  type MatchPreviewResponse,
  type MatchReportResponse,
  type MatchSummary,
} from "@/lib/api-v1";
import {
  MatchDetailBody,
  type AnalysisBundle,
} from "@/components/matches/MatchDetailBody";
import styles from "@/components/league/MemberLeagueSection.module.css";

type LoadedData = {
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
  report: MatchReportResponse | null;
  preview: MatchPreviewResponse | null;
  previousMatch: MatchSummary | null;
  nextMatch: MatchSummary | null;
};

type State =
  | { phase: "loading" }
  | { phase: "data"; data: LoadedData }
  | { phase: "notfound" }
  | { phase: "error" };

export function MemberMatchDetail({
  matchId,
  returnTo,
  returnLabel,
}: {
  matchId: number;
  returnTo: string;
  returnLabel: string;
}) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 详情是硬依赖;prediction/analysis/related 缺失时优雅降级(与 RSC 路径一致)
      const detail = await clientFetch<MatchDetailResponse>(
        `/api/v1/matches/${matchId}`,
      );
      const [analysis, report, preview, related] = await Promise.all([
        clientFetch<AnalysisBundle>(`/api/v1/matches/${matchId}/analysis`).catch(
          () => null,
        ),
        clientFetch<MatchReportResponse>(`/api/v1/matches/${matchId}/report`).catch(
          () => null,
        ),
        clientFetch<MatchPreviewResponse>(`/api/v1/matches/${matchId}/preview`).catch(
          () => null,
        ),
        clientFetch<MatchListResponse>(
          `/api/v1/matches?league_id=${detail.match.league_id}&status=upcoming&window=7d&limit=200`,
        ).catch(() => null),
      ]);
      const relatedMatches = related?.matches ?? [];
      const idx = relatedMatches.findIndex((item) => item.match_id === matchId);
      return {
        detail,
        analysis,
        report,
        preview,
        previousMatch: idx > 0 ? relatedMatches[idx - 1] : null,
        nextMatch:
          idx >= 0 && idx + 1 < relatedMatches.length
            ? relatedMatches[idx + 1]
            : null,
      };
    })()
      .then((data) => {
        if (cancelled) return;
        setState({ phase: "data", data });
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setState({ phase: "notfound" });
        } else {
          setState({ phase: "error" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, attempt]);

  const retry = useCallback(() => {
    setState({ phase: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  if (state.phase === "loading") {
    return (
      <div className={styles.skeleton} aria-label="比赛详情加载中">
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
        <span className={styles.skelLine} />
      </div>
    );
  }

  if (state.phase === "notfound") {
    return (
      <div className={styles.gateBox}>
        <h2 className={styles.gateTitle}>比赛不存在</h2>
        <p className={styles.gateText}>该场比赛不在收录范围内,请从比赛列表进入。</p>
        <div className={styles.gateActions}>
          <Link className={styles.btnPrimary} href="/matches">
            返回比赛列表
          </Link>
        </div>
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div className={styles.gateBox}>
        <h2 className={styles.gateTitle}>数据暂时无法加载</h2>
        <p className={styles.gateText}>数据服务暂时不可用,请稍后再试。</p>
        <div className={styles.gateActions}>
          <button type="button" className={styles.btnSecondary} onClick={retry}>
            重试
          </button>
        </div>
      </div>
    );
  }

  const { data } = state;
  return (
    <MatchDetailBody
      idNum={matchId}
      detail={data.detail}
      analysis={data.analysis}
      report={data.report}
      preview={data.preview}
      returnTo={returnTo}
      returnLabel={returnLabel}
      previousMatch={data.previousMatch}
      nextMatch={data.nextMatch}
    />
  );
}
