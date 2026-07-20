/**
 * API 基址单一真源(宪法 §10.3)。lib/api-v1.ts 与 lib/api.ts 都从这里取基址,
 * 禁止再各写一份解析逻辑。
 *
 * 解析规则:
 * - 浏览器端:`NEXT_PUBLIC_API_BASE` 非空则用之(development / E2E 跨端口直连);
 *   否则同源相对 `""`(production:浏览器请求 `/api/v1/*` → Cloudflare → Nginx →
 *   FastAPI)。**绝不回退 127.0.0.1** —— 那是服务器进程的回环地址,不是用户浏览器
 *   可达的地址。
 * - 服务端(RSC / route handler):`INTERNAL_API_BASE` > `NEXT_PUBLIC_API_BASE` >
 *   `http://127.0.0.1:8000`(Next 服务端与 FastAPI 同机,走 localhost 内网,不绕
 *   Cloudflare 回源,宪法 §10.2)。
 *
 * 环境变量语义(部署必读,见 .env.example 与 docs/deployment-aws-cloudflare.md):
 * - `NEXT_PUBLIC_API_BASE` 在 `next build` 时被**内联进浏览器产物**,systemd 运行期
 *   改它对已构建 bundle 无效;并且它必须在构建期与服务端运行期取值一致,否则
 *   SSR 与浏览器会渲染出不同的 href(水合不一致)。production 留空。
 * - `INTERNAL_API_BASE` 只在服务端运行期读取,可随 systemd 环境调整,无需重新构建。
 */

export interface ApiBaseInputs {
  /** 是否浏览器环境(运行时取 typeof window;测试可注入)。 */
  isBrowser: boolean;
  /** NEXT_PUBLIC_API_BASE(构建期内联进浏览器产物)。 */
  publicBase: string | undefined;
  /** INTERNAL_API_BASE(仅服务端运行期可见)。 */
  internalBase: string | undefined;
  /**
   * 服务端三级兜底(浏览器分支绝不使用)。由调用方传入而不是写死在本函数:
   * 回环地址字面量因此只存在于服务端专用调用点 serverApiBase,客户端产物在
   * export 级 tree shaking 下会把它连同 serverApiBase 一起裁掉——
   * deploy/scripts/check_browser_bundle.sh 在每次构建后机器验证这一点。
   */
  serverFallback: string;
}

/** 空串/全空白视同未设置;去掉尾部斜杠,避免与 `/api/v1/...` 拼出双斜杠。 */
function normalize(value: string | undefined): string | undefined {
  const v = value?.trim();
  if (!v) return undefined;
  return v.replace(/\/+$/, "");
}

/** 纯函数解析(依赖注入,vitest 直接覆盖矩阵);运行时用下面两个包装。 */
export function resolveApiBase(inputs: ApiBaseInputs): string {
  const pub = normalize(inputs.publicBase);
  if (inputs.isBrowser) {
    // 浏览器:显式设置优先(dev/E2E),否则同源相对。绝不回退 127.0.0.1。
    return pub ?? "";
  }
  return normalize(inputs.internalBase) ?? pub ?? inputs.serverFallback;
}

/**
 * 浏览器语义基址:同一份构建里 SSR 与浏览器结果一致(NEXT_PUBLIC_API_BASE 内联)。
 * 用于浏览器发起的 fetch 与写进 HTML 的 `<a href>`。
 */
export function clientApiBase(): string {
  return resolveApiBase({
    isBrowser: true,
    publicBase: process.env.NEXT_PUBLIC_API_BASE,
    internalBase: undefined,
    serverFallback: "",
  });
}

/**
 * 服务端兜底回环地址。只被 serverApiBase 引用,禁止在其他地方(尤其是任何
 * 浏览器可达代码)引用这个常量或复写同样的字面量。
 */
const SERVER_LOOPBACK_FALLBACK = "http://127.0.0.1:8000";

/**
 * 服务端(RSC / route handler)基址;每次调用运行期读取 env(INTERNAL_API_BASE
 * 可随 systemd 环境变更)。产出的绝对地址不得写进 HTML 交给浏览器使用。
 * 防御:若被误在浏览器调用,退回浏览器语义,绝不把回环地址给浏览器。
 */
export function serverApiBase(): string {
  return resolveApiBase({
    isBrowser: typeof window !== "undefined",
    publicBase: process.env.NEXT_PUBLIC_API_BASE,
    internalBase: process.env.INTERNAL_API_BASE,
    serverFallback: SERVER_LOOPBACK_FALLBACK,
  });
}
