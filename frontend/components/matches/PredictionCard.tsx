"use client";

/**
 * 概率卡(比赛详情页第 2 区块)。
 *
 * 免费/付费边界(宪法 §8.2):
 * - server 端传入的 initial 是匿名请求的结果,只含 top_outcome/top_probability
 *   (受限字段物理上不在响应里,不是遮挡);
 * - 挂载后用 clientFetch(带会话 cookie)重拉同一端点:有 prediction:full_wdl
 *   权益时后端返回完整 DTO(tier="full"),本组件才有三项概率可渲染。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { clientFetch, type PredictionResponse } from "@/lib/api-v1";
import { LocalTime } from "./LocalTime";
import { OUTCOME_ZH, PRED_STATUS_ZH, pct } from "./zh";
import styles from "./PredictionCard.module.css";

type Prediction = NonNullable<PredictionResponse["prediction"]>;

function FullBar({ p }: { p: Extract<Prediction, { tier: "full" }> }) {
  return (
    <div>
      <div className={styles.probBar}>
        <span
          className={styles.segHome}
          style={{ width: `${p.home_probability * 100}%` }}
        />
        <span
          className={styles.segDraw}
          style={{ width: `${p.draw_probability * 100}%` }}
        />
        <span
          className={styles.segAway}
          style={{ width: `${p.away_probability * 100}%` }}
        />
      </div>
      <div className={styles.probLabels}>
        <span>
          <i className={styles.dotHome} />
          主胜 <b className="num">{pct(p.home_probability)}</b>
        </span>
        <span>
          <i className={styles.dotDraw} />
          平局 <b className="num">{pct(p.draw_probability)}</b>
        </span>
        <span>
          <i className={styles.dotAway} />
          客胜 <b className="num">{pct(p.away_probability)}</b>
        </span>
      </div>
      {p.expected_home_goals != null && p.expected_away_goals != null && (
        <p className={styles.expected}>
          预期进球(模型 λ):主{" "}
          <span className="num">{p.expected_home_goals.toFixed(2)}</span> / 客{" "}
          <span className="num">{p.expected_away_goals.toFixed(2)}</span>
        </p>
      )}
      <p className={styles.hash}>
        预测指纹 <span className="num">{p.prediction_hash.slice(0, 16)}…</span>
        (锁定后不可修改,可与公开战绩页比对)
      </p>
    </div>
  );
}

function FreeView({ p }: { p: Extract<Prediction, { tier: "free" }> }) {
  return (
    <div>
      <div className={styles.freeTop}>
        <span className={styles.freeOutcome}>{OUTCOME_ZH[p.top_outcome]}</span>
        <span className={`${styles.freePct} num`}>{pct(p.top_probability)}</span>
      </div>
      <p className={styles.freeNote}>
        免费层仅展示模型最高一项概率;另外两项概率不在本响应中下发。
      </p>
      <p className={styles.upgrade}>
        <Link href="/pricing" className={styles.upgradeLink}>
          登录并开通会员,解锁完整胜平负三项概率 →
        </Link>
      </p>
    </div>
  );
}

export function PredictionCard({
  matchId,
  initial,
}: {
  matchId: number;
  initial: PredictionResponse | null;
}) {
  const [resp, setResp] = useState<PredictionResponse | null>(initial);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    clientFetch<PredictionResponse>(`/api/v1/matches/${matchId}/prediction`)
      .then((d) => {
        if (!cancelled) {
          setResp(d);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [matchId]);

  if (resp == null) {
    return (
      <div className={styles.empty}>
        {failed
          ? "概率数据暂时无法加载,请稍后重试。"
          : "正在加载概率数据…"}
      </div>
    );
  }

  if (!resp.available || !resp.prediction) {
    return (
      <div className={styles.empty}>
        {resp.reason ?? "该场比赛暂无已发布的正式预测。"}
      </div>
    );
  }

  const p = resp.prediction;
  return (
    <div>
      {p.tier === "full" ? <FullBar p={p} /> : <FreeView p={p} />}
      <dl className={styles.metaGrid}>
        <div>
          <dt>模型版本</dt>
          <dd className="num">{p.meta.model_version_id}</dd>
        </div>
        <div>
          <dt>登记状态</dt>
          <dd>{PRED_STATUS_ZH[p.meta.status] ?? p.meta.status}</dd>
        </div>
        <div>
          <dt>生成时间</dt>
          <dd>
            <LocalTime iso={p.meta.generated_at} />
          </dd>
        </div>
        <div>
          <dt>数据截止</dt>
          <dd>
            <LocalTime iso={p.meta.input_cutoff_at} fallback="未提供" />
          </dd>
        </div>
      </dl>
      {p.meta.confidence === "low" && (
        <p className={styles.lowConfidence}>
          模型对该场信心较低(如升班马缺乏历史数据),概率仅供参考。
        </p>
      )}
      {failed && (
        <p className={styles.staleNote}>
          实时刷新失败,以上为页面加载时的公开数据。
        </p>
      )}
    </div>
  );
}
