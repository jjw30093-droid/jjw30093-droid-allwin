"use client";

// 非英超联赛的模拟器(2026-10-02 登录门禁):参数含赔率反推的 λ 与盘口线,
// 只能经 /api/v1/simulator/params 校验登录后下发。未登录 → 跳登录页,登录后回到本页。
// 英超仍由 page.tsx 服务端直接渲染。

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, clientFetch, type GetJson } from "@/lib/api-v1";
import { isLoginRequired, redirectToLogin } from "@/lib/login-gate";
import { isStale } from "@/features/simulator/staleness";
import type { SimParams } from "@/features/simulator/types";
import { stepForQuery } from "@/features/simulator/wizard";
import type { FixtureIndexEntry } from "./loadParams";
import { SimulatorClient } from "./SimulatorClient";
import styles from "./simulator.module.css";

type ParamsResp = GetJson<"/api/v1/simulator/params">;

type State =
  | { phase: "loading" }
  | { phase: "data"; params: SimParams; fixtureIndex: FixtureIndexEntry[] }
  | { phase: "error"; message: string };

export function MemberSimulator({ leagueId }: { leagueId: number }) {
  const [state, setState] = useState<State>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    clientFetch<ParamsResp>(`/api/v1/simulator/params?league_id=${leagueId}`)
      .then((r) => {
        if (cancelled) return;
        setState({
          phase: "data",
          params: r.params as unknown as SimParams,
          fixtureIndex: r.fixture_index as FixtureIndexEntry[],
        });
      })
      .catch((e) => {
        if (cancelled) return;
        if (isLoginRequired(e)) {
          redirectToLogin();
        } else {
          setState({
            phase: "error",
            message:
              e instanceof ApiError && e.status === 503
                ? "参数正在更新或暂时无法读取,请稍后再试。"
                : "模拟器暂时无法加载,请稍后再试。",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [leagueId]);

  if (state.phase === "loading") {
    return (
      <main className={styles.page}>
        <section className={styles.card} aria-label="模拟器加载中" aria-busy="true" style={{ minHeight: 240 }} />
      </main>
    );
  }
  if (state.phase === "error") {
    return (
      <main className={styles.page}>
        <section className={styles.card} data-testid="simulator-notice">
          <h1 className={styles.title}>模拟器参数暂不可用</h1>
          <p className={styles.muted} style={{ marginTop: 12 }}>
            {state.message}
          </p>
          <p style={{ marginTop: 16 }}>
            <Link href="/simulator" className={styles.linkBtn}>
              返回英超模拟器
            </Link>
          </p>
        </section>
      </main>
    );
  }
  return (
    <SimulatorClient
      key={leagueId}
      params={state.params}
      leagueId={leagueId}
      fixtureIndex={state.fixtureIndex}
      paramsStale={isStale(state.params)}
      initialStep={stepForQuery(window.location.search || "?", state.params, leagueId)}
    />
  );
}
