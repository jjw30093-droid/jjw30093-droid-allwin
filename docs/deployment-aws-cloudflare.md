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
| `allwin-worker.service` + `allwin-worker.timer` | oneshot 任务链(schedule_sync → … → metrics_rebuild),每 15 分钟触发;链内文件锁防叠跑,job_runs(platform.db)留全生命周期记录 |
| `allwin-backup.service` + `allwin-backup.timer` | 每日 UTC 19:00 备份三库(.backup + integrity_check + 可选 S3) |

共同点:`User=allwin`(非 root 系统用户)、`WorkingDirectory=/opt/allwin/current`、
`EnvironmentFile=/opt/allwin/shared/.env`。安装:

```bash
sudo cp deploy/systemd/allwin-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now allwin-api allwin-web allwin-worker.timer allwin-backup.timer
```

## 3. 发布 / 回滚(deploy/scripts/release.sh)

发布(在服务器上):

```bash
cd /opt/allwin/source && git fetch && git checkout <ref>
bash deploy/scripts/release.sh
```

脚本内部顺序(`set -euo pipefail`,任一步失败即停):

1. rsync 代码到 `releases/<git-sha>/`(不可变目录,排除 .git/.venv/node_modules/data);
2. release 内独立 `.venv` + `pip install -r requirements.txt`;
3. `npm ci && npm run build`(frontend);
4. **migration 前强制备份**(backup_sqlite.sh,备份失败=发布失败);
5. `python -m backend.db.migrate --all`(幂等;历史迁移 checksum 漂移会拒绝执行);
6. 候选冒烟:临时端口 8001 起候选 API,curl `/healthz` `/readyz` 都 200 才继续
   (此时 current 未切,线上不受影响);
7. `ln -sfn` 原子切 `current` 软链 → `systemctl restart allwin-api allwin-web`;
8. 线上验收:curl 8000 的 healthz/readyz + 3000 首页;失败自动把 current 切回上一
   release 并重启(**回滚 = 切软链,数据不回滚**——migration 设计成向后兼容的加列/加表);
9. 清理旧 release(保留最近 5 个 + 上一个)。

手动回滚:`ln -sfn /opt/allwin/releases/<旧sha> /opt/allwin/current && sudo systemctl restart allwin-api allwin-web`。

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
| 1 | URI Path starts with `/api/v1/auth` OR `/api/v1/member` OR `/api/v1/account` OR `/api/v1/admin` OR `/api/v1/studio` OR `/api/v1/exports` | **Bypass cache** | 登录/会员付费数据/账户/管理/Studio/导出,带 Cookie/Authorization,按用户返回,进共享缓存=把 A 的付费数据发给 B |
| 2 | URI Path starts with `/login` OR `/member` OR `/account` OR `/admin` OR `/studio` | **Bypass cache** | 携带会话 Cookie 的 SSR 页面 |
| 3 | 请求带 `Cookie` 或 `Authorization` 头(任意路径) | **Bypass cache** | 兜底:任何带凭证的请求都不进共享缓存;响应带 `Set-Cookie` 同样不得缓存(Cloudflare 默认遵守) |
| 4 | URI Path starts with `/_next/static` | Cache eligible,Edge TTL: respect origin | 内容寻址文件名,源站已发 `immutable, max-age=31536000` |
| 5 | URI Path starts with `/brand` | Cache eligible,respect origin | 静态品牌资源 |
| 6 | 匿名公开页 / 公开 API(其余 `/api/v1/*` GET 与免费 SEO 页) | respect origin(源站按需下发 `s-maxage`) | 免费层可短缓存引流;是否缓存由应用显式声明,边缘不猜 |

`/healthz` `/readyz` 不缓存(源站已发 `no-store`)。

### WAF 与基础限流
- 托管规则集(Cloudflare Managed Ruleset)开;
- Rate limiting:`/api/v1/auth/*` 每 IP 10 req/min(登录/出码接口);`/api/*` 每 IP 300 req/min 兜底;
- Bot Fight Mode 开(免费档即可);
- 后端自身仍保留应用层限流(backend/api/ratelimit.py),不依赖边缘。

## 5. 备份策略

- **每日**:`allwin-backup.timer` → `deploy/scripts/backup_sqlite.sh`:
  - 三库逐个 `sqlite3 ".backup"`(WAL 安全一致性快照)→ `data/backups/<UTC时间戳>/`;
  - 每个备份文件 `PRAGMA integrity_check` 不过即失败退出,绝不留坏备份还报成功;
  - prediction manifest 导出单独落 `data/backups/manifests/<UTC时间戳>/`(与库备份分开目录);
  - 本地保留最近 `BACKUP_KEEP`(默认 14)份;
  - 配置了 `S3_BACKUP_BUCKET` + AWS 凭证才 `aws s3 cp`;未配置明确打印"S3 未配置,仅本地备份"。
- **每次 migration 前**:release.sh 第 4 步强制再跑一次同一脚本。
- **S3 bucket 要求**:东京区、**开启 Versioning**、私有(Block Public Access 全开)、
  生命周期规则 90 天后转 Glacier/删除;EC2 用 instance role(只授 `s3:PutObject`/`ListBucket`
  到该 bucket)优于长期 AK/SK。
- **恢复演练**(建议每月一次,以及每次大 migration 后):
  1. `bash deploy/scripts/restore_verify.sh` —— 取最近备份恢复到临时目录,逐库
     integrity_check + 关键表行数,退出码即结果;
  2. 真恢复:停 `allwin-api`/`allwin-worker.timer` → 把备份目录内三库复制回
     `/opt/allwin/shared/data/`(先把现场坏库 mv 走留证)→ `python -m backend.db.migrate --status`
     确认版本 → 起服务 → curl `/readyz`。

## 6. 磁盘与告警

- SQLite 主库 ~400MB 且随赛季增长,加上本地 14 份备份,**磁盘是单机最先耗尽的资源**;
- 告警阈值:used ≥ **70%** 提醒(清老备份/扩容排期),≥ **85%** 紧急(立即扩 EBS;
  SQLite 写满盘会直接报错,WAL 无法 checkpoint);
- 实现:CloudWatch Agent 的 `disk_used_percent` 两条告警(70/85)→ SNS 邮件;
  或最简 cron:`df -P /opt/allwin | awk 'NR==2{gsub("%","",$5); if($5>70) print "disk", $5"%"}'` 发通知;
- journal 限额:`SystemMaxUse=500M`(/etc/systemd/journald.conf),防日志吃盘。

## 7. /healthz 与 /readyz 语义(backend/api/app.py)

| 探针 | 含义 | 用途 |
|---|---|---|
| `GET /healthz` | 进程活着(无依赖检查),恒 200 `{"ok": true}` | systemd/监控的存活探测 |
| `GET /readyz` | 三库(core/platform/odds)可读 **且** migration 无 pending;否则 503 + problems 列表 | 发布验收、流量接入判定;release.sh 冒烟与验收都用它 |

注意:`readyz` 503 时 body 里的 `problems` 会指出是哪个库/哪个 pending migration,
排障从它开始,不用猜。

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

在 `/opt/allwin/shared/.env` 中(本仓库 .env.example 未动,按本表补):

```
ALLWIN_DATA_DIR=/opt/allwin/shared/data   # SQLite 数据目录(release 之间共享)
S3_BACKUP_BUCKET=                          # 可选:备份 S3 bucket 名;为空则仅本地备份
BACKUP_KEEP=14                             # 本地保留备份份数
# AWS_ACCESS_KEY_ID= / AWS_SECRET_ACCESS_KEY= / AWS_DEFAULT_REGION=ap-northeast-1
#   —— 仅在不用 EC2 instance role 时需要
```
