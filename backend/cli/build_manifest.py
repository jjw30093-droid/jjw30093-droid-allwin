"""生成每日正式预测 manifest(稳定 hash,幂等;内容变化则追加新版本)。

覆盖当日全部正式版本(含被取代与已撤回的正式快照),不只修正链最新版
(CLAUDE.md §9.1)。S3 上传由部署脚本/环境变量启用
(deploy/scripts/backup_sqlite.sh);未配置 AWS 凭证时只落库本地,不声称已上传。

用法:python -m backend.cli.build_manifest [--date YYYY-MM-DD](默认今天 UTC)
"""

import argparse
import sys

from backend.commands.predictions import build_daily_manifest
from backend.db.connections import connect_rw, tx
from backend.db.util import utc_now_iso


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="UTC 日期 YYYY-MM-DD,默认今天")
    args = ap.parse_args(argv)
    date = args.date or utc_now_iso()[:10]
    conn = connect_rw("platform")
    try:
        with tx(conn):
            result = build_daily_manifest(conn, date)
    finally:
        conn.close()
    print(f"manifest: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
