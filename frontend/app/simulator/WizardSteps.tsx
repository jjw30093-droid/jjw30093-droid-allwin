"use client";

import type { WizardStep } from "@/features/simulator/wizard";
import styles from "./simulator.module.css";

const STEPS: { n: WizardStep | 3; label: string }[] = [
  { n: 1, label: "主队" },
  { n: 2, label: "客队" },
  { n: 3, label: "模拟" },
];

/** 步骤条"1 主队 · 2 客队 · 3 模拟"。前两步可点(第 2 步需第 1 步阵容完整);第 3 步只是指示。 */
export function WizardSteps({ step, canGoStep2, onGo }: { step: WizardStep; canGoStep2: boolean; onGo: (s: WizardStep) => void }) {
  return (
    <ol className={styles.steps} aria-label="排阵步骤">
      {STEPS.map((s) => {
        const active = s.n === step;
        const done = s.n < step;
        const clickable = s.n === 1 || (s.n === 2 && canGoStep2);
        return (
          <li key={s.n} className={`${styles.step} ${active ? styles.stepActive : ""} ${done ? styles.stepDone : ""}`} aria-current={active ? "step" : undefined}>
            {s.n === 3 || !clickable ? (
              <span className={styles.stepInner}>
                <span className={styles.stepNum}>{s.n}</span>
                {s.label}
              </span>
            ) : (
              <button type="button" className={styles.stepInner} onClick={() => onGo(s.n as WizardStep)} data-testid={`step-${s.n}`}>
                <span className={styles.stepNum}>{s.n}</span>
                {s.label}
              </button>
            )}
          </li>
        );
      })}
    </ol>
  );
}
