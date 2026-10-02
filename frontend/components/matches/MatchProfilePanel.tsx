/**
 * 「数据 → 风格」子 tab 的排名模块:本场数据画像 + 进攻/防守/控球排名。
 *
 * 2026-10-02 站长:口径说明与「统计范围 / 样本场次」切换器全部去掉("中国人没有
 * 人关心这种东西,数据是用来展示肌肉的")。口径固定为后端默认的 "recent":
 * 只在本赛季这个联赛的球队里排名,每队最近 10 场、不分主客场
 * (backend/queries/league_percentile.py::RECENT_MIN_N 附近)。数据只来自首屏
 * `/preview` 内嵌的那一份,本组件不再发请求、不持有状态。
 */

import { MatchProfileOverview } from "./MatchProfileOverview";
import { PercentileGroupSection } from "./PercentileGroupSection";
import { groupSectionTitle, type DataProfile } from "./matchProfile";
import styles from "./MatchProfileScope.module.css";

const GROUP_TITLE_ZH: Record<string, string> = { 攻: "进攻", 守: "防守", 控: "控球" };

export function MatchProfilePanel({
  homeName,
  awayName,
  homeCrestUrl,
  awayCrestUrl,
  initialProfile,
}: {
  matchId?: number;
  homeName: string;
  awayName: string;
  homeCrestUrl?: string | null;
  awayCrestUrl?: string | null;
  /** 首屏那份(来自 `/preview`),口径恒为"本赛季联赛球队里排名、每队最近 10 场"。 */
  initialProfile: DataProfile;
}) {
  const shown = initialProfile;
  return (
    <div className={styles.body}>
      <MatchProfileOverview
        homeName={homeName}
        awayName={awayName}
        homeCrestUrl={homeCrestUrl}
        awayCrestUrl={awayCrestUrl}
        profile={shown}
      />
      {shown.groups.map((g) => (
        <PercentileGroupSection
          key={g.key}
          title={groupSectionTitle(GROUP_TITLE_ZH[g.title_zh] ?? g.title_zh, shown.comparison_mode)}
          windowNote=""
          homeName={homeName}
          awayName={awayName}
          homeCrestUrl={homeCrestUrl}
          awayCrestUrl={awayCrestUrl}
          group={g}
          mode={shown.comparison_mode}
        />
      ))}
    </div>
  );
}
