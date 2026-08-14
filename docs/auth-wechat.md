# 微信认证与账户(docs/auth-wechat.md)

> 依据真实代码撰写:`backend/auth/{config,providers,service,wechat_webhook,entitlements}.py`、
> `backend/api/{routes_auth,deps}.py`、`backend/migrations/platform/{0001_init,0008_qr_webhook_login}.sql`、
> `backend/cli/{create_admin,simulate_wechat_scan}.py`(2026-08-08 核对)。
>
> **路线声明(CLAUDE.md §7.3,修改经用户批准):唯一登录路线是「带参数二维码 +
> 消息推送 webhook」。网页授权(snsapi_base)已废弃且不得恢复——「网页授权域名」
> 要求 ICP 备案,备案硬前提是大陆服务器;本项目部署 AWS 东京且不迁回大陆备案。
> 带参二维码路线全程不在微信内打开本站网页,不受备案约束。**
>
> **UNVERIFIED 总声明:真实微信端到端(真实 AppID/AppSecret、公众号后台服务器配置、
> 微信服务器真实回调、真机扫码)尚未验证。已验证的是:代码逻辑 + Mock Provider +
> 签名 fixture 下的 pytest 全流程与 Playwright 浏览器端到端(同一条 webhook 代码路径)。**

## 1. 公众号后台需要配置什么(上线时由站长手动完成)

前提:已微信认证的**服务号**(蓝V,具备「生成带参数的二维码」接口权限)。

| 配置项 | 位置 | 填什么 |
|---|---|---|
| 服务器配置 URL | 设置与开发 → 基本配置 → 服务器配置 | `https://<域名>/api/v1/auth/wechat/webhook` |
| 服务器配置 Token | 同上 | 与 `.env` 的 `WECHAT_WEBHOOK_TOKEN` 一致(强随机,绝不进 Git) |
| 消息加解密方式 | 同上 | 明文模式或兼容模式(**安全模式(AES)本轮未实现**) |
| AppID / AppSecret | 基本配置 | 写入 `.env` 的 `WECHAT_OA_APP_ID` / `WECHAT_OA_APP_SECRET` |
| IP 白名单 | 基本配置 | 服务器出口 IP(`/cgi-bin/token`、`/cgi-bin/qrcode/create` 调用来源) |

保存「服务器配置」时微信会向 URL 发一次 GET 校验(见 §3 GET 握手),本站必须已部署
且 `WECHAT_AUTH_ENABLED=1` 才能通过。

**启用服务器配置的副作用(重要):启用后公众号的自动回复、菜单等由开发者接管,
微信会把所有用户消息推到 webhook。本站对非登录事件统一回 `success`(静默),
对 `AUTH_DISABLED` 状态回 503(用户在微信里会看到"该公众号暂时无法提供服务")。**

已知外部单点(如实):「生成带参数的二维码」权限绑定微信认证**年审**;年审过期时
`qrcode/create` 返回 `errcode=48001`,本站结构化记录并对浏览器返回 502
"微信扫码服务暂时不可用"。本轮不做降级通道(OTP 第二通道是下一轮独立课题)。

## 2. access_token(`providers.RealWechatQrProvider`)

- `GET api.weixin.qq.com/cgi-bin/token`(client_credential)获取,有效期约 7200s。
- **每个 AppID 全局唯一,重新获取会使上一个立即失效** → 缓存持久化在 platform.db
  `wechat_access_token_cache`(app_id 主键),临过期(<300s)才刷新;刷新用进程内锁
  串行化,网络请求不持任何 SQLite 写锁(§5.3 短事务)。
- token 失效类 errcode(40001/40014/42001)→ 清缓存重取**一次**,不无限重试。
- access_token 是服务端敏感凭据:只存在缓存表与服务端请求中,不进日志、不进 API 响应。

## 3. 扫码登录全流程

端点:`POST /auth/wechat/device`、`POST /auth/wechat/device/{id}/claim`、
`GET|POST /auth/wechat/webhook`(全部挂 `/api/v1` 前缀)。
状态持久化在 platform.db `device_login_requests`(不是进程内存字典)。

```text
浏览器 POST /api/v1/auth/wechat/device(限流 10 次/60s/IP)
  → 创建 request:{id(公开), secret(32B 只回浏览器,DB 只存 hash),
                   status='pending', TTL=DEVICE_REQUEST_TTL_SECONDS(默认 300s)}
  → 服务端调公众号 qrcode/create(QR_STR_SCENE, scene_str=request id,
     expire_seconds 与 request TTL 对齐)→ ticket/url 存回 request 行
  → 返回 {request_id, secret, qr_url(微信带参二维码 URL), expires_at}
  → 前端 npm `qrcode` 在本地 <canvas> 渲染 qr_url(不经第三方图片服务);
     创建失败(如 48001)→ 502,request 留给 TTL 自然过期(无 QR 指向,无害)

用户扫码(桌面:手机扫屏幕;微信内:长按识别;手机浏览器:截图后相册识别)
  → 微信服务器 POST /api/v1/auth/wechat/webhook?signature&timestamp&nonce
     body=XML:已关注 → Event=SCAN,EventKey=scene_str;
              未关注 → Event=subscribe,EventKey=qrscene_<scene_str>(关注即登录)
  → 校验链(全部通过才处理):
     ① signature = sha1(sorted(token,timestamp,nonce)) 一致,否则 403;
     ② |now - timestamp| ≤ 300s,否则 403;
     ③ nonce 一次性(INSERT OR IGNORE wechat_webhook_nonces);重复 nonce
        = 微信 5 秒未收到应答的原样重试 → 直接回 success,不二次处理;
     ④ body ≤ 64KB;XML 解析失败 → 回 success 静默丢弃(防重试风暴),只留日志
  → get_or_create_user_by_identity('wechat_oa', app_id, openid)
     (users.id UUID 是唯一业务主键;openid 只是可绑定外部身份)
  → approve_device_request:UPDATE ... WHERE status='pending' AND 未过期(原子);
     已 approved/claimed → 幂等,不改状态
  → 被动回复文本(5 秒内,处理只涉本地 DB 无外呼):
     "登录成功,请回到浏览器继续" / "已确认登录…" / "二维码已过期…" / "二维码无效…"

浏览器轮询 POST /api/v1/auth/wechat/device/{id}/claim body={secret}(限流 60 次/60s/IP)
  → 五态:pending / forbidden 403(secret 错,不烧毁请求)/ expired·gone 410 /
     claimed(UPDATE ... WHERE status='approved' 的 rowcount=1 原子单次转移,
             第二次领取得 410)
  → claimed 时才创建会话并 Set-Cookie
```

GET 握手(公众号后台保存配置时):验签通过原样回显 `echostr`(text/plain),
失败 403。`AUTH_DISABLED` 状态下 GET/POST webhook 均 503。

webhook 是服务器对服务器通道:签名即凭证,不要求 Cookie/CSRF。安全模型 =
共享 Token 签名 + 时间戳窗口 + nonce 防重放;明文模式(安全模式 AES 未实现,
如实标注)。攻击者知道公开 request id 但没有 Token 无法伪造签名,没有浏览器
secret 无法领取会话。

明确淘汰的旧项目模式(CLAUDE.md §7.3):四位随机验证码、进程内登录状态、
JWT 进查询参数/客户端 session、`users.openid='USER_xxx'` 伪身份——本实现均不存在。
旧网页授权残留:`oauth_states` 表保留(不做破坏性删除)但无代码路径写入;
oa/start、oa/callback 端点已删除(回归测试钉死 404)。

## 4. 会话与 CSRF(opaque session)

`auth/service.py` + `api/deps.py` + `auth_sessions` 表:

- 登录生成 256 bit(32 字节)随机 token 与独立 CSRF token;**数据库只存 SHA-256**,
  原始值只在 Set-Cookie 时出现一次,不记日志。
- 会话 Cookie `allwin_session`:`HttpOnly`、`SameSite=Lax`、`Secure`(production 恒开,
  development 可用 `COOKIE_SECURE=1` 强开)、host-only(不设 domain)、`Path=/api/v1`,
  TTL=`SESSION_TTL_DAYS`(默认 30 天)。
- CSRF Cookie `allwin_csrf`:JS 可读(httponly=False)、`Path=/`;写请求走双提交:
  前端把值放进 `X-CSRF-Token` 头,`require_csrf` 依赖校验 hash 一致 **且**
  Origin/Referer 命中 allowlist(`ALLOWED_ORIGINS` ∪ `PUBLIC_BASE_URL`)。
- 会话可撤销:`revoke_session`/`revoke_all_sessions`(`/api/v1/auth/logout`、
  `/api/v1/account/sessions/revoke`);过期/撤销/用户 disabled 的会话一律视为未登录。
- 登录、webhook、会话、账户接口全部 `Cache-Control: private, no-store`。
- 已知偏差(如实记录):CLAUDE.md §7.4 要求"登录后轮换"会话 token;当前实现
  每次登录新建会话,但没有"同一会话使用中再轮换"的机制。

`/api/v1/me`:匿名返回 `authenticated=false + free plan + free entitlements`;
登录返回 user/plan/entitlements/session_expires_at。权益解析见 `auth/entitlements.py`。

UnionID:webhook 事件不携带 unionid,当前不保存(`auth_identities.union_id` 字段
保留,将来接入需要 unionid 的接口时才写入,不推测、不伪造)。

## 5. Mock Provider 与本地模拟扫码(development 专用)

- 触发条件:`WECHAT_AUTH_PROVIDER=mock`(development 未显式设置时的默认值)。
- `MockWechatProvider.create_login_qrcode` 不发网络,返回
  `url=https://example.invalid/mock-wechat-qr/<request_id>`(前端照常渲染 canvas)。
- **webhook 入站链路不依赖 Provider**——测试/本地模拟扫码就是对 webhook POST 一条
  按共享 Token 签名的 SCAN 事件,走的是生产同一条代码路径:
  ```bash
  # 登录页开发环境折叠区可复制 request id
  python -m backend.cli.simulate_wechat_scan --request-id <request_id>
  ```
  该 CLI 在 `APP_ENV=production` 下拒绝运行;签名 Token 取 `WECHAT_WEBHOOK_TOKEN`
  (development 默认 `dev-webhook-token`,与后端一致时签名才通过,不绕过任何校验)。
- pytest 用 `tests/backend/authflow.py` 的同款 helper;Playwright 用
  `frontend/e2e/helpers.ts` 的 `approveViaWebhook`(node:crypto 计算 sha1)。

## 6. 认证三态与 Production fail-fast(CLAUDE.md §7.3)

| 状态 | Provider | 行为 |
|---|---|---|
| production + `WECHAT_AUTH_ENABLED=0` | `DisabledWechatProvider`(显式占位) | **无微信凭证可启动**;微信端点(device、claim、webhook GET/POST)统一 `503` + `{"code":"AUTH_DISABLED",...}`;密码登录/登出/me 不受影响 |
| production + `WECHAT_AUTH_ENABLED=1` | 只能 `RealWechatQrProvider` | 缺 AppID / AppSecret / `WECHAT_WEBHOOK_TOKEN` 或 `PUBLIC_BASE_URL` 非 https → 启动抛 `AuthConfigError` fail-fast |
| development + `WECHAT_AUTH_PROVIDER=mock` | `MockWechatProvider` | 可用(mock 视为已启用,便于本地/E2E);production 检测到 mock → fail-fast |

`GET /api/v1/auth/methods` 返回 `{"wechat_enabled": bool}`(`private, no-store`);
登录页据此显示"微信登录暂未开放"。冒烟:
`tests/backend/test_auth.py::TestProductionDisabledUvicornSmoke` 以子进程真实启动
uvicorn(production+ENABLED=0 无凭证)验证 healthz 200、POST device 503。

## 7. 管理员账号(create_admin CLI)

密码登录仅用于 CLI 创建的 admin(`POST /api/v1/auth/password/login`,
限流 5 次/60s/IP,Argon2 校验,统一 401 文案不泄露用户是否存在)。

```bash
# 交互式(getpass,输入不回显,不进 shell 历史)
.venv/bin/python -m backend.cli.create_admin --username admin

# 重置已有账号密码
.venv/bin/python -m backend.cli.create_admin --username admin --reset-password

# 非交互(CI;环境变量传入)
ALLWIN_ADMIN_PASSWORD=... .venv/bin/python -m backend.cli.create_admin --username admin
```

## 8. 账号恢复现状(如实)

- MVP 未接入短信/邮件服务。`account_links` 表已预留 recovery_email/recovery_phone
  字段,但**没有任何发送与验证实现**。
- `GET /api/v1/account` 明确返回
  `recovery: {available: false, note: "当前仅微信登录,尚未支持绑定备用恢复方式"}`,
  前端必须如实展示,不得暗示已有恢复能力。
- 接入真实通道前,唯一恢复途径是同一微信号重新扫码登录
  (身份键 = provider+app_id+openid)。

## 9. 外部能力验证状态

| 能力 | 状态 |
|---|---|
| webhook 校验链(签名/时间窗/nonce 防重放/XML 解析/幂等批准/被动回复) | 已验证(pytest,签名 fixture 离线) |
| 扫码登录全流程(创建→webhook 批准→原子领取→会话/CSRF/撤销) | 已验证(pytest `tests/backend/test_auth.py` + Playwright `frontend/e2e/device-login.spec.ts`、`auth.spec.ts`) |
| access_token 缓存(单次获取/临期刷新/失效重取一次/48001 结构化) | 已验证(pytest,httpx.MockTransport 离线) |
| 认证三态(production+ENABLED=0 无凭证启动 / AUTH_DISABLED / fail-fast 含 webhook Token) | 已验证(pytest + uvicorn 子进程冒烟) |
| 真实 access_token 获取、qrcode/create、微信服务器真实回调、真机扫码 | **UNVERIFIED**(无真实凭证;上线时按 §1 配置后验证) |
| 公众号后台服务器配置 URL/Token 握手 | **UNVERIFIED**(同上;GET 握手代码已备) |
| 年审过期 48001 的真实表现 | **UNVERIFIED**(代码按结构化错误处理,fixture 已覆盖) |
