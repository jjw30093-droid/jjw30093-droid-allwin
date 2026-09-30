// 模拟结果分享图:浏览器端 Canvas 2D 绘制,不走服务器渲染。尺寸 1080×1350 与 1080×1920。
// 版式固定浅色(与站点主题无关);中文用站点自托管的 Noto Sans SC(按实际要画的文字加载对应分片)。
// "模拟比赛"出现两处:顶部色条,以及紧贴比分右侧的标签——任何裁切都不会让比分和"模拟"分离。

import { create as createQr } from "qrcode";
import { contrastRatioHex, nudgeForContrast } from "@/components/charts/colorContrast";
import { FOCUS_LABEL, rarityTag, type SimEvent } from "./engine";
import { ahText, type ResultSnapshot } from "./snapshot";

export type ShareSize = 1350 | 1920;
export const SHARE_WIDTH = 1080;

const C = {
  bg: "#f4f6f6",
  card: "#ffffff",
  ink: "#0d2c3d",
  ink2: "#40535d",
  ink3: "#5a6b73",
  border: "#d8e0e0",
  gold: "#e9c037",
  onGold: "#2a1f05",
  draw: "#b8c4c6",
  pitch: "#e6eeea",
  pitchLine: "#ffffff",
  fallback: ["#087e78", "#b45309"] as [string, string],
};

export interface ShareFonts {
  /** 中文字体栈(站点 body 的 font-family) */
  cn: string;
  /** 数字字体栈(--font-latin),比分用 */
  latin: string;
}

export interface ShareImageInput {
  snap: ResultSnapshot;
  size: ShareSize;
  teamColors: [string, string];
  fonts: ShareFonts;
  siteName: string;
  siteUrl: string;
}

/** 队色落在白色卡片上:对比度不足时在小预算内微调,仍不够就用兜底色(与站内图表同一口径)。 */
export function shareTeamColors(colors: [string, string]): [string, string] {
  return colors.map((c, i) => {
    if (!/^#[0-9a-f]{6}$/i.test(c)) return C.fallback[i];
    return contrastRatioHex(c, C.card) >= 3 ? c : (nudgeForContrast(c, C.card) ?? C.fallback[i]);
  }) as [string, string];
}

/** 页面实际使用的字体栈:中文取 body,数字取 --font-latin。 */
export function pageFonts(): ShareFonts {
  const cn = getComputedStyle(document.body).fontFamily || '"PingFang SC", "Microsoft YaHei", sans-serif';
  const latinVar = getComputedStyle(document.documentElement).getPropertyValue("--font-latin").trim();
  return { cn, latin: latinVar ? `${latinVar}, ${cn}` : cn };
}

// ------------------------------------------------------------------ 版式
export interface Layout {
  pad: number;
  gap: number;
  bar: number;
  score: number;
  lineups: number;
  xg: number;
  wdl: number;
  footer: number;
  maxScorers: number;
  s: number; // 字号倍率
}

export function layoutFor(size: ShareSize): Layout {
  if (size === 1920) {
    const l = { pad: 48, gap: 22, bar: 112, score: 326, xg: 300, wdl: 244, footer: 200, maxScorers: 4, s: 1.15 };
    const used = l.bar + l.score + l.xg + l.wdl + l.footer + l.gap * 6 + 8;
    return { ...l, lineups: size - used };
  }
  const l = { pad: 40, gap: 12, bar: 80, score: 280, xg: 170, wdl: 184, footer: 150, maxScorers: 3, s: 1 };
  const used = l.bar + l.score + l.xg + l.wdl + l.footer + l.gap * 6 + 4;
  return { ...l, lineups: size - used };
}

// ------------------------------------------------------------------ 绘制工具
type Ctx = CanvasRenderingContext2D;

function font(weight: number, px: number, family: string): string {
  return `${weight} ${Math.round(px)}px ${family}`;
}

function fit(ctx: Ctx, text: string, maxW: number): string {
  if (ctx.measureText(text).width <= maxW) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > maxW) t = t.slice(0, -1);
  return `${t}…`;
}

function roundRect(ctx: Ctx, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function card(ctx: Ctx, x: number, y: number, w: number, h: number) {
  ctx.fillStyle = C.card;
  roundRect(ctx, x, y, w, h, 18);
  ctx.fill();
  ctx.strokeStyle = C.border;
  ctx.lineWidth = 2;
  ctx.stroke();
}

function pill(ctx: Ctx, text: string, x: number, cy: number, px: number, fam: string, bg: string, fg: string, align: "left" | "right" = "left"): number {
  ctx.font = font(900, px, fam);
  const w = ctx.measureText(text).width + px * 0.9;
  const h = px * 1.55;
  const x0 = align === "left" ? x : x - w;
  ctx.fillStyle = bg;
  roundRect(ctx, x0, cy - h / 2, w, h, h / 2);
  ctx.fill();
  ctx.fillStyle = fg;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, x0 + w / 2, cy + px * 0.04);
  return w;
}

function goalLine(e: SimEvent): string {
  const tag = e.channel === "owngoal" ? "(乌龙)" : e.channel === "penalty" ? "(点球)" : "";
  return `${e.clock} ${e.playerName ?? ""}${tag}`;
}

/** 分享图里会画到的全部文字(用于按需加载字体分片)。 */
export function shareTexts(input: ShareImageInput): string {
  const { snap } = input;
  const parts: string[] = [
    "模拟比赛 SIMULATED 非真实比赛 模拟 种子 补时 分钟 累计 xG 赛跑 半场 次模拟 胜平负 胜 平 主让 主受让 平手 模拟让球 模拟大小球 主队让 主队受让 预计进球 编号 稀有度 侧重点 无 休息不足3天 模型 参数 模拟结果,仅供娱乐 扫码自己模拟一场 等共球 乌龙 点球 …:·0123456789.+'%/",
    input.siteName,
    input.siteUrl,
    snap.modelVersion,
    snap.paramsDate,
  ];
  for (const t of snap.teams) {
    parts.push(t.name, t.formation, ...t.lineup.map((l) => `${l.name}${l.num ?? ""}`), ...t.focuses.map((f) => FOCUS_LABEL[f]));
  }
  for (const e of snap.single.events) if (e.kind === "goal") parts.push(goalLine(e));
  return parts.join(" ");
}

// ------------------------------------------------------------------ 可测试的排版纯函数
/** 文字宽度测量(绘制时用 canvas measureText;测试里可注入近似测量)。 */
export type Measure = (text: string, px: number) => number;

export const NAME_GAP = 6;
export const NAME_MIN_PX = 13;

/**
 * 同一排球员名字:先用该排统一字号放下全部名字(相邻名字之间至少 NAME_GAP、不越出球场左右边);
 * 放不下就逐步缩小该排字号(下限 NAME_MIN_PX);到下限仍放不下,再按各自可用宽度截断并加省略号。
 * 名字以徽章圆心为中心,所以某个名字可用宽度 = min(到左右相邻圆心的距离 − 间距, 到左右边界距离 × 2)。
 */
export function fitRowNames(
  items: { cx: number; name: string }[],
  opts: { left: number; right: number; basePx: number; minPx?: number; gap?: number; measure: Measure },
): { px: number; texts: string[] } {
  const gap = opts.gap ?? NAME_GAP;
  const minPx = opts.minPx ?? NAME_MIN_PX;
  const order = items.map((_, i) => i).sort((a, b) => items[a].cx - items[b].cx);
  const fits = (px: number) => {
    const w = order.map((i) => opts.measure(items[i].name, px));
    for (let k = 0; k < order.length; k++) {
      const c = items[order[k]].cx;
      if (c - w[k] / 2 < opts.left || c + w[k] / 2 > opts.right) return false;
      if (k > 0 && (w[k - 1] + w[k]) / 2 + gap > c - items[order[k - 1]].cx) return false;
    }
    return true;
  };
  for (let px = opts.basePx; px >= minPx - 1e-9; px -= 0.5) {
    if (fits(px)) return { px, texts: items.map((it) => it.name) };
  }
  const px = minPx;
  const texts = items.map((it) => it.name);
  order.forEach((i, k) => {
    const c = items[i].cx;
    let maxW = Math.min(2 * (c - opts.left), 2 * (opts.right - c));
    if (k > 0) maxW = Math.min(maxW, c - items[order[k - 1]].cx - gap);
    if (k < order.length - 1) maxW = Math.min(maxW, items[order[k + 1]].cx - c - gap);
    let t = items[i].name;
    if (opts.measure(t, px) > maxW) {
      while (t.length > 1 && opts.measure(`${t}…`, px) > maxW) t = t.slice(0, -1);
      t = `${t}…`;
    }
    texts[i] = t;
  });
  return { px, texts };
}

export interface LaidOutPlayer {
  cx: number;
  cy: number;
  r: number;
  num: string | null;
  text: string;
  px: number;
  nameY: number;
}

/** 阵容图:纵向按"排"均匀分布(模板 y 值相近的归为同一排),每排单独决定名字字号。 */
export function lineupLayout(
  lineup: ResultSnapshot["teams"][number]["lineup"],
  box: { x: number; py: number; w: number; ph: number },
  s: number,
  measure: Measure,
): LaidOutPlayer[] {
  const r = 18 * s;
  const rowKey = (y: number) => Math.round(y * 20);
  const rows = [...new Set(lineup.map((p) => rowKey(p.y)))].sort((a, b) => a - b);
  const top = box.py + r + 12;
  const bottom = box.py + box.ph - r - 30 * s;
  const rowY = (k: number) => (rows.length === 1 ? (top + bottom) / 2 : bottom - (k / (rows.length - 1)) * (bottom - top));
  const out: LaidOutPlayer[] = lineup.map((p) => {
    const cy = rowY(rows.indexOf(rowKey(p.y)));
    return { cx: box.x + 30 + p.x * (box.w - 60), cy, r, num: p.num, text: p.name, px: 17 * Math.min(s, 1.05), nameY: cy + r + 20 * s };
  });
  rows.forEach((key) => {
    const idx = lineup.map((p, i) => (rowKey(p.y) === key ? i : -1)).filter((i) => i >= 0);
    const fitted = fitRowNames(
      idx.map((i) => ({ cx: out[i].cx, name: lineup[i].name })),
      { left: box.x + 4, right: box.x + box.w - 4, basePx: 17 * Math.min(s, 1.05), measure },
    );
    idx.forEach((i, k) => {
      out[i].px = fitted.px;
      out[i].text = fitted.texts[k];
    });
  });
  return out;
}

/** xG 赛跑图末端两个数值标签:纵向距离小于 minGap(字高)时以两者中点为轴上下错开,较大值在上;不越出 [lo, hi]。 */
export function separateEndLabels(y: [number, number], minGap: number, lo: number, hi: number): [number, number] {
  if (Math.abs(y[0] - y[1]) >= minGap) return y;
  const upper = y[0] <= y[1] ? 0 : 1; // y 越小越靠上 = 数值越大;相等时主队在上
  let mid = (y[0] + y[1]) / 2;
  mid = Math.min(Math.max(mid, lo + minGap / 2), hi - minGap / 2);
  const out: [number, number] = [0, 0];
  out[upper] = mid - minGap / 2;
  out[1 - upper] = mid + minGap / 2;
  return out;
}

// ------------------------------------------------------------------ 分区
function drawBar(ctx: Ctx, L: Layout, f: ShareFonts, siteName: string) {
  ctx.fillStyle = C.gold;
  ctx.fillRect(0, 0, SHARE_WIDTH, L.bar);
  ctx.fillStyle = C.onGold;
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.font = font(900, 44 * L.s, f.cn);
  ctx.fillText("模拟比赛", L.pad, L.bar / 2);
  const w = ctx.measureText("模拟比赛").width;
  ctx.font = font(500, 20 * L.s, f.cn);
  ctx.fillText("SIMULATED · 非真实比赛", L.pad + w + 18, L.bar / 2 + 3);
  ctx.textAlign = "right";
  ctx.font = font(900, 24 * L.s, f.cn);
  ctx.fillText(siteName, SHARE_WIDTH - L.pad, L.bar / 2);
}

function drawScore(ctx: Ctx, L: Layout, f: ShareFonts, snap: ResultSnapshot, colors: [string, string], y: number) {
  const x = L.pad;
  const w = SHARE_WIDTH - 2 * L.pad;
  card(ctx, x, y, w, L.score);
  const colW = (w - 40) / 2;
  const colX = [x + 20 + colW / 2, x + 20 + colW + colW / 2];
  // 队名
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "center";
  ctx.font = font(900, 38 * L.s, f.cn);
  snap.teams.forEach((t, i) => {
    ctx.fillStyle = colors[i];
    ctx.fillText(fit(ctx, t.name, colW - 30), colX[i], y + 52 * L.s);
  });
  // 比分 + 紧贴右侧的"模拟"标签
  const scoreText = `${snap.single.score[0]} : ${snap.single.score[1]}`;
  const cy = y + 124 * L.s;
  ctx.font = font(700, 104 * L.s, f.latin);
  const sw = ctx.measureText(scoreText).width;
  ctx.fillStyle = C.ink;
  ctx.textBaseline = "middle";
  ctx.fillText(scoreText, SHARE_WIDTH / 2, cy + 4);
  pill(ctx, "模拟", SHARE_WIDTH / 2 + sw / 2 + 16, cy, 28 * L.s, f.cn, C.gold, C.onGold);
  // 进球者
  ctx.textBaseline = "alphabetic";
  ctx.font = font(500, 23 * L.s, f.cn);
  const lh = 29 * L.s;
  const top = y + 200 * L.s;
  ([0, 1] as const).forEach((i) => {
    const goals = snap.single.events.filter((e) => e.kind === "goal" && e.team === i);
    const shown = goals.length > L.maxScorers ? goals.slice(0, L.maxScorers - 1) : goals;
    const lines = shown.map(goalLine);
    if (goals.length > shown.length) lines.push(`…共 ${goals.length} 球`);
    ctx.fillStyle = C.ink2;
    lines.forEach((ln, k) => ctx.fillText(fit(ctx, ln, colW - 20), colX[i], top + k * lh));
  });
}

function drawPitch(ctx: Ctx, L: Layout, f: ShareFonts, team: ResultSnapshot["teams"][number], color: string, x: number, y: number, w: number, h: number) {
  // 标题:队名 · 阵型
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.font = font(900, 26 * L.s, f.cn);
  ctx.fillStyle = color;
  ctx.fillText(fit(ctx, `${team.name} · ${team.formation}`, w), x, y + 28 * L.s);
  const focusH = 34 * L.s;
  const py = y + 44 * L.s;
  const ph = h - (py - y) - focusH;
  // 球场(竖向,进攻朝上)
  ctx.fillStyle = C.pitch;
  roundRect(ctx, x, py, w, ph, 14);
  ctx.fill();
  ctx.strokeStyle = C.pitchLine;
  ctx.lineWidth = 3;
  const m = 12;
  ctx.strokeRect(x + m, py + m, w - 2 * m, ph - 2 * m);
  ctx.beginPath();
  ctx.moveTo(x + m, py + ph / 2);
  ctx.lineTo(x + w - m, py + ph / 2);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(x + w / 2, py + ph / 2, Math.min(w, ph) * 0.12, 0, Math.PI * 2);
  ctx.stroke();
  const boxW = w * 0.5;
  const boxH = ph * 0.14;
  ctx.strokeRect(x + (w - boxW) / 2, py + m, boxW, boxH);
  ctx.strokeRect(x + (w - boxW) / 2, py + ph - m - boxH, boxW, boxH);
  // 球员:号码徽章 + 中文名(位置与每排字号见 lineupLayout)
  const measure: Measure = (text, px) => {
    ctx.font = font(500, px, f.cn);
    return ctx.measureText(text).width;
  };
  const lay = lineupLayout(team.lineup, { x, py, w, ph }, L.s, measure);
  for (const p of lay) {
    ctx.beginPath();
    ctx.arc(p.cx, p.cy, p.r, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.lineWidth = 4;
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.fillStyle = C.ink;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = font(700, 17 * L.s, f.latin);
    ctx.fillText(p.num ?? "–", p.cx, p.cy + 1);
    ctx.textBaseline = "alphabetic";
    ctx.font = font(500, p.px, f.cn);
    ctx.fillStyle = C.ink;
    ctx.fillText(p.text, p.cx, p.nameY);
  }
  // 侧重点
  ctx.textAlign = "left";
  ctx.font = font(500, 20 * L.s, f.cn);
  ctx.fillStyle = C.ink2;
  const foc = team.focuses.length ? team.focuses.map((k) => FOCUS_LABEL[k]).join("、") : "无";
  ctx.fillText(fit(ctx, `侧重点:${foc}${team.shortRest ? " · 休息不足3天" : ""}`, w), x, py + ph + 28 * L.s);
}

function drawLineups(ctx: Ctx, L: Layout, f: ShareFonts, snap: ResultSnapshot, colors: [string, string], y: number) {
  const x = L.pad;
  const w = SHARE_WIDTH - 2 * L.pad;
  card(ctx, x, y, w, L.lineups);
  const inner = 20;
  const colW = (w - inner * 3) / 2;
  snap.teams.forEach((t, i) => drawPitch(ctx, L, f, t, colors[i], x + inner + i * (colW + inner), y + 12, colW, L.lineups - 24));
}

function drawXg(ctx: Ctx, L: Layout, f: ShareFonts, snap: ResultSnapshot, colors: [string, string], y: number) {
  const x = L.pad;
  const w = SHARE_WIDTH - 2 * L.pad;
  card(ctx, x, y, w, L.xg);
  const { single } = snap;
  const total = [0, 0];
  const series: [number, number][][] = [[[0, 0]], [[0, 0]]];
  const goals: { t: number; team: 0 | 1; v: number }[] = [];
  for (const e of single.events) {
    if (e.xg === undefined && e.kind !== "goal") continue;
    if (e.xg !== undefined) {
      total[e.team] += e.xg;
      series[e.team].push([e.tick + 1, total[e.team]]);
    }
    if (e.kind === "goal") goals.push({ t: e.tick + 1, team: e.team, v: total[e.team] });
  }
  const T = single.totalTicks;
  series.forEach((s, i) => s.push([T, total[i]]));
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.font = font(900, 22 * L.s, f.cn);
  ctx.fillStyle = C.ink;
  ctx.fillText("模拟 xG 赛跑", x + 20, y + 34 * L.s);
  ctx.font = font(500, 19 * L.s, f.cn);
  ctx.fillStyle = C.ink3;
  ctx.textAlign = "right";
  ctx.fillText(`累计 xG ${total[0].toFixed(2)} : ${total[1].toFixed(2)}`, x + w - 20, y + 34 * L.s);
  const cx0 = x + 24;
  const cx1 = x + w - 70;
  const cy0 = y + 50 * L.s;
  const cy1 = y + L.xg - 34 * L.s;
  const ymax = Math.max(1, total[0], total[1]) * 1.1;
  const px = (t: number) => cx0 + (t / T) * (cx1 - cx0);
  const py = (v: number) => cy1 - (v / ymax) * (cy1 - cy0);
  // 坐标
  ctx.strokeStyle = C.border;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx0, cy1);
  ctx.lineTo(cx1, cy1);
  ctx.stroke();
  ctx.setLineDash([6, 6]);
  ctx.beginPath();
  ctx.moveTo(px(single.halfTimeTick), cy0);
  ctx.lineTo(px(single.halfTimeTick), cy1);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = C.ink3;
  ctx.font = font(500, 17 * L.s, f.cn);
  ctx.textAlign = "center";
  ctx.fillText("0'", cx0 + 8, cy1 + 24 * L.s);
  ctx.fillText("半场", px(single.halfTimeTick), cy1 + 24 * L.s);
  ctx.fillText(`90+${single.stoppage[1]}'`, cx1 - 10, cy1 + 24 * L.s);
  // 阶梯线
  series.forEach((s, i) => {
    ctx.strokeStyle = colors[i];
    ctx.lineWidth = 4;
    ctx.beginPath();
    s.forEach(([t, v], k) => {
      if (k === 0) ctx.moveTo(px(t), py(v));
      else {
        ctx.lineTo(px(t), py(s[k - 1][1]));
        ctx.lineTo(px(t), py(v));
      }
    });
    ctx.stroke();
  });
  // 末端数值标签:相距不足一个字高时上下错开
  const labelPx = 20 * L.s;
  const ly = separateEndLabels([py(total[0]), py(total[1])], labelPx * 1.15, cy0, cy1);
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.font = font(700, labelPx, f.latin);
  ([0, 1] as const).forEach((i) => {
    ctx.fillStyle = colors[i];
    ctx.fillText(total[i].toFixed(2), cx1 + 8, ly[i]);
  });
  for (const g of goals) {
    ctx.beginPath();
    ctx.arc(px(g.t), py(g.v), 8, 0, Math.PI * 2);
    ctx.fillStyle = colors[g.team];
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
  }
}

function drawWdl(ctx: Ctx, L: Layout, f: ShareFonts, snap: ResultSnapshot, colors: [string, string], y: number) {
  const x = L.pad;
  const w = SHARE_WIDTH - 2 * L.pad;
  card(ctx, x, y, w, L.wdl);
  const { many } = snap;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.font = font(900, 22 * L.s, f.cn);
  ctx.fillStyle = C.ink;
  ctx.fillText(`${many.runs} 次模拟 · 胜平负`, x + 20, y + 34 * L.s);
  const bx = x + 20;
  const bw = w - 40;
  const by = y + 50 * L.s;
  const bh = 30 * L.s;
  const segs: [number, string][] = [[many.pHome, colors[0]], [many.pDraw, C.draw], [many.pAway, colors[1]]];
  ctx.save();
  roundRect(ctx, bx, by, bw, bh, bh / 2);
  ctx.clip();
  let cur = bx;
  for (const [p, c] of segs) {
    ctx.fillStyle = c;
    ctx.fillRect(cur, by, p * bw, bh);
    cur += p * bw;
  }
  ctx.restore();
  const pct = (p: number) => `${(p * 100).toFixed(1)}%`;
  ctx.font = font(500, 20 * L.s, f.cn);
  const ly = by + bh + 27 * L.s;
  ctx.fillStyle = colors[0];
  ctx.textAlign = "left";
  ctx.fillText(`${fit(ctx, snap.teams[0].name, bw / 3 - 90)}胜 ${pct(many.pHome)}`, bx, ly);
  ctx.fillStyle = C.ink2;
  ctx.textAlign = "center";
  ctx.fillText(`平 ${pct(many.pDraw)}`, bx + bw / 2, ly);
  ctx.fillStyle = colors[1];
  ctx.textAlign = "right";
  ctx.fillText(`${fit(ctx, snap.teams[1].name, bw / 3 - 90)}胜 ${pct(many.pAway)}`, bx + bw, ly);
  // 盘口 · 稀有度 · 随机强度
  const items: [string, string][] = [
    ["模拟让球", ahText(many.fairAhLine)],
    ["模拟大小球", String(many.fairOuLine)],
    ["稀有度", rarityTag(many.upset.scoreCount, many.runs)],
    ["预计进球", `${snap.teams[0].expectedGoals.toFixed(1)} : ${snap.teams[1].expectedGoals.toFixed(1)}`],
  ];
  const iy = ly + 38 * L.s;
  const iw = bw / items.length;
  items.forEach(([k, v], i) => {
    const ix = bx + i * iw;
    ctx.textAlign = "left";
    ctx.font = font(500, 17 * L.s, f.cn);
    ctx.fillStyle = C.ink3;
    ctx.fillText(k, ix, iy);
    ctx.font = font(900, 24 * L.s, f.cn);
    ctx.fillStyle = C.ink;
    ctx.fillText(fit(ctx, v, iw - 12), ix, iy + 30 * L.s);
  });
}

function drawFooter(ctx: Ctx, L: Layout, f: ShareFonts, snap: ResultSnapshot, siteName: string, siteUrl: string, y: number) {
  const x = L.pad;
  const w = SHARE_WIDTH - 2 * L.pad;
  const qrSize = L.footer - 20;
  // 二维码(站点地址)
  const qr = createQr(siteUrl, { errorCorrectionLevel: "M" });
  const n = qr.modules.size;
  const quiet = 2;
  const cell = qrSize / (n + quiet * 2);
  const qx = x + w - qrSize;
  const qy = y + 10;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(qx, qy, qrSize, qrSize);
  ctx.fillStyle = C.ink;
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      if (qr.modules.get(r, c)) ctx.fillRect(qx + (c + quiet) * cell, qy + (r + quiet) * cell, Math.ceil(cell), Math.ceil(cell));
    }
  }
  const tw = w - qrSize - 24;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.font = font(900, 30 * L.s, f.cn);
  ctx.fillStyle = C.ink;
  ctx.fillText(siteName, x, y + 40 * L.s);
  ctx.font = font(500, 20 * L.s, f.cn);
  ctx.fillStyle = C.ink2;
  ctx.fillText(fit(ctx, `${siteUrl.replace(/^https?:\/\//, "")} · 扫码自己模拟一场`, tw), x, y + 72 * L.s);
  ctx.fillStyle = C.ink3;
  ctx.font = font(500, 17 * L.s, f.cn);
  ctx.fillText(fit(ctx, `模型 ${snap.modelVersion} · 参数 ${snap.paramsDate.slice(0, 10)} · 编号 ${snap.seed}`, tw), x, y + 102 * L.s);
  ctx.font = font(500, 18 * L.s, f.cn);
  ctx.fillText("模拟结果,仅供娱乐", x, y + 132 * L.s);
}

/** 画到给定 canvas 上(尺寸由函数设置)。调用前应已加载字体(见 renderShareImage)。 */
export function drawShareImage(canvas: HTMLCanvasElement, input: ShareImageInput) {
  const L = layoutFor(input.size);
  canvas.width = SHARE_WIDTH;
  canvas.height = input.size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("浏览器不支持 Canvas 2D");
  const colors = shareTeamColors(input.teamColors);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, SHARE_WIDTH, input.size);
  let y = L.bar;
  drawBar(ctx, L, input.fonts, input.siteName);
  y += L.gap;
  drawScore(ctx, L, input.fonts, input.snap, colors, y);
  y += L.score + L.gap;
  drawLineups(ctx, L, input.fonts, input.snap, colors, y);
  y += L.lineups + L.gap;
  drawXg(ctx, L, input.fonts, input.snap, colors, y);
  y += L.xg + L.gap;
  drawWdl(ctx, L, input.fonts, input.snap, colors, y);
  y += L.wdl + L.gap;
  drawFooter(ctx, L, input.fonts, input.snap, input.siteName, input.siteUrl, y);
}

/** 按需加载字体分片 → 绘制 → PNG Blob。 */
export async function renderShareImage(input: ShareImageInput): Promise<Blob> {
  const text = shareTexts(input);
  const specs = [
    font(500, 20, input.fonts.cn),
    font(900, 20, input.fonts.cn),
    font(700, 20, input.fonts.latin),
  ];
  await Promise.all(specs.map((s) => document.fonts.load(s, text).catch(() => [])));
  await document.fonts.ready;
  const canvas = document.createElement("canvas");
  drawShareImage(canvas, input);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("生成图片失败"))), "image/png"),
  );
}
