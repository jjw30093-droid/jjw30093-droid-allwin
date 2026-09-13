/**
 * 「数据 → 风格」子 tab 百分位模块的口径切换器 + 四块内容(2026-09-13)。
 *
 * 站长要的两个切换:「全部 / 相同主客场」和「近 3 场 / 5 场 / 10 场」,
 * 数据跟着选择变。范围**只管共用同一份 `data_profile` 的四块**——
 * 本场数据画像 + 进攻/防守/控球百分位;同 tab 下的「本场攻防对位」(另一套
 * 数据、窗口是近 9 场)、「球队风格定位」、「进攻来源拆解」不在范围内,
 * 由 `MatchDetailBody` 原样渲染。
 *
 * 本组件是这四块里**唯一**持有状态的地方,也是唯一一个 `"use client"` 的
 * 新增层。`MatchDetailBody` 仍然**不带** `"use client"`(§11.4 的核心保护
 * 点):它只是渲染本组件这个 client boundary,props 全部可序列化,RSC 路径
 * 下服务端照常直出默认口径的完整 HTML,首屏零额外请求、零 layout shift。
 *
 * 取数走 `/matches/{id}/data-profile` 子资源而不是重跑 `/preview`:实测
 * `/preview` 是 493 次 core SELECT(0.55s / 26KB),而这份画像本身只有 10 次
 * SELECT(~133ms)。默认口径的数据已经内嵌在首屏的 `/preview` 里,**用户不
 * 动切换器时本组件一次请求都不发**。
 */

"use client";

import { useEffect, useState } from "react";
import { clientFetch } from "@/lib/api-v1";
import type { components } from "@/lib/api-types";
import { MatchProfileOverview } from "./MatchProfileOverview";
import { PercentileGroupSection } from "./PercentileGroupSection";
import {
  DEFAULT_VENUE_MODE,
  VENUE_OPTIONS,
  WINDOW_OPTIONS,
  asWindowN,
  groupSectionTitle,
  profileScopeKey,
  profileWindowNote,
  type DataProfile,
  type VenueMode,
  type WindowN,
} from "./matchProfile";
import styles from "./MatchProfileScope.module.css";

type DataProfileResponse = components["schemas"]["MatchDataProfileResponse"];

const GROUP_TITLE_ZH: Record<string, string> = { 攻: "进攻", 守: "防守", 控: "控球" };

function Segmented<T extends string | number>({
  label,
  options,
  value,
  onPick,
}: {
  label: string;
  options: { key: T; label: string }[];
  value: T;
  onPick: (key: T) => void;
}) {
  return (
    <div className={styles.group}>
      <span className={styles.groupLabel}>{label}</span>
      <div className={styles.segmented} role="tablist" aria-label={label}>
        {options.map((o) => {
          const on = o.key === value;
          return (
            <button
              key={String(o.key)}
              type="button"
              role="tab"
              aria-selected={on}
              className={on ? styles.on : styles.off}
              onClick={() => onPick(o.key)}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function MatchProfilePanel({
  matchId,
  homeName,
  awayName,
  homeCrestUrl,
  awayCrestUrl,
  initialProfile,
}: {
  matchId: number;
  homeName: string;
  awayName: string;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  /** 首屏那份(来自 `/preview`),恒为默认口径 same_venue / 近 10 场。 */
  initialProfile: DataProfile;
}) {
  // 仓库里出现过三次的稳妥范式(OddsTimeline:728,798 / TeamStyleQuadrant:360 /
  // TeamQuadrantChart:69):useState<T|null>(null),render 时解析生效值并兜底。
  // 不把 initialProfile 的值塞进 useState 初值——那样 initialProfile 变化
  // (MemberMatchDetail 兜底路径重试成功)时状态会僵在旧值。
  const [venuePick, setVenuePick] = useState<VenueMode | null>(null);
  const [windowPick, setWindowPick] = useState<WindowN | null>(null);
  const venue = venuePick ?? initialProfile.venue_mode ?? DEFAULT_VENUE_MODE;
  const windowN = windowPick ?? asWindowN(initialProfile.window_n);
  const scopeKey = profileScopeKey(venue, windowN);

  // 已取回的口径缓存:切回看过的组合零请求、零闪烁。放 state 而不是 ref
  // ——`react-hooks/refs` 明令 ref 不得在渲染期读写,而这份缓存恰恰要参与
  // 渲染取值。首屏那份不入缓存,它由 initialProfile 直接兜(它还可能变:
  // MemberMatchDetail 兜底路径重试成功时会换一份新的)。
  const [cache, setCache] = useState<Map<string, DataProfile>>(() => new Map());
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const initialKey = profileScopeKey(
    initialProfile.venue_mode ?? DEFAULT_VENUE_MODE,
    asWindowN(initialProfile.window_n),
  );
  const lookup = (key: string): DataProfile | null =>
    key === initialKey ? initialProfile : (cache.get(key) ?? null);

  // 取数中继续渲染上一次看到的那份,不把整块塌成空白——这四块实测 ~1000px 高,
  // 卸载会让页面剧烈跳动,而请求只有 ~130ms,不值得付这个代价。
  // 「上一次」记在 state 里、且只在**点击时**写:在 effect 里同步 setState
  // 会触发级联渲染(react-hooks/set-state-in-effect 明令禁止),异步的
  // `.then()` 回调不在此列,所以取数成功那条路照常走 setCache。
  const [fallbackKey, setFallbackKey] = useState(initialKey);
  const resolved = lookup(scopeKey);
  const shown = resolved ?? lookup(fallbackKey) ?? initialProfile;
  const pending = resolved == null && failedKey !== scopeKey;

  const pick = (nextVenue: VenueMode, nextWindow: WindowN) => {
    // 当前这份是好的,留作下一次切换期间的垫底内容
    if (resolved) setFallbackKey(scopeKey);
    setVenuePick(nextVenue);
    setWindowPick(nextWindow);
  };

  useEffect(() => {
    if (scopeKey === initialKey || cache.has(scopeKey)) return;
    let cancelled = false;
    clientFetch<DataProfileResponse>(
      `/api/v1/matches/${matchId}/data-profile?venue=${venue}&n=${windowN}`,
    )
      .then((r) => {
        if (cancelled) return;
        setCache((m) => new Map(m).set(scopeKey, r.profile));
        setFailedKey(null);
      })
      .catch(() => {
        // 失败不清空内容:旧数据继续可见,只在切换器下方挂一条可重试的提示。
        if (!cancelled) setFailedKey(scopeKey);
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, scopeKey, initialKey, cache, venue, windowN, attempt]);

  const retry = () => {
    setFailedKey(null);
    setAttempt((n) => n + 1);
  };

  const switcher = (
    <div className={styles.bar}>
      <Segmented label="统计范围" options={VENUE_OPTIONS} value={venue} onPick={(v) => pick(v, windowN)} />
      <Segmented label="样本场次" options={WINDOW_OPTIONS} value={windowN} onPick={(n) => pick(venue, n)} />
      {failedKey === scopeKey && (
        <p className={styles.error}>
          这个口径没取到数据，下面仍是上一次的结果。
          <button type="button" className={styles.retry} onClick={retry}>
            重试
          </button>
        </p>
      )}
    </div>
  );

  const windowNote = profileWindowNote(homeName, awayName, shown);

  return (
    <div
      className={`${styles.body}${pending ? ` ${styles.pending}` : ""}`}
      aria-busy={pending || undefined}
    >
      <MatchProfileOverview
        homeName={homeName}
        awayName={awayName}
        homeCrestUrl={homeCrestUrl}
        awayCrestUrl={awayCrestUrl}
        profile={shown}
        scopeSwitcher={switcher}
      />
      {shown.groups.map((g, i) => (
        <PercentileGroupSection
          key={g.key}
          title={groupSectionTitle(GROUP_TITLE_ZH[g.title_zh] ?? g.title_zh, shown.comparison_mode)}
          windowNote={windowNote}
          homeName={homeName}
          awayName={awayName}
          homeCrestUrl={homeCrestUrl}
          awayCrestUrl={awayCrestUrl}
          group={g}
          // 口径说明只在最后一个百分位模块底部出现一次——三段几乎相同的说明
          // 原来每个模块各印一遍。
          showMethodNote={i === shown.groups.length - 1}
          mode={shown.comparison_mode}
          venueMode={shown.venue_mode ?? DEFAULT_VENUE_MODE}
        />
      ))}
    </div>
  );
}
