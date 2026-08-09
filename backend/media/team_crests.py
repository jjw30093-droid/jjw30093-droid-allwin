"""Validated, local-only FotMob team crest cache.

Remote acquisition is confined to the explicit sync CLI. Resolver and API
reads only trust manifest-bound PNG bytes below ``ALLWIN_MEDIA_DIR``.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import tempfile
import threading
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import requests

from backend.db.paths import PROJECT_ROOT

PROVIDER = "fotmob"
SOURCE_HOST = "images.fotmob.com"
MAX_IMAGE_BYTES = 1024 * 1024
MIN_DIMENSION = 16
MAX_DIMENSION = 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MANIFEST_VERSION = 1


class TeamCrestError(RuntimeError):
    """Fixed-surface media validation error."""


class CrestDownloadError(TeamCrestError):
    """Safe per-team acquisition failure."""


@dataclass(frozen=True)
class PngInfo:
    width: int
    height: int
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class CrestBytes:
    content: bytes
    sha256: str
    width: int
    height: int


@dataclass
class RequestBudget:
    maximum: int
    attempts: int = 0

    def consume(self) -> None:
        if self.attempts >= self.maximum:
            raise CrestDownloadError("request budget exhausted")
        self.attempts += 1


Downloader = Callable[[int, str, float, int, RequestBudget], bytes]

_manifest_lock = threading.Lock()
_manifest_cache_key: tuple[str, int, int, int] | None = None
_manifest_cache_value: dict | None = None


def media_root() -> Path:
    configured = os.environ.get("ALLWIN_MEDIA_DIR")
    return Path(configured) if configured else PROJECT_ROOT / "runtime" / "media"


def manifest_path(root: Path | None = None) -> Path:
    return (root or media_root()) / "team-crests" / "manifest.json"


def crest_relative_path(provider: str, provider_team_id: int) -> Path:
    _validate_identity(provider, provider_team_id)
    return Path("team-crests") / provider / f"{provider_team_id}.png"


def source_url(provider: str, provider_team_id: int) -> str:
    _validate_identity(provider, provider_team_id)
    return (
        f"https://{SOURCE_HOST}/image_resources/logo/teamlogo/"
        f"{provider_team_id}.png"
    )


def _validate_identity(provider: object, provider_team_id: object) -> tuple[str, int]:
    if provider != PROVIDER:
        raise TeamCrestError("unsupported crest provider")
    if (
        isinstance(provider_team_id, bool)
        or not isinstance(provider_team_id, int)
        or provider_team_id <= 0
    ):
        raise TeamCrestError("invalid provider team id")
    return PROVIDER, provider_team_id


def inspect_png(data: bytes) -> PngInfo:
    if not isinstance(data, bytes) or len(data) > MAX_IMAGE_BYTES:
        raise TeamCrestError("invalid crest image")
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE):
        raise TeamCrestError("invalid crest image")

    offset = len(PNG_SIGNATURE)
    width = height = None
    saw_idat = False
    saw_iend = False
    chunk_index = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise TeamCrestError("invalid crest image")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise TeamCrestError("invalid crest image")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise TeamCrestError("invalid crest image")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                raise TeamCrestError("invalid crest image")
            width, height = struct.unpack(">II", payload[:8])
        elif chunk_type == b"IHDR":
            raise TeamCrestError("invalid crest image")
        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0 or chunk_end != len(data):
                raise TeamCrestError("invalid crest image")
            saw_iend = True
            offset = chunk_end
            break
        offset = chunk_end
        chunk_index += 1

    if (
        not saw_idat
        or not saw_iend
        or width is None
        or height is None
        or not (MIN_DIMENSION <= width <= MAX_DIMENSION)
        or not (MIN_DIMENSION <= height <= MAX_DIMENSION)
    ):
        raise TeamCrestError("invalid crest image")
    return PngInfo(
        width=width,
        height=height,
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _safe_json_load(raw: bytes) -> dict:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise TeamCrestError("invalid crest manifest")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, TeamCrestError):
        raise TeamCrestError("invalid crest manifest") from None
    if not isinstance(value, dict):
        raise TeamCrestError("invalid crest manifest")
    return value


def _open_regular_bytes(path: Path, *, maximum: int) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise TeamCrestError("crest file unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size < 1
            or metadata.st_size > maximum
        ):
            raise TeamCrestError("crest file unavailable")
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != metadata.st_size or len(raw) > maximum:
            raise TeamCrestError("crest file unavailable")
        return raw, metadata
    finally:
        os.close(descriptor)


def _is_plain_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _empty_manifest() -> dict:
    return {
        "schema_version": MANIFEST_VERSION,
        "updated_at": None,
        "entries": {},
        "unavailable": {},
    }


def _load_manifest(root: Path | None = None) -> dict:
    global _manifest_cache_key, _manifest_cache_value
    base = root or media_root()
    if not _is_plain_directory(base) or not _is_plain_directory(
        base / "team-crests"
    ):
        return _empty_manifest()
    path = manifest_path(base)
    try:
        metadata = path.lstat()
    except OSError:
        return _empty_manifest()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        return _empty_manifest()
    key = (str(path.absolute()), metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
    with _manifest_lock:
        if key == _manifest_cache_key and _manifest_cache_value is not None:
            return _manifest_cache_value
    try:
        raw, opened = _open_regular_bytes(path, maximum=MAX_MANIFEST_BYTES)
        if (
            opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
            or opened.st_mtime_ns != metadata.st_mtime_ns
        ):
            return _empty_manifest()
        value = _safe_json_load(raw)
    except TeamCrestError:
        return _empty_manifest()
    if (
        value.get("schema_version") != MANIFEST_VERSION
        or not isinstance(value.get("entries"), dict)
        or not isinstance(value.get("unavailable", {}), dict)
    ):
        return _empty_manifest()
    with _manifest_lock:
        _manifest_cache_key = key
        _manifest_cache_value = value
    return value


def clear_manifest_cache() -> None:
    global _manifest_cache_key, _manifest_cache_value
    with _manifest_lock:
        _manifest_cache_key = None
        _manifest_cache_value = None


def _manifest_entry(
    provider: str,
    provider_team_id: int,
    root: Path | None = None,
) -> dict | None:
    try:
        _validate_identity(provider, provider_team_id)
    except TeamCrestError:
        return None
    entry = _load_manifest(root).get("entries", {}).get(
        f"{provider}:{provider_team_id}"
    )
    return entry if isinstance(entry, dict) else None


def read_team_crest(
    provider: str,
    provider_team_id: int,
    *,
    version: str | None = None,
    root: Path | None = None,
) -> CrestBytes | None:
    try:
        _validate_identity(provider, provider_team_id)
    except TeamCrestError:
        return None
    entry = _manifest_entry(provider, provider_team_id, root)
    if entry is None:
        return None
    expected_relative = crest_relative_path(provider, provider_team_id).as_posix()
    sha = entry.get("sha256")
    if (
        entry.get("provider") != provider
        or entry.get("provider_team_id") != provider_team_id
        or entry.get("relative_path") != expected_relative
        or not isinstance(sha, str)
        or len(sha) != 64
        or any(char not in "0123456789abcdef" for char in sha)
        or (version is not None and version != sha[:12])
    ):
        return None
    base = root or media_root()
    if not all(
        _is_plain_directory(path)
        for path in (base, base / "team-crests", base / "team-crests" / provider)
    ):
        return None
    path = base / Path(expected_relative)
    try:
        raw, metadata = _open_regular_bytes(path, maximum=MAX_IMAGE_BYTES)
        info = inspect_png(raw)
    except TeamCrestError:
        return None
    if (
        info.sha256 != sha
        or metadata.st_size != entry.get("byte_size")
        or info.width != entry.get("width")
        or info.height != entry.get("height")
    ):
        return None
    return CrestBytes(raw, sha, info.width, info.height)


def resolve_team_crest_url(
    provider: str,
    provider_team_id: int | None,
    *,
    root: Path | None = None,
) -> str | None:
    if provider_team_id is None:
        return None
    crest = read_team_crest(provider, provider_team_id, root=root)
    if crest is None:
        return None
    return (
        f"/api/v1/media/team-crests/{provider}/{provider_team_id}.png"
        f"?v={crest.sha256[:12]}"
    )


def _ensure_cache_directories(root: Path, provider: str) -> None:
    _validate_identity(provider, 1)
    root.mkdir(parents=True, exist_ok=True)
    if not _is_plain_directory(root):
        raise TeamCrestError("unsafe media directory")
    current = root
    for component in ("team-crests", provider):
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
            metadata = current.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise TeamCrestError("unsafe media directory")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _manifest_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_manifest(root: Path, value: dict) -> None:
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, _manifest_bytes(value))
    clear_manifest_cache()


def download_crest(
    provider_team_id: int,
    url: str,
    timeout: float,
    retries: int,
    budget: RequestBudget,
) -> bytes:
    expected = source_url(PROVIDER, provider_team_id)
    if url != expected or retries < 0 or timeout <= 0:
        raise CrestDownloadError("invalid download request")
    session = requests.Session()
    session.trust_env = False
    try:
        for attempt in range(retries + 1):
            budget.consume()
            response = None
            try:
                response = session.get(
                    expected,
                    headers={
                        "Accept": "image/png",
                        "User-Agent": "allwin-team-crest-sync/1.0",
                    },
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )
                if response.status_code != 200:
                    raise CrestDownloadError("remote image unavailable")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type != "image/png":
                    raise CrestDownloadError("remote image is not png")
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > MAX_IMAGE_BYTES):
                    raise CrestDownloadError("remote image exceeds size limit")
                chunks = []
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise CrestDownloadError("remote image exceeds size limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                inspect_png(raw)
                return raw
            except CrestDownloadError:
                if attempt >= retries:
                    raise
            except requests.RequestException:
                if attempt >= retries:
                    raise CrestDownloadError("network request failed") from None
            finally:
                if response is not None:
                    response.close()
    finally:
        session.close()
    raise CrestDownloadError("network request failed")


def _existing_manifest_for_update(root: Path) -> dict:
    path = manifest_path(root)
    if not path.exists():
        return _empty_manifest()
    raw, _ = _open_regular_bytes(path, maximum=MAX_MANIFEST_BYTES)
    value = _safe_json_load(raw)
    if (
        value.get("schema_version") != MANIFEST_VERSION
        or not isinstance(value.get("entries"), dict)
        or not isinstance(value.get("unavailable", {}), dict)
    ):
        raise TeamCrestError("invalid crest manifest")
    value.setdefault("unavailable", {})
    return value


def _install_success(
    root: Path,
    manifest: dict,
    provider: str,
    provider_team_id: int,
    raw: bytes,
    fetched_at: str,
) -> None:
    info = inspect_png(raw)
    relative = crest_relative_path(provider, provider_team_id)
    image_path = root / relative
    old_image = None
    if image_path.exists():
        old_image, _ = _open_regular_bytes(image_path, maximum=MAX_IMAGE_BYTES)
    old_manifest = _manifest_bytes(manifest)
    updated = json.loads(old_manifest)
    key = f"{provider}:{provider_team_id}"
    updated["updated_at"] = fetched_at
    updated["entries"][key] = {
        "provider": provider,
        "provider_team_id": provider_team_id,
        "relative_path": relative.as_posix(),
        "sha256": info.sha256,
        "width": info.width,
        "height": info.height,
        "byte_size": info.byte_size,
        "fetched_at": fetched_at,
        "source_url": source_url(provider, provider_team_id),
    }
    updated["unavailable"].pop(key, None)
    _atomic_write(image_path, raw)
    try:
        _write_manifest(root, updated)
    except BaseException:
        if old_image is None:
            try:
                image_path.unlink()
                _fsync_directory(image_path.parent)
            except FileNotFoundError:
                pass
        else:
            _atomic_write(image_path, old_image)
        if manifest_path(root).exists():
            _atomic_write(manifest_path(root), old_manifest)
        clear_manifest_cache()
        raise
    manifest.clear()
    manifest.update(updated)


def _record_unavailable(
    root: Path,
    manifest: dict,
    provider: str,
    provider_team_id: int,
    fetched_at: str,
    reason: str,
) -> None:
    updated = json.loads(_manifest_bytes(manifest))
    key = f"{provider}:{provider_team_id}"
    updated["updated_at"] = fetched_at
    updated["unavailable"][key] = {
        "provider": provider,
        "provider_team_id": provider_team_id,
        "status": "UNAVAILABLE",
        "checked_at": fetched_at,
        "reason": reason,
    }
    _write_manifest(root, updated)
    manifest.clear()
    manifest.update(updated)


def sync_team_crests(
    team_ids: Iterable[int],
    *,
    provider: str,
    root: Path | None = None,
    force: bool = False,
    maximum_requests: int = 30,
    timeout: float = 10.0,
    retries: int = 1,
    downloader: Downloader | None = None,
) -> dict:
    if provider != PROVIDER:
        raise TeamCrestError("unsupported crest provider")
    if maximum_requests < 1 or retries < 0 or timeout <= 0:
        raise TeamCrestError("invalid sync limits")
    normalized = sorted(set(team_ids))
    for team_id in normalized:
        _validate_identity(provider, team_id)

    root = root or media_root()
    downloader = downloader or download_crest
    _ensure_cache_directories(root, provider)
    manifest = _existing_manifest_for_update(root)
    budget = RequestBudget(maximum_requests)
    inserted = skipped = failed = 0
    failures: list[dict] = []

    for team_id in normalized:
        existing = read_team_crest(provider, team_id, root=root)
        if existing is not None and not force:
            skipped += 1
            continue
        checked_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        try:
            raw = downloader(
                team_id,
                source_url(provider, team_id),
                timeout,
                retries,
                budget,
            )
            inspect_png(raw)
            _install_success(root, manifest, provider, team_id, raw, checked_at)
            inserted += 1
        except (CrestDownloadError, TeamCrestError) as exc:
            failed += 1
            reason = str(exc)
            failures.append(
                {
                    "provider_team_id": team_id,
                    "status": "UNAVAILABLE",
                    "reason": reason,
                }
            )
            if existing is None:
                try:
                    _record_unavailable(
                        root, manifest, provider, team_id, checked_at, reason
                    )
                except (TeamCrestError, OSError):
                    pass
        except OSError:
            failed += 1
            failures.append(
                {
                    "provider_team_id": team_id,
                    "status": "UNAVAILABLE",
                    "reason": "local media write failed",
                }
            )

    return {
        "provider": provider,
        "total": len(normalized),
        "inserted": inserted,
        "skipped": skipped,
        "failed": failed,
        "request_attempts": budget.attempts,
        "failures": failures,
        "manifest": str(manifest_path(root)),
    }
