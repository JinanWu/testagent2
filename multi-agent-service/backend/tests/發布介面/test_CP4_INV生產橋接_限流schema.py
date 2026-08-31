"""CP4 INV 生產限流交易與釘選 schema 邊界測試。"""

import copy
import sqlite3
import traceback
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.呼叫.生產橋接 import (
    SQLite雙層限流器,
    生產橋接錯誤,
    驗證釘選輸入結構,
    驗證釘選輸出結構,
)


def _資料庫(tmp_path):
    """建立含正式遷移表的既有一般 SQLite 檔。"""
    路徑 = tmp_path / "rate.sqlite3"
    初始化發布介面資料庫(路徑)
    return 路徑


class _釘選:
    """每次只回傳 fresh schema tree 的最小 pin 測試替身。"""

    def __init__(self, 輸入, 輸出):
        self._輸入, self._輸出, self.次數 = 輸入, 輸出, 0

    def 取得版本快照(self):
        """回傳與前次引用脫離的 schema 快照。"""
        self.次數 += 1
        return SimpleNamespace(
            input_schema=copy.deepcopy(self._輸入), response_schema=copy.deepcopy(self._輸出),
        )


def test_限流器並行提交不遺失雙層計數且使用既有資料庫(tmp_path):
    """BEGIN IMMEDIATE 序列化競態，二十次提交應各自得到唯一遞增計數。"""
    路徑 = _資料庫(tmp_path)
    限流器 = SQLite雙層限流器(路徑)
    with ThreadPoolExecutor(max_workers=8) as 執行池:
        決策 = list(執行池.map(lambda _: 限流器.提交("ep", "cred", 100, 100, 1.0), range(20)))
    assert sorted(項.端點計數 for 項 in 決策) == list(range(1, 21))
    assert sorted(項.憑證計數 for 項 in 決策) == list(range(1, 21))
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT scope_type,request_count FROM rate_limit_counters ORDER BY scope_type").fetchall() == [
            ("credential", 20), ("endpoint", 20),
        ]


class _追蹤連線:
    """保留交易與關閉順序，同時代理真實 sqlite 連線。"""

    def __init__(self, 原始):
        self._原始, self.語句, self.關閉次數 = 原始, [], 0

    @property
    def in_transaction(self):
        """回傳底層真實交易狀態。"""
        return self._原始.in_transaction

    def execute(self, 語句, 參數=()):
        """記錄後執行 SQL。"""
        self.語句.append(語句)
        return self._原始.execute(語句, 參數)

    def close(self):
        """記錄並關閉底層連線。"""
        self.關閉次數 += 1
        return self._原始.close()


def test_限流普通失敗rollback且永遠close並固定錯誤(monkeypatch, tmp_path):
    """primitive 失敗不得提交半筆端點計數，錯誤文字也不得含識別碼。"""
    路徑 = _資料庫(tmp_path)
    盒子 = []

    def 連線工廠(*參數, **命名):
        代理 = _追蹤連線(sqlite3.connect(*參數, **命名)); 盒子.append(代理); return 代理

    def 失敗操作(連線, *參數):
        連線.execute(
            "INSERT INTO rate_limit_counters(scope_type,scope_id,window_start,request_count,updated_at) "
            "VALUES ('endpoint','ep-secret',0,1,0)"
        )
        raise RuntimeError("cred-secret")

    monkeypatch.setattr("繁中代理.發布介面.呼叫.生產橋接.增加雙層計數並判定", 失敗操作)
    with pytest.raises(生產橋接錯誤, match="^限流提交失敗$") as 捕捉:
        SQLite雙層限流器(路徑, connection_factory=連線工廠).提交(
            "ep-secret", "cred-secret", 10, 10, 1.0,
        )
    assert 盒子[0].語句[0] == "BEGIN IMMEDIATE" and "ROLLBACK" in 盒子[0].語句
    assert 盒子[0].關閉次數 == 1 and "secret" not in repr(捕捉.value)
    for 框架, _ in traceback.walk_tb(捕捉.value.__traceback__):
        if 框架.f_globals.get("__name__") == "繁中代理.發布介面.呼叫.生產橋接":
            assert "ep-secret" not in repr(tuple(框架.f_locals.values()))
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT count(*) FROM rate_limit_counters").fetchone() == (0,)


def test_限流控制流程保留identity且rollback_close(monkeypatch, tmp_path):
    """控制流程不得固定化，已開始的交易仍須回滾並關閉。"""
    路徑, 盒子, 控制 = _資料庫(tmp_path), [], KeyboardInterrupt("停止")
    def 連線工廠(*參數, **命名):
        代理 = _追蹤連線(sqlite3.connect(*參數, **命名)); 盒子.append(代理); return 代理
    def 中斷(連線, *參數):
        連線.execute(
            "INSERT INTO rate_limit_counters VALUES ('endpoint','temporary',0,1,0)"
        )
        raise 控制
    monkeypatch.setattr("繁中代理.發布介面.呼叫.生產橋接.增加雙層計數並判定", 中斷)
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        SQLite雙層限流器(路徑, connection_factory=連線工廠).提交("ep-secret", "cred-secret", 1, 1, 1.0)
    assert 捕捉.value is 控制 and "ROLLBACK" in 盒子[0].語句 and 盒子[0].關閉次數 == 1


def test_schema使用每次fresh釘選樹且回傳exact_bool():
    """輸入與輸出各自從 pin 重建 Draft 2020-12 schema，不保存可變引用。"""
    釘選 = _釘選(
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
         "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": False},
        {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]},
    )
    結果 = [
        驗證釘選輸入結構(釘選, {"q": "ok"}), 驗證釘選輸入結構(釘選, {"q": 1}),
        驗證釘選輸出結構(釘選, {"answer": 1}), 驗證釘選輸出結構(釘選, {"answer": "no"}),
    ]
    assert 結果 == [True, False, True, False] and all(type(項) is bool for 項 in 結果)
    assert 釘選.次數 == 4


def test_schema拒絕remote_ref且不執行custom_format_checker():
    """遠端參照固定為 false，未知 format 僅作 annotation 且不觸發外部副作用。"""
    遠端 = _釘選({"$ref": "https://attacker.invalid/schema"}, {"type": "object"})
    assert 驗證釘選輸入結構(遠端, {}) is False
    釘選 = _釘選({"type": "string", "format": "任意自訂格式"}, {"type": "object"})
    assert 驗證釘選輸入結構(釘選, "內容") is True
