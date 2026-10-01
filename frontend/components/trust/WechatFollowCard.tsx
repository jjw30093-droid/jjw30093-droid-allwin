/**
 * 公众号关注卡(私域转化入口)。
 *
 * 与登录二维码是两件不同的东西,**必须物理分开**:
 *
 * | | components/auth/CodeLoginCard | 本组件 |
 * |---|---|---|
 * | 内容 | 4 位验证码(发给公众号),每次不同 | 公众号固定二维码图片 |
 * | 生命周期 | 5 分钟过期、一次性消费、带轮询状态机 | 永不过期、无状态 |
 * | 目的 | 建立本站会话 | 沉淀私域关系 |
 * | 位置 | 只在 /login | 页脚常驻 + 详情页底部 |
 *
 * 两者在微信侧是同一个公众号「足球喵喵第」(lib/wechat-mp.ts 单一真源),
 * 但登录有时效和状态机,关注入口是常驻的,不合并。
 *
 * 二维码图片走 public/ 静态资源而不是 NEXT_PUBLIC_* 环境变量:
 * NEXT_PUBLIC_* 在 next build 时内联,systemd 运行期注入无效(宪法 §10.3),
 * 而现有 app/about/page.tsx 读的 NEXT_PUBLIC_WECHAT_QR_CODE_URL 全仓从未
 * 被配置过(.env.example / deploy/ 都没有),线上 100% 走 else 分支。
 */

import Image from "next/image";
import { SITE_BRAND_NAME, WECHAT_MP_NAME, WECHAT_MP_QR_SRC } from "@/lib/wechat-mp";
import styles from "./WechatFollowCard.module.css";


export function WechatFollowCard({
  variant = "block",
  hasQr = false,
}: {
  /** block=独立卡片(详情页底部);compact=页脚内嵌一行 */
  variant?: "block" | "compact";
  /**
   * 二维码图片是否已放进 public/brand/。站长上传前传 false,
   * 只渲染文字入口,不显示破图 —— 破图比没有更伤信任。
   */
  hasQr?: boolean;
}) {
  return (
    <section className={styles.card} data-variant={variant} aria-labelledby="wechat-follow-title">
      <div className={styles.body}>
        <div className={styles.brandRow}>
          <Image
            src="/brand/logo-badge-256.png"
            alt=""
            width={34}
            height={34}
            className={styles.brandLogo}
          />
          <span className={styles.brandName}>{SITE_BRAND_NAME}</span>
        </div>
        <h2 id="wechat-follow-title" className={styles.title}>
          每天一场比赛的完整数据图
        </h2>
        <p className={styles.desc}>
          射门分布、xG 走势、盘口变化,先发在公众号。
        </p>
        <ul className={styles.perks}>
          <li>不收费,不填手机号</li>
          <li>不代购、不承诺收益;推荐与修正记录公开</li>
        </ul>
      </div>
      {hasQr ? (
        <div className={styles.qrBox}>
          <Image
            src={WECHAT_MP_QR_SRC}
            alt={`${WECHAT_MP_NAME} 公众号二维码`}
            width={200}
            height={200}
            className={styles.qr}
            unoptimized
          />
          <span className={styles.qrHint}>微信扫码关注「{WECHAT_MP_NAME}」</span>
        </div>
      ) : (
        <div className={styles.qrPending}>
          <span>公众号二维码待配置</span>
        </div>
      )}
    </section>
  );
}
