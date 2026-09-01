"""A08-3 stable URL: formal artifacts, retry race, restart and corruption."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import asgi as root_asgi
from a08_3_stable_support import 七欄, 呼叫, 安裝SQL觀測, resolver_sql, snapshot_select_sql, live, 破壞


@pytest.mark.parametrize("extra", [
    "SELECT MAX(version_number) FROM published_endpoint_versions WHERE endpoint_id='endpoint-1'",
    "SELECT current_version_id FROM published_endpoints WHERE slug='stable'",
    "SELECT e.id FROM published_endpoints e JOIN published_endpoint_versions v "
    "ON v.id=e.current_version_id AND v.endpoint_id=e.id WHERE e.slug='stable'",
    "/* comment */ SELECT current_version_id FROM published_endpoints",
    "WITH hidden AS (SELECT current_version_id FROM published_endpoints) SELECT * FROM hidden",
])
def test_resolver_SQL分類器拒絕任何額外authority查詢(extra):
    def observe(authority):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE published_endpoints(id TEXT,current_version_id TEXT,slug TEXT,status TEXT);"
            "CREATE TABLE published_endpoint_versions(id TEXT,endpoint_id TEXT,version_number INTEGER);"
            "INSERT INTO published_endpoints VALUES('e','v','stable','active');"
            "INSERT INTO published_endpoint_versions VALUES('v','e',1);"
        )
        evidence = []
        安裝SQL觀測(connection, evidence)
        connection.execute(authority).fetchall()
        connection.execute(extra).fetchall()
        connection.close()
        return evidence

    resolver = observe(
        "SELECT e.id FROM published_endpoints e JOIN published_endpoint_versions v "
        "ON v.id=e.current_version_id AND v.endpoint_id=e.id "
        "WHERE e.slug='stable' AND e.status='active'"
    )
    snapshot = observe(
        "SELECT v.id FROM published_endpoint_versions AS v JOIN published_endpoints AS e "
        "ON e.id=v.endpoint_id WHERE v.id='v'"
    )
    with pytest.raises(AssertionError): resolver_sql(resolver)
    with pytest.raises(AssertionError): snapshot_select_sql(snapshot)
def _assert_success(response, version: int, answer: str):
    assert response.status_code == 200
    body = response.json()
    assert set(body) == 七欄 and body["ok"] is True and body["error"] is None
    assert body["endpoint"]["version"] == version and body["data"] == {"answer": answer}
    return body
def test_canonical_stable_invoke_live_200_and_slug_miss_zero_invocation(live):
    with TestClient(root_asgi.建立應用程式(), raise_server_exceptions=False) as client:
        body = _assert_success(呼叫(client, live, {"plain": True}), 1, "v1")
        with sqlite3.connect(live["db"]) as c:
            before = c.execute("SELECT count(*) FROM endpoint_invocations").fetchone()[0]
        miss = 呼叫(client, live, {"miss": True}, "does-not-exist")
        assert miss.status_code == 404 and set(miss.json()) == 七欄
        assert miss.json()["error"]["code"] == "endpoint_not_found"
    with sqlite3.connect(live["db"]) as c:
        terminal = c.execute("SELECT endpoint_version_id,status,output_json FROM endpoint_invocations WHERE id=?",
                             (body["invocation"]["id"],)).fetchone()
        assert terminal == (live["v1"], "succeeded", '{"answer":"v1"}')
        assert c.execute("SELECT count(*) FROM endpoint_invocations").fetchone()[0] == before
def test_inflight_v1_next_v2_and_retry_keeps_pin_once(live):
    app = root_asgi.建立應用程式()
    box, errors = {}, []
    with TestClient(app, raise_server_exceptions=False) as client:
        def run_a():
            try: box["A"] = 呼叫(client, live, {"central-race": True})
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=run_a)
        thread.start()
        try:
            assert live["model"].retry已進入.wait(10)
            live["publish_v2"]()
        finally:
            live["model"].retry可執行.set()
            thread.join(10)
        assert not thread.is_alive()
        if errors: raise errors[0]
        a_body = _assert_success(box["A"], 1, "v1")
        _assert_success(呼叫(client, live, {"request-b": True}), 2, "v2")
    calls = live["model"].calls
    assert len(calls) == 3
    for call in calls[:2]:
        assert call["model"] == "gemini-pinned-v1"
        assert "SYSTEM-V1" in call["messages"][0]["content"] and "BUNDLE-V1" in call["messages"][0]["content"]
        assert [x["function"]["name"] for x in call["tools"]] == ["skills_list", "skill_view"]
    assert calls[2]["model"] == "gemini-pinned-v2" and "BUNDLE-V2" in calls[2]["messages"][0]["content"]
    with sqlite3.connect(live["db"]) as c:
        account = c.execute("SELECT service_account_id FROM published_endpoints WHERE id=?",
                            (live["endpoint"],)).fetchone()[0]
    assert live["materials"][0] == live["materials"][1]
    first, next_request = live["materials"][0], live["materials"][2]
    assert first[:3] == (live["endpoint"], account, live["v1"])
    assert next_request[:3] == (live["endpoint"], account, live["v2"])
    assert first[3] == "SYSTEM-V1" and next_request[3] == "SYSTEM-V2"
    assert first[5] != next_request[5] and first[6] != next_request[6]
    assert first[7] == next_request[7] == "testagent2-published-skills-v1"
    assert tuple((x[0], x[1]) for x in first[8]) == (
        ("skills_list", "skills_list@bundle-v1"), ("skill_view", "skill_view@bundle-v1"))
    assert all(len(x[2]) == 64 for x in first[8])
    assert dict(first[9])["model"] == "gemini-pinned-v1"
    assert dict(next_request[9])["model"] == "gemini-pinned-v2"
    assert len(live["traces"]) == 2
    authoritative = [resolver_sql(trace) for trace in live["traces"]]
    assert len(authoritative) == 2
    selects = [sql for trace in live["snapshot_traces"] for sql in snapshot_select_sql(trace)]
    assert selects
    exact = []
    for sql in selects:
        lowered = sql.lower()
        assert all(token not in lowered for token in ("current_version_id", "slug", "latest", "max("))
        if "published_endpoint_versions" in lowered and "sqlite_master" not in lowered:
            assert "from published_endpoint_versions as v" in lowered and "where v.id=" in lowered
            exact.append(sql)
    assert len(exact) >= 3
    with sqlite3.connect(live["db"]) as c:
        rows = c.execute("SELECT endpoint_version_id,status FROM endpoint_invocations ORDER BY created_at,id").fetchall()
        events = c.execute(
            "SELECT r.payload_json FROM run_events r JOIN endpoint_invocations i ON i.id=r.invocation_id "
            "WHERE r.invocation_id=? ORDER BY r.sequence_number",
            (a_body["invocation"]["id"],),
        ).fetchall()
    assert rows == [(live["v1"], "succeeded"), (live["v2"], "succeeded")]
    assert events == [('{"attempt":1,"kind":"success","schema_valid":false}',),
                      ('{"attempt":2,"kind":"success","schema_valid":true}',)]


def _wait_service(port: int, process: subprocess.Popen[str]):
    for _ in range(100):
        if process.poll() is not None:
            out, err = process.communicate()
            raise AssertionError(f"uvicorn exited: {out}\n{err}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=.2).status_code == 200: return
        except httpx.HTTPError: pass
        time.sleep(.05)
    raise AssertionError("uvicorn not ready")


def _drain_process(live):
    env = os.environ.copy()
    env.update({"A08_3_MODE": "drain", "A08_3_API_KEY": live["key"],
                "A08_3_OWNER": live["owner"], "A08_3_ENDPOINT": live["endpoint"],
                "A08_3_SKILL_ROOT": str(live["skills"])})
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("a08_3_uvicorn_runner.py"))],
        cwd=Path(__file__).parents[2], env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        out, err = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill(); out, err = process.communicate(timeout=5)
        raise AssertionError(f"drain child timeout: {out}\n{err}")
    assert process.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1])


def test_restart_keeps_stable_url_and_current_v2_openapi(live):
    route = "/v1/endpoints/{slug}/invoke"
    before_openapi = root_asgi.建立應用程式().openapi()
    drain = _drain_process(live)
    assert drain["pid"] != os.getpid() and drain["premature"] is False
    assert drain["first"]["endpoint"]["version"] == 1
    assert drain["drained"]["endpoint"]["version"] == 2
    with sqlite3.connect(live["db"]) as c:
        current = c.execute("SELECT current_version_id FROM published_endpoints WHERE id=?",
                            (live["endpoint"],)).fetchone()[0]
    assert drain["v2"] == current
    listener = socket.socket()
    process = None
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0)); listener.listen()
        port = listener.getsockname()[1]
        env = os.environ.copy(); env["A08_3_SOCKET_FD"] = str(listener.fileno())
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("a08_3_uvicorn_runner.py"))],
            cwd=Path(__file__).parents[2], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, pass_fds=(listener.fileno(),))
        listener.close()
        assert process.pid not in (os.getpid(), drain["pid"]); _wait_service(port, process)
        after = httpx.get(f"http://127.0.0.1:{port}/openapi.json").json()
        assert set(before_openapi["paths"][route]) == set(after["paths"][route]) == {"post"}
        response = httpx.post(f"http://127.0.0.1:{port}/v1/endpoints/stable/invoke",
            headers={"Authorization": f"Bearer {live['key']}"}, json={"input": {"restart": True}}, timeout=5)
        _assert_success(response, 2, "v2")
    finally:
        listener.close()
        if process is not None:
            process.terminate()
            try: process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate(timeout=5)


@pytest.mark.parametrize("case", ["manifest_digest", "bundle_hash", "bundle_size", "bundle_identity",
                                   "tool_release", "tool_revision", "tool_digest",
                                   "model_provider", "model_config"])
def test_corruption_matrix_fails_before_model(live, case):
    readback = 破壞(live["db"], live["bundles"], live["v1"], case)
    expected = {
        "manifest_digest": "tampered", "bundle_hash": "0" * 64,
        "bundle_size": 1, "bundle_identity": "wrong-bundle",
        "tool_release": "wrong-release", "tool_revision": "skills_list@wrong",
        "model_provider": "missing", "model_config": 0,
    }
    if case in {"tool_digest", "tool_revision", "model_provider", "model_config"}:
        assert isinstance(readback, dict)
    if case == "tool_digest": assert readback["description"].endswith(" drift")
    elif case == "tool_revision": assert readback["revision"] == expected[case]
    elif case == "model_provider": assert readback["provider"] == expected[case]
    elif case == "model_config": assert readback["schema_retry_count"] == expected[case]
    else: assert readback == expected[case]
    app = root_asgi.建立應用程式()
    if case in {"manifest_digest", "bundle_hash", "bundle_size", "bundle_identity"}:
        with pytest.raises(RuntimeError, match="^發布介面啟動失敗$"):
            with TestClient(app, raise_server_exceptions=False): pass
    else:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = 呼叫(client, live, {"corrupt": case})
        body = response.json()
        assert response.status_code == 500 and set(body) == 七欄
        assert body["ok"] is False and body["data"] is None and body["usage"] is None and body["warnings"] == []
        assert body["endpoint"] == {"id": live["endpoint"], "slug": "stable", "version": 1}
        assert body["error"] == {"code": "endpoint_misconfigured",
                                 "message": "Endpoint 設定錯誤。", "details": {}}
        with sqlite3.connect(live["db"]) as c:
            invocation = c.execute(
                "SELECT request_id,session_id FROM endpoint_invocations WHERE id=?",
                (body["invocation"]["id"],),).fetchone()
        assert body["invocation"] == {"id": body["invocation"]["id"],
                                      "request_id": invocation[0], "session_id": invocation[1]}
    assert live["model"].calls == []
