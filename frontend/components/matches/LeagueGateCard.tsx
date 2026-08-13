"use client";

/**
 * 联赛门禁页(比赛详情页重设计 §6.2):非五大联赛/欧战比赛,匿名点开时
 * 不再是空白登录框——带上真实赛事信息(联赛/轮次/双方/开球时间,来自
 * 401 body 的非敏感字段,见 backend/api/routes_public.py::_require_league_access),
 * 说清楚"登录能看到什么",并直接嵌入扫码登录卡片。
 */

import { ScanLoginCard, useEnv } from "@/components/auth/ScanLoginCard";
import { formatBeijingZh } from "@/components/matches/zh";
import styles from "./LeagueGateCard.module.css";

export type GateInfo = {
  leagueName?: string;
  round?: string | null;
  homeTeam?: string;
  awayTeam?: string;
  kickoffAtUtc?: string | null;
};

export function LeagueGateCard({
  matchId,
  info,
}: {
  matchId: number;
  info: GateInfo;
}) {
  const env = useEnv();
  const nextPath = `/matches/${matchId}`;
  const kickoff = info.kickoffAtUtc ? formatBeijingZh(info.kickoffAtUtc) : null;

  return (
    <div className={styles.card}>
      {(info.leagueName || info.homeTeam) && (
        <div className={styles.eventHead}>
          {info.leagueName && (
            <span className={styles.chip}>
              {info.leagueName}
              {info.round ? ` · 第${info.round}轮` : ""}
            </span>
          )}
          {info.homeTeam && info.awayTeam && (
            <div className={styles.teams}>
              <span>{info.homeTeam}</span>
              <span className={styles.vs}>VS</span>
              <span>{info.awayTeam}</span>
            </div>
          )}
          {kickoff && <p className={styles.kickoff}>北京时间 {kickoff}</p>}
        </div>
      )}

      {env !== null && (
        <ScanLoginCard nextPath={nextPath} env={env} title="扫码登录后查看本场数据" />
      )}

      <h2 className={styles.title}>登录后可免费查看</h2>
      <ul className={styles.perks}>
        <li>双方近5场战绩与进失球</li>
        <li>近5场射门分布图</li>
        <li>初盘 → 临场赔率变化</li>
      </ul>

      <p className={styles.note}>微信扫码 · 免费,不需付费,不填手机号</p>
    </div>
  );
}
