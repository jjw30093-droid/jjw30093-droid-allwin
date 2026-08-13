/**
 * 公众号关注卡(私域转化入口)。
 *
 * 与登录二维码是两件不同的东西,**必须物理分开**:
 *
 * | | components/auth/ScanLoginCard | 本组件 |
 * |---|---|---|
 * | 内容 | 微信带参临时码,每次不同 | 固定静态图片 |
 * | 生命周期 | 5 分钟过期、一次性消费、带轮询状态机 | 永不过期、无状态 |
 * | 目的 | 建立本站会话 | 沉淀私域关系 |
 * | 位置 | 只在 /login 与联赛门禁页 | 页脚常驻 + 详情页底部 |
 *
 * 两者在微信侧确实是同一个公众号(扫带参码时未关注用户会先被要求关注),
 * 但登录码有时效和状态机 —— 合并会让"我已经关注了,为什么还要扫一次"
 * 变成困惑,而且登录码过期刷新时静态关注入口会跟着消失。
 *
 * 二维码图片走 public/ 静态资源而不是 NEXT_PUBLIC_* 环境变量:
 * NEXT_PUBLIC_* 在 next build 时内联,systemd 运行期注入无效(宪法 §10.3),
 * 而现有 app/about/page.tsx 读的 NEXT_PUBLIC_WECHAT_QR_CODE_URL 全仓从未
 * 被配置过(.env.example / deploy/ 都没有),线上 100% 走 else 分支。
 */

import Image from "next/image";
import styles from "./WechatFollowCard.module.css";

/** 站长在微信后台「账号详情」下载的固定二维码。缺省时整卡不渲染。 */
const QR_SRC = "/brand/wechat-mp-qr.png";

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
        <h2 id="wechat-follow-title" className={styles.title}>
          每天一场比赛的完整数据图
        </h2>
        <p className={styles.desc}>
          射门分布、xG 走势、盘口变化,先发在公众号。
        </p>
        <ul className={styles.perks}>
          <li>不收费,不填手机号</li>
          <li>只发数据,不发荐单</li>
        </ul>
      </div>
      {hasQr ? (
        <div className={styles.qrBox}>
          <Image
            src={QR_SRC}
            alt="欧赢 ALLWIN 公众号二维码"
            width={200}
            height={200}
            className={styles.qr}
            unoptimized
          />
          <span className={styles.qrHint}>微信扫码关注</span>
        </div>
      ) : (
        <div className={styles.qrPending}>
          <span>公众号二维码待配置</span>
        </div>
      )}
    </section>
  );
}
