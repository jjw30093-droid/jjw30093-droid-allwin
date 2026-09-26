/**
 * 手机端页脚公众号入口(2026-09-26):一行"品牌名 + 关注公众号"按钮,点开底部面板;
 * 微信内置浏览器显示二维码 + "长按识别二维码关注";其它浏览器显示公众号名称 +
 * "复制公众号名称"(成功有提示)+ "保存二维码到相册"。
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MobileFollowBar, isWeChatBrowser } from "@/components/trust/MobileFollowBar";
import { FooterDisclaimer } from "@/components/FooterDisclaimer";
import { WECHAT_MP_NAME } from "@/lib/wechat-mp";

const WECHAT_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.44 NetType/WIFI Language/zh_CN";
const SAFARI_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";

function setUA(ua: string) {
  vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ua);
}

beforeEach(() => setUA(SAFARI_UA));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.style.overflow = "";
});

describe("isWeChatBrowser", () => {
  it("UA 含 MicroMessenger(大小写无关)才算微信内置浏览器", () => {
    expect(isWeChatBrowser(WECHAT_UA)).toBe(true);
    expect(isWeChatBrowser("xx micromessenger/8")).toBe(true);
    expect(isWeChatBrowser(SAFARI_UA)).toBe(false);
    expect(isWeChatBrowser("")).toBe(false);
  });
});

describe("MobileFollowBar", () => {
  it("默认只有一行:品牌名 + 关注公众号按钮,没有面板、没有二维码", () => {
    render(<MobileFollowBar />);
    expect(screen.getByText(WECHAT_MP_NAME)).toBeTruthy();
    expect(screen.getByRole("button", { name: "关注公众号" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("img", { name: /二维码/ })).toBeNull();
  });

  it("普通浏览器:面板里是公众号名称 + 复制 + 保存二维码,没有'长按识别'", () => {
    render(<MobileFollowBar />);
    fireEvent.click(screen.getByRole("button", { name: "关注公众号" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain(WECHAT_MP_NAME);
    expect(screen.getByRole("button", { name: "复制公众号名称" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存二维码到相册" })).toBeTruthy();
    expect(dialog.textContent).not.toContain("长按识别二维码关注");
    expect(screen.queryByRole("img", { name: /二维码/ })).toBeNull();
  });

  it("微信内置浏览器:面板里是二维码 + '长按识别二维码关注',没有复制/保存按钮", () => {
    setUA(WECHAT_UA);
    render(<MobileFollowBar />);
    fireEvent.click(screen.getByRole("button", { name: "关注公众号" }));
    expect(screen.getByRole("img", { name: /二维码/ })).toBeTruthy();
    expect(screen.getByText("长按识别二维码关注")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "复制公众号名称" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保存二维码到相册" })).toBeNull();
  });

  it("复制成功:写入公众号名称,并给出提示", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<MobileFollowBar />);
    fireEvent.click(screen.getByRole("button", { name: "关注公众号" }));
    fireEvent.click(screen.getByRole("button", { name: "复制公众号名称" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(WECHAT_MP_NAME));
    expect((await screen.findByRole("status")).textContent).toContain("已复制");
  });

  it("Esc 与点遮罩、点关闭按钮都能关面板,并恢复页面滚动", () => {
    render(<MobileFollowBar />);
    const open = () => fireEvent.click(screen.getByRole("button", { name: "关注公众号" }));
    open();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");

    open();
    fireEvent.click(screen.getByTestId("follow-sheet-overlay"));
    expect(screen.queryByRole("dialog")).toBeNull();

    open();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("点面板内部不会关闭面板", () => {
    render(<MobileFollowBar />);
    fireEvent.click(screen.getByRole("button", { name: "关注公众号" }));
    fireEvent.click(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("FooterDisclaimer", () => {
  it("默认折叠(data-open=false,按钮'展开'),点击后展开并变成'收起'", () => {
    const { container } = render(<FooterDisclaimer first="第一句。" rest="后面的话。" />);
    const p = container.querySelector("p")!;
    expect(p.getAttribute("data-open")).toBe("false");
    const btn = screen.getByRole("button", { name: "展开" });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(btn);
    expect(p.getAttribute("data-open")).toBe("true");
    expect(screen.getByRole("button", { name: "收起" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("全文始终在 DOM 里(桌面端由 CSS 直接展示,搜索引擎/读屏器拿到完整声明)", () => {
    const { container } = render(<FooterDisclaimer first="第一句。" rest="后面的话。" />);
    expect(container.textContent).toContain("第一句。后面的话。");
  });
});
