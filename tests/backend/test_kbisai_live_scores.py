"""春秋直播公开实时比分 Provider 的离线永久测试。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from backend.cli import kbisai_live_scores as cli
from backend.providers import kbisai_live_scores as provider


def _varint(value: int) -> bytes:
    if value < 0:
        value &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _v(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _b(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _s(field: int, value: str) -> bytes:
    return _b(field, value.encode("utf-8"))


def _packed(values: list[int]) -> bytes:
    return b"".join(_varint(value) for value in values)


def _competition(competition_id: int, name: str) -> bytes:
    return _v(1, competition_id) + _s(2, name) + _s(5, "E")


def _team(team_id: int, name: str) -> bytes:
    return _v(1, team_id) + _s(2, name)


def _int_items(values: list[int]) -> bytes:
    return _b(1, _packed(values))


def _string_items(values: list[str]) -> bytes:
    return b"".join(_s(1, value) for value in values)


def _match(
    match_id: int,
    *,
    status: int,
    home_score: list[int],
    away_score: list[int],
    competition_id: int = 59,
    home_id: int = 8007,
    away_id: int = 8448,
    kickoff: int = 1785517200,
) -> bytes:
    ints = [
        match_id,
        competition_id,
        home_id,
        away_id,
        kickoff,
        status,
        0,
        123,
        0,
        64 if status in range(2, 8) else 0,
        1,
        0,
    ]
    return (
        _b(1, _int_items(ints))
        + _b(2, _string_items(["1", "2", "公开备注", ""]))
        + _b(3, _packed(home_score))
        + _b(4, _packed(away_score))
        + _s(5, "ignored-public-room")
        + _b(99, b"unknown")
    )


def _lottery(match_id: int) -> bytes:
    return _v(1, match_id) + _v(2, 101) + _s(3, "4001")


def _snapshot_bytes(
    *,
    matches: list[bytes] | None = None,
    extra_teams: list[bytes] | None = None,
    code: int = 0,
) -> bytes:
    if matches is None:
        matches = [
            _match(5104968, status=4, home_score=[2, 1], away_score=[1, 0]),
            _match(5104969, status=1, home_score=[0], away_score=[0]),
        ]
    teams = [_team(8007, "Vålerenga"), _team(8448, "HamKam")]
    teams.extend(extra_teams or [])
    return (
        _b(1, _competition(59, "Eliteserien"))
        + b"".join(_b(2, team) for team in teams)
        + _b(3, _lottery(5104968))
        + b"".join(_b(4, match) for match in matches)
        + _v(5, code)
    )


class TestRestProtobuf:
    def test_decodes_and_normalizes_real_contract_shape(self):
        raw = _snapshot_bytes()
        decoded = provider.decode_match_list_response(raw)
        rows = provider.normalize_match_list(decoded, observed_at="2026-07-31T00:00:00.000000Z")

        assert len(decoded["competitions"]) == 1
        assert len(decoded["teams"]) == 2
        assert [row["provider_match_id"] for row in rows] == ["5104968", "5104969"]
        live = rows[0]
        assert live["competition"] == {
            "provider_competition_id": "59",
            "name": "Eliteserien",
        }
        assert live["home_team"] == {"provider_team_id": "8007", "name": "Vålerenga"}
        assert live["away_team"] == {"provider_team_id": "8448", "name": "HamKam"}
        assert live["status"] == "IN_PLAY"
        assert live["home_score"] == 2
        assert live["away_score"] == 1
        assert live["provider_clock_reference"] == 64
        assert live["clock_semantics"] == "UNVERIFIED"
        assert live["lottery_ids"] == [101]
        assert live["source_updated_at"] is None
        upcoming = rows[1]
        assert upcoming["status"] == "NOT_STARTED"
        assert upcoming["home_score"] is None
        assert upcoming["away_score"] is None

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            (_snapshot_bytes(code=9), "non-zero"),
            (_snapshot_bytes(matches=[_match(1, status=77, home_score=[0], away_score=[0])]), "status"),
            (
                _snapshot_bytes(
                    matches=[
                        _match(1, status=4, home_score=[1], away_score=[0], home_id=9999)
                    ]
                ),
                "unknown entity",
            ),
            (
                _snapshot_bytes(
                    matches=[
                        _match(1, status=4, home_score=[1], away_score=[0]),
                        _match(1, status=4, home_score=[1], away_score=[0]),
                    ]
                ),
                "duplicate match",
            ),
            (
                _snapshot_bytes(
                    matches=[_match(1, status=4, home_score=[1, 0], away_score=[0])]
                ),
                "different lengths",
            ),
            (
                _snapshot_bytes(extra_teams=[_team(8007, "duplicate")]),
                "duplicate team",
            ),
        ],
    )
    def test_fails_closed_on_invalid_business_structure(self, raw: bytes, message: str):
        if "non-zero" in message:
            with pytest.raises(provider.KbisaiProtocolError, match=message):
                provider.decode_match_list_response(raw)
            return
        decoded = provider.decode_match_list_response(raw)
        with pytest.raises(provider.KbisaiProtocolError, match=message):
            provider.normalize_match_list(decoded, observed_at="2026-07-31T00:00:00.000000Z")

    @pytest.mark.parametrize(
        "raw",
        [
            b"\x80",
            _b(1, b"\x08"),
            _varint((1 << 3) | 3),
            b"x" * (provider.MAX_REST_BYTES + 1),
        ],
    )
    def test_rejects_malformed_or_oversized_protobuf(self, raw: bytes):
        with pytest.raises(provider.KbisaiProtocolError):
            provider.decode_match_list_response(raw)


class _Response:
    def __init__(self, raw: bytes, *, status_code: int = 200, content_type: str = "application/x-protobuf"):
        self.content = raw
        self.status_code = status_code
        self.headers = {"content-type": content_type}


class _Client:
    response = _Response(_snapshot_bytes())
    received: dict | None = None

    def __init__(self, **kwargs):
        type(self).received = {"client": kwargs}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, **kwargs):
        type(self).received["post"] = {"url": url, **kwargs}
        return type(self).response


class TestRestTransport:
    def test_fetch_uses_anonymous_direct_transport_and_returns_snapshot(self):
        snapshot = provider.fetch_realtime_snapshot(
            observed_at="2026-07-31T00:00:00.000000Z",
            client_factory=_Client,
        )
        assert snapshot.live_count == 1
        assert len(snapshot.matches) == 2
        assert snapshot.raw_sha256 == hashlib.sha256(_snapshot_bytes()).hexdigest()
        assert _Client.received["client"]["trust_env"] is False
        headers = _Client.received["post"]["headers"]
        assert headers["scope"] == "web"
        assert "application/x-protobuf" in headers["accept"]
        assert "*/*" in headers["accept"]
        assert "authorization" not in {key.lower() for key in headers}
        assert "cookie" not in {key.lower() for key in headers}

    @pytest.mark.parametrize(
        "response",
        [
            _Response(_snapshot_bytes(), status_code=403),
            _Response(_snapshot_bytes(), content_type="application/json"),
            _Response(b""),
        ],
    )
    def test_invalid_http_surface_is_fixed_safe_error(self, response):
        _Client.response = response
        with pytest.raises(provider.KbisaiTransportError) as info:
            provider.fetch_realtime_snapshot(client_factory=_Client)
        assert info.value.__cause__ is None
        assert info.value.__context__ is None
        assert "kbisailive" not in str(info.value)

    def test_underlying_transport_exception_does_not_leak(self):
        marker = "PROXY://USER:PASS@PRIVATE/MARKER"

        class FailingClient:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                raise RuntimeError(marker)

            def __exit__(self, *_args):
                return None

        with pytest.raises(provider.KbisaiTransportError) as info:
            provider.fetch_realtime_snapshot(client_factory=FailingClient)
        surfaces = [
            str(info.value),
            repr(info.value),
            repr(info.value.args),
            repr(info.value.__cause__),
            repr(info.value.__context__),
        ]
        assert all(marker not in surface for surface in surfaces)
        assert info.value.__cause__ is None
        assert info.value.__context__ is None


def _score_obj(
    *,
    match_id: int = 5104968,
    status_id: int = 4,
    home: list[int] | None = None,
    away: list[int] | None = None,
) -> bytes:
    return (
        _v(1, match_id)
        + _v(2, status_id)
        + _b(3, _packed(home or [2]))
        + _b(4, _packed(away or [1]))
        + _v(5, 67)
        + _s(6, "goal")
    )


def _ws_message(message_type: int = 12) -> bytes:
    return _v(1, 5104968) + _v(2, 1785456000) + _v(3, message_type) + _b(4, _score_obj())


class TestWebSocketContract:
    def test_public_handshake_matches_frontend_algorithm(self):
        url = provider.build_ws_url(epoch_seconds=1785456000)
        encoded = url.split("sign=", 1)[1]
        # urlencode 只会编码 base64 的 padding/+；还原后验证公开算法。
        from urllib.parse import unquote_plus

        payload = json.loads(base64.b64decode(unquote_plus(encoded)))
        assert payload == {
            "scope": "web",
            "timestamp": 1785456000,
            "sign": hashlib.md5(b"web1785456000").hexdigest(),
        }
        assert provider.encode_ws_message_type(10) == b"\x18\x0a"

    def test_decodes_and_normalizes_score_update(self):
        decoded = provider.decode_ws_message(_ws_message())
        update = provider.normalize_ws_score_update(
            decoded,
            observed_at="2026-07-31T00:00:00.000000Z",
        )
        assert update == {
            "provider": "kbisai",
            "provider_match_id": "5104968",
            "event_type": "SCORE_CHANGE",
            "status": "IN_PLAY",
            "provider_status_id": 4,
            "provider_clock_reference": 67,
            "clock_semantics": "UNVERIFIED",
            "home_score": 2,
            "away_score": 1,
            "home_score_parts": [2],
            "away_score_parts": [1],
            "note": "goal",
            "provider_event_timestamp": 1785456000,
            "observed_at": "2026-07-31T00:00:00.000000Z",
            "source_updated_at": None,
        }

    def test_ignores_non_score_websocket_messages(self):
        decoded = provider.decode_ws_message(_v(3, 2))
        assert provider.normalize_ws_score_update(
            decoded, observed_at="2026-07-31T00:00:00.000000Z"
        ) is None

    def test_short_listener_uses_subscription_and_returns_score(self):
        class FakeWs:
            sent: list[bytes] = []

            def __init__(self):
                self.calls = 0

            def send(self, payload):
                self.sent.append(payload)

            def recv(self):
                self.calls += 1
                if self.calls == 1:
                    return _ws_message(), 2
                raise RuntimeError("stop")

            def terminate(self):
                return None

            def close(self):
                return None

        class FakeSession:
            ws = FakeWs()

            def __init__(self, **_kwargs):
                pass

            def ws_connect(self, *_args, **_kwargs):
                return self.ws

            def close(self):
                return None

        updates = provider.listen_ws_updates(
            duration_seconds=1,
            max_events=1,
            session_factory=FakeSession,
        )
        assert len(updates) == 1
        assert updates[0]["event_type"] == "SCORE_CHANGE"
        assert FakeWs.sent == [b"\x18\x0a"]


class TestCliArtifacts:
    def test_run_writes_private_auditable_artifacts(self, tmp_path: Path, monkeypatch):
        raw = _snapshot_bytes()
        decoded = provider.decode_match_list_response(raw)
        matches = provider.normalize_match_list(
            decoded, observed_at="2026-07-31T00:00:00.000000Z"
        )
        snapshot = provider.RealtimeSnapshot(
            observed_at="2026-07-31T00:00:00.000000Z",
            raw_bytes=raw,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            matches=matches,
            competition_count=1,
            team_count=2,
            live_count=1,
        )
        monkeypatch.setattr(cli, "fetch_realtime_snapshot", lambda: snapshot)
        monkeypatch.setattr(cli, "listen_ws_updates", lambda **_kwargs: ({"event": "ok"},))

        summary = cli.run(output_dir=tmp_path, listen_seconds=1, max_events=1)
        run_dir = tmp_path / summary["run_id"]
        assert json.loads((run_dir / "summary.json").read_text())["match_count"] == 2
        assert json.loads((run_dir / "updates.json").read_text()) == [{"event": "ok"}]
        assert summary["ws_status"] == "OBSERVED"
        assert (run_dir / "realtime-match-list.pb").read_bytes() == raw
        for path in run_dir.iterdir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_optional_ws_failure_keeps_successful_rest_snapshot(self, tmp_path: Path, monkeypatch):
        raw = _snapshot_bytes()
        decoded = provider.decode_match_list_response(raw)
        snapshot = provider.RealtimeSnapshot(
            observed_at="2026-07-31T00:00:00.000000Z",
            raw_bytes=raw,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            matches=provider.normalize_match_list(
                decoded, observed_at="2026-07-31T00:00:00.000000Z"
            ),
            competition_count=1,
            team_count=2,
            live_count=1,
        )
        monkeypatch.setattr(cli, "fetch_realtime_snapshot", lambda: snapshot)
        monkeypatch.setattr(
            cli,
            "listen_ws_updates",
            lambda **_kwargs: (_ for _ in ()).throw(
                provider.KbisaiTransportError("fixed safe error")
            ),
        )

        summary = cli.run(output_dir=tmp_path, listen_seconds=1, max_events=1)
        assert summary["status"] == "OK"
        assert summary["ws_status"] == "UNAVAILABLE"
        assert summary["ws_error"] == "KbisaiTransportError"
        assert summary["match_count"] == 2

    def test_credentialed_mode_is_explicitly_rejected(self, monkeypatch):
        monkeypatch.setenv("KBISAI_TOKEN", "SECRET-MARKER")
        with pytest.raises(provider.KbisaiTransportError) as info:
            provider.assert_no_runtime_credentials()
        assert "SECRET-MARKER" not in str(info.value)
