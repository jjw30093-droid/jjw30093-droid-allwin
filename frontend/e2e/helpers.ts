import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export const API = "http://127.0.0.1:8010";

/** 种子信息(webServer 启动命令里 seed_e2e 先跑,测试执行时文件必已存在)。 */
export function seedMatchId(): number {
  const txt = readFileSync(
    resolve(__dirname, "../../data/e2e/seed_info.txt"),
    "utf-8",
  );
  const m = txt.match(/match_id=(\d+)/);
  if (!m) throw new Error("seed_info.txt 缺 match_id");
  return Number(m[1]);
}

export function seedRedeemCode(): string {
  return readFileSync(
    resolve(__dirname, "../../data/e2e/redeem_code.txt"),
    "utf-8",
  ).trim();
}
