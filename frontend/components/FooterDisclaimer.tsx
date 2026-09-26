"use client";

/**
 * 页脚免责声明。手机端(<768px)默认只显示第一句,点"展开"看全文;桌面端始终显示全文
 * (展开按钮与折叠都只在手机断点生效,见 SiteFooter.module.css)。
 * 全文与分句都是 props,文案的唯一来源仍是 SiteFooter。
 */

import { useState } from "react";
import styles from "./SiteFooter.module.css";

export function FooterDisclaimer({ first, rest }: { first: string; rest: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.disclaimerBox}>
      <p className={styles.disclaimer} data-open={open}>
        {first}
        <span className={styles.disclaimerRest}>{rest}</span>
        <button
          type="button"
          className={styles.disclaimerToggle}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "收起" : "展开"}
        </button>
      </p>
    </div>
  );
}
