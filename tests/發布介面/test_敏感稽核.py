"""LOG L07 每個位置命中一筆 sanitized generic audit event 測試。"""

import json
import os
import sqlite3
import threading

import pytest

import 繁中代理.發布介面.呼叫.敏感稽核 as 稽核模組
from 繁中代理.發布介面.呼叫.敏感稽核 import SQLite敏感稽核儲存庫, 敏感稽核錯誤
from 繁中代理.發布介面.呼叫.擷取政策 import (
    擷取階段, 準備含敏感偵測的呼叫擷取,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

秘密們 = ("raw@example.com", "0912-345-678", "A123456789", "4111111111111111", "token_ABC1234567")


def _建立資料庫(tmp_path, 名稱="audit.sqlite3"):
    路徑 = tmp_path / 名稱
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) VALUES ('ep','owner','svc','hit','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
        for invocation_id in ("inv-1", "inv", "inv-a", "inv-b"):
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
                "input_json,created_at) VALUES (?,?,?,?,'pending','{}',0)",
                (invocation_id, "ep", "ver", f"seed-{invocation_id}"),
            )
    return 路徑


def _三命中結果():
    return 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, {"mail": 秘密們[0]}, {"phone": 秘密們[1]},
        response_data={"id": 秘密們[2]},
    )


def _標記三命中結果(標記):
    return 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {
        f"{標記}-a": 秘密們[0], f"{標記}-b": 秘密們[1], f"{標記}-c": 秘密們[2]}, None)


def _列數(路徑, 表格="audit_events"):
    with sqlite3.connect(路徑) as 連線:
        return 連線.execute(f"SELECT count(*) FROM {表格}").fetchone()[0]


def test_三命中依L06順序寫入generic表且只有位置資料(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    結果 = _三命中結果()
    原快照 = repr(結果)
    工廠 = iter(("audit-1", "audit-2", "audit-3"))
    時鐘次數 = [0]
    def 時鐘():
        時鐘次數[0] += 1
        return 7
    識別碼們 = SQLite敏感稽核儲存庫(
        路徑, 時鐘=時鐘, 識別碼工廠=工廠.__next__,
    ).附加偵測事件(結果, "inv-1", "ep", "req-1")
    assert 識別碼們 == ("audit-1", "audit-2", "audit-3") and 時鐘次數 == [1]
    with sqlite3.connect(路徑) as 連線:
        rows = 連線.execute(
            "SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,"
            "request_id,endpoint_id,invocation_id,metadata_json,created_at FROM audit_events ORDER BY rowid"
        ).fetchall()
        dump = "\n".join(連線.iterdump())
    assert [row[:12] for row in rows] == [
        (f"audit-{i}", f"audit-{i}", 7, "published_api.sensitive_data_detected", "success",
         "system", None, "invocation", "inv-1", "req-1", "ep", "inv-1")
        for i in range(1, 4)
    ]
    metadata們 = [json.loads(row[12]) for row in rows]
    assert [值["target"] for 值 in metadata們] == ["input", "metadata", "response_data"]
    assert [值["detector_type"] for 值 in metadata們] == ["email", "phone", "tw_national_id_format"]
    assert all(set(值) == {"warning_code", "target", "detector_type", "json_path", "start", "end"}
               and 值["warning_code"] == "sensitive_data_detected" for 值 in metadata們)
    assert all(row[13] == 7 for row in rows)
    assert repr(結果) == 原快照 and _列數(路徑, "endpoint_redactions") == 0
    assert not any(秘密 in dump + repr(識別碼們) for 秘密 in 秘密們)


def test_空命中零factory時鐘與資料庫寫入(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    次數 = [0]
    def 不可呼叫():
        次數[0] += 1
        raise AssertionError
    結果 = 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
    庫 = SQLite敏感稽核儲存庫(路徑, 時鐘=不可呼叫, 識別碼工廠=不可呼叫)
    原開啟 = 庫._開啟連線
    庫._開啟連線 = 不可呼叫
    assert 庫.附加偵測事件(結果, "inv", "ep", "req") == ()
    assert 次數 == [0] and _列數(路徑) == 0
    庫._開啟連線 = 原開啟


@pytest.mark.parametrize("欄位", ["invocation", "endpoint", "request", "result"])
def test_畸形識別碼或竄改結果在所有副作用前拒絕(tmp_path, 欄位):
    路徑 = _建立資料庫(tmp_path)
    次數 = [0]
    def 不可呼叫():
        次數[0] += 1
        raise AssertionError
    結果 = _三命中結果()
    值們 = ["inv", "ep", "req"]
    if 欄位 == "result":
        object.__setattr__(結果.命中們[0], "JSON路徑", "/bad~")
    else:
        值們[{"invocation": 0, "endpoint": 1, "request": 2}[欄位]] = ""
    庫 = SQLite敏感稽核儲存庫(路徑, 時鐘=不可呼叫, 識別碼工廠=不可呼叫)
    庫._開啟連線 = 不可呼叫
    with pytest.raises(敏感稽核錯誤, match="敏感稽核附加失敗"):
        庫.附加偵測事件(結果, *值們)
    assert 次數 == [0] and _列數(路徑) == 0


@pytest.mark.parametrize("情境", ["duplicate", "foreign_key"])
def test_重複ID與外鍵錯誤皆完整回滾(tmp_path, 情境):
    路徑 = _建立資料庫(tmp_path)
    if 情境 == "duplicate":
        with sqlite3.connect(路徑) as 連線:
            連線.execute(
                "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
                "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) "
                "VALUES ('existing','existing',0,'seed','success','system',NULL,"
                "'invocation','seed','seed','ep',NULL,'{}',0)"
            )
        ids = iter(("new", "existing", "third"))
    else:
        ids = iter(("a", "b", "c"))
    endpoint_id = "ep" if 情境 == "duplicate" else "missing"
    with pytest.raises(敏感稽核錯誤):
        SQLite敏感稽核儲存庫(路徑, 時鐘=lambda: 1,
                         識別碼工廠=ids.__next__).附加偵測事件(
            _三命中結果(), "inv", endpoint_id, "req")
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT id FROM audit_events ORDER BY id").fetchall() == (
            [("existing",)] if 情境 == "duplicate" else [])


@pytest.mark.parametrize("情境", ["missing", "symlink", "wrong_schema"])
def test_只開既有非symlink且精確schema資料庫(tmp_path, 情境):
    路徑 = tmp_path / "runtime.sqlite3"
    if 情境 == "symlink":
        目標 = _建立資料庫(tmp_path, "target.sqlite3")
        os.symlink(目標, 路徑)
    elif 情境 == "wrong_schema":
        with sqlite3.connect(路徑) as 連線:
            連線.execute("CREATE TABLE audit_events(id TEXT)")
    with pytest.raises(敏感稽核錯誤):
        SQLite敏感稽核儲存庫(路徑, 時鐘=lambda: 1,
                         識別碼工廠=lambda: "id").附加偵測事件(
            _三命中結果(), "inv", "ep", "req")
    if 情境 == "missing":
        assert not 路徑.exists()
    if 情境 == "symlink":
        assert _列數(目標) == 0


class _自訂基礎錯誤(BaseException):
    """供 callback 固定化政策測試。"""


@pytest.mark.parametrize("來源", ["id", "clock", "connection"])
def test_自訂BaseException固定無鏈且無原始資料(tmp_path, 來源):
    路徑 = _建立資料庫(tmp_path)
    標記 = 秘密們[4]
    def 投擲(*_參數, **_關鍵字):
        raise _自訂基礎錯誤(標記)
    kwargs = {"時鐘": 投擲 if 來源 == "clock" else (lambda: 1),
              "識別碼工廠": 投擲 if 來源 == "id" else iter(("a", "b", "c")).__next__}
    if 來源 == "connection":
        kwargs["連線工廠"] = 投擲
    with pytest.raises(敏感稽核錯誤) as 資訊:
        SQLite敏感稽核儲存庫(路徑, **kwargs).附加偵測事件(
            _三命中結果(), "inv", "ep", "req")
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None
    assert 標記 not in repr(資訊.value) and _列數(路徑) == 0


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流程保持identity_args且不寫入(tmp_path, 錯誤類型):
    路徑 = _建立資料庫(tmp_path)
    錯誤 = 錯誤類型("control", 17)
    def 投擲():
        raise 錯誤
    with pytest.raises(錯誤類型) as 資訊:
        SQLite敏感稽核儲存庫(路徑, 時鐘=lambda: 1,
                         識別碼工廠=投擲).附加偵測事件(_三命中結果(), "inv", "ep", "req")
    assert 資訊.value is 錯誤 and 資訊.value.args == ("control", 17) and _列數(路徑) == 0


def test_兩個並行批次保留全部獨立ID(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    barrier = threading.Barrier(2)
    錯誤們 = []
    def 寫入(prefix):
        try:
            ids = iter((f"{prefix}-1", f"{prefix}-2", f"{prefix}-3"))
            barrier.wait(timeout=5)
            SQLite敏感稽核儲存庫(路徑, 時鐘=lambda: 2,
                             識別碼工廠=ids.__next__).附加偵測事件(
                _三命中結果(), f"inv-{prefix}", "ep", f"req-{prefix}")
        except BaseException as 錯誤:
            錯誤們.append(錯誤)
    threads = [threading.Thread(target=寫入, args=(值,)) for 值 in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    with sqlite3.connect(路徑) as 連線:
        ids = {row[0] for row in 連線.execute("SELECT id FROM audit_events")}
    assert 錯誤們 == [] and ids == {"a-1", "a-2", "a-3", "b-1", "b-2", "b-3"}


def test_append_only觸發器仍拒絕更新刪除(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    SQLite敏感稽核儲存庫(路徑, 時鐘=lambda: 1,
                     識別碼工廠=iter(("a", "b", "c")).__next__).附加偵測事件(
        _三命中結果(), "inv", "ep", "req")
    with sqlite3.connect(路徑) as 連線:
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute("UPDATE audit_events SET request_id='x'")
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute("DELETE FROM audit_events")
    assert _列數(路徑) == 3


class _失敗交易連線:
    """在第二筆 INSERT 投擲並觀測明確交易狀態機。"""

    def __init__(self, 錯誤):
        self.錯誤 = 錯誤
        self.寫入數 = self.關閉數 = self.回滾數 = self.提交數 = 0
        self.進入數 = self.離開數 = 0
        self.順序 = []

    def __enter__(self):
        self.進入數 += 1
        return self

    def __exit__(self, 類型, _值, _追蹤):
        self.離開數 += 1
        return True

    def execute(self, SQL, _參數=None):
        if SQL == "BEGIN IMMEDIATE":
            self.順序.append("begin")
        elif "sqlite_master" in SQL:
            self.順序.append("schema")
            return iter(稽核模組._稽核結構)
        elif "published_api_schema_migrations" in SQL:
            return iter(稽核模組._必要遷移)
        if SQL.startswith("INSERT"):
            self.寫入數 += 1
            self.順序.append(f"insert-{self.寫入數}")
            if self.寫入數 == 2:
                raise self.錯誤
        return self

    def commit(self):
        self.提交數 += 1

    def rollback(self):
        self.回滾數 += 1
        self.順序.append("rollback")

    def close(self):
        self.關閉數 += 1
        self.順序.append("close")


@pytest.mark.parametrize("錯誤類型", [sqlite3.OperationalError, _自訂基礎錯誤,
                                    KeyboardInterrupt, SystemExit, GeneratorExit])
def test_execute失敗明確回滾再精確關閉一次且不用context_manager(monkeypatch, tmp_path, 錯誤類型):
    路徑 = _建立資料庫(tmp_path)
    錯誤 = 錯誤類型("execute-control")
    假連線 = _失敗交易連線(錯誤)
    庫 = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=iter(("a", "b", "c")).__next__)
    monkeypatch.setattr(庫, "_開啟連線", lambda: 假連線)
    預期 = 錯誤類型 if 錯誤類型 in (KeyboardInterrupt, SystemExit, GeneratorExit) else 敏感稽核錯誤
    with pytest.raises(預期) as 資訊:
        庫.附加偵測事件(_三命中結果(), "inv", "ep", "req")
    if 預期 is not 敏感稽核錯誤:
        assert 資訊.value is 錯誤
    assert 假連線.順序 == ["begin", "schema", "insert-1", "insert-2", "rollback", "close"]
    assert 假連線.回滾數 == 假連線.關閉數 == 1 and 假連線.提交數 == 0
    assert 假連線.進入數 == 假連線.離開數 == 0


class _門控連線:
    """在 BEGIN 前或 BEGIN 後 schema 查詢前建立 deterministic gate。"""

    def __init__(self, 連線, 已到達, 繼續, 階段):
        self._連線, self._已到達, self._繼續, self._階段 = 連線, 已到達, 繼續, 階段

    def execute(self, SQL, 參數=None):
        if ((self._階段 == "before" and SQL == "BEGIN IMMEDIATE")
                or (self._階段 == "after" and "sqlite_master" in SQL)):
            self._已到達.set()
            assert self._繼續.wait(timeout=5)
        return self._連線.execute(SQL) if 參數 is None else self._連線.execute(SQL, 參數)

    def commit(self):
        return self._連線.commit()

    def rollback(self):
        return self._連線.rollback()

    def close(self):
        return self._連線.close()


def _門控附加(路徑, 階段, 已到達, 繼續, 錯誤們):
    def 工廠(*參數, **關鍵字):
        return _門控連線(sqlite3.connect(*參數, **關鍵字), 已到達, 繼續, 階段)
    try:
        SQLite敏感稽核儲存庫(
            路徑, 時鐘=lambda: 1, 識別碼工廠=iter(("a", "b", "c")).__next__,
            連線工廠=工廠,
        ).附加偵測事件(_三命中結果(), "inv", "ep", "req")
    except BaseException as 錯誤:
        錯誤們.append(錯誤)


def test_BEGIN後schema鎖阻止第二連線DDL直到稽核提交(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    已到達, 繼續, 錯誤們 = threading.Event(), threading.Event(), []
    執行緒 = threading.Thread(target=_門控附加, args=(路徑, "after", 已到達, 繼續, 錯誤們))
    執行緒.start()
    assert 已到達.wait(timeout=5)
    with sqlite3.connect(路徑, timeout=0.05) as 競爭連線:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            競爭連線.execute("DROP TRIGGER audit_events_no_update")
    繼續.set()
    執行緒.join(timeout=5)
    assert not 執行緒.is_alive() and 錯誤們 == [] and _列數(路徑) == 3
    with sqlite3.connect(路徑) as 競爭連線:
        競爭連線.execute("DROP TRIGGER audit_events_no_update")


def test_BEGIN前schema已漂移則固定失敗且零寫入(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    已到達, 繼續, 錯誤們 = threading.Event(), threading.Event(), []
    執行緒 = threading.Thread(target=_門控附加, args=(路徑, "before", 已到達, 繼續, 錯誤們))
    執行緒.start()
    assert 已到達.wait(timeout=5)
    with sqlite3.connect(路徑) as 競爭連線:
        競爭連線.execute("DROP TRIGGER audit_events_no_update")
    繼續.set()
    執行緒.join(timeout=5)
    assert not 執行緒.is_alive() and len(錯誤們) == 1
    assert type(錯誤們[0]) is 敏感稽核錯誤 and _列數(路徑) == 0


class _關閉失敗連線(_門控連線):
    def __init__(self, 連線, 錯誤):
        self._連線, self._錯誤 = 連線, 錯誤

    def execute(self, SQL, 參數=None):
        return self._連線.execute(SQL) if 參數 is None else self._連線.execute(SQL, 參數)

    def close(self):
        self._連線.close()
        raise self._錯誤


@pytest.mark.parametrize("錯誤類型", [OSError, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_COMMIT後關閉失敗遵守durable_truth(tmp_path, 錯誤類型):
    路徑 = _建立資料庫(tmp_path)
    錯誤 = 錯誤類型("close", 9)
    def 工廠(*參數, **關鍵字):
        return _關閉失敗連線(sqlite3.connect(*參數, **關鍵字), 錯誤)
    庫 = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=iter(("a", "b", "c")).__next__, 連線工廠=工廠)
    if 錯誤類型 is OSError:
        assert 庫.附加偵測事件(_三命中結果(), "inv", "ep", "req") == ("a", "b", "c")
    else:
        with pytest.raises(錯誤類型) as 資訊:
            庫.附加偵測事件(_三命中結果(), "inv", "ep", "req")
        assert 資訊.value is 錯誤 and 資訊.value.args == ("close", 9)
    assert _列數(路徑) == 3


def _含標記(值, 標記, 已訪問):
    if id(值) in 已訪問:
        return False
    已訪問.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) in (tuple, list, set, frozenset):
        return any(_含標記(項目, 標記, 已訪問) for 項目 in 值)
    if type(值) is dict:
        return any(_含標記(鍵, 標記, 已訪問) or _含標記(項目, 標記, 已訪問)
                   for 鍵, 項目 in 值.items())
    if isinstance(值, BaseException):
        return _含標記(值.args, 標記, 已訪問)
    if type(值).__module__ == __name__ and hasattr(值, "__dict__"):
        return _含標記(vars(值), 標記, 已訪問)
    return False


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流程traceback每個production_local使用fresh_visited皆無標記(tmp_path, 錯誤類型):
    路徑, 標記 = _建立資料庫(tmp_path), "valid-marker-secret"
    錯誤 = 錯誤類型(標記, 23)
    def 投擲():
        raise 錯誤
    with pytest.raises(錯誤類型) as 資訊:
        SQLite敏感稽核儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=投擲).附加偵測事件(
            _三命中結果(), f"inv-{標記}", "ep", f"req-{標記}")
    assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 23)
    追蹤 = 資訊.value.__traceback__
    while 追蹤 is not None:
        if 追蹤.tb_frame.f_code.co_filename == 稽核模組.__file__:
            for 值 in tuple(追蹤.tb_frame.f_locals.values()):
                assert not _含標記(值, 標記, set()), 追蹤.tb_frame.f_code.co_name
        追蹤 = 追蹤.tb_next


class _階段失敗連線:
    """於指定交易階段投擲並記錄每項 cleanup 的精確次數。"""

    def __init__(self, 階段, 錯誤, 標記):
        self.階段, self.錯誤, self.標記 = 階段, 錯誤, 標記
        self.寫入數 = self.提交數 = self.回滾數 = self.關閉數 = 0

    def execute(self, SQL, _參數=None):
        if SQL == "BEGIN IMMEDIATE" and self.階段 == "begin":
            raise self.錯誤
        if "sqlite_master" in SQL:
            if self.階段 == "schema":
                raise self.錯誤
            return iter(稽核模組._稽核結構)
        if "published_api_schema_migrations" in SQL:
            return iter(稽核模組._必要遷移)
        if SQL.startswith("INSERT"):
            self.寫入數 += 1
            if self.階段 == "insert" and self.寫入數 == 2:
                raise self.錯誤
            if self.階段 in ("rollback", "close") and self.寫入數 == 2:
                raise ValueError(self.標記)
        return self

    def commit(self):
        self.提交數 += 1
        if self.階段 == "commit":
            raise self.錯誤

    def rollback(self):
        self.回滾數 += 1
        if self.階段 == "rollback":
            raise self.錯誤

    def close(self):
        self.關閉數 += 1
        if self.階段 in ("close", "postcommit_close"):
            raise self.錯誤


def _斷言production追蹤無標記(錯誤, 標記):
    """逐 production frame/local 使用全新 visited 驗證遞迴 oracle。"""
    追蹤 = 錯誤.__traceback__
    while 追蹤 is not None:
        if 追蹤.tb_frame.f_code.co_filename == 稽核模組.__file__:
            for 值 in tuple(追蹤.tb_frame.f_locals.values()):
                assert not _含標記(值, 標記, set()), 追蹤.tb_frame.f_code.co_name
        追蹤 = 追蹤.tb_next


@pytest.mark.parametrize("階段", ["begin", "schema", "insert", "commit", "rollback", "close",
                                    "postcommit_close"])
@pytest.mark.parametrize("錯誤類型", [_自訂基礎錯誤, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_每個交易callback的BaseException優先序與遞迴traceback清理(tmp_path, 階段, 錯誤類型):
    路徑, 標記 = _建立資料庫(tmp_path), f"marker-{階段}-{錯誤類型.__name__}"
    錯誤 = 錯誤類型(標記, 31)
    假連線 = _階段失敗連線(階段, 錯誤, 標記)
    庫 = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=iter(("a", "b", "c")).__next__)
    庫._開啟連線 = lambda: 假連線
    是控制 = 錯誤類型 in (KeyboardInterrupt, SystemExit, GeneratorExit)
    if 階段 == "postcommit_close" and not 是控制:
        assert 庫.附加偵測事件(_標記三命中結果(標記), f"inv-{標記}", "ep", f"req-{標記}") == ("a", "b", "c")
        assert 假連線.提交數 == 假連線.關閉數 == 1 and 假連線.回滾數 == 0
        return
    預期 = 錯誤類型 if 是控制 else 敏感稽核錯誤
    with pytest.raises(預期) as 資訊:
        庫.附加偵測事件(_標記三命中結果(標記), f"inv-{標記}", "ep", f"req-{標記}")
    if 是控制:
        assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 31)
    assert 假連線.關閉數 == 1
    assert 假連線.回滾數 == (0 if 階段 in ("begin", "postcommit_close") else 1)
    assert 假連線.提交數 == (1 if 階段 in ("commit", "postcommit_close") else 0)
    _斷言production追蹤無標記(資訊.value, 標記)


@pytest.mark.parametrize("來源", ["id", "clock", "connection"])
@pytest.mark.parametrize("錯誤類型", [_自訂基礎錯誤, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_工廠callback完整BaseException矩陣與遞迴traceback清理(tmp_path, 來源, 錯誤類型):
    路徑, 標記 = _建立資料庫(tmp_path), f"marker-{來源}-{錯誤類型.__name__}"
    錯誤 = 錯誤類型(標記, 41)
    def 投擲(*_參數, **_關鍵字):
        raise 錯誤
    庫 = SQLite敏感稽核儲存庫(
        路徑, 識別碼工廠=投擲 if 來源 == "id" else iter(("a", "b", "c")).__next__,
        時鐘=投擲 if 來源 == "clock" else (lambda: 1),
        連線工廠=投擲 if 來源 == "connection" else sqlite3.connect)
    是控制 = 錯誤類型 in (KeyboardInterrupt, SystemExit, GeneratorExit)
    with pytest.raises(錯誤類型 if 是控制 else 敏感稽核錯誤) as 資訊:
        庫.附加偵測事件(_標記三命中結果(標記), f"inv-{標記}", "ep", f"req-{標記}")
    if 是控制:
        assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 41)
    _斷言production追蹤無標記(資訊.value, 標記)


def test_遞迴marker_oracle確實對已知洩漏RED():
    標記 = "oracle-known-leak"
    assert _含標記({"outer": [{"inner": 標記}]}, 標記, set())


def _斷言例外圖無標記(錯誤, 標記):
    """遞迴檢查公開例外鏈，並沿每個例外的 production traceback 檢查 locals。"""
    待查, 已查 = [錯誤], set()
    while 待查:
        目前 = 待查.pop()
        if 目前 is None or id(目前) in 已查:
            continue
        已查.add(id(目前))
        assert not _含標記(目前.args, 標記, set())
        _斷言production追蹤無標記(目前, 標記)
        待查.extend((目前.__cause__, 目前.__context__))


class _雙失敗連線(_階段失敗連線):
    """以 distinct primary/cleanup 例外驗證清理控制流程不攜帶 primary。"""

    def __init__(self, primary, rollback=None, close=None):
        super().__init__("insert", primary, "unused")
        self._rollback錯誤, self._close錯誤 = rollback, close

    def rollback(self):
        self.回滾數 += 1
        if self._rollback錯誤 is not None:
            raise self._rollback錯誤

    def close(self):
        self.關閉數 += 1
        if self._close錯誤 is not None:
            raise self._close錯誤


@pytest.mark.parametrize("清理階段", ["rollback", "close"])
def test_普通primary遇清理控制傳回exact且例外圖無primary標記(tmp_path, 清理階段):
    路徑, 標記 = _建立資料庫(tmp_path), "PRIMARY-SECRET"
    primary = RuntimeError(標記)
    cleanup = KeyboardInterrupt("CLEANUP", 41)
    假連線 = _雙失敗連線(primary, **{清理階段: cleanup})
    庫 = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=iter(("a", "b", "c")).__next__)
    庫._開啟連線 = lambda: 假連線
    with pytest.raises(KeyboardInterrupt) as 資訊:
        庫.附加偵測事件(_三命中結果(), "inv", "ep", "req")
    assert 資訊.value is cleanup and cleanup.args == ("CLEANUP", 41)
    assert cleanup.__cause__ is None and cleanup.__context__ is None
    _斷言例外圖無標記(cleanup, 標記)


def test_開啟普通primary遇close_SystemExit傳回exact且無primary標記(tmp_path):
    路徑, 標記 = _建立資料庫(tmp_path), "OPEN-PRIMARY-SECRET"
    cleanup = SystemExit("OPEN-CLEANUP", 42)
    class 假開啟連線:
        def execute(self, _SQL):
            raise RuntimeError(標記)
        def close(self):
            raise cleanup
    庫 = SQLite敏感稽核儲存庫(路徑, 連線工廠=lambda *_參數, **_關鍵字: 假開啟連線())
    with pytest.raises(SystemExit) as 資訊:
        庫._開啟連線()
    assert 資訊.value is cleanup and cleanup.args == ("OPEN-CLEANUP", 42)
    assert cleanup.__cause__ is None and cleanup.__context__ is None
    _斷言例外圖無標記(cleanup, 標記)


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_預先串鏈primary控制保持exact並清鏈且不被cleanup取代(tmp_path, 錯誤類型):
    路徑, 標記 = _建立資料庫(tmp_path), "CHAINED-PRIMARY-SECRET"
    primary = 錯誤類型("PRIMARY-CONTROL", 43)
    primary.__cause__ = RuntimeError(標記)
    primary.__context__ = ValueError(標記)
    假連線 = _雙失敗連線(
        primary, KeyboardInterrupt("ROLLBACK-CLEANUP"), SystemExit("CLOSE-CLEANUP"))
    庫 = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=iter(("a", "b", "c")).__next__)
    庫._開啟連線 = lambda: 假連線
    with pytest.raises(錯誤類型) as 資訊:
        庫.附加偵測事件(_三命中結果(), "inv", "ep", "req")
    assert 資訊.value is primary and primary.args == ("PRIMARY-CONTROL", 43)
    assert primary.__cause__ is None and primary.__context__ is None
    _斷言例外圖無標記(primary, 標記)
