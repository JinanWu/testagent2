"""PostgreSQL Published session history adapter。"""
from __future__ import annotations

from typing import Callable

from 繁中代理.PostgreSQL連線 import 交易連線
from .Published工作階段 import (
    Published對話組,
    Published工作階段錯誤,
    SQLitePublished工作階段儲存庫,
    _是核准訊息,
    最大成功對話組數,
    最大歷史位元組,
    最大歷史TOKEN數,
)
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON


_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class PostgreSQLPublished工作階段儲存庫:
    """The same DTO and scope semantics as SQLite, backed only by PostgreSQL。"""

    def __init__(self, 設定: object, *, 時鐘: Callable[[], float] | None = None) -> None:
        self._設定 = 設定
        self._時鐘 = 時鐘

    def 讀取成功歷史(
        self, endpoint_id: str, service_account_id: str, session_id: str,
    ) -> tuple[Published對話組, ...]:
        try:
            SQLitePublished工作階段儲存庫._驗證scope(
                endpoint_id, service_account_id, session_id,
            )
            with 交易連線(self._設定) as connection:
                rows = connection.execute(
                    "SELECT sequence_number,endpoint_version_id,user_message,assistant_message,"
                    "pair_size_bytes,token_count FROM published_session_turn_pairs "
                    "WHERE endpoint_id=%s AND service_account_id=%s AND session_id=%s "
                    "ORDER BY sequence_number DESC LIMIT %s",
                    (endpoint_id, service_account_id, session_id, 最大成功對話組數),
                ).fetchall()
            selected: list[Published對話組] = []
            total_bytes = total_tokens = 0
            previous_sequence: int | None = None
            for row in rows:
                pair = self._重建對話組(row)
                if previous_sequence is not None and pair.sequence_number != previous_sequence - 1:
                    raise ValueError
                if (total_bytes + pair.pair_size_bytes > 最大歷史位元組
                        or total_tokens + pair.token_count > 最大歷史TOKEN數):
                    break
                selected.append(pair)
                total_bytes += pair.pair_size_bytes
                total_tokens += pair.token_count
                previous_sequence = pair.sequence_number
            return tuple(reversed(selected))
        except _控制流程:
            raise
        except BaseException:
            raise Published工作階段錯誤("Published工作階段讀取失敗") from None

    @staticmethod
    def _重建對話組(row: object) -> Published對話組:
        """以欄名解析 psycopg dict_row；tuple fallback 僅供相容既有注入測試。"""
        if isinstance(row, dict):
            values = tuple(row[name] for name in (
                "sequence_number", "endpoint_version_id", "user_message",
                "assistant_message", "pair_size_bytes", "token_count",
            ))
        else:
            values = tuple(row)  # type: ignore[arg-type]
        if len(values) != 6:
            raise ValueError
        user = 解析嚴格JSON(values[2]) if isinstance(values[2], str) else values[2]
        assistant = 解析嚴格JSON(values[3]) if isinstance(values[3], str) else values[3]
        pair = Published對話組(values[0], values[1], user, assistant, values[4], values[5])
        if (type(pair.sequence_number) is not int or pair.sequence_number < 1
                or type(pair.endpoint_version_id) is not str or not pair.endpoint_version_id.strip()
                or not _是核准訊息(pair.user_message, "user")
                or not _是核准訊息(pair.assistant_message, "assistant")
                or type(pair.pair_size_bytes) is not int or not 0 < pair.pair_size_bytes <= 最大歷史位元組
                or type(pair.token_count) is not int or not 1 <= pair.token_count <= 最大歷史TOKEN數):
            raise ValueError
        expected_size = len(建立正規JSON(pair.user_message).encode("utf-8")) + len(
            建立正規JSON(pair.assistant_message).encode("utf-8")
        )
        if pair.pair_size_bytes != expected_size:
            raise ValueError
        return pair

    def 附加成功對話組(
        self, endpoint_id: str, service_account_id: str, session_id: str,
        endpoint_version_id: str, user_message: dict[str, object],
        assistant_message: dict[str, object], token_count: int, *, expected_sequence: int,
    ) -> int:
        try:
            SQLitePublished工作階段儲存庫._驗證scope(
                endpoint_id, service_account_id, session_id,
            )
            if (type(endpoint_version_id) is not str or not endpoint_version_id.strip()
                    or type(expected_sequence) is not int or expected_sequence < 1
                    or type(token_count) is not int or not 1 <= token_count <= 最大歷史TOKEN數
                    or not _是核准訊息(user_message, "user")
                    or not _是核准訊息(assistant_message, "assistant")):
                raise ValueError
            user_json = 建立正規JSON(user_message)
            assistant_json = 建立正規JSON(assistant_message)
            size = len(user_json.encode("utf-8")) + len(assistant_json.encode("utf-8"))
            if not 0 < size <= 最大歷史位元組:
                raise ValueError

            with 交易連線(self._設定) as connection:
                # Aggregate SELECT ... FOR UPDATE 在 PostgreSQL 不合法。鎖住 canonical
                # endpoint parent row，令同 endpoint 的 sequence CAS 交易序列化；鎖會持有至 commit。
                parent = connection.execute(
                    "SELECT id FROM published_endpoints WHERE id=%s FOR UPDATE",
                    (endpoint_id,),
                ).fetchone()
                if parent is None:
                    raise ValueError
                summary = connection.execute(
                    "SELECT COUNT(*) AS count,MIN(sequence_number) AS minimum,"
                    "COALESCE(MAX(sequence_number),0) AS n "
                    "FROM published_session_turn_pairs "
                    "WHERE endpoint_id=%s AND service_account_id=%s AND session_id=%s",
                    (endpoint_id, service_account_id, session_id),
                ).fetchone()
                if summary is None:
                    raise ValueError
                if isinstance(summary, dict):
                    count, minimum, current = (
                        summary["count"], summary["minimum"], summary["n"],
                    )
                else:
                    count, minimum, current = summary
                if (type(count) is not int or type(current) is not int or count < 0
                        or (count == 0 and minimum is not None)
                        or (count > 0 and (type(minimum) is not int or minimum != 1 or count != current))
                        or current + 1 != expected_sequence):
                    raise ValueError
                connection.execute(
                    "INSERT INTO published_session_turn_pairs("
                    "endpoint_id,service_account_id,session_id,sequence_number,endpoint_version_id,"
                    "user_message,assistant_message,pair_size_bytes,token_count) "
                    "VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)",
                    (endpoint_id, service_account_id, session_id, expected_sequence,
                     endpoint_version_id, user_json, assistant_json, size, token_count),
                )
            return expected_sequence
        except _控制流程:
            raise
        except BaseException:
            raise Published工作階段錯誤("Published工作階段附加失敗") from None
