"use client";

/**
 * 比赛列表页的整个交互主体(筛选栏 + 比赛行 + 翻页)。
 *
 * 会话 cookie Path=/api/v1,Next RSC 读不到 —— /matches 的服务端渲染永远是
 * 匿名口径(联赛筛选栏的"登录"角标、比赛行是否出现,都按匿名 entitlement 算)。
 * 本组件挂载后用浏览器带 cookie 重新拉一次 /api/v1/leagues + /api/v1/matches;
 * 已登录且持有 league:lottery 等权益的用户,真实结果会替换掉 SSR 渲染的匿名
 * 内容——否则任何身份登录后打开 /matches 看到的筛选栏和比赛行都和匿名访客
 * 完全一样(2026-08-13 用户报告的真实回归:即便持有全部联赛权益的账号,
 * 列表也只剩五大联赛;这是 B1/B4 已经修过的"服务端匿名取数被当成最终结果"
 * 同一根因,只是发生在比赛列表页而不是详情页/联赛子页,当时没有覆盖到)。
 *
 * 静默降级:浏览器端刷新失败(网络问题等)时保留 SSR 渲染的匿名内容,不整页
 * 报错——匿名可见的那部分本来就是正确、可用的。
 *
 * 无 JS 环境:整块仍然是纯 GET 链接 + `<form method="get">`,服务端渲染出的
 * 初始 HTML(匿名口径)本身完整可用;只是拿不到"挂载后用 cookie 刷新"这一步
 * 的增强,与 MemberLeagueSection 同款降级方式。
 *
 * 联赛筛选行必须和状态/时间/内容/赛季/日期/搜索几行放在同一个组件里渲染
 * (而不是分拆成两个客户端组件插在服务端 JSX 中间),否则筛选行的可见顺序会
 * 因为哪部分是"客户端"哪部分是"服务端"而被打散——这几行在数据来源上互相
 * 独立,但在页面视觉结构上必须保持一个整体。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  clientFetch,
  type LeagueInfo,
  type MatchListResponse,
  type MatchSummary,
} from "@/lib/api-v1";
import {
  buildMatchesApiQuery,
  buildMatchesHref,
  type MatchFilters as Filters,
} from "@/lib/match-filters";
import { MatchRow } from "./MatchRow";
import styles from "@/app/matches/matches.module.css";

interface Props {
  filters: Filters;
  pageSize: number;
  autoWidenEligible: boolean;
  seasonOptions: string[];
  initialLeagues: LeagueInfo[];
  initialMatches: MatchSummary[];
  initialTotal: number;
  initialWindowWidened: boolean;
}

const STATUS_TABS = [
  [undefined, "全部"],
  ["upcoming", "未开赛"],
  ["finished", "已完赛"],
] as const;

const WINDOW_TABS = [
  ["today", "今天"],
  ["tomorrow", "明天"],
  ["3d", "未来三天"],
  ["7d", "未来七天"],
  ["all", "全部未来"],
] as const;

export function MatchListLive({
  filters,
  pageSize,
  autoWidenEligible,
  seasonOptions,
  initialLeagues,
  initialMatches,
  initialTotal,
  initialWindowWidened,
}: Props) {
  const [leagues, setLeagues] = useState(initialLeagues);
  const [matches, setMatches] = useState(initialMatches);
  const [total, setTotal] = useState(initialTotal);
  const [windowWidened, setWindowWidened] = useState(initialWindowWidened);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const [freshLeagues, firstPass] = await Promise.all([
          clientFetch<LeagueInfo[]>("/api/v1/leagues"),
          clientFetch<MatchListResponse>(
            `/api/v1/matches?${buildMatchesApiQuery(filters, { limit: pageSize })}`,
          ),
        ]);
        let freshData = firstPass;
        let widened = false;
        if (freshData.total === 0 && autoWidenEligible) {
          freshData = await clientFetch<MatchListResponse>(
            `/api/v1/matches?${buildMatchesApiQuery(filters, {
              limit: pageSize,
              windowOverride: "all",
            })}`,
          );
          widened = true;
        }
        if (cancelled) return;
        setLeagues(freshLeagues);
        setMatches(freshData.matches);
        setTotal(freshData.total);
        setWindowWidened(widened);
      } catch {
        // 保留 SSR 的匿名口径渲染,不覆盖成错误态。
      }
    }
    run();
    return () => {
      cancelled = true;
    };
    // 父组件按最终 query 串 key={} 整个 remount 本组件,挂载时跑一次即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { date, league, season, status, window, content, q, page } = filters;
  const selectedLeague = leagues.find((l) => l.league_id === league);
  const lockedLeagueSelected = selectedLeague != null && !selectedLeague.accessible;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <>
      <span className={styles.countChip}>
        当前筛选共 <span className="num">{total}</span> 场
      </span>
      {windowWidened && (
        <p className={styles.widenNote}>未来 7 天暂无比赛,已自动展示全部未来赛程。</p>
      )}

      {/* 筛选:纯 GET 表单 + 链接,无 JS 也可用 */}
      <div className={styles.filters}>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>状态</span>
          {STATUS_TABS.map(([value, label]) => (
            <Link
              key={label}
              href={buildMatchesHref(filters, {
                status: value ?? "all",
                window: value === "finished" || value == null ? "all" : filters.window,
                page: 1,
              })}
              className={status === (value ?? "all") ? styles.chipActive : styles.chip}
            >
              {label}
            </Link>
          ))}
        </div>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>时间</span>
          {WINDOW_TABS.map(([value, label]) => (
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
              {!l.accessible && <span className={styles.proBadge}>登录</span>}
              {l.data_status !== "AVAILABLE" && (
                <span className={styles.proBadge}>暂未收录</span>
              )}
            </Link>
          ))}
        </div>
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
          {league != null && <input type="hidden" name="league" value={String(league)} />}
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
          {league != null && <input type="hidden" name="league" value={String(league)} />}
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

      {matches.length === 0 ? (
        <div className={styles.emptyBox}>
          <p>没有符合当前筛选条件的比赛。</p>
          {/* 2026-08-13:锁定联赛现在会真的出现在列表里,这里不再是"该联赛
              整体隐藏"的解释,只是当前筛选恰好 0 场时的登录引导。 */}
          {lockedLeagueSelected && (
            <p className={styles.emptySub}>
              <Link
                href={`/login?next=${encodeURIComponent(buildMatchesHref(filters, {}))}`}
                className={styles.emptyLink}
              >
                登录后查看「{selectedLeague.name_zh}」完整赔率与概率 →
              </Link>
            </p>
          )}
        </div>
      ) : (
        <div className={styles.card}>
          {matches.map((m) => (
            <MatchRow key={m.match_id} match={m} returnTo={buildMatchesHref(filters, {})} />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className={styles.pager}>
          {page > 1 ? (
            <Link href={buildMatchesHref(filters, { page: page - 1 })} className={styles.pagerLink}>
              ← 上一页
            </Link>
          ) : (
            <span className={styles.pagerDisabled}>← 上一页</span>
          )}
          <span className={styles.pagerInfo}>
            第 <span className="num">{page}</span> / <span className="num">{totalPages}</span> 页
          </span>
          {page < totalPages ? (
            <Link href={buildMatchesHref(filters, { page: page + 1 })} className={styles.pagerLink}>
              下一页 →
            </Link>
          ) : (
            <span className={styles.pagerDisabled}>下一页 →</span>
          )}
        </div>
      )}
    </>
  );
}
