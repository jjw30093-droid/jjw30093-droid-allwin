# 微信认证与账户(docs/auth-wechat.md)

> 依据真实代码撰写:`backend/auth/{config,providers,service,wechat_webhook}.py`、
> `backend/api/{routes_auth,routes_member,routes_reco,deps}.py`、
> `backend/commands/reco_access.py`、`backend/migrations/platform/{0001_init,0008_qr_webhook_login,0019_code_login_profiles_periods}.sql`、
> `backend/cli/{create_admin,simulate_wechat_scan}.py`(2026-10-01 核对)。
>
> **路线声明(CLAUDE.md §7.3,2026-10 经站长批准修订):唯一微信登录路线是「公众号发码
> 登录」——网页给出 4 位验证码,用户把它发给公众号「足球喵喵第」,微信服务器把这条消息
> 推到本站 webhook 完成批准。** 此前的「带参数二维码」路线只对**已认证服务号**开放
> (微信官方文档:`qrcode/create` 接口权限为「服务号(仅认证)」),站长的公众号是**未认证
> 的个人公众号**,该路线走不通。发码登录只需要「消息推送」能力,未认证个人公众号也有。
> 网页授权(snsapi_base)仍然废弃、不得恢复(网页授权域名要求 ICP 备案)。
>
> **UNVERIFIED 总声明:真实微信端到端(开发者平台消息推送配置、微信服务器真实推送、
> 真机发码)尚未验证。已验证的是:代码逻辑 + 签名 fixture 下的 pytest 全流程与 Playwright
> 浏览器端到端(同一条 webhook 代码路径)。**

## 1. 公众号要配置什么(上线时由站长手动完成)

账号:「足球喵喵第」,公众号(订阅号)、未认证、个人主体。AppID `wx2b9ef558a6a1e425`,
原始 ID `gh_785b28c6a8a0`(两者都是公开标识,不是密钥)。

**2025-12 起,公众号的开发设置已迁到「微信开发者平台」**
(https://developers.weixin.qq.com/platform/ → 我的业务 → 公众号/服务号 → 选中账号):

| 配置项 | 位置 | 填什么 |
|---|---|---|
| 消息推送 URL | 域名与消息推送配置 → 消息推送 | `https://miaomiaodi.vip/api/v1/auth/wechat/webhook` |
| Token | 同上 | 与服务器 `.env` 的 `WECHAT_WEBHOOK_TOKEN` 完全一致(在服务器上用强随机生成,绝不进 Git、不发聊天) |
| 消息加解密方式 | 同上 | **明文模式**(安全模式 AES 未实现) |
| 数据格式 | 同上 | XML |
| AppID | 基础信息 | 写入 `.env` 的 `WECHAT_OA_APP_ID` |
| AppSecret | 基础信息 → 开发密钥 | **登录不需要**(发码登录不调用任何微信接口),不必生成、不必配置 |

保存「消息推送」配置时微信会向 URL 发一次 GET 校验(见 §3 GET 握手),本站必须已部署
且 `WECHAT_AUTH_ENABLED=1` 才能通过。所以顺序是:先在服务器 `.env` 写好
`WECHAT_OA_APP_ID`、`WECHAT_WEBHOOK_TOKEN`、`WECHAT_AUTH_ENABLED=1` 并重启 API,
再去开发者平台点保存。

**启用消息推送的副作用(重要):启用后公众号后台的自动回复、自定义菜单失效**,微信把
所有用户消息推到 webhook,由本站回复。站长确认目前没有在用自动回复/菜单。本站回复规则:

| 用户在公众号里做了什么 | 本站回复 |
|---|---|
| 关注 | 欢迎关注喵弟数据研究室！想登录网站，把网页上的 4 位验证码发给我就行。 |
| 发来 4 位数字且对上一个等待中的登录请求 | 登录成功，请回到网页继续 |
| 发来 4 位数字但没对上(过期/已用/不存在) | 验证码无效或已过期，请回到网页重新获取 |
| 发来别的任何内容(文字、图片、语音…) | 验证码错误，请重新输入(站长定的文案) |
| 同一微信号 10 分钟内发错验证码满 10 次 | 尝试次数过多，请 10 分钟后再试 |
| 取消关注等其它事件 | 不回复(回 `success`) |

`AUTH_DISABLED` 状态下 webhook 回 503(用户在微信里会看到"该公众号暂时无法提供服务")。

## 2. 用户身份、编号与昵称

- 身份键 = `(provider='wechat_oa', provider_app_id=AppID, provider_subject=openid)`;
  openid 取自消息的 `FromUserName`。`users.id`(UUID)仍是唯一业务主键。
- **只有验证码对上了才会建账号**——发错码、乱发消息不会凭空产生用户。
- 未认证公众号拿不到微信昵称/头像(微信 2021-12-27 起也不再对任何账号输出),所以:
  - 每个用户有一个 **6 位用户编号** `users.short_code`(大写十六进制,唯一),后台按编号
    认人,开通权限时让用户报编号;账户中心显示自己的编号;后台用户列表/选择器可按编号搜索;
  - 默认昵称为「球迷 + 编号」;**第一次登录**(`nickname_set=0`)跳转前会请用户起个昵称
    (2–16 个字,允许重名,可跳过),之后可在账户中心修改(`POST /api/v1/account/profile`,
    CSRF 保护)。昵称不是身份凭证。
- 存量密码账号(管理员)迁移时视为已设置昵称,不弹框。

## 3. 发码登录全流程

端点:`POST /auth/wechat/device`、`POST /auth/wechat/device/{id}/claim`、
`GET|POST /auth/wechat/webhook`(全部挂 `/api/v1` 前缀)。
状态持久化在 platform.db `device_login_requests`(不是进程内存字典)。

```text
浏览器 POST /api/v1/auth/wechat/device(限流 10 次/60s/IP)
  → 先把已过期的 pending 请求标 expired
  → 创建 request:{id(公开), secret(32B 只回浏览器,DB 只存 hash),
                   login_code(4 位数字,secrets 安全随机,与所有等待中的请求互不重复,
                              部分唯一索引兜底), status='pending',
                   TTL=DEVICE_REQUEST_TTL_SECONDS(默认 300s)}
  → 返回 {request_id, secret, login_code, expires_at};不调用任何微信接口
  → 等待中的码用完(极端情况)→ 429 "当前登录人数过多"

用户关注公众号「足球喵喵第」,把验证码发过去
  → 微信服务器 POST /api/v1/auth/wechat/webhook?signature&timestamp&nonce
     body=XML:MsgType=text,Content=验证码,FromUserName=openid
  → 校验链(全部通过才处理):
     ① signature = sha1(sorted(token,timestamp,nonce)) 一致,否则 403;
     ② |now - timestamp| ≤ 300s,否则 403;
     ③ nonce 一次性(INSERT OR IGNORE wechat_webhook_nonces);重复 nonce
        = 微信 5 秒未收到应答的原样重试 → 直接回 success,不二次处理;
     ④ body ≤ 64KB;XML 解析失败 → 回 success 静默丢弃(防重试风暴),只留日志
  → 内容规范化(全角转半角、去空白)后必须正好 4 位数字,否则回"验证码错误"
  → 按码找 pending 且未过期的请求;找到才 get_or_create_user_by_identity,
     approve_device_request:UPDATE ... WHERE status='pending' AND 未过期(原子)
  → 被动回复文本(5 秒内,只涉本地 DB,无外呼),文案见 §1

浏览器每 2.5 秒轮询 POST /api/v1/auth/wechat/device/{id}/claim body={secret}(限流 60 次/60s/IP)
  → 五态:pending / forbidden 403(secret 错,不烧毁请求)/ expired·gone 410 /
     claimed(UPDATE ... WHERE status='approved' 的 rowcount=1 原子单次转移,
             第二次领取得 410)
  → claimed 时才创建会话并 Set-Cookie
  → 前端再查 /api/v1/me:nickname_set=false 时先显示起昵称,再跳回原页面
```

安全说明(站长确认的威胁模型:小站,用户量以百计):
- 验证码只决定"批准哪个请求",**领取会话必须有只在发起浏览器内存里的 secret**;
  别人看到你的验证码也领不走你的会话。
- 4 位码在等待中的请求之间唯一;每个微信号 10 分钟最多发错 10 次(只对发错计数,
  正常登录不消耗次数);码 5 分钟过期、只能用一次。
- 与 cc 旧站的区别:旧站用 `random.randint` 生成、登录状态存在进程内存、没有 secret
  领取这一步;本实现码用 `secrets` 生成,状态持久化在 SQLite,会话只能由发起浏览器领取。

GET 握手(开发者平台保存配置时):验签通过原样回显 `echostr`(text/plain),
失败 403。`AUTH_DISABLED` 状态下 GET/POST webhook 均 503。

webhook 是服务器对服务器通道:签名即凭证,不要求 Cookie/CSRF。安全模型 =
共享 Token 签名 + 时间戳窗口 + nonce 防重放;明文模式(安全模式 AES 未实现,如实标注)。

带参数二维码时代的遗留:`device_login_requests.qr_ticket/qr_url` 列、
`wechat_access_token_cache` 表、`RealWechatQrProvider` 代码保留(不做破坏性删除),
登录流程不再使用;以后若公众号认证为服务号,可再评估是否恢复扫码。

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

UnionID:webhook 消息不携带 unionid,当前不保存(`auth_identities.union_id` 字段
保留,将来接入需要 unionid 的接口时才写入,不推测、不伪造)。

## 5. 本地模拟发码(development 专用)

- development 未显式设置 `WECHAT_AUTH_PROVIDER` 时默认 mock(视为已启用,便于本地/E2E)。
- **webhook 入站链路不依赖 Provider**——本地模拟就是对 webhook POST 一条按共享 Token
  签名的文本消息,走的是生产同一条代码路径:
  ```bash
  # 登录页开发环境折叠区会显示带当前验证码的完整命令
  python -m backend.cli.simulate_wechat_scan --code 1234
  python -m backend.cli.simulate_wechat_scan --code 1234 --openid mock-openid-2   # 模拟另一个新用户
  ```
  该 CLI 在 `APP_ENV=production` 下拒绝运行;签名 Token 取 `WECHAT_WEBHOOK_TOKEN`
  (development 默认 `dev-webhook-token`,与后端一致时签名才通过,不绕过任何校验)。
- pytest 用 `tests/backend/authflow.py`(`post_code`、`wechat_code_login`);Playwright 用
  `frontend/e2e/helpers.ts` 的 `sendCodeViaWebhook`(node:crypto 计算 sha1)。

## 6. 认证三态与 Production fail-fast(CLAUDE.md §7.3)

| 状态 | 行为 |
|---|---|
| production + `WECHAT_AUTH_ENABLED=0` | **无微信凭证可启动**;微信端点(device、claim、webhook GET/POST)统一 `503` + `{"code":"AUTH_DISABLED",...}`;密码登录/登出/me 不受影响 |
| production + `WECHAT_AUTH_ENABLED=1` | 缺 `WECHAT_OA_APP_ID` / `WECHAT_WEBHOOK_TOKEN` 或 `PUBLIC_BASE_URL` 非 https → 启动抛 `AuthConfigError` fail-fast;**AppSecret 不再是必填** |
| development + `WECHAT_AUTH_PROVIDER=mock` | 可用;production 检测到 mock → fail-fast |

`GET /api/v1/auth/methods` 返回 `{"wechat_enabled": bool}`(`private, no-store`);
登录页只在 `true` 时显示验证码登录卡片。冒烟:
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
- 接入真实通道前,唯一恢复途径是用同一微信号重新发码登录
  (身份键 = provider+app_id+openid)。

## 9. 每日精选授权(后台操作,CLAUDE.md §8.2)

两种方式并存,任一有效即可看;全程在后台「精选授权」页操作,每次开通/撤销写审计日志;
不再做兑换码(站长 2026-10 决定)。

- **按单场**:`reco_access_grants`(用户 + 单条精选)。
- **按时段**:`reco_access_periods`(用户 + 起止日期,北京时间自然日,含首尾;后台有
  「一周 / 一个月 / 自定义」快捷选项,最长 366 天)。口径(站长定):
  1. 只覆盖**发布时间**(`reco_slips.published_at` 换算北京时间日期)落在期内的精选;
  2. 时段自然结束后,期内发布的精选**仍然能看**;
  3. 提前撤销 = 期内全部收回。
- 用户在账户中心能看到自己的时段与单场授权记录。

## 10. 外部能力验证状态

| 能力 | 状态 |
|---|---|
| webhook 校验链(签名/时间窗/nonce 防重放/XML 解析/验证码匹配/幂等批准/被动回复/发错限次) | 已验证(pytest,签名 fixture 离线) |
| 发码登录全流程(取码→webhook 批准→原子领取→会话/CSRF/撤销;新用户起昵称) | 已验证(pytest `tests/backend/test_auth.py` + Playwright `frontend/e2e/device-login.spec.ts`、`auth.spec.ts`) |
| 时段授权(覆盖口径/到期仍可看/撤销收回/与单场并存/审计) | 已验证(pytest `tests/backend/test_reco_access_periods.py`) |
| 认证三态(production+ENABLED=0 无凭证启动 / AUTH_DISABLED / fail-fast) | 已验证(pytest + uvicorn 子进程冒烟) |
| 开发者平台消息推送配置的 GET 握手 | **UNVERIFIED**(代码已备;上线时按 §1 配置后验证) |
| 微信服务器真实推送、真机发码登录 | **UNVERIFIED**(同上) |
