"""A3-04：Canonical Production Composition 的授權與失敗注入矩陣。

證明安全失敗不是只在 fake route test 成立：所有案例都打 canonical app
（``建立CP4ASGI應用程式``）真實掛載的 ``POST /api/published-endpoints/draft``，
使用真 Cookie Session 與 single-use CSRF，並在每次失敗後 readback 檔案系統與
Published SQLite，證明 Endpoint／Version／Credential／Bundle 全零副作用。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from published_resource_support import 取得Published資源

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.規劃 import 綱要 as 綱要模組
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.設定 import 生產設定

_草稿路徑 = "/api/published-endpoints/draft"
_技能名稱 = "alpha"
_未授權技能 = "beta"
_帳號 = "owner"
_密碼 = "correct horse battery"
_帳號二 = "owner-two"
_密碼二 = "another correct battery"
_發布識別 = "release-1"
_敏感標記 = "SECRET-PROVIDER-DETAIL-/private/skills/alpha/SKILL.md"
_角色敏感標記 = "ROLE-MEMBERSHIP-super-admin-internal"
_handler敏感標記 = "private.tools.handlers.alpha_handler:QualifiedHandler"

_必要副作用資料表 = {
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


def _工具發布描述() -> 工具發布描述:
    """建立 startup 安裝的固定 pinned release。"""
    return 工具發布描述(_發布識別, (工具發布註冊("revision-1", 工具定義(
        "alpha-tool", "Alpha tool",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _參數: "ok",
    )),))


def _建立環境(tmp_path: Path) -> dict:
    """建立技能來源、真實使用者與 canonical Controller 應用程式。"""
    技能根 = tmp_path / "skills"
    for 名稱 in (_技能名稱, _未授權技能):
        (技能根 / 名稱).mkdir(parents=True)
        (技能根 / 名稱 / "SKILL.md").write_text(
            f"---\nname: {名稱}\ndescription: {名稱} skill\n---\n# {名稱}\n",
            encoding="utf-8",
        )
    網頁資料庫 = tmp_path / "web.sqlite3"
    使用者庫物件 = 使用者庫(網頁資料庫)
    使用者庫物件.建立使用者(
        _帳號, _密碼, roles=["admin"], enabled_tools=["alpha-tool"],
        enabled_skills=[_技能名稱], skill_roots=[str(技能根)],
        allowed_workdirs=[str(技能根)],
    )
    使用者庫物件.建立使用者(
        _帳號二, _密碼二, roles=["member"], enabled_tools=["alpha-tool"],
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
            _發布識別, 使用者庫, lambda: 決定性假規劃器(), 3600.0,
        ),
    )
    return {
        "應用": 建立CP4ASGI應用程式(網頁設定, 發布設定),
        "技能根": 技能根, "套件根": 套件根,
        "發布資料庫": tmp_path / "published.sqlite3",
        "網頁資料庫": 網頁資料庫,
    }


def _登入(客戶端: TestClient, 帳號: str = _帳號, 密碼: str = _密碼) -> str:
    """以真實帳密取得 cookie session 與首枚 single-use CSRF。"""
    回應 = 客戶端.post(
        "/api/auth/login", json={"username": 帳號, "password": 密碼},
    )
    assert 回應.status_code == 200, 回應.text
    return 回應.json()["csrf_token"]


def _本文(技能: list[str] | None = None) -> dict:
    """建立唯一合法的三鍵 request body。"""
    return {
        "original_requirement_text": "建立 Alpha API",
        "selected_skills": 技能 if 技能 is not None else [_技能名稱],
        "response_mode": "structured",
    }


def _擁有者身份(環境: dict, 帳號: str = _帳號) -> 網頁使用者:
    """由真實 Web 使用者庫取得 canonical session principal。"""
    使用者庫物件 = 使用者庫(環境["網頁資料庫"])
    try:
        識別碼 = str(使用者庫物件.讀取使用者(username=帳號)["id"])
    finally:
        使用者庫物件.連線.close()
    return 網頁使用者(識別碼, 帳號, "member")


def _Planner資源(應用):
    """取得 canonical startup 安裝的 Planner 資源。"""
    return 取得Published資源(應用).取得Planner資源()


class _注入規劃器:
    """以注入行為取代 canonical planner；``決定性假規劃器`` 為 frozen 不可改。"""

    def __init__(self, 失敗) -> None:
        """保存注入行為。"""
        self._失敗 = 失敗

    def 產生(self, 輸入, /) -> str:
        """忽略輸入並執行注入行為。"""
        del 輸入
        return self._失敗()


def _注入規劃器失敗(應用, 失敗):
    """把 canonical 已安裝的 planner 整個換成注入版本，並回傳原始 planner。"""
    服務 = _Planner資源(應用)._服務
    原始 = 服務._規劃器
    服務._規劃器 = _注入規劃器(失敗)
    return 原始


def _還原規劃器(應用, 原始) -> None:
    """把 canonical planner 還原成 startup 安裝的原件。"""
    _Planner資源(應用)._服務._規劃器 = 原始


def _草稿數(應用) -> int:
    """讀取 canonical Draft Aggregate 目前保存的草稿數。"""
    return len(_Planner資源(應用).取得規劃服務()._草稿)


def _副作用快照(環境: dict, 應用) -> dict:
    """完整 readback Draft、Published 全部存在表，以及 Bundle tree bytes。"""
    草稿 = _Planner資源(應用).取得規劃服務()._草稿
    快照 = {
        "drafts": tuple(sorted((鍵, 值.擁有者識別碼, 值._綱要正規JSON,
                                 getattr(值.能力摘要, "正規JSON", None)) for 鍵, 值 in 草稿.items())),
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
        assert _必要副作用資料表 <= set(現有表)
        for 表 in 現有表:
            安全表 = 表.replace('"', '""')
            快照["tables"][表] = tuple(連線.execute(f'SELECT * FROM "{安全表}" ORDER BY rowid'))
    finally:
        連線.close()
    return 快照


def _斷言零副作用(環境: dict, 應用, 之前: dict | None = None) -> None:
    """以完整 before/after snapshot 證明任何失敗點都沒有 publication mutation。"""
    之後 = _副作用快照(環境, 應用)
    if 之前 is not None:
        assert 之後 == 之前
        return
    assert 之後["drafts"] == ()
    assert 之後["bundle_tree"] == ()
    assert all(之後["tables"][表] == () for 表 in _必要副作用資料表)


def _斷言不洩漏(環境: dict, 回應) -> None:
    """回應不得洩漏 provider、role、handler、技能路徑或 Bundle Root。"""
    for 敏感 in (
        _敏感標記, str(環境["技能根"]), str(環境["套件根"]),
        str(環境["網頁資料庫"]), "SKILL.md", "Traceback", "planner_content",
        _角色敏感標記, _handler敏感標記, "QualifiedHandler", "alpha_handler",
    ):
        assert 敏感 not in 回應.text


# ---------------------------------------------------------------------------
# 矩陣一：Authorization — Unauthorized 與 Unknown 不可枚舉
# ---------------------------------------------------------------------------


_授權案例 = {
    "未授權但存在": [_未授權技能],
    "完全不存在": ["does-not-exist"],
    "授權與未授權混合": [_技能名稱, _未授權技能],
    "授權與不存在混合": [_技能名稱, "does-not-exist"],
    "大小寫不符": [_技能名稱.upper()],
}


@pytest.mark.parametrize("案例", sorted(_授權案例))
def test_授權矩陣一律固定拒絕且零副作用(tmp_path, 案例: str):
    """任何未獲授權的技能選擇都必須 fail closed，且不留任何副作用。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑, json=_本文(_授權案例[案例]), headers={"X-CSRF-Token": csrf},
        )
        assert 回應.status_code == 403, 回應.text
        assert 回應.json() == {"detail": {"code": "planning_not_authorized"}}
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"])


def test_Unauthorized與Unknown回應完全不可枚舉(tmp_path):
    """存在但未授權、與根本不存在的技能，回應必須逐字節相同。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    收集: list[tuple] = []

    with TestClient(環境["應用"]) as 客戶端:
        for 技能 in ([_未授權技能], ["does-not-exist"], ["zzz-unknown-9"]):
            csrf = _登入(客戶端)
            回應 = 客戶端.post(
                _草稿路徑, json=_本文(技能), headers={"X-CSRF-Token": csrf},
            )
            收集.append((回應.status_code, 回應.text))

    assert len(set(收集)) == 1, f"回應可被枚舉：{收集}"
    assert 收集[0][0] == 403


def test_登入後撤權立即Fail_Closed(tmp_path):
    """草稿建立前撤銷技能授權必須立刻關閉，不得沿用登入當下的快照。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        成功 = 客戶端.post(
            _草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 成功.status_code == 201, 成功.text

        使用者庫物件 = 使用者庫(環境["網頁資料庫"])
        使用者庫物件.設定權限欄位(_帳號, "enabled_skills_json", ["other"])
        使用者庫物件.連線.close()

        csrf = _登入(客戶端)
        撤權後 = 客戶端.post(
            _草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf},
        )

    assert 撤權後.status_code == 403
    assert 撤權後.json() == {"detail": {"code": "planning_not_authorized"}}
    _斷言不洩漏(環境, 撤權後)


# ---------------------------------------------------------------------------
# 矩陣二：Provider Error 不洩漏
# ---------------------------------------------------------------------------


def _丟出敏感例外():
    """模擬 provider 以含敏感細節的訊息失敗。"""
    raise RuntimeError(_敏感標記)


def _丟出連線失敗():
    """模擬 provider 傳輸層失敗。"""
    raise ConnectionError(_敏感標記)


def _回傳非JSON():
    """模擬 provider 回傳非 JSON 文字。"""
    return f"not-json {_敏感標記}"


def _回傳缺欄位JSON():
    """模擬 provider 回傳缺少契約欄位的 JSON。"""
    return json.dumps({"endpoint_name": _敏感標記})


def _合法供應商值() -> dict:
    return {
        "endpoint_name": "Alpha API", "suggested_slug": "alpha-api",
        "behavior_summary": "摘要", "selected_skills": [_技能名稱],
        "recommended_tools": ["alpha-tool"], "tool_capabilities": {"alpha-tool": "查詢"},
        "system_prompt": "提示", "input_schema": None,
        "response_schema": {"type": "object", "properties": {"result": {"type": "string"}},
                            "required": ["result"], "additionalProperties": False},
        "human_docs": "文件",
        "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
        "warnings": [],
    }


def _回傳越權工具():
    """模擬 provider 建議未獲授權的工具，並夾帶 handler detail。"""
    值 = _合法供應商值()
    值["recommended_tools"] = ["unauthorized-tool"]
    值["tool_capabilities"] = {"unauthorized-tool": _handler敏感標記}
    return json.dumps(值)


def _回傳NaN():
    值 = _合法供應商值()
    值["rate_limit"]["endpoint_per_minute"] = float("nan")
    return json.dumps(值)


def _回傳錯誤Schema():
    值 = _合法供應商值()
    值["response_schema"] = {"type": "not-a-json-schema-type", "handler": _handler敏感標記}
    return json.dumps(值)


class _敵對文字(str):
    def encode(self, *_參數, **_關鍵字):
        raise AssertionError(_handler敏感標記)


_供應商案例 = {
    "敏感例外": (_丟出敏感例外, 503, "planner_unavailable"),
    "連線失敗": (_丟出連線失敗, 503, "planner_unavailable"),
    "timeout": (lambda: (_ for _ in ()).throw(TimeoutError(_敏感標記)), 503, "planner_unavailable"),
    "非JSON": (_回傳非JSON, 502, "planner_output_invalid"),
    "缺欄位": (_回傳缺欄位JSON, 502, "planner_output_invalid"),
    "越權工具": (_回傳越權工具, 502, "planner_output_invalid"),
    "bytes hostile type": (lambda: _handler敏感標記.encode(), 502, "planner_output_invalid"),
    "dict hostile type": (lambda: {"handler": _handler敏感標記}, 502, "planner_output_invalid"),
    "str subclass trap": (lambda: _敵對文字("{}"), 502, "planner_output_invalid"),
    "NaN": (_回傳NaN, 502, "planner_output_invalid"),
    "schema invalid": (_回傳錯誤Schema, 502, "planner_output_invalid"),
    "oversize output": (lambda: "x" * 131_073 + _handler敏感標記, 502, "planner_output_invalid"),
}


@pytest.mark.parametrize("案例", sorted(_供應商案例))
def test_供應商失敗矩陣固定映射不洩漏且零副作用(tmp_path, 案例: str):
    """任何 provider 失敗都必須映射為固定錯誤碼，不外洩細節且無副作用。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    失敗, 預期狀態, 預期碼 = _供應商案例[案例]

    with TestClient(環境["應用"]) as 客戶端:
        _注入規劃器失敗(環境["應用"], 失敗)
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 回應.status_code == 預期狀態, 回應.text
        assert 回應.json() == {"detail": {"code": 預期碼}}
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"])


def test_供應商失敗後服務仍可正常建立草稿(tmp_path):
    """失敗必須關閉單一請求，不得毒化 canonical composition。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]) as 客戶端:
        原始規劃器 = _注入規劃器失敗(環境["應用"], _丟出敏感例外)
        csrf = _登入(客戶端)
        失敗回應 = 客戶端.post(
            _草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 失敗回應.status_code == 503
        _斷言零副作用(環境, 環境["應用"])

        _還原規劃器(環境["應用"], 原始規劃器)
        csrf = _登入(客戶端)
        成功回應 = 客戶端.post(
            _草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 成功回應.status_code == 201, 成功回應.text
        assert _草稿數(環境["應用"]) == 1


# ---------------------------------------------------------------------------
# 矩陣三：Control-flow 不得被轉成 503
# ---------------------------------------------------------------------------


def _草稿端點(應用):
    """取出 canonical app 上實際掛載的草稿 route endpoint。"""
    路由們 = [
        路由 for 路由 in 應用.routes
        if getattr(路由, "path", None) == _草稿路徑
    ]
    assert len(路由們) == 1, "canonical app 必須只掛一條草稿 route"
    return 路由們[0].endpoint


def _請求(本文: dict):
    """建立可餵給 canonical endpoint 的最小 ASGI 請求。"""
    原始 = json.dumps(本文, ensure_ascii=False).encode()
    已送 = False

    async def receive():
        """一次送出完整本文，之後回空片段。"""
        nonlocal 已送
        if 已送:
            return {"type": "http.request", "body": b"", "more_body": False}
        已送 = True
        return {"type": "http.request", "body": 原始, "more_body": False}

    from starlette.requests import Request
    return Request(
        {
            "type": "http", "method": "POST", "path": _草稿路徑,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流例外原樣傳出不被轉成503(tmp_path, 控制型別):
    """provider 的控制流例外必須保持 identity 與 args，不得映射為 HTTP 錯誤。

    這裡直接呼叫 canonical app 已掛載的 route endpoint，而非經 ``TestClient``：
    控制流例外無法穿過 TestClient 的 blocking portal（會 deadlock），因此以
    canonical composition 已啟動、真實 Planner 服務已安裝的狀態直接驅動 endpoint，
    受測對象仍是 canonical route 的例外映射本身。
    """
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    原控制 = 控制型別("停止")

    def 丟出控制():
        """以同一物件丟出控制流例外。"""
        raise 原控制

    with TestClient(環境["應用"]):
        _注入規劃器失敗(環境["應用"], 丟出控制)
        端點 = _草稿端點(環境["應用"])
        使用者 = _擁有者身份(環境)

        with pytest.raises(控制型別) as 捕捉:
            asyncio.run(端點(_請求(_本文()), 使用者, 使用者))

        assert 捕捉.value is 原控制, "控制流例外必須保持同一物件 identity"
        assert 捕捉.value.args == ("停止",), "args 不得被改寫"
        assert not isinstance(捕捉.value, Exception) or 控制型別 is GeneratorExit
        _斷言零副作用(環境, 環境["應用"])


def test_普通例外仍被映射為固定503(tmp_path):
    """同一入口下的一般例外必須固定映射，證明控制流是被特別放行而非全部逃逸。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]):
        _注入規劃器失敗(環境["應用"], _丟出敏感例外)
        端點 = _草稿端點(環境["應用"])
        使用者 = _擁有者身份(環境)

        with pytest.raises(HTTPException) as 捕捉:
            asyncio.run(端點(_請求(_本文()), 使用者, 使用者))

        assert 捕捉.value.status_code == 503
        assert 捕捉.value.detail == {"code": "planner_unavailable"}
        assert _敏感標記 not in str(捕捉.value.detail)
        _斷言零副作用(環境, 環境["應用"])


# ---------------------------------------------------------------------------
# 矩陣四：Request 邊界失敗同樣零副作用
# ---------------------------------------------------------------------------


_本文案例 = {
    "legacy規劃內容": {"original_requirement_text": "x",
                       "planner_content": {"system_prompt": "注入"}},
    "額外欄位": {**_本文(), "selected_tools": []},
    "缺回應模式": {"original_requirement_text": "x", "selected_skills": [_技能名稱]},
    "技能空陣列": {**_本文([])},
    "回應模式非法": {**_本文(), "response_mode": "current"},
}


@pytest.mark.parametrize("案例", sorted(_本文案例))
def test_本文邊界失敗在服務前關閉且零副作用(tmp_path, 案例: str):
    """任何違反 exact request 契約的本文都必須在觸及服務前 422 關閉。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑, json=_本文案例[案例], headers={"X-CSRF-Token": csrf},
        )
        assert 回應.status_code == 422, 回應.text
        assert 回應.json() == {"detail": {"code": "invalid_request"}}
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"])


@pytest.mark.parametrize("身份案例", ["無工作階段", "缺CSRF", "錯CSRF"])
def test_身份失敗在服務前關閉且零副作用(tmp_path, 身份案例: str):
    """session 與 CSRF 失敗必須在觸及 Planner 前關閉。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]) as 客戶端:
        觸及: list[str] = []
        _注入規劃器失敗(
            環境["應用"], lambda: 觸及.append("planner") or _丟出敏感例外(),
        )
        標頭 = {}
        if 身份案例 == "缺CSRF":
            _登入(客戶端)
        elif 身份案例 == "錯CSRF":
            _登入(客戶端)
            標頭 = {"X-CSRF-Token": "x" * 43}

        回應 = 客戶端.post(_草稿路徑, json=_本文(), headers=標頭)

        assert 回應.status_code == (401 if 身份案例 == "無工作階段" else 403)
        assert 觸及 == [], "身份失敗不得觸及 Planner"
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"])


# ---------------------------------------------------------------------------
# 補正矩陣：canonical identity／authority／request／store／concurrency
# ---------------------------------------------------------------------------


def test_disabled_user持有既有session仍固定401且完整快照不變(tmp_path):
    環境 = _建立環境(tmp_path.resolve())
    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        with sqlite3.connect(環境["網頁資料庫"]) as 連線:
            連線.execute("UPDATE users SET disabled=1 WHERE username=?", (_帳號,))
        之前 = _副作用快照(環境, 環境["應用"])
        回應 = 客戶端.post(_草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf})
        assert 回應.status_code == 401
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"], 之前)


def test_single_use_CSRF_replay固定403且不增加既有草稿(tmp_path):
    環境 = _建立環境(tmp_path.resolve())
    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        成功 = 客戶端.post(_草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf})
        assert 成功.status_code == 201
        之前 = _副作用快照(環境, 環境["應用"])
        重播 = 客戶端.post(_草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf})
        assert 重播.status_code == 403
        _斷言零副作用(環境, 環境["應用"], 之前)


def test_canonical_principal_mismatch固定500且服務前關閉(tmp_path):
    環境 = _建立環境(tmp_path.resolve())
    應用 = 環境["應用"]
    with TestClient(應用) as 客戶端:
        csrf = _登入(客戶端)
        路由 = next(路由 for 路由 in 應用.routes if getattr(路由, "path", None) == _草稿路徑)
        csrf相依 = 路由.dependant.dependencies[1].call
        應用.dependency_overrides[csrf相依] = lambda: _擁有者身份(環境, _帳號二)
        之前 = _副作用快照(環境, 應用)
        try:
            回應 = 客戶端.post(_草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf})
        finally:
            應用.dependency_overrides.clear()
        assert 回應.status_code == 500
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 應用, 之前)


def test_authority_provider普通失敗固定503且role_detail不洩漏(tmp_path):
    class _失敗權威:
        def 查詢規劃權限(self, _owner, /):
            raise RuntimeError(f"{_角色敏感標記} {_敏感標記}")

    環境 = _建立環境(tmp_path.resolve())
    with TestClient(環境["應用"]) as 客戶端:
        _Planner資源(環境["應用"])._服務._權限查詢 = _失敗權威()
        csrf = _登入(客戶端)
        之前 = _副作用快照(環境, 環境["應用"])
        回應 = 客戶端.post(_草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf})
        assert 回應.status_code == 503
        assert 回應.json() == {"detail": {"code": "planner_unavailable"}}
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"], 之前)


@pytest.mark.parametrize("案例", ["wrong content-type", "duplicate key", "raw oversize", "unsorted skills", "duplicate skills"])
def test_canonical_request_strictness完整readback(tmp_path, 案例):
    環境 = _建立環境(tmp_path.resolve())
    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        標頭 = {"X-CSRF-Token": csrf, "content-type": "application/json"}
        if 案例 == "wrong content-type":
            標頭["content-type"] = "text/plain"
            原始 = json.dumps(_本文()).encode()
        elif 案例 == "duplicate key":
            原始 = b'{"original_requirement_text":"x","original_requirement_text":"y","selected_skills":["alpha"],"response_mode":"structured"}'
        elif 案例 == "raw oversize":
            原始 = b"x" * 32769
        elif 案例 == "unsorted skills":
            原始 = json.dumps(_本文(["beta", "alpha"])).encode()
        else:
            原始 = json.dumps(_本文(["alpha", "alpha"])).encode()
        之前 = _副作用快照(環境, 環境["應用"])
        回應 = 客戶端.post(_草稿路徑, content=原始, headers=標頭)
        assert 回應.status_code == 422
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"], 之前)


def test_authorized_store_post_insert_reconstruction_failure不留幽靈草稿(tmp_path, monkeypatch):
    環境 = _建立環境(tmp_path.resolve())
    原錯誤 = RuntimeError(f"{_handler敏感標記} {_敏感標記}")

    def 重建失敗(_草稿):
        raise 原錯誤

    with TestClient(環境["應用"]) as 客戶端:
        monkeypatch.setattr(綱要模組, "_必須重建公開草稿", 重建失敗)
        csrf = _登入(客戶端)
        之前 = _副作用快照(環境, 環境["應用"])
        回應 = 客戶端.post(_草稿路徑, json=_本文(), headers={"X-CSRF-Token": csrf})
        assert 回應.status_code == 503
        _斷言不洩漏(環境, 回應)
        _斷言零副作用(環境, 環境["應用"], 之前)


def test_兩owner並行canonical_requests與readback完全隔離(tmp_path):
    環境 = _建立環境(tmp_path.resolve())
    應用 = 環境["應用"]
    with TestClient(應用) as 客戶端:
        csrf一 = _登入(客戶端)
        cookie一 = dict(客戶端.cookies)
        客戶端.cookies.clear()
        csrf二 = _登入(客戶端, _帳號二, _密碼二)
        cookie二 = dict(客戶端.cookies)
        客戶端.cookies.clear()

        def 建立(資料):
            cookie, csrf, 標記 = 資料
            cookie_header = "; ".join(f"{名稱}={值}" for 名稱, 值 in sorted(cookie.items()))
            return 客戶端.post(
                _草稿路徑,
                json={**_本文(), "original_requirement_text": f"建立 {標記} API"},
                headers={"X-CSRF-Token": csrf, "Cookie": cookie_header},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as 執行器:
            回應一, 回應二 = list(執行器.map(建立, ((cookie一, csrf一, "ONE"), (cookie二, csrf二, "TWO"))))
        assert 回應一.status_code == 回應二.status_code == 201
        id一, id二 = 回應一.json()["draft_id"], 回應二.json()["draft_id"]
        owner一, owner二 = _擁有者身份(環境), _擁有者身份(環境, _帳號二)
        服務 = _Planner資源(應用).取得規劃服務()
        草稿一 = 服務.讀取草稿(owner一.識別碼, id一, 現在=0)
        草稿二 = 服務.讀取草稿(owner二.識別碼, id二, 現在=0)
        assert 草稿一.擁有者識別碼 == owner一.識別碼
        assert 草稿二.擁有者識別碼 == owner二.識別碼
        assert 草稿一.能力摘要 is not 草稿二.能力摘要
        with pytest.raises(Exception):
            服務.讀取草稿(owner一.識別碼, id二, 現在=0)
        with pytest.raises(Exception):
            服務.讀取草稿(owner二.識別碼, id一, 現在=0)
