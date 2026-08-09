import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 时区展示纪律的仓库级守卫(CLAUDE.md §11.2:面向中文用户默认北京时间展示)。
 *
 * 比赛/预测/赔率等赛程语境的时间戳统一走 components/matches/LocalTime.tsx
 * (北京时间,固定 UTC+8,服务端客户端结果恒等)。浏览器本地时区格式化
 * (.toLocaleString()/new Intl.DateTimeFormat())只允许出现在下面显式登记的
 * 账户/运维语境——那些不是比赛时间,用户自己的时区是合理选择,不强求统一。
 * 新增一处这两个 API 的调用,要么改用北京时间,要么显式登记到 ALLOWLIST
 * 并写明理由,不能悄悄漏网。
 *
 * 范围说明:本守卫只识别 .toLocaleString()/new Intl.DateTimeFormat() 这两个
 * 最常见的"引入浏览器本地时区"入口,不识别手写的 Date getter(如不带 UTC
 * 前缀的 getFullYear()/getHours())——components/trust/LocalTime.tsx 用的
 * 就是这种写法,同样是浏览器本地时区,但不在本文件的检测范围内,是已知的
 * 覆盖边界,不是遗漏。
 */

const ROOT = join(__dirname, "..");
const SCAN_DIRS = ["app", "components", "lib"];
// 要求形如函数调用(.toLocaleString( / new Intl.DateTimeFormat()),不匹配
// 注释里提到这两个名字(例如解释"为什么没有用")的说明性文字。
const PATTERN = /\.toLocaleString\(|new Intl\.DateTimeFormat\(/;

// path 相对仓库(frontend/)根目录。
const ALLOWLIST: Record<string, string> = {
  "app/admin/page.tsx": "运维后台内部工具,操作者自己的时区即可,不面向普通用户",
  "app/account/page.tsx": "账户/订阅到期日期,用户本地时区是合理的账单展示惯例",
};

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      walk(full, out);
    } else if (/\.(tsx?|jsx?)$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

describe("时区展示纪律", () => {
  const files = SCAN_DIRS.flatMap((d) => walk(join(ROOT, d)));
  const hits = files
    .map((f) => ({ path: relative(ROOT, f), text: readFileSync(f, "utf-8") }))
    .filter((f) => PATTERN.test(f.text));

  it("浏览器本地时区格式化只能出现在显式登记的白名单文件里", () => {
    const unexpected = hits.filter((h) => !(h.path in ALLOWLIST));
    expect(
      unexpected.map((h) => h.path),
      "发现未登记的 toLocaleString/Intl.DateTimeFormat 用法——比赛/预测/赔率" +
        "语境请改用 components/matches/LocalTime.tsx(北京时间),账户/运维" +
        "语境请把文件路径连同理由加进本文件的 ALLOWLIST",
    ).toEqual([]);
  });

  it("白名单条目本身仍然存在且确实还在用浏览器本地时区格式化(防止白名单腐化)", () => {
    const hitPaths = new Set(hits.map((h) => h.path));
    for (const path of Object.keys(ALLOWLIST)) {
      expect(hitPaths.has(path), `${path} 已登记在白名单,但不再匹配——请把它从 ALLOWLIST 里删掉`).toBe(true);
    }
  });
});
