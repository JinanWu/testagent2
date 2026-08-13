"""INV 生產 SQLite、Draft 2020-12 schema 與 invocation ledger 橋接。

參數：本模組公開邊界只接受既有資料庫路徑、釘選版本與 INV DTO。
回傳：提供限流決策、精確布林 schema 結果及已提交紀錄收據。
例外：控制流程保留 identity；普通基礎設施錯誤固定化且不攜帶輸入識別。
副作用：限流與 ledger 操作會交易寫入既有 SQLite；schema 驗證不作外部 I/O。
"""

from __future__ import annotations

import os
import math
import sqlite3
import stat
import traceback
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..領域模型 import InvocationRef
from .儲存庫 import SQLite呼叫儲存庫
from .Published工作階段 import Published成功對話提交
from .編排器 import 執行嘗試結果, 執行嘗試紀錄收據, 執行嘗試請求
from .限流 import 增加雙層計數並判定, 限流決策

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_最大節點 = 10_000
_最大深度 = 64
_最大容器項目 = 1_024
_最大UTF8位元組 = 1_048_576


class 生產橋接錯誤(RuntimeError):
    """表示 INV 生產 bridge 無法安全完成。

    參數：固定公開訊息描述失敗邊界，不接受內部識別或密鑰。
    回傳：建立可供 controller 固定映射的錯誤實例。
    例外：沒有額外建構例外。
    副作用：無。
    """


def _清理框架(錯誤: BaseException) -> None:
    """盡力清除已退出 helper frame，不取代原始例外。

    參數：錯誤為剛捕捉且可能持有敏感 locals 的例外。
    回傳：無。
    例外：清理器自己的任何例外均被忽略。
    副作用：清空錯誤 traceback 中已退出 frame 的 locals。
    """
    try:
        traceback.clear_frames(錯誤.__traceback__)
    except BaseException:
        pass


class SQLite雙層限流器:
    """擁有每次雙層限流計數的完整 SQLite 寫入交易。

    參數：資料庫路徑指定既有一般檔；連線工廠只供可控測試與部署注入。
    回傳：建立不長存連線的限流服務。
    例外：建構只保存設定；提交失敗固定為 ``生產橋接錯誤``。
    副作用：提交時以 rw 模式開啟、鎖定、提交或回滾並關閉資料庫。
    """

    def __init__(
        self, database_path: str | Path, *,
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        """保存資料庫位置及連線工廠，不提早開啟資源。

        參數：資料庫路徑與 callable 連線工廠。
        回傳：無。
        例外：設定型別不符時固定拋 ``生產橋接錯誤``。
        副作用：無資料庫或檔案存取。
        """
        if (type(database_path) not in (str, type(Path()))
                or (type(database_path) is str and not database_path) or not callable(connection_factory)):
            raise 生產橋接錯誤("限流器初始化失敗") from None
        self._資料庫 = Path(database_path)
        self._連線工廠 = connection_factory

    def 提交(
        self, endpoint_id: str, credential_id: str, endpoint_limit: int,
        credential_limit: int, timestamp: float,
    ) -> 限流決策:
        """在單一立即交易增加兩層計數並提交 exact 決策。

        參數：端點與憑證識別、各自上限及本次驗證時間。
        回傳：只在 COMMIT 成功後回傳 ``限流決策``。
        例外：控制流程保留 identity；普通失敗固定為 ``限流提交失敗``。
        副作用：驗證一般檔 identity，以 rw/BEGIN IMMEDIATE 寫入，失敗回滾且永遠 close。
        """
        連線 = 路徑 = URI = 前狀態 = 後狀態 = 決策 = 邊界錯誤 = None
        try:
            路徑 = self._資料庫.absolute()
            前狀態 = os.lstat(路徑)
            if stat.S_ISLNK(前狀態.st_mode) or not stat.S_ISREG(前狀態.st_mode) or 前狀態.st_size <= 0:
                raise ValueError
            URI = 路徑.as_uri() + "?mode=rw"
            連線 = self._連線工廠(URI, uri=True, timeout=30.0, isolation_level=None)
            後狀態 = os.lstat(路徑)
            if (stat.S_ISLNK(後狀態.st_mode)
                    or (前狀態.st_dev, 前狀態.st_ino) != (後狀態.st_dev, 後狀態.st_ino)):
                raise ValueError
            連線.execute("BEGIN IMMEDIATE")
            決策 = 增加雙層計數並判定(
                連線, endpoint_id, credential_id, endpoint_limit, credential_limit, timestamp,
            )
            if type(決策) is not 限流決策:
                raise ValueError
            連線.execute("COMMIT")
        except BaseException as 捕捉錯誤:
            邊界錯誤 = 捕捉錯誤
            if 連線 is not None:
                try:
                    if 連線.in_transaction:
                        連線.execute("ROLLBACK")
                except BaseException:
                    pass
            _清理框架(邊界錯誤)
        finally:
            if 連線 is not None:
                try:
                    連線.close()
                except BaseException as 關閉錯誤:
                    if 邊界錯誤 is None:
                        邊界錯誤 = 關閉錯誤
                    _清理框架(關閉錯誤)
        if 邊界錯誤 is not None:
            是控制流程 = isinstance(邊界錯誤, _控制流程)
            控制 = 邊界錯誤 if 是控制流程 else None
            self = endpoint_id = credential_id = endpoint_limit = credential_limit = timestamp = None
            連線 = 路徑 = URI = 前狀態 = 後狀態 = 決策 = 邊界錯誤 = None
            if 控制 is not None:
                raise 控制.with_traceback(控制.__traceback__)
            raise 生產橋接錯誤("限流提交失敗") from None
        return 決策


def _有界且無遠端參照(根: object) -> bool:
    """驗證 exact JSON tree 資源界限並拒絕所有非 fragment ``$ref``。

    參數：根為待驗 schema 或 instance tree。
    回傳：完全有界且無遠端參照時回傳 exact ``True``，否則 ``False``。
    例外：不傳出普通資料錯誤。
    副作用：無；不解析 URI、不呼叫 resolver 或 format checker。
    """
    計數 = 0
    堆疊 = [(根, 0)]
    try:
        while 堆疊:
            值, 深度 = 堆疊.pop()
            計數 += 1
            if 計數 > _最大節點 or 深度 > _最大深度:
                return False
            型別 = type(值)
            if 值 is None or 型別 in (bool, int):
                continue
            if 型別 is float:
                if not math.isfinite(值):
                    return False
                continue
            if 型別 is str:
                if len(值.encode("utf-8")) > _最大UTF8位元組:
                    return False
                continue
            if 型別 not in (dict, list) or len(值) > _最大容器項目:
                return False
            if 型別 is list:
                堆疊.extend((項, 深度 + 1) for 項 in list.__iter__(值))
                continue
            for 鍵, 項 in dict.items(值):
                if type(鍵) is not str:
                    return False
                if 鍵 in ("$ref", "$dynamicRef", "$recursiveRef") and (
                    type(項) is not str or not 項.startswith("#")
                ):
                    return False
                堆疊.append((項, 深度 + 1))
        return True
    except (UnicodeError, OverflowError, RecursionError):
        return False


def _驗證釘選結構(釘選版本: object, 資料: object, 欄位: str) -> bool:
    """從 pin 的 fresh version tree 執行無 format side effect 的 2020-12 驗證。

    參數：釘選版本提供 fresh snapshot；資料為 instance；欄位選 input 或 response schema。
    回傳：schema 與資料皆安全且資料有效時回傳 exact ``True``，其餘 ``False``。
    例外：控制流程原樣傳出；普通 pin、schema 或驗證錯誤收斂為 ``False``。
    副作用：呼叫 pin 的快照重建一次；不作網路、檔案或 custom format 呼叫。
    """
    快照 = 綱要 = None
    try:
        快照 = 釘選版本.取得版本快照()
        綱要 = object.__getattribute__(快照, 欄位)
        if 綱要 is None and 欄位 == "input_schema":
            return _有界且無遠端參照(資料)
        if type(綱要) is not dict or not _有界且無遠端參照(綱要) or not _有界且無遠端參照(資料):
            return False
        Draft202012Validator.check_schema(綱要)
        return Draft202012Validator(綱要, format_checker=None).is_valid(資料) is True
    except _控制流程:
        raise
    except (SchemaError, BaseException):
        return False


def 驗證釘選輸入結構(釘選版本: object, 輸入資料: object) -> bool:
    """依 exact pin 的 fresh input schema 驗證輸入。

    參數：釘選版本及已脫離的輸入 JSON tree。
    回傳：符合 Draft 2020-12 schema 時回傳 exact 布林值。
    例外：控制流程原樣傳出；普通錯誤收斂為 ``False``。
    副作用：只呼叫 pin 重建 fresh snapshot，不作其他 I/O。
    """
    return _驗證釘選結構(釘選版本, 輸入資料, "input_schema")


def 驗證釘選輸出結構(釘選版本: object, 輸出資料: object) -> bool:
    """依 exact pin 的 fresh response schema 驗證輸出。

    參數：釘選版本及已脫離的模型輸出 JSON tree。
    回傳：符合 Draft 2020-12 schema 時回傳 exact 布林值。
    例外：控制流程原樣傳出；普通錯誤收斂為 ``False``。
    副作用：只呼叫 pin 重建 fresh snapshot，不作其他 I/O。
    """
    return _驗證釘選結構(釘選版本, 輸出資料, "response_schema")


class InvocationLedger橋接:
    """把 INV attempt lifecycle 寫入既有 SQLite 呼叫儲存庫。

    參數：儲存庫必須是 production ``SQLite呼叫儲存庫``。
    回傳：提供 pre-hook 與 recorder callbacks。
    例外：輸入或儲存失敗沿用固定 repository/bridge 錯誤，不揭露 DTO 內容。
    副作用：依序轉 running、附加 attempt event，並在 terminal 時一次結案。
    """

    def __init__(self, repository: SQLite呼叫儲存庫) -> None:
        """保存 genuine SQLite repository，不建立第二套 DTO 或連線。

        參數：repository 為 exact ``SQLite呼叫儲存庫``。
        回傳：無。
        例外：型別不符時固定拋 ``生產橋接錯誤``。
        副作用：無。
        """
        if type(repository) is not SQLite呼叫儲存庫:
            raise 生產橋接錯誤("invocation ledger橋接初始化失敗") from None
        self._儲存庫 = repository

    def 開始執行嘗試(self, invocation: InvocationRef, request: 執行嘗試請求) -> None:
        """只在 attempt 1 的模型呼叫前把 pending 轉為 running。

        參數：exact invocation 與 attempt 1 request。
        回傳：成功提交後回傳 ``None``。
        例外：DTO 不符固定拒絕；repository 失敗沿用其固定錯誤。
        副作用：一次更新 invocation status；attempt 2 不應呼叫本方法。
        """
        if type(invocation) is not InvocationRef or type(request) is not 執行嘗試請求 or request.attempt != 1:
            raise 生產橋接錯誤("invocation ledger開始失敗") from None
        self._儲存庫.標記執行中(invocation.id)

    def 記錄執行嘗試(
        self, invocation: InvocationRef, request: 執行嘗試請求,
        result: 執行嘗試結果, schema_valid: bool | None,
    ) -> 執行嘗試紀錄收據:
        """以 repository 單一交易 append attempt event 並依 terminal 規則結案。

        參數：exact invocation、request、result 與 output schema 判定。
        回傳：匹配 invocation/attempt/sequence 的 exact committed receipt。
        例外：非法組合固定拒絕；repository 失敗沿用固定且不可重複結案語意。
        副作用：附加 deterministic event；成功、typed failure 或第二次 invalid 時結案。
        """
        if (type(invocation) is not InvocationRef or type(request) is not 執行嘗試請求
                or type(result) is not 執行嘗試結果
                or (schema_valid is not None and type(schema_valid) is not bool)):
            raise 生產橋接錯誤("invocation ledger記錄失敗") from None
        種類, 次數 = result.kind, request.attempt
        if (種類 == "success") != (schema_valid is not None):
            raise 生產橋接錯誤("invocation ledger記錄失敗") from None
        狀態 = 輸出 = 錯誤 = 用量資料 = None
        工作階段對話組 = None
        if 種類 == "success" and schema_valid is True:
            用量 = result.usage
            用量資料 = None if 用量 is None else {
                "total_tokens": object.__getattribute__(用量, "total_tokens"),
            }
            狀態, 輸出 = "succeeded", result.data
            工作階段 = invocation.session_id
            if 工作階段 is not None:
                釘選 = request.pinned_version
                歷史 = request.history
                下一序號 = 1 if not 歷史 else object.__getattribute__(歷史[-1], "sequence_number") + 1
                token數 = 1 if 用量 is None or 用量.total_tokens is None else max(1, 用量.total_tokens)
                工作階段對話組 = Published成功對話提交(
                    endpoint_id=object.__getattribute__(釘選, "endpoint_id"),
                    service_account_id=object.__getattribute__(釘選, "service_account_id"),
                    session_id=工作階段,
                    endpoint_version_id=object.__getattribute__(釘選, "version_id"),
                    sequence_number=下一序號,
                    user_message={"role": "user", "content": request.input},
                    assistant_message={"role": "assistant", "content": result.data},
                    token_count=token數,
                )
        elif 種類 != "success" or 次數 == 2:
            錯誤碼 = 種類 if 種類 != "success" else "model_output_schema_invalid"
            狀態, 錯誤 = "failed", {"code": 錯誤碼}
        序號 = self._儲存庫.原子記錄執行事件並結案(
            invocation.id, f"{invocation.id}:attempt:{次數}", "model_attempt",
            {"attempt": 次數, "kind": 種類, "schema_valid": schema_valid}, 次數,
            status=狀態, output=輸出, error=錯誤, usage=用量資料,
            session_pair=工作階段對話組,
        )
        return 執行嘗試紀錄收據(invocation.id, 次數, True, 序號)
