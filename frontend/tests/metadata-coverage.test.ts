import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 页面标题覆盖率的仓库级守卫(2026-08 QA 抽查发现:6 个联赛子页 + /reco 浏览器
 * 标签标题完全没设置,停留在根 layout 的默认标题)。
 *
 * 判定:每个 app/**\/page.tsx 必须满足——自身 export 了 metadata/generateMetadata,
 * 或者从它所在目录往上、到(但不含)app/ 根目录为止,存在一个 layout.tsx 导出了
 * metadata/generateMetadata。**排除根 layout.tsx**——它的 title.default 会让
 * 所有页面都"通过",守卫直接失效。
 *
 * 同一份测试顺带钉死 title.template 迁移(2026-08-21)的结果:app/ 下任何
 * `title:` 后面不得再出现手写的 "— 欧赢 ALLWIN" 后缀,否则会和根 layout 的
 * template 拼成双后缀。
 */

const ROOT = join(__dirname, "..");
const APP_DIR = join(ROOT, "app");
const METADATA_EXPORT_RE = /export\s+(const\s+metadata|(async\s+)?function\s+generateMetadata)\b/;

// path 相对 app/ 目录。理由见各条注释。
const ALLOWLIST: Record<string, string> = {
  "page.tsx": "首页,走根 layout 的 title.default,不需要自己的 metadata",
  "admin/page.tsx": "内部管理后台,robots.ts 已 Disallow,不进 sitemap,标题无产品价值",
  "studio/page.tsx": "Creator Studio 内部工具,同上",
  "studio/matches/[matchId]/page.tsx": "Creator Studio 内部工具,同上",
};

function findAllPageFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      findAllPageFiles(full, out);
    } else if (entry === "page.tsx") {
      out.push(full);
    }
  }
  return out;
}

function exportsMetadata(filePath: string): boolean {
  return METADATA_EXPORT_RE.test(readFileSync(filePath, "utf-8"));
}

/** 从 page.tsx 所在目录起,向上找到第一个导出 metadata 的 layout.tsx;
 * 到 app/ 根目录为止(根 layout 不算数,见文件头说明)。route group
 * 目录(如 "(public)")在文件系统里就是普通目录,天然被这个向上遍历覆盖,
 * 不需要特殊处理。 */
function hasAncestorLayoutMetadata(pageDir: string): boolean {
  let dir = pageDir;
  while (dir !== APP_DIR && dir.startsWith(APP_DIR + sep)) {
    const layout = join(dir, "layout.tsx");
    if (existsSync(layout) && exportsMetadata(layout)) return true;
    dir = dirname(dir);
  }
  return false;
}

describe("页面标题覆盖率守卫", () => {
  const pageFiles = findAllPageFiles(APP_DIR);

  it("每个 page.tsx 自身或祖先 layout(不含根 layout)导出了 metadata,或在白名单里", () => {
    const missing = pageFiles
      .map((f) => relative(APP_DIR, f))
      .filter((rel) => {
        if (rel in ALLOWLIST) return false;
        const full = join(APP_DIR, rel);
        if (exportsMetadata(full)) return false;
        return !hasAncestorLayoutMetadata(dirname(full));
      });
    expect(
      missing,
      "以下页面没有浏览器标签标题(document.title 会停留在根默认标题)——" +
        "请给页面加 export const metadata / generateMetadata,或加一个" +
        "同目录 layout.tsx(client component 页面走这条路,见 app/reco/layout.tsx" +
        "的先例),或者带理由登记进本文件的 ALLOWLIST",
    ).toEqual([]);
  });

  it("ALLOWLIST 条目本身仍然存在且确实没有 metadata(防止白名单腐化)", () => {
    const pageRelPaths = new Set(pageFiles.map((f) => relative(APP_DIR, f)));
    for (const rel of Object.keys(ALLOWLIST)) {
      expect(pageRelPaths.has(rel), `${rel} 已登记在白名单,但该 page.tsx 不存在——请从 ALLOWLIST 删掉`).toBe(
        true,
      );
      const full = join(APP_DIR, rel);
      const hasOwn = exportsMetadata(full);
      const hasAncestor = hasAncestorLayoutMetadata(dirname(full));
      expect(
        !hasOwn && !hasAncestor,
        `${rel} 已登记在白名单(理由:"${ALLOWLIST[rel]}"),但现在已经有 metadata 了——请从 ALLOWLIST 删掉`,
      ).toBe(true);
    }
  });

  it('title.template 迁移(2026-08-21)不得留下双后缀:app/ 下 title 不得再手写 "— 欧赢 ALLWIN"', () => {
    // 根 layout.tsx 自己(template 定义 + 说明注释)是唯一允许出现这段文本的地方。
    const offenders: string[] = [];
    for (const f of pageFiles) {
      const rel = relative(APP_DIR, f);
      if (readFileSync(f, "utf-8").includes("— 欧赢 ALLWIN")) offenders.push(rel);
    }
    // layout.tsx 里除根 layout 外,子 layout(reco/login/account)也不该出现。
    for (const rel of ["reco/layout.tsx", "login/layout.tsx", "account/layout.tsx"]) {
      const full = join(APP_DIR, rel);
      if (existsSync(full) && readFileSync(full, "utf-8").includes("— 欧赢 ALLWIN")) {
        offenders.push(rel);
      }
    }
    expect(offenders, "这些文件手写了站名后缀,会和根 layout 的 title.template 拼成双后缀").toEqual([]);
  });
});
