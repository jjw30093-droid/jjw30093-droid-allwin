import { readFile } from "node:fs/promises";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { SimParams } from "@/features/simulator/types";
import { SimulatorClient } from "./SimulatorClient";

// 本地原型:只在开发环境、且显式设置 SIMULATOR_ENABLED=1 时可访问;生产构建一律 404。
export const dynamic = "force-dynamic";

function enabled(): boolean {
  return process.env.NODE_ENV !== "production" && process.env.SIMULATOR_ENABLED === "1";
}

// 用 generateMetadata 而不是静态 metadata:静态导出即使页面 notFound() 也会把标题写进生产响应。
export async function generateMetadata(): Promise<Metadata> {
  if (!enabled()) return { robots: { index: false, follow: false } };
  return { title: "比赛模拟器(未校准原型)", robots: { index: false, follow: false } };
}

export default async function SimulatorPage() {
  if (!enabled()) notFound();
  const path = process.env.SIMULATOR_PARAMS_PATH;
  if (!path) {
    return <p style={{ padding: 24 }}>未设置 SIMULATOR_PARAMS_PATH,无法读取模拟器参数。</p>;
  }
  let params: SimParams;
  try {
    params = JSON.parse(await readFile(path, "utf8")) as SimParams;
  } catch (e) {
    return <p style={{ padding: 24 }}>读取参数失败:{String(e)}</p>;
  }
  return <SimulatorClient params={params} />;
}
