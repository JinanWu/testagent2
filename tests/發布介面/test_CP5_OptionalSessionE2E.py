"""A09-04 canonical ASGI 可選工作階段 live／restart 驗收。"""

import sqlite3

from fastapi.testclient import TestClient

from test_CP4_Controller生產呼叫 import _建立live環境


def _呼叫(client, key, *, session: str | None | object = ...):
    本文: dict[str, object] = {"input": {"question": "CP5"}}
    if session is not ...:
        本文["session_id"] = session
    return client.post(
        "/v1/endpoints/demo/invoke",
        headers={"Authorization": f"Bearer {key}"},
        json=本文,
    )


def test_canonical真Key多輪隔離null省略與restart都由durable_history驅動(tmp_path):
    db, app, key, model, _ = _建立live環境(tmp_path)

    with TestClient(app, raise_server_exceptions=False) as client:
        openapi = client.get("/openapi.json").json()
        session_schema = openapi["paths"]["/v1/endpoints/{slug}/invoke"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]["properties"]["session_id"]
        assert session_schema == {
            "anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]
        }

        第一輪 = _呼叫(client, key, session="case-a")
        其他工作階段 = _呼叫(client, key, session="case-b")
        省略 = _呼叫(client, key)
        明確空值 = _呼叫(client, key, session=None)
        第二輪 = _呼叫(client, key, session="case-a")

    assert [回應.status_code for 回應 in (第一輪, 其他工作階段, 省略, 明確空值, 第二輪)] == [200] * 5
    assert 第一輪.json()["invocation"]["session_id"] == "case-a"
    assert 其他工作階段.json()["invocation"]["session_id"] == "case-b"
    assert 省略.json()["invocation"]["session_id"] is None
    assert 明確空值.json()["invocation"]["session_id"] is None

    assert [訊息["role"] for 訊息 in model.calls[0]["messages"]] == ["system", "user"]
    assert [訊息["role"] for 訊息 in model.calls[1]["messages"]] == ["system", "user"]
    assert [訊息["role"] for 訊息 in model.calls[4]["messages"]] == [
        "system", "user", "assistant", "user",
    ]
    assert model.calls[4]["messages"][1]["content"] == {"question": "CP5"}
    assert model.calls[4]["messages"][2]["content"] == {"answer": "CP4"}

    with sqlite3.connect(db) as connection:
        before_restart = connection.execute(
            "SELECT session_id,sequence_number FROM published_session_turn_pairs "
            "ORDER BY session_id,sequence_number"
        ).fetchall()
    assert before_restart == [("case-a", 1), ("case-a", 2), ("case-b", 1)]

    # 同一 canonical app factory 重新進入 lifespan，會重建 production repository/provider。
    with TestClient(app, raise_server_exceptions=False) as restarted:
        第三輪 = _呼叫(restarted, key, session="case-a")
    assert 第三輪.status_code == 200
    assert [訊息["role"] for 訊息 in model.calls[-1]["messages"]] == [
        "system", "user", "assistant", "user", "assistant", "user",
    ]

    with sqlite3.connect(db) as connection:
        after_restart = connection.execute(
            "SELECT session_id,sequence_number,endpoint_version_id "
            "FROM published_session_turn_pairs ORDER BY session_id,sequence_number"
        ).fetchall()
    assert after_restart == [
        ("case-a", 1, "ver-1"), ("case-a", 2, "ver-1"),
        ("case-a", 3, "ver-1"), ("case-b", 1, "ver-1"),
    ]
