"use client";

/**
 * 比赛列表页的整个交互主体(筛选栏 + 比赛行 + 翻页)。
 *
 * 会话 cookie Path=/api/v1,Next RSC 读不到 —— /matches 的服务端渲染始终按
 * 匿名身份请求。2026-08-16 权限口径修正后,后端对任何人(含匿名)恒返回
 * 完整联赛集合与完整比赛内容,不再有 entitlement/accessible/requires_login
 * 这类登录门禁字段——筛选栏与比赛行不因登录状态而不同。本组件挂载后仍会
 * 用浏览器带 cookie 重新拉一次 /api/v1/leagues + /api/v1/matches,但这只是
 * 为了拿到比服务端渲染时更新的数据,不再是"匿名口径 → 换成会员口径"这个
 * 语义。
 *
 * 静默降级:浏览器端刷新失败(网络问题等)时保留 SSR 渲染的内容,不整页
 * 报错。
 *
 * 无 JS 环境:整块仍然是纯 GET 链接 + `<form method="get">`,服务端渲染出的
 * 初始 HTML 本身完整可用;只是拿不到"挂载后用 cookie 刷新"这一步的增强,
 * 与 MemberLeagueSection 同款降级方式。
 *
 * 联赛筛选行必须和状态/时间/内容/赛季/日期/搜索几行放在同一个组件里渲染
 * (而不是分拆成两个客户端组件插在服务端 JSX 中间),否则筛选行的可见顺序会
 * 因为哪部分是"客户端"哪部分是"服务端"而被打散——这几行在数据来源上互相
 * 独立,但在页面视觉结构上必须保持一个整体。
 *
 * 移动端首屏(P3.B,2026-08-16):390px 视口下所有筛选控件曾经全部摊平展开,
 * 第一场比赛要滑到 y≈495px 才出现。现在只有"时间/内容/联赛"三组高频筛选
 * 留在主筛选行(始终可见);"状态"(全部/未开赛/已完赛,与"时间"轴语义
 * 重叠,只在回看已完赛比赛时才用得到)、"赛季""日期""球队搜索"四个低频
 * 控件收进一个默认折叠的原生 `<details>`"更多筛选"(与
 * MatchDetailBody.tsx::metaDetails、MarketCard.tsx 同款折叠模式,不用 JS
 * state 模拟——折叠本身也不需要脚本才能展开,无 JS 环境同样可用)。若用户
 * 已经通过 URL 带着这四个控件里的任意筛选值进来,`<details>` 默认展开,
 * 不能把用户已经选中的筛选悄悄藏起来。
 *
 * 比赛行按开球日期分组同样是纯前端视觉分组:分组 key 直接复用
 * zh.ts::beijingDateKey(精确 kickoff 缺失时退回 date_utc,与 MatchRow 展示
 * 该字段时的既有 fallback 约定一致),按 matches 数组已有顺序逐条判断
 * "是否换了一天"插入标题,不重新排序、不合并非相邻的同一天比赛、不因为
 * 分页边界切在某天中间就发额外请求"补全"——同一天标题在下一页重复出现是
 * 分页的正常代价。
 */

import { Fragment, useEffect, useState } from "react";
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
  defaultWindowFor,
  type MatchFilters as Filters,
} from "@/lib/match-filters";
import { MatchRow } from "./MatchRow";
import { beijingDateKey, formatDateHeadingZh } from "./zh";
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

/**
 * 时间 chip 按状态分叉(2026-08-19)。
 *
 * 两套刻意都是 5 个 chip:移动端首屏"第一场比赛 y < 495px"这条验收只剩
 * 25px 余量(e2e/matches-mobile-first-screen.spec.ts、
 * matches-mobile-touch-targets.spec.ts 都断言它),chip 行数或行高一变就顶穿。
 *
 * 赛果侧的「今天」复用既有的 `today` token,不另造:`today` 本来就是北京
 * 自然日 [今天00:00, 明天00:00),天然双向,配 status=finished 就是"今天已经
 * 结束的比赛"。其余三个是新增的向过去窗口(见 backend/queries/matches.py
 * 的 _window_bounds)。
 */
const UPCOMING_WINDOW_TABS = [
  ["today", "今天"],
  ["tomorrow", "明天"],
  ["3d", "未来三天"],
  ["7d", "未来七天"],
  ["all", "全部未来"],
] as const;

const FINISHED_WINDOW_TABS = [
  ["today", "今天"],
  ["yesterday", "昨天"],
  ["past3d", "近三天"],
  ["past7d", "近七天"],
  ["all", "全部赛果"],
] as const;

function windowTabsFor(
  status: Filters["status"],
): readonly (readonly [Filters["window"], string])[] {
  return status === "upcoming" ? UPCOMING_WINDOW_TABS : FINISHED_WINDOW_TABS;
}

/** 分组 key:精确 kickoff 缺失时退回 date_utc,与 MatchRow 渲染该字段时的
 * fallback 约定一致(见 MatchRow.tsx:`match.kickoff_at_utc ? <LocalTime .../> :
 * match.date_utc`)——不为了凑一个"北京日"就给没有精确时刻的比赛编造一个。 */
function matchDateKey(m: MatchSummary): string {
  if (m.kickoff_at_utc) {
    const key = beijingDateKey(m.kickoff_at_utc);
    if (key) return key;
  }
  return m.date_utc;
}

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
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <>
      <span className={styles.countChip}>
        当前筛选共 <span className="num">{total}</span> 场
      </span>
      {windowWidened && (
        <p className={styles.widenNote}>未来 7 天暂无比赛，已自动展示全部未来赛程。</p>
      )}

      {/* 筛选:纯 GET 表单 + 链接,无 JS 也可用。只有"时间/内容/联赛"三组
          高频筛选留在这里始终可见;"状态/赛季/日期/球队搜索"收进下面默认
          折叠的"更多筛选"(见文件头注释)。 */}
      <div className={styles.filters}>
        <div className={styles.chipRow}>
          <span className={styles.chipLabel}>时间</span>
          {/* 刻意**不**写 status:"upcoming" —— 那是修掉的 bug:用户切到
              已完赛后点任意时间 chip 就被弹回赛程,赛果里等于没有时间筛选。
              保留当前 status,时间与状态是两个正交维度。 */}
          {windowTabsFor(status).map(([value, label]) => (
            <Link
              key={value}
              href={buildMatchesHref(filters, {
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
              {l.data_status !== "AVAILABLE" && (
                <span className={styles.proBadge}>暂未收录</span>
              )}
            </Link>
          ))}
        </div>

        {/* 更多筛选:状态 + 赛季 + 日期 + 球队搜索,默认折叠。用户已经带着
            这四项里任意一项的筛选值进来时自动展开,不藏起用户已选的条件。
            原生 <details>,不需要脚本即可展开——保持"无 JS 也可用"。 */}
        <details
          className={styles.moreFilters}
          open={status !== "upcoming" || Boolean(season) || Boolean(date) || Boolean(q)}
        >
          <summary className={styles.moreFiltersSummary}>更多筛选</summary>
          <div className={styles.moreFiltersBody}>
            <div className={styles.chipRow}>
              <span className={styles.chipLabel}>状态</span>
              {/* 切状态必须同时改写 window:两侧的时间 token 不通用(带着
                  past7d 切回未开赛 = 恒空,而 isWindowAutoWidenEligible 因为
                  window 显式存在也不会放宽 → 白板且无任何解释)。统一走
                  defaultWindowFor,不再硬编码 "all"。 */}
              {STATUS_TABS.map(([value, label]) => (
                <Link
                  key={label}
                  href={buildMatchesHref(filters, {
                    status: value ?? "all",
                    window: defaultWindowFor(value ?? "all"),
                    page: 1,
                  })}
                  className={status === (value ?? "all") ? styles.chipActive : styles.chip}
                >
                  {label}
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
        </details>
      </div>

      {matches.length === 0 ? (
        <div className={styles.emptyBox}>
          <p>这几个条件凑一起，一场都没有。</p>
          {/* 选了一个已经过去的日期却停在赛程视图 = 恒空(date ∧ 未开赛)。
              这是"看不到已结束比赛"的第二条死路,而且白板不给任何解释。
              这里只给出口,不自动改写用户的筛选——静默改写会让 URL 与界面
              上显示的筛选状态对不上。 */}
          {date && status === "upcoming" && (
            <p className={styles.emptyHint}>
              这一天的比赛可能已经结束了。
              <Link href={buildMatchesHref(filters, { status: "finished", window: "all", page: 1 })}>
                查看 {date} 的赛果 →
              </Link>
            </p>
          )}
        </div>
      ) : (
        <div className={styles.card}>
          {matches.map((m, i) => {
            // 按开球日期(北京时间,缺失精确 kickoff 时退回 date_utc)插入组间
            // 小标题。纯前端视觉分组:只看"这条和上一条是否同一天",不重新
            // 排序、不合并数组里非相邻的同一天比赛——分页边界切在某天中间时
            // 标题会在下一页重复出现,这是分页的正常代价,不为此发额外请求。
            // 标题和比赛行必须是 .card 的直接子级(不能再套一层 wrapper div),
            // 否则 MatchRow.module.css 的 `.row:last-child` 去边框选择器会
            // 按"每个 wrapper 只有一个 .row 子节点"误判成每一行都是 last-child。
            const dateKey = matchDateKey(m);
            const showHeading = i === 0 || matchDateKey(matches[i - 1]) !== dateKey;
            return (
              <Fragment key={m.match_id}>
                {showHeading && (
                  <div className={styles.dateHeading} data-testid="date-heading">
                    {formatDateHeadingZh(dateKey)}
                  </div>
                )}
                <MatchRow match={m} returnTo={buildMatchesHref(filters, {})} />
              </Fragment>
            );
          })}
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
