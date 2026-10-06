import { simulateMany, simulateOnce, type MatchConfig } from "./engine";
import { winRate, type ImpactJob } from "./focusImpact";

type Req =
  | { kind?: "run"; config: MatchConfig; seed: number; manySeed?: number; runs: number }
  | { kind: "impact"; jobs: ImpactJob[]; seed: number; runs: number };

const ctx = self as unknown as {
  onmessage: ((e: MessageEvent<Req>) => void) | null;
  postMessage: (msg: unknown) => void;
};

ctx.onmessage = (e) => {
  const req = e.data;
  if (req.kind === "impact") {
    const results = req.jobs.map((j) => ({ side: j.side, kind: j.kind, win: winRate(j.config, j.side, req.seed, req.runs) }));
    ctx.postMessage({ kind: "impact", results });
    return;
  }
  const { config, seed, manySeed, runs } = req;
  const single = simulateOnce(config, seed);
  // 单场剧情每次换种子；统计分布用稳定种子，避免用户只是“换个剧本”时胜率也跟着抖动。
  const many = simulateMany(config, manySeed ?? seed, runs, single.score);
  ctx.postMessage({ single, many });
};
