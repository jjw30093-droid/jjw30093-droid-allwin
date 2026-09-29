import { simulateMany, simulateOnce, type MatchConfig } from "./engine";

interface Req {
  config: MatchConfig;
  seed: number;
  runs: number;
}

const ctx = self as unknown as {
  onmessage: ((e: MessageEvent<Req>) => void) | null;
  postMessage: (msg: unknown) => void;
};

ctx.onmessage = (e) => {
  const { config, seed, runs } = e.data;
  const single = simulateOnce(config, seed);
  const many = simulateMany(config, seed, runs, single.score);
  ctx.postMessage({ single, many });
};
