"""Acceptance #5 SA-1：凍結服務帳戶建立的 canonical HTTP 契約。

本模組只從 ``建立CP4ASGI應用程式`` 觀測公開路由與 OpenAPI seam；
服務帳戶只能是 Endpoint Create 的內部原子副作用，不得成為 client claim 或獨立 CRUD。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.路由.規劃發布 import 發布確認
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.設定 import (
    生產設定,
    網頁CSRFHeader名稱,
    網頁CSRFCookie名稱,
    網頁工作階段Cookie名稱,
)


端點建立路徑 = "/api/published-endpoints"
公開建立欄位 = {
    "endpoint_id",
    "version_id",
    "version_number",
    "status",
    "initial_api_key",
}


def _安裝固定工具(工具發布庫物件, 工廠呼叫: list[str]) -> None:
    """安裝 Planner owner resolver 使用的 deterministic tool release。

    參數：
        工具發布庫物件: canonical startup 建立的 per-app registry。
        工廠呼叫: 記錄 installer exact-once 呼叫。
    返回值：
        無；安裝一個無外部副作用的工具定義。
    """
    工廠呼叫.append("tools")
    工具發布庫物件.登錄發布(工具發布描述(
        "acceptance-release",
        (工具發布註冊(
            "revision-1",
            工具定義(
                "acceptance-tool",
                "Acceptance deterministic tool",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _參數: {"ok": True},
            ),
        ),),
    ))


def _解析綱要(規格: dict[str, Any], 綱要: dict[str, Any]) -> dict[str, Any]:
    """解析 OpenAPI 本地元件參照。

    參數：
        規格: canonical app 產生的完整 OpenAPI 文件。
        綱要: 內嵌綱要或只含本地 ``$ref`` 的綱要。
    返回值：
        可直接檢查的綱要物件。
    """
    if "$ref" not in 綱要:
        return 綱要
    return 規格["components"]["schemas"][綱要["$ref"].rsplit("/", 1)[1]]


def _建立完整管理應用程式(
    暫存目錄: Path,
    工廠呼叫: list[str],
    *,
    模型表工廠=None,
):
    """以 explicit factories 建立完整管理能力，但不啟動 lifespan。

    參數：
        暫存目錄: 提供彼此隔離的 Web DB、Published DB 與 bundle root 路徑。
        工廠呼叫: 若 app construction 錯誤執行 callback，會留下可觀測事件。
        模型表工廠: 可選的 restart provider registry factory；預設使用隔離假物件。
    返回值：
        尚未啟動、但 OpenAPI 應已公開完整管理路由的 canonical app。
    """
    網頁設定 = 生產設定(
        暫存目錄 / "web.sqlite3",
        ("http://localhost:5173",),
        "fake",
        "fake",
        Cookie安全=False,
        工作階段有效秒數=60,
    )
    planner設定 = Planner生產設定(
        "acceptance-release",
        lambda 路徑: 工廠呼叫.append("owner") or 使用者庫(路徑),
        lambda: 工廠呼叫.append("planner") or 決定性假規劃器(),
        3600.0,
    )
    def 建立模型表():
        """記錄 startup exact-once 呼叫並建立本次 provider registry。

        參數：無；使用外層 explicit factory。
        返回值：本次 fresh provider registry。
        """
        工廠呼叫.append("models")
        return {"fake": object()} if 模型表工廠 is None else 模型表工廠()

    published設定 = Published生產設定(
        暫存目錄 / "published.sqlite3",
        暫存目錄 / "bundles",
        lambda 工具庫: _安裝固定工具(工具庫, 工廠呼叫),
        建立模型表,
        Planner設定=planner設定,
        憑證封套工廠=lambda: 工廠呼叫.append("envelope") or AESGCM憑證封套(
            {1: b"A" * 32}, 1,
        ),
    )
    return 建立CP4ASGI應用程式(網頁設定, published設定)


def test_canonical_OpenAPI只有一個endpoint_create且不接受service_account_id(tmp_path):
    """SA-1：Endpoint Create 是唯一 SA 建立入口，且 client／public DTO 都看不到 SA ID。

    參數：
        tmp_path: pytest 提供的隔離絕對路徑。
    返回值：
        無；route inventory、strict request 與 public response 契約皆由 assertion 固定。
    重要副作用：
        只建立 app 與 OpenAPI；不得建立 DB、bundle root 或執行 startup factories。
    """
    工廠呼叫: list[str] = []
    應用程式 = _建立完整管理應用程式(tmp_path, 工廠呼叫)

    符合建立路由 = [
        路由
        for 路由 in 應用程式.routes
        if isinstance(路由, APIRoute) and 路由.path == 端點建立路徑
    ]
    assert len(符合建立路由) == 1
    assert 符合建立路由[0].methods == {"POST"}

    規格 = 應用程式.openapi()
    assert set(規格["paths"][端點建立路徑]) == {"post"}
    assert not any("service-account" in 路徑 for 路徑 in 規格["paths"])

    請求綱要 = _解析綱要(
        規格,
        規格["paths"][端點建立路徑]["post"]["requestBody"]
        ["content"]["application/json"]["schema"],
    )
    assert 請求綱要["additionalProperties"] is False
    assert set(請求綱要["required"]) == {
        "draft_id",
        "slug",
        "configuration_confirmation",
    }
    assert set(請求綱要["properties"]) == set(請求綱要["required"])
    assert {"service_account_id", "owner_user_id", "created_by_user_id", "role"}.isdisjoint(
        請求綱要["properties"]
    )

    回應綱要 = _解析綱要(
        規格,
        規格["paths"][端點建立路徑]["post"]["responses"]["201"]
        ["content"]["application/json"]["schema"],
    )
    assert set(回應綱要["required"]) == 公開建立欄位
    assert set(回應綱要["properties"]) == 公開建立欄位
    assert "service_account_id" not in 回應綱要["properties"]

    assert 工廠呼叫 == []
    assert not (tmp_path / "published.sqlite3").exists()
    assert not (tmp_path / "web.sqlite3").exists()
    assert not (tmp_path / "bundles").exists()


def test_startup重用A3資源並於shutdown撤銷服務帳戶建立authority(tmp_path):
    """SA-2：Create coordinator 重用同一 Draft／Owner／Registry，關閉後固定 fail closed。

    參數：
        tmp_path: 隔離 Web DB、Published DB 與 bundle root。
    返回值：
        無；lifespan identity 與 shutdown authority assertions 皆成立。
    重要副作用：
        啟動並關閉一次 canonical app，建立隔離 SQLite DB；不建立 endpoint 或 SA。
    """
    (tmp_path / "bundles").mkdir()
    工廠呼叫: list[str] = []
    應用程式 = _建立完整管理應用程式(tmp_path, 工廠呼叫)
    捕捉管理代理 = None

    with TestClient(應用程式):
        published資源 = 應用程式.state.發布介面資源[-1]
        planner資源 = published資源.取得Planner資源()
        管理服務 = published資源.取得發布管理服務()
        assert planner資源 is not None and 管理服務 is not None
        assert 管理服務._草稿服務 is planner資源.取得規劃服務()
        assert 管理服務._擁有者解析器 is planner資源.取得擁有者解析器()
        assert planner資源.取得工具發布庫() is published資源._工具庫
        assert 管理服務._套件協調器 is published資源._技能套件協調器
        捕捉管理代理 = published資源._發布管理代理

    assert 工廠呼叫 == ["tools", "models", "owner", "planner", "envelope"]
    assert 捕捉管理代理 is not None
    with pytest.raises(RuntimeError, match="發布管理服務不可用"):
        捕捉管理代理.原子發布(
            擁有者使用者識別碼="owner",
            確認=發布確認("draft", "safe-api", {}),
        )


def _建立Owner(暫存目錄: Path, 帳號: str, 密碼: str) -> str:
    """建立具固定技能與工具權限的真 Web owner。

    參數：暫存目錄定位技能及 Web DB；帳號與密碼供 canonical login。
    返回值：權威使用者識別碼。
    """
    技能目錄 = 暫存目錄 / "skills" / "demo"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text(
        "---\nname: demo\ndescription: acceptance skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    使用者儲存庫 = 使用者庫(暫存目錄 / "web.sqlite3")
    try:
        使用者 = 使用者儲存庫.建立使用者(
            帳號,
            密碼,
            roles=["user"],
            enabled_tools=["acceptance-tool"],
            enabled_skills=["demo"],
            skill_roots=[str(暫存目錄 / "skills")],
            allowed_workdirs=[str(暫存目錄)],
        )
        return str(使用者["id"])
    finally:
        使用者儲存庫.連線.close()


def _登入(客戶端: TestClient, 帳號: str, 密碼: str) -> str:
    """經 canonical login 建立真 session。

    參數：canonical client，以及測試 owner 的帳號與密碼。
    返回值：本次 session 的 fresh CSRF token。
    """
    回應 = 客戶端.post("/api/auth/login", json={"username": 帳號, "password": 密碼})
    assert 回應.status_code == 200
    assert 網頁工作階段Cookie名稱 in 客戶端.cookies
    assert 網頁CSRFCookie名稱 in 客戶端.cookies
    return str(回應.json()["csrf_token"])


def _建立草稿(客戶端: TestClient, csrf: str):
    """經 canonical Draft route 建立 server-owned configuration。

    參數：持有真 session 的 client 與尚未使用的 CSRF token。
    返回值：原始 Draft HTTP response。
    """
    return 客戶端.post(
        "/api/published-endpoints/draft",
        json={
            "original_requirement_text": "建立 Demo API",
            "selected_skills": ["demo"],
            "response_mode": "structured",
        },
        headers={網頁CSRFHeader名稱: csrf},
    )


def _建立確認(預覽: dict[str, Any]) -> dict[str, Any]:
    """只從 server preview 建立 route 允許的五個確認欄位。

    參數：Draft 201 回傳的 server-owned preview。
    返回值：與 preview 脫離的五鍵 configuration confirmation。
    """
    return json.loads(json.dumps({
        "system_prompt": 預覽["system_prompt"],
        "input_schema": 預覽["input_schema"],
        "response_schema": 預覽["response_schema"],
        "human_docs": 預覽["human_docs"],
        "rate_limit": 預覽["rate_limit"],
    }))


def _送出建立(客戶端: TestClient, csrf: str, 草稿: dict[str, Any], slug: str, **額外欄位):
    """經 canonical Create route 送出本文。

    參數：真 session client、fresh CSRF、server Draft、slug，以及負向案例額外欄位。
    返回值：原始 Endpoint Create HTTP response。
    """
    本文 = {
        "draft_id": 草稿["draft_id"],
        "slug": slug,
        "configuration_confirmation": _建立確認(草稿["preview"]),
        **額外欄位,
    }
    return 客戶端.post(
        端點建立路徑,
        json=本文,
        headers={網頁CSRFHeader名稱: csrf},
    )


def test_live登入草稿建立兩端點並產生不同服務帳戶且拒絕client_claim(tmp_path, caplog):
    """以真 HTTP 建立完整 SQLite 圖形並拒絕敵對 claim。

    參數：``tmp_path`` 隔離持久層；``caplog`` 觀測 secret absence。
    返回值：無；第二端點使用不同 SA、完整 graph 與秘密邊界由 assertions 固定。
    """
    (tmp_path / "bundles").mkdir()
    應用程式 = _建立完整管理應用程式(tmp_path, [])

    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner(tmp_path, "alice", "correct horse")
        csrf = _登入(客戶端, "alice", "correct horse")
        草稿回應 = _建立草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        草稿 = 草稿回應.json()

        偽造 = _送出建立(
            客戶端,
            草稿回應.headers[網頁CSRFHeader名稱],
            草稿,
            "forged-api",
            service_account_id="client-sa",
        )
        assert 偽造.status_code == 422
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            assert 連線.execute("SELECT COUNT(*) FROM service_accounts").fetchone()[0] == 0

        csrf = _登入(客戶端, "alice", "correct horse")
        建立一 = _送出建立(客戶端, csrf, 草稿, "first-api")
        assert 建立一.status_code == 201
        本文一 = 建立一.json()
        assert set(本文一) == 公開建立欄位
        assert "service_account_id" not in 本文一
        初始金鑰 = 本文一["initial_api_key"]
        assert type(初始金鑰) is str and 初始金鑰

        csrf = _登入(客戶端, "alice", "correct horse")
        草稿二回應 = _建立草稿(客戶端, csrf)
        assert 草稿二回應.status_code == 201
        建立二 = _送出建立(
            客戶端,
            草稿二回應.headers[網頁CSRFHeader名稱],
            草稿二回應.json(),
            "second-api",
        )
        assert 建立二.status_code == 201

        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            連線.row_factory = sqlite3.Row
            端點 = [dict(列) for 列 in 連線.execute(
                "SELECT id, owner_user_id, service_account_id, current_version_id "
                "FROM published_endpoints ORDER BY slug"
            )]
            服務帳戶 = [列[0] for 列 in 連線.execute("SELECT id FROM service_accounts ORDER BY id")]
            assert len(端點) == len(服務帳戶) == 2
            assert {列["owner_user_id"] for 列 in 端點} == {擁有者}
            assert len({列["service_account_id"] for 列 in 端點}) == 2
            assert {列["service_account_id"] for 列 in 端點} == set(服務帳戶)
            for 表 in (
                "published_endpoint_versions",
                "endpoint_credentials",
                "published_skill_bundles",
                "published_draft_consumptions",
                "published_endpoint_version_metadata",
                "audit_events",
            ):
                assert 連線.execute(f'SELECT COUNT(*) FROM "{表}"').fetchone()[0] == 2

        金鑰位元 = 初始金鑰.encode()
        assert 金鑰位元 not in (tmp_path / "published.sqlite3").read_bytes()
        assert all(
            金鑰位元 not in 路徑.read_bytes()
            for 路徑 in (tmp_path / "bundles").rglob("*")
            if 路徑.is_file()
        )
        assert all(初始金鑰 not in 紀錄.getMessage() for 紀錄 in caplog.records)
        初始金鑰 = "[REDACTED]"


_圖形寫入前綴 = (
    "INSERT INTO service_accounts",
    "INSERT INTO published_endpoints",
    "INSERT INTO published_endpoint_versions",
    "INSERT INTO published_draft_consumptions",
    "INSERT INTO published_endpoint_version_metadata",
    "INSERT INTO endpoint_credentials",
    "INSERT INTO published_skill_bundles",
    "INSERT INTO audit_events",
    "UPDATE published_endpoints SET current_version_id",
    "COMMIT",
)


@pytest.mark.parametrize("失敗前綴", _圖形寫入前綴)
def test_live_HTTP每個交易寫入與commit失敗皆零孤立服務帳戶(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    失敗前綴: str,
):
    """從 canonical HTTP 在每個 P04 mutation／commit seam 注入失敗。

    參數：``tmp_path`` 隔離資源；``monkeypatch`` 替換本次服務的連線工廠；
        ``失敗前綴`` 選擇 exact SQL seam。
    返回值：無；固定 500、八表零 graph、零孤立 SA 與錯誤遮罩由 assertions 固定。
    """
    (tmp_path / "bundles").mkdir()
    應用程式 = _建立完整管理應用程式(tmp_path, [])

    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        _建立Owner(tmp_path, "alice", "correct horse")
        csrf = _登入(客戶端, "alice", "correct horse")
        草稿 = _建立草稿(客戶端, csrf)
        assert 草稿.status_code == 201
        管理服務 = 應用程式.state.發布介面資源[-1].取得發布管理服務()

        class 精確失敗連線(sqlite3.Connection):
            """保留真 SQLite semantics，並在指定 mutation 前拋固定錯誤。"""

            def execute(self, sql, parameters=()):
                """只攔截本案例 exact SQL prefix，其餘委派 SQLite 基底實作。"""
                if sql.startswith(失敗前綴):
                    raise sqlite3.OperationalError("[REDACTED]")
                return super().execute(sql, parameters)

        def 建立失敗連線(*參數, **選項):
            """建立使用 exact failure subclass 的正式 SQLite 連線。"""
            return sqlite3.connect(*參數, **選項, factory=精確失敗連線)

        monkeypatch.setattr(管理服務._端點發布服務, "_連線工廠", 建立失敗連線)
        回應 = _送出建立(
            客戶端,
            草稿.headers[網頁CSRFHeader名稱],
            草稿.json(),
            "fail-api",
        )

        assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
        assert str(tmp_path) not in 回應.text
        assert "service_account" not in 回應.text
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            for 表 in (
                "service_accounts",
                "published_endpoints",
                "published_endpoint_versions",
                "published_draft_consumptions",
                "published_endpoint_version_metadata",
                "endpoint_credentials",
                "published_skill_bundles",
                "audit_events",
            ):
                assert 連線.execute(f'SELECT COUNT(*) FROM "{表}"').fetchone() == (0,)


def test_相同slug兩個canonical_writer恰一winner且無多餘服務帳戶(tmp_path):
    """以兩個真 session 並行建立相同 slug，固定一個 winner 與單一 durable graph。

    參數：``tmp_path`` 隔離兩個 writer 共用的 canonical app、DB 與 bundle root。
    返回值：無；201/409、八表單一 graph 與單一 SA 由 assertions 固定。
    """
    (tmp_path / "bundles").mkdir()
    應用程式 = _建立完整管理應用程式(tmp_path, [])

    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        _建立Owner(tmp_path, "alice", "correct horse")
        csrf一 = _登入(客戶端, "alice", "correct horse")
        草稿一 = _建立草稿(客戶端, csrf一)
        assert 草稿一.status_code == 201
        cookie一 = dict(客戶端.cookies)
        客戶端.cookies.clear()

        csrf二 = _登入(客戶端, "alice", "correct horse")
        草稿二 = _建立草稿(客戶端, csrf二)
        assert 草稿二.status_code == 201
        cookie二 = dict(客戶端.cookies)

        def 建立(輸入):
            """以隔離 session cookie 與 successor CSRF 送出一個 canonical Create。"""
            cookie, 草稿 = 輸入
            return 客戶端.post(
                端點建立路徑,
                json={
                    "draft_id": 草稿.json()["draft_id"],
                    "slug": "same-api",
                    "configuration_confirmation": _建立確認(草稿.json()["preview"]),
                },
                headers={網頁CSRFHeader名稱: 草稿.headers[網頁CSRFHeader名稱]},
                cookies=cookie,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as 執行器:
            回應們 = list(執行器.map(建立, ((cookie一, 草稿一), (cookie二, 草稿二))))

        狀態與本文 = [(回應.status_code, 回應.json()) for 回應 in 回應們]
        assert sorted(回應.status_code for 回應 in 回應們) == [201, 409], 狀態與本文
        for 回應 in 回應們:
            assert "service_account_id" not in 回應.text
            assert str(tmp_path) not in 回應.text
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            for 表 in (
                "service_accounts",
                "published_endpoints",
                "published_endpoint_versions",
                "published_draft_consumptions",
                "published_endpoint_version_metadata",
                "endpoint_credentials",
                "published_skill_bundles",
                "audit_events",
            ):
                assert 連線.execute(f'SELECT COUNT(*) FROM "{表}"').fetchone() == (1,)


class _記錄假模型:
    """記錄 restart invocation 的 detached provider 參數。"""

    def __init__(self) -> None:
        """建立空呼叫紀錄。

        參數：無。
        返回值：None。
        """
        self.呼叫: list[dict[str, Any]] = []

    def 產生發布回應(self, **參數):
        """保存 JSON detached 參數並建立 deterministic 結果。

        參數：Published runtime 傳入的 provider keyword arguments。
        返回值：符合 structured schema 的模型回應快照。
        """
        self.呼叫.append(json.loads(json.dumps(參數)))
        return 模型回應快照(
            text='{"result":"restart-ok"}',
            finish_reason="stop",
            usage={"total_tokens": 1},
            tool_calls=[],
        )


_FRESH_PROCESS_INVOKE_SCRIPT = r'''
import json
import os
import runpy
from pathlib import Path

from fastapi.testclient import TestClient
import 繁中代理.發布介面.執行期.執行器 as 執行器模組

repo = Path(os.environ["A5_REPO"])
tmp = Path(os.environ["A5_TMP"])
api_key = os.environ.pop("A5_API_KEY")
expected_sa = os.environ["A5_EXPECTED_SA"]
expected_version = os.environ["A5_EXPECTED_VERSION"]
helpers = runpy.run_path(str(repo / "tests/發布介面/test_Acceptance05_服務帳戶建立Live.py"))
model = helpers["_記錄假模型"]()
app = helpers["_建立完整管理應用程式"](
    tmp, [], 模型表工廠=lambda: {"fake": model},
)
captured = []
original_loader = 執行器模組.載入服務帳戶上下文或失敗關閉

def capture_context(*args, **kwargs):
    context = original_loader(*args, **kwargs)
    captured.append((context.service_account_id, context.endpoint_version_id))
    return context

執行器模組.載入服務帳戶上下文或失敗關閉 = capture_context
with TestClient(app, raise_server_exceptions=False) as client:
    response = client.post(
        "/v1/endpoints/restart-api/invoke",
        json={"input": {"question": "restart"}},
        headers={"Authorization": f"Bearer {api_key}"},
    )
provider_text = json.dumps(model.呼叫[0], ensure_ascii=False, sort_keys=True) if model.呼叫 else ""
print(json.dumps({
    "status": response.status_code,
    "data": response.json().get("data"),
    "context": captured,
    "context_matches": captured == [(expected_sa, expected_version)],
    "model_calls": len(model.呼叫),
    "bundle_snapshot_present": "# Demo" in provider_text,
    "owner_data_absent": "correct horse" not in provider_text and str(tmp / "skills") not in provider_text,
}, ensure_ascii=False, sort_keys=True))
api_key = "[REDACTED]"
'''


def test_restart後由exact服務帳戶與v1快照完成invoke且不讀live_skill(tmp_path):
    """證明 fresh process invoke 只使用 exact Published snapshot。

    參數：``tmp_path`` 供父程序與 fresh child process 共用 durable DB 與 bundle root。
    返回值：無；Create→shutdown→刪除 owner skill→restart invoke 全由 assertions 固定。
    """
    (tmp_path / "bundles").mkdir()
    第一應用 = _建立完整管理應用程式(tmp_path, [])
    初始金鑰 = None

    with TestClient(第一應用, raise_server_exceptions=False) as 客戶端:
        _建立Owner(tmp_path, "alice", "correct horse")
        csrf = _登入(客戶端, "alice", "correct horse")
        草稿 = _建立草稿(客戶端, csrf)
        assert 草稿.status_code == 201
        建立 = _送出建立(
            客戶端,
            草稿.headers[網頁CSRFHeader名稱],
            草稿.json(),
            "restart-api",
        )
        assert 建立.status_code == 201
        初始金鑰 = 建立.json()["initial_api_key"]

    with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
        端點識別碼, 服務帳戶識別碼, 版本識別碼 = 連線.execute(
            "SELECT id,service_account_id,current_version_id FROM published_endpoints "
            "WHERE slug='restart-api'"
        ).fetchone()
        assert 連線.execute(
            "SELECT COUNT(*) FROM service_accounts WHERE id=?",
            (服務帳戶識別碼,),
        ).fetchone() == (1,)
        assert 連線.execute(
            "SELECT endpoint_id FROM published_endpoint_versions WHERE id=?",
            (版本識別碼,),
        ).fetchone() == (端點識別碼,)

    (tmp_path / "skills" / "demo" / "SKILL.md").unlink()
    child環境 = os.environ.copy()
    child環境.update({
        "A5_REPO": str(Path(__file__).resolve().parents[2]),
        "A5_TMP": str(tmp_path),
        "A5_API_KEY": 初始金鑰,
        "A5_EXPECTED_SA": 服務帳戶識別碼,
        "A5_EXPECTED_VERSION": 版本識別碼,
    })
    child結果 = subprocess.run(
        [sys.executable, "-I", "-c", _FRESH_PROCESS_INVOKE_SCRIPT],
        cwd=Path(__file__).resolve().parents[2],
        env=child環境,
        text=True,
        capture_output=True,
        check=False,
    )
    assert 初始金鑰 not in child結果.stdout
    assert 初始金鑰 not in child結果.stderr
    child環境["A5_API_KEY"] = "[REDACTED]"
    assert child結果.returncode == 0, "[REDACTED]"
    child證據 = json.loads(child結果.stdout)
    assert child證據 == {
        "bundle_snapshot_present": True,
        "context": [[服務帳戶識別碼, 版本識別碼]],
        "context_matches": True,
        "data": {"result": "restart-ok"},
        "model_calls": 1,
        "owner_data_absent": True,
        "status": 200,
    }
    初始金鑰 = "[REDACTED]"
