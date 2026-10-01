/**
 * 全站页脚。此前 RootLayout 只有 `<SiteNav/> + {children}`,**全站没有页脚**,
 * 也就没有任何常驻的"联系站长 / 关注公众号"入口 —— 而站长的商业链路
 * (短视频 → 网站 → 公众号 → 私域)最后一跳恰恰依赖它。
 *
 * 页脚同时承接原本散落在各页的免责声明,不再逐页重复。
 */

import Link from "next/link";
import { FooterDisclaimer } from "@/components/FooterDisclaimer";
import { MobileFollowBar } from "@/components/trust/MobileFollowBar";
import { WechatFollowCard } from "@/components/trust/WechatFollowCard";
import styles from "./SiteFooter.module.css";

/**
 * 公众号二维码已就位:public/brand/wechat-oa-qr.jpg(「足球喵喵第」,430×430,见 lib/wechat-mp.ts。
 * 2026-10-01 替换掉此前的占位图 gh_8dab312fa23f。保留微信后台原始 JPG 不转 PNG:事后转格式
 * 去不掉已有的压缩伪影,原图本身用 CoreImage 解码正常)。
 */
const HAS_WECHAT_QR = true;

export function SiteFooter() {
  return (
    <footer className={styles.footer} data-testid="site-footer">
      <div className={styles.inner}>
        {/* 桌面:大卡片(含二维码);手机:折叠成一行"品牌名 + 关注公众号",点开底部面板 */}
        <div className={styles.desktopOnly}>
          <WechatFollowCard variant="block" hasQr={HAS_WECHAT_QR} />
        </div>
        <MobileFollowBar />

        <nav className={styles.links} aria-label="页脚导航">
          <Link href="/about">关于我们</Link>
          <Link href="/pricing">会员与权限</Link>
        </nav>

        <FooterDisclaimer
          first="我们只做足球数据和分析，不给投注建议，不代购彩票，也不往任何博彩平台导流。"
          rest="数据仅供参考，怎么用你自己判断。"
        />
      </div>
    </footer>
  );
}
