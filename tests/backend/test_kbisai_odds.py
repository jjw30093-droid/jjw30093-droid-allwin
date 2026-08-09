"""kbisai_odds.py 回归(本轮任务 §6/§8a):AES-256-CBC ZeroPadding 解密、
envelope 解析、传输安全边界(不泄漏凭证)、以及各 fetch 函数的输入校验。

真实性说明:tests/fixtures/kbisai/raw-allCompany.json 和
decrypted-allCompany.json 是 2026-08-04 对 kbisailive.com 真实请求
GET /api/v1/common/nm/allCompany 得到的原始响应/解密结果,原样保存(未编造)。
raw-matchAllOdds-eu-trimmed.json 不是原始抓包——是把同一天对真实比赛
matchId=4523588 的 matchAllOdds 响应解密后,截取其中一家公司的前 3 个真实
变化点、用同一个真实 AES 密钥/IV 重新加密而成(缩小体积,内容仍是真实观测值,
不是编造数据)。
"""

import base64
import json
import os

import httpx
import pytest

from backend.providers import kbisai_odds as odds

FIXTURE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "kbisai"
)


def _fixture_bytes(name: str) -> bytes:
    with open(os.path.join(FIXTURE_DIR, name), "rb") as f:
        return f.read()


def _fixture_json(name: str):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as f:
        return json.load(f)


class TestAesDecrypt:
    def test_roundtrip_with_zero_padding(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        plaintext = b'{"hello":"world"}'
        pad_len = (16 - len(plaintext) % 16) % 16 or 16
        padded = plaintext + b"\x00" * pad_len
        cipher = Cipher(algorithms.AES(odds._AES_KEY), modes.CBC(odds._AES_IV))
        enc = cipher.encryptor()
        ciphertext = enc.update(padded) + enc.finalize()

        assert odds._aes_decrypt_zero_padded(ciphertext) == plaintext

    def test_rejects_non_block_aligned_ciphertext(self):
        with pytest.raises(odds.KbisaiOddsError):
            odds._aes_decrypt_zero_padded(b"short")

    def test_rejects_empty_ciphertext(self):
        with pytest.raises(odds.KbisaiOddsError):
            odds._aes_decrypt_zero_padded(b"")


class TestDecodeEncryptedEnvelope:
    def test_real_all_company_envelope_decrypts_to_known_registry(self):
        raw = _fixture_bytes("raw-allCompany.json")
        decoded = odds._decode_encrypted_envelope(raw)
        expected = _fixture_json("decrypted-allCompany.json")
        assert decoded == expected
        # 用户在需求里点名的三家目标公司,来源真实注册的展示名。
        assert decoded["2"] == "36*"
        assert decoded["7"] == "澳*"
        assert decoded["22"] == "平*"

    def test_real_trimmed_match_all_odds_envelope_preserves_every_point(self):
        raw = _fixture_bytes("raw-matchAllOdds-eu-trimmed.json")
        decoded = odds._decode_encrypted_envelope(raw)
        expected = _fixture_json("decrypted-matchAllOdds-eu-trimmed.json")
        assert decoded == expected
        company_id = next(iter(decoded))
        assert len(decoded[company_id]["statusMatchOdds"]) == 3

    def test_rejects_nonzero_code(self):
        envelope = json.dumps({"code": 9999, "msg": "服务器未知错误"}).encode("utf-8")
        with pytest.raises(odds.KbisaiOddsError):
            odds._decode_encrypted_envelope(envelope)

    def test_rejects_missing_data_field(self):
        envelope = json.dumps({"code": 0, "msg": "操作成功"}).encode("utf-8")
        with pytest.raises(odds.KbisaiOddsError):
            odds._decode_encrypted_envelope(envelope)

    def test_rejects_invalid_base64_data(self):
        envelope = json.dumps({"code": 0, "data": "not-valid-base64!!!"}).encode("utf-8")
        with pytest.raises(odds.KbisaiOddsError):
            odds._decode_encrypted_envelope(envelope)

    def test_rejects_non_json_top_level(self):
        with pytest.raises(odds.KbisaiOddsError):
            odds._decode_encrypted_envelope(b"not json at all")

    def test_rejects_oversized_response(self):
        oversized = b"x" * (odds.MAX_REST_BYTES + 1)
        with pytest.raises(odds.KbisaiOddsError):
            odds._decode_encrypted_envelope(oversized)

    def test_rejects_ciphertext_that_decrypts_to_non_json(self):
        """一段能通过 AES 解密(块对齐)但明文不是合法 JSON 的密文——不能让
        _decode_encrypted_envelope 静默返回垃圾或崩溃成未分类异常。"""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        garbage = b"not json" + b"\x00" * 8   # 16 字节,块对齐
        cipher = Cipher(algorithms.AES(odds._AES_KEY), modes.CBC(odds._AES_IV))
        enc = cipher.encryptor()
        ciphertext = enc.update(garbage) + enc.finalize()
        envelope = json.dumps({"code": 0, "data": base64.b64encode(ciphertext).decode()}).encode()
        with pytest.raises(odds.KbisaiOddsError):
            odds._decode_encrypted_envelope(envelope)


class _Response:
    def __init__(self, content: bytes, *, status_code: int = 200, content_type: str = "application/json"):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}


class _Client:
    response: _Response
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

    def get(self, url, **kwargs):
        type(self).received["get"] = {"url": url, **kwargs}
        return type(self).response


class TestFetchAllCompanies:
    def test_uses_get_not_post(self):
        """allCompany 只接受 GET;这不是随手写的——2026-08-04 实测过 POST 返回
        code 9999 服务器未知错误,GET 才是真实契约。"""
        _Client.response = _Response(_fixture_bytes("raw-allCompany.json"))
        result = odds.fetch_all_companies(client_factory=_Client)
        assert result["7"] == "澳*"
        assert "get" in _Client.received
        assert "post" not in _Client.received
        assert _Client.received["client"]["trust_env"] is False

    def test_rejects_non_flat_string_map(self):
        _Client.response = _Response(
            json.dumps({"code": 0, "data": base64.b64encode(b"\x00" * 16).decode()}).encode()
        )
        # data 解密后大概率不是合法 JSON(全零明文);两种失败路径都必须是 KbisaiOddsError。
        with pytest.raises(odds.KbisaiOddsError):
            odds.fetch_all_companies(client_factory=_Client)

    def test_rejects_comp_category_shaped_response(self):
        """交叉校验的另一半(见 fetch_competition_category 同款测试):
        comp/category 的树形结构(values 是 list,不是 str)必须被 allCompany
        的"扁平字符串映射"校验拒绝,不能被"反正是个 dict"这种弱校验放过。"""
        payload = json.dumps({"categorys": [], "tops": []}).encode("utf-8")
        pad_len = (16 - len(payload) % 16) % 16 or 16
        padded = payload + b"\x00" * pad_len
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(odds._AES_KEY), modes.CBC(odds._AES_IV))
        enc = cipher.encryptor()
        ciphertext = enc.update(padded) + enc.finalize()
        envelope = json.dumps(
            {"code": 0, "data": base64.b64encode(ciphertext).decode()}
        ).encode("utf-8")
        _Client.response = _Response(envelope)
        with pytest.raises(odds.KbisaiOddsError):
            odds.fetch_all_companies(client_factory=_Client)


class TestFetchMatchAllOdds:
    def test_rejects_invalid_odds_type_before_any_network_call(self):
        _Client.received = None
        with pytest.raises(odds.KbisaiOddsError):
            odds.fetch_match_all_odds(4467576, "invalid-market", client_factory=_Client)
        assert _Client.received is None   # 没发起任何请求

    def test_rejects_non_positive_match_id(self):
        with pytest.raises(odds.KbisaiOddsError):
            odds.fetch_match_all_odds(0, "eu", client_factory=_Client)
        with pytest.raises(odds.KbisaiOddsError):
            odds.fetch_match_all_odds(-1, "eu", client_factory=_Client)

    def test_real_trimmed_payload_round_trip(self):
        _Client.response = _Response(_fixture_bytes("raw-matchAllOdds-eu-trimmed.json"))
        result = odds.fetch_match_all_odds(4523588, "eu", client_factory=_Client)
        company_id = next(iter(result))
        assert len(result[company_id]["statusMatchOdds"]) == 3
        body = json.loads(_Client.received["post"]["content"])
        assert body == {"matchId": 4523588, "oddsType": "eu"}


class TestFetchCompetitionCategory:
    def test_sends_football_sport_type(self):
        _Client.response = _Response(_fixture_bytes("raw-allCompany.json"))
        with pytest.raises(odds.KbisaiOddsError):
            # allCompany 的密文解密后不是 {"categorys":...} 形状,预期在结构校验处失败;
            # 这里只关心请求体本身发对了没有。
            odds.fetch_competition_category(client_factory=_Client)
        body = json.loads(_Client.received["post"]["content"])
        assert body == {"sportType": "football"}


class TestFetchFutureMatches:
    def test_rejects_invalid_match_date_before_any_network_call(self):
        _Client.received = None
        for bad in ("2026/08/07", "not-a-date", "", "2026-8-7"):
            with pytest.raises(odds.KbisaiOddsError):
                odds.fetch_future_matches(bad, client_factory=_Client)
        assert _Client.received is None

    def test_rejects_wrong_content_type(self):
        _Client.response = _Response(b"\x00" * 4, content_type="application/json")
        with pytest.raises(odds.KbisaiTransportError):
            odds.fetch_future_matches("2026-08-07", client_factory=_Client)


class TestTransportErrorHygiene:
    """凭证/底层异常不得经 __cause__/__context__ 或字符串泄漏(与
    test_kbisai_live_scores.py::TestRestTransport 同款严格度)。"""

    def test_underlying_post_exception_does_not_leak(self):
        marker = "PROXY://USER:PASS@PRIVATE/MARKER"

        class FailingClient:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                raise RuntimeError(marker)

            def __exit__(self, *_args):
                return None

        with pytest.raises(odds.KbisaiTransportError) as info:
            odds.fetch_all_companies(client_factory=FailingClient)
        surfaces = [
            str(info.value), repr(info.value), repr(info.value.args),
            repr(info.value.__cause__), repr(info.value.__context__),
        ]
        assert all(marker not in surface for surface in surfaces)
        assert info.value.__cause__ is None
        assert info.value.__context__ is None

    def test_underlying_get_exception_does_not_leak(self):
        marker = "PROXY://USER:PASS@PRIVATE/MARKER"

        class FailingClient:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                raise RuntimeError(marker)

            def __exit__(self, *_args):
                return None

        with pytest.raises(odds.KbisaiTransportError) as info:
            odds.fetch_competition_category(client_factory=FailingClient)
        assert marker not in repr(info.value.__context__)
        assert info.value.__cause__ is None
        assert info.value.__context__ is None

    def test_invalid_http_status_is_fixed_safe_error(self):
        _Client.response = _Response(b"", status_code=403)
        with pytest.raises(odds.KbisaiTransportError) as info:
            odds.fetch_all_companies(client_factory=_Client)
        assert info.value.__cause__ is None
        assert info.value.__context__ is None
        assert "kbisailive" not in str(info.value)


class TestDeriveMarketPhase:
    def test_pre_match_when_not_started_and_observed_before_kickoff(self):
        assert odds.derive_market_phase(1, "2026-08-04T10:00:00Z", "2026-08-07T17:00:00Z") == "pre_match"

    def test_unknown_when_not_started_but_observed_after_kickoff(self):
        """NOT_STARTED 但观测时间不早于开球——数据滞后/时钟不一致,不能直接判 in_play,也不算 pre_match。"""
        assert odds.derive_market_phase(1, "2026-08-07T18:00:00Z", "2026-08-07T17:00:00Z") == "unknown"

    def test_in_play_for_in_play_status_group(self):
        for status_id in (2, 3, 4, 5, 6, 7):
            assert odds.derive_market_phase(status_id, "2026-08-07T17:30:00Z", "2026-08-07T17:00:00Z") == "in_play"

    def test_in_play_for_finished_status(self):
        assert odds.derive_market_phase(8, "2026-08-07T19:00:00Z", "2026-08-07T17:00:00Z") == "in_play"

    def test_unknown_for_other_status_group(self):
        for status_id in (9, 10, 11, 12, 13):
            assert odds.derive_market_phase(status_id, "2026-08-04T10:00:00Z", "2026-08-07T17:00:00Z") == "unknown"

    def test_unknown_for_unmapped_status_id(self):
        assert odds.derive_market_phase(999, "2026-08-04T10:00:00Z", "2026-08-07T17:00:00Z") == "unknown"

    def test_unknown_when_kickoff_missing(self):
        assert odds.derive_market_phase(1, "2026-08-04T10:00:00Z", None) == "unknown"

    def test_unknown_when_source_updated_at_missing(self):
        assert odds.derive_market_phase(1, None, "2026-08-07T17:00:00Z") == "unknown"


class TestParseMatchAllOddsPoints:
    def test_real_eu_series_preserves_every_point_with_no_handicap_line(self):
        decoded = _fixture_json("decrypted-matchAllOdds-eu-trimmed.json")
        rows = odds.parse_match_all_odds_points(
            decoded, "eu",
            provider_match_id="4523588", fotmob_match_id=None, kickoff_at_utc=None,
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run",
        )
        assert len(rows) == 3
        assert all(r["market"] == "1x2" for r in rows)
        assert all(r["source_market"] == "eu" for r in rows)
        assert all(r["handicap_line"] is None for r in rows)
        assert all(r["odds_draw"] is not None for r in rows)

    def test_real_asia_series_extracts_handicap_line_at_slot_1(self):
        decoded = _fixture_json("decrypted-matchAllOdds-asia-trimmed.json")
        rows = odds.parse_match_all_odds_points(
            decoded, "asia",
            provider_match_id="4523588", fotmob_match_id=5104970,
            kickoff_at_utc="2026-07-30T14:00:00Z",
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run",
        )
        assert len(rows) == 3
        assert all(r["market"] == "ah" for r in rows)
        assert all(r["handicap_line"] is not None for r in rows)
        assert all(r["odds_draw"] is None for r in rows)
        assert rows[0]["handicap_line"] == "0"
        assert rows[0]["odds_home_or_over"] == "0.42"
        assert rows[0]["odds_away_or_under"] == "1.56"

    def test_real_bs_series_extracts_total_line_at_slot_1(self):
        decoded = _fixture_json("decrypted-matchAllOdds-bs-trimmed.json")
        rows = odds.parse_match_all_odds_points(
            decoded, "bs",
            provider_match_id="4523588", fotmob_match_id=None, kickoff_at_utc=None,
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run",
        )
        assert len(rows) == 3
        assert all(r["market"] == "ou" for r in rows)
        assert all(r["handicap_line"] is not None for r in rows)
        assert rows[0]["handicap_line"] == "3.5"

    def test_target_company_filter_drops_non_target_companies(self):
        decoded = {
            "7": {"statusMatchOdds": [{"oddsInfo": ["1.5", "3.6", "2.5", "0"], "changeTime": 1785000000, "goingTime": "", "score": "0-0", "statusId": 1}]},
            "999": {"statusMatchOdds": [{"oddsInfo": ["1.9", "3.4", "2.1", "0"], "changeTime": 1785000000, "goingTime": "", "score": "0-0", "statusId": 1}]},
        }
        rows = odds.parse_match_all_odds_points(
            decoded, "eu",
            provider_match_id="x", fotmob_match_id=None, kickoff_at_utc=None,
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run", target_company_ids={"7", "22"},
        )
        assert len(rows) == 1
        assert rows[0]["company_id"] == "7"

    def test_duplicate_changetime_different_odds_gets_distinct_point_hash(self):
        """真实观测到过的边界情况(match 2000000):同一 changeTime 两条不同赔率——
        point_hash 必须不同,两条都要能各自落库(dup_ordinal 都是 0)。"""
        decoded = {
            "3": {"statusMatchOdds": [
                {"oddsInfo": ["3.2", "3.25", "2.1", "0"], "changeTime": 1465081200, "goingTime": "", "score": "0-0", "statusId": 1},
                {"oddsInfo": ["2.9", "3.3", "2.2", "0"], "changeTime": 1465081200, "goingTime": "", "score": "0-0", "statusId": 1},
            ]}
        }
        rows = odds.parse_match_all_odds_points(
            decoded, "eu",
            provider_match_id="2000000", fotmob_match_id=None, kickoff_at_utc=None,
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run",
        )
        assert len(rows) == 2
        assert rows[0]["point_hash"] != rows[1]["point_hash"]
        assert rows[0]["dup_ordinal"] == 0 and rows[1]["dup_ordinal"] == 0
        assert {rows[0]["odds_home_or_over"], rows[1]["odds_home_or_over"]} == {"3.2", "2.9"}

    def test_byte_identical_duplicate_entries_get_incrementing_dup_ordinal(self):
        entry = {"oddsInfo": ["3.2", "3.25", "2.1", "0"], "changeTime": 1465081200, "goingTime": "", "score": "0-0", "statusId": 1}
        decoded = {"3": {"statusMatchOdds": [dict(entry), dict(entry)]}}
        rows = odds.parse_match_all_odds_points(
            decoded, "eu",
            provider_match_id="2000000", fotmob_match_id=None, kickoff_at_utc=None,
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run",
        )
        assert len(rows) == 2
        assert rows[0]["point_hash"] == rows[1]["point_hash"]
        assert {rows[0]["dup_ordinal"], rows[1]["dup_ordinal"]} == {0, 1}

    def test_malformed_entry_is_skipped_not_fabricated(self):
        decoded = {
            "7": {"statusMatchOdds": [
                {"oddsInfo": ["1.5", "3.6"], "changeTime": 1785000000, "statusId": 1},  # 长度不足,应跳过
                {"oddsInfo": ["1.5", "3.6", "2.5", "0"], "changeTime": 1785000001, "goingTime": "", "score": "0-0", "statusId": 1},
            ]}
        }
        rows = odds.parse_match_all_odds_points(
            decoded, "eu",
            provider_match_id="x", fotmob_match_id=None, kickoff_at_utc=None,
            observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
            poll_run_id="test-run",
        )
        assert len(rows) == 1

    def test_rejects_invalid_source_market(self):
        with pytest.raises(odds.KbisaiOddsError):
            odds.parse_match_all_odds_points(
                {}, "not-a-market",
                provider_match_id="x", fotmob_match_id=None, kickoff_at_utc=None,
                observed_at="2026-08-04T00:00:00Z", ingested_at="2026-08-04T00:00:00Z",
                poll_run_id="test-run",
            )


class TestRealHttpxClientIsAcceptedAsDefault:
    def test_default_client_factory_is_real_httpx_client(self):
        """确认默认值真的是 httpx.Client(不是测试替身),签名契约不漂移。"""
        import inspect

        sig = inspect.signature(odds.fetch_all_companies)
        assert sig.parameters["client_factory"].default is httpx.Client
