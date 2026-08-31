"""驗證 CP4 Controller 的資料庫實體隔離與局部啟動清理。

參數：
    由 pytest 提供暫存路徑與 monkeypatch fixture。
返回值：
    測試以斷言表示成功，不提供公開回傳值。
例外：
    只允許各案例明確捕捉的固定啟動錯誤或原始控制流程例外。
副作用：
    在暫存目錄建立 SQLite 別名，並短暫啟動測試應用程式。
"""

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.工具 import 工具定義
import 繁中代理.發布介面.生產Published執行 as 組裝
import 繁中代理.發布介面.生產Web代理 as 網頁組裝
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.設定 import 生產設定


def _設定(網頁資料庫: Path, Published資料庫: Path, 安裝器, 模型工廠):
    """建立只供組裝安全案例使用的 exact 生產設定。

    參數：
        網頁資料庫: CP3 Web SQLite 路徑。
        Published資料庫: CP4 Published SQLite 路徑。
        安裝器: startup 工具發布安裝 callback。
        模型工廠: startup 模型註冊表 callback。
    返回值：
        生產設定與 Published 生產設定的二元組。
    例外：
        設定不符合正式契約時傳出 ``ValueError``。
    副作用：
        無；只建立不可變設定。
    """
    生產 = 生產設定(網頁資料庫, ("https://client.example",), "fake", "fake", None, None)
    發布 = 組裝.Published生產設定(Published資料庫, 網頁資料庫.parent / "bundles", 安裝器, 模型工廠)
    return 生產, 發布


def _斷言啟動拒絕(生產, 發布) -> None:
    """啟動應用程式並斷言一般失敗只呈現固定訊息。

    參數：
        生產: CP3 生產設定。
        發布: CP4 Published 生產設定。
    返回值：
        ``None``；成功條件由 pytest 斷言表示。
    例外：
        啟動未固定拒絕時讓測試失敗。
    副作用：
        建立並嘗試啟動一次 FastAPI 應用程式。
    """
    應用程式 = 建立CP4ASGI應用程式(生產, 發布)
    with pytest.raises(RuntimeError, match="^發布介面啟動失敗$") as 捕捉:
        with TestClient(應用程式):
            pass
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None


@pytest.mark.parametrize("別名種類", ("相同", "點點", "符號連結", "硬連結"))
def test_same_inode各種別名在任何migration或callback前拒絕(tmp_path, monkeypatch, 別名種類):
    """CP4-SAFE-01：既存 lexical、canonical、symlink 與 hardlink alias 必須零副作用拒絕。

    參數：
        tmp_path: pytest 暫存目錄。
        monkeypatch: 用來觀測兩個 migration initializer。
        別名種類: 本次建立的四種 alias 類型之一。
    返回值：
        ``None``；以 migration 與 callback 紀錄皆空作為成功條件。
    例外：
        只捕捉 lifespan 對一般錯誤的固定映射。
    副作用：
        在暫存目錄建立一般檔案、目錄、符號連結或硬連結。
    """
    共同 = tmp_path / "shared.sqlite3"
    with sqlite3.connect(共同) as 連線:
        連線.execute("CREATE TABLE marker(value INTEGER)")
    if 別名種類 == "相同":
        Published路徑 = 共同
    elif 別名種類 == "點點":
        (tmp_path / "nested").mkdir()
        Published路徑 = tmp_path / "nested" / ".." / 共同.name
    elif 別名種類 == "符號連結":
        Published路徑 = tmp_path / "linked.sqlite3"
        Published路徑.symlink_to(共同)
    else:
        Published路徑 = tmp_path / "hard.sqlite3"
        os.link(共同, Published路徑)
    紀錄 = []
    monkeypatch.setattr(網頁組裝, "初始化發布介面資料庫", lambda _路徑: 紀錄.append("Web migration"))
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _路徑: 紀錄.append("Published migration"))
    生產, 發布 = _設定(
        共同, Published路徑,
        lambda _庫: 紀錄.append("installer"),
        lambda: 紀錄.append("model") or {"fake": object()},
    )
    _斷言啟動拒絕(生產, 發布)
    assert 紀錄 == []


def test_Published初始化後實體變成別名仍在callback前拒絕(tmp_path, monkeypatch):
    """CP4-SAFE-02：第二次 FS identity check 阻止 initializer 期間產生的 hardlink alias。

    參數：
        tmp_path: pytest 暫存目錄。
        monkeypatch: 以可觀測 initializer 模擬實體交換。
    返回值：
        ``None``；以兩次 migration 後零 callback 為成功條件。
    例外：
        只捕捉 lifespan 的固定一般錯誤。
    副作用：
        initializer 在暫存目錄將 Published 路徑建立為 Web DB 硬連結。
    """
    網頁資料庫, Published資料庫, 紀錄 = tmp_path / "web.sqlite3", tmp_path / "published.sqlite3", []

    def 初始化網頁(路徑: Path) -> None:
        """建立 Web 檔案；參數為路徑；返回 ``None``；例外原樣；副作用是寫檔。"""
        路徑.write_bytes(b"web")
        紀錄.append("Web migration")

    def 初始化Published(路徑: Path) -> None:
        """建立 hardlink；參數為路徑；返回 ``None``；例外原樣；副作用是建立別名。"""
        os.link(網頁資料庫, 路徑)
        紀錄.append("Published migration")

    monkeypatch.setattr(網頁組裝, "初始化發布介面資料庫", 初始化網頁)
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", 初始化Published)
    生產, 發布 = _設定(
        網頁資料庫, Published資料庫,
        lambda _庫: 紀錄.append("installer"),
        lambda: 紀錄.append("model") or {"fake": object()},
    )
    _斷言啟動拒絕(生產, 發布)
    assert 紀錄 == ["Web migration", "Published migration"]


def _安裝可觀測發布(盒: dict):
    """建立會保留工具庫且登錄一個 live handler 的 installer。

    參數：
        盒: 測試用可變觀測容器。
    返回值：
        接受工具發布庫的 installer callback。
    例外：
        登錄契約失敗時原樣傳出。
    副作用：
        callback 被呼叫時保存工具庫並登錄 ``release-1``。
    """
    def 安裝(工具庫) -> None:
        """登錄測試發布；參數為工具庫；返回 ``None``；例外原樣；副作用是保存及登錄。"""
        盒["工具庫"] = 工具庫
        工具庫.登錄發布(工具發布描述("release-1", (工具發布註冊(
            "rev-1", 工具定義("lookup", "測試", {"type": "object"}, lambda _參數: "ok"),
        ),)))
    return 安裝


def test_模型工廠一般失敗會移除installer持有庫的所有live_handler(tmp_path, monkeypatch):
    """CP4-CLEAN-01：installer 成功後 model factory 失敗仍不可留下可取得的 handler。

    參數：
        tmp_path: pytest 暫存目錄。
        monkeypatch: 跳過本案例無關的 SQLite migration。
    返回值：
        ``None``；以原錯誤 identity 與 ``取得發布`` 為 ``None`` 斷言成功。
    例外：
        捕捉案例建立的 ordinary sentinel。
    副作用：
        建立後立即清除一個行程內工具發布。
    """
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _路徑: None)
    monkeypatch.setattr(組裝, "_執行技能套件啟動協調", lambda _設定: None)
    monkeypatch.setattr(組裝.SQLite敏感稽核儲存庫, "驗證啟動結構", lambda _self: None)
    盒, 原始錯誤 = {}, LookupError("model failed")
    生產, 發布 = _設定(
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3", _安裝可觀測發布(盒),
        lambda: (_ for _ in ()).throw(原始錯誤),
    )
    with pytest.raises(LookupError) as 捕捉:
        組裝._建立Published資源(生產, 發布, 組裝.延遲外部呼叫編排器())
    assert 捕捉.value is 原始錯誤
    assert 盒["工具庫"].取得發布("release-1") is None


def test_橋接控制流程失敗保持identity參數且清空模型與工具(tmp_path, monkeypatch):
    """CP4-CLEAN-02：bridge control-flow failure 完整清理且不附帶秘密例外鏈。

    參數：
        tmp_path: pytest 暫存目錄。
        monkeypatch: 攔截 migration 與橋接 factory。
    返回值：
        ``None``；以 identity、args、chain 與 detached containers 斷言成功。
    例外：
        捕捉案例建立的 ``KeyboardInterrupt``。
    副作用：
        建立後清除工具發布及 detached 模型表。
    """
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _路徑: None)
    monkeypatch.setattr(組裝, "_執行技能套件啟動協調", lambda _設定: None)
    monkeypatch.setattr(組裝.SQLite敏感稽核儲存庫, "驗證啟動結構", lambda _self: None)
    盒, 中斷 = {}, KeyboardInterrupt("stop", "opaque")

    def 橋接失敗(**參數):
        """保存 detached 模型表後中斷；參數為橋接依賴；無返回；例外為 sentinel；只寫觀測盒。"""
        盒["模型表"] = 參數["模型供應商註冊表"]
        raise 中斷

    monkeypatch.setattr(組裝, "建立發布執行嘗試橋接", 橋接失敗)
    生產, 發布 = _設定(
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3", _安裝可觀測發布(盒),
        lambda: {"fake": object()},
    )
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        組裝._建立Published資源(生產, 發布, 組裝.延遲外部呼叫編排器())
    assert 捕捉.value is 中斷 and 捕捉.value.args == ("stop", "opaque")
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    assert 盒["模型表"] == {} and 盒["工具庫"].取得發布("release-1") is None


def test_正常resource關閉也清除工具庫live_handler(tmp_path, monkeypatch):
    """CP4-CLEAN-03：正常 shutdown 除了 proxy/model detach 也釋放所有工具 handler references。

    參數：
        tmp_path: pytest 暫存目錄。
        monkeypatch: 跳過本案例無關的 migration。
    返回值：
        ``None``；以關閉後發布不可取得斷言成功。
    例外：
        組裝或關閉失敗時原樣傳出並使測試失敗。
    副作用：
        建立並關閉一次 Published lifespan resource。
    """
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _路徑: None)
    monkeypatch.setattr(組裝, "_執行技能套件啟動協調", lambda _設定: None)
    monkeypatch.setattr(組裝.SQLite敏感稽核儲存庫, "驗證啟動結構", lambda _self: None)
    盒 = {}
    生產, 發布 = _設定(
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3", _安裝可觀測發布(盒),
        lambda: {"fake": object()},
    )
    資源 = 組裝._建立Published資源(生產, 發布, 組裝.延遲外部呼叫編排器())
    assert 盒["工具庫"].取得發布("release-1") is not None
    asyncio.run(資源.關閉())
    assert 盒["工具庫"].取得發布("release-1") is None
