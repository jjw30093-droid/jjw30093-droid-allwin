import { simulateMany, simulateOnce, type MatchConfig } from "./engine";
import { winRate, type ImpactJob } from "./focusImpact";

type Req =
  | { kind?: "run"; config: MatchConfig; seed: number; runs: number }
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
  const { config, seed, runs } = req;
  const single = simulateOnce(config, seed);
  const many = simulateMany(config, seed, runs, single.score);
  ctx.postMessage({ single, many });
};
