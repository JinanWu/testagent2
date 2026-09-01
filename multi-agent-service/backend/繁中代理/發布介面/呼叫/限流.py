"""發布介面的 UTC 固定視窗與平台限流邊界。"""

from dataclasses import dataclass, field
import math
import re
from typing import cast

固定視窗秒數 = 60
最小限流上限 = 1
最大限流上限 = 10_000
預設端點每分鐘上限 = 60
預設憑證每分鐘上限 = 30
最大安全時間戳記 = 253_402_300_799
最大限流計數 = 9_223_372_036_854_775_807

_安全範圍識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}").fullmatch
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_預期限流資料表 = [
    (0, "scope_type", "TEXT", 1, None, 1),
    (1, "scope_id", "TEXT", 1, None, 2),
    (2, "window_start", "INTEGER", 1, None, 3),
    (3, "request_count", "INTEGER", 1, None, 0),
    (4, "updated_at", "REAL", 1, None, 0),
]
_原子增加語句 = """INSERT INTO rate_limit_counters(
scope_type,scope_id,window_start,request_count,updated_at) VALUES(?,?,?,1,?)
ON CONFLICT(scope_type,scope_id,window_start) DO UPDATE SET request_count=request_count+1,
updated_at=excluded.updated_at
WHERE typeof(request_count)='integer' AND request_count >= 0
AND request_count < 9223372036854775807 RETURNING request_count"""


class 限流上限錯誤(ValueError):
    """表示限流上限不符合固定平台邊界。"""


class 時間戳記錯誤(ValueError):
    """表示 epoch 時間戳記無法安全用於固定視窗。"""


class 限流計數錯誤(RuntimeError):
    """表示限流計數未能在呼叫者交易內安全完成。"""


def _決策值有效(
    允許: object,
    端點計數: object,
    憑證計數: object,
    超限範圍: object,
    重試秒數: object,
    端點上限: object,
    憑證上限: object,
    要求上限: bool,
) -> bool:
    """以 exact 純量確認決策，且不對敵對值呼叫使用者方法。"""
    if (
        type(允許) is not bool
        or type(端點計數) is not int
        or not 1 <= 端點計數 <= 最大限流計數
        or type(憑證計數) is not int
        or not 1 <= 憑證計數 <= 最大限流計數
    ):
        return False
    有上限 = 端點上限 is not None or 憑證上限 is not None
    if 有上限 and (
        type(端點上限) is not int
        or not 最小限流上限 <= 端點上限 <= 最大限流上限
        or type(憑證上限) is not int
        or not 最小限流上限 <= 憑證上限 <= 最大限流上限
    ):
        return False
    if 要求上限 and not 有上限:
        return False
    if not 有上限 and (端點上限 is not None or 憑證上限 is not None):
        return False
    if 允許:
        if 超限範圍 is not None or 重試秒數 is not None:
            return False
        return not 有上限 or (端點計數 <= 端點上限 and 憑證計數 <= 憑證上限)
    if (
        type(超限範圍) is not str
        or 超限範圍 not in ("endpoint", "credential")
        or type(重試秒數) is not int
        or not 1 <= 重試秒數 <= 固定視窗秒數
    ):
        return False
    if not 有上限:
        return not 要求上限
    if 超限範圍 == "endpoint":
        return 端點計數 > 端點上限
    return 端點計數 <= 端點上限 and 憑證計數 > 憑證上限


@dataclass(frozen=True, slots=True, init=False)
class 限流決策:
    """攜帶雙層計數及其權威配置上限的不可變純量決策。"""

    允許: bool
    端點計數: int
    憑證計數: int
    超限範圍: str | None
    重試秒數: int | None
    端點上限: int | None = field(default=None, compare=False)
    憑證上限: int | None = field(default=None, compare=False)

    def __init__(
        self, 允許: object, 端點計數: object, 憑證計數: object,
        超限範圍: object, 重試秒數: object,
        端點上限: object = None, 憑證上限: object = None,
    ) -> None:
        """保留舊五欄建構相容性；權威邊界另要求兩個上限。"""
        if not _決策值有效(
            允許, 端點計數, 憑證計數, 超限範圍, 重試秒數,
            端點上限, 憑證上限, False,
        ):
            self = 允許 = 端點計數 = 憑證計數 = None
            超限範圍 = 重試秒數 = 端點上限 = 憑證上限 = None
            raise 限流計數錯誤("限流計數失敗") from None
        object.__setattr__(self, "允許", 允許)
        object.__setattr__(self, "端點計數", 端點計數)
        object.__setattr__(self, "憑證計數", 憑證計數)
        object.__setattr__(self, "超限範圍", 超限範圍)
        object.__setattr__(self, "重試秒數", 重試秒數)
        object.__setattr__(self, "端點上限", 端點上限)
        object.__setattr__(self, "憑證上限", 憑證上限)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """禁止衍生公開決策 DTO，維持 exact type 契約。"""
        del cls, kwargs
        raise TypeError("限流決策不可被繼承")


@dataclass(frozen=True, slots=True)
class 限流錯誤片段:
    """供 INV 組合 refs 與完整信封的 transport-neutral 429 錯誤片段。"""

    範圍: str
    重試秒數: int
    _標頭值: str = field(init=False, repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """禁止衍生公開片段 DTO，避免改寫序列化契約。"""
        del cls, kwargs
        raise TypeError("限流錯誤片段不可被繼承")

    def 轉為JSON(self) -> dict[str, object]:
        """重驗 own state，輸出不含 endpoint／invocation refs 的 fresh 片段。"""
        範圍 = 重試秒數 = 標頭值 = 輸出 = None
        失敗 = False
        try:
            範圍 = self.範圍
            重試秒數 = self.重試秒數
            標頭值 = self._標頭值
            if (
                type(self) is not 限流錯誤片段
                or type(範圍) is not str
                or 範圍 not in ("endpoint", "credential")
                or type(重試秒數) is not int
                or not 1 <= 重試秒數 <= 固定視窗秒數
                or type(標頭值) is not str
                or 標頭值 != str(重試秒數)
            ):
                失敗 = True
            else:
                輸出 = {
                    "status_code": 429,
                    "headers": {"Retry-After": 標頭值},
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "呼叫頻率超過限制。",
                        "details": {"scope": 範圍, "retry_after_seconds": 重試秒數},
                    },
                }
        except _控制流程例外 as 控制流程:
            BaseException.__setattr__(控制流程, "__cause__", None)
            BaseException.__setattr__(控制流程, "__context__", None)
            BaseException.__setattr__(控制流程, "__suppress_context__", True)
            self = 範圍 = 重試秒數 = 標頭值 = 輸出 = 控制流程 = None
            raise
        except BaseException:
            失敗 = True
        self = 範圍 = 重試秒數 = 標頭值 = None
        if 失敗 or type(輸出) is not dict:
            輸出 = None
            raise 限流計數錯誤("限流計數失敗") from None
        return 輸出

setattr(限流錯誤片段, "to_json", 限流錯誤片段.轉為JSON)


def 建立限流錯誤片段(決策: object) -> 限流錯誤片段:
    """驗證權威計數／上限配對後，建立不含 refs 的 429 錯誤片段。"""
    允許 = 端點計數 = 憑證計數 = 超限範圍 = 重試秒數 = None
    端點上限 = 憑證上限 = 片段 = None
    失敗 = False
    try:
        if type(決策) is not 限流決策:
            失敗 = True
        else:
            允許 = 決策.允許
            端點計數 = 決策.端點計數
            憑證計數 = 決策.憑證計數
            超限範圍 = 決策.超限範圍
            重試秒數 = 決策.重試秒數
            端點上限 = 決策.端點上限
            憑證上限 = 決策.憑證上限
            if 允許 is not False or not _決策值有效(
                允許, 端點計數, 憑證計數, 超限範圍, 重試秒數,
                端點上限, 憑證上限, True,
            ):
                失敗 = True
            else:
                片段 = 限流錯誤片段(cast(str, 超限範圍), cast(int, 重試秒數))
                object.__setattr__(片段, "_標頭值", str(重試秒數))
    except _控制流程例外 as 控制流程:
        BaseException.__setattr__(控制流程, "__cause__", None)
        BaseException.__setattr__(控制流程, "__context__", None)
        BaseException.__setattr__(控制流程, "__suppress_context__", True)
        決策 = 允許 = 端點計數 = 憑證計數 = 超限範圍 = None
        重試秒數 = 端點上限 = 憑證上限 = 片段 = 控制流程 = None
        raise
    except BaseException:
        失敗 = True
    決策 = 允許 = 端點計數 = 憑證計數 = 超限範圍 = None
    重試秒數 = 端點上限 = 憑證上限 = None
    if 失敗 or type(片段) is not 限流錯誤片段:
        片段 = None
        raise 限流計數錯誤("限流計數失敗") from None
    return 片段


def 驗證限流上限(上限: object) -> int:
    """確認端點或憑證的每分鐘上限是平台允許的精確整數。"""
    if type(上限) is not int or not 最小限流上限 <= 上限 <= 最大限流上限:
        raise 限流上限錯誤("限流上限必須是 1 到 10000 的整數")
    return 上限


def _時間戳記有效(時間戳記: object) -> bool:
    """判斷輸入是否為可安全計算的精確內建 epoch 數值。"""
    if type(時間戳記) is int:
        return 0 <= 時間戳記 <= 最大安全時間戳記
    if type(時間戳記) is float:
        return math.isfinite(時間戳記) and 0 <= 時間戳記 <= 最大安全時間戳記
    return False


@dataclass(frozen=True, slots=True)
class 固定視窗:
    """以 UTC epoch 精確整數秒表示的六十秒視窗身分。"""

    開始秒: int
    結束秒: int

    def __init_subclass__(cls, **kwargs: object) -> None:
        """禁止衍生公開視窗DTO，維持exact type契約。"""
        del cls, kwargs
        raise TypeError("固定視窗不可被繼承")

    def __post_init__(self) -> None:
        """拒絕非正規或遭子型別混入的公開 DTO 建構。"""
        if type(self) is not 固定視窗 or type(self.開始秒) is not int or type(self.結束秒) is not int:
            raise 時間戳記錯誤("固定視窗邊界無效")
        if not 0 <= self.開始秒 <= 最大安全時間戳記:
            raise 時間戳記錯誤("固定視窗邊界無效")
        if self.結束秒 != self.開始秒 + 固定視窗秒數 or self.開始秒 % 固定視窗秒數 != 0:
            raise 時間戳記錯誤("固定視窗邊界無效")


def 計算固定視窗(時間戳記: object) -> 固定視窗:
    """只以 UTC epoch 算術計算輸入所屬的六十秒固定視窗。"""
    if not _時間戳記有效(時間戳記):
        raise 時間戳記錯誤("時間戳記必須是安全範圍內的非負有限數值")
    if type(時間戳記) is int:
        開始秒 = (時間戳記 // 固定視窗秒數) * 固定視窗秒數
    else:
        浮點時間戳記 = cast(float, 時間戳記)
        開始秒 = math.floor(浮點時間戳記 / 固定視窗秒數) * 固定視窗秒數
    return 固定視窗(開始秒, 開始秒 + 固定視窗秒數)


def _執行並讀取(執行, 語句: str, 參數: tuple[object, ...], 方法名稱: str):
    """執行一個 statement，並依控制流程優先序關閉其 cursor。"""
    游標 = None
    主要為控制流程 = False
    try:
        游標 = 執行(語句, 參數)
        try:
            return getattr(游標, 方法名稱)()
        except _控制流程例外:
            主要為控制流程 = True
            raise
        finally:
            try:
                游標.close()
            except BaseException:
                if not 主要為控制流程:
                    raise
    finally:
        游標 = None
        執行 = None
        參數 = ()
        語句 = ""
        方法名稱 = ""


def 增加限流計數(
    連線: object,
    範圍類型: object,
    範圍識別碼: object,
    視窗: object,
    更新時間: object,
) -> int:
    """在呼叫者既有 SQLite 交易中，以單一 UPSERT 增加一個範圍計數。"""
    執行 = None
    資料表資訊 = None
    結果列 = None
    正規視窗 = None
    失敗 = False
    計數 = 0
    try:
        if type(範圍類型) is not str or 範圍類型 not in ("endpoint", "credential"):
            raise ValueError
        if type(範圍識別碼) is not str or _安全範圍識別碼(範圍識別碼) is None:
            raise ValueError
        if type(視窗) is not 固定視窗:
            raise ValueError
        正規視窗 = 固定視窗(視窗.開始秒, 視窗.結束秒)
        if not _時間戳記有效(更新時間):
            raise ValueError
        if getattr(連線, "in_transaction") is not True:
            raise ValueError
        執行 = getattr(連線, "execute")
        資料表資訊 = _執行並讀取(執行, "PRAGMA table_info(rate_limit_counters)", (), "fetchall")
        if type(資料表資訊) is not list or 資料表資訊 != _預期限流資料表:
            raise ValueError
        結果列 = _執行並讀取(
            執行,
            _原子增加語句,
            (範圍類型, 範圍識別碼, 正規視窗.開始秒, 更新時間),
            "fetchone",
        )
        if (
            type(結果列) is not tuple
            or len(結果列) != 1
            or type(結果列[0]) is not int
            or not 1 <= 結果列[0] <= 最大限流計數
        ):
            raise ValueError
        if getattr(連線, "in_transaction") is not True:
            raise ValueError
        計數 = 結果列[0]
    except _控制流程例外 as 控制流程:
        BaseException.__setattr__(控制流程, "__cause__", None)
        BaseException.__setattr__(控制流程, "__context__", None)
        BaseException.__setattr__(控制流程, "__suppress_context__", True)
        連線 = None
        範圍類型 = 範圍識別碼 = 視窗 = 更新時間 = None
        執行 = 資料表資訊 = 結果列 = 正規視窗 = None
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        連線 = None
        範圍類型 = 範圍識別碼 = 視窗 = 更新時間 = None
        執行 = 資料表資訊 = 結果列 = 正規視窗 = None
        raise 限流計數錯誤("限流計數失敗") from None
    連線 = None
    範圍類型 = 範圍識別碼 = 視窗 = 更新時間 = None
    執行 = 資料表資訊 = 結果列 = 正規視窗 = None
    return 計數


def 增加雙層計數並判定(
    連線: object,
    端點識別碼: object,
    憑證識別碼: object,
    端點上限: object,
    憑證上限: object,
    時間戳記: object,
) -> 限流決策:
    """在呼叫者交易內先增加端點與憑證計數，再做固定視窗決策。"""
    正規端點識別碼 = 正規憑證識別碼 = None
    正規端點上限 = 正規憑證上限 = 0
    正規時間戳記 = None
    視窗 = None
    端點計數 = 憑證計數 = 0
    失敗 = False
    try:
        if type(端點識別碼) is not str or _安全範圍識別碼(端點識別碼) is None:
            raise ValueError
        正規端點識別碼 = str(端點識別碼)
        if type(憑證識別碼) is not str or _安全範圍識別碼(憑證識別碼) is None:
            raise ValueError
        正規憑證識別碼 = str(憑證識別碼)
        正規端點上限 = 驗證限流上限(端點上限)
        正規憑證上限 = 驗證限流上限(憑證上限)
        視窗 = 計算固定視窗(時間戳記)
        if type(時間戳記) is int:
            正規時間戳記 = int(時間戳記)
        else:
            正規時間戳記 = float(cast(float, 時間戳記))
        端點計數 = 增加限流計數(連線, "endpoint", 正規端點識別碼, 視窗, 正規時間戳記)
        憑證計數 = 增加限流計數(連線, "credential", 正規憑證識別碼, 視窗, 正規時間戳記)
        if type(端點計數) is not int or type(憑證計數) is not int:
            raise ValueError
        端點超限 = 端點計數 > 正規端點上限
        憑證超限 = 憑證計數 > 正規憑證上限
        if not 端點超限 and not 憑證超限:
            return 限流決策(True, 端點計數, 憑證計數, None, None, 正規端點上限, 正規憑證上限)
        超限範圍 = "endpoint" if 端點超限 else "credential"
        重試秒數 = math.ceil(視窗.結束秒 - 正規時間戳記)
        重試秒數 = max(1, min(固定視窗秒數, 重試秒數))
        return 限流決策(False, 端點計數, 憑證計數, 超限範圍, 重試秒數, 正規端點上限, 正規憑證上限)
    except _控制流程例外 as 控制流程:
        BaseException.__setattr__(控制流程, "__cause__", None)
        BaseException.__setattr__(控制流程, "__context__", None)
        BaseException.__setattr__(控制流程, "__suppress_context__", True)
        連線 = 端點識別碼 = 憑證識別碼 = 端點上限 = 憑證上限 = 時間戳記 = None
        正規端點識別碼 = 正規憑證識別碼 = 正規時間戳記 = 視窗 = None
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        連線 = 端點識別碼 = 憑證識別碼 = 端點上限 = 憑證上限 = 時間戳記 = None
        正規端點識別碼 = 正規憑證識別碼 = 正規時間戳記 = 視窗 = None
        raise 限流計數錯誤("限流計數失敗") from None
    raise AssertionError("不可到達")
