/**
 * 全站页脚。此前 RootLayout 只有 `<SiteNav/> + {children}`,**全站没有页脚**,
 * 也就没有任何常驻的"联系站长 / 关注公众号"入口 —— 而站长的商业链路
 * (短视频 → 网站 → 公众号 → 私域)最后一跳恰恰依赖它。
 *
 * 页脚同时承接原本散落在各页的免责声明,不再逐页重复。
 */

import Link from "next/link";
import { WechatFollowCard } from "@/components/trust/WechatFollowCard";
import styles from "./SiteFooter.module.css";

/**
 * 公众号二维码已就位:public/brand/wechat-mp-qr.png(430×430,
 * 由微信后台「账号详情」下载的 gh_8dab312fa23f 二维码转 PNG —— 用 PNG 不用
 * 原始 JPG,是因为 JPEG 压缩伪影会降低二维码的扫描可靠性)。
 */
const HAS_WECHAT_QR = true;

export function SiteFooter() {
  return (
    <footer className={styles.footer} data-testid="site-footer">
      <div className={styles.inner}>
        <WechatFollowCard variant="block" hasQr={HAS_WECHAT_QR} />

        <nav className={styles.links} aria-label="页脚导航">
          <Link href="/about">关于我们</Link>
          <Link href="/about-model">数据与方法</Link>
          <Link href="/track-record">公开战绩</Link>
          <Link href="/pricing">权限说明</Link>
        </nav>

        <p className={styles.disclaimer}>
          本站提供足球数据与分析内容,不提供投注建议,不代购彩票,不导流至任何博彩平台。
          数据来源与更新时间在各页面如实标注;模型与统计结果存在不确定性,请自行判断。
        </p>
      </div>
    </footer>
  );
}
