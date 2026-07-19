"""CP3 Controller startup cleanup、draining與非阻塞生命週期回歸。"""

import asyncio
import threading
import time

import pytest

import 繁中代理.發布介面.生產Web代理 as 組裝
from 繁中代理.發布介面.Web代理服務 import Web代理服務, Web服務不可用
from 繁中代理.發布介面.生產Web代理 import 延遲Web代理服務, 生產Web代理資源
from 繁中代理.發布介面.設定 import 生產設定


class _連線:
    def __init__(self, 名稱, 紀錄, 錯誤=None, 延遲=0):
        self.名稱, self.紀錄, self.錯誤, self.延遲 = 名稱, 紀錄, 錯誤, 延遲

    def close(self):
        time.sleep(self.延遲)
        self.紀錄.append(self.名稱)
        if self.錯誤:
            raise self.錯誤


class _庫:
    def __init__(self, 連線):
        self.連線 = 連線


@pytest.mark.parametrize("第一錯誤,第二錯誤", [
    (RuntimeError("close-user"), None),
    (None, RuntimeError("close-session")),
    (RuntimeError("close-user"), RuntimeError("close-session")),
])
def test_startup失敗attempt兩個close且保留原始identity與traceback(tmp_path, monkeypatch, 第一錯誤, 第二錯誤):
    """migration ordinary failure永遠保留，任一close失敗不阻止下一個attempt。"""
    紀錄 = []
    使用者 = _庫(_連線("user", 紀錄, 第一錯誤))
    工作階段 = _庫(_連線("session", 紀錄, 第二錯誤))
    monkeypatch.setattr(組裝, "使用者庫", lambda _path: 使用者)
    monkeypatch.setattr(組裝, "工作階段庫", lambda _path: 工作階段)
    原始錯誤 = LookupError("startup-original")

    def 遷移失敗(_path):
        raise 原始錯誤

    monkeypatch.setattr(組裝, "初始化發布介面資料庫", 遷移失敗)
    設定 = 生產設定(tmp_path / "db.sqlite3", ("https://web.example",), "fake", "fake")
    with pytest.raises(LookupError) as 捕捉:
        組裝._建立生產Web代理資源(設定, 延遲Web代理服務())
    assert 捕捉.value is 原始錯誤
    assert any(frame.name == "遷移失敗" for frame in 捕捉.traceback)
    assert 紀錄 == ["user", "session"]


def test_startup_cleanup的control_flow優先於ordinary_startup(tmp_path, monkeypatch):
    """ordinary startup不能吞掉cleanup control-flow，且仍attempt第二個close。"""
    紀錄 = []
    monkeypatch.setattr(組裝, "使用者庫", lambda _path: _庫(_連線("user", 紀錄, KeyboardInterrupt())))
    monkeypatch.setattr(組裝, "工作階段庫", lambda _path: _庫(_連線("session", 紀錄)))
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _path: (_ for _ in ()).throw(LookupError()))
    設定 = 生產設定(tmp_path / "db.sqlite3", ("https://web.example",), "fake", "fake")
    with pytest.raises(KeyboardInterrupt):
        組裝._建立生產Web代理資源(設定, 延遲Web代理服務())
    assert 紀錄 == ["user", "session"]


def test_service安裝失敗也清理兩個connection(tmp_path, monkeypatch):
    """migration後service slot install失敗仍完整清理。"""
    紀錄 = []
    monkeypatch.setattr(組裝, "使用者庫", lambda _path: _庫(_連線("user", 紀錄)))
    monkeypatch.setattr(組裝, "工作階段庫", lambda _path: _庫(_連線("session", 紀錄)))
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _path: ())
    延遲 = 延遲Web代理服務()
    monkeypatch.setattr(延遲, "安裝", lambda _service: (_ for _ in ()).throw(RuntimeError("install")))
    設定 = 生產設定(tmp_path / "db.sqlite3", ("https://web.example",), "fake", "fake")
    with pytest.raises(RuntimeError, match="install"):
        組裝._建立生產Web代理資源(設定, 延遲)
    assert 紀錄 == ["user", "session"]


def test_draining_barrier拒新lease並等待既有call後才close():
    """真實thread交錯證明close不跨越active service lease。"""
    已進入, 可返回 = threading.Event(), threading.Event()

    class 工作庫:
        第一次 = True

        def 列出工作階段(self, **_kwargs):
            if self.第一次:
                self.第一次 = False
                已進入.set()
                assert 可返回.wait(2)
            return []

    服務 = Web代理服務(工作庫(), object(), lambda **_kwargs: None)
    延遲 = 延遲Web代理服務(); 延遲.安裝(服務)
    紀錄 = []
    資源 = 生產Web代理資源(延遲, 服務, _庫(_連線("session", 紀錄)), _庫(_連線("user", 紀錄)))
    呼叫 = threading.Thread(target=lambda: 延遲.列出工作階段("user"))
    關閉 = threading.Thread(target=lambda: asyncio.run(資源.關閉()))
    呼叫.start(); assert 已進入.wait(1); 關閉.start()
    for _ in range(100):
        try:
            延遲.列出工作階段("new")
        except Web服務不可用:
            break
        time.sleep(.005)
    else:
        pytest.fail("shutdown未進入draining")
    assert 紀錄 == [] and 關閉.is_alive()
    可返回.set(); 呼叫.join(2); 關閉.join(2)
    assert not 呼叫.is_alive() and not 關閉.is_alive()
    assert 紀錄 == ["user", "session"]
