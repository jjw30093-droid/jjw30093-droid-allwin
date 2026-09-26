/**
 * 公众号的固定信息(单一真源)。名称沿用 app/about/page.tsx 既有文案
 * "微信公众号:喵弟数据研究室";二维码是微信后台「账号详情」下载的固定图
 * (public/brand/wechat-mp-qr.png,gh_8dab312fa23f),与登录用的带参临时码是
 * 两回事(见 components/trust/WechatFollowCard.tsx 头注)。
 */
export const WECHAT_MP_NAME = "喵弟数据研究室";
export const WECHAT_MP_QR_SRC = "/brand/wechat-mp-qr.png";
