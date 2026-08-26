/**
 * 进攻区域三分带的纯逻辑(2026-08-26 从 AttackingZonesChart.tsx 拆出)。
 *
 * 真实生产事故:AttackingZonesChart.tsx 顶部是 "use client"(useState 做
 * 时段切换器需要),但 zoneSplitFrom() 被 MatchShotsSection.tsx(服务端
 * 组件)直接调用来算 props——Next.js 的 "use client" 是整个文件级别的边界,
 * 不是逐个 export 判断,服务端组件从一个 client 文件里 import 任何东西
 * (哪怕是纯函数)都会在渲染时抛 "Attempted to call X from the server but
 * X is on the client",导致比赛详情页整页崩溃(2026-08-25 部署后线上复现,
 * 见 CLAUDE.md §11.3)。
 *
 * 纯数据函数(不依赖任何 React hook/浏览器 API)搬进这个不带 "use client"
 * 的文件,服务端组件和客户端组件都能安全 import;AttackingZonesChart.tsx
 * 只保留真正需要客户端交互(时段切换器 state)的渲染逻辑。
 */

/** 整数百分比(来源 content.attackingZones 的原样数值)。 */
export interface AttackingZoneSplit {
  left: number;
  center: number;
  right: number;
}

/** 三个投影字段 → 三分区对象;任一缺失整组按 null 处理(三路占比缺了一路
 * 就不是一个完整的分布,不能拿 0 顶,CLAUDE.md §6.2)。 */
export function zoneSplitFrom(
  left: number | null | undefined,
  center: number | null | undefined,
  right: number | null | undefined,
): AttackingZoneSplit | null {
  if (left == null || center == null || right == null) return null;
  return { left, center, right };
}

/** §11.2 文字摘要的唯一出口(纯函数,测试直接断言;与 buildShotMapSummary
 * 同一体例)。缺失侧如实说"暂无",不编 0。 */
export function buildAttackingZonesSummary(args: {
  home: AttackingZoneSplit | null;
  away: AttackingZoneSplit | null;
  homeName: string;
  awayName: string;
  periodLabel: string;
}): string {
  const { home, away, homeName, awayName, periodLabel } = args;
  const side = (name: string, dir: string, s: AttackingZoneSplit | null) =>
    s
      ? `${name}(${dir})左路 ${s.left}%、中路 ${s.center}%、右路 ${s.right}%`
      : `${name}暂无进攻区域数据`;
  return (
    `进攻区域(${periodLabel},左/中/右三路各占该队进攻的比例):` +
    `${side(homeName, "攻向右", home)};${side(awayName, "攻向左", away)}。`
  );
}
