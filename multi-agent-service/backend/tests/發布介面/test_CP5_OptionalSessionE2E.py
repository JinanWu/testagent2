"""A09-04 canonical ASGI 可選工作階段 live／restart 驗收。"""

import sqlite3
import time

from fastapi.testclient import TestClient

from test_CP4_Controller生產呼叫 import _建立live環境
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.服務 import SQLite憑證撤銷服務
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.儲存庫 import 套件收據儲存庫
from 繁中代理.發布介面.呼叫.Published工作階段 import SQLitePublished工作階段儲存庫


def _呼叫(client, key, *, session: str | None | object = ..., slug="demo"):
    """經 canonical live route 呼叫指定 Published endpoint。

    參數：TestClient、API key、optional session wire value 與 endpoint slug。
    返回值：live HTTP response。
    """
    本文: dict[str, object] = {"input": {"question": "CP5"}}
    if session is not ...:
        本文["session_id"] = session
    return client.post(
        f"/v1/endpoints/{slug}/invoke",
        headers={"Authorization": f"Bearer {key}"},
        json=本文,
    )


def test_canonical真Key多輪隔離null省略與restart都由durable_history驅動(tmp_path):
    """驗證真 key 多輪、null／省略與 app restart 使用 durable history。

    參數：``tmp_path`` 提供 canonical app 的隔離資源。
    返回值：無；live response、prompt 與 durable rows assertions 必須通過。
    """
    db, app, key, model, _ = _建立live環境(tmp_path)

    with TestClient(app, raise_server_exceptions=False) as client:
        openapi = client.get("/openapi.json").json()
        session_schema = openapi["paths"]["/v1/endpoints/{slug}/invoke"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]["properties"]["session_id"]
        assert session_schema == {
            "anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}],
            "x-utf8-max-bytes": 128,
            "description": "Optional Published session identifier；上限 128 UTF-8 bytes。",
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

    # 關閉第一個 app 後，以相同 explicit settings／DB 建立全新 canonical app instance。
    重啟應用程式 = app.state.重建canonical應用程式()
    assert 重啟應用程式 is not app
    with TestClient(重啟應用程式, raise_server_exceptions=False) as restarted:
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


def test_canonical同服務帳戶不同有效key共享且拒絕key零history讀寫(tmp_path, monkeypatch):
    """驗證第二把 key 延續同 SA session，三類拒絕 key 全部零附加。

    參數：``tmp_path`` 提供 canonical app 的隔離資源。
    返回值：無；live prompt、HTTP status 與 row count assertions 必須通過。
    """
    db, app, 第一金鑰, model, _ = _建立live環境(tmp_path)
    現在 = time.time()
    封套 = AESGCM憑證封套({1: b"k" * 32}, 1)
    第二 = SQLite憑證儲存庫(
        db, 封套, clock=lambda: 現在, id_factory=lambda: "cred-2",
    ).建立(
        "ep-1", WebOwnerPrincipal("owner-1"), name="second", purpose="A09 shared SA",
        expires_at=現在 + 3600, rate_limit_requests=60,
    )
    過期 = SQLite憑證儲存庫(
        db, 封套, clock=lambda: 現在 - 7200, id_factory=lambda: "cred-expired",
    ).建立(
        "ep-1", WebOwnerPrincipal("owner-1"), name="expired", purpose="A09 rejection",
        expires_at=現在 - 3600, rate_limit_requests=60,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        assert _呼叫(client, 第一金鑰, session="shared").status_code == 200
        共享 = _呼叫(client, 第二.api_key, session="shared")
        assert 共享.status_code == 200
        assert [訊息["role"] for 訊息 in model.calls[-1]["messages"]] == [
            "system", "user", "assistant", "user",
        ]
        with sqlite3.connect(db) as connection:
            基準 = connection.execute("SELECT COUNT(*) FROM published_session_turn_pairs").fetchone()
        讀取次數 = 0
        原讀取 = SQLitePublished工作階段儲存庫.讀取成功歷史

        def 可觀測讀取(self, *參數):
            """計數credential gate後是否觸及history reader。

            參數：repository instance與原讀取scope參數。
            返回值：原repository讀取結果。
            """
            nonlocal 讀取次數
            讀取次數 += 1
            return 原讀取(self, *參數)

        monkeypatch.setattr(SQLitePublished工作階段儲存庫, "讀取成功歷史", 可觀測讀取)
        assert _呼叫(client, "pak_INVALID_KEY", session="shared").status_code == 401
        assert _呼叫(client, 過期.api_key, session="shared").status_code == 401
        SQLite憑證撤銷服務(db, clock=lambda: 現在 + 1).撤銷(
            "ep-1", "cred-2", WebOwnerPrincipal("owner-1"), "revoke-a09",
        )
        assert _呼叫(client, 第二.api_key, session="shared").status_code == 401
        assert 讀取次數 == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM published_session_turn_pairs").fetchone() == 基準


def _新增端點或版本(tmp_path, db, *, endpoint, version, owner, account, number, bundle):
    """以正式 bundle publisher 建立 canonical runtime exact snapshot。

    參數：隔離根、資料庫，以及 wire identity／version／bundle 欄位。
    返回值：無；完成可信 bundle receipt、version 與 current pointer。
    """
    receipt = 技能套件發布器(tmp_path / "bundles").發布(
        套件識別碼=bundle, 端點識別碼=endpoint, 端點版本識別碼=version,
        版本號碼=number, 建立時間=float(number + 10), 建立者識別碼=owner,
        技能表={"cp5": tmp_path / "skill"},
    )
    manifest = (receipt.路徑 / "manifest.json").read_text(encoding="utf-8")
    with sqlite3.connect(db) as connection:
        if number == 1:
            connection.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", (account, number + 10))
            connection.execute(
                "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (endpoint, owner, account, f"demo-{number + 1}", "active", None,
                 number + 10, number + 10, 60, 60),
            )
        基準 = list(connection.execute(
            "SELECT * FROM published_endpoint_versions WHERE id='ver-1'"
        ).fetchone())
        基準[0:5] = [version, endpoint, number, "需求", "第二固定提示"]
        基準[11] = manifest
        基準[14:17] = [0, owner, number + 10]
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(基準),
        )
        套件收據儲存庫(connection).新增(版本識別碼=version, 收據=receipt, 發布時間=float(number + 11))
        connection.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (version, endpoint))


def test_canonical跨endpoint_owner隔離且version_switch保留舊turn_identity(tmp_path):
    """驗證跨 owner／SA 隔離及切版後新 pin、舊 turn identity。

    參數：``tmp_path`` 提供 canonical app 與正式 bundle graph。
    返回值：無；live prompt、version echo 與 durable identity assertions 必須通過。
    """
    db, app, 第一金鑰, model, _ = _建立live環境(tmp_path)
    _新增端點或版本(
        tmp_path, db, endpoint="ep-2", version="ver-foreign", owner="owner-2",
        account="sa-2", number=1, bundle="bundle-foreign",
    )
    現在 = time.time()
    第二金鑰 = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 現在,
        id_factory=lambda: "cred-foreign",
    ).建立(
        "ep-2", WebOwnerPrincipal("owner-2"), name="foreign", purpose="A09 isolation",
        expires_at=現在 + 3600, rate_limit_requests=60,
    ).api_key
    with TestClient(app, raise_server_exceptions=False) as client:
        assert _呼叫(client, 第一金鑰, session="same").status_code == 200
        foreign = _呼叫(client, 第二金鑰, session="same", slug="demo-2")
        assert foreign.status_code == 200
        assert [訊息["role"] for 訊息 in model.calls[-1]["messages"]] == ["system", "user"]

        _新增端點或版本(
            tmp_path, db, endpoint="ep-1", version="ver-2", owner="owner-1",
            account="sa-1", number=2, bundle="bundle-2",
        )
        switched = _呼叫(client, 第一金鑰, session="same")
        assert switched.status_code == 200
        assert switched.json()["endpoint"]["version"] == 2
        系統提示 = model.calls[-1]["messages"][0]["content"]
        assert 系統提示.startswith("第二固定提示\n\n")
        assert "## 技能套件：cp5/SKILL.md" in 系統提示
        assert "固定提示\n\n## 技能套件：cp4/SKILL.md" not in 系統提示
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT endpoint_id,service_account_id,endpoint_version_id,sequence_number "
            "FROM published_session_turn_pairs ORDER BY endpoint_id,sequence_number"
        ).fetchall() == [
            ("ep-1", "sa-1", "ver-1", 1), ("ep-1", "sa-1", "ver-2", 2),
            ("ep-2", "sa-2", "ver-foreign", 1),
        ]
