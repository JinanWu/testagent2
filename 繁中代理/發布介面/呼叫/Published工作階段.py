"""Published Endpoint／Service Account scoped 的有界工作階段歷史。

參數：本模組公開儲存庫接受 SQLite 路徑、工作階段 scope 與成功對話組。
返回值：提供不可變對話組、bounded read 與 CAS append。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..嚴格JSON import 建立正規JSON


最大成功對話組數 = 32
最大歷史位元組 = 262144
最大歷史TOKEN數 = 32768
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class Published工作階段錯誤(RuntimeError):
    """表示歷史輸入、結構、CAS 或持久化狀態不符合契約。

    參數：沿用 ``RuntimeError`` 的固定公開訊息。
    返回值：建立供 Published 邊界固定映射的錯誤實例。
    """


@dataclass(frozen=True, slots=True)
class Published對話組:
    """保存一個原子成功 user/assistant pair 與 pinned endpoint version。

    參數：欄位名稱沿用 runtime／ledger DTO wire contract。
    返回值：不可變的單一成功對話組快照。
    """

    sequence_number: int
    endpoint_version_id: str
    user_message: dict[str, object]
    assistant_message: dict[str, object]
    pair_size_bytes: int
    token_count: int


@dataclass(frozen=True, slots=True)
class Published成功對話提交:
    """承載 invocation completion transaction 所需的具名 session pair。

    參數：英文欄位精確對應 SQL／runtime wire identities 與成功訊息。
    返回值：不可變的原子提交 DTO；``驗證並建立快照`` 回傳可信 canonical 值。
    """

    endpoint_id: str
    service_account_id: str
    session_id: str
    endpoint_version_id: str
    sequence_number: int
    user_message: dict[str, object]
    assistant_message: dict[str, object]
    token_count: int

    def 驗證並建立快照(self) -> tuple[str, str, str, str, int, dict[str, object], dict[str, object], int]:
        """集中驗證 composite scope、sequence、messages 與 token bound。

        參數：無；讀取本 DTO 的八個 immutable 欄位。
        返回值：可安全交給 transaction adapter 的具型別八欄快照。
        """
        SQLitePublished工作階段儲存庫._驗證scope(
            self.endpoint_id, self.service_account_id, self.session_id,
        )
        if (type(self.endpoint_version_id) is not str or not self.endpoint_version_id.strip()
                or type(self.sequence_number) is not int or self.sequence_number < 1
                or type(self.token_count) is not int
                or not 1 <= self.token_count <= 最大歷史TOKEN數
                or type(self.user_message) is not dict or type(self.assistant_message) is not dict):
            raise ValueError
        return (
            self.endpoint_id, self.service_account_id, self.session_id,
            self.endpoint_version_id, self.sequence_number,
            self.user_message, self.assistant_message, self.token_count,
        )


class SQLitePublished工作階段儲存庫:
    """以單一小介面封裝 composite scope、CAS 與三重 bounded read。

    參數：建構時接收資料庫路徑與可注入時鐘。
    返回值：提供 durable read／append 操作的儲存庫物件。
    """

    def __init__(self, 資料庫: str | Path, *, 時鐘: Callable[[], float] = time.time) -> None:
        """保存 durable SQLite authority 與建立時間來源。

        參數：``資料庫`` 為 Published DB；``時鐘`` 回傳非負有限 epoch 秒。
        返回值：無；完成可重用儲存庫的初始化。
        """
        if not isinstance(資料庫, (str, Path)) or not str(資料庫) or not callable(時鐘):
            raise Published工作階段錯誤("Published工作階段初始化失敗") from None
        self._資料庫 = Path(資料庫)
        self._時鐘 = 時鐘

    def 讀取成功歷史(self, endpoint_id: str, service_account_id: str,
                session_id: str) -> tuple[Published對話組, ...]:
        """只讀 exact scope 最新完整 pair，任一損毀或單 pair 超限即 fail closed。

        參數：三個英文名稱精確對應 schema composite scope 欄位。
        返回值：依 sequence 正序排列的 bounded 不可變對話組。
        """
        try:
            self._驗證scope(endpoint_id, service_account_id, session_id)
            with closing(self._開啟連線()) as 連線:
                序列摘要 = 連線.execute(
                    "SELECT COUNT(*),MIN(sequence_number),MAX(sequence_number) "
                    "FROM published_session_turn_pairs "
                    "WHERE endpoint_id=? AND service_account_id=? AND session_id=?",
                    (endpoint_id, service_account_id, session_id),
                ).fetchone()
                資料列 = 連線.execute(
                    "SELECT sequence_number,endpoint_version_id,user_message_json,assistant_message_json,"
                    "pair_size_bytes,token_count FROM published_session_turn_pairs "
                    "WHERE endpoint_id=? AND service_account_id=? AND session_id=? "
                    "ORDER BY sequence_number DESC LIMIT ?",
                    (endpoint_id, service_account_id, session_id, 最大成功對話組數 + 1),
                ).fetchall()
            if (type(序列摘要) is not tuple or len(序列摘要) != 3
                    or type(序列摘要[0]) is not int or 序列摘要[0] < 0):
                raise ValueError
            if 序列摘要[0] == 0:
                if 序列摘要[1:] != (None, None):
                    raise ValueError
            elif (type(序列摘要[1]) is not int or type(序列摘要[2]) is not int
                  or 序列摘要[1] != 1 or 序列摘要[0] != 序列摘要[2]):
                raise ValueError
            if len(資料列) > 最大成功對話組數:
                資料列 = 資料列[:最大成功對話組數]
            選取: list[Published對話組] = []
            位元組合計 = TOKEN合計 = 0
            前一序號 = None
            for 列 in 資料列:
                對話組 = self._重建對話組(列)
                if 前一序號 is not None and 對話組.sequence_number != 前一序號 - 1:
                    raise ValueError
                if (位元組合計 + 對話組.pair_size_bytes > 最大歷史位元組
                        or TOKEN合計 + 對話組.token_count > 最大歷史TOKEN數):
                    break
                選取.append(對話組)
                位元組合計 += 對話組.pair_size_bytes
                TOKEN合計 += 對話組.token_count
                前一序號 = 對話組.sequence_number
            return tuple(reversed(選取))
        except BaseException as 錯誤:
            if type(錯誤) in _控制流程例外:
                raise
        raise Published工作階段錯誤("Published工作階段讀取失敗") from None

    def 附加成功對話組(self, endpoint_id: str, service_account_id: str, session_id: str,
                  endpoint_version_id: str, user_message: dict[str, object],
                  assistant_message: dict[str, object], token_count: int, *,
                  expected_sequence: int) -> int:
        """以 BEGIN IMMEDIATE + expected sequence 原子附加完整成功 pair。

        參數：schema scope、pinned version、兩個 wire message、token 數與預期序號。
        返回值：提交成功的 exact sequence number。
        """
        try:
            self._驗證scope(endpoint_id, service_account_id, session_id)
            if (type(endpoint_version_id) is not str or not endpoint_version_id.strip()
                    or type(expected_sequence) is not int or expected_sequence < 1
                    or type(token_count) is not int or not 1 <= token_count <= 最大歷史TOKEN數
                    or type(user_message) is not dict or type(assistant_message) is not dict):
                raise ValueError
            user_json = 建立正規JSON(user_message)
            assistant_json = 建立正規JSON(assistant_message)
            pair_size = len(user_json.encode("utf-8")) + len(assistant_json.encode("utf-8"))
            if not 0 < pair_size <= 最大歷史位元組:
                raise ValueError
            現在 = self._時鐘()
            if type(現在) not in (int, float) or not math.isfinite(float(現在)) or 現在 < 0:
                raise ValueError
            with closing(self._開啟連線()) as 連線, 連線:
                連線.execute("BEGIN IMMEDIATE")
                最大列 = 連線.execute(
                    "SELECT MAX(sequence_number) FROM published_session_turn_pairs "
                    "WHERE endpoint_id=? AND service_account_id=? AND session_id=?",
                    (endpoint_id, service_account_id, session_id),
                ).fetchone()
                if 最大列 is None or len(最大列) != 1:
                    raise ValueError
                下一序號 = 1 if 最大列[0] is None else 最大列[0] + 1
                if 下一序號 != expected_sequence:
                    raise ValueError
                連線.execute(
                    "INSERT INTO published_session_turn_pairs("
                    "endpoint_id,service_account_id,session_id,sequence_number,endpoint_version_id,"
                    "user_message_json,assistant_message_json,pair_size_bytes,token_count,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (endpoint_id, service_account_id, session_id, 下一序號, endpoint_version_id,
                     user_json, assistant_json, pair_size, token_count, float(現在)),
                )
            return expected_sequence
        except BaseException as 錯誤:
            if type(錯誤) in _控制流程例外:
                raise
        raise Published工作階段錯誤("Published工作階段附加失敗") from None

    def _開啟連線(self) -> sqlite3.Connection:
        """開啟啟用 foreign keys 的短生命週期 SQLite 連線。

        參數：無；使用建構時保存的 Published DB。
        返回值：已確認 foreign keys 開啟的連線。
        """
        連線 = sqlite3.connect(str(self._資料庫), timeout=30.0, isolation_level=None)
        連線.execute("PRAGMA foreign_keys=ON")
        if 連線.execute("PRAGMA foreign_keys").fetchone() != (1,):
            連線.close()
            raise ValueError
        return 連線

    @staticmethod
    def _驗證scope(endpoint_id: object, service_account_id: object, session_id: object) -> None:
        """驗證三個 schema scope identifiers 的 lexical 契約。

        參數：英文名稱精確對應資料庫欄位，不接受隱性 trim／normalize。
        返回值：無；合法時正常返回，違約時拋 ``ValueError``。
        """
        if any(type(值) is not str or not 值 or 值 != 值.strip() for 值 in (endpoint_id, service_account_id, session_id)):
            raise ValueError
        session文字 = str(session_id)
        if len(session文字.encode("utf-8")) > 128 or any(ord(字元) < 32 or 127 <= ord(字元) <= 159 for 字元 in session文字):
            raise ValueError

    @staticmethod
    def _重建對話組(資料列: object) -> Published對話組:
        """從不可信 SQLite row 重建並驗證 canonical 對話組。

        參數：``資料列`` 為六欄 query row。
        返回值：完整驗證後的不可變 ``Published對話組``。
        """
        if type(資料列) is not tuple or len(資料列) != 6:
            raise ValueError
        序號, 版本, user_json, assistant_json, 位元組, tokens = 資料列
        if (type(序號) is not int or 序號 < 1 or type(版本) is not str or not 版本
                or type(user_json) is not str or type(assistant_json) is not str
                or type(位元組) is not int or not 0 < 位元組 <= 最大歷史位元組
                or type(tokens) is not int or not 0 < tokens <= 最大歷史TOKEN數):
            raise ValueError
        user = json.loads(user_json)
        assistant = json.loads(assistant_json)
        if (type(user) is not dict or type(assistant) is not dict
                or 建立正規JSON(user) != user_json or 建立正規JSON(assistant) != assistant_json
                or len(user_json.encode("utf-8")) + len(assistant_json.encode("utf-8")) != 位元組):
            raise ValueError
        return Published對話組(序號, 版本, user, assistant, 位元組, tokens)
