"""受信任代理來源位址解析器的安全與隱私契約測試。"""

import importlib
import inspect
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import traceback

import pytest

from 繁中代理.發布介面.呼叫.來源位址 import (
    最大信任代理網段數,
    最大轉送標頭位元組數,
    最大轉送跳數,
    來源位址錯誤,
    解析來源位址,
)
from 繁中代理.發布介面.呼叫.來源節流 import (
    來源節流錯誤,
    來源驗證失敗節流器,
    來源驗證失敗節流決策,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

來源模組 = importlib.import_module("繁中代理.發布介面.呼叫.來源位址")
節流模組 = importlib.import_module("繁中代理.發布介面.呼叫.來源節流")
固定訊息 = "來源位址解析失敗"


class 敵對元組(tuple):
    """記錄任何可能觸發的 tuple override。"""

    次數 = 0

    def __len__(self):
        敵對元組.次數 += 1
        raise AssertionError("不得呼叫")

    def __iter__(self):
        敵對元組.次數 += 1
        raise AssertionError("不得呼叫")


class 敵對位元組(bytes):
    """記錄解析器是否在 exact-type gate 前操作 bytes subclass。"""

    次數 = 0

    def count(self, *參數):
        del 參數
        敵對位元組.次數 += 1
        raise AssertionError("不得呼叫")


class 敵對字串(str):
    """記錄解析器是否在 exact-type gate 前操作 str subclass。"""

    次數 = 0

    def isascii(self):
        敵對字串.次數 += 1
        raise AssertionError("不得呼叫")


class 自訂基礎錯誤(BaseException):
    """模擬非控制流程 BaseException。"""


敵對例外觸發次數 = {"取屬性": 0, "迭代": 0}


def _禁止例外取屬性(self, 名稱):
    del self, 名稱
    敵對例外觸發次數["取屬性"] += 1
    raise AssertionError("隱私 oracle 不得呼叫例外 override")


def _禁止例外迭代(self):
    del self
    敵對例外觸發次數["迭代"] += 1
    raise AssertionError("隱私 oracle 不得迭代敵對例外")


class 敵對鍵盤中斷(KeyboardInterrupt):
    __getattribute__ = _禁止例外取屬性
    __iter__ = _禁止例外迭代


class 敵對注入錯誤(自訂基礎錯誤):
    __getattribute__ = _禁止例外取屬性
    __iter__ = _禁止例外迭代


class 敵對系統離開(SystemExit):
    __getattribute__ = _禁止例外取屬性
    __iter__ = _禁止例外迭代


class 敵對產生器離開(GeneratorExit):
    __getattribute__ = _禁止例外取屬性
    __iter__ = _禁止例外迭代


def _值含標記(值, 標記, 已見=None):
    """只走 exact builtins，避免隱私 oracle 自己觸發敵對 callback。"""
    if 已見 is None:
        已見 = set()
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is bytes:
        return 標記.encode() in 值
    if type(值) is type(Path()):
        return 標記 in Path.__str__(值)
    if issubclass(type(值), BaseException):
        參數 = BaseException.__getattribute__(值, "args")
        return type(參數) is tuple and _值含標記(參數, 標記, 已見)
    if type(值) in (tuple, list, set, frozenset):
        return any(_值含標記(項, 標記, 已見) for 項 in 值)
    if type(值) is dict:
        return any(_值含標記(鍵, 標記, 已見) or _值含標記(項, 標記, 已見)
                   for 鍵, 項 in dict.items(值))
    return False


def _斷言產品追蹤無標記(錯誤, 標記):
    """逐一掃描所有產品 traceback locals，確認沒有 caller-derived marker。"""
    追蹤 = BaseException.__getattribute__(錯誤, "__traceback__")
    for 框架, _ in traceback.walk_tb(追蹤):
        if "/繁中代理/" in 框架.f_code.co_filename:
            for 值 in tuple(框架.f_locals.values()):
                if _值含標記(值, 標記, set()):
                    raise AssertionError(框架.f_code.co_name)


def _斷言隱私oracle拒絕已知洩漏(錯誤, 標記):
    """正向控制必須真的讓 oracle 以 AssertionError 拒絕。"""
    捕獲 = None
    try:
        _斷言產品追蹤無標記(錯誤, 標記)
    except AssertionError as 錯誤結果:
        捕獲 = 錯誤結果
    assert type(捕獲) is AssertionError


def test_隱私oracle正向控制會拒絕直接與例外args洩漏():
    """避免 opaque BaseException 或未驗證 helper 造成假綠。"""
    命名空間 = {}
    程式 = compile(
        "def 直接洩漏(標記, 錯誤):\n    raise 錯誤\n"
        "def 例外洩漏(錯誤):\n    raise 錯誤\n",
        "/正向控制/繁中代理/來源位址洩漏.py",
        "exec",
    )
    exec(程式, 命名空間)

    try:
        命名空間["直接洩漏"]("direct-oracle-marker", RuntimeError("安全"))
    except RuntimeError as 直接錯誤:
        _斷言隱私oracle拒絕已知洩漏(直接錯誤, "direct-oracle-marker")

    敵對例外觸發次數["取屬性"] = 敵對例外觸發次數["迭代"] = 0
    原始錯誤 = 敵對鍵盤中斷("args-oracle-marker")
    已捕獲錯誤 = None
    try:
        命名空間["例外洩漏"](原始錯誤)
    except BaseException as 捕獲錯誤:
        已捕獲錯誤 = 捕獲錯誤
    assert 已捕獲錯誤 is 原始錯誤
    assert BaseException.__getattribute__(已捕獲錯誤, "args") == ("args-oracle-marker",)
    追蹤 = BaseException.__getattribute__(已捕獲錯誤, "__traceback__")
    產品框架 = [框架 for 框架, _ in traceback.walk_tb(追蹤)
            if "/繁中代理/" in 框架.f_code.co_filename]
    assert all(not any(type(值) is str and "args-oracle-marker" in 值
                       for 值 in tuple(框架.f_locals.values())) for 框架 in 產品框架)
    敵對例外觸發次數["取屬性"] = 敵對例外觸發次數["迭代"] = 0
    _斷言隱私oracle拒絕已知洩漏(已捕獲錯誤, "args-oracle-marker")
    assert 敵對例外觸發次數 == {"取屬性": 0, "迭代": 0}


def test_隱私oracle辨識exact_Path且不呼叫敵對路徑override():
    命名空間 = {}
    exec(compile("def 洩漏(路徑):\n    raise RuntimeError('安全')\n",
                 "/正向控制/繁中代理/路徑洩漏.py", "exec"), 命名空間)
    標記 = "real-db-path-marker"
    try:
        命名空間["洩漏"](Path(f"/tmp/{標記}.sqlite"))
    except RuntimeError as 錯誤:
        _斷言隱私oracle拒絕已知洩漏(錯誤, 標記)

    class 敵對路徑(type(Path())):
        次數 = 0

        def __str__(self):
            type(self).次數 += 1
            raise AssertionError("不得呼叫")

        def __fspath__(self):
            type(self).次數 += 1
            raise AssertionError("不得呼叫")

    assert _值含標記(敵對路徑("/tmp/hostile-marker"), "hostile-marker") is False
    assert 敵對路徑.次數 == 0


def _斷言固定錯誤(對端="10.0.0.1", 標頭=(b"bad",), 網段=("10.0.0.0/8",)):
    """確認所有 fail-closed 分支只公開固定錯誤。"""
    with pytest.raises(來源位址錯誤) as 捕獲:
        解析來源位址(對端, 標頭, 網段)
    assert type(捕獲.value) is 來源位址錯誤
    assert 捕獲.value.args == (固定訊息,)
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None


def test_直連與不受信對端完全忽略偽造或敵對標頭():
    """只有 socket peer 落入明確 CIDR 時，XFF 才能影響權威來源。"""
    敵對元組.次數 = 0
    assert 解析來源位址("198.51.100.9", 敵對元組((b"1.2.3.4",)), ()) == "198.51.100.9"
    assert 解析來源位址("10.0.0.2", (b"1.2.3.4", b"5.6.7.8"), ("10.0.0.0/31",)) == "10.0.0.2"
    assert 敵對元組.次數 == 0


@pytest.mark.parametrize(
    ("對端", "標頭", "網段", "預期"),
    [
        ("10.0.0.1", b"203.0.113.7", ("10.0.0.0/8",), "203.0.113.7"),
        ("10.0.0.1", b"203.0.113.7, 10.2.0.8", ("10.0.0.0/8",), "203.0.113.7"),
        ("10.0.0.1", b"192.0.2.4, 203.0.113.7, 10.2.0.8", ("10.0.0.0/8",), "203.0.113.7"),
        ("10.0.0.1", b"10.3.0.9, 10.2.0.8", ("10.0.0.0/8",), "10.3.0.9"),
        ("10.0.0.0", b"192.0.2.1", ("10.0.0.0/31",), "192.0.2.1"),
        ("10.0.0.1", b"192.0.2.1", ("10.0.0.0/31",), "192.0.2.1"),
    ],
)
def test_受信鏈由右至左找第一個不受信跳點且全受信取最左(對端, 標頭, 網段, 預期):
    assert 解析來源位址(對端, (標頭,), 網段) == 預期


@pytest.mark.parametrize(
    ("對端", "標頭", "網段", "預期"),
    [
        ("2001:db8::1", b" 2001:0DB9:0:0:0:0:0:ABCD\t", ("2001:db8::/32",), "2001:db9::abcd"),
        ("2001:db8::1", b"192.0.2.9", ("2001:db8::/32",), "192.0.2.9"),
        ("10.0.0.1", b"2001:db8::abcd", ("10.0.0.0/8",), "2001:db8::abcd"),
    ],
)
def test_IPv4_IPv6混合family獨立且輸出正規compressed(對端, 標頭, 網段, 預期):
    結果 = 解析來源位址(對端, (標頭,), 網段)
    assert type(結果) is str and 結果 == 預期


@pytest.mark.parametrize(
    "標頭",
    [
        (), (b"1.2.3.4", b"5.6.7.8"), (b"",), (b" \t",), (b"1.2.3.4,",),
        (b",1.2.3.4",), (b"1.2.3.4,,5.6.7.8",), (b"1.2.3.4:80",),
        (b"[2001:db8::1]",), (b"fe80::1%en0",), (b"::ffff:192.0.2.1",),
        (b"1.2.3.4\n",), ("1.2.3.4",), ([b"1.2.3.4"],), (b"for=1.2.3.4",),
    ],
)
def test_受信代理的重複空白畸形port_bracket_zone或非bytes標頭皆關閉(標頭):
    _斷言固定錯誤(標頭=標頭)


def test_標頭位元組與跳數在split前有硬上限():
    _斷言固定錯誤(標頭=(b"1" * (最大轉送標頭位元組數 + 1),))
    過多跳點 = b",".join([b"1.1.1.1"] * (最大轉送跳數 + 1))
    _斷言固定錯誤(標頭=(過多跳點,))
    assert 解析來源位址("10.0.0.1", (b",".join([b"10.0.0.2"] * 最大轉送跳數),),
                         ("10.0.0.0/8",)) == "10.0.0.2"


@pytest.mark.parametrize(
    ("對端", "網段"),
    [
        ("bad", ("10.0.0.0/8",)), ("::ffff:192.0.2.1", ("::ffff:0:0/96",)),
        ("10.0.0.1", ["10.0.0.0/8"]), ("10.0.0.1", ("10.0.0.1/8",)),
        ("10.0.0.1", ("bad",)), ("10.0.0.1", ("::ffff:192.0.2.0/120",)),
        ("10.0.0.1", tuple("10.0.0.0/8" for _ in range(最大信任代理網段數 + 1))),
    ],
)
def test_對端與明確CIDR配置畸形或映射位址皆關閉(對端, 網段):
    _斷言固定錯誤(對端=對端, 網段=網段)


def test_敵對內建子類在override前被拒絕():
    敵對元組.次數 = 敵對位元組.次數 = 敵對字串.次數 = 0
    _斷言固定錯誤(網段=敵對元組(("10.0.0.0/8",)))
    _斷言固定錯誤(對端=敵對字串("10.0.0.1"))
    _斷言固定錯誤(標頭=(敵對位元組(b"1.2.3.4"),))
    assert (敵對元組.次數, 敵對位元組.次數, 敵對字串.次數) == (0, 0, 0)


@pytest.mark.parametrize("錯誤類型", [自訂基礎錯誤, 敵對注入錯誤, RuntimeError])
def test_非控制流程例外固定化且產品追蹤不留原始資料(monkeypatch, 錯誤類型):
    敵對例外觸發次數["取屬性"] = 敵對例外觸發次數["迭代"] = 0
    錯誤 = 錯誤類型("private-marker")

    def 注入解析(文字):
        del 文字
        raise 錯誤

    monkeypatch.setattr(來源模組.ipaddress, "ip_address", 注入解析)
    with pytest.raises(來源位址錯誤) as 捕獲:
        解析來源位址("10.0.0.1", (b"private-marker",), ("10.0.0.0/8",))
    assert 捕獲.value.args == (固定訊息,)
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    敵對例外觸發次數["取屬性"] = 敵對例外觸發次數["迭代"] = 0
    _斷言產品追蹤無標記(捕獲.value, "private-marker")
    assert 敵對例外觸發次數 == {"取屬性": 0, "迭代": 0}


@pytest.mark.parametrize(
    "錯誤類型",
    [KeyboardInterrupt, SystemExit, GeneratorExit,
     敵對鍵盤中斷, 敵對系統離開, 敵對產生器離開],
)
def test_控制流程保持exact物件與args並清除所有產品locals(monkeypatch, 錯誤類型):
    敵對例外觸發次數["取屬性"] = 敵對例外觸發次數["迭代"] = 0
    錯誤 = 錯誤類型("control-private-marker")

    def 注入解析(文字):
        del 文字
        raise 錯誤

    monkeypatch.setattr(來源模組.ipaddress, "ip_address", 注入解析)
    with pytest.raises(錯誤類型) as 捕獲:
        解析來源位址("10.0.0.1", (b"control-private-marker",), ("10.0.0.0/8",))
    assert 捕獲.value is 錯誤
    assert BaseException.__getattribute__(捕獲.value, "args") == ("control-private-marker",)
    assert BaseException.__getattribute__(捕獲.value, "__cause__") is None
    assert BaseException.__getattribute__(捕獲.value, "__context__") is None
    追蹤 = BaseException.__getattribute__(捕獲.value, "__traceback__")
    for 框架, _ in traceback.walk_tb(追蹤):
        if "/繁中代理/" in 框架.f_code.co_filename:
            assert all(值 is not 錯誤 for 值 in tuple(框架.f_locals.values()))
    敵對例外觸發次數["取屬性"] = 敵對例外觸發次數["迭代"] = 0
    _斷言產品追蹤無標記(捕獲.value, "control-private-marker")
    assert 敵對例外觸發次數 == {"取屬性": 0, "迭代": 0}


@pytest.mark.parametrize(
    ("對端", "標頭", "網段"),
    [
        ("10.0.0.1", (b"raw-private-marker",), ("10.0.0.0/8",)),
        ("raw-private-marker", (), ("10.0.0.0/8",)),
        ("10.0.0.1", (), ("raw-private-marker",)),
    ],
)
def test_畸形標頭_peer_config原始標記不出現在固定錯誤產品locals(對端, 標頭, 網段):
    標記 = "raw-private-marker"
    with pytest.raises(來源位址錯誤) as 捕獲:
        解析來源位址(對端, 標頭, 網段)
    _斷言產品追蹤無標記(捕獲.value, 標記)


def _建立來源節流資料庫(tmp_path, 名稱="source.sqlite"):
    路徑 = tmp_path / 名稱
    assert 初始化發布介面資料庫(路徑) == tuple(range(1, 17))
    return 路徑


def _查詢(路徑, SQL, 參數=()):
    連線 = sqlite3.connect(路徑)
    try:
        return 連線.execute(SQL, 參數).fetchall()
    finally:
        連線.close()


class _讀取注入游標:
    def __init__(self, 游標, 代理):
        self.游標, self.代理 = 游標, 代理

    def fetchone(self):
        self.代理.事件.append("FETCH")
        錯誤 = self.代理.注入.get("FETCH")
        if 錯誤 is not None:
            raise 錯誤
        return self.游標.fetchone()

class _交易代理:
    def __init__(self, 連線, 注入=None, 事件=None, 開始前=None, 開始後=None, 開始釋放=None):
        self.連線 = 連線
        self.注入 = {} if 注入 is None else 注入
        self.事件 = [] if 事件 is None else 事件
        self.開始前, self.開始後, self.開始釋放 = 開始前, 開始後, 開始釋放


    def execute(self, SQL, *參數):
        if SQL == "BEGIN IMMEDIATE":
            self.事件.append("BEGIN")
            if self.開始前 is not None:
                self.開始前.set()
            錯誤 = self.注入.get("BEGIN")
            if 錯誤 is not None:
                raise 錯誤
            結果 = self.連線.execute(SQL, *參數)
            if self.開始後 is not None:
                self.開始後.set()
            if self.開始釋放 is not None and not self.開始釋放.wait(5):
                raise TimeoutError("BEGIN gate timeout")
            return 結果
        if SQL == 節流模組._增加語句:
            self.事件.append("UPSERT")
            錯誤 = self.注入.get("UPSERT")
            if 錯誤 is not None:
                raise 錯誤
            return _讀取注入游標(self.連線.execute(SQL, *參數), self)
        return self.連線.execute(SQL, *參數)

    def commit(self):
        self.事件.append("COMMIT")
        錯誤 = self.注入.get("COMMIT")
        if 錯誤 is not None:
            raise 錯誤
        return self.連線.commit()

    def rollback(self):
        self.事件.append("ROLLBACK")
        self.連線.rollback()
        錯誤 = self.注入.get("ROLLBACK")
        if 錯誤 is not None:
            raise 錯誤

    def close(self):
        self.事件.append("CLOSE")
        self.連線.close()
        錯誤 = self.注入.get("CLOSE")
        if 錯誤 is not None:
            raise 錯誤


def test_來源失敗遷移使用獨立表精確主鍵CHECK與索引(tmp_path):
    路徑 = _建立來源節流資料庫(tmp_path)
    assert _查詢(路徑, "PRAGMA table_info(auth_failure_rate_counters)") == [
        (0, "client_ip", "TEXT", 1, None, 1),
        (1, "endpoint_slug", "TEXT", 1, None, 2),
        (2, "window_start", "INTEGER", 1, None, 3),
        (3, "failure_count", "INTEGER", 1, None, 0),
        (4, "updated_at", "REAL", 1, None, 0),
    ]
    assert _查詢(路徑, "PRAGMA index_info(idx_auth_failure_rate_counters_window_start)") == [
        (0, 2, "window_start")
    ]
    連線 = sqlite3.connect(路徑)
    try:
        合法 = ("192.0.2.1", "ep", 0, 1, 0)
        for 欄位, 值 in ((0, " x"), (1, ""), (2, 0.5), (3, 1.5), (4, math.inf)):
            資料: list[object] = list(合法)
            資料[欄位] = 值
            with pytest.raises(sqlite3.IntegrityError):
                連線.execute("INSERT INTO auth_failure_rate_counters VALUES(?,?,?,?,?)", 資料)
            連線.rollback()
    finally:
        連線.close()


def test_每次無效金鑰皆增加且等於上限仍未超限並不動主計數(tmp_path):
    路徑 = _建立來源節流資料庫(tmp_path)
    服務 = 來源驗證失敗節流器(路徑)
    前 = _查詢(路徑, "SELECT * FROM rate_limit_counters")
    結果 = [服務.記錄失敗("192.0.2.9", "chat", 秒, 2, 60) for 秒 in (0, 1, 2, 3)]
    assert [(項.計數, 項.已超限, 項.重試秒數) for 項 in 結果] == [
        (1, False, None), (2, False, None), (3, True, 58), (4, True, 57)
    ]
    assert all(type(項) is 來源驗證失敗節流決策 for 項 in 結果)
    assert _查詢(路徑, "SELECT * FROM rate_limit_counters") == 前
    assert _查詢(路徑, "SELECT client_ip,endpoint_slug,window_start,failure_count FROM auth_failure_rate_counters") == [
        ("192.0.2.9", "chat", 0, 4)
    ]


def test_IP_slug_視窗與IPv4_IPv6正規來源彼此隔離(tmp_path):
    路徑 = _建立來源節流資料庫(tmp_path)
    服務 = 來源驗證失敗節流器(路徑)
    for IP, slug, 現在 in (
        ("192.0.2.1", "a", 59.5), ("192.0.2.1", "b", 59.5),
        ("192.0.2.2", "a", 59.5), ("2001:db8::1", "a", 59.5),
        ("192.0.2.1", "a", 60),
    ):
        決策 = 服務.記錄失敗(IP, slug, 現在, 1, 60)
        assert (決策.計數, 決策.已超限) == (1, False)
    assert _查詢(路徑, "SELECT client_ip,endpoint_slug,window_start,failure_count "
                      "FROM auth_failure_rate_counters ORDER BY 1,2,3") == [
        ("192.0.2.1", "a", 0, 1), ("192.0.2.1", "a", 60, 1),
        ("192.0.2.1", "b", 0, 1), ("192.0.2.2", "a", 0, 1),
        ("2001:db8::1", "a", 0, 1),
    ]


@pytest.mark.parametrize(
    "參數",
    [
        ("192.0.2.01", "ep", 0, 1, 60), ("::ffff:192.0.2.1", "ep", 0, 1, 60),
        (敵對字串("192.0.2.1"), "ep", 0, 1, 60), ("192.0.2.1", "bad slug", 0, 1, 60),
        ("192.0.2.1", "x" * 129, 0, 1, 60), ("192.0.2.1", "ep", True, 1, 60),
        ("192.0.2.1", "ep", math.nan, 1, 60), ("192.0.2.1", "ep", 0, True, 60),
        ("192.0.2.1", "ep", 0, 1, 0), ("192.0.2.1", "ep", 0, 1, 86_401),
    ],
)
def test_畸形IP_slug_時間與配置在開啟資料庫前固定拒絕(tmp_path, monkeypatch, 參數):
    次數 = 0

    def 禁止連接(*args, **kwargs):
        nonlocal 次數
        del args, kwargs
        次數 += 1
        raise AssertionError

    monkeypatch.setattr(節流模組, "_連接SQLite", 禁止連接)
    with pytest.raises(來源節流錯誤, match="^來源節流失敗$") as 捕獲:
        來源驗證失敗節流器(tmp_path / "missing").記錄失敗(*參數)
    assert 次數 == 0
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None


@pytest.mark.parametrize("錯誤類型", [RuntimeError, 自訂基礎錯誤])
def test_來源正規化解析器普通與自訂Base固定化且無鏈(tmp_path, monkeypatch, 錯誤類型):
    標記 = "bad-parser-marker"
    原始 = 錯誤類型(標記)
    呼叫 = []

    def 注入解析器(文字):
        呼叫.append(文字)
        raise 原始

    monkeypatch.setattr(節流模組.ipaddress, "ip_address", 注入解析器)
    with pytest.raises(來源節流錯誤, match="^來源節流失敗$") as 捕獲:
        來源驗證失敗節流器(tmp_path / f"{標記}.sqlite").記錄失敗(
            "2001:db8::bad", f"{標記}-slug", 59.5, 7, 60)
    assert type(捕獲.value) is 來源節流錯誤
    assert 捕獲.value.args == ("來源節流失敗",)
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    assert 呼叫 == ["2001:db8::bad"]
    _斷言產品追蹤無標記(捕獲.value, 標記)


@pytest.mark.parametrize(
    ("標記", "錯誤類型"),
    [("a11", KeyboardInterrupt), ("b22", SystemExit), ("c33", GeneratorExit)],
)
def test_來源正規化解析器KISG保持identity_args且所有產品locals已清除(
    tmp_path, monkeypatch, 標記, 錯誤類型,
):
    原始 = 錯誤類型(標記)
    呼叫 = []

    def 注入解析器(文字):
        呼叫.append(文字)
        raise 原始

    monkeypatch.setattr(節流模組.ipaddress, "ip_address", 注入解析器)
    with pytest.raises(錯誤類型) as 捕獲:
        來源驗證失敗節流器(tmp_path / f"{標記}-parser.sqlite").記錄失敗(
            f"2001:db8::{標記}", f"{標記}-parser-slug", 59.5, 7, 60)
    assert 捕獲.value is 原始
    assert BaseException.__getattribute__(捕獲.value, "args") == (標記,)
    assert BaseException.__getattribute__(捕獲.value, "__cause__") is None
    assert BaseException.__getattribute__(捕獲.value, "__context__") is None
    assert 呼叫 == [f"2001:db8::{標記}"]
    追蹤 = BaseException.__getattribute__(捕獲.value, "__traceback__")
    所有框架 = [框架 for 框架, _ in traceback.walk_tb(追蹤)]
    產品框架 = [框架 for 框架 in 所有框架 if "/繁中代理/" in 框架.f_code.co_filename]
    assert {框架.f_code.co_name for 框架 in 產品框架} >= {
        "記錄失敗", "記錄來源驗證失敗", "_正規輸入",
    }
    assert any(框架.f_code.co_name == "注入解析器" and "/tests/" in 框架.f_code.co_filename
               for 框架 in 所有框架)
    assert all(框架.f_code.co_name != "注入解析器" for 框架 in 產品框架)
    _斷言產品追蹤無標記(捕獲.value, 標記)


def test_真實最大計數使guarded_UPSERT無RETURNING列且完整回滾(tmp_path):
    """合法canonical列在SQLite最大值時才是truthful overflow，不靠trigger/schema drift。"""
    路徑 = _建立來源節流資料庫(tmp_path)
    連線 = sqlite3.connect(路徑)
    try:
        連線.execute("INSERT INTO auth_failure_rate_counters VALUES(?,?,?,?,?)",
                   ("192.0.2.1", "max", 0, 9223372036854775807, 0))
        連線.execute("INSERT INTO rate_limit_counters VALUES(?,?,?,?,?)",
                   ("endpoint", "main", 0, 7, 0))
        連線.commit()
    finally:
        連線.close()
    主計數 = _查詢(路徑, "SELECT * FROM rate_limit_counters")

    with pytest.raises(來源節流錯誤, match="^來源節流失敗$") as 捕獲:
        來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "max", 0, 1, 60)

    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    assert _查詢(路徑, "SELECT endpoint_slug,failure_count FROM auth_failure_rate_counters") == [
        ("max", 9223372036854775807)
    ]
    assert _查詢(路徑, "SELECT * FROM rate_limit_counters") == 主計數


def test_結構通過後在精確增加語句注入寫入失敗且完整回滾(tmp_path, monkeypatch):
    """寫入失敗由real-SQLite proxy精確注入，不以會被schema pin拒絕的trigger替代。"""
    路徑 = _建立來源節流資料庫(tmp_path)
    原連接 = 節流模組._連接SQLite
    事件 = []

    def 代理連接(*參數, **關鍵字):
        return _交易代理(原連接(*參數, **關鍵字),
                     {"UPSERT": sqlite3.OperationalError("write-private-marker")}, 事件)

    monkeypatch.setattr(節流模組, "_連接SQLite", 代理連接)
    with pytest.raises(來源節流錯誤, match="^來源節流失敗$") as 捕獲:
        來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "blocked", 0, 1, 60)
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    assert 事件 == ["BEGIN", "UPSERT", "ROLLBACK", "CLOSE"]
    assert _查詢(路徑, "SELECT * FROM auth_failure_rate_counters") == []
    assert _查詢(路徑, "SELECT * FROM rate_limit_counters") == []


def test_缺檔_空檔_symlink與同欄位schema漂移皆關閉(tmp_path):
    缺檔 = tmp_path / "missing.sqlite"
    空檔 = tmp_path / "empty.sqlite"
    空檔.touch()
    真實 = _建立來源節流資料庫(tmp_path, "real.sqlite")
    連結 = tmp_path / "link.sqlite"
    os.symlink(真實, 連結)
    漂移 = _建立來源節流資料庫(tmp_path, "drift.sqlite")
    連線 = sqlite3.connect(漂移)
    try:
        連線.execute("DROP INDEX idx_auth_failure_rate_counters_window_start")
        連線.commit()
    finally:
        連線.close()
    for 路徑 in (缺檔, 空檔, 連結, 漂移):
        with pytest.raises(來源節流錯誤, match="^來源節流失敗$"):
            來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "ep", 0, 1, 60)


@pytest.mark.parametrize("漂移", ["check", "pk"])
def test_同欄位但CHECK或主鍵語意漂移在UPSERT前關閉(tmp_path, 漂移):
    路徑 = _建立來源節流資料庫(tmp_path, f"{漂移}.sqlite")
    連線 = sqlite3.connect(路徑)
    try:
        定義 = 連線.execute(
            "SELECT sql FROM sqlite_master WHERE name='auth_failure_rate_counters'"
        ).fetchone()[0]
        if 漂移 == "check":
            定義 = 定義.replace("length(client_ip) BETWEEN 2 AND 45", "length(client_ip) BETWEEN 1 AND 45")
        else:
            定義 = 定義.replace("PRIMARY KEY(client_ip, endpoint_slug, window_start)",
                              "UNIQUE(client_ip, endpoint_slug, window_start)")
        連線.executescript(
            "ALTER TABLE auth_failure_rate_counters RENAME TO 舊來源計數;\n"
            + 定義 + ";\nDROP TABLE 舊來源計數;\n"
            "CREATE INDEX idx_auth_failure_rate_counters_window_start "
            "ON auth_failure_rate_counters(window_start);"
        )
        連線.commit()
    finally:
        連線.close()
    with pytest.raises(來源節流錯誤, match="^來源節流失敗$"):
        來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "ep", 0, 1, 60)
    assert _查詢(路徑, "SELECT * FROM auth_failure_rate_counters") == []


@pytest.mark.parametrize("時機", ["BEFORE", "AFTER"])
def test_敵對trigger在UPSERT前關閉且主計數與spy皆不變(tmp_path, 時機):
    路徑 = _建立來源節流資料庫(tmp_path, f"trigger-{時機}.sqlite")
    連線 = sqlite3.connect(路徑)
    try:
        連線.execute("CREATE TABLE trigger_spy(value TEXT)")
        連線.execute("INSERT INTO rate_limit_counters VALUES(?,?,?,?,?)",
                   ("endpoint", "scope", 0, 7, 0))
        連線.execute(
            f"CREATE TRIGGER 敵對_{時機} {時機} INSERT ON auth_failure_rate_counters BEGIN "
            "UPDATE rate_limit_counters SET request_count=999; "
            "INSERT INTO trigger_spy VALUES(NEW.endpoint_slug); END"
        )
        連線.commit()
    finally:
        連線.close()
    主計數 = _查詢(路徑, "SELECT * FROM rate_limit_counters")
    with pytest.raises(來源節流錯誤):
        來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "trigger-marker", 0, 1, 60)
    assert _查詢(路徑, "SELECT * FROM rate_limit_counters") == 主計數
    assert _查詢(路徑, "SELECT * FROM trigger_spy") == []
    assert _查詢(路徑, "SELECT * FROM auth_failure_rate_counters") == []


def test_過多索引以expected加一有界查詢關閉(tmp_path, monkeypatch):
    路徑 = _建立來源節流資料庫(tmp_path)
    連線 = sqlite3.connect(路徑)
    try:
        for 編號 in range(40):
            連線.execute(f"CREATE INDEX extra_{編號} ON auth_failure_rate_counters(failure_count)")
        連線.commit()
    finally:
        連線.close()
    查詢 = []
    原連接 = 節流模組._連接SQLite

    def 追蹤連接(*參數, **關鍵字):
        結果 = 原連接(*參數, **關鍵字)
        結果.set_trace_callback(查詢.append)
        return 結果

    monkeypatch.setattr(節流模組, "_連接SQLite", 追蹤連接)
    with pytest.raises(來源節流錯誤):
        來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "ep", 0, 1, 60)
    索引查詢 = [項 for 項 in 查詢 if "pragma_index_list" in 項.lower()]
    assert 索引查詢 and all("limit 3" in 項.lower() for 項 in 索引查詢)
    assert not any(項.lstrip().upper().startswith("PRAGMA INDEX_LIST") for 項 in 查詢)


class _開啟代理:
    def __init__(self, 連線, 主要錯誤, 關閉錯誤):
        self.連線, self.主要錯誤, self.關閉錯誤 = 連線, 主要錯誤, 關閉錯誤
        self.關閉次數 = 0

    def execute(self, SQL, *參數):
        if SQL == "PRAGMA foreign_keys=ON" and self.主要錯誤 is not None:
            raise self.主要錯誤
        return self.連線.execute(SQL, *參數)

    def close(self):
        self.關閉次數 += 1
        self.連線.close()
        if self.關閉錯誤 is not None:
            raise self.關閉錯誤


@pytest.mark.parametrize("關閉種類", ["成功", "普通", "控制"])
@pytest.mark.parametrize("主要種類", ["inode普通", "pragma控制"])
def test_部分開啟主要與close優先序_exact_once及追蹤隱私(
    tmp_path, monkeypatch, 主要種類, 關閉種類,
):
    路徑 = _建立來源節流資料庫(tmp_path)
    原連接 = 節流模組._連接SQLite
    主要 = RuntimeError("open-primary-marker") if 主要種類 == "inode普通" else 敵對鍵盤中斷("open-primary-marker")
    關閉 = {"成功": None, "普通": RuntimeError("close-ordinary-marker"),
          "控制": 敵對系統離開("close-control-marker")}[關閉種類]
    代理盒 = []

    def 代理連接(*參數, **關鍵字):
        代理 = _開啟代理(原連接(*參數, **關鍵字),
                     主要 if 主要種類 == "pragma控制" else None, 關閉)
        代理盒.append(代理)
        return 代理

    monkeypatch.setattr(節流模組, "_連接SQLite", 代理連接)
    if 主要種類 == "inode普通":
        monkeypatch.setattr(節流模組.os, "stat", lambda 路徑值: (_ for _ in ()).throw(主要))
    預期 = 敵對系統離開 if 主要種類 == "inode普通" and 關閉種類 == "控制" else (
        敵對鍵盤中斷 if 主要種類 == "pragma控制" else 來源節流錯誤)
    with pytest.raises(預期) as 捕獲:
        來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "open-primary-marker", 0, 1, 60)
    assert 代理盒[0].關閉次數 == 1
    if 預期 is 敵對鍵盤中斷:
        assert 捕獲.value is 主要
    elif 預期 is 敵對系統離開:
        assert 捕獲.value is 關閉
    else:
        assert 捕獲.value.args == ("來源節流失敗",)
    if 預期 in (敵對鍵盤中斷, 敵對系統離開):
        assert BaseException.__getattribute__(捕獲.value, "args") in (
            ("open-primary-marker",), ("close-control-marker",))
    assert BaseException.__getattribute__(捕獲.value, "__cause__") is None
    assert BaseException.__getattribute__(捕獲.value, "__context__") is None
    _斷言產品追蹤無標記(捕獲.value, "open-primary-marker")
    _斷言產品追蹤無標記(捕獲.value, "close-control-marker")


@pytest.mark.parametrize(
    ("案例", "預期事件", "預期類型", "已提交"),
    [
        ("begin", ["BEGIN", "CLOSE"], 來源節流錯誤, False),
        ("upsert", ["BEGIN", "UPSERT", "ROLLBACK", "CLOSE"], 來源節流錯誤, False),
        ("fetch", ["BEGIN", "UPSERT", "FETCH", "ROLLBACK", "CLOSE"], 來源節流錯誤, False),
        ("commit", ["BEGIN", "UPSERT", "FETCH", "COMMIT", "ROLLBACK", "CLOSE"], 來源節流錯誤, False),
        ("primary-kisg", ["BEGIN", "UPSERT", "ROLLBACK", "CLOSE"], KeyboardInterrupt, False),
        ("rollback-kisg", ["BEGIN", "UPSERT", "ROLLBACK", "CLOSE"], SystemExit, False),
        ("close-kisg", ["BEGIN", "UPSERT", "ROLLBACK", "CLOSE"], GeneratorExit, False),
        ("durable-close-ordinary", ["BEGIN", "UPSERT", "FETCH", "COMMIT", "CLOSE"], None, True),
        ("durable-close-kisg", ["BEGIN", "UPSERT", "FETCH", "COMMIT", "CLOSE"], KeyboardInterrupt, True),
    ],
)
def test_真實SQLite交易清理耐久性矩陣(
    tmp_path, monkeypatch, 案例, 預期事件, 預期類型, 已提交,
):
    """BEGIN/UPSERT/fetch/COMMIT/ROLLBACK/CLOSE皆exact once且遵守KISG優先序。"""
    路徑 = _建立來源節流資料庫(tmp_path, f"db-private-marker-{案例}.sqlite")
    原連接 = 節流模組._連接SQLite
    事件 = []
    主要 = KeyboardInterrupt("primary-private-marker")
    回滾 = SystemExit("rollback-private-marker")
    關閉 = GeneratorExit("close-private-marker")
    普通 = RuntimeError(f"ordinary-private-marker-{案例}")
    注入 = {
        "begin": {"BEGIN": 普通}, "upsert": {"UPSERT": 普通},
        "fetch": {"FETCH": 普通}, "commit": {"COMMIT": 普通},
        "primary-kisg": {"UPSERT": 主要, "ROLLBACK": 回滾, "CLOSE": 關閉},
        "rollback-kisg": {"UPSERT": 普通, "ROLLBACK": 回滾, "CLOSE": 關閉},
        "close-kisg": {"UPSERT": 普通, "ROLLBACK": RuntimeError("rollback-ordinary"), "CLOSE": 關閉},
        "durable-close-ordinary": {"CLOSE": 普通},
        "durable-close-kisg": {"CLOSE": 主要},
    }[案例]

    def 代理連接(*參數, **關鍵字):
        return _交易代理(原連接(*參數, **關鍵字), 注入, 事件)

    monkeypatch.setattr(節流模組, "_連接SQLite", 代理連接)
    if 預期類型 is None:
        結果 = 來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "ep", 0, 1, 60)
        assert 結果.計數 == 1
    else:
        with pytest.raises(預期類型) as 捕獲:
            來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "ep", 0, 1, 60)
        if 預期類型 is 來源節流錯誤:
            assert 捕獲.value.args == ("來源節流失敗",)
        else:
            勝者 = 主要 if 案例 in ("primary-kisg", "durable-close-kisg") else (回滾 if 案例 == "rollback-kisg" else 關閉)
            assert 捕獲.value is 勝者
        assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
        for 標記 in ("primary-private-marker", "rollback-private-marker", "close-private-marker", "db-private-marker"):
            _斷言產品追蹤無標記(捕獲.value, 標記)
    assert 事件 == 預期事件
    assert 事件.count("ROLLBACK") == (1 if "ROLLBACK" in 預期事件 else 0)
    assert _查詢(路徑, "SELECT failure_count FROM auth_failure_rate_counters") == ([(1,)] if 已提交 else [])


def test_Event閘控真實連線證明BEGIN_IMMEDIATE爭用且無遺失更新(tmp_path, monkeypatch):
    """A持有真實寫鎖時，B的真實BEGIN須先由SQLite回報locked，釋放後重試才成功。"""
    路徑 = _建立來源節流資料庫(tmp_path)
    原連接 = 節流模組._連接SQLite
    甲已取得鎖 = threading.Event()
    乙已進入SQLite = threading.Event()
    釋放甲 = threading.Event()
    連接鎖 = threading.Lock()
    連接次數 = 0
    連線們 = []
    事件 = [[], [], []]
    甲結果: list[int] = []
    甲錯誤 = []
    乙鎖錯誤 = []
    已移除追蹤 = []

    class _鎖碰撞代理(_交易代理):
        def execute(self, SQL, *參數):
            try:
                return super().execute(SQL, *參數)
            except sqlite3.OperationalError as 錯誤:
                if SQL == "BEGIN IMMEDIATE":
                    乙鎖錯誤.append(錯誤)
                raise

        def close(self):
            self.連線.set_trace_callback(None)
            已移除追蹤.append(True)
            return super().close()

    def 代理連接(*參數, **關鍵字):
        nonlocal 連接次數
        with 連接鎖:
            索引 = 連接次數
            連接次數 += 1
        關鍵字["check_same_thread"] = False
        if 索引 == 1:
            關鍵字["timeout"] = 0.0
        真實 = 原連接(*參數, **關鍵字)
        連線們.append(真實)
        if 索引 == 0:
            return _交易代理(真實, 事件=事件[0], 開始後=甲已取得鎖, 開始釋放=釋放甲)
        if 索引 == 1:
            def 追蹤(SQL):
                if SQL == "BEGIN IMMEDIATE":
                    事件[1].append("SQLITE:BEGIN IMMEDIATE")
                    乙已進入SQLite.set()
            真實.set_trace_callback(追蹤)
            return _鎖碰撞代理(真實, 事件=事件[1])
        return _交易代理(真實, 事件=事件[2])

    monkeypatch.setattr(節流模組, "_連接SQLite", 代理連接)

    def 甲工作者():
        try:
            甲結果.append(來源驗證失敗節流器(路徑).記錄失敗(
                "192.0.2.1", "same", 0, 99, 60).計數)
        except BaseException as 錯誤:
            甲錯誤.append(錯誤)

    甲 = threading.Thread(target=甲工作者, daemon=True)
    甲.start()
    try:
        assert 甲已取得鎖.wait(5)
        with pytest.raises(來源節流錯誤, match="^來源節流失敗$") as 捕獲:
            來源驗證失敗節流器(路徑).記錄失敗("192.0.2.1", "same", 1, 99, 60)
        assert not 釋放甲.is_set() and 甲.is_alive()
        assert 乙已進入SQLite.is_set()
        assert len(乙鎖錯誤) == 1 and type(乙鎖錯誤[0]) is sqlite3.OperationalError
        assert str(乙鎖錯誤[0]).lower() in ("database is locked", "database is busy")
        assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
        assert 事件[1] == ["BEGIN", "SQLITE:BEGIN IMMEDIATE", "CLOSE"]
        assert 已移除追蹤 == [True]
    finally:
        釋放甲.set()
        甲.join(5)
    assert not 甲.is_alive() and 甲錯誤 == [] and 甲結果 == [1]

    乙結果 = 來源驗證失敗節流器(路徑).記錄失敗(
        "192.0.2.1", "same", 1, 99, 60)
    assert 乙結果.計數 == 2
    assert 事件[0] == ["BEGIN", "UPSERT", "FETCH", "COMMIT", "CLOSE"]
    assert 事件[2] == ["BEGIN", "UPSERT", "FETCH", "COMMIT", "CLOSE"]
    assert len(連線們) == 3 and all(type(連線) is sqlite3.Connection for 連線 in 連線們)
    for 連線 in 連線們:
        with pytest.raises(sqlite3.ProgrammingError, match="^Cannot operate on a closed database\\.$"):
            連線.execute("SELECT 1")
    assert _查詢(路徑, "SELECT failure_count FROM auth_failure_rate_counters") == [(2,)]


@pytest.mark.parametrize("錯誤類型", [自訂基礎錯誤, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_來源節流KISG與自訂Base清理輸入且不接收金鑰(tmp_path, monkeypatch, 錯誤類型):
    原始 = 錯誤類型("source-secret-marker")
    路徑 = _建立來源節流資料庫(tmp_path, "source-secret-marker.sqlite")

    def 注入連接(*args, **kwargs):
        del args, kwargs
        raise 原始

    monkeypatch.setattr(節流模組, "_連接SQLite", 注入連接)
    服務 = 來源驗證失敗節流器(路徑)
    預期 = 錯誤類型 if 錯誤類型 in (KeyboardInterrupt, SystemExit, GeneratorExit) else 來源節流錯誤
    with pytest.raises(預期) as 捕獲:
        服務.記錄失敗("192.0.2.1", "source-secret-marker", 0, 1, 60)
    if 預期 is 來源節流錯誤:
        assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    else:
        assert 捕獲.value is 原始
    _斷言產品追蹤無標記(捕獲.value, "source-secret-marker")
    assert "key" not in str(inspect.signature(服務.記錄失敗)).lower()
