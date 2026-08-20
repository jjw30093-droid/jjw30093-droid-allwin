/**
 * 共享球场底图(SVG):射门落点图(ShotMapChart)与阵型图(PitchFormation)
 * 此前各自用 4 个绝对定位 `<div>`(半场线/中圈/两个禁区矩形)画一个残缺球场
 * ——没有小禁区、罚球点、罚球弧、球门、角球弧,CSS 逐行重复在两个文件里。
 * 这里合并成一份按真实 FIFA 比例(米制)画的完整球场,两处调用方共用。
 *
 * viewBox 固定 "0 0 105 68"(米),与 ShotMapChart 的 PITCH_LEN/PITCH_WID 及
 * ECharts scatter 的坐标域(xAxis/yAxis min:0 max:105/68,grid 零边距)完全
 * 对齐——射门点因此天然落在正确的球场位置上,不需要额外换算。**不要**扩大
 * viewBox 去画"探出边界"的球门纵深(cc/vip 参考实现都这样画),那会让这份
 * SVG 的像素边界和 ECharts 数据域的 0/105 边界错开,球场画完了但射门点跟
 * 线条对不上——球门改成贴边界画一条加粗线代替,不牺牲对齐正确性。
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

/** 8 条交替色竖纹,只是平色块,不叠加渐变/光晕。 */
function mowStripes() {
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

export function FootballPitchBackground() {
  return (
    <svg
      viewBox="0 0 105 68"
      preserveAspectRatio="none"
      aria-hidden
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
    >
      {mowStripes()}

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
