import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import styles from "./about.module.css";

export const metadata: Metadata = {
  title: "关于我们",
  description: "了解喵弟数据研究室的足球数据来源、分析方式、内容工作流和合作方向。",
};

/** 与页脚 WechatFollowCard 同一张固定二维码,不走从未配置过的 NEXT_PUBLIC_* 变量。 */
const WECHAT_QR_SRC = "/brand/wechat-mp-qr.png";
const WECHAT_MP_NAME = "喵弟数据研究室";

export default function AboutPage() {
  return (
    <main className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>ABOUT MEOWDI</p>
          <h1>关于喵弟</h1>
          <p className={styles.lead}>
            喵弟数据研究室是一个面向中文足球用户的独立数据产品。我们把赛程、球队状态、
            xG、赔率观察和历史记录整理到同一场比赛里，再把同一份数据做成网站、
            图卡和视频内容。
          </p>
          <p className={styles.origin}>
            做这个站的人自己看球。我们只负责把数据摆清楚，判断留给你。
          </p>
        </div>
        <figure className={styles.heroMedia}>
          <div className={styles.heroMediaFrame}>
            <Image
              src="/brand/logo-mark.png"
              alt="喵弟数据研究室 logo"
              width={1600}
              height={1082}
              sizes="(max-width: 760px) calc(100vw - 32px), 48vw"
              unoptimized
            />
          </div>
          <figcaption>
            <span>品牌形象 · 喵弟与球</span>
            <span>喵弟数据研究室</span>
          </figcaption>
        </figure>
      </header>

      <section className={styles.section}>
        <h2>我们在做什么</h2>
        <div className={styles.grid}>
          <article>
            <span>01</span>
            <h3>比赛数据</h3>
            <p>把赛程、近期状态、xG 和赔率记录放到一张中文比赛页里。</p>
          </article>
          <article>
            <span>02</span>
            <h3>公开记录</h3>
            <p>预测发布后锁定，赛后统一评估；修正和撤回仍保留原始记录。</p>
          </article>
          <article>
            <span>03</span>
            <h3>内容制作</h3>
            <p>网站、竖屏图卡、口播稿和字幕使用同一份版本化数据。</p>
          </article>
        </div>
      </section>

      <section className={styles.splitSection}>
        <div>
          <h2>数据从哪里来</h2>
        </div>
        <div className={styles.prose}>
          <p>
            比赛与球队数据主要来自 FotMob，赔率快照来自 NowGoal。
            每个区块保留数据更新时间和来源；来源没有提供的字段就显示不可用，
            不用零值补齐。
          </p>
          <p>
            概率可能来自登记模型，也可能是明确标识的赔率折算概率。
            两者不会混写，页面会直接告诉你来源。
          </p>
          <div className={styles.inlineLinks}>
            <Link href="/about-model">查看模型说明</Link>
            <Link href="/track-record">查看公开记录</Link>
          </div>
        </div>
      </section>

      <section className={styles.cooperate}>
        <div>
          <h2>合作联系</h2>
          <p>
            欢迎足球内容创作者、数据产品团队和有定制开发需求的企业联系。
            可合作方向包括内容共创、数据页面、可视化工作流和足球数据平台开发。
          </p>
        </div>
        <div className={styles.contact}>
          <strong>微信公众号：{WECHAT_MP_NAME}</strong>
          <Image
            src={WECHAT_QR_SRC}
            alt={`微信公众号 ${WECHAT_MP_NAME} 二维码`}
            width={148}
            height={148}
            unoptimized
          />
        </div>
      </section>

      <p className={styles.boundary}>
        本站内容为数据研究和内容创作素材，不构成投注建议；模型概率存在不确定性，
        历史表现不代表未来。
      </p>
    </main>
  );
}
