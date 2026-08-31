"""Legacy 敏感稽核入口的 frozen hard-fail compatibility contract。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from 繁中代理.發布介面.呼叫.敏感稽核 import SQLite敏感稽核儲存庫, 敏感稽核錯誤
from 繁中代理.發布介面.呼叫.擷取政策 import 擷取階段, 準備含敏感偵測的呼叫擷取
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _建立資料庫(tmp_path: Path) -> Path:
    路徑 = tmp_path / "legacy-disabled.sqlite3"
    初始化發布介面資料庫(路徑)
    return 路徑


def _有命中結果():
    return 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, {"mail": "legacy@example.test"}, None,
    )


def _零命中結果():
    return 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)


def _斷言固定停用(庫, 結果, *識別碼們):
    with pytest.raises(敏感稽核錯誤, match="^舊式敏感稽核附加已停用$") as info:
        庫.附加偵測事件(結果, *識別碼們)
    assert info.value.args == ("舊式敏感稽核附加已停用",)
    assert info.value.__cause__ is None


@pytest.mark.parametrize("資料庫型別", ["str", "path"])
def test_class與constructor_import_compatibility且初始化不開DB(tmp_path, 資料庫型別):
    路徑 = tmp_path / "尚不存在.sqlite3"
    authority = str(路徑) if 資料庫型別 == "str" else 路徑
    庫 = SQLite敏感稽核儲存庫(authority)
    assert type(庫) is SQLite敏感稽核儲存庫
    assert callable(庫.寫入呼叫交易) and callable(庫.附加偵測事件)
    assert not 路徑.exists()


@pytest.mark.parametrize("結果工廠", [_有命中結果, _零命中結果])
def test_valid_hit與zero_hit都不再建立audit_only_authority(tmp_path, 結果工廠):
    路徑 = _建立資料庫(tmp_path)
    before = 路徑.read_bytes()
    庫 = SQLite敏感稽核儲存庫(路徑)
    _斷言固定停用(庫, 結果工廠(), "inv", "ep", "req")
    assert 路徑.read_bytes() == before
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (0,)


class _毒結果:
    def __getattribute__(self, _名稱):
        raise AssertionError("legacy result 不可被讀取")


@pytest.mark.parametrize("識別碼們", [
    ("", "ep", "req"),
    ("inv", "", "req"),
    ("inv", "ep", ""),
    (object(), object(), object()),
])
def test_hard_fail早於result與identifier_validation(tmp_path, 識別碼們):
    庫 = SQLite敏感稽核儲存庫(tmp_path / "missing.sqlite3")
    _斷言固定停用(庫, _毒結果(), *識別碼們)
    assert not (tmp_path / "missing.sqlite3").exists()


@pytest.mark.parametrize("callback", ["clock", "audit_id", "hit_id", "connection"])
def test_hard_fail早於所有callback與DB_open(tmp_path, callback):
    呼叫 = []

    def 不可呼叫(*_args, **_kwargs):
        呼叫.append(callback)
        raise KeyboardInterrupt("不可觸及", callback)

    kwargs = {
        "時鐘": 不可呼叫 if callback == "clock" else (lambda: 1),
        "識別碼工廠": 不可呼叫 if callback == "audit_id" else (lambda: "audit"),
        "命中識別碼工廠": 不可呼叫 if callback == "hit_id" else (lambda: "hit"),
        "連線工廠": 不可呼叫 if callback == "connection" else sqlite3.connect,
    }
    路徑 = tmp_path / "missing.sqlite3"
    _斷言固定停用(
        SQLite敏感稽核儲存庫(路徑, **kwargs), _有命中結果(), "inv", "ep", "req",
    )
    assert 呼叫 == [] and not 路徑.exists()


def test_repeated_call固定deterministic且不保存legacy_state(tmp_path):
    庫 = SQLite敏感稽核儲存庫(tmp_path / "missing.sqlite3")
    for _ in range(3):
        _斷言固定停用(庫, _有命中結果(), "inv", "ep", "req")
    assert not hasattr(庫, "_legacy_transaction")
    assert not (tmp_path / "missing.sqlite3").exists()
