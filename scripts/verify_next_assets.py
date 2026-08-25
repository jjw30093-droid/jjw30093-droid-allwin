#!/usr/bin/env python3
"""Verify that rendered Next HTML only references existing/servable local assets."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


CORE_PAGES = (
    "/",
    "/matches",
    "/matches/5104968",
    "/pricing",
    "/about",
)
ASSET_RE = re.compile(r"""(?:href|src)=["']([^"']+)["']""")


class AssetIntegrityError(RuntimeError):
    pass


def local_assets(html: str) -> set[str]:
    return {
        urllib.parse.urlsplit(value).path
        for value in ASSET_RE.findall(html)
        if urllib.parse.urlsplit(value).path.startswith("/_next/static/")
    }


def verify_build(frontend_dir: Path) -> dict[str, int]:
    build_dir = frontend_dir / ".next"
    html_files = sorted((build_dir / "server" / "app").rglob("*.html"))
    if not html_files:
        raise AssetIntegrityError("Next build has no rendered HTML")
    assets: set[str] = set()
    missing: list[str] = []
    for html_path in html_files:
        for asset in local_assets(html_path.read_text(encoding="utf-8")):
            assets.add(asset)
            target = build_dir / asset.removeprefix("/_next/")
            if not target.is_file():
                missing.append(asset)
    if missing:
        raise AssetIntegrityError(
            f"Next build references {len(set(missing))} missing static assets"
        )
    return {"html_files": len(html_files), "assets": len(assets)}


def _get(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "allwin-preview-check/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def verify_running(base_url: str, pages: tuple[str, ...] = CORE_PAGES) -> dict[str, int]:
    checked_assets: set[str] = set()
    for page in pages:
        status, html = _get(urllib.parse.urljoin(base_url.rstrip("/") + "/", page.lstrip("/")))
        if status != 200:
            raise AssetIntegrityError(f"core page unavailable: {page}")
        for asset in local_assets(html):
            if asset in checked_assets:
                continue
            asset_status, _ = _get(
                urllib.parse.urljoin(base_url.rstrip("/") + "/", asset.lstrip("/"))
            )
            if asset_status != 200:
                raise AssetIntegrityError(f"core page references unavailable asset: {asset}")
            checked_assets.add(asset)
    return {"pages": len(pages), "assets": len(checked_assets)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--base-url")
    args = parser.parse_args()
    try:
        if args.frontend:
            print(verify_build(args.frontend.resolve()))
        if args.base_url:
            print(verify_running(args.base_url))
        if not args.frontend and not args.base_url:
            parser.error("one of --frontend or --base-url is required")
    except AssetIntegrityError as exc:
        print(f"asset integrity check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
