// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// 分享图是 canvas 导出:跨源 <img>(球员头像走 FotMob CDN)会污染 canvas,导致 toBlob 失败。
// 头像只允许出现在页面 DOM 里,绝不能进 shareImage.ts。
describe("分享图不引用球员头像外链", () => {
  it("shareImage.ts 不含 playerAvatarUrl / images.fotmob.com / PlayerAvatar", () => {
    const src = readFileSync(join(__dirname, "..", "features", "simulator", "shareImage.ts"), "utf8");
    expect(src).not.toMatch(/playerAvatarUrl|images\.fotmob\.com|PlayerAvatar/);
  });
});
