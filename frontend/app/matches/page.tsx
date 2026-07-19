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
import styles from "./matches.module.css";

export const metadata: Metadata = {
  title: "比赛列表 — 欧赢 allwin",
  description:
    "按日期、联赛、状态浏览比赛:中文队名、比分与免费层模型最高一项概率。",
};

const PAGE_SIZE = 20;

interface Filters {
  date?: string;
  league?: number;
  status?: "upcoming" | "finished";
  page: number;
}

/** 组当前筛选的 /matches 链接;patch 中显式 undefined 表示清除该项 */
function buildHref(f: Filters, patch: Partial<Filters>): string {
  const next = { ...f, ...patch };
  const qs = new URLSearchParams();
  if (next.date) qs.set("date", next.date);
  if (next.league != null) qs.set("league", String(next.league));
  if (next.status) qs.set("status", next.status);
  if (next.page > 1) qs.set("page", String(next.page));
  const s = qs.toString();
  return s ? `/matches?${s}` : "/matches";
}

function freeTipOf(resp: PredictionResponse | null): FreeTip | null {
  if (!resp?.available || !resp.prediction) return null;
  const p = resp.prediction;
  if (p.tier === "free") {
    return { top_outcome: p.top_outcome, top_probability: p.top_probability };
  }
  return null;
}

export default async function MatchesPage({
  searchParams,
}: {
  searchParams: Promise<{
    date?: string;
    league?: string;
    status?: string;
    page?: string;
  }>;
}) {
  const sp = await searchParams;
  const date = /^\d{4}-\d{2}-\d{2}$/.test(sp.date ?? "") ? sp.date : undefined;
  const status =
    sp.status === "upcoming" || sp.status === "finished" ? sp.status : undefined;
  const league = /^\d+$/.test(sp.league ?? "") ? Number(sp.league) : undefined;
  const page = Math.max(1, parseInt(sp.page ?? "1", 10) || 1);
  const filters: Filters = { date, league, status, page };

  const qs = new URLSearchParams();
  if (date) qs.set("date", date);
  if (league != null) qs.set("league_id", String(league));
  if (status) qs.set("status", status);
  qs.set("limit", String(PAGE_SIZE));
  qs.set("offset", String((page - 1) * PAGE_SIZE));

  let leagues: LeagueInfo[];
  let data: MatchListResponse;
  try {
    [leagues, data] = await Promise.all([
      serverGet<LeagueInfo[]>("/api/v1/leagues"),
      serverGet<MatchListResponse>(`/api/v1/matches?${qs.toString()}`, {
        revalidate: 60,
      }),
    ]);
  } catch (err) {
    return (
      <main className={styles.page}>
        <h1 className={styles.title}>比赛</h1>
        <div className={styles.errorBox}>
          <div className={styles.errorTitle}>数据暂时无法加载</div>
          <p>
            后端 API 未响应,请稍后刷新重试。
            <br />
            <span className={styles.errorDetail}>
              {err instanceof Error ? err.message : String(err)}
            </span>
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
  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));

  return (
    <main className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>比赛</h1>
        <span className={styles.countChip}>
          共 <span className="num">{data.total}</span> 场
        </span>
      </div>

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
              href={buildHref(filters, { status: value, page: 1 })}
              className={status === value ? styles.chipActive : styles.chip}
            >
              {label}
            </Link>
          ))}
        </div>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>联赛</span>
          <Link
            href={buildHref(filters, { league: undefined, page: 1 })}
            className={league == null ? styles.chipActive : styles.chip}
          >
            全部
          </Link>
          {leagues.map((l) => (
            <Link
              key={l.league_id}
              href={buildHref(filters, { league: l.league_id, page: 1 })}
              className={league === l.league_id ? styles.chipActive : styles.chip}
            >
              {l.name_zh}
              {!l.accessible && <span className={styles.proBadge}>Pro</span>}
            </Link>
          ))}
        </div>
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
          {status && <input type="hidden" name="status" value={status} />}
          <button type="submit" className={styles.filterBtn}>
            筛选
          </button>
          {date && (
            <Link
              href={buildHref(filters, { date: undefined, page: 1 })}
              className={styles.clearLink}
            >
              清除日期
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
            <MatchRow key={m.match_id} match={m} freeTip={freeTipOf(tips[i])} />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className={styles.pager}>
          {page > 1 ? (
            <Link
              href={buildHref(filters, { page: page - 1 })}
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
              href={buildHref(filters, { page: page + 1 })}
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
        比赛日期为 UTC 口径的比赛日(暂无精确开球时刻);列表为公开数据,
        缓存约 1 分钟。未开赛且已有正式预测的比赛,展示免费层的模型最高一项概率。
      </p>
    </main>
  );
}
