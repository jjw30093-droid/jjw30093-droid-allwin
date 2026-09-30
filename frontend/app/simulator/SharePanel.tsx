"use client";

import { useEffect, useState } from "react";
import { SITE_URL } from "@/lib/site";
import { buildShareUrl } from "@/features/simulator/shareLink";
import { pageFonts, renderShareImage, type ShareSize } from "@/features/simulator/shareImage";
import type { ResultSnapshot } from "@/features/simulator/snapshot";
import { useSimTeamColors } from "@/features/simulator/useSimTeamColors";
import styles from "./simulator.module.css";

const SITE_NAME = "喵弟数据研究室";
// 二维码与图上地址指向模拟器页(生产域名来自构建期 NEXT_PUBLIC_SITE_URL)
const SIMULATOR_URL = `${SITE_URL}/simulator`;
const SIZES: ShareSize[] = [1350, 1920];

export function SharePanel({ snap }: { snap: ResultSnapshot }) {
  const colors = useSimTeamColors().surface;
  const [busy, setBusy] = useState<ShareSize | null>(null);
  const [img, setImg] = useState<{ url: string; size: ShareSize; bytes: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean | null>(null);

  useEffect(() => () => {
    if (img) URL.revokeObjectURL(img.url);
  }, [img]);

  const generate = async (size: ShareSize) => {
    setBusy(size);
    setError(null);
    try {
      const blob = await renderShareImage({ snap, size, teamColors: colors, fonts: pageFonts(), siteName: SITE_NAME, siteUrl: SIMULATOR_URL });
      setImg({ url: URL.createObjectURL(blob), size, bytes: blob.size });
    } catch (e) {
      setError(`生成分享图失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const makeLink = async () => {
    const url = await buildShareUrl(`${window.location.origin}${window.location.pathname}`, snap);
    setLink(url);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const fileName = img
    ? `模拟_${snap.teams[0].name}vs${snap.teams[1].name}_${snap.single.score.join("-")}_1080x${img.size}.png`
    : "";

  return (
    <section className={styles.card} data-testid="share-panel">
      <h2 className={styles.cardTitle}>分享</h2>
      <div className={styles.row}>
        {SIZES.map((size) => (
          <button
            key={size}
            type="button"
            className={styles.secondaryBtn}
            disabled={busy !== null}
            onClick={() => generate(size)}
            data-testid={`share-img-${size}`}
          >
            {busy === size ? "生成中…" : `生成分享图 1080×${size}`}
          </button>
        ))}
        <button type="button" className={styles.secondaryBtn} onClick={makeLink} data-testid="share-link-btn">
          复制分享链接
        </button>
      </div>
      <p className={styles.muted}>
        分享图在浏览器里生成,不上传服务器;分享链接把设定写在网址里、本次结果压缩后写在 # 之后,打开时原样展示这次结果,不经服务器、不写数据库。
      </p>
      {error ? <p className={styles.error}>{error}</p> : null}
      {link ? (
        <div className={styles.shareLinkBox}>
          <textarea className={styles.shareLink} readOnly value={link} rows={3} onFocus={(e) => e.currentTarget.select()} data-testid="share-link" />
          <p className={styles.muted}>
            链接长度 {link.length} 字符{copied === true ? " · 已复制到剪贴板" : copied === false ? " · 浏览器未允许自动复制,请手动复制" : ""}
          </p>
        </div>
      ) : null}
      {img ? (
        <div className={styles.sharePreviewBox}>
          {/* eslint-disable-next-line @next/next/no-img-element -- 本地 blob 预览,不走 next/image 优化 */}
          <img className={styles.sharePreview} src={img.url} alt={`分享图 1080×${img.size}`} data-testid="share-img" />
          <p className={styles.muted}>
            1080×{img.size} · {(img.bytes / 1024).toFixed(0)} KB ·{" "}
            <a className={styles.linkBtn} href={img.url} download={fileName}>
              下载图片
            </a>{" "}
            · 手机上也可以长按图片保存
          </p>
        </div>
      ) : null}
    </section>
  );
}
