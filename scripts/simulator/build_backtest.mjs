// 把回测入口(frontend/features/simulator/backtest/run.ts,调用页面同一套 engine.ts)打包成
// 单个 Node 可运行的 .mjs。用法:node scripts/simulator/build_backtest.mjs <输出文件>
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const frontend = resolve(repo, "frontend");
const require = createRequire(resolve(frontend, "package.json"));
const { rolldown } = await import(pathToFileURL(require.resolve("rolldown")).href);

const out = resolve(process.argv[2] ?? resolve(repo, ".local-data/simulator/backtest/run.mjs"));
const bundle = await rolldown({
  input: resolve(frontend, "features/simulator/backtest/run.ts"),
  platform: "node",
  resolve: { alias: { "@": frontend } },
});
await bundle.write({ file: out, format: "esm" });
await bundle.close();
console.log(`bundled → ${out}`);
