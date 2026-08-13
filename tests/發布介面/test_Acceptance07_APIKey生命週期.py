"""Acceptance 07 API Key canonical HTTP、SQLite 與 restart 生命週期驗收。"""

from __future__ import annotations

import sqlite3
import time
import json

from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.設定 import 生產設定, 網頁CSRFHeader名稱
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.儲存庫 import 套件收據儲存庫
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照
from 繁中代理.發布介面.憑證.服務 import SQLite憑證驗證服務
from 繁中代理.發布介面.憑證.管理 import SQLite憑證管理服務
from 繁中代理.發布介面.路由.憑證管理 import 建立憑證管理路由器
from 繁中代理.發布介面.路由.外部呼叫 import 建立外部呼叫路由
from tests.發布介面.test_Acceptance04_端點建立Live import (
    _建立正式應用程式, _建立Owner技能與使用者, _登入Owner,
    _建立Server草稿, _建立Endpoint, _記錄假模型,
)


class _模型:
    """回傳固定合法輸出的 production provider test double。"""

    def 產生發布回應(self, **_參數):
        """回傳符合 endpoint response schema 的結果。"""
        return 模型回應快照('{"answer":"A07"}', "stop", {"total_tokens": 1}, [])


def _正規(值) -> str:
    """輸出 production schema 使用的 canonical JSON。"""
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _建立設定(tmp_path):
    """建立 restart 可重用且 keyring 固定的 explicit production settings。"""
    web = 生產設定(
        tmp_path / "web.sqlite3", ("http://localhost:5173",), "fake", "fake", None, None,
        Cookie安全=False, 工作階段有效秒數=60,
    )
    模型 = _模型()

    def 安裝(工具庫) -> None:
        """安裝 v1 snapshot 釘選的固定工具。"""
        工具庫.登錄發布(工具發布描述("release-a07", (工具發布註冊(
            "rev-a07", 工具定義(
                "lookup", "fixed lookup",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _參數: {"ok": True},
            ),
        ),)))

    published = Published生產設定(
        tmp_path / "published.sqlite3", tmp_path / "bundles",
        安裝, lambda: {"fake": 模型},
        憑證封套工廠=lambda: AESGCM憑證封套({1: b"A" * 32}, 1),
    )
    return web, published


def _建立擁有者(web路徑) -> str:
    """建立可由 canonical login 驗證的真 Web owner。"""
    使用者們 = 使用者庫(web路徑)
    try:
        return str(使用者們.建立使用者("alice", "correct horse", roles=["user"])["id"])
    finally:
        使用者們.連線.close()


def _登入(client: TestClient) -> str:
    """登入並回傳目前 single-use CSRF token。"""
    回應 = client.post("/api/auth/login", json={"username": "alice", "password": "correct horse"})
    assert 回應.status_code == 200
    return 回應.json()["csrf_token"]


def _建立端點圖形(資料庫, owner: str) -> None:
    """建立可真實 invoke 的 endpoint/version/bundle authority 與 wrong-endpoint 對照。"""
    根 = 資料庫.parent / "source-a07"
    根.mkdir()
    (根 / "SKILL.md").write_text("# A07", encoding="utf-8")
    收據 = 技能套件發布器(資料庫.parent / "bundles").發布(
        套件識別碼="bundle-a07", 端點識別碼="endpoint-a07", 端點版本識別碼="version-a07",
        版本號碼=1, 建立時間=1.0, 建立者識別碼=owner, 技能表={"a07": 根},
    )
    清單 = (收據.路徑 / "manifest.json").read_text(encoding="utf-8")
    其他根 = 資料庫.parent / "source-other"
    其他根.mkdir()
    (其他根 / "SKILL.md").write_text("# Other", encoding="utf-8")
    其他收據 = 技能套件發布器(資料庫.parent / "bundles").發布(
        套件識別碼="bundle-other", 端點識別碼="endpoint-other",
        端點版本識別碼="version-other", 版本號碼=1, 建立時間=1.0,
        建立者識別碼=owner, 技能表={"other": 其他根},
    )
    其他清單 = (其他收據.路徑 / "manifest.json").read_text(encoding="utf-8")
    工具綱要 = {"type": "object", "properties": {}, "additionalProperties": False}
    工具快照 = {"lookup": {"revision": "rev-a07", "description": "fixed lookup", "parameters": 工具綱要}}
    模型設定 = {"provider": "fake", "model": "fake", "temperature": 0.0,
                "max_tokens": 20, "timeout_seconds": 3.0,
                "structured_output": True, "schema_retry_count": 1}
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-a07',1,NULL)")
        連線.execute("INSERT INTO service_accounts VALUES('sa-other',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("endpoint-a07", owner, "sa-a07", "a07", "active", "version-a07", 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("endpoint-other", owner, "sa-other", "other", "active", "version-other", 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("version-a07", "endpoint-a07", 1, "A07", "fixed prompt", "[]", _正規(["lookup"]),
             _正規(工具快照), "release-a07", _正規(模型設定), "{}", 清單,
             _正規({"type": "object", "required": ["question"]}),
             _正規({"type": "object", "required": ["answer"]}), 0, owner, 1),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("version-other", "endpoint-other", 1, "Other", "fixed prompt", "[]", _正規(["lookup"]),
             _正規(工具快照), "release-a07", _正規(模型設定), "{}", 其他清單,
             _正規({"type": "object", "required": ["question"]}),
             _正規({"type": "object", "required": ["answer"]}), 0, owner, 1),
        )
        套件收據儲存庫(連線).新增(版本識別碼="version-a07", 收據=收據, 發布時間=2.0)
        套件收據儲存庫(連線).新增(
            版本識別碼="version-other", 收據=其他收據, 發布時間=2.0,
        )


def _invoke(client: TestClient, slug: str, key: str):
    """經 canonical public route 執行一次 endpoint invocation。"""
    return client.post(
        f"/v1/endpoints/{slug}/invoke", json={"input": {"question": "A07"}},
        headers={"Authorization": f"Bearer {key}"},
    )


class _可變時鐘:
    """提供canonical credential各層共用的deterministic clock。

    描述：測試可在不重建app下移動目前時間。
    參數：``目前``為初始Unix timestamp。
    返回值：callable clock instance。
    """

    def __init__(self, 目前: float) -> None:
        """保存初始時間。

        參數：``目前``為有限、非負測試timestamp。
        返回值：無；建立可變clock state。
        """
        self.目前 = 目前

    def __call__(self) -> float:
        """讀取目前測試時間。

        參數：無。
        返回值：目前Unix timestamp。
        """
        return self.目前


def test_initial_publication與additional_key使用決定性clock驗證exact邊界(tmp_path, monkeypatch) -> None:
    """由canonical publication取得initial key並驗證expiry/inactivity exact boundaries。

    描述：走真session、single-use CSRF、Draft、Endpoint Create、credential HTTP與invoke。
    參數：``tmp_path``隔離正式DB／bundle；``monkeypatch``只替換既有clock defaults。
    返回值：無；initial/additional key與T-1/T、179d/180d assertions必須通過。
    """
    現在 = _可變時鐘(2_000_000_000.0)
    monkeypatch.setitem(SQLite憑證驗證服務.__init__.__kwdefaults__, "clock", 現在)
    monkeypatch.setitem(SQLite憑證管理服務.__init__.__kwdefaults__, "時鐘", 現在)
    monkeypatch.setitem(建立憑證管理路由器.__kwdefaults__, "時鐘", 現在)
    monkeypatch.setitem(建立外部呼叫路由.__kwdefaults__, "時鐘", 現在)
    app = _建立正式應用程式(
        tmp_path, 模型表工廠=lambda: {"fake": _記錄假模型()},
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(client, "alice", "correct horse")
        草稿 = _建立Server草稿(client, csrf)
        assert 草稿.status_code == 201
        發布 = _建立Endpoint(client, 草稿.headers[網頁CSRFHeader名稱], 草稿.json())
        assert 發布.status_code == 201
        endpoint_id = 發布.json()["endpoint_id"]
        initial_key = 發布.json().pop("initial_api_key")
        csrf = 發布.headers[網頁CSRFHeader名稱]
        建立 = client.post(
            f"/api/published-endpoints/{endpoint_id}/credentials",
            headers={網頁CSRFHeader名稱: csrf},
            json={
                "name": "boundary", "purpose": "exact lifecycle boundary",
                "expires_at": 現在.目前 + 365 * 86_400,
                "ip_allowlist": [], "rate_limit_requests": 60,
            },
        )
        assert 建立.status_code == 201
        additional_key = 建立.json().pop("initial_api_key")
        additional_id = 建立.json()["credential_id"]
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            initial_id = 連線.execute(
                "SELECT id FROM endpoint_credentials WHERE endpoint_id=? AND id<>?",
                (endpoint_id, additional_id),
            ).fetchone()[0]
            到期邊界 = 現在.目前 + 1000
            連線.execute(
                "UPDATE endpoint_credentials SET created_at=?,updated_at=?,last_used_at=NULL,expires_at=? WHERE id=?",
                (到期邊界 - 100, 到期邊界 - 100, 到期邊界, initial_id),
            )
        現在.目前 = 到期邊界 - 1
        到期前 = _invoke(client, "demo-api", initial_key)
        assert 到期前.status_code == 200, {
            "error": 到期前.json().get("error"), "warnings": 到期前.json().get("warnings"),
        }
        現在.目前 = 到期邊界
        過期 = _invoke(client, "demo-api", initial_key)
        assert 過期.status_code == 401 and 過期.json()["error"]["code"] == "api_key_expired"

        閒置邊界 = 2_100_000_000.0
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            連線.execute(
                "UPDATE endpoint_credentials SET created_at=?,last_used_at=?,updated_at=?,expires_at=? WHERE id=?",
                (閒置邊界 - 200 * 86_400, 閒置邊界 - 179 * 86_400, 閒置邊界,
                 閒置邊界 + 365 * 86_400, additional_id),
            )
        現在.目前 = 閒置邊界
        assert _invoke(client, "demo-api", additional_key).status_code == 200
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            連線.execute(
                "UPDATE endpoint_credentials SET last_used_at=? WHERE id=?",
                (閒置邊界 - 180 * 86_400, additional_id),
            )
        閒置 = _invoke(client, "demo-api", additional_key)
        assert 閒置.status_code == 401 and 閒置.json()["error"]["code"] == "invalid_api_key"


def test_create_multi_key_expire_inactive_revoke與restart_readback(tmp_path) -> None:
    """經真 cookie/CSRF/canonical app 證明安全摘要與 lifecycle durability。"""
    web, published = _建立設定(tmp_path)
    published.技能套件發布根.mkdir()
    owner = _建立擁有者(web.資料庫路徑)
    app = 建立CP4ASGI應用程式(web, published)
    now = time.time()
    additional_key = None

    with TestClient(app, raise_server_exceptions=False) as client:
        _建立端點圖形(published.發布資料庫路徑, owner)
        initial = SQLite憑證儲存庫(
            published.發布資料庫路徑, AESGCM憑證封套({1: b"A" * 32}, 1),
            clock=lambda: now, id_factory=lambda: "credential-initial",
        ).建立管理憑證(
            "endpoint-a07", WebOwnerPrincipal(owner), name="initial", purpose="initial integration",
            expires_at=now + 86_400, ip_allowlist=(), rate_limit_requests=60,
        )
        csrf = _登入(client)
        建立 = client.post(
            "/api/published-endpoints/endpoint-a07/credentials",
            headers={網頁CSRFHeader名稱: csrf},
            json={
                "name": "additional", "purpose": "partner integration",
                "expires_at": now + 172_800, "ip_allowlist": [], "rate_limit_requests": 60,
            },
        )
        assert 建立.status_code == 201
        additional_key = 建立.json().pop("initial_api_key")
        assert type(additional_key) is str and additional_key != initial.api_key
        csrf = 建立.headers[網頁CSRFHeader名稱]

        assert _invoke(client, "a07", initial.api_key).status_code == 200
        assert _invoke(client, "a07", additional_key).status_code == 200
        wrong = _invoke(client, "other", additional_key)
        assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_api_key"

        列表 = client.get("/api/published-endpoints/endpoint-a07/credentials")
        assert 列表.status_code == 200 and "initial_api_key" not in 列表.text
        assert {項目["credential_id"] for 項目 in 列表.json()["items"]} == {
            "credential-initial", 建立.json()["credential_id"],
        }

        with sqlite3.connect(published.發布資料庫路徑) as 連線:
            連線.execute(
                "UPDATE endpoint_credentials SET created_at=?,expires_at=? "
                "WHERE id='credential-initial'",
                (now - 172_800, now - 1),
            )
        過期列表 = client.get("/api/published-endpoints/endpoint-a07/credentials").json()["items"]
        assert {項目["credential_id"]: 項目["status"] for 項目 in 過期列表}["credential-initial"] == "expired"
        expired = _invoke(client, "a07", initial.api_key)
        assert expired.status_code == 401 and expired.json()["error"]["code"] == "api_key_expired"

        credential_id = 建立.json()["credential_id"]
        with sqlite3.connect(published.發布資料庫路徑) as 連線:
            連線.execute(
                "UPDATE endpoint_credentials SET created_at=?,last_used_at=?,updated_at=? WHERE id=?",
                (time.time() - 200 * 86_400, time.time() - 179 * 86_400, time.time(), credential_id),
            )
        assert _invoke(client, "a07", additional_key).status_code == 200
        with sqlite3.connect(published.發布資料庫路徑) as 連線:
            連線.execute(
                "UPDATE endpoint_credentials SET last_used_at=? WHERE id=?",
                (time.time() - 180 * 86_400 - 1, credential_id),
            )
        inactive = _invoke(client, "a07", additional_key)
        assert inactive.status_code == 401 and inactive.json()["error"]["code"] == "invalid_api_key"

        撤銷 = client.post(
            f"/api/published-endpoints/endpoint-a07/credentials/{credential_id}/revoke",
            headers={網頁CSRFHeader名稱: csrf}, content=b"",
        )
        assert 撤銷.status_code == 204 and 撤銷.content == b""
        csrf = 撤銷.headers[網頁CSRFHeader名稱]
        重複撤銷 = client.post(
            f"/api/published-endpoints/endpoint-a07/credentials/{credential_id}/revoke",
            headers={網頁CSRFHeader名稱: csrf}, content=b"",
        )
        assert 重複撤銷.status_code == 204 and 重複撤銷.content == b""
        revoked = _invoke(client, "a07", additional_key)
        assert revoked.status_code == 401 and revoked.json()["error"]["code"] == "invalid_api_key"

    restarted = 建立CP4ASGI應用程式(web, published)
    with TestClient(restarted, raise_server_exceptions=False) as client:
        _登入(client)
        items = client.get("/api/published-endpoints/endpoint-a07/credentials").json()["items"]
        status = {項目["credential_id"]: 項目["status"] for 項目 in items}
        assert status["credential-initial"] == "expired"
        assert status[credential_id] == "revoked"

    assert additional_key is not None
