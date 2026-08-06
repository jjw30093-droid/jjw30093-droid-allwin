import type { Metadata } from "next";
import Link from "next/link";
import {
  serverGet,
  serverGetOptional,
  type LeagueInfo,
  type MatchListResponse,
  type PredictionResponse,
} from "@/lib/api-v1";
import { MatchRow, type FreeTip } from "@/components/matches/MatchRow";
import {
  buildMatchesHref,
  type MatchFilters as Filters,
} from "@/lib/match-filters";
import styles from "./matches.module.css";

export const metadata: Metadata = {
  title: "比赛列表 — 欧赢 ALLWIN",
  description:
    "按日期、联赛、状态浏览比赛:中文队名、比分与免费层模型最高一项概率。",
};

const PAGE_SIZE = 20;

function freeTipOf(resp: PredictionResponse | null): FreeTip | null {
  if (!resp?.available || !resp.prediction) return null;
  const p = resp.prediction;
  if (p.tier === "free") {
    return {
      top_outcome: p.top_outcome,
      top_probability: p.top_probability,
      probability_source: p.meta.probability_source,
    };
  }
  return null;
}

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

  const qs = new URLSearchParams();
  if (date) qs.set("date", date);
  if (league != null) qs.set("league_id", String(league));
  if (season) qs.set("season", season);
  if (status !== "all") qs.set("status", status);
  qs.set("window", window);
  if (content) qs.set("content", content);
  if (q) qs.set("q", q);
  qs.set("limit", String(PAGE_SIZE));
  qs.set("offset", String((page - 1) * PAGE_SIZE));

  let leagues: LeagueInfo[];
  let data: MatchListResponse;
  // D1:默认视图(status=upcoming + 隐式 window=7d)在赛季间歇期会是 0 场
  // (如 8 月上旬:五大联赛下赛季尚未开打)。用户没显式选时间窗时自动放宽到
  // 全部未来赛程并如实提示,不让首屏对着一片空白。
  let windowWidened = false;
  try {
    [leagues, data] = await Promise.all([
      serverGet<LeagueInfo[]>("/api/v1/leagues"),
      serverGet<MatchListResponse>(`/api/v1/matches?${qs.toString()}`, {
        revalidate: 60,
      }),
    ]);
    if (
      data.total === 0 &&
      status === "upcoming" &&
      sp.window == null &&
      !date &&
      !season &&
      !q
    ) {
      qs.set("window", "all");
      data = await serverGet<MatchListResponse>(`/api/v1/matches?${qs.toString()}`, {
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

  // 免费层概率:只为当前页的未开赛比赛拉匿名预测(free DTO)
  const tips = await Promise.all(
    data.matches.map((m) =>
      m.status === "Finish"
        ? Promise.resolve(null)
        : serverGetOptional<PredictionResponse>(
            `/api/v1/matches/${m.match_id}/prediction`,
          ).catch(() => null),
    ),
  );

  const selectedLeague = leagues.find((l) => l.league_id === league);
  const lockedLeagueSelected = selectedLeague != null && !selectedLeague.accessible;
  // 选中联赛 → 只列该联赛赛季;否则列并集(降序,最新赛季在前)
  const seasonOptions = Array.from(
    new Set(
      (selectedLeague ? [selectedLeague] : leagues).flatMap(
        (l) => l.available_seasons ?? [],
      ),
    ),
  ).sort((a, b) => b.localeCompare(a));
  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>比赛</h1>
        <span className={styles.countChip}>
          当前筛选共 <span className="num">{data.total}</span> 场
        </span>
      </div>
      {windowWidened && (
        <p className={styles.widenNote}>
          未来 7 天暂无比赛,已自动展示全部未来赛程。
        </p>
      )}
      <Link href="/leagues" className={styles.leagueDirectoryLink}>
        浏览联赛排名与球队数据 →
      </Link>

      {/* 筛选:纯 GET 表单 + 链接,无 JS 也可用 */}
      <div className={styles.filters}>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>状态</span>
          {(
            [
              [undefined, "全部"],
              ["upcoming", "未开赛"],
              ["finished", "已完赛"],
            ] as const
          ).map(([value, label]) => (
            <Link
              key={label}
              href={buildMatchesHref(filters, {
                status: value ?? "all",
                window:
                  value === "finished" || value == null ? "all" : filters.window,
                page: 1,
              })}
              className={
                status === (value ?? "all") ? styles.chipActive : styles.chip
              }
            >
              {label}
            </Link>
          ))}
        </div>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>时间</span>
          {(
            [
              ["today", "今天"],
              ["tomorrow", "明天"],
              ["3d", "未来三天"],
              ["7d", "未来七天"],
              ["all", "全部未来"],
            ] as const
          ).map(([value, label]) => (
            <Link
              key={value}
              href={buildMatchesHref(filters, {
                status: "upcoming",
                window: value,
                date: undefined,
                page: 1,
              })}
              className={window === value ? styles.chipActive : styles.chip}
            >
              {label}
            </Link>
          ))}
        </div>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>内容</span>
          <Link
            href={buildMatchesHref(filters, { content: undefined, page: 1 })}
            className={content == null ? styles.chipActive : styles.chip}
          >
            全部
          </Link>
          <Link
            href={buildMatchesHref(filters, { content: "analysis", page: 1 })}
            className={content === "analysis" ? styles.chipActive : styles.chip}
          >
            已有分析
          </Link>
          <Link
            href={buildMatchesHref(filters, { content: "odds", page: 1 })}
            className={content === "odds" ? styles.chipActive : styles.chip}
          >
            已有赔率
          </Link>
        </div>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>联赛</span>
          <Link
            href={buildMatchesHref(filters, { league: undefined, page: 1 })}
            className={league == null ? styles.chipActive : styles.chip}
          >
            全部
          </Link>
          {leagues.map((l) => (
            <Link
              key={l.league_id}
              href={buildMatchesHref(filters, { league: l.league_id, page: 1 })}
              className={league === l.league_id ? styles.chipActive : styles.chip}
            >
              {l.name_zh}
              {!l.accessible && <span className={styles.proBadge}>Pro</span>}
            </Link>
          ))}
        </div>
        {/* 赛季:库里每个联赛的真实赛季来自 /api/v1/leagues.available_seasons,
            不写死。选中联赛时只列该联赛的赛季;未选联赛时列全部联赛赛季的并集
            (五大联赛为 2020/2021..2026/2027,挪超/瑞超为自然年 "2026")。
            选赛季时一并把 status 放开为 all、window 放开为 all——否则默认的
            "未开赛 + 未来七天"会把历史赛季筛成 0 场。 */}
        {seasonOptions.length > 0 && (
          <div className={styles.chipRow}>
            <span className={styles.chipLabel}>赛季</span>
            <Link
              href={buildMatchesHref(filters, { season: undefined, page: 1 })}
              className={season == null ? styles.chipActive : styles.chip}
            >
              全部
            </Link>
            {seasonOptions.map((s) => (
              <Link
                key={s}
                href={buildMatchesHref(filters, {
                  season: s,
                  status: "all",
                  window: "all",
                  date: undefined,
                  page: 1,
                })}
                className={season === s ? styles.chipActive : styles.chip}
              >
                {s}
              </Link>
            ))}
          </div>
        )}
        <form method="get" action="/matches" className={styles.dateForm}>
          <label className={styles.chipLabel} htmlFor="matches-date">
            日期
          </label>
          <input
            id="matches-date"
            type="date"
            name="date"
            defaultValue={date}
            className={styles.dateInput}
          />
          {league != null && (
            <input type="hidden" name="league" value={String(league)} />
          )}
          {season && <input type="hidden" name="season" value={season} />}
          {status && <input type="hidden" name="status" value={status} />}
          <input type="hidden" name="window" value={window} />
          {content && <input type="hidden" name="content" value={content} />}
          {q && <input type="hidden" name="q" value={q} />}
          <button type="submit" className={styles.filterBtn}>
            筛选
          </button>
          {date && (
            <Link
              href={buildMatchesHref(filters, { date: undefined, page: 1 })}
              className={styles.clearLink}
            >
              清除日期
            </Link>
          )}
        </form>
        <form method="get" action="/matches" className={styles.dateForm}>
          <label className={styles.chipLabel} htmlFor="matches-team-search">
            球队
          </label>
          <input
            id="matches-team-search"
            type="search"
            name="q"
            defaultValue={q}
            placeholder="中文名或英文名"
            className={styles.searchInput}
          />
          {date && <input type="hidden" name="date" value={date} />}
          {league != null && (
            <input type="hidden" name="league" value={String(league)} />
          )}
          {season && <input type="hidden" name="season" value={season} />}
          <input type="hidden" name="status" value={status} />
          <input type="hidden" name="window" value={window} />
          {content && <input type="hidden" name="content" value={content} />}
          <button type="submit" className={styles.filterBtn}>
            搜索
          </button>
          {q && (
            <Link
              href={buildMatchesHref(filters, { q: undefined, page: 1 })}
              className={styles.clearLink}
            >
              清除搜索
            </Link>
          )}
        </form>
      </div>

      {data.matches.length === 0 ? (
        <div className={styles.emptyBox}>
          {lockedLeagueSelected ? (
            <>
              <p>
                「{selectedLeague.name_zh}」为 Pro 会员联赛,公开列表暂不展示其比赛。
              </p>
              <p className={styles.emptySub}>
                <Link href="/pricing" className={styles.emptyLink}>
                  了解会员权益 →
                </Link>
              </p>
            </>
          ) : (
            <p>没有符合当前筛选条件的比赛。</p>
          )}
        </div>
      ) : (
        <div className={styles.card}>
          {data.matches.map((m, i) => (
            <MatchRow
              key={m.match_id}
              match={m}
              freeTip={freeTipOf(tips[i])}
              returnTo={buildMatchesHref(filters, {})}
            />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className={styles.pager}>
          {page > 1 ? (
            <Link
              href={buildMatchesHref(filters, { page: page - 1 })}
              className={styles.pagerLink}
            >
              ← 上一页
            </Link>
          ) : (
            <span className={styles.pagerDisabled}>← 上一页</span>
          )}
          <span className={styles.pagerInfo}>
            第 <span className="num">{page}</span> /{" "}
            <span className="num">{totalPages}</span> 页
          </span>
          {page < totalPages ? (
            <Link
              href={buildMatchesHref(filters, { page: page + 1 })}
              className={styles.pagerLink}
            >
              下一页 →
            </Link>
          ) : (
            <span className={styles.pagerDisabled}>下一页 →</span>
          )}
        </div>
      )}

      <p className={styles.footNote}>
        默认展示未来七天未开赛比赛，并按精确开球时间排序。未开赛且已有正式
        分析的比赛只展示匿名层允许公开的一项概率。
      </p>
    </main>
  );
}
