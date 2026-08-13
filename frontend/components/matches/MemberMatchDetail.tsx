"use client";

/**
 * 会员比赛详情客户端加载器(修复审计 B1)。
 *
 * 会话 cookie Path=/api/v1,Next 服务端读不到——RSC 对 /api/v1/matches/{id}
 * 的取数永远是匿名身份,非免费联赛被门禁挡下(401/403)后,旧实现把 null
 * 一律当 notFound(),导致 Pro/Premium 点开西甲/意甲/德甲/法甲详情页全是 404。
 *
 * 本组件与 MemberLeagueSection 同构:浏览器带 credentials 重试同一端点,
 * - 已开通会员 → 并行补拉 prediction/analysis/related,渲染与 SSR 完全相同的
 *   MatchDetailBody(公共 HTML 外壳不因登录态变化,宪法 §10.2);
 * - 401(未登录)→ 门禁卡片(带真实赛事信息 + 扫码登录,见 LeagueGateCard);
 *   member 基线权益覆盖全部联赛(见 platform 0009),登录用户不会撞到同一
 *   门禁,因此不再区分"已登录但无权益"的 403 分支——审计确认该分支在生产
 *   不可达,是死文案。
 * - 404 → 比赛不存在的诚实说明;
 * - 其他错误 → 可重试错误态。
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  clientFetch,
  type MatchDetailResponse,
  type MatchListResponse,
  type MatchReportResponse,
  type MatchSummary,
} from "@/lib/api-v1";
import {
  MatchDetailBody,
  type AnalysisBundle,
} from "@/components/matches/MatchDetailBody";
import { LeagueGateCard, type GateInfo } from "@/components/matches/LeagueGateCard";
import styles from "@/components/league/MemberLeagueSection.module.css";

type LoadedData = {
  detail: MatchDetailResponse;
  analysis: AnalysisBundle | null;
  report: MatchReportResponse | null;
  previousMatch: MatchSummary | null;
  nextMatch: MatchSummary | null;
};

type State =
  | { phase: "loading" }
  | { phase: "data"; data: LoadedData }
  | { phase: "gate"; info: GateInfo }
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
      const [analysis, report, related] = await Promise.all([
        clientFetch<AnalysisBundle>(`/api/v1/matches/${matchId}/analysis`).catch(
          () => null,
        ),
        clientFetch<MatchReportResponse>(`/api/v1/matches/${matchId}/report`).catch(
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
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          const d = e.details ?? {};
          setState({
            phase: "gate",
            info: {
              leagueName: typeof d.league_name === "string" ? d.league_name : undefined,
              round: typeof d.round === "string" ? d.round : null,
              homeTeam: typeof d.home_team === "string" ? d.home_team : undefined,
              awayTeam: typeof d.away_team === "string" ? d.away_team : undefined,
              kickoffAtUtc:
                typeof d.kickoff_at_utc === "string" ? d.kickoff_at_utc : null,
            },
          });
        } else if (e instanceof ApiError && e.status === 404) {
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

  if (state.phase === "gate") {
    return <LeagueGateCard matchId={matchId} info={state.info} />;
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
      returnTo={returnTo}
      returnLabel={returnLabel}
      previousMatch={data.previousMatch}
      nextMatch={data.nextMatch}
    />
  );
}
