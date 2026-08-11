"""Acceptance #5 SA-1：凍結服務帳戶建立的 canonical HTTP 契約。

本模組只從 ``建立CP4ASGI應用程式`` 觀測公開路由與 OpenAPI seam；
服務帳戶只能是 Endpoint Create 的內部原子副作用，不得成為 client claim 或獨立 CRUD。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.設定 import 生產設定


端點建立路徑 = "/api/published-endpoints"
公開建立欄位 = {
    "endpoint_id",
    "version_id",
    "version_number",
    "status",
    "initial_api_key",
}


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


def _建立完整管理應用程式(暫存目錄: Path, 工廠呼叫: list[str]):
    """以 explicit factories 建立完整管理能力，但不啟動 lifespan。

    參數：
        暫存目錄: 提供彼此隔離的 Web DB、Published DB 與 bundle root 路徑。
        工廠呼叫: 若 app construction 錯誤執行 callback，會留下可觀測事件。
    返回值：
        尚未啟動、但 OpenAPI 應已公開完整管理路由的 canonical app。
    """
    網頁設定 = 生產設定(
        暫存目錄 / "web.sqlite3",
        ("https://client.example",),
        "fake",
        "fake",
    )
    planner設定 = Planner生產設定(
        "acceptance-release",
        lambda 路徑: 工廠呼叫.append("owner") or 使用者庫(路徑),
        lambda: 工廠呼叫.append("planner") or 決定性假規劃器(),
        3600.0,
    )
    published設定 = Published生產設定(
        暫存目錄 / "published.sqlite3",
        暫存目錄 / "bundles",
        lambda _工具庫: 工廠呼叫.append("tools"),
        lambda: 工廠呼叫.append("models") or {"fake": object()},
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
