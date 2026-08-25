/**
 * 共享球场底图(SVG):射门落点图(ShotMapChart)、纵向双队阵容图
 * (VerticalPitchFormation)、预计首发此前各自用几个绝对定位 `<div>`/
 * `<span>`(半场线/中圈/禁区矩形)画一个残缺球场——没有小禁区、罚球点、
 * 罚球弧、球门、角球弧,CSS 逐处重复。这里合并成一份按真实 FIFA 比例
 * (米制)画的完整球场,三处调用方共用。
 *
 * `orientation="landscape"`(默认):viewBox 固定 "0 0 105 68"(米),与
 * ShotMapChart 的 PITCH_LEN/PITCH_WID 及 ECharts scatter 的坐标域
 * (xAxis/yAxis min:0 max:105/68,grid 零边距)完全对齐——射门点因此天然
 * 落在正确的球场位置上,不需要额外换算。**不要**扩大 viewBox 去画"探出
 * 边界"的球门纵深(cc/vip 参考实现都这样画),那会让这份 SVG 的像素边界和
 * ECharts 数据域的 0/105 边界错开,球场画完了但射门点跟线条对不上——球门
 * 改成贴边界画一条加粗线代替,不牺牲对齐正确性。(2026-08-25 前的横向
 * 阵型图 PitchFormation 曾用这个朝向,已被纵向的 portrait-full 取代。)
 *
 * `orientation="portrait"`:viewBox "0 52.5 68 52.5"——真实半场(2026-08-20
 * 由全场改半场:ProjectedLineupSection 一次只画一队,球场另一端(禁区/球门)
 * 从来没有球员站在那,画出来是纯装饰,站长要求改成真实半场,参照球队战术板
 * 惯例)。裁到只保留禁区/小禁区/罚球点/罚球弧/球门在 y=105 这一端(门将真正
 * 所在的那一端,见 ProjectedLineupSection.tsx 的 `column-reverse` 布局——
 * 门将天然贴容器底部,与这一端的球门对齐),半场线 + 半个中圈落在 viewBox
 * 顶边——与真实半场战术图的观感一致(禁区在下,中线在上)。容器的
 * aspect-ratio 应为真实的 68/52.5(≈1.295:1,比全场版更宽更矮),不能沿用
 * 全场时代的 68/105。
 * 禁区弧线的 sweep-flag 不是把 landscape 的 x/y 直接对调就对——x↔y 互换是
 * 一次镜像(手性反转),原样搬会让罚球弧朝禁区里面鼓包而不是朝外,已用独立
 * 脚本逐点模拟验证过真实落点(而不是仅凭手算三角函数),下方 sweep 值已验证
 * 正确。
 *
 * 真实比例来源(与 miaomiaodi.cc `FormationPitch.tsx`/`FotmobFinalClient.tsx`
 * 的 PitchLines、miaomiaodi.vip `InteractiveShotMap.tsx` 独立核对一致):
 * 禁区 40.32m×16.5m、小禁区 18.32m×5.5m、球门 7.32m、罚球点距底线 11m、
 * 罚球弧半径 9.15m、角球弧半径 1m。
 *
 * 颜色是硬编码常量,不随亮/暗色主题切换——草坪就是草坪,不因为站点在深色
 * 模式就变成别的颜色(阵型图/预计首发两处的球员点是不透明色块,在绿色上
 * 对比度没问题,继续用绿)。刻意不用光晕/毛玻璃(app/globals.css 深色模式
 * 注释:"仍是体育数据编辑部,不使用霓虹光晕或玻璃拟态")——只用交替色块画
 * 草坪割纹,不加 shadowBlur/发光描边。
 *
 * `variant="neutral"`(2026-08-24 新增,射门落点专用):FotMob 官方安卓包解包
 * 实测(res/drawable/ic_shotmap_pitch.xml + resources.arsc 解出的真实颜色值)
 * 证实他们的射门图球场根本不是绿色——浅色 `#F8FAFA` 底 + `#D6D5D5`/`#ABABAB`
 * 线,深色 `drawable-night-v8/` 变体 `#333333` 底 + `#454545`/`#6E6E6E` 线。
 * 原因:射门点要用半透明/描边表达"进球/非进球"这类语义色,压在高饱和绿色上
 * 天然低对比(2026-08-23 曾把点色从金/中性灰换成品牌青绿/蓝,合成后对比度
 * 只有 1.09:1~1.17:1,实测非进球点等于隐形)。中性底把颜色信号完全让给标记,
 * 品牌色在浅色中性底上有 4.7~5.4:1,比 FotMob 自己的琥珀色在浅底上的 1.78:1
 * 还高。只用于 ShotMapChart/ShotMapExplorer,不影响阵型图/预计首发。
 */

const LINE = "rgba(255,255,255,0.62)";
const LINE_SOFT = "rgba(255,255,255,0.4)";
const TURF_A = "#2c8a57";
const TURF_B = "#297f50";
// 中性变体:纯色底 + var() 走 CSS 级联随主题切换(内联 SVG 是 DOM 元素,
// 天然支持 CSS 自定义属性,不像 ECharts canvas 需要运行期读取解析值)。
const NEUTRAL_BG = "var(--pitch-neutral-bg)";
const NEUTRAL_LINE = "var(--pitch-neutral-line-strong)";
const NEUTRAL_LINE_SOFT = "var(--pitch-neutral-line)";

/** 阵容球场两档(2026-08-25,FotMob 安卓包 Base.Theme.FotMob.DayNight 主题
 * 解包实测):浅色模式**确认首发=绿场 #01935C、预计首发=石板灰 #596470**
 * ——FotMob 用球场底色本身区分"预计 vs 确认";深色模式两档都转中性近黑
 * #1D1D1D(不是绿的)。色值全部走 CSS 变量随主题级联(globals.css),
 * 同一份 JSX 深浅共用(CLAUDE.md §11.2)。线色是 FotMob 同款的同调微对比
 * (tone-on-tone),不沿用绿茵变体的白色线。 */
const LINEUP_BG = "var(--pitch-lineup-bg)";
const LINEUP_LINE = "var(--pitch-lineup-line)";
const PROBABLE_BG = "var(--pitch-probable-bg)";
const PROBABLE_LINE = "var(--pitch-probable-line)";

type PitchVariant = "turf" | "neutral" | "lineup" | "probable";

/** variant → {底色(null=绿茵割纹), 主线色, 弱线色}。lineup/probable 是
 * FotMob 式纯色平底(它的球场底就是主题色块,线是矢量,没有割纹纹理)。 */
function pitchColors(variant: PitchVariant): {
  bg: string | null;
  line: string;
  lineSoft: string;
} {
  switch (variant) {
    case "neutral":
      return { bg: NEUTRAL_BG, line: NEUTRAL_LINE, lineSoft: NEUTRAL_LINE_SOFT };
    case "lineup":
      return { bg: LINEUP_BG, line: LINEUP_LINE, lineSoft: LINEUP_LINE };
    case "probable":
      return { bg: PROBABLE_BG, line: PROBABLE_LINE, lineSoft: PROBABLE_LINE };
    default:
      return { bg: null, line: LINE, lineSoft: LINE_SOFT };
  }
}

// 禁区/小禁区/罚球点/罚球弧的真实比例(米),两侧关于 x=52.5 镜像。
const BOX_W = 16.5;
const BOX_Y0 = 13.84;
const BOX_H = 40.32; // 54.16 - 13.84
const GOAL_BOX_W = 5.5;
const GOAL_BOX_Y0 = 24.84;
const GOAL_BOX_H = 18.32; // 43.16 - 24.84
const SPOT_X = 11;
const ARC_R = 9.15;
const GOAL_Y0 = 30.34;
const GOAL_Y1 = 37.66; // 7.32m 球门宽
// 罚球弧与禁区线的交点(见模块顶部真实比例注释,弦长按勾股定理算出):
// dx = 16.5-11 = 5.5,dy = sqrt(9.15² - 5.5²) ≈ 7.312。
const ARC_DY = Math.sqrt(ARC_R * ARC_R - (BOX_W - SPOT_X) * (BOX_W - SPOT_X));
const ARC_Y0 = 34 - ARC_DY;
const ARC_Y1 = 34 + ARC_DY;
// portrait 罚球弧与禁区线交点的 x 坐标(宽度轴仍是 0..68,中点同样是 34,
// 与 landscape 的 ARC_Y0/ARC_Y1 是同一个数值,只是套在不同的轴上)。
const ARC_X0 = 34 - ARC_DY;
const ARC_X1 = 34 + ARC_DY;

/** 8 条交替色横纹(landscape 是竖纹沿 x,portrait 转 90° 沿 y),只是平色块,
 * 不叠加渐变/光晕。中性变体不画割纹(FotMob 的射门图球场是纯色平底,割纹
 * 纹理本身也是弱对比度的视觉噪声,索性去掉,只留一块 NEUTRAL_BG 底色)。 */
function mowStripesLandscape(variant: PitchVariant) {
  const { bg } = pitchColors(variant);
  if (bg != null) {
    return <rect x={0} y={0} width={105} height={68} fill={bg} />;
  }
  const n = 8;
  const w = 105 / n;
  return Array.from({ length: n }, (_, i) => (
    <rect
      key={i}
      x={i * w}
      y={0}
      width={w}
      height={68}
      fill={i % 2 === 0 ? TURF_A : TURF_B}
    />
  ));
}

function mowStripesPortrait(variant: PitchVariant) {
  const { bg } = pitchColors(variant);
  if (bg != null) {
    return <rect x={0} y={0} width={68} height={105} fill={bg} />;
  }
  const n = 8;
  const h = 105 / n;
  return Array.from({ length: n }, (_, i) => (
    <rect
      key={i}
      x={0}
      y={i * h}
      width={68}
      height={h}
      fill={i % 2 === 0 ? TURF_A : TURF_B}
    />
  ));
}

function LandscapePitch({ variant }: { variant: PitchVariant }) {
  const { line, lineSoft } = pitchColors(variant);
  return (
    <svg
      viewBox="0 0 105 68"
      preserveAspectRatio="none"
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    >
      {mowStripesLandscape(variant)}

      {/* 外边界 */}
      <rect x={0} y={0} width={105} height={68} fill="none" stroke={line} strokeWidth={0.4} />
      {/* 半场线 */}
      <line x1={52.5} y1={0} x2={52.5} y2={68} stroke={line} strokeWidth={0.4} />
      {/* 中圈 + 中点 */}
      <circle cx={52.5} cy={34} r={ARC_R} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={52.5} cy={34} r={0.35} fill={line} />

      {/* 左禁区 */}
      <rect x={0} y={BOX_Y0} width={BOX_W} height={BOX_H} fill="none" stroke={line} strokeWidth={0.4} />
      <rect x={0} y={GOAL_BOX_Y0} width={GOAL_BOX_W} height={GOAL_BOX_H} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={SPOT_X} cy={34} r={0.35} fill={line} />
      <path
        d={`M ${BOX_W} ${ARC_Y0.toFixed(3)} A ${ARC_R} ${ARC_R} 0 0 1 ${BOX_W} ${ARC_Y1.toFixed(3)}`}
        fill="none"
        stroke={line}
        strokeWidth={0.4}
      />
      {/* 左球门(贴边界加粗线,不探出边界——保持 ECharts 数据域对齐,见模块注释) */}
      <line x1={0} y1={GOAL_Y0} x2={0} y2={GOAL_Y1} stroke={line} strokeWidth={1.3} strokeLinecap="round" />

      {/* 右禁区(左侧镜像 x → 105-x) */}
      <rect x={105 - BOX_W} y={BOX_Y0} width={BOX_W} height={BOX_H} fill="none" stroke={line} strokeWidth={0.4} />
      <rect x={105 - GOAL_BOX_W} y={GOAL_BOX_Y0} width={GOAL_BOX_W} height={GOAL_BOX_H} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={105 - SPOT_X} cy={34} r={0.35} fill={line} />
      <path
        d={`M ${105 - BOX_W} ${ARC_Y1.toFixed(3)} A ${ARC_R} ${ARC_R} 0 0 1 ${105 - BOX_W} ${ARC_Y0.toFixed(3)}`}
        fill="none"
        stroke={line}
        strokeWidth={0.4}
      />
      <line x1={105} y1={GOAL_Y0} x2={105} y2={GOAL_Y1} stroke={line} strokeWidth={1.3} strokeLinecap="round" />

      {/* 四角角球弧(半径 1m,与边界圆弧同心于四个角点) */}
      <path d="M 1 0 A 1 1 0 0 1 0 1" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 104 0 A 1 1 0 0 0 105 1" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 1 68 A 1 1 0 0 0 0 67" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 104 68 A 1 1 0 0 1 105 67" fill="none" stroke={lineSoft} strokeWidth={0.35} />
    </svg>
  );
}

/** 竖版单队半场(viewBox "0 52.5 68 52.5")。只画球门真正所在的 y=105 这一端
 * ——半场线 + 半个中圈落在顶边,禁区/小禁区/罚球点/罚球弧/球门在底边,四个
 * 角球弧只保留 y=105 这一端的两个(y=0 端完全在 viewBox 之外,画出来也不会
 * 显示,不留这段死代码)。禁区弧线的 sweep-flag 不是转置 landscape 的坐标就
 * 对——转置是镜像、会反转手性,已用独立脚本逐点模拟验证过朝外鼓包(而不是
 * 仅凭三角函数手算),见模块顶部注释。 */
function PortraitPitch({ variant }: { variant: PitchVariant }) {
  const { line, lineSoft } = pitchColors(variant);
  return (
    <svg
      viewBox="0 52.5 68 52.5"
      preserveAspectRatio="none"
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    >
      {mowStripesPortrait(variant)}

      {/* 外边界(只有左右两条边线 + 底边球门线落在可见范围内,上边是半场线,
          不是真实球场边界,所以不额外画一条容易和半场线混淆的横线) */}
      <rect x={0} y={0} width={68} height={105} fill="none" stroke={line} strokeWidth={0.4} />
      {/* 半场线 */}
      <line x1={0} y1={52.5} x2={68} y2={52.5} stroke={line} strokeWidth={0.4} />
      {/* 中圈(半场只露出朝向禁区的那半个弧) + 中点 */}
      <circle cx={34} cy={52.5} r={ARC_R} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={34} cy={52.5} r={0.35} fill={line} />

      {/* 禁区(球门在 y=105) */}
      <rect x={BOX_Y0} y={105 - BOX_W} width={BOX_H} height={BOX_W} fill="none" stroke={line} strokeWidth={0.4} />
      <rect x={GOAL_BOX_Y0} y={105 - GOAL_BOX_W} width={GOAL_BOX_H} height={GOAL_BOX_W} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={34} cy={105 - SPOT_X} r={0.35} fill={line} />
      <path
        d={`M ${ARC_X1.toFixed(3)} ${105 - BOX_W} A ${ARC_R} ${ARC_R} 0 0 0 ${ARC_X0.toFixed(3)} ${105 - BOX_W}`}
        fill="none"
        stroke={line}
        strokeWidth={0.4}
      />
      <line x1={GOAL_Y0} y1={105} x2={GOAL_Y1} y2={105} stroke={line} strokeWidth={1.3} strokeLinecap="round" />

      {/* 两个角球弧(y=105 这一端) */}
      <path d="M 1 105 A 1 1 0 0 0 0 104" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 67 105 A 1 1 0 0 1 68 104" fill="none" stroke={lineSoft} strokeWidth={0.35} />
    </svg>
  );
}

/** 竖版整场(viewBox "0 0 68 105",2026-08-25 新增)——纵向双队阵容图专用
 * (对齐 FotMob 原生 APP 恒纵向布局,两队上下各半、面对面)。近端(y=105)
 * 图元与 PortraitPitch 完全同一套;远端(y=0)那组禁区/球门/罚球弧是独立
 * 镜像出来的。⚠️ 远端罚球弧的 sweep-flag **不能**靠转置/取反近端的值推——
 * 该文件既有纪律(转置=镜像=手性反转);已用独立脚本按 W3C 圆弧终点参数化
 * 逐点模拟验证:`M ARC_X0 16.5 A r r 0 0 0 ARC_X1 16.5` 的圆心正落在远端
 * 罚球点 (34,11)、中间采样点 y∈[18.03,20.15] 朝中场鼓包(2026-08-25,四个
 * sweep/方向组合全部模拟过,仅此一组既朝外又同心)。 */
function PortraitFullPitch({ variant }: { variant: PitchVariant }) {
  const { line, lineSoft } = pitchColors(variant);
  return (
    <svg
      viewBox="0 0 68 105"
      preserveAspectRatio="none"
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    >
      {mowStripesPortrait(variant)}

      {/* 外边界 */}
      <rect x={0} y={0} width={68} height={105} fill="none" stroke={line} strokeWidth={0.4} />
      {/* 半场线 + 完整中圈 + 中点 */}
      <line x1={0} y1={52.5} x2={68} y2={52.5} stroke={line} strokeWidth={0.4} />
      <circle cx={34} cy={52.5} r={ARC_R} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={34} cy={52.5} r={0.35} fill={line} />

      {/* 远端禁区(球门在 y=0,主队门将端) */}
      <rect x={BOX_Y0} y={0} width={BOX_H} height={BOX_W} fill="none" stroke={line} strokeWidth={0.4} />
      <rect x={GOAL_BOX_Y0} y={0} width={GOAL_BOX_H} height={GOAL_BOX_W} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={34} cy={SPOT_X} r={0.35} fill={line} />
      <path
        d={`M ${ARC_X0.toFixed(3)} ${BOX_W} A ${ARC_R} ${ARC_R} 0 0 0 ${ARC_X1.toFixed(3)} ${BOX_W}`}
        fill="none"
        stroke={line}
        strokeWidth={0.4}
      />
      <line x1={GOAL_Y0} y1={0} x2={GOAL_Y1} y2={0} stroke={line} strokeWidth={1.3} strokeLinecap="round" />

      {/* 近端禁区(球门在 y=105,与 PortraitPitch 同一组图元) */}
      <rect x={BOX_Y0} y={105 - BOX_W} width={BOX_H} height={BOX_W} fill="none" stroke={line} strokeWidth={0.4} />
      <rect x={GOAL_BOX_Y0} y={105 - GOAL_BOX_W} width={GOAL_BOX_H} height={GOAL_BOX_W} fill="none" stroke={line} strokeWidth={0.4} />
      <circle cx={34} cy={105 - SPOT_X} r={0.35} fill={line} />
      <path
        d={`M ${ARC_X1.toFixed(3)} ${105 - BOX_W} A ${ARC_R} ${ARC_R} 0 0 0 ${ARC_X0.toFixed(3)} ${105 - BOX_W}`}
        fill="none"
        stroke={line}
        strokeWidth={0.4}
      />
      <line x1={GOAL_Y0} y1={105} x2={GOAL_Y1} y2={105} stroke={line} strokeWidth={1.3} strokeLinecap="round" />

      {/* 四角角球弧(角点几何与球场尺寸无关,y=0 端两条取自 landscape 左右
          上角的同款 path,y=105 端两条与 PortraitPitch 相同) */}
      <path d="M 1 0 A 1 1 0 0 1 0 1" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 67 0 A 1 1 0 0 0 68 1" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 1 105 A 1 1 0 0 0 0 104" fill="none" stroke={lineSoft} strokeWidth={0.35} />
      <path d="M 67 105 A 1 1 0 0 1 68 104" fill="none" stroke={lineSoft} strokeWidth={0.35} />
    </svg>
  );
}

export function FootballPitchBackground({
  orientation = "landscape",
  variant = "turf",
}: {
  /** "portrait" 是**半场**(单队战术板);"portrait-full" 是竖版**整场**
   * (纵向双队阵容图)——是两个不同的 viewBox,已有测试硬断言了前者,
   * 不要动 portrait 去凑整场。 */
  orientation?: "landscape" | "portrait" | "portrait-full";
  /** "neutral":射门落点图专用中性球场(2026-08-24);"lineup"/"probable":
   * 阵容球场确认/预计两档(2026-08-25,FotMob 主题同款,深浅随 CSS 变量)。
   * 默认 "turf"(绿茵)。 */
  variant?: PitchVariant;
} = {}) {
  if (orientation === "portrait-full") return <PortraitFullPitch variant={variant} />;
  return orientation === "portrait" ? (
    <PortraitPitch variant={variant} />
  ) : (
    <LandscapePitch variant={variant} />
  );
}
