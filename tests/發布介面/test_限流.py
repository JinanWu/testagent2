"""發布介面 UTC 固定視窗與平台限流邊界測試。"""

import math
import os
import sqlite3
import threading
import time
import traceback
from dataclasses import FrozenInstanceError

import pytest

from 繁中代理.發布介面.呼叫.限流 import (
    最大安全時間戳記,
    建立限流錯誤片段,
    增加雙層計數並判定,
    增加限流計數,
    固定視窗,
    時間戳記錯誤,
    計算固定視窗,
    限流決策,
    限流上限錯誤,
    限流錯誤片段,
    限流計數錯誤,
    驗證限流上限,
)


class 整數子類(int):
    """用來證明平台邊界拒絕 int subclass。"""


class 浮點子類(float):
    """用來證明時間邊界拒絕 float subclass。"""


class 惡意加法物件:
    """若DTO在exact-type驗證前做加法，就會留下呼叫證據。"""

    被呼叫 = False

    def __add__(self, other):
        惡意加法物件.被呼叫 = True
        raise RuntimeError("不應執行")


def _值含標記(值, 標記, 已見=None):
    """只走 exact builtins 與已知 immutable-slot DTO，不觸發敵對 callback。"""
    if 已見 is None:
        已見 = set()
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) in (tuple, list, set, frozenset):
        return any(_值含標記(項, 標記, 已見) for 項 in 值)
    if type(值) is dict:
        return any(
            _值含標記(鍵, 標記, 已見) or _值含標記(項, 標記, 已見)
            for 鍵, 項 in dict.items(值)
        )
    if type(值) is 限流決策:
        return any(
            _值含標記(object.__getattribute__(值, 欄位), 標記, 已見)
            for 欄位 in ("允許", "端點計數", "憑證計數", "超限範圍", "重試秒數", "端點上限", "憑證上限")
        )
    if type(值) is 限流錯誤片段:
        return any(
            _值含標記(object.__getattribute__(值, 欄位), 標記, 已見)
            for 欄位 in ("範圍", "重試秒數", "_標頭值")
            if hasattr(值, 欄位)
        )
    return False


def _斷言產品追蹤無標記(錯誤, 標記):
    for 框架, _ in traceback.walk_tb(錯誤.__traceback__):
        if "/繁中代理/" in 框架.f_code.co_filename:
            for 值 in tuple(框架.f_locals.values()):
                assert not _值含標記(值, 標記, set()), 框架.f_code.co_name


@pytest.mark.parametrize("上限", [1, 10_000])
def test_限流上限接受平台邊界(上限):
    """端點與憑證確認值共用一組固定平台邊界。"""
    assert 驗證限流上限(上限) == 上限


@pytest.mark.parametrize("上限", [0, 10_001, True, 整數子類(1), 1.0, "1"])
def test_限流上限拒絕非精確整數或超界值且錯誤固定(上限):
    """環境或 Python 子型別不得放寬已確認的限流上限。"""
    with pytest.raises(限流上限錯誤, match="^限流上限必須是 1 到 10000 的整數$"):
        驗證限流上限(上限)


@pytest.mark.parametrize(
    ("時間戳記", "預期開始", "預期結束"),
    [
        (0, 0, 60),
        (59, 0, 60),
        (59.999, 0, 60),
        (60, 60, 120),
        (119.999, 60, 120),
        (120, 120, 180),
    ],
)
def test_固定視窗為六十秒且邊界歸入下一視窗(時間戳記, 預期開始, 預期結束):
    """視窗使用 UTC epoch 算術，不含時區或夏令時間語意。"""
    assert 計算固定視窗(時間戳記) == 固定視窗(預期開始, 預期結束)


@pytest.mark.parametrize("時間戳記", [0, 59, 60, 120, 最大安全時間戳記])
def test_整數與等值浮點時間戳記得到相同視窗(時間戳記):
    """安全範圍內的整數與精確等值浮點輸入結果一致。"""
    assert 計算固定視窗(時間戳記) == 計算固定視窗(float(時間戳記))


def test_固定視窗不受執行環境時區或DST影響():
    """同一春秋 DST 邊界 epoch 在多個 TZ 設定下都產生相同結果。"""
    if not hasattr(time, "tzset"):
        pytest.skip("平台不支援 tzset")
    原始時區 = os.environ.get("TZ")
    時間戳記 = (1_710_054_000, 1_730_613_600)
    try:
        結果 = []
        for 時區 in ("UTC0", "America/New_York", "Asia/Taipei"):
            os.environ["TZ"] = 時區
            time.tzset()
            結果.append(tuple(計算固定視窗(值) for 值 in 時間戳記))
        assert 結果[0] == 結果[1] == 結果[2]
    finally:
        if 原始時區 is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = 原始時區
        time.tzset()


@pytest.mark.parametrize(
    "時間戳記",
    [
        -1,
        True,
        整數子類(60),
        浮點子類(60.0),
        "60",
        math.nan,
        math.inf,
        -math.inf,
        最大安全時間戳記 + 0.5,
        最大安全時間戳記 + 1,
        10**400,
    ],
)
def test_固定視窗拒絕無效時間戳記且錯誤固定(時間戳記):
    """算術前先拒絕非精確內建數值、非有限值與安全範圍外值。"""
    with pytest.raises(時間戳記錯誤, match="^時間戳記必須是安全範圍內的非負有限數值$"):
        計算固定視窗(時間戳記)


def test_最大安全時間戳記仍可決定視窗():
    """year 9999 最後一秒是公開支援的最大 epoch。"""
    assert 計算固定視窗(最大安全時間戳記) == 固定視窗(253_402_300_740, 253_402_300_800)


def test_固定視窗DTO為精確凍結且使用slots():
    """視窗身分只包含精確整數秒的開始與結束。"""
    視窗 = 固定視窗(60, 120)
    assert not hasattr(視窗, "__dict__")
    with pytest.raises(FrozenInstanceError):
        視窗.開始秒 = 0
    with pytest.raises(時間戳記錯誤):
        固定視窗(True, 60)
    with pytest.raises(時間戳記錯誤):
        固定視窗(60, 121)


@pytest.mark.parametrize("開始秒", ["0", object(), 惡意加法物件()])
def test_固定視窗DTO在算術前拒絕非精確整數(開始秒):
    """敵對開始值不得觸發加法，也不得洩漏原生例外。"""
    惡意加法物件.被呼叫 = False
    with pytest.raises(時間戳記錯誤, match="^固定視窗邊界無效$"):
        固定視窗(開始秒, 60)
    assert 惡意加法物件.被呼叫 is False


def test_固定視窗DTO禁止衍生類別():
    """公開視窗只允許exact frozen slotted型別。"""
    with pytest.raises(TypeError, match="^固定視窗不可被繼承$"):
        class 衍生固定視窗(固定視窗):
            pass


建表語句 = """CREATE TABLE rate_limit_counters (
scope_type TEXT NOT NULL CHECK(scope_type IN ('endpoint','credential')),
scope_id TEXT NOT NULL CHECK(length(scope_id)>0),
window_start INTEGER NOT NULL CHECK(window_start>=0),
request_count INTEGER NOT NULL CHECK(request_count>=0),
updated_at REAL NOT NULL CHECK(updated_at>=0),
PRIMARY KEY(scope_type,scope_id,window_start))"""


_待關閉測試連線 = []


def _追蹤測試連線(連線):
    _待關閉測試連線.append(連線)
    return 連線


@pytest.fixture(autouse=True)
def _每案例關閉所有測試連線():
    """即使assertion中斷，也回滾並關閉本案例建立的每個SQLite handle。"""
    try:
        yield
    finally:
        while _待關閉測試連線:
            連線 = _待關閉測試連線.pop()
            try:
                連線.close()
            except sqlite3.Error:
                pass


def 建立限流資料庫(路徑=":memory:"):
    連線 = _追蹤測試連線(sqlite3.connect(路徑))
    連線.execute(建表語句)
    連線.commit()
    return 連線


def test_UPSERT計數與各維度隔離且只使用單一原子異動():
    連線 = 建立限流資料庫()
    SQL紀錄 = []
    連線.set_trace_callback(SQL紀錄.append)
    連線.execute("BEGIN")
    assert 增加限流計數(連線, "endpoint", "ep-1", 固定視窗(0, 60), 1.0) == 1
    assert 增加限流計數(連線, "endpoint", "ep-1", 固定視窗(0, 60), 2) == 2
    assert 增加限流計數(連線, "credential", "ep-1", 固定視窗(0, 60), 2) == 1
    assert 增加限流計數(連線, "endpoint", "ep:2", 固定視窗(60, 120), 2) == 1
    列 = 連線.execute(
        "SELECT scope_type,scope_id,window_start,request_count,typeof(request_count) "
        "FROM rate_limit_counters ORDER BY 1,2,3"
    ).fetchall()
    assert len(列) == 3
    assert [項[3:] for 項 in 列] == [(1, "integer"), (2, "integer"), (1, "integer")]
    異動 = [SQL for SQL in SQL紀錄 if SQL.lstrip().upper().startswith("INSERT")]
    assert len(異動) == 4
    assert all("ON CONFLICT(scope_type,scope_id,window_start)" in SQL for SQL in 異動)
    assert all("DO UPDATE SET request_count=request_count+1" in SQL for SQL in 異動)
    assert not any(SQL.lstrip().upper().startswith("UPDATE") for SQL in SQL紀錄)


def test_交易由呼叫者擁有且提交前不可見並可回滾(tmp_path):
    路徑 = tmp_path / "rate.sqlite"
    甲 = 建立限流資料庫(路徑)
    乙 = _追蹤測試連線(sqlite3.connect(路徑))
    try:
        with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
            增加限流計數(甲, "endpoint", "ep", 固定視窗(0, 60), 0)
        甲.execute("BEGIN")
        assert 增加限流計數(甲, "endpoint", "ep", 固定視窗(0, 60), 0) == 1
        assert 乙.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0
        甲.commit()
        assert 乙.execute("SELECT request_count FROM rate_limit_counters").fetchone()[0] == 1
        甲.execute("BEGIN")
        assert 增加限流計數(甲, "endpoint", "ep", 固定視窗(0, 60), 1) == 2
        甲.rollback()
        assert 乙.execute("SELECT request_count FROM rate_limit_counters").fetchone()[0] == 1
    finally:
        甲.close()
        乙.close()


def test_多連線以呼叫者BEGIN_IMMEDIATE原子累加而不遺失更新(tmp_path):
    """R04/R81/R95：第二個BEGIN確實送出並受阻時，同鍵計數依序成為一與二。"""
    路徑 = tmp_path / "concurrent-rate.sqlite"
    建立限流資料庫(路徑).close()
    期限 = time.monotonic() + 5
    甲已鎖定 = threading.Event()
    乙已送出BEGIN = threading.Event()
    結果: list[int | None] = [None, None]
    錯誤: list[list[tuple[str, BaseException]]] = [[], []]

    def 等待(事件: threading.Event, 名稱: str):
        剩餘秒數 = 期限 - time.monotonic()
        if 剩餘秒數 <= 0 or not 事件.wait(剩餘秒數):
            raise TimeoutError(f"等待{名稱}逾時")

    def 清理(索引: int, 連線):
        if 連線 is None:
            return
        if 連線.in_transaction:
            try:
                連線.rollback()
            except BaseException as 例外:
                錯誤[索引].append(("rollback", 例外))
        try:
            連線.set_trace_callback(None)
        except BaseException as 例外:
            錯誤[索引].append(("trace", 例外))
        try:
            連線.close()
        except BaseException as 例外:
            錯誤[索引].append(("close", 例外))

    def 寫入甲():
        連線 = None
        try:
            連線 = sqlite3.connect(路徑, timeout=max(0.001, 期限 - time.monotonic()))
            連線.execute("BEGIN IMMEDIATE")
            甲已鎖定.set()
            等待(乙已送出BEGIN, "乙送出BEGIN")
            結果[0] = 增加限流計數(連線, "endpoint", "same-key", 固定視窗(0, 60), 0)
            連線.commit()
        except BaseException as 例外:
            錯誤[0].append(("primary", 例外))
        finally:
            清理(0, 連線)

    def 寫入乙():
        連線 = None
        try:
            連線 = sqlite3.connect(路徑, timeout=max(0.001, 期限 - time.monotonic()))

            def 追蹤(語句: str):
                if 語句.strip().upper() == "BEGIN IMMEDIATE":
                    乙已送出BEGIN.set()

            連線.set_trace_callback(追蹤)
            等待(甲已鎖定, "甲取得寫鎖")
            連線.execute("BEGIN IMMEDIATE")
            結果[1] = 增加限流計數(連線, "endpoint", "same-key", 固定視窗(0, 60), 1)
            連線.commit()
        except BaseException as 例外:
            錯誤[1].append(("primary", 例外))
        finally:
            清理(1, 連線)

    執行緒 = [threading.Thread(target=目標, daemon=True) for 目標 in (寫入甲, 寫入乙)]
    for 執行 in 執行緒:
        執行.start()
    for 執行 in 執行緒:
        執行.join(max(0, 期限 - time.monotonic()))

    assert not any(執行.is_alive() for 執行 in 執行緒)
    assert 錯誤 == [[], []]
    assert 結果 == [1, 2]
    觀察者 = sqlite3.connect(路徑)
    try:
        assert 觀察者.execute(
            "SELECT request_count FROM rate_limit_counters "
            "WHERE scope_type='endpoint' AND scope_id='same-key' AND window_start=0"
        ).fetchone() == (2,)
    finally:
        觀察者.close()


def test_計數上溢關閉且既有列不變():
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    連線.execute("INSERT INTO rate_limit_counters VALUES('endpoint','max',0,9223372036854775806,0)")
    assert 增加限流計數(連線, "endpoint", "max", 固定視窗(0, 60), 1) == 9223372036854775807
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
        增加限流計數(連線, "endpoint", "max", 固定視窗(0, 60), 2)
    assert 連線.execute("SELECT request_count FROM rate_limit_counters").fetchone()[0] == 9223372036854775807
    assert 連線.in_transaction is True


@pytest.mark.parametrize("污染計數", [-1, 1.5, "corrupt"])
def test_相同table_info但既有計數污染時fail_closed且不異動(污染計數):
    """缺少migration CHECK時也不可把污染值修復後留給caller誤commit。"""
    連線 = _追蹤測試連線(sqlite3.connect(":memory:"))
    try:
        連線.execute(
            "CREATE TABLE rate_limit_counters("
            "scope_type TEXT NOT NULL,scope_id TEXT NOT NULL,window_start INTEGER NOT NULL,"
            "request_count INTEGER NOT NULL,updated_at REAL NOT NULL,"
            "PRIMARY KEY(scope_type,scope_id,window_start))"
        )
        連線.execute("INSERT INTO rate_limit_counters VALUES('endpoint','bad',0,?,0)", (污染計數,))
        連線.commit()
        原值 = 連線.execute("SELECT request_count,typeof(request_count) FROM rate_limit_counters").fetchone()
        連線.execute("BEGIN")

        with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
            增加限流計數(連線, "endpoint", "bad", 固定視窗(0, 60), 1)

        assert 連線.execute(
            "SELECT request_count,typeof(request_count) FROM rate_limit_counters"
        ).fetchone() == 原值
        assert 連線.in_transaction is True
    finally:
        連線.close()


class 字串子類(str):
    pass


@pytest.mark.parametrize(
    ("範圍", "識別碼", "視窗", "更新時間"),
    [
        ("other", "id", 固定視窗(0, 60), 0),
        (字串子類("endpoint"), "id", 固定視窗(0, 60), 0),
        ("endpoint", "", 固定視窗(0, 60), 0),
        ("endpoint", "bad id", 固定視窗(0, 60), 0),
        ("endpoint", 字串子類("id"), 固定視窗(0, 60), 0),
        ("endpoint", "id", object(), 0),
        ("endpoint", "id", 固定視窗(0, 60), True),
        ("endpoint", "id", 固定視窗(0, 60), math.nan),
        ("endpoint", "id", 固定視窗(0, 60), math.inf),
        ("endpoint", "id", 固定視窗(0, 60), 浮點子類(1)),
    ],
)
def test_無效領域值一律在SQL前拒絕(範圍, 識別碼, 視窗, 更新時間):
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
        增加限流計數(連線, 範圍, 識別碼, 視窗, 更新時間)
    assert 連線.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0


def test_遭竄改固定視窗在SQL前拒絕():
    視窗 = 固定視窗(0, 60)
    object.__setattr__(視窗, "結束秒", 61)
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    with pytest.raises(限流計數錯誤):
        增加限流計數(連線, "endpoint", "id", 視窗, 0)
    assert 連線.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0


@pytest.mark.parametrize(
    "建表",
    [
        None,
        "CREATE TABLE rate_limit_counters(scope_type TEXT,scope_id TEXT,window_start INTEGER,request_count INTEGER,updated_at REAL)",
        "CREATE TABLE rate_limit_counters(scope_type TEXT NOT NULL,scope_id TEXT NOT NULL,window_start INTEGER NOT NULL,request_count TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(scope_type,scope_id,window_start))",
    ],
)
def test_缺表欄位型別或主鍵漂移皆關閉(建表):
    連線 = _追蹤測試連線(sqlite3.connect(":memory:"))
    if 建表:
        連線.execute(建表)
    連線.execute("BEGIN")
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
        增加限流計數(連線, "endpoint", "id", 固定視窗(0, 60), 0)
    assert 連線.in_transaction is True


class 自訂基礎錯誤(BaseException):
    pass


class 注入連線:
    def __init__(self, 錯誤, 階段):
        self.錯誤, self.階段 = 錯誤, 階段

    @property
    def in_transaction(self):
        if self.階段 == "transaction":
            raise self.錯誤
        return True

    def execute(self, *參數):
        del 參數
        raise self.錯誤


@pytest.mark.parametrize("錯誤", [RuntimeError("secret"), 自訂基礎錯誤("secret")])
@pytest.mark.parametrize("階段", ["transaction", "execute"])
def test_一般與自訂基礎例外皆固定且無因果(錯誤, 階段):
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$") as 捕獲:
        增加限流計數(注入連線(錯誤, 階段), "endpoint", "id", 固定視窗(0, 60), 0)
    assert 捕獲.value.__cause__ is None and 捕獲.value.__context__ is None


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("階段", ["transaction", "execute"])
def test_控制流程例外保持原物件與參數(錯誤類型, 階段):
    錯誤 = 錯誤類型("marker")
    with pytest.raises(錯誤類型) as 捕獲:
        增加限流計數(注入連線(錯誤, 階段), "endpoint", "id", 固定視窗(0, 60), 0)
    assert 捕獲.value is 錯誤 and 捕獲.value.args == ("marker",)


預期欄位 = [
    (0, "scope_type", "TEXT", 1, None, 1),
    (1, "scope_id", "TEXT", 1, None, 2),
    (2, "window_start", "INTEGER", 1, None, 3),
    (3, "request_count", "INTEGER", 1, None, 0),
    (4, "updated_at", "REAL", 1, None, 0),
]


class 注入游標:
    def __init__(self, 結果, 讀取錯誤=None, 關閉錯誤=None):
        self.結果, self.讀取錯誤, self.關閉錯誤 = 結果, 讀取錯誤, 關閉錯誤

    def fetchall(self):
        return self.結果

    def fetchone(self):
        if self.讀取錯誤:
            raise self.讀取錯誤
        return self.結果

    def close(self):
        if self.關閉錯誤:
            raise self.關閉錯誤


class 游標注入連線:
    in_transaction = True

    def __init__(self, 讀取錯誤=None, 關閉錯誤=None):
        self.讀取錯誤, self.關閉錯誤, self.次數 = 讀取錯誤, 關閉錯誤, 0

    def execute(self, 語句, 參數):
        del 語句, 參數
        self.次數 += 1
        if self.次數 == 1:
            return 注入游標(預期欄位)
        return 注入游標((1,), self.讀取錯誤, self.關閉錯誤)


def test_fetchone一般錯誤後的close控制流程優先():
    清理控制 = KeyboardInterrupt("cleanup-marker")
    連線 = 游標注入連線(RuntimeError("primary-marker"), 清理控制)
    with pytest.raises(KeyboardInterrupt) as 捕獲:
        增加限流計數(連線, "endpoint", "id", 固定視窗(0, 60), 0)
    assert 捕獲.value is 清理控制


def test_fetchone控制流程優先於close控制流程():
    主要控制 = SystemExit("primary-marker")
    連線 = 游標注入連線(主要控制, GeneratorExit("cleanup-marker"))
    with pytest.raises(SystemExit) as 捕獲:
        增加限流計數(連線, "endpoint", "id", 固定視窗(0, 60), 0)
    assert 捕獲.value is 主要控制


def test_fetchone自訂BaseException轉固定錯誤():
    with pytest.raises(限流計數錯誤) as 捕獲:
        增加限流計數(
            游標注入連線(自訂基礎錯誤("private-marker")),
            "endpoint",
            "id",
            固定視窗(0, 60),
            0,
        )
    assert 捕獲.value.__cause__ is None and 捕獲.value.__context__ is None


class 敵對鍵盤中斷(KeyboardInterrupt):
    def __setattr__(self, 名稱, 值):
        del 名稱, 值
        raise RuntimeError("不得呼叫覆寫方法")


def test_控制流程子類覆寫setattr仍保持原物件():
    錯誤 = 敵對鍵盤中斷("marker")
    with pytest.raises(敵對鍵盤中斷) as 捕獲:
        增加限流計數(注入連線(錯誤, "execute"), "endpoint", "id", 固定視窗(0, 60), 0)
    assert 捕獲.value is 錯誤 and 捕獲.value.__context__ is None


def test_雙層計數先計數再決策且超限端點優先():
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    第一次 = 增加雙層計數並判定(連線, "ep", "cred", 2, 2, 0)
    第二次 = 增加雙層計數並判定(連線, "ep", "cred", 2, 2, 1)
    第三次 = 增加雙層計數並判定(連線, "ep", "cred", 2, 2, 2)
    第四次 = 增加雙層計數並判定(連線, "ep", "cred", 2, 2, 3)
    assert 第一次 == 限流決策(True, 1, 1, None, None)
    assert 第二次 == 限流決策(True, 2, 2, None, None)
    assert 第三次 == 限流決策(False, 3, 3, "endpoint", 58)
    assert 第四次 == 限流決策(False, 4, 4, "endpoint", 57)
    assert (第三次.端點上限, 第三次.憑證上限) == (2, 2)
    assert 連線.execute(
        "SELECT scope_type,request_count,typeof(request_count) FROM rate_limit_counters ORDER BY 1"
    ).fetchall() == [("credential", 4, "integer"), ("endpoint", 4, "integer")]


@pytest.mark.parametrize(
    ("端點上限", "憑證上限", "預期範圍"),
    [(1, 10, "endpoint"), (10, 1, "credential"), (1, 1, "endpoint")],
)
def test_雙層各自超限與同時超限範圍明確(端點上限, 憑證上限, 預期範圍):
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    增加雙層計數並判定(連線, "ep", "cred", 10, 10, 0)
    決策 = 增加雙層計數並判定(連線, "ep", "cred", 端點上限, 憑證上限, 1)
    assert 決策 == 限流決策(False, 2, 2, 預期範圍, 59)


def test_端點已超限仍增加憑證且拒絕呼叫持續消耗雙層額度():
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    結果 = [增加雙層計數並判定(連線, "ep", "cred", 1, 99, 秒) for 秒 in range(4)]
    assert [(項.允許, 項.端點計數, 項.憑證計數) for 項 in 結果] == [
        (True, 1, 1), (False, 2, 2), (False, 3, 3), (False, 4, 4)
    ]


def test_雙層識別碼與視窗在資料列中彼此隔離():
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    for 參數 in (("ep-a", "cr-a", 0), ("ep-b", "cr-a", 0), ("ep-a", "cr-a", 60)):
        增加雙層計數並判定(連線, 參數[0], 參數[1], 10, 10, 參數[2])
    assert 連線.execute(
        "SELECT scope_type,scope_id,window_start,request_count FROM rate_limit_counters ORDER BY 1,2,3"
    ).fetchall() == [
        ("credential", "cr-a", 0, 2), ("credential", "cr-a", 60, 1),
        ("endpoint", "ep-a", 0, 1), ("endpoint", "ep-a", 60, 1),
        ("endpoint", "ep-b", 0, 1),
    ]


@pytest.mark.parametrize(("時間戳記", "預期"), [(0, 60), (59, 1), (59.5, 1), (60, 60)])
def test_拒絕重試秒數使用精確上捨入與新視窗邊界(時間戳記, 預期):
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    增加雙層計數並判定(連線, "ep", "cred", 10, 10, 時間戳記)
    決策 = 增加雙層計數並判定(連線, "ep", "cred", 1, 1, 時間戳記)
    assert 決策.重試秒數 == 預期
    assert type(決策.重試秒數) is int


def test_雙層交易由呼叫者擁有且提交前不可見並可回滾(tmp_path):
    路徑 = tmp_path / "dual.sqlite"
    甲 = 建立限流資料庫(路徑)
    乙 = _追蹤測試連線(sqlite3.connect(路徑))
    with pytest.raises(限流計數錯誤):
        增加雙層計數並判定(甲, "ep", "cred", 1, 1, 0)
    甲.execute("BEGIN")
    增加雙層計數並判定(甲, "ep", "cred", 1, 1, 0)
    assert 乙.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0
    甲.rollback()
    assert 乙.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0
    甲.close()
    乙.close()


@pytest.mark.parametrize(
    "參數",
    [
        ("bad id", "cred", 1, 1, 0), (字串子類("ep"), "cred", 1, 1, 0),
        ("ep", "", 1, 1, 0), ("ep", "cred", True, 1, 0),
        ("ep", "cred", 1, 0, 0), ("ep", "cred", 1, 1, True),
        ("ep", "cred", 1, 1, 浮點子類(1)), ("ep", "cred", 1, 1, math.inf),
    ],
)
def test_雙層所有輸入先驗證且無效值不寫入(參數):
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$") as 捕獲:
        增加雙層計數並判定(連線, *參數)
    assert 捕獲.value.__cause__ is None and 捕獲.value.__context__ is None
    assert 連線.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0


def test_第二層失敗保留第一層異動供呼叫者回滾且不洩漏識別碼():
    連線 = 建立限流資料庫()
    連線.execute("CREATE TRIGGER 阻擋憑證 BEFORE INSERT ON rate_limit_counters "
                 "WHEN NEW.scope_type='credential' BEGIN SELECT RAISE(FAIL,'private-marker'); END")
    連線.execute("BEGIN")
    with pytest.raises(限流計數錯誤) as 捕獲:
        增加雙層計數並判定(連線, "ep-private-marker", "cred-private-marker", 2, 2, 0)
    assert 連線.in_transaction is True
    assert 連線.execute("SELECT scope_type,request_count FROM rate_limit_counters").fetchall() == [("endpoint", 1)]
    for 框架, _ in traceback.walk_tb(捕獲.value.__traceback__):
        if "/繁中代理/" in 框架.f_code.co_filename:
            assert "private-marker" not in repr(框架.f_locals)
    連線.rollback()
    assert 連線.execute("SELECT count(*) FROM rate_limit_counters").fetchone()[0] == 0


class 連線代理:
    def __init__(self, 連線):
        self.連線 = 連線

    @property
    def in_transaction(self):
        return self.連線.in_transaction

    def execute(self, *參數):
        return self.連線.execute(*參數)


def test_雙層服務接受結構性連線代理():
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    assert 增加雙層計數並判定(連線代理(連線), "ep", "cred", 1, 1, 0).允許 is True


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_雙層服務保持底層控制流程原物件且清除識別碼(錯誤類型):
    錯誤 = 錯誤類型("control-marker")
    with pytest.raises(錯誤類型) as 捕獲:
        增加雙層計數並判定(注入連線(錯誤, "execute"), "ep-marker", "cred-marker", 1, 1, 0)
    assert 捕獲.value is 錯誤 and 捕獲.value.args == ("control-marker",)
    for 框架, _ in traceback.walk_tb(捕獲.value.__traceback__):
        if "/繁中代理/" in 框架.f_code.co_filename:
            assert "ep-marker" not in repr(框架.f_locals)


def test_限流決策DTO凍結精確不可衍生且拒絕偽造():
    決策 = 限流決策(True, 1, 1, None, None)
    assert not hasattr(決策, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(決策, "允許", False)
    with pytest.raises(限流計數錯誤):
        限流決策(True, True, 1, None, None)
    with pytest.raises(限流計數錯誤):
        限流決策(False, 1, 1, "endpoint", True)
    with pytest.raises(限流計數錯誤):
        限流決策(False, 1, 1, "endpoint", 0)
    with pytest.raises(TypeError, match="^限流決策不可被繼承$"):
        class 衍生限流決策(限流決策):
            pass


@pytest.mark.parametrize("範圍", ["endpoint", "credential"])
def test_端點與principal拒絕共用固定429錯誤片段及Retry_After(範圍):
    """RATE只交付錯誤片段；endpoint與invocation refs由INV協調者組合。"""
    上限 = (1, 10) if 範圍 == "endpoint" else (10, 1)
    結果 = 建立限流錯誤片段(限流決策(False, 2, 2, 範圍, 17, *上限))
    輸出 = 結果.to_json()
    assert type(結果) is 限流錯誤片段
    assert 輸出 == {
        "status_code": 429,
        "headers": {"Retry-After": "17"},
        "error": {
            "code": "rate_limit_exceeded",
            "message": "呼叫頻率超過限制。",
            "details": {"scope": 範圍, "retry_after_seconds": 17},
        },
    }
    assert "endpoint" not in 輸出 and "invocation" not in 輸出


@pytest.mark.parametrize(("時間戳記", "預期"), [(0, 60), (0.001, 60), (59, 1), (59.001, 1), (60, 60)])
def test_429標頭與body秒數同源且依reset差值上捨入(時間戳記, 預期):
    """Retry-After與body snapshot都沿用經驗證時間所算出的同一整數秒。"""
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    增加雙層計數並判定(連線, "ep", "cred", 10, 10, 時間戳記)
    決策 = 增加雙層計數並判定(連線, "ep", "cred", 1, 1, 時間戳記)
    輸出 = 建立限流錯誤片段(決策).to_json()
    assert 輸出["headers"]["Retry-After"] == str(預期)
    assert 輸出["error"]["details"]["retry_after_seconds"] == 預期


def test_建立429片段不再異動資料庫且每次輸出皆脫離():
    """Transport-neutral fragment不得額外消耗限流額度或外洩可變容器。"""
    連線 = 建立限流資料庫()
    連線.execute("BEGIN")
    增加雙層計數並判定(連線, "ep", "cred", 10, 10, 0)
    決策 = 增加雙層計數並判定(連線, "ep", "cred", 1, 1, 1)
    建立前 = 連線.total_changes
    結果 = 建立限流錯誤片段(決策)
    第一次, 第二次 = 結果.to_json(), 結果.to_json()
    第一次["headers"]["Retry-After"] = "secret-marker"
    第一次["error"]["details"]["scope"] = "secret-marker"
    assert 連線.total_changes == 建立前
    assert 第二次 == 結果.to_json()


@pytest.mark.parametrize(
    ("欄位", "值"),
    [("允許", True), ("超限範圍", "private-secret-marker"), ("重試秒數", 0), ("重試秒數", True)],
)
def test_429映射拒絕允許或遭竄改決策且不洩漏內容(欄位, 值):
    """Frozen DTO遭object.__setattr__偽造時仍固定fail closed。"""
    決策 = 限流決策(False, 2, 2, "endpoint", 1)
    object.__setattr__(決策, 欄位, 值)
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$") as 捕獲:
        建立限流錯誤片段(決策)
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    for 框架, _ in traceback.walk_tb(捕獲.value.__traceback__):
        if "/繁中代理/" in 框架.f_code.co_filename:
            assert "private-secret-marker" not in repr(框架.f_locals)


def test_429片段DTO凍結不可衍生且序列化會重驗own_state():
    """結果即使被低階竄改，也不能輸出不一致的header/body。"""
    結果 = 建立限流錯誤片段(限流決策(False, 2, 2, "credential", 8, 10, 1))
    assert not hasattr(結果, "__dict__")
    with pytest.raises(FrozenInstanceError):
        結果.重試秒數 = 7
    object.__setattr__(結果, "重試秒數", 7)
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
        結果.to_json()
    with pytest.raises(TypeError, match="^限流錯誤片段不可被繼承$"):
        class 衍生限流錯誤片段(限流錯誤片段):
            pass


@pytest.mark.parametrize("案例", ["missing", "endpoint", "credential-precedence"])
def test_429片段拒絕缺少權威上限或不可能的計數上限配對(案例):
    if 案例 == "missing":
        決策 = 限流決策(False, 2, 2, "endpoint", 1)
    elif 案例 == "endpoint":
        決策 = 限流決策(False, 3, 2, "endpoint", 1, 2, 10)
        object.__setattr__(決策, "端點計數", 2)
    else:
        決策 = 限流決策(False, 2, 2, "credential", 1, 10, 1)
        object.__setattr__(決策, "端點上限", 1)
    with pytest.raises(限流計數錯誤, match="^限流計數失敗$"):
        建立限流錯誤片段(決策)


@pytest.mark.parametrize("目標", ["decision-counter", "fragment-state"])
def test_偽造決策與片段失敗時所有產品框架不保留巢狀標記(目標):
    標記 = "nested-private-marker"
    if 目標 == "decision-counter":
        決策 = 限流決策(False, 2, 2, "endpoint", 1, 1, 10)
        object.__setattr__(決策, "端點計數", {"nested": [標記]})
        with pytest.raises(限流計數錯誤) as 捕獲:
            建立限流錯誤片段(決策)
    else:
        片段 = 建立限流錯誤片段(限流決策(False, 2, 2, "endpoint", 1, 1, 10))
        object.__setattr__(片段, "範圍", {"nested": [標記]})
        with pytest.raises(限流計數錯誤) as 捕獲:
            片段.to_json()
    assert 捕獲.value.__cause__ is 捕獲.value.__context__ is None
    _斷言產品追蹤無標記(捕獲.value, 標記)
