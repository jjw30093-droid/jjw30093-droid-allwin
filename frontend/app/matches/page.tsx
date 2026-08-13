import type { Metadata } from "next";
import Link from "next/link";
import {
  serverGet,
  type LeagueInfo,
  type MatchListResponse,
} from "@/lib/api-v1";
import { MatchListLive } from "@/components/matches/MatchListLive";
import {
  buildMatchesApiQuery,
  isWindowAutoWidenEligible,
  type MatchFilters as Filters,
} from "@/lib/match-filters";
import styles from "./matches.module.css";

export const metadata: Metadata = {
  title: "比赛列表 — 欧赢 ALLWIN",
  description:
    "按日期、联赛、状态浏览比赛:中文队名、比分与 Bet365 赔率折算的胜平负概率。",
};

const PAGE_SIZE = 20;

export default async function MatchesPage({
  searchParams,
}: {
  searchParams: Promise<{
    date?: string;
    league?: string;
    season?: string;
    status?: string;
    window?: string;
    content?: string;
    q?: string;
    page?: string;
  }>;
}) {
  const sp = await searchParams;
  const date = /^\d{4}-\d{2}-\d{2}$/.test(sp.date ?? "") ? sp.date : undefined;
  const status =
    sp.status === "finished" || sp.status === "all" ? sp.status : "upcoming";
  const window =
    sp.window === "today" ||
    sp.window === "tomorrow" ||
    sp.window === "3d" ||
    sp.window === "all"
      ? sp.window
      : "7d";
  const content =
    sp.content === "analysis" || sp.content === "odds" ? sp.content : undefined;
  const q = (sp.q ?? "").trim().slice(0, 80) || undefined;
  const league = /^\d+$/.test(sp.league ?? "") ? Number(sp.league) : undefined;
  // 与后端 /api/v1/matches 的 season 校验同口径:"2024/2025" 或自然年 "2026"
  const season = /^\d{4}(\/\d{4})?$/.test(sp.season ?? "") ? sp.season : undefined;
  const page = Math.max(1, parseInt(sp.page ?? "1", 10) || 1);
  const filters: Filters = { date, league, season, status, window, content, q, page };
  // sp.window == null(用户没在 URL 里显式带 window)才允许 0 场时自动放宽;
  // 浏览器端会员刷新(MatchListLive)复用同一判据,放宽时机不能和匿名首屏错开。
  const autoWidenEligible = isWindowAutoWidenEligible(filters, sp.window != null);

  let leagues: LeagueInfo[];
  let data: MatchListResponse;
  let finalQs = buildMatchesApiQuery(filters, { limit: PAGE_SIZE });
  // D1:默认视图(status=upcoming + 隐式 window=7d)在赛季间歇期会是 0 场
  // (如 8 月上旬:五大联赛下赛季尚未开打)。用户没显式选时间窗时自动放宽到
  // 全部未来赛程并如实提示,不让首屏对着一片空白。
  let windowWidened = false;
  try {
    [leagues, data] = await Promise.all([
      serverGet<LeagueInfo[]>("/api/v1/leagues"),
      serverGet<MatchListResponse>(`/api/v1/matches?${finalQs}`, {
        revalidate: 60,
      }),
    ]);
    if (data.total === 0 && autoWidenEligible) {
      finalQs = buildMatchesApiQuery(filters, { limit: PAGE_SIZE, windowOverride: "all" });
      data = await serverGet<MatchListResponse>(`/api/v1/matches?${finalQs}`, {
        revalidate: 60,
      });
      windowWidened = true;
    }
  } catch {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>比赛</h1>
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>
            数据服务暂时不可用，请稍后刷新重试。
          </p>
        </div>
      </main>
    );
  }

  // 选中联赛 → 只列该联赛赛季;否则列并集(降序,最新赛季在前)。
  // available_seasons 与身份无关(不随登录态变化),留在 SSR 计算即可——
  // 只有 accessible/data_status 这类随身份变化的字段才需要客户端刷新。
  const selectedLeagueForSeasons = leagues.find((l) => l.league_id === league);
  const seasonOptions = Array.from(
    new Set(
      (selectedLeagueForSeasons ? [selectedLeagueForSeasons] : leagues).flatMap(
        (l) => l.available_seasons ?? [],
      ),
    ),
  ).sort((a, b) => b.localeCompare(a));

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>比赛</h1>
      </div>
      <Link href="/leagues" className={styles.leagueDirectoryLink}>
        浏览联赛排名与球队数据 →
      </Link>

      {/* 筛选栏、比赛行、翻页都在 MatchListLive 里:服务端渲染的是匿名口径
          (SSR 读不到会话 cookie),挂载后浏览器带 cookie 刷新一次,已登录且
          持有小联赛权益的账号会看到真实的完整列表,而不是永远停在匿名视图。
          key 用最终 query 串——筛选变化即整个重新挂载,拿到对应的新 initial*。 */}
      <MatchListLive
        key={finalQs}
        filters={filters}
        pageSize={PAGE_SIZE}
        autoWidenEligible={autoWidenEligible}
        seasonOptions={seasonOptions}
        initialLeagues={leagues}
        initialMatches={data.matches}
        initialTotal={data.total}
        initialWindowWidened={windowWidened}
      />

      <p className={styles.footNote}>
        默认展示未来七天未开赛比赛，并按精确开球时间排序。胜平负概率条由
        Bet365 赔率折算得到，标注了采集时间；无赔率的比赛不展示概率条。
      </p>
    </main>
  );
}
