# 部署:AWS(东京)+ Cloudflare(P0.11)

> 本文是部署材料的说明书。所有文件都已在仓库准备好(`deploy/`),**本机不执行任何线上操作**;
> 真正上线时按本文在服务器上执行。占位符:`ALLWIN_DOMAIN` = 真实域名。

## 1. 拓扑(东京 EC2 单机)

```
用户(中国大陆/海外)
   │  DNS + CDN + WAF + 边缘缓存
   ▼
Cloudflare(橙云代理,TLS Full strict)
   │  443(Origin Certificate)
   ▼
EC2(ap-northeast-1 东京,单机)
   └─ Nginx :443(deploy/nginx/allwin.conf.example)
        ├─ /api/*、/healthz、/readyz → 127.0.0.1:8000  uvicorn(allwin-api.service)
        └─ 其余                      → 127.0.0.1:3000  next start(allwin-web.service)
   └─ /opt/allwin/shared/data/  SQLite 三库(core=allwin.db / platform.db / odds.db,WAL)
   └─ allwin-worker.timer(15min)→ python -m backend.worker.runner --chain
   └─ allwin-backup.timer(每日)→ deploy/scripts/backup_sqlite.sh → 本地 + S3
```

单机原则:uvicorn/next 只监听 127.0.0.1,公网只有 Cloudflare → Nginx 一条通路;
安全组只放行 80/443(Cloudflare IP 段)+ 22(管理 IP)。

目录布局(release.sh 维护):

```
/opt/allwin/
├── source/                  # git 检出(发布时 fetch/checkout)
├── releases/<git-sha>/      # 不可变 release(代码 + .venv + .next)
├── current -> releases/<sha>  # systemd 单元统一指向的软链
└── shared/
    ├── .env                 # 生产环境变量唯一副本(权限 600,属主 allwin)
    └── data/                # SQLite 三库;ALLWIN_DATA_DIR 指到这里,发布/回滚不动数据
```

## 2. systemd 单元职责(deploy/systemd/)

| 单元 | 职责 |
|---|---|
| `allwin-api.service` | uvicorn `backend.api.app:app`,仅 127.0.0.1:8000,常驻 |
| `allwin-web.service` | `next start`,仅 127.0.0.1:3000,常驻 |
| `allwin-worker.service` + `allwin-worker.timer` | oneshot 任务链,每 15 分钟触发,`--chain --periodic`(见下"调度拓扑"小节);链内文件锁防叠跑,job_runs(platform.db)留全生命周期记录 |
| `allwin-poll.service` + `allwin-poll.timer` | oneshot,每 5 分钟触发赛前采集"到期判断"(NowGoal 赔率 + FotMob 阵容/伤停);真正是否请求数据源由 poll_state 节流(2–72h 每 15 分钟,0–2h 每 5 分钟) |
| `allwin-backup.service` + `allwin-backup.timer` | 每日 UTC 19:00 备份三库(.backup + integrity_check + checksum + 可选 S3) |

共同点:`User=allwin`(非 root 系统用户)、`WorkingDirectory=/opt/allwin/current`、
`EnvironmentFile=/opt/allwin/shared/.env`、`UMask=0077`、`NoNewPrivileges=true`、
`PrivateTmp=true`、`ProtectSystem=full`、合理的 `TimeoutStartSec`/`TimeoutStopSec`。
安装/更新(每次改动 `deploy/systemd/*` 后都要做,普通代码 release **不会**自动生效):

```bash
sudo cp deploy/systemd/allwin-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now allwin-api allwin-web allwin-worker.timer \
  allwin-poll.timer allwin-backup.timer
# 确认服务器上安装的 unit 与当前 release 模板一致(避免手改漂移):
diff /etc/systemd/system/allwin-worker.service \
  /opt/allwin/current/deploy/systemd/allwin-worker.service
```

如果本机装有 `systemd-analyze`,发布前用它做静态校验:
`systemd-analyze verify /etc/systemd/system/allwin-*.service`(本仓库开发/测试机是
macOS,没有 systemd,这一步在开发环境**保持 UNVERIFIED**,只在真实服务器上跑)。

### 调度拓扑:避免 poll 与 worker 链重复调度

`nowgoal_snapshot`/`fotmob_snapshot` 曾经同时被两个定时器调度:`allwin-poll.timer`
每 5 分钟独立触发,`allwin-worker.timer` 每 15 分钟的默认任务链里也包含这两步。
两个定时器会争抢同一个 `data/locks/<job>.lock`,而 `run_chain()` 把"被锁"与
"失败"同等对待,会让 15 分钟链上其余全部步骤(silver build/model predict/
analysis bundle/postmatch settle/manifest——都不依赖 nowgoal/fotmob 本轮是否真的
抓取到新数据)被无谓地级联跳过。现在:

- `allwin-poll.timer` 是这两步**唯一**的周期调度者(5 分钟一次到期判断);
- `allwin-worker.service` 改用 `--chain --periodic`,`--periodic` 会跳过
  `nowgoal_snapshot`/`fotmob_snapshot`(`backend/worker/runner.py` 的
  `PERIODIC_CHAIN_EXCLUDE`);
- 手动端到端重跑仍用完整链(不带 `--periodic`):
  `/opt/allwin/current/.venv/bin/python -m backend.worker.runner --chain`。

`allwin-poll.service` 本身也不再是两条独立 `ExecStart=`(NowGoal 失败会让 systemd
跳过后面那条 FotMob 的 ExecStart=,与"两个采集任务独立执行"的设计矛盾)——现在
用 `backend.worker.poll_wrapper` 顺序执行两个任务、汇总退出码,两者各自的真实
状态仍完整写入 `job_runs`,只是 service 层面的成功/失败判定不再互相拖累。

## 3. 发布 / 回滚(deploy/scripts/release.sh)

发布(在服务器上):

```bash
cd /opt/allwin/source && git fetch && git checkout <ref>
bash deploy/scripts/release.sh
```

脚本内部顺序(`set -euo pipefail`,任一步失败即停;每个阶段是独立的 bash 函数,
`RELEASE_SH_SOURCE_ONLY=1` 可单独 source 后调用某个函数做离线单元测试,
见 `tests/backend/test_release_rollback.py`):

0. **preflight**(在任何构建/备份/migration/切换之前完成全部检查):
   - 必需命令齐全(git/rsync/python3/npm/curl/sqlite3/sudo/systemctl);
   - `shared/.env`、`shared/data/{allwin,platform,odds}.db` 三库都存在;
   - 磁盘可用空间 ≥ `MIN_FREE_DISK_MB`(默认 2048MB,可覆盖);
   - `current` 软链形状合法(不存在,或指向 `releases/` 下的目录——不是普通目录、
     不是指向仓库外的路径);
   - **默认拒绝 dirty 源码树**:`source/` 有任何未提交/未跟踪改动直接拒绝发布;
   - **release 一旦构建即不可变**:目标 `releases/<sha>/` 已存在就拒绝覆盖
     (不会对同一 SHA 重新 rsync);
1. rsync 代码到 `releases/<git-sha>/`(不可变目录);排除列表已扩展到
   `.git/.venv/node_modules/data/__pycache__/.next/.env/.env.*/frontend/.env.local/
   .pytest_cache/test-results/playwright-report/.claude/.codex`,以及通用私钥/证书/
   凭证文件——`.ssh/.aws` 整个目录、`id_rsa(.pub)/id_dsa(.pub)/id_ecdsa(.pub)/
   id_ed25519(.pub)`、`*.pem/*.key/*.p12/*.pfx`、`credentials.json`(根目录和任意
   嵌套子目录都生效,rsync 的 basename 匹配语义天然覆盖嵌套路径,不需要额外配置;
   只排除这些具体文件名/扩展名,不粗暴排除所有 `*.json`,普通 JSON 文件仍会被
   复制)——生产配置只能来自 `shared/.env`,任何秘密/测试产物都不应该、也不会
   进入不可变 release 目录。`do_rsync()` 之后还有一道 `assert_no_credentials_in_release`
   纵深防御扫描(复制完成后再确认没有已知凭证文件混入),但这只是双保险,主
   机制始终是上面的 rsync `--exclude` 列表;
2. release 内独立 `.venv` + `pip install -r requirements.txt`;
3. **先 `set -a; . shared/.env; set +a` 再** `npm ci && npm run build`(frontend)——
   `NEXT_PUBLIC_*` 是构建期内联,systemd 运行期注入改不了已构建产物(宪法 §10.3);
   构建后立即跑 `deploy/scripts/check_browser_bundle.sh`:`.next/static` 浏览器产物
   含 `http://127.0.0.1:8000` / `http://localhost:8000` 即发布失败;
4. **migration 前强制备份**(backup_sqlite.sh,备份失败=发布失败,不执行 migration,
   current 不切换,线上不受影响);
5. `python -m backend.db.migrate --all`(幂等;历史迁移 checksum 漂移会拒绝执行;
   **migration 失败 → current 不切换**,线上不受影响);
6. 候选冒烟:临时端口 8001 起候选 API,curl `/healthz` `/readyz` 都 200 才继续
   (此时 current 未切,线上不受影响;**候选冒烟失败 → current 不切换**);
7. `ln -sfn` 原子切 `current` 软链 → `systemctl restart allwin-api allwin-web`;
8. 线上验收(宪法 §14.2,失败自动回滚):
   - curl 8000 的 healthz/readyz + 3000 首页 HTTP 200;
   - 业务冒烟:`/api/v1/products` 与 `/api/v1/matches` 必须是可解析 JSON
     (release .venv 的 `python -m json.tool`;`grep` 匹配 `SMOKE_HTML_MARKER` 时用
     `grep -qF --` 固定字符串匹配,不把用户可控的 marker 值当 grep 选项解析);
   - 首页 HTML 必须含真实 API 数据标志(默认产品名 `Pro 月度`,可用
     `SMOKE_HTML_MARKER` 覆盖;首页是 ISR,构建期 API 不在线时预渲染为降级态,
     脚本最长等 180s 让 revalidate 重取);
   - **任一失败 → 回滚,且回滚不是"切完就假定成功"**:切回 `previous` 并重启后,
     用同一套 `verify_live` + `business_smoke` **重新验收旧版本**——重新验收也失败
     时,明确非零退出并要求人工介入(不会假装"已恢复");重新验收通过才提示
     "已回滚并验证健康";
   - **回滚只切代码,不自动恢复数据库**:migration 必须向后兼容(只加列/加表,
     不删列/改类型),否则回滚后的旧代码可能读不动已迁移的新 schema;
   - 没有 `previous`(首次发布)时验收失败 → 直接非零退出、要求人工介入,不做
     任何自动切换;
9. 清理旧 release(保留最近 `KEEP_RELEASES` 个;**`current`/`previous` 永远不清理**,
   清理目标路径必须落在 `APP_ROOT` 内,不会波及仓库外任意路径)。

手动回滚:`ln -sfn /opt/allwin/releases/<旧sha> /opt/allwin/current && sudo systemctl restart allwin-api allwin-web`
(手动回滚后同样应该手动跑一遍 healthz/readyz/首页 + 业务冒烟确认,不要假设成功)。

**可测试性**:所有外部命令(git/rsync/python3/npm/curl/sqlite3/sudo/systemctl)都
可以通过 `PATH` 替换为测试用假实现,`ALLWIN_APP_ROOT` 可整体重定向到临时目录;
`tests/backend/test_release_rollback.py` 用这套机制覆盖了 dirty source 拒绝、
secret 文件不进 release、同 SHA release 拒绝覆盖、备份/migration/候选失败各自
阻止后续步骤、切换后 smoke 失败触发回滚、回滚重新验收、current/previous 不被
清理、marker 不被 grep 当选项解析等场景——全部离线模拟,不在开发机上跑真正的
release,不 sudo、不 systemctl、不碰真实 `/opt/allwin`。

发布后三必查(CLAUDE.md §10):`systemctl show allwin-api -p ExecStart` 的实际路径、
`nginx -T` 的 proxy_pass、`curl https://ALLWIN_DOMAIN/readyz` 域名指纹,三路同一目录才算已部署;
再 Cloudflare purge + 浏览器隐私模式核验。

## 4. Cloudflare 配置

### DNS
- `ALLWIN_DOMAIN` A → EC2 弹性 IP,**橙云(Proxied)**;
- 不建裸暴露源站的记录;源站 IP 只存在于 Cloudflare 后面。

### TLS
- 模式 **Full (strict)**;源站装 Cloudflare Origin Certificate(nginx conf 已引用);
- Always Use HTTPS 开;最低 TLS 1.2。

### Cache Rules(顺序敏感,BYPASS 规则在前)

| # | 匹配 | 动作 | 理由 |
|---|---|---|---|
| 1 | URI Path starts with `/api/v1/auth` OR `/api/v1/member` OR `/api/v1/account` OR `/api/v1/admin` OR `/api/v1/studio` OR `/api/v1/exports` OR `/api/league` | **Bypass cache** | 登录/会员付费数据/账户/管理/Studio/导出/旧兼容层,带 Cookie/Authorization,按用户返回,进共享缓存=把 A 的付费数据发给 B(legacy 不再扩展,保守统一 Bypass) |
| 2 | URI Path starts with `/login` OR `/member` OR `/account` OR `/admin` OR `/studio` | **Bypass cache** | 携带会话 Cookie 的 SSR 页面 |
| 3 | 请求带 `Cookie` 或 `Authorization` 头(任意路径) | **Bypass cache** | 兜底:任何带凭证的请求都不进共享缓存;响应带 `Set-Cookie` 同样不得缓存(Cloudflare 默认遵守) |
| 4 | URI Path starts with `/_next/static` | Cache eligible,Edge TTL: respect origin | 内容寻址文件名,源站已发 `immutable, max-age=31536000` |
| 5 | URI Path starts with `/brand` | Cache eligible,respect origin | 静态品牌资源 |
| 6 | 匿名公开页 / 公开 API(其余 `/api/v1/*` GET 与免费 SEO 页) | respect origin(源站按需下发 `s-maxage`) | 免费层可短缓存引流;是否缓存由应用显式声明,边缘不猜 |

`/healthz` `/readyz` 不缓存(源站已发 `no-store`)。

**源站保证(已实现 + 已有回归测试,非仅设计意图)**:上表第 6 条"respect origin"能够
安全生效,前提是源站自己绝不会对带凭证请求或非白名单路径误发 `public`——这一点现在由
`backend/api/cache_policy.py` 的 `CachePolicyMiddleware` 在应用层强制:请求带 `Cookie`/
`Authorization`,或响应带 `Set-Cookie`,或路径不在应用内显式 `PUBLIC_ALLOWLIST`,一律强制
`Cache-Control: private, no-store`;两个可运行的 FastAPI 实例(`backend.api.app:app` 生产
入口、`backend.api_server.app` 独立/legacy 运行模式)都已接入。回归覆盖见
`tests/backend/test_cache_policy.py`(权益投影矩阵 + 缓存矩阵,临时数据库,不依赖网络)。

**UNVERIFIED**:以上是源站(FastAPI)真实响应头行为的验证,不是 Cloudflare 边缘真实
HIT/BYPASS 行为的验证——本轮未实际登录 AWS/Cloudflare 控制台配置或调用其 API,上表
6 条规则在真实 Cloudflare 账号里是否已经按此顺序建立、边缘对 `private, no-store` 响应
是否确实从不缓存,仍需要在有真实生产环境凭证时用 `cf-cache-status` 响应头实测确认。

### WAF 与基础限流
- 托管规则集(Cloudflare Managed Ruleset)开;
- Rate limiting:`/api/v1/auth/*` 每 IP 10 req/min(登录/出码接口);`/api/*` 每 IP 300 req/min 兜底;
- Bot Fight Mode 开(免费档即可);
- 后端自身仍保留应用层限流(backend/api/ratelimit.py),不依赖边缘。

## 5. 备份策略

- **每日**:`allwin-backup.timer` → `deploy/scripts/backup_sqlite.sh`:
  - **完整性不变量**:一份"完整备份"必须同时包含 `allwin.db`+`platform.db`+`odds.db`,
    缺任何一个都是失败退出,不是"跳过并报告成功"(修复前的行为是缺 1-2 个库仍
    `exit 0`);
  - **原子发布**:先在 `.incomplete-<UTC时间戳>-<PID>/` 临时目录里做完
    `.backup` + `PRAGMA integrity_check` + 写 `backup_metadata.json`
    (size/sha256/integrity_check/complete),全部通过后才 `mv` 原子改名成
    `<UTC时间戳>/`(同一文件系统内的 `mv` = POSIX `rename(2)`,不会出现"看起来
    完整其实半成品"的目录);任何一步失败,临时目录被清理,不留痕迹;
  - **并发保护**:单个 lock 文件 + `noclobber`(等价 `O_CREAT|O_EXCL`),两个
    重叠的备份触发(如 timer 与手动运行撞上)只有一个真正执行,其余明确
    `exit 75`(跳过,不是失败也不是静默成功),不会互相覆盖或在同一 UTC 秒内
    产生冲突目录;
  - `umask 077`:备份文件/`backup_metadata.json`/manifest 导出默认不允许组或
    其他用户读取;
  - prediction manifest 导出单独落 `data/backups/manifests/<UTC时间戳>/`(与库备份分开目录);
  - 本地保留最近 `BACKUP_KEEP`(默认 14,必须为正整数)份**完整**备份;
    `.incomplete-*`/manifests 目录不计入这个计数;
  - 配置了 `S3_BACKUP_BUCKET` + AWS 凭证才 `aws s3 cp`(只上传已原子发布、
    `complete=true` 的备份);未配置明确打印 `LOCAL_ONLY`;aws CLI 缺失或上传
    失败都是发布失败(非静默降级)。
- **每次 migration 前**:release.sh 第 4 步强制再跑一次同一脚本。
- **S3 bucket 要求**:东京区、**开启 Versioning**、私有(Block Public Access 全开)、
  生命周期规则 90 天后转 Glacier/删除;EC2 用 instance role(只授 `s3:PutObject`/`ListBucket`
  到该 bucket)优于长期 AK/SK。**UNVERIFIED**:真实 S3 Versioning/Object Lock/
  真实恢复——本轮只用假 `aws` 可执行文件做了离线行为测试(见
  `tests/backend/test_backup_restore.py::TestS3Offline`),未连接真实 AWS。
- **恢复演练**(建议每月一次,以及每次大 migration 后):
  1. `bash deploy/scripts/restore_verify.sh` —— 只接受带 `backup_metadata.json`
     且 `complete=true` 的备份目录;逐库校验 SHA-256(与 metadata 记录比对,
     检测落盘后被篡改/损坏)+ 独立重跑 `PRAGMA integrity_check` + 关键表可查询
     (`dim_match` / `job_runs`+`prediction_snapshots` / `source_health`+`poll_state`)
     + migration 无 pending/checksum drift(对恢复出的库副本只读检查,不修改
     任何库);恢复到 `mktemp` 临时目录,不修改备份源、不修改真实数据库;
     退出码即结果;
  2. 真恢复:停 `allwin-api`/`allwin-worker.timer`/`allwin-poll.timer` → 把
     备份目录内三库复制回 `/opt/allwin/shared/data/`(先把现场坏库 mv 走留证)
     → `python -m backend.db.migrate --status` 确认版本 → 起服务 → curl `/readyz`。

## 6. 磁盘与告警

- SQLite 主库 ~400MB 且随赛季增长,加上本地 14 份备份,**磁盘是单机最先耗尽的资源**;
- 告警阈值:used ≥ **70%** 提醒(清老备份/扩容排期),≥ **85%** 紧急(立即扩 EBS;
  SQLite 写满盘会直接报错,WAL 无法 checkpoint);阈值现在有真实实现:
  `python -m backend.cli.ops_check`(见 §11),环境变量 `OPS_DISK_WARN_PCT`/
  `OPS_DISK_CRITICAL_PCT` 覆盖默认的 70/85;
- 补充:CloudWatch Agent 的 `disk_used_percent` 两条告警(70/85)→ SNS 邮件仍可
  作为边缘/系统级冗余告警,与 ops_check 并不互斥;**UNVERIFIED**(未接真实
  CloudWatch/SNS);
- journal 限额:`SystemMaxUse=500M`(/etc/systemd/journald.conf),防日志吃盘。

## 7. /healthz 与 /readyz 语义(backend/api/app.py)

| 探针 | 含义 | 用途 |
|---|---|---|
| `GET /healthz` | 进程活着(无依赖检查),恒 200 `{"ok": true}` | systemd/监控的存活探测 |
| `GET /readyz` | 三库(core/platform/odds)可读 **且** migration 无 pending/checksum drift;否则 503 + problems 列表 | 发布验收、流量接入判定;release.sh 冒烟与验收都用它 |

`readyz` 只负责"数据库可读 + migration 无 pending"这一件事,**不**把 job_runs/
source_health 等外部数据源健康状况塞进公网 readiness 判定——外部采集失败不能
拖垮 API(CLAUDE.md §13),运维需要看更全面的状态用 `ops_check`(见 §11)。

**脱敏**(本轮修复):503 的 `problems` 列表以前会包含具体迁移文件名
(`0002_kickoff_provenance.sql` 等)和原始 SQLite 异常文本(如
`unable to open database file`、`file is not a database`)——这些对未认证的
公网调用者是不必要的内部实现细节侦察信息。现在只返回稳定标识,例如
`"platform: pending_migrations=1"`、`"odds: unavailable"`;真实异常详情记录到
服务端日志(`log.exception(...)`),不进响应体。`ReadyzProblemsDTO` 的 OpenAPI
形状不变(仍是 `{ok, problems: string[]}`),只是 `problems` 里每条字符串的
内容更保守。

## 8. 上线前需要你提供的清单

| 项 | 用途 | 备注 |
|---|---|---|
| 域名(ALLWIN_DOMAIN) | Cloudflare DNS + Nginx server_name + PUBLIC_BASE_URL | 需已转入/托管到 Cloudflare |
| 微信公众号**网页授权域名** | 公众号后台"网页授权域名"须填 ALLWIN_DOMAIN | 需域名 ICP 备案状态确认 |
| 微信公众号 AppID / AppSecret | 后端 OAuth(WECHAT_* env) | 只进 /opt/allwin/shared/.env,绝不进 git |
| AWS 账号 / EC2 密钥对 | 开东京 EC2 + EBS + 弹性 IP | 建议 t3.small 起步,EBS ≥ 30GB gp3 |
| S3 bucket 名(versioned) | 备份异地存放(S3_BACKUP_BUCKET) | 建议 instance role 授权,免长期 AK/SK |
| (可选)AWS AK/SK | 不用 instance role 时的 aws CLI 凭证 | 只进 .env |
| ThorData 代理凭证 | THORDATA_PROXY(FotMob 抓取) | 已有,迁到服务器 .env |
| 告警接收邮箱/手机 | CloudWatch SNS / 磁盘告警通知 | |

## 9. 环境变量(部署新增,汇总)

在 `/opt/allwin/shared/.env` 中(变量语义以仓库根 `.env.example` 为准,按本表补):

```
ALLWIN_DATA_DIR=/opt/allwin/shared/data   # SQLite 数据目录(release 之间共享)
S3_BACKUP_BUCKET=                          # 可选:备份 S3 bucket 名;为空则仅本地备份
BACKUP_KEEP=14                             # 本地保留备份份数
# AWS_ACCESS_KEY_ID= / AWS_SECRET_ACCESS_KEY= / AWS_DEFAULT_REGION=ap-northeast-1
#   —— 仅在不用 EC2 instance role 时需要

# 前端 API 基址(单一真源 frontend/lib/api-base.ts,宪法 §10.3):
# NEXT_PUBLIC_API_BASE=                    # 生产必须留空/不设:浏览器走同源相对 /api/v1
INTERNAL_API_BASE=http://127.0.0.1:8000    # Next 服务端(RSC)运行期直连同机 FastAPI
```

## 10. 前端 API 基址与构建期环境(必读)

解析规则(`frontend/lib/api-base.ts` 单一真源;`lib/api-v1.ts` / `lib/api.ts` 都从它取值):

| 端 | 取值顺序 | 生产结果 |
|---|---|---|
| 浏览器 | `NEXT_PUBLIC_API_BASE` 非空 → 用之;否则同源相对 `""` | `""` → 请求 `/api/v1/*`,经 Cloudflare → Nginx → FastAPI;**绝不默认 127.0.0.1** |
| Next 服务端(RSC) | `INTERNAL_API_BASE` > `NEXT_PUBLIC_API_BASE` > `http://127.0.0.1:8000` | 同机回环直连 FastAPI,不绕 Cloudflare 回源 |

关键纪律:

1. **`NEXT_PUBLIC_*` 是构建期内联**:`next build` 时写死进浏览器产物,systemd 的
   `EnvironmentFile` 运行期改它无效;改值必须重新 build(release.sh 已在 build 前
   `set -a; . shared/.env; set +a`)。它还必须在构建期与服务端运行期取值一致,
   否则 SSR 与浏览器渲染出的链接不同(水合不一致)。生产两处都留空即可。
2. **`INTERNAL_API_BASE` 是运行期读取**:只在 Next 服务端进程可见,随 systemd
   环境调整即可生效,不进浏览器产物。
3. **env 文件优先级**:`next build` 除 shell 环境变量外还会读取
   `frontend/.env.local`(shell env 优先,shell 未设时 `.env.local` 的值会渗入
   构建产物)。因此生产服务器与 CI 不得存在 `frontend/.env.local`;开发机上该
   文件保持"注释掉的示例",需要时临时取消注释或用
   `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 npm run dev`。
4. **发布门禁**:`deploy/scripts/check_browser_bundle.sh` 在每次构建后 grep
   `.next/static`(浏览器 chunk),发现 `http://127.0.0.1:8000` 或
   `http://localhost:8000` 即发布失败——把"生产用户浏览器绝不指向回环地址"
   固化为机器检查,而非口头约定。

## 11. 只读运维检查(backend/cli/ops_check.py)

不新增公开、无认证的 metrics API——检查入口是一个只读 CLI:

```bash
/opt/allwin/current/.venv/bin/python -m backend.cli.ops_check          # 文本(适合 journalctl)
/opt/allwin/current/.venv/bin/python -m backend.cli.ops_check --json    # 结构化 JSON,字段稳定
```

退出码:`0` OK(全部健康)、`1` WARN(降级但服务仍可用)、`2` CRITICAL(需要立即
关注,或 `OPS_*` 阈值配置显式非法——见下方"配置校验"，此时不执行任何数据库/
磁盘检查就直接失败)。检查项:

| 检查 | CRITICAL 条件 | WARN 条件 |
|---|---|---|
| 三库可读性 | 文件缺失/不可读/`PRAGMA quick_check` 失败 | — |
| migration 状态 | 有 pending 迁移或 checksum drift | — |
| 本地备份新鲜度 | — | 无任何 `complete=true` 的备份,或最新一份超过 `OPS_BACKUP_STALE_HOURS`(默认 30h) |
| 磁盘使用率 | ≥ `OPS_DISK_CRITICAL_PCT`(默认 85%) | ≥ `OPS_DISK_WARN_PCT`(默认 70%) |
| job_runs | — | 最近一次 `failed`;`running` 超过 `OPS_JOB_STUCK_MINUTES`(默认 120 分钟)判定"卡住";超过 `OPS_JOB_STALE_HOURS`(默认 24h)没有一次成功 |
| source_health(NowGoal/FotMob) | — | 从未成功过,或超过 `OPS_SOURCE_STALE_HOURS`(默认 6h)没有一次成功——两者分别标注,不混为一谈 |

配置校验(生产可靠性收口第二轮,取代早期"解析失败静默用默认值"的实现):

- `OPS_*` 环境变量未设置或为空白 → 使用上表列出的默认值;
- 显式设置但不合法 → `OpsConfig.from_env()` 立即抛 `ConfigError`,CLI 捕获后
  打印能定位到具体变量名的简短 stderr 消息(不含 traceback、不含 Secret、
  不含完整环境或数据库路径)并以 `2`(CRITICAL)退出——**不会**继续跑任何
  数据库/磁盘检查后再报错,也**不会**静默改用默认值继续执行(那等于允许一个
  写错的阈值悄悄关掉真实告警,例如 `OPS_DISK_CRITICAL_PCT=200` 会让磁盘用满
  也不报 CRITICAL);
  - 磁盘百分比:必须是 finite(拒绝 NaN/Infinity),且满足
    `0 < OPS_DISK_WARN_PCT < OPS_DISK_CRITICAL_PCT <= 100`;
  - 四个时间阈值(`OPS_BACKUP_STALE_HOURS`/`OPS_JOB_STUCK_MINUTES`/
    `OPS_JOB_STALE_HOURS`/`OPS_SOURCE_STALE_HOURS`)必须是严格正整数
    (拒绝 0/负数/小数/非数字);
- 同一次 `run_all_checks()` 只解析一次配置,传给全部依赖阈值的检查项,不会
  出现同一次运行里前后阈值不一致。

输出脱敏边界:

- 本工具与 `/readyz` 相互独立——外部数据源(NowGoal/FotMob)健康状况不影响、
  也不依赖 API 本身是否正常响应;
- **`error_summary` 不能假设已经安全**——本工具是最终输出边界,`_sanitize_summary`
  在截断之前先做真正的脱敏:URL userinfo(`user:pass@host`)、常见
  `password=/token=/secret=/api_key=/authorization=/user=` 等键值对、
  `Authorization: Bearer <token>`、`proxy=user:pass@host`、Unix 绝对路径
  (`/Users`/`/home`/`/root`/`/opt`/`/srv`/`/var`/`/etc`/`/tmp`)、Windows 绝对路径
  (`C:\...`)均被替换为稳定占位符;SQL 语句(`SELECT`/`INSERT`/…/`PRAGMA`)与
  Traceback/异常堆栈形状的内容整体退化为 `[SQL_REDACTED]`/`[TRACEBACK_REDACTED]`,
  不做局部脱敏(结构不可预测,宁可整体替换);无法判断为安全的内容一律不放行;
- 全部阈值可由环境变量覆盖(见 `.env.example` 的 `OPS_*` 变量),默认值同时
  写在代码里,不依赖 `.env` 才能运行;
- 可以增加 `ops-check.service`/`.timer` 定期跑并把非零退出接到告警渠道,但
  **不伪造告警发送成功**——CloudWatch/SNS/邮件通道未真实配置前，这里只提供
  退出码和结构化输出,真正接入告警渠道仍是 **UNVERIFIED**。
