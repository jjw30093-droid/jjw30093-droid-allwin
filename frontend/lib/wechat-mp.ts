/**
 * 公众号的固定信息(单一真源)。
 *
 * 2026-10-01 起换成站长真实的公众号「足球喵喵第」(原始 ID gh_785b28c6a8a0,未认证个人号):
 * 二维码是站长在公众号后台「账号详情 → 二维码下载」取的 15cm 版(430×430,解码为
 * weixin.qq.com/r/mp/… 关注链接)。此前的 wechat-mp-qr.png(gh_8dab312fa23f,名称写作
 * "喵弟数据研究室")是占位图,已删除。
 *
 * 注意区分:**网站品牌**叫「喵弟数据研究室」(SITE_BRAND_NAME,页脚 logo 旁、标题等);
 * **公众号**叫「足球喵喵第」(WECHAT_MP_NAME,凡是让用户去微信里搜索/关注/发验证码的地方)。
 * 公众号同时是发码登录的入口(components/auth/CodeLoginCard.tsx),两处必须是同一个号。
 */
export const SITE_BRAND_NAME = "喵弟数据研究室";
export const WECHAT_MP_NAME = "足球喵喵第";
export const WECHAT_MP_QR_SRC = "/brand/wechat-oa-qr.jpg";
