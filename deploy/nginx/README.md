# deploy/nginx/

- `allwin.conf.example`：可移植模板（`ALLWIN_DOMAIN` 占位符 + Cloudflare Origin
  Certificate），供新环境/新域名参考起步，不对应任何已安装文件，`release.sh`
  不检查它。
- `nginx.conf`、`miaomiaodi.vip`：生产服务器上**当前实际生效**的原样副本
  （2026-09-27 起纳入仓库），分别对应服务器上的 `/etc/nginx/nginx.conf` 与
  `/etc/nginx/sites-available/miaomiaodi.vip`。`release.sh` 的 preflight 会比较
  这两个文件与已安装版本，不一致就打印 diff 并中止发布（同 systemd 单元一致性
  检查的先例，见 CLAUDE.md §14.2/§14.4）——不自动安装，改完先手动
  `sudo cp` + `nginx -t` + `systemctl reload nginx`，再重新发布。
