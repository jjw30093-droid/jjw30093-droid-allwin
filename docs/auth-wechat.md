# 微信认证与账户(docs/auth-wechat.md)

> 依据真实代码撰写:`backend/auth/{config,providers,service,entitlements}.py`、
> `backend/api/{routes_auth,deps}.py`、`backend/migrations/platform/0001_init.sql`、
> `backend/cli/create_admin.py`(2026-07-19 核对)。
>
> **UNVERIFIED 总声明:真实微信端到端流程(真实 AppID/AppSecret、公众号后台配置、
> 微信服务器回调)尚未在本项目验证过。已验证的是:代码逻辑 + Mock Provider 下的
> pytest 全流程(state 一次性、设备扫码原子领取、会话撤销、CSRF)。**

## 1. 公众号后台需要配置什么

前提:已认证**服务号**(网页授权能力),域名已在 Cloudflare 托管且 ICP 备案状态满足微信要求。

| 配置项 | 位置 | 填什么 |
|---|---|---|
| 网页授权域名 | 公众号后台 → 设置与开发 → 公众号设置 → 功能设置 | `ALLWIN_DOMAIN`(不带协议;需上传微信校验文件到站点根路径) |
| AppID / AppSecret | 设置与开发 → 基本配置 | 写入服务器 `.env` 的 `WECHAT_OA_APP_ID` / `WECHAT_OA_APP_SECRET`,绝不进 Git |
| IP 白名单 | 基本配置 | 服务器出口 IP(`/sns/oauth2/access_token` 调用来源) |

UnionID:仅当公众号绑定到微信开放平台且微信真实返回 `unionid` 时才保存
(`auth_identities.union_id`,`WechatIdentity.union_id`),代码不推测、不伪造。

## 2. OAuth 流程(手机 / 微信内,snsapi_base)

端点:`GET /api/v1/auth/wechat/oa/start`、`GET /api/v1/auth/wechat/oa/callback`(`routes_auth.py`)。

```text
用户点击"登录"(前端不得在页面加载时自动跳转)
  │
  ▼
GET /api/v1/auth/wechat/oa/start?next=/xxx
  ├─ 限流:每 IP 20 次/60s(ratelimit.limiter)
  ├─ next 校验:只允许本站相对路径(is_safe_next_path:必须以单个 / 开头,
  │            禁止 //、\\、scheme)→ 不合法降级为 /
  ├─ 生成一次性 state(32 字节随机;DB 只存 SHA-256,TTL=OAUTH_STATE_TTL_SECONDS,默认 600s)
  └─ 302 → open.weixin.qq.com/connect/oauth2/authorize
             ?appid=...&redirect_uri=<PUBLIC_BASE_URL>/api/v1/auth/wechat/oa/callback
             &response_type=code&scope=snsapi_base&state=...#wechat_redirect
  │
  ▼ 用户在微信内授权,微信 302 回:
GET /api/v1/auth/wechat/oa/callback?code=...&state=...
  ├─ consume_oauth_state:UPDATE ... WHERE used_at IS NULL AND expires_at > now
  │   的 rowcount 判定 → 原子一次性消费;无效/过期/重放一律同一 400 响应,不泄露区分
  ├─ provider.exchange_code(code):服务端 GET api.weixin.qq.com/sns/oauth2/access_token
  │   (AppSecret 只存在服务端;微信错误详情不透传客户端,失败 502)
  ├─ get_or_create_user_by_identity('wechat_oa', app_id, openid[, union_id])
  │   → 内部 users.id(UUID)是唯一业务主键;OpenID 只是可绑定的外部身份
  └─ 创建网站自己的会话,种 Cookie,302 → FRONTEND_BASE_URL + next
```

回调地址固定为 `PUBLIC_BASE_URL + /api/v1/auth/wechat/oa/callback`(`_callback_uri`),
不接受请求方传入的 redirect,配合 next 相对路径校验杜绝开放重定向。

## 3. Device Login(电脑扫码)

端点:`POST /api/v1/auth/wechat/device`、`POST /api/v1/auth/wechat/device/{request_id}/claim`。
状态持久化在 platform.db `device_login_requests` 表(不是进程内存字典)。

```text
电脑浏览器 POST /api/v1/auth/wechat/device(限流 10 次/60s/IP)
  → 服务端创建 request:{id(公开), secret(32 字节,只回给浏览器,DB 只存 hash),
                        status='pending', TTL=DEVICE_REQUEST_TTL_SECONDS(默认 300s)}
  → 返回 {request_id, secret, qr_url, expires_at}
  → 二维码内容 = PUBLIC_BASE_URL/api/v1/auth/wechat/oa/start?device=<request_id>
    (二维码只含公开 request id,绝不含浏览器 secret)

手机微信扫码 → 走上面同一个公众号 OAuth(state.kind='device_approve')
  → callback 中 approve_device_request:UPDATE ... WHERE status='pending' AND 未过期
    (原子;成功返回 JSON"已批准登录,请回到电脑继续",手机端不建会话)

电脑轮询 POST /api/v1/auth/wechat/device/{id}/claim  body={secret}(限流 60 次/60s/IP)
  → claim_device_request 返回五态:
     pending(继续轮询)/ forbidden 403(secret 错)/ expired·gone 410 /
     claimed(UPDATE ... WHERE status='approved' 的 rowcount=1 原子单次转移,
             第二次领取得 gone)
  → claimed 时才创建会话并 Set-Cookie
```

明确淘汰的旧项目模式(CLAUDE.md §7.3):四位随机验证码、进程内登录状态、
JWT 进查询参数/客户端 session、`users.openid='USER_xxx'` 伪身份——本实现均不存在。

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
- 登录、回调、会话、账户接口全部 `Cache-Control: private, no-store`。
- 已知偏差(如实记录):CLAUDE.md §7.4 要求"登录后轮换"会话 token;当前实现
  每次登录新建会话,但没有"同一会话使用中再轮换"的机制。

`/api/v1/me`:匿名返回 `authenticated=false + free plan + free entitlements`;
登录返回 user/plan/entitlements/session_expires_at。权益解析见 `auth/entitlements.py`
(有效订阅中 rank 最高 plan 的 entitlement 集合,Role 与付费能力分离)。

## 5. Mock Provider(development 专用)

- 触发条件:`WECHAT_AUTH_PROVIDER=mock`(development 未显式设置时的默认值,
  见 `load_auth_settings`)。
- 行为(`MockWechatProvider`):`authorize_url` 不去微信,直接把浏览器 302 回本站
  callback 并附 `code=mock-user-1`;`exchange_code` 只接受 `mock-` 前缀 code,
  产出 `openid=mock-openid-<subject>`。
- 用途:本地/E2E 无需真实公众号即可走完整 OAuth + Device Login 流程
  (`tests/backend/test_auth.py` 即用它)。
- 想 mock 不同用户:直接调用 callback 并携带合法 state 与 `code=mock-<任意>`。
- Mock 视为"已启用微信登录"(`_ensure_wechat_enabled`:仅 real 才要求
  `WECHAT_AUTH_ENABLED=1`),便于本地默认可登录。

## 6. Production fail-fast 条件

应用启动即 `load_auth_settings()`(`api/app.py`),`APP_ENV=production` 时以下任一
条件直接抛 `AuthConfigError` 拒绝启动:

1. `WECHAT_AUTH_PROVIDER` 不是 `real`(Mock 只允许 development;
   `build_provider` 里再拦一次,双保险);
2. `WECHAT_AUTH_ENABLED=1` 且缺 `WECHAT_OA_APP_ID` 或 `WECHAT_OA_APP_SECRET`;
3. `WECHAT_AUTH_ENABLED=1` 且 `PUBLIC_BASE_URL` 不是 `https://` 地址。

另:production 下 Cookie 强制 `Secure`;`RealWechatOAProvider` 构造时缺
AppID/AppSecret 同样抛错。

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

行为:密码 ≥8 位;写 `users`(role='admin', password_hash=Argon2)+
`auth_identities(provider='password', provider_subject=<username>)` + `audit_logs`。

## 8. 账号恢复现状(如实)

- MVP 未接入短信/邮件服务。`account_links` 表已预留 recovery_email/recovery_phone
  字段,但**没有任何发送与验证实现**。
- `GET /api/v1/account` 明确返回
  `recovery: {available: false, note: "当前仅微信登录,尚未支持绑定备用恢复方式"}`,
  前端必须如实展示,不得暗示已有恢复能力。
- 付费用户绑定恢复身份属后续工作;接入真实通道前,唯一恢复途径是同一微信号重新登录
  (身份键 = provider+app_id+openid)。

## 9. 外部能力验证状态

| 能力 | 状态 |
|---|---|
| Mock Provider 全流程(OAuth/Device/会话/CSRF/撤销) | 已验证(pytest,`tests/backend/test_auth.py`) |
| 微信 authorize 跳转、code 换 openid、UnionID 返回 | **UNVERIFIED**(无真实凭证) |
| 公众号后台网页授权域名/IP 白名单配置 | **UNVERIFIED**(上线时按 §1 配置后验证) |
| 真机微信内 H5 + 电脑扫码端到端 | **UNVERIFIED** |
