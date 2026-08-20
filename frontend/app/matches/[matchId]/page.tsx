import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
  serverGetOptional,
  type MatchDetailResponse,
  type MatchListResponse,
  type MatchPreviewResponse,
  type MatchReportResponse,
} from "@/lib/api-v1";
import {
  MatchDetailBody,
  type AnalysisBundle,
} from "@/components/matches/MatchDetailBody";
import { MemberMatchDetail } from "@/components/matches/MemberMatchDetail";
import { LEAGUE_ZH } from "@/components/matches/zh";
import { relatedMatchesQuery, returnLabelFor, sanitizeReturnTo } from "@/lib/match-links";
import styles from "./match-detail.module.css";

/**
 * 比赛详情页(宪法 §11.1 固定顺序,主体见 components/matches/MatchDetailBody):
 * 1 头部 → 2 概率卡 → 3 证据/反向证据 → 4 可视化 → 5 赔率时间轴 → 6 同期事件 → 7 模型与登记信息。
 *
 * 2026-08-16 权限口径修正:本站比赛内容对任何人(含匿名)恒完整,不再有
 * entitlement 分层投影。本 server component 请求 serverGetOptional,拿不到
 * 数据(比赛真的不存在,或服务端取数失败)时**不 notFound()**,改为渲染
 * MemberMatchDetail:浏览器重新请求一次,三分"拿到数据/比赛不存在/网络
 * 错误"。公共 HTML 外壳对所有身份一致(§10.2)。
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
  if (!detail) return { title: "比赛详情" };
  const m = detail.match;
  return {
    title: `${m.home.name} vs ${m.away.name}`,
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
  const [analysis, report, preview, related] = await Promise.all([
    // revalidate: 120(2026-08-19 性能修复)——此前没有 revalidate,落到
    // cache:"no-store",每次都真回源(占该端点耗时的大头,详见
    // backend/queries/teams.py::team_display_for 的修复说明),而它只喂
    // 折叠起来的「数据来源与说明」区块,与 detail/preview 同档刷新完全够用。
    serverGetOptional<AnalysisBundle>(`/api/v1/matches/${idNum}/analysis`, {
      revalidate: 120,
    }).catch(() => null),
    serverGetOptional<MatchReportResponse>(`/api/v1/matches/${idNum}/report`, {
      revalidate: 300, // 完赛事实不再变化,可安心 ISR;未完赛响应为 available=false
    }).catch(() => null),
    serverGetOptional<MatchPreviewResponse>(`/api/v1/matches/${idNum}/preview`, {
      revalidate: 120, // 两队历史聚合随赛程推进变化,与 detail 同档刷新
    }).catch(() => null),
    // 只用于算"上一场/下一场"两个 match_id(见下方 relatedIndex),query 串
    // 收口到 lib/match-links.ts::relatedMatchesQuery——与 MemberMatchDetail.tsx
    // 的客户端兜底路径共用同一个函数,不允许出现第二套拼接逻辑。
    serverGetOptional<MatchListResponse>(
      `/api/v1/matches?${relatedMatchesQuery(m.league_id)}`,
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
      preview={preview}
      returnTo={returnTo}
      returnLabel={returnLabel}
      previousMatch={previousMatch}
      nextMatch={nextMatch}
    />
  );
}
