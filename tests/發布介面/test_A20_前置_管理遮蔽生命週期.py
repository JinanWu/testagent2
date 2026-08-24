"""A20 管理遮蔽 authority 的 generation、drain 與 trusted installer 契約。"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.治理.管理遮蔽治理 import (
    _管理遮蔽安裝能力,
    管理遮蔽內部失敗,
    管理遮蔽請求,
    管理遮蔽成功,
    管理遮蔽治理權限,
)
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.治理.遮蔽命令 import SQLite遮蔽命令服務
from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import 網頁工作階段服務


def _建立權限(tmp_path: Path) -> 管理遮蔽治理權限:
    路徑 = tmp_path / "auth.sqlite3"
    使用者 = 使用者庫(路徑)
    使用者.連線.close()
    設定 = 網頁安全設定(
        ("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60,
    )
    return 管理遮蔽治理權限(網頁工作階段服務(路徑, 有效秒數=60), 設定)


def _建立服務(tmp_path: Path):
    服務 = SQLite不可逆遮蔽服務(os.path.realpath(tmp_path / "published.sqlite3"))
    命令 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=lambda: "redaction-1",
        稽核事件識別碼工廠=lambda: "audit-1",
        請求識別碼工廠=lambda: "request-1",
        時鐘=lambda: 1.0,
    )
    return 服務, 命令


def _請求() -> 管理遮蔽請求:
    return 管理遮蔽請求(
        管理員識別碼="admin-1",
        冪等鍵="idem-1",
        端點識別碼="endpoint-1",
        呼叫識別碼="invocation-1",
        目標類型="tool_result",
        目標列識別碼="tool-1",
        JSON路徑="/secret",
        原因="privacy request",
    )


def test_A20_L1_same_object_ABA與stale_generation不清除新安裝(monkeypatch, tmp_path):
    權限 = _建立權限(tmp_path)
    服務, 命令 = _建立服務(tmp_path)
    呼叫 = []

    def 執行命令(self, command, **kwargs):
        del self, command, kwargs
        呼叫.append("called")
        return object()

    monkeypatch.setattr(SQLite不可逆遮蔽服務, "執行命令", 執行命令)
    g1 = 權限.安裝(服務, 命令)
    assert 權限.解除(g1) is None
    g2 = 權限.安裝(服務, 命令)
    assert g2 != g1
    assert 權限.解除(g1) is None
    assert type(權限.執行(_請求())) is 管理遮蔽內部失敗
    assert 呼叫 == ["called"]
    assert 權限.解除(g2) is None
    assert type(權限.執行(_請求())) is 管理遮蔽內部失敗


@pytest.mark.parametrize("token", [True, False, None, "1", 0, -1])
def test_A20_L1_invalid_generation固定拒絕(token, tmp_path):
    權限 = _建立權限(tmp_path)
    with pytest.raises(ValueError, match="^管理遮蔽治理解除無效$") as caught:
        權限.解除(token)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_A20_L1_unknown_positive_generation是safe_noop(tmp_path):
    權限 = _建立權限(tmp_path)
    assert 權限.解除(1) is None
    assert 權限.解除(10**30) is None


def test_A20_L2_active_drain封鎖新lease且兩個caller共同等待(monkeypatch, tmp_path):
    權限 = _建立權限(tmp_path)
    服務, 命令 = _建立服務(tmp_path)
    已進入 = threading.Event()
    可離開 = threading.Event()

    def 執行命令(self, command, **kwargs):
        del self, command, kwargs
        已進入.set()
        assert 可離開.wait(5)
        return object()

    monkeypatch.setattr(SQLite不可逆遮蔽服務, "執行命令", 執行命令)
    generation = 權限.安裝(服務, 命令)
    operation = threading.Thread(target=lambda: 權限.執行(_請求()))
    operation.start()
    assert 已進入.wait(2)

    完成: list[str] = []
    def clear(label: str) -> None:
        權限.解除(generation)
        完成.append(label)

    a = threading.Thread(target=clear, args=("a",))
    b = threading.Thread(target=clear, args=("b",))
    a.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if type(權限.執行(_請求())) is 管理遮蔽內部失敗:
            break
        time.sleep(0.01)
    else:
        pytest.fail("drain未封鎖新lease")
    b.start()
    time.sleep(0.05)
    assert a.is_alive() and b.is_alive() and 完成 == []

    可離開.set()
    operation.join(2); a.join(2); b.join(2)
    assert not operation.is_alive() and not a.is_alive() and not b.is_alive()
    assert sorted(完成) == ["a", "b"]
    assert type(權限.執行(_請求())) is 管理遮蔽內部失敗


def test_A20_L3_opaque_attempt可收斂已發布authority(tmp_path):
    權限 = _建立權限(tmp_path)
    服務, 命令 = _建立服務(tmp_path)
    嘗試 = _管理遮蔽安裝能力.建立安裝嘗試(權限)
    _管理遮蔽安裝能力.準備安裝(權限, 嘗試, 服務, 命令)
    generation = _管理遮蔽安裝能力.發布已準備安裝(權限, 嘗試)
    assert type(generation) is int and generation >= 1

    assert _管理遮蔽安裝能力.依安裝嘗試撤銷並等待(權限, 嘗試) is None
    assert _管理遮蔽安裝能力.依安裝嘗試撤銷並等待(權限, 嘗試) is None
    assert type(權限.執行(_請求())) is 管理遮蔽內部失敗


def test_A20_L3_sealed_capability不受class_method_monkeypatch影響(monkeypatch, tmp_path):
    權限 = _建立權限(tmp_path)
    服務, 命令 = _建立服務(tmp_path)
    嘗試 = _管理遮蔽安裝能力.建立安裝嘗試(權限)

    def hostile(*_args, **_kwargs):
        raise AssertionError("runtime class lookup")

    for 名稱 in ("_建立安裝嘗試", "_準備安裝", "_發布已準備安裝", "_依安裝嘗試撤銷並等待"):
        monkeypatch.setattr(管理遮蔽治理權限, 名稱, hostile)
    _管理遮蔽安裝能力.準備安裝(權限, 嘗試, 服務, 命令)
    _管理遮蔽安裝能力.發布已準備安裝(權限, 嘗試)
    _管理遮蔽安裝能力.依安裝嘗試撤銷並等待(權限, 嘗試)
    assert type(權限.執行(_請求())) is 管理遮蔽內部失敗


def test_A20_L3_fake_attempt固定拒絕(tmp_path):
    權限 = _建立權限(tmp_path)
    with pytest.raises(ValueError, match="^管理遮蔽治理安裝嘗試無效$"):
        _管理遮蔽安裝能力.依安裝嘗試撤銷並等待(權限, object())
