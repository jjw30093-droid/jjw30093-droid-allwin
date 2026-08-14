"""方糖告警通道测试(数据管道重建 Phase 5,全离线,推送经 stub)。

覆盖:持久化优先、NOTIFY_ENABLED=0 全落库零网络、code!=0(如 40024 配额耗尽)
不算成功、24h 去重、每日配额(INFO 1 / WARNING 2 / 软上限 4 只放行 CRITICAL)、
sendkey 脱敏、敏感文本整体退化、notify 永不抛 + 三条暴露路径、runner 失败分支
只对最初失败步骤告警。
"""

import pytest

from backend import notify as notify_mod
from backend.db.connections import connect_ro, connect_rw
from backend.worker import runner

ENABLED_ENV = {"NOTIFY_ENABLED": "1", "SERVERCHAN_SENDKEY": "SK_TEST_123"}
DISABLED_ENV = {"NOTIFY_ENABLED": "0"}
T0 = "2026-08-10T12:00:00Z"


@pytest.fixture
def push_log(monkeypatch):
    """截获真实推送:测试永不发网络;可按需改返回值。"""
    calls = []
    state = {"reply": (True, "sent")}

    def fake_push(sendkey, title, body):
        calls.append({"sendkey": sendkey, "title": title, "body": body})
        return state["reply"]

    monkeypatch.setattr(notify_mod, "_push_serverchan", fake_push)
    calls_obj = {"calls": calls, "state": state}
    return calls_obj


def _rows(where="1=1"):
    conn = connect_ro("platform")
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM pipeline_alerts WHERE {where} ORDER BY id")]
    finally:
        conn.close()


class TestPersistFirst:
    def test_disabled_records_row_without_network(self, data_dir, push_log):
        res = notify_mod.notify("CRITICAL", "pipeline_step_failure", "t1", "b1",
                                env=DISABLED_ENV, now_iso=T0)
        assert res["persisted"] is True and res["notified"] is False
        assert res["result"] == "suppressed:disabled"
        assert push_log["calls"] == [], "NOTIFY_ENABLED=0 时绝不发网络"
        rows = _rows()
        assert len(rows) == 1
        assert rows[0]["notify_result"] == "suppressed:disabled"
        assert rows[0]["notified_at"] is None

    def test_enabled_with_key_sends(self, data_dir, push_log):
        res = notify_mod.notify("CRITICAL", "pipeline_step_failure", "t1", "b1",
                                env=ENABLED_ENV, now_iso=T0)
        assert res["notified"] is True and res["result"] == "sent"
        assert len(push_log["calls"]) == 1
        row = _rows()[0]
        assert row["notify_result"] == "sent" and row["notified_at"] is not None

    def test_enabled_without_key_is_misconfigured_not_crash(self, data_dir, push_log):
        res = notify_mod.notify("CRITICAL", "s", "t", "b",
                                env={"NOTIFY_ENABLED": "1"}, now_iso=T0)
        assert res["result"] == "failed:misconfigured"
        assert push_log["calls"] == []
        assert _rows()[0]["notify_result"] == "failed:misconfigured"

    def test_http_200_but_api_code_nonzero_is_failure(self, data_dir, push_log):
        """40024=配额耗尽等:HTTP 200 不代表推送成功,必须解析响应体 code==0。"""
        push_log["state"]["reply"] = (False, "failed:api_code_40024")
        res = notify_mod.notify("CRITICAL", "s", "t", "b", env=ENABLED_ENV, now_iso=T0)
        assert res["notified"] is False
        assert res["result"] == "failed:api_code_40024"
        assert _rows()[0]["notified_at"] is None


class TestDedup:
    def test_same_dedup_key_within_24h_suppressed(self, data_dir, push_log):
        notify_mod.notify("CRITICAL", "s", "t", "b", dedup_key="k1",
                          env=ENABLED_ENV, now_iso=T0)
        res2 = notify_mod.notify("CRITICAL", "s", "t", "b", dedup_key="k1",
                                 env=ENABLED_ENV, now_iso="2026-08-10T18:00:00Z")
        assert res2["result"] == "suppressed:dedup"
        assert len(push_log["calls"]) == 1
        # 两条都落库:记录不去重,只有推送去重
        assert len(_rows("dedup_key='k1'")) == 2

    def test_same_dedup_key_after_24h_sends_again(self, data_dir, push_log):
        notify_mod.notify("CRITICAL", "s", "t", "b", dedup_key="k1",
                          env=ENABLED_ENV, now_iso=T0)
        res2 = notify_mod.notify("CRITICAL", "s", "t", "b", dedup_key="k1",
                                 env=ENABLED_ENV, now_iso="2026-08-11T13:00:00Z")
        assert res2["result"] == "sent"
        assert len(push_log["calls"]) == 2

    def test_failed_push_does_not_dedup_next_attempt(self, data_dir, push_log):
        """去重只看已成功推送(sent)——上次失败不应吞掉下次重试。"""
        push_log["state"]["reply"] = (False, "failed:http_500")
        notify_mod.notify("CRITICAL", "s", "t", "b", dedup_key="k1",
                          env=ENABLED_ENV, now_iso=T0)
        push_log["state"]["reply"] = (True, "sent")
        res2 = notify_mod.notify("CRITICAL", "s", "t", "b", dedup_key="k1",
                                 env=ENABLED_ENV, now_iso="2026-08-10T12:30:00Z")
        assert res2["result"] == "sent"


class TestQuota:
    def _send(self, level, key, now=T0):
        return notify_mod.notify(level, "s", f"t-{key}", "b", dedup_key=key,
                                 env=ENABLED_ENV, now_iso=now)

    def test_info_limited_to_one_per_day(self, data_dir, push_log):
        assert self._send("INFO", "i1")["result"] == "sent"
        assert self._send("INFO", "i2")["result"] == "suppressed:quota"

    def test_warning_limited_to_two_per_day(self, data_dir, push_log):
        assert self._send("WARNING", "w1")["result"] == "sent"
        assert self._send("WARNING", "w2")["result"] == "sent"
        assert self._send("WARNING", "w3")["result"] == "suppressed:quota"

    def test_soft_cap_reserves_last_slot_for_critical(self, data_dir, push_log):
        """4 条已发出后:非 CRITICAL 一律 suppressed:quota,CRITICAL 照发。"""
        for i in range(4):
            assert self._send("CRITICAL", f"c{i}")["result"] == "sent"
        assert self._send("WARNING", "w1")["result"] == "suppressed:quota"
        assert self._send("INFO", "i1")["result"] == "suppressed:quota"
        assert self._send("CRITICAL", "c9")["result"] == "sent"

    def test_quota_resets_next_utc_day(self, data_dir, push_log):
        assert self._send("INFO", "i1")["result"] == "sent"
        assert self._send("INFO", "i2", now="2026-08-11T00:10:00Z")["result"] == "sent"


class TestRedaction:
    def test_sendkey_never_persisted_or_pushed(self, data_dir, push_log):
        notify_mod.notify("CRITICAL", "s", "含 key 的标题 SK_TEST_123",
                          "正文也有 SK_TEST_123", env=ENABLED_ENV, now_iso=T0)
        row = _rows()[0]
        assert "SK_TEST_123" not in (row["title"] or "")
        assert "SK_TEST_123" not in (row["body"] or "")
        call = push_log["calls"][0]
        assert "SK_TEST_123" not in call["title"] and "SK_TEST_123" not in call["body"]

    def test_sensitive_body_lines_degrade_wholesale(self, data_dir, push_log):
        notify_mod.notify("CRITICAL", "s", "t",
                          "proxy=user:supersecret@host\n第二行是安全的",
                          env=DISABLED_ENV, now_iso=T0)
        body = _rows()[0]["body"]
        assert "supersecret" not in body
        assert "[CREDENTIAL_REDACTED]" in body
        assert "第二行是安全的" in body, "逐行脱敏:安全行必须保留"


class TestNeverRaises:
    def test_missing_table_returns_unpersisted_with_stderr_marker(self, data_dir, capsys):
        conn = connect_rw("platform")
        conn.execute("DROP TABLE pipeline_alerts")
        conn.commit()
        conn.close()
        res = notify_mod.notify("CRITICAL", "s", "t", "b", env=DISABLED_ENV, now_iso=T0)
        assert res["persisted"] is False
        assert res["result"] == "suppressed:disabled"
        assert notify_mod.PERSIST_FAIL_MARKER in capsys.readouterr().err

    def test_internal_error_swallowed(self, data_dir, monkeypatch, capsys):
        monkeypatch.setattr(notify_mod, "_sanitize_text",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        res = notify_mod.notify("CRITICAL", "s", "t", "b", env=DISABLED_ENV, now_iso=T0)
        assert res == {"persisted": False, "notified": False,
                       "result": "failed:internal", "alert_id": None}
        assert notify_mod.PERSIST_FAIL_MARKER in capsys.readouterr().err


def _boom():
    raise RuntimeError("模拟任务失败")


@pytest.fixture
def registry():
    saved = dict(runner.REGISTRY)
    yield runner.REGISTRY
    runner.REGISTRY.clear()
    runner.REGISTRY.update(saved)


class TestRunnerWiring:
    def test_failed_job_alerts_after_job_runs_persisted(self, data_dir, registry, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        runner.register_job("boom_job", fn=_boom, max_attempts=1)
        res = runner.run_job("boom_job")
        assert res["status"] == "failed"
        rows = _rows("source='pipeline_step_failure'")
        assert len(rows) == 1 and rows[0]["level"] == "CRITICAL"
        assert "boom_job" in rows[0]["title"]
        # 暴露路径②:告警结果写回 job_runs.meta_json
        conn = connect_ro("platform")
        try:
            meta = conn.execute(
                "SELECT meta_json FROM job_runs WHERE id=?", (res["run_id"],)
            ).fetchone()[0]
        finally:
            conn.close()
        assert '"alert"' in meta and '"persisted": true' in meta

    def test_missing_proxy_env_maps_to_proxy_unavailable(self, data_dir, registry, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        monkeypatch.delenv("THORDATA_PROXY", raising=False)
        runner.register_job("needs_proxy", fn=_boom, max_attempts=1,
                            require_env=("THORDATA_PROXY",))
        res = runner.run_job("needs_proxy")
        assert res["status"] == "failed"
        assert len(_rows("source='proxy_unavailable'")) == 1

    def test_chain_alerts_only_initial_failure(self, data_dir, registry, monkeypatch):
        """cascade-skip 的步骤不执行、不告警——只有最初失败那一步推一条。"""
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        calls = []
        runner.register_job("a_boom", fn=_boom, max_attempts=1)
        runner.register_job("b_after", fn=lambda: calls.append("b") or {"output_count": 0},
                            max_attempts=1)
        runner.register_job("c_after", fn=lambda: calls.append("c") or {"output_count": 0},
                            max_attempts=1)
        results = runner.run_chain(names=["a_boom", "b_after", "c_after"])
        assert [r["status"] for r in results] == ["failed", "skipped", "skipped"]
        assert calls == [], "下游步骤不得执行"
        assert len(_rows()) == 1, "只有最初失败的那一步告警"
        assert _rows()[0]["dedup_key"] == "pipeline_step_failure:a_boom"

    def test_skipped_and_locked_do_not_alert(self, data_dir, registry, monkeypatch):
        monkeypatch.setenv("NOTIFY_ENABLED", "0")
        runner.register_job("ok_job", fn=lambda: {"output_count": 0}, max_attempts=1)
        runner.run_job("ok_job", idempotency_key="k")
        runner.run_job("ok_job", idempotency_key="k")   # 第二次 skipped
        assert _rows() == []
