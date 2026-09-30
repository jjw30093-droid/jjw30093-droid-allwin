// 文字直播:每个事件一句口语化中文解说,模板生成。
// 句式由"模拟编号 + 事件序号"派生的独立随机数流挑选(eventRng 用途 1),同一次模拟每次打开都是同一句。
// 约束(tests/simulator-commentary.test.ts 逐句检查):不出现"预测""推荐""稳""让球""赢盘",不出现任何博彩公司名。

import type { SimEvent } from "./engine";
import { BODY_NAME, eventRng, missKind, RESULT_BLOCKED, RESULT_GOAL, RESULT_POST, RESULT_SAVED, shotEnd, zoneOf, type BodyPart } from "./shotDetail";
import type { Rng } from "./rng";
import { cumulativeXg } from "./xg";

export const BANNED_WORDS = ["预测", "推荐", "稳", "让球", "赢盘", "盘口", "投注", "下注"];

const pick = <T,>(rng: Rng, xs: T[]): T => xs[Math.floor(rng() * xs.length) % xs.length];

/** 射门位置的说法(进攻方视角) */
export function zonePhrase(x: number, y: number, rng: Rng): string {
  const z = zoneOf(x, y);
  if (z.area === "six") return pick(rng, ["在门前", "在小禁区里", "在球门前咫尺之遥"]);
  if (z.area === "box") {
    if (z.side === "center") return z.distance >= 9.5 && z.distance <= 13 ? "在点球点附近" : pick(rng, ["在禁区中路", "在禁区正面"]);
    return z.side === "left" ? "在禁区左侧" : "在禁区右侧";
  }
  if (z.side === "center") return z.distance <= 24 ? "在禁区弧顶" : "在三十米开外";
  return z.side === "left" ? "在禁区外左侧" : "在禁区外右侧";
}

const BODY_SHOT: Record<BodyPart, string[]> = {
  right: ["右脚打门", "右脚抽射", "右脚低射"],
  left: ["左脚打门", "左脚劲射", "左脚推射"],
  head: ["头球攻门", "甩头一蹭"],
  other: ["身体一碰", "凌空一挡"],
};

const BODY_GOAL: Record<BodyPart, string[]> = {
  right: ["右脚破门", "右脚推射得手", "右脚一脚打进"],
  left: ["左脚破门", "左脚推射入网", "左脚一脚打进"],
  head: ["头球破门", "高高跃起头球建功"],
  other: ["用身体把球挤进球门", "凌空一碰，球进了"],
};

function who(e: SimEvent, names: [string, string]): string {
  return e.playerName || `${names[e.team]}球员`;
}

function scoreText(e: SimEvent, names: [string, string]): string {
  const s = e.score ?? [0, 0];
  return `${names[0]} ${s[0]}:${s[1]} ${names[1]}`;
}

/** 截至第 index 个事件(含),该球员本场的进球数(不含乌龙) */
function goalsBy(events: SimEvent[], index: number, playerId: string | undefined): number {
  if (!playerId) return 0;
  let n = 0;
  for (let i = 0; i <= index; i++) {
    const e = events[i];
    if (e.kind === "goal" && e.channel !== "owngoal" && e.playerId === playerId) n += 1;
  }
  return n;
}

function goalLine(events: SimEvent[], index: number, seed: number, names: [string, string]): string {
  const e = events[index];
  const rng = eventRng(seed, index, 1);
  const p = who(e, names);
  const team = names[e.team];
  const sc = scoreText(e, names);

  if (e.channel === "owngoal") {
    const victim = names[1 - e.team];
    return pick(rng, [
      `乌龙球！${victim}的${p}解围失误，把球送进了自家球门，${sc}。`,
      `哎呀，${p}回追时不慎自摆乌龙，${team}白捡一球，${sc}。`,
      `${p}一脚挡偏，球滚进了自家大门，乌龙！${sc}。`,
    ]);
  }
  if (e.channel === "penalty") {
    return pick(rng, [
      `${p}主罚点球，骗过门将，球进了！${sc}。`,
      `点球命中！${p}一蹴而就，${sc}。`,
      `${p}站上点球点，推射死角，门将毫无办法，${sc}。`,
    ]);
  }

  const sd = e.sd;
  const body = BODY_NAME[sd?.[2] ?? 0] ?? "right";
  const where = sd ? zonePhrase(sd[0], sd[1], rng) : "";
  const finish = pick(rng, BODY_GOAL[body]);
  const n = goalsBy(events, index, e.playerId);
  const tag = n === 2 ? `，梅开二度` : n === 3 ? `，上演帽子戏法` : n > 3 ? `，个人本场第 ${n} 球` : "";

  if (e.channel === "gk_error") {
    return pick(rng, [
      `门将出现失误，${p}抓住机会${where}${finish}${tag}！${sc}。`,
      `送礼了！门将脱手，${p}${where}补射得手${tag}，${sc}。`,
    ]);
  }
  if (e.channel === "counter") {
    return pick(rng, [
      `${team}打出快速反击，${p}${where}${finish}${tag}！${sc}。`,
      `一次漂亮的反击！${p}${where}冷静${finish}${tag}，${sc}。`,
      `断球后直插对方身后，${p}${where}${finish}${tag}，${sc}。`,
    ]);
  }
  if (e.channel === "setpiece" && sd && zoneOf(sd[0], sd[1]).area === "outside") {
    return pick(rng, [
      `${p}${where}主罚任意球，直接破门${tag}！${sc}。`,
      body === "head" ? `任意球直接得分！${p}${where}${finish}${tag}，${sc}。` : `任意球直接得分！${p}${where}一脚兜进球门${tag}，${sc}。`,
    ]);
  }
  if (e.channel === "setpiece") {
    return pick(rng, [
      `定位球机会！${p}${where}${finish}${tag}，${sc}。`,
      `${team}的定位球发出来，${p}${where}抢点${finish}${tag}！${sc}。`,
      `球传到禁区里一阵混战，${p}${where}${finish}${tag}，${sc}。`,
    ]);
  }
  // "起脚"只用于脚下射门,头球 / 其它部位不能这么说
  return pick(rng, [
    `球进了！${p}${where}${finish}${tag}，${sc}。`,
    `${p}${where}${finish}${tag}！${team}取得进球，${sc}。`,
    body === "head" ? `漂亮！${p}${where}抢到落点，${finish}${tag}，${sc}。` : `漂亮！${p}${where}起脚，${finish}${tag}，${sc}。`,
  ]);
}

function shotLine(events: SimEvent[], index: number, seed: number, names: [string, string]): string {
  const e = events[index];
  const rng = eventRng(seed, index, 1);
  const p = who(e, names);
  const sd = e.sd;
  const res = sd?.[3] ?? RESULT_SAVED;
  const body = BODY_NAME[sd?.[2] ?? 0] ?? "right";

  if (e.channel === "penalty") {
    if (res === RESULT_SAVED) return pick(rng, [`${p}的点球被门将扑出！`, `门将猜对了方向，${p}的点球被扑！`]);
    if (res === RESULT_POST) return `${p}罚出的点球击中门柱弹出！`;
    return pick(rng, [`${p}罚丢了点球，球打偏了！`, `点球罚失！${p}这脚踢飞了。`]);
  }

  const where = sd ? zonePhrase(sd[0], sd[1], rng) : "";
  const shot = pick(rng, BODY_SHOT[body]);
  const lead =
    e.channel === "counter"
      ? pick(rng, [`${names[e.team]}打反击，${p}${where}${shot}`, `快速反击中，${p}${where}${shot}`])
      : e.channel === "setpiece"
        ? sd && zoneOf(sd[0], sd[1]).area === "outside"
          ? pick(rng, [`${p}${where}主罚任意球，直接打门`, `${where.replace(/^在/, "")}的任意球，${p}${shot}`])
          : pick(rng, [`定位球传到禁区，${p}${where}${shot}`, `角球开出，${p}${where}${shot}`, `任意球吊进来，${p}${where}${shot}`])
        : e.channel === "gk_error"
          ? `门将失误送出机会，${p}${where}${shot}`
          : pick(rng, [
              `${p}${where}${shot}`,
              body === "head" ? `球传到禁区，${p}${where}${shot}` : `${p}${where}突然起脚，${shot}`,
              `${names[e.team]}打出配合，${p}${where}${shot}`,
            ]);

  if (res === RESULT_SAVED) return `${lead}，${pick(rng, ["被门将扑了出来。", "门将反应神速，把球挡了出去。", "打得太正，被门将抱住。", "门将侧身飞扑，化解险情。"])}`;
  if (res === RESULT_BLOCKED) return `${lead}，${pick(rng, ["被防守球员挡了一下。", "被后卫用身体封堵。", "球打在防守球员身上弹了出来。"])}`;
  if (!sd) return `${lead}，可惜偏出。`;
  const kind = missKind(sd, shotEnd(sd, seed, index));
  if (kind === "bar") return `${lead}，${pick(rng, ["球砸在横梁上弹了出来！", "横梁！就差一点点！"])}`;
  if (kind === "post") return `${lead}，${pick(rng, ["球击中门柱弹出！", "门柱！运气差了一点！"])}`;
  if (kind === "high") return `${lead}，${pick(rng, ["球高出横梁。", "发力过猛，球飞上了看台。", "打高了。"])}`;
  if (kind === "wideLeft") return `${lead}，${pick(rng, ["球擦着左侧门柱偏出。", "稍稍偏左，差之毫厘。"])}`;
  if (kind === "wideRight") return `${lead}，${pick(rng, ["球擦着右侧门柱偏出。", "稍稍偏右，差之毫厘。"])}`;
  return `${lead}，${pick(rng, ["可惜偏出。", "球偏出了底线。"])}`;
}

/** 第 index 个事件的解说;不需要解说的事件返回 null */
export function commentLine(events: SimEvent[], index: number, seed: number, names: [string, string]): string | null {
  const e = events[index];
  if (!e) return null;
  if (e.kind === "goal") return goalLine(events, index, seed, names);
  if (e.kind === "shot") return shotLine(events, index, seed, names);
  const rng = eventRng(seed, index, 1);
  if (e.kind === "red") {
    const p = who(e, names);
    return pick(rng, [
      `红牌！${p}被直接罚下，${names[e.team]}只能少一人作战。`,
      `${p}这次犯规太狠了，裁判出示红牌！`,
      `裁判掏出红牌，${p}提前告别比赛。`,
    ]);
  }
  if (e.kind === "injury") return pick(rng, [`${names[e.team]}有球员受伤倒地，被迫换人。`, `${names[e.team]}出现伤病，不得不做出调整。`]);
  return null;
}

export function kickoffLine(seed: number, names: [string, string]): string {
  return pick(eventRng(seed, -1, 1), [`比赛开始！${names[0]}主场迎战${names[1]}。`, `裁判一声哨响，${names[0]}对${names[1]}的比赛开始了！`]);
}

export function halfTimeLine(seed: number, names: [string, string], score: [number, number]): string {
  const rng = eventRng(seed, -2, 1);
  const s = `${names[0]} ${score[0]}:${score[1]} ${names[1]}`;
  if (score[0] === score[1]) return pick(rng, [`上半场结束，双方暂时战平，${s}。`, `半场休息，${s}，下半场还有好戏。`]);
  const lead = names[score[0] > score[1] ? 0 : 1];
  return pick(rng, [`上半场结束，${lead}暂时领先，${s}。`, `半场休息，${s}，${lead}带着领先优势回到更衣室。`]);
}

export function fullTimeLine(seed: number, names: [string, string], score: [number, number]): string {
  const rng = eventRng(seed, -3, 1);
  const s = `${names[0]} ${score[0]}:${score[1]} ${names[1]}`;
  if (score[0] === score[1]) return pick(rng, [`全场比赛结束！双方握手言和，${s}。`, `终场哨响，${s}，两队各取一分。`]);
  const win = names[score[0] > score[1] ? 0 : 1];
  const margin = Math.abs(score[0] - score[1]);
  return pick(rng, [
    `全场比赛结束！${win}拿下胜利，${s}。`,
    margin >= 3 ? `终场哨响，${win}大胜对手，${s}。` : `终场哨响，${win}笑到了最后，${s}。`,
  ]);
}

/** 统计:射门 / 射正 / xG(截至 uptoTick,含)。射正 = 进球 + 被扑出(门柱与封堵不算射正,与 FotMob 口径一致);
 *  乌龙不算射门;没有射门细节的旧事件按"非射正"计。 */
export function liveStats(events: SimEvent[], uptoTick: number): { shots: [number, number]; onTarget: [number, number]; xg: [number, number] } {
  const shots: [number, number] = [0, 0];
  const onTarget: [number, number] = [0, 0];
  for (const e of events) {
    if (e.tick > uptoTick) continue;
    if ((e.kind !== "shot" && e.kind !== "goal") || e.channel === "owngoal") continue;
    shots[e.team] += 1;
    if (e.kind === "goal" || e.sd?.[3] === RESULT_GOAL || e.sd?.[3] === RESULT_SAVED) onTarget[e.team] += 1;
  }
  // xG 与结果页赛跑图同源(CLAUDE.md §11.3)
  return { shots, onTarget, xg: cumulativeXg(events, uptoTick) };
}

