/**
 * 纵向双队阵容图(2026-08-25,赛前预计首发与赛后确认首发共用)。
 *
 * 对齐 FotMob 原生 APP 的恒纵向布局(APK 反编译核实:比赛详情的阵容图是
 * Compose 自定义 Layout,按服务端下发的 verticalLayout.{x,y} 绝对定位、
 * 用 verticalLayout.width 约束姓名标签宽度;res/layout-land/ 无任何 lineup
 * 横屏变体)。两队画在同一块竖版整场上:主队占上半场(己方球门在顶边,
 * 攻向下)、客队占下半场镜像(己方球门在底边,攻向上)——与 APK
 * fragment_team_lineup_home/away 上下拼接、两个半圆在中线合成完整中圈的
 * 结构一致;FotMob 网页版实测同样是 home 在上。
 *
 * 坐标语义(两侧数据源同源,都来自 FotMob verticalLayout,0..1 归一化):
 *   x = 横向(0.5=中路),y = 纵深(0.1=本方球门端 → 0.87=进攻端),
 *   w = 该球员在本行独占的横向格宽(门将/单箭头=1、四后卫=0.25)——
 *   FotMob 用它约束姓名标签宽度防串行,它是标签格尺寸**不是位置**。
 *   两队各自相对自己的半场记录,镜像在这里(渲染层)做,数据层不动
 *   (与射门图 toPoint 的架构约定一致)。
 *
 * 任一队全员缺坐标 → 该队不画点;两队都缺 → 返回 null,调用方降级为纯名单
 * (不猜站位,CLAUDE.md §6.2)。
 */

import { PlayerAvatar } from "@/components/players/PlayerAvatar";
import { FootballPitchBackground } from "./FootballPitchBackground";
import styles from "./VerticalPitchFormation.module.css";

export interface VerticalPitchPlayer {
  /** React key(赛后 player_id: string / 赛前 id: number,统一转 string 传入)。 */
  key: string;
  /** 头像 id(FotMob player id,PlayerAvatar 用它拼 CDN URL)。 */
  avatarId: string | number;
  name: string;
  shirtNumber?: string | null;
  /** verticalLayout.x/y,缺失(null)的球员不画。 */
  x: number | null | undefined;
  y: number | null | undefined;
  /** verticalLayout.width(行格宽);缺失回退 0.25(四人行宽度,最保守)。 */
  w?: number | null;
}

export interface VerticalPitchSide {
  name: string;
  formation?: string | null;
  players: VerticalPitchPlayer[];
}

/** 单个球员点位 → 球场容器百分比(纯函数,单独可测,CLAUDE.md §11.3)。
 *
 * 主队(上半场,己方球门在顶边 top=0,攻向下):
 *   left = x*100,top = y*50 —— 门将 y=0.1 → top 5%(贴顶),
 *   前锋 y=0.87 → top 43.5%(中线上方)。
 * 客队(下半场,双轴镜像,己方球门在底边,攻向上):
 *   left = (1-x)*100,top = 50 + (1-y)*50 —— 门将 y=0.1 → top 95%(贴底),
 *   前锋 y=0.87 → top 56.5%(中线下方)。
 * 客队 x 也镜像(FotMob 网页版实测 22/22 逐人核对是双轴 180° 点对称),
 * 保持两队阵型左右对称的观感——只镜像 y 会让两队的"左路"落在同一侧。 */
export function verticalDotPosition(
  isHome: boolean,
  x: number,
  y: number,
): { leftPct: number; topPct: number } {
  if (isHome) {
    return { leftPct: x * 100, topPct: y * 50 };
  }
  return { leftPct: (1 - x) * 100, topPct: 50 + (1 - y) * 50 };
}

/** 拉丁名在球场图上取姓氏(FotMob 同款:标记下方只显示姓);中文名完整
 * 渲染,截断交给 CSS 省略号——slice 会砍掉姓。2026-08-25 从赛前组件收敛到
 * 这里,赛前赛后同一份逻辑(此前赛后直接渲染全名,两边不一致)。 */
export function pitchLabel(name: string): string {
  if (/[A-Za-z]/.test(name)) {
    const parts = name.trim().split(/\s+/);
    return parts[parts.length - 1];
  }
  return name;
}

/** 格宽缺失时按"同一行人数"推导(赛前快照只存了 x/y 没存 width):
 * 真实数据里 verticalLayout.width ≈ 1/该行人数(门将=1、四后卫=0.25、
 * 双后腰=0.4……三人行 FotMob 实际给 0.325~0.337,与 1/3 差 ≤0.01,对
 * "约束姓名标签宽度防串行"这个用途等效)。同行判定用 y 值精确相等——
 * 来源坐标是量化值(同一行所有人 y 完全相同,如 0.357/0.613),两侧
 * 查询层都四舍五入到 3 位。 */
function deriveSlotW(
  p: { y: number; w?: number | null },
  rowCounts: Map<number, number>,
): number {
  if (p.w != null) return p.w;
  const n = rowCounts.get(p.y) ?? 4;
  return 1 / n;
}

function plottable(players: VerticalPitchPlayer[]): (VerticalPitchPlayer & {
  x: number;
  y: number;
})[] {
  return players.filter((p): p is VerticalPitchPlayer & { x: number; y: number } =>
    p.x != null && p.y != null,
  );
}

function Dots({ side, isHome }: { side: VerticalPitchSide; isHome: boolean }) {
  const dots = plottable(side.players);
  const rowCounts = new Map<number, number>();
  for (const p of dots) rowCounts.set(p.y, (rowCounts.get(p.y) ?? 0) + 1);
  return (
    <>
      {dots.map((p) => {
        const { leftPct, topPct } = verticalDotPosition(isHome, p.x, p.y);
        const w = deriveSlotW(p, rowCounts);
        return (
          <div
            key={p.key}
            className={styles.slot}
            style={{
              // FotMob 同款槽位定位:left = 中心 - 格宽/2,槽位宽 = 格宽,
              // 标签 max-width:100% 吃满槽位即天然防串行(拥挤行格宽小)。
              left: `${(leftPct - (w * 100) / 2).toFixed(3)}%`,
              width: `${(w * 100).toFixed(3)}%`,
              top: `${topPct.toFixed(3)}%`,
            }}
          >
            <span className={styles.avatarRing}>
              <PlayerAvatar
                playerId={p.avatarId}
                playerName={p.name}
                shirtNumber={p.shirtNumber}
                size={40}
              />
            </span>
            <span className={styles.name}>
              {p.shirtNumber ? `${p.shirtNumber} ` : ""}
              {pitchLabel(p.name)}
            </span>
          </div>
        );
      })}
    </>
  );
}

export function VerticalPitchFormation({
  home,
  away,
  variant,
}: {
  home: VerticalPitchSide;
  away: VerticalPitchSide;
  /** "confirmed"=已确认首发(绿场,FotMob lineupBackgroundColor);
   * "probable"=预计首发(石板灰场,lineupProbableBackgroundColor)——
   * FotMob 用球场底色本身区分预计/确认。深色模式两档都转中性近黑,
   * 色值见 globals.css 的 --pitch-lineup-… 与 --pitch-probable-… 两组。 */
  variant: "confirmed" | "probable";
}) {
  const homeDots = plottable(home.players);
  const awayDots = plottable(away.players);
  if (homeDots.length === 0 && awayDots.length === 0) return null;
  return (
    <div
      className={styles.pitch}
      data-variant={variant}
      role="img"
      aria-label={`阵容图:${home.name} ${home.formation ?? "阵型未知"}(上半场,攻向下),${
        away.name
      } ${away.formation ?? "阵型未知"}(下半场,攻向上)`}
    >
      <FootballPitchBackground
        orientation="portrait-full"
        variant={variant === "probable" ? "probable" : "lineup"}
      />
      {/* 两端队名+阵型角标(FotMob 每队有独立 header 行;竖版双队里放进
          球场对应半场的角上,一眼分清上下各是谁) */}
      <span className={`${styles.teamTag} ${styles.teamTagHome}`}>
        {home.name}
        {home.formation ? <b className="num"> {home.formation}</b> : null}
      </span>
      <span className={`${styles.teamTag} ${styles.teamTagAway}`}>
        {away.name}
        {away.formation ? <b className="num"> {away.formation}</b> : null}
      </span>
      <Dots side={home} isHome />
      <Dots side={away} isHome={false} />
    </div>
  );
}
