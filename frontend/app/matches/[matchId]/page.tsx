import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
  serverGetOptional,
  type MatchDetailResponse,
  type MatchListResponse,
  type MatchReportResponse,
} from "@/lib/api-v1";
import {
  MatchDetailBody,
  type AnalysisBundle,
} from "@/components/matches/MatchDetailBody";
import { MemberMatchDetail } from "@/components/matches/MemberMatchDetail";
import { LEAGUE_ZH } from "@/components/matches/zh";
import { returnLabelFor, sanitizeReturnTo } from "@/lib/match-links";
import styles from "./match-detail.module.css";

/**
 * 比赛详情页(宪法 §11.1 固定顺序,主体见 components/matches/MatchDetailBody):
 * 1 头部 → 2 概率卡 → 3 证据/反向证据 → 4 可视化 → 5 赔率时间轴 → 6 同期事件 → 7 模型与登记信息。
 *
 * 免费/付费边界:本 server component 只请求匿名投影(受限字段物理不存在)。
 * 匿名取数被联赛门禁挡下(401/403 → null)时**不再 notFound()**——那会让
 * Pro/Premium 用户对整个付费联赛拿到 404(审计 B1)。改为渲染
 * MemberMatchDetail:浏览器带会话 cookie 重试,已开通会员直接看到与 SSR
 * 完全相同的页面主体,未开通看到升级引导。公共 HTML 外壳对所有身份一致(§10.2)。
 */

export async function generateMetadata({
  params,
}: {
  params: Promise<{ matchId: string }>;
}): Promise<Metadata> {
  const { matchId } = await params;
  const detail = await serverGetOptional<MatchDetailResponse>(
    `/api/v1/matches/${matchId}`,
    { revalidate: 300 },
  ).catch(() => null);
  if (!detail) return { title: "比赛详情 — 欧赢 ALLWIN" };
  const m = detail.match;
  return {
    title: `${m.home.name} vs ${m.away.name} — 欧赢 ALLWIN`,
    description: `${m.season} ${LEAGUE_ZH[m.league_id] ?? ""} 赛前分析:数据、模型概率与不确定性`,
  };
}

export default async function MatchDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ matchId: string }>;
  searchParams: Promise<{ from?: string }>;
}) {
  const { matchId } = await params;
  const { from } = await searchParams;
  const idNum = Number(matchId);
  if (!Number.isInteger(idNum) || idNum <= 0) notFound();

  const returnTo = sanitizeReturnTo(from);
  const returnLabel = returnLabelFor(returnTo);

  let detail: MatchDetailResponse | null = null;
  let loadError = false;
  try {
    detail = await serverGetOptional<MatchDetailResponse>(`/api/v1/matches/${idNum}`, {
      revalidate: 120,
    });
  } catch {
    loadError = true;
  }

  if (loadError) {
    return (
      <main className={styles.page}>
        <div className={styles.errorBox}>
          数据暂时无法加载,请稍后重试(serving API 未响应)。
        </div>
      </main>
    );
  }

  if (!detail) {
    // 匿名视角的 401/403/404 在服务端不可区分(serverGetOptional 一律 null)。
    // 交给客户端带 cookie 重试后三分:会员数据 / 升级引导 / 真实不存在。
    return (
      <main className={styles.page}>
        <MemberMatchDetail
          matchId={idNum}
          returnTo={returnTo}
          returnLabel={returnLabel}
        />
      </main>
    );
  }

  const m = detail.match;
  const [analysis, report, related] = await Promise.all([
    serverGetOptional<AnalysisBundle>(`/api/v1/matches/${idNum}/analysis`).catch(() => null),
    serverGetOptional<MatchReportResponse>(`/api/v1/matches/${idNum}/report`, {
      revalidate: 300, // 完赛事实不再变化,可安心 ISR;未完赛响应为 available=false
    }).catch(() => null),
    serverGetOptional<MatchListResponse>(
      `/api/v1/matches?league_id=${m.league_id}&status=upcoming&window=7d&limit=200`,
      { revalidate: 60 },
    ).catch(() => null),
  ]);

  const relatedMatches = related?.matches ?? [];
  const relatedIndex = relatedMatches.findIndex((item) => item.match_id === idNum);
  const previousMatch = relatedIndex > 0 ? relatedMatches[relatedIndex - 1] : null;
  const nextMatch =
    relatedIndex >= 0 && relatedIndex + 1 < relatedMatches.length
      ? relatedMatches[relatedIndex + 1]
      : null;

  return (
    <MatchDetailBody
      idNum={idNum}
      detail={detail}
      analysis={analysis}
      report={report}
      returnTo={returnTo}
      returnLabel={returnLabel}
      previousMatch={previousMatch}
      nextMatch={nextMatch}
    />
  );
}
