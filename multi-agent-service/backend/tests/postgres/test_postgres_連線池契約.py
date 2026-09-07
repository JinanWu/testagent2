"""Block 2A PostgreSQL pool 的無資料庫因果與安全契約測試。"""

from __future__ import annotations

import importlib
import inspect
import os
import subprocess
import sys
from threading import Event, Thread
from typing import Any, cast

import pytest

from 繁中代理.環境設定 import 交易儲存設定

連線名稱 = "lab-cola-rd:asia-east1:testagent2-postgres-lab"
秘密 = "block-2a-secret"
DSN = f"postgresql://alice:{秘密}@/app?host=/cloudsql/{連線名稱}"


def postgres設定(
    *,
    dsn: str = DSN,
    最小: int = 1,
    最大: int = 5,
    等待: int = 10,
) -> 交易儲存設定:
    return 交易儲存設定("postgres", dsn, 連線名稱, 最小, 最大, 等待)


class 假交易:
    def __init__(self, 事件: list[str], *, 進入錯誤: BaseException | None = None):
        self.事件 = 事件
        self.進入錯誤 = 進入錯誤

    def __enter__(self):
        self.事件.append("transaction-enter")
        if self.進入錯誤 is not None:
            raise self.進入錯誤
        return self

    def __exit__(self, 類型, 值, traceback):
        self.事件.append("transaction-rollback" if 類型 else "transaction-commit")
        return False


class 假連線:
    def __init__(self, 事件: list[str]):
        self.事件 = 事件

    def transaction(self):
        self.事件.append("transaction")
        return 假交易(self.事件)


class 假借用:
    def __init__(self, 事件: list[str]):
        self.事件 = 事件
        self.連線 = 假連線(事件)

    def __enter__(self):
        self.事件.append("connection-enter")
        return self.連線

    def __exit__(self, 類型, 值, traceback):
        self.事件.append("connection-exit")
        return False


class 精確假Pool:
    """參數名稱與 psycopg-pool 3.3.1 的受測方法一致，刻意不收 **kwargs。"""

    實例: list["精確假Pool"] = []
    open錯誤: BaseException | None = None
    close錯誤: BaseException | None = None

    def __init__(
        self,
        conninfo: str = "",
        *,
        connection_class=None,
        kwargs=None,
        min_size: int = 4,
        max_size: int | None = None,
        open: bool | None = None,
        configure=None,
        check=None,
        reset=None,
        name: str | None = None,
        close_returns: bool = False,
        timeout: float = 30.0,
        max_waiting: int = 0,
        max_lifetime: float = 3600.0,
        max_idle: float = 600.0,
        reconnect_timeout: float = 300.0,
        reconnect_failed=None,
        num_workers: int = 3,
    ):
        self.建構參數 = {
            "conninfo": conninfo,
            "kwargs": kwargs,
            "min_size": min_size,
            "max_size": max_size,
            "open": open,
            "check": check,
            "name": name,
            "timeout": timeout,
        }
        self.事件: list[str] = []
        self.closed = True
        self.open次數 = 0
        self.connection次數 = 0
        self.close次數 = 0
        type(self).實例.append(self)

    @staticmethod
    def check_connection(connection):
        return None

    def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        self.open次數 += 1
        self.事件.append(f"open:{wait}:{timeout}")
        錯誤 = type(self).open錯誤
        if 錯誤 is not None:
            raise 錯誤
        self.closed = False

    def connection(self, timeout: float | None = None):
        self.connection次數 += 1
        self.事件.append(f"connection:{timeout}")
        return 假借用(self.事件)

    def close(self, timeout: float = 5.0) -> None:
        self.close次數 += 1
        self.事件.append(f"close:{timeout}")
        self.closed = True
        錯誤 = type(self).close錯誤
        if 錯誤 is not None:
            raise 錯誤


@pytest.fixture
def 模組(monkeypatch):
    精確假Pool.實例 = []
    精確假Pool.open錯誤 = None
    精確假Pool.close錯誤 = None
    模組 = importlib.import_module("繁中代理.PostgreSQL連線")
    模組.關閉共用連線池()
    monkeypatch.setattr(模組, "ConnectionPool", 精確假Pool)
    monkeypatch.setattr(模組, "conninfo_to_dict", lambda dsn: {"dsn": dsn})
    yield 模組
    精確假Pool.close錯誤 = None
    模組.關閉共用連線池()


def 設定目前環境讀取(monkeypatch, 模組, 設定):
    呼叫參數 = []

    def 讀取(環境):
        呼叫參數.append(環境)
        return 設定

    monkeypatch.setattr(模組, "_讀取交易儲存設定", 讀取)
    return 呼叫參數


def test_假Pool受測介面參數名稱與psycopg_pool_3_3_1一致():
    from psycopg_pool import ConnectionPool

    for 真實, 假造 in (
        (ConnectionPool, 精確假Pool),
        (ConnectionPool.open, 精確假Pool.open),
        (ConnectionPool.connection, 精確假Pool.connection),
        (ConnectionPool.close, 精確假Pool.close),
    ):
        真實參數 = inspect.signature(真實).parameters
        假造參數 = inspect.signature(假造).parameters
        assert list(假造參數) == list(真實參數)
        assert [參數.kind for 參數 in 假造參數.values()] == [
            參數.kind for 參數 in 真實參數.values()
        ]


def test_冷process_import不載入環境模組且不呼叫Path_resolve():
    程式 = r'''
import pathlib
import sys
from typing import get_type_hints

原resolve = pathlib.Path.resolve
呼叫 = []
def 記錄resolve(self, *args, **kwargs):
    呼叫.append(str(self))
    return 原resolve(self, *args, **kwargs)
pathlib.Path.resolve = 記錄resolve
assert "繁中代理.環境設定" not in sys.modules
from 繁中代理.交易儲存設定 import 交易儲存設定
import 繁中代理.PostgreSQL連線 as 連線
assert "繁中代理.環境設定" not in sys.modules
assert 呼叫 == [], 呼叫
for 名稱 in ("建立連線池", "啟動共用連線池", "取得共用連線池", "交易連線"):
    assert get_type_hints(getattr(連線, 名稱))["凍結設定"] is 交易儲存設定
    assert "繁中代理.環境設定" not in sys.modules
    assert 呼叫 == [], 呼叫
import 繁中代理.環境設定 as 環境設定
assert 環境設定.交易儲存設定 is 交易儲存設定
'''
    結果 = subprocess.run(
        [sys.executable, "-c", 程式],
        cwd=os.getcwd(),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert 結果.returncode == 0, 結果.stderr


def test_建立連線池只建構open_false且參數精確(monkeypatch, 模組):
    設定 = postgres設定(最小=2, 最大=7, 等待=11)
    呼叫參數 = 設定目前環境讀取(monkeypatch, 模組, 設定)
    pool = 模組.建立連線池(設定)
    assert 呼叫參數 == [os.environ]
    assert pool.open次數 == 0
    assert pool.建構參數 == {
        "conninfo": DSN,
        "kwargs": {"autocommit": False, "row_factory": 模組.dict_row},
        "min_size": 2,
        "max_size": 7,
        "open": False,
        "check": 精確假Pool.check_connection,
        "name": "testagent2-postgres-runtime",
        "timeout": 11.0,
    }


@pytest.mark.parametrize("邊界", ["create", "open", "reuse", "acquire"])
def test_每個邊界重新讀完整process環境且mismatch不觸發下一層(monkeypatch, 模組, 邊界):
    設定 = postgres設定()
    呼叫參數 = 設定目前環境讀取(monkeypatch, 模組, 設定)
    if 邊界 in {"reuse", "acquire"}:
        pool = 模組.啟動共用連線池(設定)
        建構前 = len(精確假Pool.實例)
        open前 = pool.open次數
        connection前 = pool.connection次數
    else:
        pool = None
        建構前 = len(精確假Pool.實例)
        open前 = 0
        connection前 = 0
    漂移 = postgres設定(等待=9)
    monkeypatch.setattr(模組, "_讀取交易儲存設定", lambda 環境: 漂移)
    with pytest.raises(RuntimeError):
        if 邊界 == "create":
            模組.建立連線池(設定)
        elif 邊界 == "open":
            模組.啟動共用連線池(設定)
        elif 邊界 == "reuse":
            模組.取得共用連線池(設定)
        else:
            with 模組.交易連線(設定):
                pass
    assert len(精確假Pool.實例) == 建構前
    if pool is not None:
        assert pool.open次數 == open前
        assert pool.connection次數 == connection前


def test_啟動重用與取得皆不重開(monkeypatch, 模組):
    設定 = postgres設定()
    呼叫 = 設定目前環境讀取(monkeypatch, 模組, 設定)
    pool = 模組.啟動共用連線池(設定)
    assert pool.open次數 == 1
    assert 模組.啟動共用連線池(設定) is pool
    assert 模組.取得共用連線池(設定) is pool
    assert pool.open次數 == 1
    assert 呼叫 == [os.environ, os.environ, os.environ, os.environ]


def test_pool建構後open前再次讀環境且漂移時open為零(monkeypatch, 模組):
    設定 = postgres設定()
    漂移 = postgres設定(等待=9)
    讀取次數 = 0

    def 讀取(環境):
        nonlocal 讀取次數
        assert 環境 is os.environ
        讀取次數 += 1
        return 設定 if 讀取次數 == 1 else 漂移

    monkeypatch.setattr(模組, "_讀取交易儲存設定", 讀取)
    with pytest.raises(RuntimeError, match="^PostgreSQL 連線設定不一致$"):
        模組.啟動共用連線池(設定)
    pool = 精確假Pool.實例[0]
    assert 讀取次數 == 2
    assert pool.open次數 == 0
    assert pool.close次數 == 1
    assert 模組._共用連線池 is None


def test_借用connection前再次讀環境且漂移時connection為零(monkeypatch, 模組):
    設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 設定)
    pool = 模組.啟動共用連線池(設定)
    漂移 = postgres設定(等待=9)
    讀取次數 = 0

    def 讀取(環境):
        nonlocal 讀取次數
        讀取次數 += 1
        return 設定 if 讀取次數 == 1 else 漂移

    monkeypatch.setattr(模組, "_讀取交易儲存設定", 讀取)
    with pytest.raises(RuntimeError, match="^PostgreSQL 連線設定不一致$"):
        with 模組.交易連線(設定):
            pass
    assert 讀取次數 == 2
    assert pool.connection次數 == 0


def test_多筆交易可同時進入而shutdown等待全部歸還(monkeypatch, 模組):
    設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 設定)
    pool = 模組.啟動共用連線池(設定)
    兩者已進入 = Event()
    可離開 = Event()
    已進入: list[int] = []
    執行緒錯誤: list[BaseException] = []

    def 借用工作():
        try:
            with 模組.交易連線(設定):
                已進入.append(1)
                if len(已進入) == 2:
                    兩者已進入.set()
                assert 可離開.wait(2.0)
        except BaseException as 錯誤:
            執行緒錯誤.append(錯誤)

    執行緒們 = [Thread(target=借用工作) for _ in range(2)]
    for 執行緒 in 執行緒們:
        執行緒.start()
    assert 兩者已進入.wait(2.0)
    assert pool.connection次數 == 2
    可離開.set()
    for 執行緒 in 執行緒們:
        執行緒.join(2.0)
        assert not 執行緒.is_alive()
    assert not 執行緒錯誤


def test_外部關閉且fingerprint未變時丟棄重建(monkeypatch, 模組):
    設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 設定)
    舊 = 模組.啟動共用連線池(設定)
    舊.closed = True
    新 = 模組.取得共用連線池(設定)
    assert 新 is not 舊
    assert len(精確假Pool.實例) == 2
    assert 新.open次數 == 1


def test_pool_bound_fingerprint漂移即使supplied與current同步也fail_closed(monkeypatch, 模組):
    原設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 原設定)
    pool = 模組.啟動共用連線池(原設定)
    漂移 = postgres設定(等待=9)
    monkeypatch.setattr(模組, "_讀取交易儲存設定", lambda 環境: 漂移)
    with pytest.raises(RuntimeError):
        模組.取得共用連線池(漂移)
    assert len(精確假Pool.實例) == 1
    assert pool.open次數 == 1
    assert pool.connection次數 == 0


def test_open失敗保留原錯誤_清理錯誤不得遮蔽_且可retry(monkeypatch, 模組):
    設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 設定)
    原錯誤 = LookupError("open-failure")
    精確假Pool.open錯誤 = 原錯誤
    精確假Pool.close錯誤 = RuntimeError("cleanup-failure")
    with pytest.raises(LookupError, match="open-failure") as 捕捉:
        模組.啟動共用連線池(設定)
    assert 捕捉.value is 原錯誤
    失敗pool = 精確假Pool.實例[0]
    assert 失敗pool.close次數 == 1
    assert 模組._共用連線池 is None
    assert 模組._共用連線池指紋 is None
    精確假Pool.open錯誤 = None
    精確假Pool.close錯誤 = None
    assert 模組.啟動共用連線池(設定) is 精確假Pool.實例[1]


def test_交易連線使用timeout與transaction且例外rollback(monkeypatch, 模組):
    設定 = postgres設定(等待=11)
    設定目前環境讀取(monkeypatch, 模組, 設定)
    with pytest.raises(ZeroDivisionError):
        with 模組.交易連線(設定) as 連線:
            assert isinstance(連線, 假連線)
            raise ZeroDivisionError("boom")
    pool = 精確假Pool.實例[0]
    assert pool.connection次數 == 1
    assert pool.事件 == [
        "open:True:11.0",
        "connection:11.0",
        "connection-enter",
        "transaction",
        "transaction-enter",
        "transaction-rollback",
        "connection-exit",
    ]


def test_交易借用與shutdown由同一module_lock線性化(monkeypatch, 模組):
    設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 設定)
    pool = 模組.啟動共用連線池(設定)
    已借用 = Event()
    可離開 = Event()
    已關閉 = Event()
    執行緒錯誤: list[BaseException] = []

    def 借用工作():
        try:
            with 模組.交易連線(設定):
                已借用.set()
                assert 可離開.wait(2.0)
        except BaseException as 錯誤:
            執行緒錯誤.append(錯誤)

    def 關閉工作():
        try:
            模組.關閉共用連線池()
            已關閉.set()
        except BaseException as 錯誤:
            執行緒錯誤.append(錯誤)

    借用執行緒 = Thread(target=借用工作)
    關閉執行緒 = Thread(target=關閉工作)
    借用執行緒.start()
    assert 已借用.wait(2.0)
    關閉執行緒.start()
    assert not 已關閉.wait(0.05)
    assert pool.close次數 == 0
    可離開.set()
    借用執行緒.join(2.0)
    關閉執行緒.join(2.0)
    assert not 借用執行緒.is_alive()
    assert not 關閉執行緒.is_alive()
    assert 已關閉.is_set()
    assert pool.close次數 == 1
    assert not 執行緒錯誤


def test_關閉冪等並在close錯誤時仍清除狀態(monkeypatch, 模組):
    設定 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 設定)
    pool = 模組.啟動共用連線池(設定)
    精確假Pool.close錯誤 = RuntimeError("close-failure")
    with pytest.raises(RuntimeError, match="close-failure"):
        模組.關閉共用連線池()
    assert pool.close次數 == 1
    assert 模組._共用連線池 is None
    assert 模組._共用連線池指紋 is None
    精確假Pool.close錯誤 = None
    模組.關閉共用連線池()
    assert pool.close次數 == 1


class 惡意字串(str):
    def __eq__(self, other):
        raise AssertionError("不得執行 attacker equality")

    def __hash__(self):
        raise AssertionError("不得執行 attacker hash")


@pytest.mark.parametrize(
    "欄位,值",
    [
        ("後端", 惡意字串("postgres")),
        ("資料庫URL", 惡意字串(DSN)),
        ("CloudSQL連線名稱", 惡意字串(連線名稱)),
        ("Pool最小連線數", True),
        ("Pool最大連線數", True),
        ("Pool等待秒數", True),
    ],
)
def test_exact_type_hostile值fail_closed且不執行攻擊者比較(monkeypatch, 模組, 欄位, 值):
    正常 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 正常)
    惡意 = postgres設定()
    object.__setattr__(惡意, 欄位, cast(Any, 值))
    with pytest.raises(RuntimeError, match="^PostgreSQL 連線設定無效$") as 捕捉:
        模組.建立連線池(惡意)
    assert 秘密 not in str(捕捉.value)
    assert not 精確假Pool.實例


def test_object_mutation與非exact_dataclass均在parser與pool前拒絕(monkeypatch, 模組):
    正常 = postgres設定()
    設定目前環境讀取(monkeypatch, 模組, 正常)
    mutated = postgres設定()
    object.__setattr__(mutated, "Pool最大連線數", 99)
    for 值 in (mutated, object()):
        with pytest.raises(RuntimeError):
            模組.建立連線池(cast(Any, 值))
    assert not 精確假Pool.實例


def test_DSN_parser錯誤固定轉譯且無cause秘密不進pool(monkeypatch, 模組):
    壞密碼 = 秘密 + "%" + "ZZ"
    壞DSN = f"postgresql://alice:{壞密碼}@/app?host=/cloudsql/{連線名稱}"
    設定 = postgres設定(dsn=壞DSN)
    設定目前環境讀取(monkeypatch, 模組, 設定)

    def parser(dsn):
        raise ValueError(f"bad conninfo: {dsn}")

    monkeypatch.setattr(模組, "conninfo_to_dict", parser)
    with pytest.raises(RuntimeError, match="^PostgreSQL 連線設定無效$") as 捕捉:
        模組.建立連線池(設定)
    assert 捕捉.value.__cause__ is None
    assert 捕捉.value.__suppress_context__ is True
    assert 秘密 not in str(捕捉.value)
    assert 秘密 not in repr(捕捉.value)
    assert not 精確假Pool.實例


def test_真實_conninfo_parser在畸形percent時於pool前固定失敗(monkeypatch, 模組):
    from psycopg.conninfo import conninfo_to_dict as 真實parser

    壞密碼 = "malformed" + "%" + "ZZ"
    壞DSN = f"postgresql://alice:{壞密碼}@/app?host=/cloudsql/{連線名稱}"
    設定 = postgres設定(dsn=壞DSN)
    設定目前環境讀取(monkeypatch, 模組, 設定)
    monkeypatch.setattr(模組, "conninfo_to_dict", 真實parser)
    with pytest.raises(RuntimeError, match="^PostgreSQL 連線設定無效$") as 捕捉:
        模組.建立連線池(設定)
    assert 捕捉.value.__cause__ is None
    assert 捕捉.value.__suppress_context__ is True
    assert 壞密碼 not in str(捕捉.value)
    assert not 精確假Pool.實例


def test_非postgres在建構前拒絕(monkeypatch, 模組):
    設定 = 交易儲存設定("sqlite")
    設定目前環境讀取(monkeypatch, 模組, 設定)
    with pytest.raises(RuntimeError, match="^PostgreSQL 連線設定無效$"):
        模組.建立連線池(設定)
    assert not 精確假Pool.實例
