/**
 * 共享球场底图(SVG):射门落点图(ShotMapChart)、阵型图(PitchFormation)、
 * 预计首发单队球场(ProjectedLineupSection)此前各自用几个绝对定位 `<div>`/
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
 * 改成贴边界画一条加粗线代替,不牺牲对齐正确性。PitchFormation 用这个朝向
 * (两队合画在一块横向球场上,主队攻右、客队攻左)。
 *
 * `orientation="portrait"`:viewBox "0 0 68 105",单队竖版全场(球门在 y=0
 * 一端,门将在这一端;若整队画在球场另一端见下)。ProjectedLineupSection
 * 用这个朝向(单队分行摆位,不含 ECharts,没有坐标对齐约束,可以自由选真实
 * 竖版比例——因此容器的 aspect-ratio 也应改成真实的 68/105,不能沿用旧的
 * 近似值 5/7,否则 SVG 的 preserveAspectRatio="none" 会把球场轻微压扁/拉宽)。
 * 禁区弧线两个方向的 sweep-flag 不是把 landscape 的 x/y 直接对调就对——
 * x↔y 互换是一次镜像(手性反转),原样搬会让罚球弧朝禁区里面鼓包而不是朝外,
 * 已用独立脚本逐点模拟验证过 near/far 两端弧线的真实落点(而不是仅凭手算
 * 三角函数),下方 sweep 值均已验证正确。
 *
 * 真实比例来源(与 miaomiaodi.cc `FormationPitch.tsx`/`FotmobFinalClient.tsx`
 * 的 PitchLines、miaomiaodi.vip `InteractiveShotMap.tsx` 独立核对一致):
 * 禁区 40.32m×16.5m、小禁区 18.32m×5.5m、球门 7.32m、罚球点距底线 11m、
 * 罚球弧半径 9.15m、角球弧半径 1m。
 *
 * 颜色是硬编码常量,不随亮/暗色主题切换——草坪就是草坪,不因为站点在深色
 * 模式就变成别的颜色(与 ShotMapChart.tsx 里射门点颜色 GOLD/INK2 硬编码是
 * 同一惯例)。刻意不用光晕/毛玻璃(app/globals.css 深色模式注释:"仍是体育
 * 数据编辑部,不使用霓虹光晕或玻璃拟态")——只用交替色块画草坪割纹,不加
 * shadowBlur/发光描边。
 */

const LINE = "rgba(255,255,255,0.62)";
const LINE_SOFT = "rgba(255,255,255,0.4)";
const TURF_A = "#2c8a57";
const TURF_B = "#297f50";

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
 * 不叠加渐变/光晕。 */
function mowStripesLandscape() {
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

function mowStripesPortrait() {
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

function LandscapePitch() {
  return (
    <svg
      viewBox="0 0 105 68"
      preserveAspectRatio="none"
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    >
      {mowStripesLandscape()}

      {/* 外边界 */}
      <rect x={0} y={0} width={105} height={68} fill="none" stroke={LINE} strokeWidth={0.4} />
      {/* 半场线 */}
      <line x1={52.5} y1={0} x2={52.5} y2={68} stroke={LINE} strokeWidth={0.4} />
      {/* 中圈 + 中点 */}
      <circle cx={52.5} cy={34} r={ARC_R} fill="none" stroke={LINE} strokeWidth={0.4} />
      <circle cx={52.5} cy={34} r={0.35} fill={LINE} />

      {/* 左禁区 */}
      <rect x={0} y={BOX_Y0} width={BOX_W} height={BOX_H} fill="none" stroke={LINE} strokeWidth={0.4} />
      <rect x={0} y={GOAL_BOX_Y0} width={GOAL_BOX_W} height={GOAL_BOX_H} fill="none" stroke={LINE} strokeWidth={0.4} />
      <circle cx={SPOT_X} cy={34} r={0.35} fill={LINE} />
      <path
        d={`M ${BOX_W} ${ARC_Y0.toFixed(3)} A ${ARC_R} ${ARC_R} 0 0 1 ${BOX_W} ${ARC_Y1.toFixed(3)}`}
        fill="none"
        stroke={LINE}
        strokeWidth={0.4}
      />
      {/* 左球门(贴边界加粗线,不探出边界——保持 ECharts 数据域对齐,见模块注释) */}
      <line x1={0} y1={GOAL_Y0} x2={0} y2={GOAL_Y1} stroke={LINE} strokeWidth={1.3} strokeLinecap="round" />

      {/* 右禁区(左侧镜像 x → 105-x) */}
      <rect x={105 - BOX_W} y={BOX_Y0} width={BOX_W} height={BOX_H} fill="none" stroke={LINE} strokeWidth={0.4} />
      <rect x={105 - GOAL_BOX_W} y={GOAL_BOX_Y0} width={GOAL_BOX_W} height={GOAL_BOX_H} fill="none" stroke={LINE} strokeWidth={0.4} />
      <circle cx={105 - SPOT_X} cy={34} r={0.35} fill={LINE} />
      <path
        d={`M ${105 - BOX_W} ${ARC_Y1.toFixed(3)} A ${ARC_R} ${ARC_R} 0 0 1 ${105 - BOX_W} ${ARC_Y0.toFixed(3)}`}
        fill="none"
        stroke={LINE}
        strokeWidth={0.4}
      />
      <line x1={105} y1={GOAL_Y0} x2={105} y2={GOAL_Y1} stroke={LINE} strokeWidth={1.3} strokeLinecap="round" />

      {/* 四角角球弧(半径 1m,与边界圆弧同心于四个角点) */}
      <path d="M 1 0 A 1 1 0 0 1 0 1" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
      <path d="M 104 0 A 1 1 0 0 0 105 1" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
      <path d="M 1 68 A 1 1 0 0 0 0 67" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
      <path d="M 104 68 A 1 1 0 0 1 105 67" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
    </svg>
  );
}

/** 竖版单队全场(viewBox "0 0 68 105")。球门分别在 y=0("近端")与 y=105
 * ("远端"),两端禁区互为上下镜像。禁区弧线的 sweep-flag 不是把 landscape
 * 直接转置——转置是镜像、会反转手性,近端/远端各自的 sweep 值都用独立脚本
 * 逐点模拟验证过朝外鼓包(而不是仅凭三角函数手算),见模块顶部注释。 */
function PortraitPitch() {
  return (
    <svg
      viewBox="0 0 68 105"
      preserveAspectRatio="none"
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    >
      {mowStripesPortrait()}

      {/* 外边界 */}
      <rect x={0} y={0} width={68} height={105} fill="none" stroke={LINE} strokeWidth={0.4} />
      {/* 半场线 */}
      <line x1={0} y1={52.5} x2={68} y2={52.5} stroke={LINE} strokeWidth={0.4} />
      {/* 中圈 + 中点 */}
      <circle cx={34} cy={52.5} r={ARC_R} fill="none" stroke={LINE} strokeWidth={0.4} />
      <circle cx={34} cy={52.5} r={0.35} fill={LINE} />

      {/* 近端禁区(球门在 y=0) */}
      <rect x={BOX_Y0} y={0} width={BOX_H} height={BOX_W} fill="none" stroke={LINE} strokeWidth={0.4} />
      <rect x={GOAL_BOX_Y0} y={0} width={GOAL_BOX_H} height={GOAL_BOX_W} fill="none" stroke={LINE} strokeWidth={0.4} />
      <circle cx={34} cy={SPOT_X} r={0.35} fill={LINE} />
      <path
        d={`M ${ARC_X1.toFixed(3)} ${BOX_W} A ${ARC_R} ${ARC_R} 0 0 1 ${ARC_X0.toFixed(3)} ${BOX_W}`}
        fill="none"
        stroke={LINE}
        strokeWidth={0.4}
      />
      <line x1={GOAL_Y0} y1={0} x2={GOAL_Y1} y2={0} stroke={LINE} strokeWidth={1.3} strokeLinecap="round" />

      {/* 远端禁区(球门在 y=105,近端上下镜像) */}
      <rect x={BOX_Y0} y={105 - BOX_W} width={BOX_H} height={BOX_W} fill="none" stroke={LINE} strokeWidth={0.4} />
      <rect x={GOAL_BOX_Y0} y={105 - GOAL_BOX_W} width={GOAL_BOX_H} height={GOAL_BOX_W} fill="none" stroke={LINE} strokeWidth={0.4} />
      <circle cx={34} cy={105 - SPOT_X} r={0.35} fill={LINE} />
      <path
        d={`M ${ARC_X1.toFixed(3)} ${105 - BOX_W} A ${ARC_R} ${ARC_R} 0 0 0 ${ARC_X0.toFixed(3)} ${105 - BOX_W}`}
        fill="none"
        stroke={LINE}
        strokeWidth={0.4}
      />
      <line x1={GOAL_Y0} y1={105} x2={GOAL_Y1} y2={105} stroke={LINE} strokeWidth={1.3} strokeLinecap="round" />

      {/* 四角角球弧 */}
      <path d="M 1 0 A 1 1 0 0 1 0 1" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
      <path d="M 67 0 A 1 1 0 0 0 68 1" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
      <path d="M 1 105 A 1 1 0 0 0 0 104" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
      <path d="M 67 105 A 1 1 0 0 1 68 104" fill="none" stroke={LINE_SOFT} strokeWidth={0.35} />
    </svg>
  );
}

export function FootballPitchBackground({
  orientation = "landscape",
}: {
  orientation?: "landscape" | "portrait";
} = {}) {
  return orientation === "portrait" ? <PortraitPitch /> : <LandscapePitch />;
}
