import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * 颜色语义纪律的仓库级守卫(CLAUDE.md §11.2:
 *   深蓝=品牌/大标题,青绿=主要操作/选中,黄绿=推荐发布/关键数据,
 *   橙=等待更新/临近开球,红=真实错误或不可用,灰=辅助说明。
 *   "推荐待发布""数据等待刷新"不得用红色)。
 *
 * 2026-08-21 之前 STALE("数据等待刷新"/"数据已过期")一直用 --loss(红),
 * UNAVAILABLE("部分数据暂不可用",真正的"不可用")反而是灰色——两者相对
 * 规则是互换的。CSS Modules 在 vitest 里被 stub 成空对象,渲染测试拿不到
 * 真实颜色值,只有直接读源文件文本才能守住这条规则(同 timezone-discipline
 * 的读文件校验范式)。
 */

const ROOT = join(__dirname, "..");

/** 返回**全部**匹配到的规则块内容拼接——不能只取第一个匹配:比如
 * MatchHeaderPre.module.css 里 .stalePill 同时出现在共享选择器组
 * (`.statusPill,\n.stalePill { ... }`,只给 padding/border-radius/font-size)
 * 和单独的颜色覆写规则(`.stalePill { border-color: ...; color: ... }`)里,
 * 只取第一个匹配会拿到不含颜色声明的那个块,让断言失去意义。 */
function blocks(css: string, selectorPattern: RegExp): string {
  const flags = selectorPattern.flags.includes("g")
    ? selectorPattern.flags
    : `${selectorPattern.flags}g`;
  const g = new RegExp(selectorPattern.source, flags);
  return Array.from(css.matchAll(g), (m) => m[1] ?? "").join("\n");
}

describe("颜色语义纪律(CLAUDE.md §11.2)", () => {
  const pageCss = readFileSync(join(ROOT, "app/page.module.css"), "utf-8");
  const globalsCss = readFileSync(join(ROOT, "app/globals.css"), "utf-8");
  const headerPreCss = readFileSync(
    join(ROOT, "components/matches/MatchHeaderPre.module.css"),
    "utf-8",
  );
  const headerFinishedCss = readFileSync(
    join(ROOT, "components/matches/MatchHeaderFinished.module.css"),
    "utf-8",
  );

  it("--pending token 浅色/深色两套都要定义,不能只定义一半", () => {
    expect(globalsCss.match(/--pending:/g)?.length).toBe(2);
  });

  it('首页新鲜度提示:STALE("数据等待刷新")不得用 --loss(红),必须用 --pending(橙)', () => {
    const stale = blocks(
      pageCss,
      /\.freshnessItem\[data-state="STALE"\]\s*\.freshnessState\s*\{([^}]*)\}/,
    );
    expect(stale).not.toBe("");
    expect(stale, "§11.2:\"数据等待刷新\"不得用红色").not.toContain("var(--loss)");
    expect(stale).toContain("var(--pending)");
  });

  it('首页新鲜度提示:UNAVAILABLE("部分数据暂不可用")才是红色的正当场景', () => {
    const unavailable = blocks(
      pageCss,
      /\.freshnessItem\[data-state="UNAVAILABLE"\]\s*\.freshnessState\s*\{([^}]*)\}/,
    );
    expect(unavailable).not.toBe("");
    expect(unavailable).toContain("var(--loss)");
  });

  it("首页新鲜度提示:无 state(null)时是中性灰,不落入任何一个 data-state 分支", () => {
    // 只匹配"行首就是 .freshnessState {"的裸规则——排除
    // `.freshnessItem[data-state="..."] .freshnessState {` 这类复合选择器
    // (它们的 .freshnessState 前面是 "] ",不是行首)。
    const base = blocks(pageCss, /(?:^|\n)\.freshnessState\s*\{([^}]*)\}/);
    expect(base).not.toBe("");
    expect(base).toContain("var(--ink-3)");
    expect(base).not.toContain("var(--win)");
    expect(base).not.toContain("var(--loss)");
  });

  it.each([
    ["MatchHeaderPre.module.css", headerPreCss],
    ["MatchHeaderFinished.module.css", headerFinishedCss],
  ])('%s 的 .stalePill("数据已过期")不得用 --loss,必须用 --pending', (_name, css) => {
    const pill = blocks(css, /\.stalePill\s*\{([^}]*)\}/);
    expect(pill).not.toBe("");
    expect(pill).not.toContain("var(--loss)");
    expect(pill).toContain("var(--pending)");
  });
});
