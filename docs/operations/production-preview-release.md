# Production preview and release

## 本地 production preview

不要在正在运行的 Next 进程旁直接执行 `npm run build` 覆盖同一个
`frontend/.next`。这会让旧 HTML/进程继续引用已经被新 build 删除的 hash asset。

使用独立 staging：

```bash
bash scripts/build_local_preview.sh
```

脚本只在 `runtime/previews/releases/<id>` 构建。它先验证浏览器 bundle 不含回环
API 地址，再验证所有 prerendered HTML 引用的 CSS/JS 真正存在，最后原子切换：

```text
runtime/previews/current -> runtime/previews/releases/<id>
```

随后从 current 启动 Next。backend/frontend 都健康后再启动 loopback proxy：

```bash
cd runtime/previews/current/frontend
INTERNAL_API_BASE=http://127.0.0.1:<api-port> \
NEXT_PUBLIC_API_BASE= \
npm start -- --hostname 127.0.0.1 --port <web-port>

cd /path/to/all-win
.venv/bin/python scripts/local_preview_proxy.py \
  --listen-port <preview-port> \
  --backend-port <api-port> \
  --frontend-port <web-port>
```

代理会在绑定 `<preview-port>` 前检查 `/healthz` 和 frontend `/`。任一上游死亡
都立即退出，不会留下只能返回空响应的监听壳。

运行七页 asset smoke：

```bash
.venv/bin/python scripts/verify_next_assets.py \
  --base-url http://127.0.0.1:<preview-port>
```

固定覆盖 `/`、`/matches`、样板比赛详情、`/pricing`、`/about-model`、
`/track-record`、`/about`，并逐个验证 HTML 中所有同源 `/_next/static` 资源 200。

浏览器验收使用隔离 `/tmp`/E2E 数据副本。不要把 production-like preview
直接指到 `data/*.db` 或持久 pilot 库，因为 analytics 等正常页面行为可能写入
platform 数据。

## 服务器 release

服务器继续使用 `deploy/scripts/release.sh` 的不可变 release：

```text
/opt/allwin/releases/<git-sha>
/opt/allwin/current -> releases/<git-sha>
```

build、静态 asset 门禁、数据库备份/migration、候选 API smoke 都成功后才切
`current`。切换后 health/ready、业务 JSON、七页 HTML 和 CSS/JS asset 再次验收；
失败进入既有 rollback。不要在 `/opt/allwin/current/frontend/.next` 原地重建。

本轮没有执行 AWS deployment。
