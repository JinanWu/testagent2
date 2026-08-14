"""GOV G05 唯讀規劃的控制流程、exact-once cleanup 與有界結構釘選。"""

from contextlib import closing
import sqlite3
import traceback

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.治理 import 保存期限 as 模組
from 繁中代理.發布介面.治理.保存期限 import SQLite保存候選規劃器, 保存候選規劃錯誤


class 自訂Base(BaseException):
    pass


class 敵對中斷(KeyboardInterrupt):
    def __setattr__(self, _名稱, _值):
        raise RuntimeError("禁止覆寫")


class 敵對離開(SystemExit):
    def __setattr__(self, _名稱, _值):
        raise RuntimeError("禁止覆寫")


class 敵對生成器離開(GeneratorExit):
    def __setattr__(self, _名稱, _值):
        raise RuntimeError("禁止覆寫")


@pytest.fixture
def 資料庫(tmp_path):
    路徑 = tmp_path / "PRIVATE_PATH_G05.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('sa',0,NULL)")
        連線.execute("INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES('ep','owner','sa','slug','active',0,0)")
        連線.execute("INSERT INTO published_endpoint_versions VALUES('ver','ep',1,'r','s','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)")
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
        連線.execute("INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) VALUES('PRIVATE_INVOCATION_G05','ep','ver','req','succeeded','{}',0)")
    return 路徑


class 計數游標:
    def __init__(self, 游標, 代理, 是ledger=False):
        self._游標 = 游標
        self._代理 = 代理
        self._是ledger = 是ledger

    def __iter__(self):
        for 列 in self._游標:
            if self._是ledger:
                self._代理.ledger讀列 += 1
            yield 列

    def __getattr__(self, 名稱):
        return getattr(self._游標, 名稱)


class SQLite代理:
    def __init__(self, 連線, 階段="", 主要=None, 回滾=None, 關閉=None):
        self._連線 = 連線
        self.階段 = 階段
        self.主要 = 主要
        self.回滾錯誤 = 回滾
        self.關閉錯誤 = 關閉
        self.回滾次數 = self.關閉次數 = self.ledger讀列 = 0

    def execute(self, SQL, *參數):
        命中 = {
            "pragma": SQL == "PRAGMA query_only=ON",
            "begin": SQL == "BEGIN",
            "path": SQL == "PRAGMA database_list",
            "schema": SQL.startswith("SELECT version,name FROM"),
            "root": SQL.startswith("SELECT typeof(id),id,typeof(created_at)"),
            "dependency": "FROM run_events WHERE invocation_id" in SQL,
            "commit": SQL == "COMMIT",
        }.get(self.階段, False)
        if 命中:
            raise self.主要
        游標 = self._連線.execute(SQL, *參數)
        return 計數游標(游標, self, SQL.startswith("SELECT version,name FROM"))

    def rollback(self):
        self.回滾次數 += 1
        self._連線.rollback()
        if self.回滾錯誤 is not None:
            raise self.回滾錯誤

    def close(self):
        self.關閉次數 += 1
        self._連線.close()
        if self.關閉錯誤 is not None:
            raise self.關閉錯誤


def _注入(monkeypatch, 資料庫, **選項):
    真實 = sqlite3.connect
    代理 = SQLite代理(真實(資料庫, isolation_level=None), **選項)
    monkeypatch.setattr(模組, "_建立連線", lambda *_參數, **_選項: 代理)
    return 代理


def _含標記(值, 標記, 已看=None):
    if 已看 is None:
        已看 = set()
    if id(值) in 已看:
        return False
    已看.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if isinstance(值, BaseException):
        return any(_含標記(項, 標記, 已看) for 項 in 值.args) or any(
            _含標記(項, 標記, 已看) for 項 in (值.__cause__, 值.__context__) if 項 is not None
        )
    if type(值) is dict:
        return any(_含標記(項, 標記, 已看) for 配對 in 值.items() for 項 in 配對)
    if type(值) in (tuple, list, set, frozenset):
        return any(_含標記(項, 標記, 已看) for 項 in 值)
    try:
        屬性 = object.__getattribute__(值, "__dict__")
    except (AttributeError, TypeError):
        return False
    return type(屬性) is dict and _含標記(屬性, 標記, 已看)


def _斷言控制乾淨(捕捉, 原始, *禁止):
    assert 捕捉.value is 原始
    assert 捕捉.value.args == 原始.args
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    for 框架, _行號 in traceback.walk_tb(捕捉.value.__traceback__):
        if 框架.f_globals.get("__name__", "").startswith("繁中代理.發布介面.治理"):
            for 值 in tuple(框架.f_locals.values()):
                for 標記 in 禁止:
                    assert not _含標記(值, 標記, set())
            for 名稱 in ("主要控制盒", "回滾控制盒", "關閉控制盒"):
                assert not 框架.f_locals.get(名稱)


@pytest.mark.parametrize("階段", ["pragma", "begin", "path", "schema", "root", "dependency", "commit"])
@pytest.mark.parametrize("控制類型", [KeyboardInterrupt, SystemExit, GeneratorExit, 敵對中斷, 敵對離開, 敵對生成器離開])
def test_各階段KISG保持exact且清理一次(資料庫, monkeypatch, 階段, 控制類型):
    原始 = 控制類型("PRIMARY_G05")
    BaseException.__setattr__(原始, "__cause__", RuntimeError("OLD_CAUSE_G05"))
    代理 = _注入(monkeypatch, 資料庫, 階段=階段, 主要=原始)
    with pytest.raises(控制類型) as 捕捉:
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    _斷言控制乾淨(捕捉, 原始, "PRIVATE_PATH_G05", "PRIVATE_INVOCATION_G05", "OLD_CAUSE_G05")
    assert 代理.關閉次數 == 1
    assert 代理.回滾次數 == (階段 not in ("pragma", "begin"))


@pytest.mark.parametrize("主要", [RuntimeError("ordinary"), 自訂Base("custom base")])
def test_ordinary與自訂Base固定化且仍清理(資料庫, monkeypatch, 主要):
    代理 = _注入(monkeypatch, 資料庫, 階段="root", 主要=主要)
    with pytest.raises(保存候選規劃錯誤) as 捕捉:
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    assert (代理.回滾次數, 代理.關閉次數) == (1, 1)


def test_primary控制勝過rollback與close控制(資料庫, monkeypatch):
    主要 = 敵對中斷("PRIMARY_G05")
    代理 = _注入(monkeypatch, 資料庫, 階段="root", 主要=主要,
              回滾=SystemExit("ROLLBACK_G05"), 關閉=GeneratorExit("CLOSE_G05"))
    with pytest.raises(敵對中斷) as 捕捉:
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    _斷言控制乾淨(捕捉, 主要, "ROLLBACK_G05", "CLOSE_G05", "PRIVATE_INVOCATION_G05")
    assert (代理.回滾次數, 代理.關閉次數) == (1, 1)


def test_partial_open的ordinary主要錯誤由close控制取代(資料庫, monkeypatch):
    關閉 = 敵對生成器離開("PARTIAL_CLOSE_G05")
    代理 = _注入(monkeypatch, 資料庫, 階段="pragma", 主要=RuntimeError("partial ordinary"), 關閉=關閉)
    with pytest.raises(敵對生成器離開) as 捕捉:
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    _斷言控制乾淨(捕捉, 關閉, "partial ordinary", "PRIVATE_PATH_G05")
    assert (代理.回滾次數, 代理.關閉次數) == (0, 1)


@pytest.mark.parametrize("回滾控制", [True, False])
def test_ordinary_primary時rollback優先於close控制(資料庫, monkeypatch, 回滾控制):
    回滾 = SystemExit("ROLLBACK_G05") if 回滾控制 else RuntimeError("ordinary rollback")
    關閉 = GeneratorExit("CLOSE_G05")
    代理 = _注入(monkeypatch, 資料庫, 階段="root", 主要=RuntimeError("ordinary primary"), 回滾=回滾, 關閉=關閉)
    預期 = 回滾 if 回滾控制 else 關閉
    with pytest.raises(type(預期)) as 捕捉:
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    _斷言控制乾淨(捕捉, 預期, "ordinary primary", "PRIVATE_INVOCATION_G05")
    assert (代理.回滾次數, 代理.關閉次數) == (1, 1)


@pytest.mark.parametrize("關閉", [RuntimeError("ordinary close"), 敵對離開("POST_COMMIT_G05")])
def test_提交後close失敗政策(資料庫, monkeypatch, 關閉):
    代理 = _注入(monkeypatch, 資料庫, 關閉=關閉)
    預期 = type(關閉) if isinstance(關閉, (KeyboardInterrupt, SystemExit, GeneratorExit)) else 保存候選規劃錯誤
    with pytest.raises(預期) as 捕捉:
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    if 預期 is not 保存候選規劃錯誤:
        _斷言控制乾淨(捕捉, 關閉, "PRIVATE_PATH_G05", "PRIVATE_INVOCATION_G05")
    assert (代理.回滾次數, 代理.關閉次數) == (0, 1)


@pytest.mark.parametrize("名稱,錯誤SQL", [
    ("idx_endpoint_invocations_retention_candidates", "CREATE INDEX idx_endpoint_invocations_retention_candidates ON endpoint_invocations(id)"),
    ("idx_run_events_retention_invocation_id", "CREATE INDEX idx_run_events_retention_invocation_id ON run_events(id)"),
    ("idx_endpoint_tool_calls_retention_invocation_id", "CREATE INDEX idx_endpoint_tool_calls_retention_invocation_id ON endpoint_tool_calls(id)"),
    ("idx_endpoint_redactions_retention_invocation_id", "CREATE INDEX idx_endpoint_redactions_retention_invocation_id ON endpoint_redactions(id)"),
    ("idx_audit_events_retention_invocation_id", "CREATE INDEX idx_audit_events_retention_invocation_id ON audit_events(id)"),
])
@pytest.mark.parametrize("替換", [False, True])
def test_所有關鍵索引刪除或替換均失敗(資料庫, 名稱, 錯誤SQL, 替換):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute(f"DROP INDEX {名稱}")
        if 替換:
            連線.execute(錯誤SQL)
    with pytest.raises(保存候選規劃錯誤):
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)


def test_額外ledger列最多只讀expected加一即失敗(資料庫, monkeypatch):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute("INSERT INTO published_api_schema_migrations VALUES(15,'ADVERSARIAL_EXTRA_G05',0)")
        連線.execute("INSERT INTO published_api_schema_migrations VALUES(16,'UNREAD_EXTRA_G05',0)")
    代理 = _注入(monkeypatch, 資料庫)
    with pytest.raises(保存候選規劃錯誤):
        SQLite保存候選規劃器(str(資料庫)).規劃(2000000000)
    assert 代理.ledger讀列 == len(模組._LEDGER) + 1
    assert (代理.回滾次數, 代理.關閉次數) == (1, 1)
