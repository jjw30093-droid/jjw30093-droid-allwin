"""公开站点 URL 门禁回归测试(2026-09-30)。

事故:生产 shared/.env 缺 NEXT_PUBLIC_SITE_URL,frontend/lib/site.ts 回退到 http://localhost:3000,
sitemap.xml / robots.txt / llms.txt 全部指向 localhost;deploy/scripts/check_public_urls.sh 只拦
example.com 类占位域名,把 localhost 报成 OK(2026-09-13 起留存的 17 份发版日志均如此)。

1. check_public_urls.sh:用构造的构建产物(.next/server/app/*.body)验证 localhost / 127.0.0.1 /
   0.0.0.0 / 任何 http:// 都会失败,https://miaomiaodi.vip 通过(sitemap 的 XML 命名空间 http 例外)。
2. release.sh::verify_public_urls:用假 curl 返回构造的线上响应,验证 sitemap/首页必须含本域名、
   不得含本机地址;main() 中它失败时必须回滚。
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "deploy" / "scripts" / "check_public_urls.sh"
RELEASE_SH = ROOT / "deploy" / "scripts" / "release.sh"
NS = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'


def build_artifacts(tmp_path: Path, base: str) -> Path:
    """构造一份最小的 Next 构建产物:sitemap / robots / llms 三个 .body 文件,全部由 base 生成。"""
    app = tmp_path / "frontend" / ".next" / "server" / "app"
    app.mkdir(parents=True)
    (app / "sitemap.xml.body").write_text(
        NS + f"<url>\n<loc>{base}/</loc>\n</url>\n<url>\n<loc>{base}/matches</loc>\n</url>\n</urlset>\n")
    (app / "robots.txt.body").write_text(f"User-Agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")
    (app / "llms.txt.body").write_text(f"# 喵弟数据研究室\n- [首页]({base}/):今日焦点比赛\n")
    return tmp_path / "frontend"


def run_check(frontend: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(CHECK), str(frontend)], capture_output=True, text=True, timeout=30)


class TestCheckPublicUrls:
    def test_production_https_domain_passes(self, tmp_path):
        r = run_check(build_artifacts(tmp_path, "https://miaomiaodi.vip"))
        assert r.returncode == 0, r.stderr
        assert "https://miaomiaodi.vip" in r.stdout

    @pytest.mark.parametrize("base", [
        "http://localhost:3000",      # 2026-09-30 的真实生产状态
        "https://localhost:3000",
        "http://127.0.0.1:3000",
        "https://0.0.0.0",
    ])
    def test_local_addresses_fail(self, tmp_path, base):
        r = run_check(build_artifacts(tmp_path, base))
        assert r.returncode != 0
        assert "本机地址" in r.stderr

    def test_plain_http_production_domain_fails(self, tmp_path):
        r = run_check(build_artifacts(tmp_path, "http://miaomiaodi.vip"))
        assert r.returncode != 0
        assert "非 https" in r.stderr

    def test_placeholder_domain_still_fails(self, tmp_path):
        r = run_check(build_artifacts(tmp_path, "https://allwin.example.com"))
        assert r.returncode != 0
        assert "占位域名" in r.stderr

    def test_single_file_with_localhost_fails(self, tmp_path):
        frontend = build_artifacts(tmp_path, "https://miaomiaodi.vip")
        llms = frontend / ".next" / "server" / "app" / "llms.txt.body"
        llms.write_text(llms.read_text() + "- [积分榜](http://localhost:3000/league/47/standings)\n")
        r = run_check(frontend)
        assert r.returncode != 0


# ------------------------------------------------------------------ release.sh::verify_public_urls
def fake_curl(tmp_path: Path, sitemap: str, home: str) -> Path:
    """假 curl:按 URL 返回构造的 sitemap / 首页响应(忽略其它参数)。"""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    (tmp_path / "sitemap.out").write_text(sitemap)
    (tmp_path / "home.out").write_text(home)
    (bin_dir / "curl").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do url="$a"; done\n'
        'case "$url" in\n'
        f'  *sitemap.xml*) cat "{tmp_path}/sitemap.out" ;;\n'
        f'  *) cat "{tmp_path}/home.out" ;;\n'
        "esac\n"
    )
    (bin_dir / "curl").chmod(0o755)
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "sleep").chmod(0o755)
    return bin_dir


def run_verify(tmp_path: Path, sitemap: str, home: str) -> subprocess.CompletedProcess:
    bin_dir = fake_curl(tmp_path, sitemap, home)
    env = dict(os.environ, RELEASE_SH_SOURCE_ONLY="1", PATH=f"{bin_dir}:{os.environ['PATH']}",
               PUBLIC_CHECK_RETRIES="2", PUBLIC_CHECK_INTERVAL="0", RELEASE_DIR="/opt/allwin/releases/abc123")
    return subprocess.run(["bash", "-c", f'source "{RELEASE_SH}"; verify_public_urls'],
                          env=env, capture_output=True, text=True, timeout=30)


GOOD_SITEMAP = NS + "<url>\n<loc>https://miaomiaodi.vip/</loc>\n</url>\n</urlset>\n"
GOOD_HOME = '<html><head><link rel="canonical" href="https://miaomiaodi.vip/"/></head><body>赛程</body></html>'


class TestVerifyPublicUrls:
    def test_passes_when_both_point_to_domain(self, tmp_path):
        r = run_verify(tmp_path, GOOD_SITEMAP, GOOD_HOME)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_fails_when_sitemap_points_to_localhost(self, tmp_path):
        bad = NS + "<url>\n<loc>http://localhost:3000/</loc>\n</url>\n</urlset>\n"
        r = run_verify(tmp_path, bad, GOOD_HOME)
        assert r.returncode != 0
        assert "公开域名验收失败" in r.stdout

    def test_fails_when_sitemap_mixes_in_localhost(self, tmp_path):
        mixed = GOOD_SITEMAP.replace("</urlset>", "<url>\n<loc>http://localhost:3000/matches</loc>\n</url>\n</urlset>")
        assert run_verify(tmp_path, mixed, GOOD_HOME).returncode != 0

    def test_fails_when_home_lacks_domain(self, tmp_path):
        r = run_verify(tmp_path, GOOD_SITEMAP, "<html><head></head><body>赛程</body></html>")
        assert r.returncode != 0
        assert "首页 HTML 不含" in r.stdout

    def test_fails_when_home_has_localhost(self, tmp_path):
        home = GOOD_HOME + '<a href="http://localhost:3000/x">x</a>'
        assert run_verify(tmp_path, GOOD_SITEMAP, home).returncode != 0

    def test_main_rolls_back_when_public_check_fails(self, tmp_path):
        marker = tmp_path / "rollback.txt"
        script = f'''
source "{RELEASE_SH}"
preflight() {{ RELEASE_DIR=/tmp/x; PREVIOUS=/tmp/prev; }}
do_build() {{ :; }}
do_backup_and_migrate() {{ :; }}
candidate_smoke() {{ return 0; }}
switch_current() {{ :; }}
verify_live() {{ return 0; }}
warm_up_live() {{ :; }}
business_smoke() {{ return 0; }}
verify_public_urls() {{ return 1; }}
rollback() {{ echo ROLLBACK >> "{marker}"; die "rollback invoked (test stub)"; }}
main
'''
        env = dict(os.environ, RELEASE_SH_SOURCE_ONLY="1")
        r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode != 0
        assert marker.exists(), "公开域名验收失败后必须回滚"
