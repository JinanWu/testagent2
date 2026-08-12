"""A08-1 Canonical Published 環境與固定 provider composition 測試。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import asgi as root_asgi
from 繁中代理.發布介面 import asgi as asgi模組

def _環境(tmp_path: Path) -> dict[str, str]:
    """建立只含 canonical 路徑名稱與既有 Web 安全設定的環境。"""
    return {
        "TESTAGENT2_DB_PATH": str(tmp_path / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(tmp_path / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(tmp_path / "bundles"),
        "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
        "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash-lite",
        "AIAGENT_GCP_PROJECT": "example-project",
        "AIAGENT_GCP_LOCATION": "global",
    }


def test_公開canonical_parser與root_factory存在():
    """A08-ENV-01：root factory 與 strict parser 是 canonical 公開 seam。"""
    assert callable(getattr(asgi模組, "解析Canonical環境設定", None))
    assert root_asgi.建立應用程式 is asgi模組.建立環境應用程式


@pytest.mark.parametrize(
    "刪除鍵",
    ("TESTAGENT2_DB_PATH", "TESTAGENT2_PUBLISHED_DB_PATH", "TESTAGENT2_PUBLISHED_BUNDLE_ROOT"),
)
def test_三個canonical路徑皆明確必填(tmp_path: Path, 刪除鍵: str):
    """A08-ENV-02：不得接受 legacy alias 或 cwd fallback。"""
    環境 = _環境(tmp_path)
    del 環境[刪除鍵]
    with pytest.raises(ValueError, match="^Canonical環境設定無效$"):
        asgi模組.解析Canonical環境設定(環境)


@pytest.mark.parametrize(
    ("鍵", "值"),
    (
        ("TESTAGENT2_DB_PATH", "relative.sqlite3"),
        ("TESTAGENT2_PUBLISHED_DB_PATH", "relative.sqlite3"),
        ("TESTAGENT2_PUBLISHED_BUNDLE_ROOT", "relative-bundles"),
        ("TESTAGENT2_WEB_DB_PATH", "/tmp/wrong-alias.sqlite3"),
        ("TESTAGENT2_BUNDLE_ROOT", "/tmp/wrong-alias-bundles"),
        ("TESTAGENT2_PUBLISHED_IMPORT_PATH", "package:callable"),
        ("TESTAGENT2_UNKNOWN", "not-approved"),
        ("TESTAGENT2_MODEL_IMPORT_PATH", "package:model"),
        ("AIAGENT_UNKNOWN", "not-approved"),
        ("TESTAGENT2_MODEL_PROVIDER", "fake"),
    ),
)
def test_relative_alias_unknown與fake_production皆固定拒絕(tmp_path: Path, 鍵: str, 值: str):
    """A08-ENV-03：環境不能選 callable、tool authority 或 fake provider。"""
    環境 = _環境(tmp_path)
    環境[鍵] = 值
    with pytest.raises(ValueError, match="^Canonical環境設定無效$") as 捕捉:
        asgi模組.解析Canonical環境設定(環境)
    assert 值 not in str(捕捉.value)


def test_直接DB_alias在純記憶體parser拒絕(tmp_path: Path):
    """A08-ENV-04：相同 lexical DB path 不等到 migration 才拒絕。"""
    環境 = _環境(tmp_path)
    環境["TESTAGENT2_PUBLISHED_DB_PATH"] = 環境["TESTAGENT2_DB_PATH"]
    with pytest.raises(ValueError, match="^Canonical環境設定無效$"):
        asgi模組.解析Canonical環境設定(環境)


def test_construction零IO且root_app已包含stable_route(tmp_path: Path, monkeypatch):
    """A08-ENV-05：root construction 只組裝，DB、bundle root 與 callback 留給 lifespan。"""
    環境 = _環境(tmp_path)
    Web設定, Published設定 = asgi模組.解析Canonical環境設定(環境)
    app = asgi模組.建立Canonical應用程式(Web設定, Published設定)
    monkeypatch.setattr(asgi模組.os, "environ", 環境)
    root_app = root_asgi.建立應用程式()
    assert not Path(環境["TESTAGENT2_DB_PATH"]).exists()
    assert not Path(環境["TESTAGENT2_PUBLISHED_DB_PATH"]).exists()
    assert not Path(環境["TESTAGENT2_PUBLISHED_BUNDLE_ROOT"]).exists()
    assert tuple(app.openapi()["paths"]["/v1/endpoints/{slug}/invoke"]) == ("post",)
    assert tuple(root_app.openapi()["paths"]["/v1/endpoints/{slug}/invoke"]) == ("post",)


def test_resolved_symlink與inode_alias在lifespan先於provider拒絕(tmp_path: Path):
    """A08-ENV-06：可解析別名與 hard-link identity 都 fail closed 且 callback 為零。"""
    for 類型 in ("symlink", "hardlink"):
        根 = tmp_path / 類型
        根.mkdir()
        Web = 根 / "web.sqlite3"
        Web.touch()
        Published = 根 / "published.sqlite3"
        if 類型 == "symlink":
            Published.symlink_to(Web)
        else:
            Published.hardlink_to(Web)
        Bundles = 根 / "bundles"
        Bundles.mkdir()
        環境 = _環境(根)
        Web設定, Published設定 = asgi模組.解析Canonical環境設定(環境)
        次數 = {"installer": 0, "model": 0}

        def 不可安裝(_工具庫):
            次數["installer"] += 1

        def 不可建立模型表():
            次數["model"] += 1
            return {"unexpected": object()}

        app = asgi模組.建立Canonical應用程式(Web設定, replace(
            Published設定,
            工具發布安裝器=不可安裝,
            模型供應商註冊表工廠=不可建立模型表,
        ))
        with pytest.raises(RuntimeError, match="^發布介面啟動失敗$"):
            with TestClient(app):
                pass
        assert 次數 == {"installer": 0, "model": 0}


def test_lifespan成功時固定authority各建立一次且shutdown只清理一次(tmp_path: Path, monkeypatch):
    """A08-ENV-07：construction 零 callback；startup 各一次；shutdown drain 只執行一次。"""
    from 繁中代理.發布介面 import 生產Published執行 as 執行模組
    from 繁中代理.發布介面.生產技能工具 import 安裝生產技能工具

    環境 = _環境(tmp_path)
    Path(環境["TESTAGENT2_PUBLISHED_BUNDLE_ROOT"]).mkdir()
    Web設定, Published設定 = asgi模組.解析Canonical環境設定(環境)
    次數 = {"installer": 0, "model": 0, "shutdown": 0}

    def 安裝(工具庫):
        次數["installer"] += 1
        安裝生產技能工具(工具庫)

    def 建立模型表():
        次數["model"] += 1
        return {"gemini-adc": object()}

    原清理 = 執行模組.生產Published執行資源._清除同步

    def 計數清理(self):
        次數["shutdown"] += 1
        return 原清理(self)

    monkeypatch.setattr(執行模組.生產Published執行資源, "_清除同步", 計數清理)
    app = asgi模組.建立Canonical應用程式(Web設定, replace(
        Published設定,
        工具發布安裝器=安裝,
        模型供應商註冊表工廠=建立模型表,
    ))
    assert 次數 == {"installer": 0, "model": 0, "shutdown": 0}
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert 次數 == {"installer": 1, "model": 1, "shutdown": 0}
    assert 次數 == {"installer": 1, "model": 1, "shutdown": 1}
