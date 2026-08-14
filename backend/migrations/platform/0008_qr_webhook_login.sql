-- 0008: 微信登录从「网页授权」切换到「带参数二维码 + webhook 事件」。
--
-- 背景(已获用户批准修改 CLAUDE.md §7.3):网页授权域名要求 ICP 备案,本项目部署
-- AWS 东京且不迁回大陆备案,网页授权在当前形态下不可用。替代路线为:
-- 浏览器创建 device_login_request → 服务端用公众号「生成带参数二维码」接口创建
-- 临时二维码(scene_str = request id)→ 用户微信扫码 → 微信服务器 POST 事件到
-- 本站 webhook → 服务端按 openid 批准 request → 浏览器携 secret 轮询领取。
-- device_login_requests 的一次性 secret / 原子领取骨架原样保留。

-- 带参二维码信息挂在已有 request 上(可空:创建 QR 失败/尚未创建时为 NULL)
ALTER TABLE device_login_requests ADD COLUMN qr_ticket TEXT;
ALTER TABLE device_login_requests ADD COLUMN qr_url TEXT;

-- 公众号 access_token 缓存:每个 AppID 全局唯一,重新获取会使上一个立即失效,
-- 因此必须跨进程共享同一份(进程内存缓存会在多进程/重启场景互相顶掉)。
-- access_token 属服务端敏感凭据:只存在本表与请求微信 API 的服务端调用中,
-- 不进日志、不进任何 API 响应。
CREATE TABLE wechat_access_token_cache (
  app_id       TEXT PRIMARY KEY,
  access_token TEXT NOT NULL,
  expires_at   TEXT NOT NULL,              -- UTC ISO;含提前刷新余量前的原始过期时刻
  fetched_at   TEXT NOT NULL
);

-- webhook 防重放:同一 (timestamp, nonce) 签名可在时间窗内被重放,
-- nonce 一次性登记;seen_at 早于清理窗口的行在每次写入时顺手删除。
CREATE TABLE wechat_webhook_nonces (
  nonce   TEXT PRIMARY KEY,
  seen_at TEXT NOT NULL
);
