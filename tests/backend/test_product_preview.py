from __future__ import annotations

from pathlib import Path

import pytest

from scripts import local_preview_proxy
from scripts.verify_next_assets import AssetIntegrityError, local_assets, verify_build


def test_asset_extractor_only_returns_same_origin_next_static() -> None:
    html = """
    <link href="/_next/static/chunks/app.css" rel="stylesheet">
    <script src="/_next/static/chunks/app.js"></script>
    <script src="https://example.invalid/external.js"></script>
    """
    assert local_assets(html) == {
        "/_next/static/chunks/app.css",
        "/_next/static/chunks/app.js",
    }


def test_build_asset_verifier_rejects_missing_chunk(tmp_path: Path) -> None:
    app = tmp_path / ".next" / "server" / "app"
    app.mkdir(parents=True)
    (app / "index.html").write_text(
        '<link href="/_next/static/chunks/missing.css" rel="stylesheet">',
        encoding="utf-8",
    )
    with pytest.raises(AssetIntegrityError, match="missing static assets"):
        verify_build(tmp_path)


def test_build_asset_verifier_accepts_complete_chunk_set(tmp_path: Path) -> None:
    app = tmp_path / ".next" / "server" / "app"
    static = tmp_path / ".next" / "static" / "chunks"
    app.mkdir(parents=True)
    static.mkdir(parents=True)
    (app / "index.html").write_text(
        '<link href="/_next/static/chunks/app.css" rel="stylesheet">',
        encoding="utf-8",
    )
    (static / "app.css").write_text("body{}", encoding="utf-8")
    assert verify_build(tmp_path) == {"html_files": 1, "assets": 1}


def test_preview_proxy_fails_before_binding_when_an_upstream_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str]] = []

    def fake_probe(port: int, path: str) -> bool:
        calls.append((port, path))
        return path == "/healthz"

    monkeypatch.setattr(local_preview_proxy, "_probe_http", fake_probe)
    with pytest.raises(
        local_preview_proxy.PreviewUpstreamError,
        match="preview upstream unavailable: frontend",
    ):
        local_preview_proxy.check_upstreams(8400, 3501)
    assert calls == [(8400, "/healthz"), (3501, "/")]
