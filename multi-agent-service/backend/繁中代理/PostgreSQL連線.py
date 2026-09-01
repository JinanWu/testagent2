"""安全且有界的 psycopg3 PostgreSQL runtime 連線池 primitive。

此模組匯入時只建立鎖與空的 singleton 狀態；不讀環境、不建構 pool，亦不做
任何檔案、socket 或資料庫 I/O。
"""

from __future__ import annotations

import hmac
import os
from contextlib import contextmanager
from threading import Condition, RLock
from typing import Any, Iterator

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .交易儲存設定 import 交易儲存設定

_POOL名稱 = "testagent2-postgres-runtime"
_關閉等待秒數 = 5.0
_連線池鎖 = RLock()
_連線池條件 = Condition(_連線池鎖)
_共用連線池: ConnectionPool[Any] | None = None
_共用連線池指紋: tuple[str, str, str, int, int, int] | None = None
_作用中交易數 = 0
_正在關閉 = False


def _讀取交易儲存設定(環境: Any):
    """只在明確 lifecycle API 呼叫時延遲載入並解析 process environment。"""
    from .環境設定 import 讀取交易儲存設定

    return 讀取交易儲存設定(環境)


def _設定指紋(設定: 交易儲存設定) -> tuple[str, str, str, int, int, int]:
    """驗證 exact runtime type 與所有欄位，回傳只含 built-in 的安全指紋。"""
    if type(設定) is not 交易儲存設定:
        raise RuntimeError("PostgreSQL 連線設定無效")

    後端 = 設定.後端
    DSN = 設定.資料庫URL
    連線名稱 = 設定.CloudSQL連線名稱
    最小 = 設定.Pool最小連線數
    最大 = 設定.Pool最大連線數
    等待 = 設定.Pool等待秒數
    if (
        type(後端) is not str
        or type(DSN) is not str
        or type(連線名稱) is not str
        or type(最小) is not int
        or type(最大) is not int
        or type(等待) is not int
    ):
        raise RuntimeError("PostgreSQL 連線設定無效")

    try:
        # 重新走 immutable setting 的完整語意驗證，防止 object.__setattr__ 後繞過。
        交易儲存設定(後端, DSN, 連線名稱, 最小, 最大, 等待)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise RuntimeError("PostgreSQL 連線設定無效") from None
    if 後端 != "postgres":
        raise RuntimeError("PostgreSQL 連線設定無效")
    return (後端, DSN, 連線名稱, 最小, 最大, 等待)


def _指紋相同(
    左: tuple[str, str, str, int, int, int],
    右: tuple[str, str, str, int, int, int],
) -> bool:
    """逐欄比較安全 built-in 指紋；DSN 一律使用 constant-time compare。"""
    return (
        左[0] == 右[0]
        and hmac.compare_digest(左[1], 右[1])
        and 左[2] == 右[2]
        and 左[3] == 右[3]
        and 左[4] == 右[4]
        and 左[5] == 右[5]
    )


def _驗證呼叫設定(
    凍結設定: 交易儲存設定,
) -> tuple[tuple[str, str, str, int, int, int], 交易儲存設定]:
    """逐次從明確 process environment 讀取並核對 supplied/current 設定。"""
    try:
        目前設定 = _讀取交易儲存設定(os.environ)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise RuntimeError("PostgreSQL 連線設定無效") from None
    supplied指紋 = _設定指紋(凍結設定)
    current指紋 = _設定指紋(目前設定)
    if not _指紋相同(supplied指紋, current指紋):
        raise RuntimeError("PostgreSQL 連線設定不一致")
    return supplied指紋, 目前設定


def _純解析DSN(DSN: str) -> None:
    """在任何 pool worker 看見 DSN 前先由 libpq parser 純解析。"""
    try:
        conninfo_to_dict(DSN)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise RuntimeError("PostgreSQL 連線設定無效") from None


def _建構已驗證連線池(設定: 交易儲存設定) -> ConnectionPool[Any]:
    """設定已與 process environment 核對後，僅建構 closed pool。"""
    DSN = 設定.資料庫URL
    assert type(DSN) is str
    _純解析DSN(DSN)
    return ConnectionPool(
        conninfo=DSN,
        min_size=設定.Pool最小連線數,
        max_size=設定.Pool最大連線數,
        timeout=float(設定.Pool等待秒數),
        kwargs={"autocommit": False, "row_factory": dict_row},
        check=ConnectionPool.check_connection,
        name=_POOL名稱,
        open=False,
    )


def 建立連線池(凍結設定: 交易儲存設定) -> ConnectionPool[Any]:
    """驗證 supplied/current 設定後，只建立 ``open=False`` 的 pool。"""
    _, 目前設定 = _驗證呼叫設定(凍結設定)
    return _建構已驗證連線池(目前設定)


def _取得或啟動鎖內(凍結設定: 交易儲存設定) -> ConnectionPool[Any]:
    """須由 module lock 呼叫；驗證、重用或建立並啟動 singleton。"""
    global _共用連線池, _共用連線池指紋

    if _正在關閉:
        raise RuntimeError("PostgreSQL 連線池正在關閉")
    指紋, 目前設定 = _驗證呼叫設定(凍結設定)
    if _共用連線池 is not None:
        if _共用連線池指紋 is None or not _指紋相同(_共用連線池指紋, 指紋):
            raise RuntimeError("PostgreSQL 連線池設定已漂移")
        if not _共用連線池.closed:
            return _共用連線池
        # 外部關閉的 pool 不可回傳；相同設定可安全建立新的 singleton。
        _共用連線池 = None
        _共用連線池指紋 = None

    連線池 = _建構已驗證連線池(目前設定)
    # Pool constructor 與真正 open 是兩個邊界；open 前再次讀完整環境。
    try:
        open指紋, open設定 = _驗證呼叫設定(凍結設定)
        if not _指紋相同(open指紋, 指紋):
            raise RuntimeError("PostgreSQL 連線設定不一致")
    except BaseException:
        try:
            連線池.close(timeout=_關閉等待秒數)
        except BaseException:
            pass
        raise

    _共用連線池 = 連線池
    _共用連線池指紋 = 指紋
    try:
        連線池.open(wait=True, timeout=float(open設定.Pool等待秒數))
    except BaseException:
        try:
            連線池.close(timeout=_關閉等待秒數)
        except BaseException:
            # cleanup 永遠不得遮蔽 open/wait 的原始例外。
            pass
        finally:
            if _共用連線池 is 連線池:
                _共用連線池 = None
                _共用連線池指紋 = None
        raise
    return 連線池


def 啟動共用連線池(凍結設定: 交易儲存設定) -> ConnectionPool[Any]:
    """在 module lock 內驗證並啟動或重用 shared pool。"""
    with _連線池鎖:
        return _取得或啟動鎖內(凍結設定)


def 取得共用連線池(凍結設定: 交易儲存設定) -> ConnectionPool[Any]:
    """取得可用且已啟動、設定未漂移的 shared pool。"""
    with _連線池鎖:
        return _取得或啟動鎖內(凍結設定)


@contextmanager
def 交易連線(凍結設定: 交易儲存設定) -> Iterator[Any]:
    """逐邊界驗證後借用交易連線；不同交易可並行，shutdown 會等待歸還。"""
    global _作用中交易數

    with _連線池鎖:
        連線池 = _取得或啟動鎖內(凍結設定)
        acquire指紋, acquire設定 = _驗證呼叫設定(凍結設定)
        if _共用連線池指紋 is None or not _指紋相同(
            _共用連線池指紋, acquire指紋,
        ):
            raise RuntimeError("PostgreSQL 連線池設定已漂移")
        if _正在關閉:
            raise RuntimeError("PostgreSQL 連線池正在關閉")
        _作用中交易數 += 1
    try:
        with 連線池.connection(timeout=float(acquire設定.Pool等待秒數)) as 連線:
            with 連線.transaction():
                yield 連線
    finally:
        with _連線池條件:
            _作用中交易數 -= 1
            _連線池條件.notify_all()


def 關閉共用連線池() -> None:
    """等待 active transactions 後關閉 shared pool；冪等清除 singleton。"""
    global _共用連線池, _共用連線池指紋, _正在關閉

    with _連線池條件:
        連線池 = _共用連線池
        if 連線池 is None:
            _共用連線池指紋 = None
            return
        _正在關閉 = True
        try:
            while _作用中交易數:
                _連線池條件.wait()
            連線池.close(timeout=_關閉等待秒數)
        finally:
            _共用連線池 = None
            _共用連線池指紋 = None
            _正在關閉 = False
            _連線池條件.notify_all()
