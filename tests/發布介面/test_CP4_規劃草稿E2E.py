"""A3-05：Canonical Production App 的規劃草稿 Live Product E2E。

前四張卡分別證明零件與安全邊界；本檔案改由真正的 canonical composition
（``生產Controller建構器`` 搭配 ``建立生產應用程式``，即 ``建立CP4ASGI應用程式`` 的內部組裝）
啟動完整應用，走真 Login、Cookie、單次 CSRF、Owner Authority 與 Planner Composition
建立草稿，並證明草稿不可 Invoke、跨擁有者不可存取，且完全沒有發布副作用。

本檔案刻意不建立任何手工小型 Test App：所有 HTTP 請求都打 canonical app 實際掛載的路由。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.生產Published執行 import Published生產設定, 生產Controller建構器
from 繁中代理.發布介面.生產Published管理 import (
    Planner生產設定, 延遲草稿規劃服務, 草稿規劃服務不可用,
)
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式
from 繁中代理.發布介面.規劃.綱要 import 規劃服務, 草稿不可執行錯誤, 草稿存取錯誤
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.規劃.規劃器契約 import 文字回應結構
from 繁中代理.發布介面.設定 import (
    生產設定, 網頁CSRFHeader名稱, 網頁工作階段Cookie名稱, 網頁CSRFCookie名稱,
)

_草稿路徑 = "/api/published-endpoints/draft"
_技能名稱 = "alpha"
_帳號 = "owner"
_密碼 = "correct horse battery"
_帳號二 = "owner-two"
_密碼二 = "another correct battery"
_發布識別 = "release-1"
_草稿存續秒數 = 3600.0

_回應頂層鍵 = {"draft_id", "expires_at", "preview"}
_預覽鍵 = {
    "endpoint_name", "suggested_slug", "behavior_summary", "selected_skills",
    "recommended_tools", "tool_capabilities", "system_prompt", "input_schema",
    "response_schema", "human_docs", "rate_limit", "warnings",
}
_結構化回應結構 = {
    "type": "object", "properties": {"result": {"type": "string"}},
    "required": ["result"], "additionalProperties": False,
}

_發布副作用資料表 = {
    "published_endpoints",
    "published_endpoint_versions",
    "endpoint_credentials",
    "published_skill_bundles",
    "service_accounts",
    "endpoint_invocations",
    "run_events",
    "endpoint_tool_calls",
    "published_draft_consumptions",
    "published_endpoint_version_metadata",
}


# ---------------------------------------------------------------------------
# Canonical composition helpers
# ---------------------------------------------------------------------------


def _工具發布描述() -> 工具發布描述:
    """建立 startup 安裝的固定 pinned release。"""
    return 工具發布描述(_發布識別, (工具發布註冊("revision-1", 工具定義(
        "alpha-tool", "Alpha tool",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _參數: "ok",
    )),))


def _建立環境(tmp_path: Path) -> dict:
    """建立技能來源、兩位真實使用者，並回傳可重複啟動的 canonical app 工廠。"""
    tmp_path = tmp_path.resolve()
    技能根 = tmp_path / "skills"
    (技能根 / _技能名稱).mkdir(parents=True)
    (技能根 / _技能名稱 / "SKILL.md").write_text(
        f"---\nname: {_技能名稱}\ndescription: {_技能名稱} skill\n---\n# {_技能名稱}\n",
        encoding="utf-8",
    )
    網頁資料庫 = tmp_path / "web.sqlite3"
    使用者庫物件 = 使用者庫(網頁資料庫)
    for 帳號, 密碼, 角色 in ((_帳號, _密碼, "admin"), (_帳號二, _密碼二, "member")):
        使用者庫物件.建立使用者(
            帳號, 密碼, roles=[角色], enabled_tools=["alpha-tool"],
            enabled_skills=[_技能名稱], skill_roots=[str(技能根)],
            allowed_workdirs=[str(技能根)],
        )
    使用者庫物件.連線.close()

    套件根 = tmp_path / "bundles"
    套件根.mkdir()
    網頁設定 = 生產設定(
        網頁資料庫, ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=3600,
    )
    發布設定 = Published生產設定(
        tmp_path / "published.sqlite3", 套件根,
        lambda 儲存庫: 儲存庫.登錄發布(_工具發布描述()),
        lambda: {"fake": object()}, 60.0,
        Planner設定=Planner生產設定(
            _發布識別, 使用者庫, lambda: 決定性假規劃器(), _草稿存續秒數,
        ),
    )

    def 建立應用():
        """以 canonical 生產組裝建立一次完整 app，並交出同一個 per-app proxy。"""
        建構器 = 生產Controller建構器(發布設定)
        應用 = 建立生產應用程式(網頁設定, 建構器)
        return 應用, 建構器._Published.取得草稿規劃代理()

    return {
        "建立應用": 建立應用, "網頁設定": 網頁設定, "發布設定": 發布設定,
        "技能根": 技能根, "套件根": 套件根,
        "網頁資料庫": 網頁資料庫, "發布資料庫": tmp_path / "published.sqlite3",
    }


def _登入(客戶端: TestClient, 帳號: str = _帳號, 密碼: str = _密碼):
    """以真實帳密登入，回傳完整回應與首枚單次 CSRF 權杖。"""
    回應 = 客戶端.post("/api/auth/login", json={"username": 帳號, "password": 密碼})
    assert 回應.status_code == 200, 回應.text
    return 回應, 回應.json()["csrf_token"]


def _本文(回應模式: str) -> dict:
    """建立唯一合法的三鍵 request body。"""
    return {
        "original_requirement_text": "建立 Alpha API",
        "selected_skills": [_技能名稱],
        "response_mode": 回應模式,
    }


def _建立草稿(客戶端: TestClient, csrf: str, 回應模式: str):
    """以 canonical 草稿路由建立一次草稿。"""
    return 客戶端.post(
        _草稿路徑, json=_本文(回應模式), headers={網頁CSRFHeader名稱: csrf},
    )


def _使用者識別碼(環境: dict, 帳號: str) -> str:
    """由真實 Web 使用者庫取得 canonical session principal 識別碼。"""
    使用者庫物件 = 使用者庫(環境["網頁資料庫"])
    try:
        return str(使用者庫物件.讀取使用者(username=帳號)["id"])
    finally:
        使用者庫物件.連線.close()


def _Planner資源(應用):
    """取得 canonical startup 安裝的 Planner 資源。"""
    return 應用.state.發布介面資源[1].取得Planner資源()


def _草稿聚合(應用) -> 規劃服務:
    """取得 canonical startup 建立的唯一 Draft Aggregate。"""
    聚合 = _Planner資源(應用).取得規劃服務()
    assert type(聚合) is 規劃服務
    return 聚合


def _發布面快照(環境: dict) -> dict:
    """完整 readback Published 全部存在表與 Bundle Root tree bytes。"""
    快照 = {
        "bundle_tree": tuple(sorted(
            (str(路徑.relative_to(環境["套件根"])), 路徑.is_dir(),
             None if 路徑.is_dir() else 路徑.read_bytes())
            for 路徑 in 環境["套件根"].rglob("*")
        )),
        "tables": {},
    }
    連線 = sqlite3.connect(環境["發布資料庫"])
    try:
        現有表 = sorted({
            列[0] for 列 in 連線.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        })
        assert _發布副作用資料表 <= set(現有表)
        for 表 in 現有表:
            安全表 = 表.replace('"', '""')
            快照["tables"][表] = tuple(連線.execute(f'SELECT * FROM "{安全表}" ORDER BY rowid'))
    finally:
        連線.close()
    return 快照


def _斷言零發布副作用(環境: dict, 之前: dict) -> None:
    """證明整段 E2E 沒有建立任何 Endpoint／Version／憑證／Bundle／Invocation。"""
    之後 = _發布面快照(環境)
    assert 之後 == 之前
    assert 之後["bundle_tree"] == ()
    assert all(之後["tables"][表] == () for 表 in _發布副作用資料表)


# ---------------------------------------------------------------------------
# 步驟一：以 Fake Planner 啟動完整 Canonical App
# ---------------------------------------------------------------------------


def test_canonical_app以Fake_Planner啟動並公開草稿路由(tmp_path):
    """完整 canonical app 必須啟動成功，且 live OpenAPI 公開 exact 草稿 route。"""
    環境 = _建立環境(tmp_path)
    應用, 代理 = 環境["建立應用"]()

    assert type(代理) is 延遲草稿規劃服務
    assert tuple(應用.openapi()["paths"][_草稿路徑]) == ("post",)

    with TestClient(應用) as 客戶端:
        assert 客戶端.get("/healthz").status_code == 200
        資源 = _Planner資源(應用)
        assert 資源._代理 is 代理
        assert type(資源.取得規劃服務()) is 規劃服務
        assert type(資源.取得工具發布庫()) is 工具發布庫


def test_canonical工廠與生產組裝產生同一草稿路由(tmp_path):
    """``建立CP4ASGI應用程式`` 與本檔案使用的組裝必須公開同一份 route inventory。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()
    工廠應用 = 建立CP4ASGI應用程式(環境["網頁設定"], 環境["發布設定"])

    assert set(工廠應用.openapi()["paths"]) == set(應用.openapi()["paths"])
    assert tuple(工廠應用.openapi()["paths"][_草稿路徑]) == ("post",)


# ---------------------------------------------------------------------------
# 步驟二：真 Login 取得 Cookie 與 Single-use CSRF
# ---------------------------------------------------------------------------


def test_真Login發出工作階段Cookie與單次CSRF(tmp_path):
    """真實帳密登入必須同時發出 session cookie、CSRF cookie 與 CSRF 權杖。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        回應, csrf = _登入(客戶端)

        assert 網頁工作階段Cookie名稱 in 客戶端.cookies
        assert 網頁CSRFCookie名稱 in 客戶端.cookies
        assert 32 <= len(csrf) <= 512
        assert 回應.json()["user"]["username"] == _帳號
        assert 回應.json()["user"]["id"] == _使用者識別碼(環境, _帳號)

        我 = 客戶端.get("/api/auth/me")
        assert 我.status_code == 200
        assert 我.json()["user"]["username"] == _帳號


def test_未登入與錯誤密碼皆無法取得草稿(tmp_path):
    """沒有真 session 就不可能走到 Planner。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        assert _建立草稿(客戶端, "x" * 32, "text").status_code == 401
        錯誤 = 客戶端.post(
            "/api/auth/login", json={"username": _帳號, "password": "wrong"},
        )
        assert 錯誤.status_code == 401
        assert _草稿聚合(應用)._草稿 == {}


# ---------------------------------------------------------------------------
# 步驟三／四：Text 與 Structured 模式各建立一個草稿
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "回應模式, 期望回應結構",
    [("text", 文字回應結構), ("structured", _結構化回應結構)],
)
def test_兩種回應模式各建立一個草稿並回201(tmp_path, 回應模式: str, 期望回應結構: dict):
    """Text 與 Structured 模式都必須回 201、exact 三鍵與 exact 十二鍵預覽。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        回應 = _建立草稿(客戶端, csrf, 回應模式)

    assert 回應.status_code == 201, 回應.text
    本文 = 回應.json()
    assert set(本文) == _回應頂層鍵
    預覽 = 本文["preview"]
    assert set(預覽) == _預覽鍵
    assert 預覽["response_schema"] == 期望回應結構
    assert 預覽["selected_skills"] == [_技能名稱]
    assert 預覽["recommended_tools"] == ["alpha-tool"]
    assert 預覽["rate_limit"] == {"endpoint_per_minute": 60, "credential_per_minute": 30}
    assert type(本文["draft_id"]) is str and 本文["draft_id"]
    assert type(本文["expires_at"]) is float


def test_同一工作階段可連續建立兩種模式草稿(tmp_path):
    """兩種模式在同一 session 下必須各自產生獨立草稿 identity。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        文字回應 = _建立草稿(客戶端, csrf, "text")
        assert 文字回應.status_code == 201, 文字回應.text
        結構回應 = _建立草稿(
            客戶端, 文字回應.headers[網頁CSRFHeader名稱], "structured",
        )
        assert 結構回應.status_code == 201, 結構回應.text

        識別 = {文字回應.json()["draft_id"], 結構回應.json()["draft_id"]}
        assert len(識別) == 2
        assert set(_草稿聚合(應用)._草稿) == 識別


# ---------------------------------------------------------------------------
# 步驟五：Readback 內部 Store — Owner、TTL、Snapshot 與 Generation
# ---------------------------------------------------------------------------


def test_內部Store_readback擁有者_TTL_快照與世代(tmp_path):
    """草稿必須綁 canonical principal、固定 TTL、與回應同一份快照且世代為零。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()
    擁有者識別碼 = _使用者識別碼(環境, _帳號)

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        回應 = _建立草稿(客戶端, csrf, "structured")
        assert 回應.status_code == 201, 回應.text
        本文 = 回應.json()

        聚合 = _草稿聚合(應用)
        草稿 = 聚合.讀取草稿(擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"] - 1)

        assert 草稿.擁有者識別碼 == 擁有者識別碼
        assert 草稿.草稿識別碼 == 本文["draft_id"]
        assert 草稿.到期時間 == 本文["expires_at"]
        assert 草稿.到期時間 - 草稿.建立時間 == _草稿存續秒數
        assert 草稿.綱要 == 本文["preview"]
        assert 草稿.狀態 == "draft"
        assert 草稿._世代 == 0
        assert 草稿.發布確認 is None
        assert 草稿.能力摘要 is not None
        assert [項目.名稱 for 項目 in 草稿.能力摘要.技能] == [_技能名稱]
        assert [項目.名稱 for 項目 in 草稿.能力摘要.工具] == ["alpha-tool"]


def test_草稿在到期後不可讀取(tmp_path):
    """TTL 是硬邊界：到期時間之後同一擁有者也讀不到。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()
    擁有者識別碼 = _使用者識別碼(環境, _帳號)

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        本文 = _建立草稿(客戶端, csrf, "text").json()
        聚合 = _草稿聚合(應用)

        with pytest.raises(草稿存取錯誤):
            聚合.讀取草稿(擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"])


# ---------------------------------------------------------------------------
# 步驟六：第二位 User 不得取得或使用第一位 User 的 Draft
# ---------------------------------------------------------------------------


def test_第二位使用者不得存取第一位使用者草稿(tmp_path):
    """跨擁有者存取必須以不可列舉的固定錯誤拒絕。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()
    擁有者一 = _使用者識別碼(環境, _帳號)
    擁有者二 = _使用者識別碼(環境, _帳號二)
    assert 擁有者一 != 擁有者二

    with TestClient(應用) as 客戶端:
        _, csrf一 = _登入(客戶端)
        草稿一 = _建立草稿(客戶端, csrf一, "structured").json()
        客戶端.post("/api/auth/logout", headers={網頁CSRFHeader名稱: csrf一})

        _, csrf二 = _登入(客戶端, _帳號二, _密碼二)
        草稿二 = _建立草稿(客戶端, csrf二, "structured").json()

        assert 草稿一["draft_id"] != 草稿二["draft_id"]
        聚合 = _草稿聚合(應用)
        現在 = 草稿一["expires_at"] - 1

        with pytest.raises(草稿存取錯誤):
            聚合.讀取草稿(擁有者二, 草稿一["draft_id"], 現在=現在)
        with pytest.raises(草稿存取錯誤):
            聚合.讀取草稿(擁有者一, 草稿二["draft_id"], 現在=現在)
        with pytest.raises(草稿存取錯誤):
            聚合.呼叫草稿(擁有者二, 草稿一["draft_id"], 現在=現在)

        assert 聚合.讀取草稿(擁有者一, 草稿一["draft_id"], 現在=現在).擁有者識別碼 == 擁有者一
        assert 聚合.讀取草稿(擁有者二, 草稿二["draft_id"], 現在=現在).擁有者識別碼 == 擁有者二


def test_canonical_app沒有任何讀取草稿的HTTP路由(tmp_path):
    """草稿只在伺服器端存在；live OpenAPI 不得公開任何草稿讀取或列舉路由。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    路徑們 = 應用.openapi()["paths"]
    assert tuple(路徑們[_草稿路徑]) == ("post",)
    for 路徑, 操作 in 路徑們.items():
        if "draft" in 路徑:
            assert 路徑 == _草稿路徑 and tuple(操作) == ("post",)


# ---------------------------------------------------------------------------
# 步驟七：Draft ID 不可 Invoke
# ---------------------------------------------------------------------------


def test_草稿識別碼不可經由外部呼叫路由執行(tmp_path):
    """以草稿識別碼當 slug 呼叫 invoke 必須不可達，且不留任何呼叫紀錄。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        本文 = _建立草稿(客戶端, csrf, "structured").json()
        草稿識別碼 = 本文["draft_id"]

        無金鑰 = 客戶端.post(
            f"/v1/endpoints/{草稿識別碼}/invoke", json={"input": {}},
        )
        有金鑰 = 客戶端.post(
            f"/v1/endpoints/{草稿識別碼}/invoke", json={"input": {}},
            headers={"X-API-Key": "not-a-real-key"},
        )
        建議短名呼叫 = 客戶端.post(
            f"/v1/endpoints/{本文['preview']['suggested_slug']}/invoke",
            json={"input": {}}, headers={"X-API-Key": "not-a-real-key"},
        )
        不存在短名呼叫 = 客戶端.post(
            "/v1/endpoints/no-such-endpoint/invoke", json={"input": {}},
            headers={"X-API-Key": "not-a-real-key"},
        )

    回應們 = (無金鑰, 有金鑰, 建議短名呼叫, 不存在短名呼叫)
    for 回應 in 回應們:
        assert 回應.status_code == 401, 回應.text
        assert 回應.json()["ok"] is False
        assert 回應.json()["error"]["code"] == "invalid_api_key"
        assert 回應.json()["invocation"] is None
        assert 草稿識別碼 not in 回應.text

    # 草稿識別碼、已存在短名與根本不存在的短名必須逐字節相同，invoke 邊界不可用來列舉草稿。
    assert len({回應.text for 回應 in 回應們}) == 1
    _斷言零發布副作用(環境, _發布面快照(環境))


def test_草稿Aggregate固定拒絕執行草稿(tmp_path):
    """即使是合法擁有者，草稿在 Aggregate 層也永遠不可執行。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()
    擁有者識別碼 = _使用者識別碼(環境, _帳號)

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        本文 = _建立草稿(客戶端, csrf, "structured").json()
        聚合 = _草稿聚合(應用)

        with pytest.raises(草稿不可執行錯誤):
            聚合.呼叫草稿(擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"] - 1)

        assert 聚合.讀取草稿(
            擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"] - 1,
        ).狀態 == "draft"


# ---------------------------------------------------------------------------
# 步驟八：Published DB 與 Bundle Root 前後比較，零發布副作用
# ---------------------------------------------------------------------------


def test_完整E2E流程零發布副作用(tmp_path):
    """成功建立多個草稿後，發布面必須逐列、逐位元組與之前完全相同。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        之前 = _發布面快照(環境)
        _, csrf = _登入(客戶端)
        文字回應 = _建立草稿(客戶端, csrf, "text")
        assert 文字回應.status_code == 201, 文字回應.text
        結構回應 = _建立草稿(
            客戶端, 文字回應.headers[網頁CSRFHeader名稱], "structured",
        )
        assert 結構回應.status_code == 201, 結構回應.text

        assert len(_草稿聚合(應用)._草稿) == 2
        _斷言零發布副作用(環境, 之前)

    _斷言零發布副作用(環境, 之前)


# ---------------------------------------------------------------------------
# 步驟九：CSRF Replay 必須拒絕
# ---------------------------------------------------------------------------


def test_單次CSRF重放被拒且不建立第二份草稿(tmp_path):
    """同一枚 CSRF 權杖用第二次必須 403，並且不得產生副作用。"""
    環境 = _建立環境(tmp_path)
    應用, _ = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        首次 = _建立草稿(客戶端, csrf, "structured")
        assert 首次.status_code == 201, 首次.text
        接續 = 首次.headers[網頁CSRFHeader名稱]
        assert 接續 != csrf

        重放 = _建立草稿(客戶端, csrf, "structured")
        assert 重放.status_code == 403
        assert len(_草稿聚合(應用)._草稿) == 1

        接續成功 = _建立草稿(客戶端, 接續, "structured")
        assert 接續成功.status_code == 201, 接續成功.text
        assert len(_草稿聚合(應用)._草稿) == 2

    _斷言零發布副作用(環境, _發布面快照(環境))


# ---------------------------------------------------------------------------
# 步驟十：Restart 行為符合 A3-01 Frozen Contract（草稿 Aggregate 為 in-memory）
# ---------------------------------------------------------------------------


def test_草稿Aggregate為記憶體且Restart後不保留(tmp_path):
    """A3-01 凍結契約為 in-memory：重啟後既有草稿必須完全失效。"""
    環境 = _建立環境(tmp_path)
    擁有者識別碼 = _使用者識別碼(環境, _帳號)

    第一應用, _ = 環境["建立應用"]()
    with TestClient(第一應用) as 客戶端:
        _, csrf = _登入(客戶端)
        本文 = _建立草稿(客戶端, csrf, "structured").json()
        第一聚合 = _草稿聚合(第一應用)
        assert 第一聚合.讀取草稿(
            擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"] - 1,
        ).草稿識別碼 == 本文["draft_id"]

    第二應用, _ = 環境["建立應用"]()
    with TestClient(第二應用) as 客戶端:
        第二聚合 = _草稿聚合(第二應用)
        assert 第二聚合 is not 第一聚合
        assert 第二聚合._草稿 == {}

        with pytest.raises(草稿存取錯誤):
            第二聚合.讀取草稿(擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"] - 1)

        _, csrf = _登入(客戶端)
        重啟後 = _建立草稿(客戶端, csrf, "structured")
        assert 重啟後.status_code == 201, 重啟後.text
        assert 重啟後.json()["draft_id"] != 本文["draft_id"]

    _斷言零發布副作用(環境, _發布面快照(環境))


# ---------------------------------------------------------------------------
# 步驟十一：Shutdown 後直接呼叫 Proxy 必須 Fail Closed
# ---------------------------------------------------------------------------


def test_Shutdown後直接呼叫Proxy固定Fail_Closed(tmp_path):
    """lifespan 結束後 proxy 必須撤銷 authority，直接呼叫也不得建立草稿。"""
    環境 = _建立環境(tmp_path)
    應用, 代理 = 環境["建立應用"]()
    擁有者識別碼 = _使用者識別碼(環境, _帳號)

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        assert _建立草稿(客戶端, csrf, "structured").status_code == 201
        聚合 = _草稿聚合(應用)
        資源 = _Planner資源(應用)

    assert 代理._服務 is None
    assert 資源.取得規劃服務() is None
    assert 資源.取得工具發布庫() is None

    with pytest.raises(草稿規劃服務不可用):
        代理.建立草稿(
            擁有者識別碼, "建立 Alpha API", (_技能名稱,), "structured", 現在=0.0,
        )

    assert len(聚合._草稿) == 1
    _斷言零發布副作用(環境, _發布面快照(環境))


def test_Shutdown後草稿路由不再建立草稿(tmp_path):
    """關閉後的 app 不得再經由 HTTP 建立任何草稿。"""
    環境 = _建立環境(tmp_path)
    應用, 代理 = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        assert _建立草稿(客戶端, csrf, "structured").status_code == 201

    assert 代理._服務 is None
    with pytest.raises(草稿規劃服務不可用):
        代理.建立草稿("1", "建立 Alpha API", (_技能名稱,), "text", 現在=0.0)
