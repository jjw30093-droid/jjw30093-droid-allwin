"""导出 OpenAPI schema(Pydantic 单一真源 → TypeScript 类型生成的输入)。

用法:python -m backend.cli.export_openapi [--out frontend/lib/openapi.json]
前端随后:cd frontend && npm run gen:api
"""

import argparse
import json
import sys
from pathlib import Path

from backend.db.paths import PROJECT_ROOT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PROJECT_ROOT / "frontend" / "lib" / "openapi.json"))
    args = ap.parse_args(argv)

    from backend.api.app import create_app

    schema = create_app().openapi()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"openapi schema → {out} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
