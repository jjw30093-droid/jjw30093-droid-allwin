"""Unified local team-crest pipeline contract."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cli import sync_team_crests as cli
from backend.db.connections import connect_rw
from backend.media import team_crests
from backend.media.team_crests import (
    CrestDownloadError,
    TeamCrestError,
    clear_manifest_cache,
    inspect_png,
    read_team_crest,
    resolve_team_crest_url,
    sync_team_crests,
)

from .coreseed import seed_basic_core


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def png(width: int = 192, height: int = 192, rgb=(20, 80, 140)) -> bytes:
    rows = b"".join(
        b"\x00" + bytes(rgb) * width
        for _ in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    root = tmp_path / "media"
    monkeypatch.setenv("ALLWIN_MEDIA_DIR", str(root))
    clear_manifest_cache()
    yield root
    clear_manifest_cache()


def fake_downloader(payload: bytes):
    def download(team_id, url, timeout, retries, budget):
        assert url.endswith(f"/{team_id}.png")
        assert timeout > 0 and retries >= 0
        budget.consume()
        return payload

    return download


def install(root: Path, team_id: int = 8007, payload: bytes | None = None):
    return sync_team_crests(
        [team_id],
        provider="fotmob",
        root=root,
        downloader=fake_downloader(payload or png()),
    )


class TestPngGate:
    def test_accepts_complete_png_and_extracts_metadata(self):
        payload = png(192, 192)
        info = inspect_png(payload)
        assert (info.width, info.height) == (192, 192)
        assert info.byte_size == len(payload)
        assert info.sha256 == hashlib.sha256(payload).hexdigest()

    @pytest.mark.parametrize(
        "payload",
        [
            b"<html>not found</html>",
            b'{"error":"not png"}',
            b"<svg></svg>",
            png()[:-8],
            png(8, 192),
            b"\x89PNG\r\n\x1a\n" + b"x" * (1024 * 1024),
        ],
        ids=["html", "json", "svg", "truncated", "wrong-dimensions", "oversized"],
    )
    def test_rejects_non_png_truncated_wrong_size_and_oversized(self, payload):
        with pytest.raises(TeamCrestError, match="invalid crest image"):
            inspect_png(payload)


class TestManifestResolver:
    def test_manifest_hit_returns_same_origin_versioned_url(self, media_dir):
        install(media_dir)
        url = resolve_team_crest_url("fotmob", 8007)
        assert url is not None
        assert url.startswith("/api/v1/media/team-crests/fotmob/8007.png?v=")
        assert "images.fotmob.com" not in url

    def test_missing_sha_mismatch_and_illegal_identity_fail_closed(self, media_dir):
        assert resolve_team_crest_url("fotmob", 8007) is None
        install(media_dir)
        image = media_dir / "team-crests" / "fotmob" / "8007.png"
        image.write_bytes(png(rgb=(200, 10, 10)))
        clear_manifest_cache()
        assert resolve_team_crest_url("fotmob", 8007) is None
        assert resolve_team_crest_url("../fotmob", 8007) is None
        assert resolve_team_crest_url("fotmob", -1) is None
        assert resolve_team_crest_url("fotmob", True) is None

    def test_path_traversal_symlink_and_hardlink_are_rejected(self, media_dir, tmp_path):
        install(media_dir)
        manifest = media_dir / "team-crests" / "manifest.json"
        value = json.loads(manifest.read_text())
        value["entries"]["fotmob:8007"]["relative_path"] = "../outside.png"
        manifest.write_text(json.dumps(value))
        clear_manifest_cache()
        assert resolve_team_crest_url("fotmob", 8007) is None

        install(media_dir, 8448)
        image = media_dir / "team-crests" / "fotmob" / "8448.png"
        target = tmp_path / "target.png"
        image.replace(target)
        image.symlink_to(target)
        clear_manifest_cache()
        assert resolve_team_crest_url("fotmob", 8448) is None

        image.unlink()
        image.hardlink_to(target)
        clear_manifest_cache()
        assert resolve_team_crest_url("fotmob", 8448) is None

        install(media_dir, 8007)
        provider_dir = media_dir / "team-crests" / "fotmob"
        outside_provider = tmp_path / "outside-provider"
        provider_dir.rename(outside_provider)
        provider_dir.symlink_to(outside_provider, target_is_directory=True)
        clear_manifest_cache()
        assert resolve_team_crest_url("fotmob", 8007) is None

        physical_root = tmp_path / "physical-media"
        install(physical_root, 8007)
        linked_root = tmp_path / "linked-media"
        linked_root.symlink_to(physical_root, target_is_directory=True)
        clear_manifest_cache()
        assert resolve_team_crest_url("fotmob", 8007, root=linked_root) is None

    def test_manifest_mtime_cache_refreshes_after_atomic_change(self, media_dir):
        install(media_dir, 8007)
        assert resolve_team_crest_url("fotmob", 8007)
        install(media_dir, 8448)
        assert resolve_team_crest_url("fotmob", 8448)


class TestSync:
    def test_idempotent_second_run_skips_without_download(self, media_dir):
        calls = []

        def downloader(team_id, url, timeout, retries, budget):
            calls.append(team_id)
            budget.consume()
            return png()

        first = sync_team_crests(
            [8448, 8007, 8007],
            provider="fotmob",
            root=media_dir,
            downloader=downloader,
        )
        second = sync_team_crests(
            [8007, 8448],
            provider="fotmob",
            root=media_dir,
            downloader=downloader,
        )
        assert first["inserted"] == 2 and first["failed"] == 0
        assert second["skipped"] == 2 and second["request_attempts"] == 0
        assert calls == [8007, 8448]

    def test_failed_refresh_preserves_old_image_manifest_and_resolver(self, media_dir):
        install(media_dir)
        image = media_dir / "team-crests" / "fotmob" / "8007.png"
        manifest = media_dir / "team-crests" / "manifest.json"
        before = (image.read_bytes(), manifest.read_bytes())

        def fail(*args):
            raise CrestDownloadError("network request failed")

        result = sync_team_crests(
            [8007],
            provider="fotmob",
            root=media_dir,
            force=True,
            downloader=fail,
        )
        assert result["failed"] == 1
        assert (image.read_bytes(), manifest.read_bytes()) == before
        assert resolve_team_crest_url("fotmob", 8007)

    def test_new_failure_records_unavailable_and_continues(self, media_dir):
        def mixed(team_id, url, timeout, retries, budget):
            budget.consume()
            if team_id == 8007:
                raise CrestDownloadError("remote image unavailable")
            return png()

        result = sync_team_crests(
            [8007, 8448],
            provider="fotmob",
            root=media_dir,
            downloader=mixed,
        )
        assert result["inserted"] == 1 and result["failed"] == 1
        manifest = json.loads(
            (media_dir / "team-crests" / "manifest.json").read_text()
        )
        assert manifest["unavailable"]["fotmob:8007"]["status"] == "UNAVAILABLE"
        assert "fotmob:8448" in manifest["entries"]

    @pytest.mark.parametrize("bad_id", [0, -1, True, "8007"])
    def test_invalid_ids_rejected_before_download(self, media_dir, bad_id):
        with pytest.raises(TeamCrestError, match="invalid provider team id"):
            sync_team_crests(
                [bad_id],
                provider="fotmob",
                root=media_dir,
                downloader=fake_downloader(png()),
            )

    def test_cli_discovers_unique_teams_and_is_idempotent(
        self, data_dir, media_dir, monkeypatch, capsys
    ):
        conn = connect_rw("core")
        conn.execute(
            """INSERT INTO dim_match
               (Match_ID,Season,League_ID,Date,Home_Team_ID,Away_Team_ID,status)
               VALUES (?,?,?,?,?,?,?)""",
            (59001, "2026", 59, "2026-08-01", 8007, 8448, "NotStarted"),
        )
        conn.close()
        monkeypatch.setattr(
            team_crests, "download_crest", fake_downloader(png())
        )
        assert cli.main(["--league-id", "59", "--season", "2026"]) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["inserted"] == 2 and first["request_attempts"] == 2
        assert cli.main(["--league-id", "59", "--season", "2026"]) == 0
        second = json.loads(capsys.readouterr().out)
        assert second["skipped"] == 2 and second["request_attempts"] == 0


class TestApiContract:
    def test_team_ref_nullable_crest_and_runtime_projection(
        self, app, data_dir, media_dir
    ):
        seed_basic_core(data_dir)
        install(media_dir, 1001)
        with TestClient(app) as client:
            body = client.get("/api/v1/matches/9001").json()
        assert body["match"]["home"]["crest_url"].startswith(
            "/api/v1/media/team-crests/fotmob/1001.png?v="
        )
        assert body["match"]["away"]["crest_url"] is None
        assert "images.fotmob.com" not in json.dumps(body)

    def test_media_route_headers_version_and_no_network(
        self, app, media_dir, monkeypatch
    ):
        install(media_dir)
        crest_url = resolve_team_crest_url("fotmob", 8007)
        assert crest_url

        def forbidden(*args, **kwargs):
            raise AssertionError("API media route attempted network")

        monkeypatch.setattr(team_crests.requests.Session, "get", forbidden)
        with TestClient(app) as client:
            response = client.get(crest_url)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/png")
            assert response.headers["etag"].startswith('"')
            assert response.headers["cache-control"] == (
                "public, max-age=31536000, immutable"
            )
            assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
            bad = crest_url.rsplit("=", 1)[0] + "=000000000000"
            assert client.get(bad).status_code == 404

    def test_media_route_missing_illegal_and_traversal_return_not_found(
        self, app, media_dir
    ):
        with TestClient(app) as client:
            assert (
                client.get(
                    "/api/v1/media/team-crests/fotmob/999.png?v=000000000000"
                ).status_code
                == 404
            )
            assert (
                client.get(
                    "/api/v1/media/team-crests/unknown/8007.png?v=000000000000"
                ).status_code
                == 404
            )
            assert (
                client.get(
                    "/api/v1/media/team-crests/fotmob/-1.png?v=000000000000"
                ).status_code
                == 404
            )
            assert client.get(
                "/api/v1/media/team-crests/fotmob/../manifest.json?v=000000000000"
            ).status_code in {404, 422}

    def test_openapi_team_ref_crest_is_nullable_and_optional(self, app):
        schema = app.openapi()["components"]["schemas"]["TeamRef"]
        crest = schema["properties"]["crest_url"]
        assert "crest_url" not in schema["required"]
        assert {variant.get("type") for variant in crest["anyOf"]} == {
            "string",
            "null",
        }
