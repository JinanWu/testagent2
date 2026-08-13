"""依既有發布介面結構建立與結案呼叫紀錄。

參數／欄位：不適用；本模組定義呼叫收據、錯誤與 SQLite 儲存操作。
回傳：不適用；各儲存操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別、結構常數與函式，不建立或更新呼叫紀錄。
"""

from __future__ import annotations

import math
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Callable, cast

from ..嚴格JSON import 建立正規JSON
from ..資料庫結構契約 import 遷移帳本 as _必要遷移
from .Published工作階段 import 最大歷史TOKEN數, 最大歷史位元組


class 呼叫儲存錯誤(RuntimeError):
    """代表呼叫紀錄寫入或狀態轉換被固定錯誤拒絕。"""


_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_Path具體型別 = type(Path())
_呼叫建表SQL = """CREATE TABLE endpoint_invocations (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  endpoint_version_id TEXT NOT NULL,
  credential_id TEXT,
  request_id TEXT NOT NULL UNIQUE CHECK(trim(request_id) <> ''),
  session_id TEXT,
  message_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','rate_limited','invalid_api_key')),
  input_json TEXT NOT NULL CHECK(json_valid(input_json)),
  metadata_json TEXT CHECK(metadata_json IS NULL OR json_valid(metadata_json)),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
  usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
  metadata_size_bytes INTEGER CHECK(metadata_size_bytes IS NULL OR (typeof(metadata_size_bytes) = 'integer' AND metadata_size_bytes >= 0)),
  metadata_sha256 TEXT,
  latency_ms REAL CHECK(latency_ms IS NULL OR (typeof(latency_ms) IN ('real','integer') AND latency_ms >= 0)),
  pricing_version TEXT,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  completed_at REAL CHECK(completed_at IS NULL OR (typeof(completed_at) IN ('real','integer') AND completed_at >= created_at)),
  FOREIGN KEY(endpoint_version_id, endpoint_id) REFERENCES published_endpoint_versions(id, endpoint_id),
  FOREIGN KEY(credential_id, endpoint_id) REFERENCES endpoint_credentials(id, endpoint_id)
)"""
_必要呼叫結構 = (
    ("index", "idx_endpoint_invocations_credential_created", "endpoint_invocations",
     "CREATE INDEX idx_endpoint_invocations_credential_created\n  ON endpoint_invocations(credential_id, created_at)"),
    ("index", "idx_endpoint_invocations_endpoint_created", "endpoint_invocations",
     "CREATE INDEX idx_endpoint_invocations_endpoint_created\n  ON endpoint_invocations(endpoint_id, created_at)"),
    ("index", "idx_endpoint_invocations_retention_candidates", "endpoint_invocations",
     "CREATE INDEX idx_endpoint_invocations_retention_candidates\n  ON endpoint_invocations(created_at, id)"),
    ("index", "idx_endpoint_invocations_status_created", "endpoint_invocations",
     "CREATE INDEX idx_endpoint_invocations_status_created\n  ON endpoint_invocations(status, created_at)"),
    ("index", "sqlite_autoindex_endpoint_invocations_1", "endpoint_invocations", None),
    ("index", "sqlite_autoindex_endpoint_invocations_2", "endpoint_invocations", None),
    ("table", "endpoint_invocations", "endpoint_invocations", _呼叫建表SQL),
    ("trigger", "redacted_invocation_payload_no_update", "endpoint_invocations", """CREATE TRIGGER redacted_invocation_payload_no_update
BEFORE UPDATE OF input_json,metadata_json,output_json,error_json ON endpoint_invocations
WHEN
  (NEW.input_json IS NOT OLD.input_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='invocation_input' AND target_row_id=OLD.id
  )) OR
  (NEW.metadata_json IS NOT OLD.metadata_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='metadata' AND target_row_id=OLD.id
  )) OR
  (NEW.output_json IS NOT OLD.output_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='output' AND target_row_id=OLD.id
  )) OR
  (NEW.error_json IS NOT OLD.error_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='error' AND target_row_id=OLD.id
  ))
BEGIN
  SELECT RAISE(ABORT, 'redacted invocation payload is immutable');
END"""),
)
_事件建表SQL = """CREATE TABLE run_events (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  sequence_number INTEGER NOT NULL CHECK(typeof(sequence_number) = 'integer' AND sequence_number > 0),
  event_type TEXT NOT NULL CHECK(trim(event_type) <> ''),
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  UNIQUE(invocation_id, sequence_number),
  UNIQUE(id, invocation_id)
)"""
_必要事件結構 = (
    ("index", "idx_run_events_retention_invocation_id", "run_events",
     "CREATE INDEX idx_run_events_retention_invocation_id\n  ON run_events(invocation_id, id)"),
    ("index", "sqlite_autoindex_run_events_1", "run_events", None),
    ("index", "sqlite_autoindex_run_events_2", "run_events", None),
    ("index", "sqlite_autoindex_run_events_3", "run_events", None),
    ("table", "run_events", "run_events", _事件建表SQL),
    ("trigger", "redacted_run_event_no_delete", "run_events", """CREATE TRIGGER redacted_run_event_no_delete
BEFORE DELETE ON run_events
WHEN EXISTS (
  SELECT 1 FROM endpoint_redactions
  WHERE target_type='run_event' AND target_row_id=OLD.id
)
BEGIN
  SELECT RAISE(ABORT, 'redacted run event identity is retained');
END"""),
    ("trigger", "redacted_run_event_no_update", "run_events", """CREATE TRIGGER redacted_run_event_no_update
BEFORE UPDATE OF id,invocation_id,payload_json ON run_events
WHEN EXISTS (
  SELECT 1 FROM endpoint_redactions
  WHERE target_type='run_event' AND target_row_id=OLD.id
)
BEGIN
  SELECT RAISE(ABORT, 'redacted run event is immutable');
END"""),
)
_工具建表SQL = """CREATE TABLE endpoint_tool_calls (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  run_event_id TEXT,
  sequence_number INTEGER NOT NULL CHECK(typeof(sequence_number) = 'integer' AND sequence_number > 0),
  tool_name TEXT NOT NULL CHECK(trim(tool_name) <> ''),
  arguments_json TEXT NOT NULL CHECK(json_valid(arguments_json) AND json_type(arguments_json) = 'object'),
  outcome TEXT NOT NULL CHECK(outcome IN ('success','error')),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
  latency_ms REAL CHECK(latency_ms IS NULL OR (typeof(latency_ms) IN ('real','integer') AND latency_ms >= 0)),
  retry_of_tool_call_id TEXT,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  UNIQUE(invocation_id, sequence_number),
  UNIQUE(id, invocation_id),
  FOREIGN KEY(run_event_id, invocation_id) REFERENCES run_events(id, invocation_id),
  FOREIGN KEY(retry_of_tool_call_id, invocation_id) REFERENCES endpoint_tool_calls(id, invocation_id),
  CHECK(
    (outcome = 'success' AND result_json IS NOT NULL AND error_json IS NULL)
    OR (outcome = 'error' AND result_json IS NULL AND error_json IS NOT NULL)
  )
)"""
_必要工具結構 = (
    ("index", "idx_endpoint_tool_calls_invocation_created", "endpoint_tool_calls",
     "CREATE INDEX idx_endpoint_tool_calls_invocation_created\n  ON endpoint_tool_calls(invocation_id, created_at)"),
    ("index", "idx_endpoint_tool_calls_retention_invocation_id", "endpoint_tool_calls",
     "CREATE INDEX idx_endpoint_tool_calls_retention_invocation_id\n  ON endpoint_tool_calls(invocation_id, id)"),
    ("index", "sqlite_autoindex_endpoint_tool_calls_1", "endpoint_tool_calls", None),
    ("index", "sqlite_autoindex_endpoint_tool_calls_2", "endpoint_tool_calls", None),
    ("index", "sqlite_autoindex_endpoint_tool_calls_3", "endpoint_tool_calls", None),
    ("table", "endpoint_tool_calls", "endpoint_tool_calls", _工具建表SQL),
    ("trigger", "redacted_tool_call_no_delete", "endpoint_tool_calls", """CREATE TRIGGER redacted_tool_call_no_delete
BEFORE DELETE ON endpoint_tool_calls
WHEN EXISTS (
  SELECT 1 FROM endpoint_redactions
  WHERE target_row_id=OLD.id
    AND target_type IN ('tool_arguments','tool_result','tool_error')
)
BEGIN
  SELECT RAISE(ABORT, 'redacted tool identity is retained');
END"""),
    ("trigger", "redacted_tool_call_no_update", "endpoint_tool_calls", """CREATE TRIGGER redacted_tool_call_no_update
BEFORE UPDATE OF id,invocation_id,arguments_json,result_json,error_json ON endpoint_tool_calls
WHEN
  (NEW.id IS NOT OLD.id OR NEW.invocation_id IS NOT OLD.invocation_id) AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id
      AND target_type IN ('tool_arguments','tool_result','tool_error')
  ) OR
  (NEW.arguments_json IS NOT OLD.arguments_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id AND target_type='tool_arguments'
  )) OR
  (NEW.result_json IS NOT OLD.result_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id AND target_type='tool_result'
  )) OR
  (NEW.error_json IS NOT OLD.error_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id AND target_type='tool_error'
  ))
BEGIN
  SELECT RAISE(ABORT, 'redacted tool payload is immutable');
END"""),
)
_未提供 = object()
_最大TOKEN數 = 2**63 - 1
_成本格式 = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{0,27}[1-9])?\Z")
_定價版本格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class 呼叫計量:
    """runtime 完成邊界提供的 invocation 級精確計量快照。"""

    input_tokens: int
    output_tokens: int
    estimated_cost_usd: str
    pricing_version: str


def 合併呼叫計量(*計量們: 呼叫計量) -> 呼叫計量:
    """同定價版本的 retries 合併為單一 invocation 計量。"""
    輸入總數 = 輸出總數 = 0
    成本總數 = 定價版本 = 計量 = 快照 = 成本 = context = None
    try:
        if not 計量們:
            raise ValueError
        with localcontext() as context:
            context.prec = 64
            成本總數 = Decimal(0)
            for 計量 in 計量們:
                快照 = _重建呼叫計量(計量)
                if 定價版本 is None:
                    定價版本 = 快照.pricing_version
                elif 快照.pricing_version != 定價版本:
                    raise ValueError
                輸入總數 += 快照.input_tokens
                輸出總數 += 快照.output_tokens
                成本總數 += Decimal(快照.estimated_cost_usd)
                成本 = _正規成本(成本總數)
                快照 = 計量 = None
            if 輸入總數 > _最大TOKEN數 or 輸出總數 > _最大TOKEN數 or 輸入總數 + 輸出總數 > _最大TOKEN數:
                raise ValueError
            成本 = _正規成本(成本總數)
        return 呼叫計量(輸入總數, 輸出總數, 成本, cast(str, 定價版本))
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        計量們 = 計量 = 快照 = 輸入總數 = 輸出總數 = 成本總數 = None
        定價版本 = 成本 = context = None
        if 是控制流程:
            raise
    raise 呼叫儲存錯誤("呼叫計量合併失敗") from None


def _有效TOKEN數(值: object) -> bool:
    """token 數只接受 SQLite/JSON 可安全保存的精確非負整數。"""
    return type(值) is int and 0 <= cast(int, 值) <= _最大TOKEN數


def _正規成本(值: Decimal) -> str:
    """輸出符合持久化 contract 的非 exponent 正規十進位字串。"""
    if type(值) is not Decimal or not 值.is_finite() or 值 < 0:
        raise ValueError
    文字 = format(值, "f")
    if "." in 文字:
        文字 = 文字.rstrip("0").rstrip(".")
    if _成本格式.fullmatch(文字) is None:
        raise ValueError
    return 文字


def _重建呼叫計量(usage: object) -> 呼叫計量:
    """逐一讀取固定 slots、驗證並建立 module-owned DTO。"""
    輸入 = 輸出 = 成本 = 定價版本 = None
    try:
        if type(usage) is not 呼叫計量:
            raise ValueError
        輸入 = usage.input_tokens
        輸出 = usage.output_tokens
        成本 = usage.estimated_cost_usd
        定價版本 = usage.pricing_version
        if (not _有效TOKEN數(輸入) or not _有效TOKEN數(輸出)
                or cast(int, 輸入) + cast(int, 輸出) > _最大TOKEN數
                or type(成本) is not str or _成本格式.fullmatch(成本) is None
                or type(定價版本) is not str or _定價版本格式.fullmatch(定價版本) is None):
            raise ValueError
        Decimal(成本)
        return 呼叫計量(cast(int, 輸入), cast(int, 輸出), 成本, 定價版本)
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        usage = 輸入 = 輸出 = 成本 = 定價版本 = None
        if 是控制流程:
            raise
    raise ValueError from None


class SQLite呼叫儲存庫:
    """只寫入既有endpoint_invocations，不建立任何備援結構。"""

    def __init__(self, 資料庫: str | Path, *, 時鐘: Callable[[], float] = time.time,
                 識別碼工廠: Callable[[], str] | None = None) -> None:
        """保存資料庫位置與可測試依賴；不開啟連線或變更資料庫。"""
        if type(資料庫) not in (str, _Path具體型別) or (type(資料庫) is str and not 資料庫):
            raise 呼叫儲存錯誤("呼叫儲存庫初始化失敗") from None
        if not callable(時鐘) or (識別碼工廠 is not None and not callable(識別碼工廠)):
            raise 呼叫儲存錯誤("呼叫儲存庫初始化失敗") from None
        self._資料庫 = Path(資料庫)
        self._時鐘 = 時鐘
        self._識別碼工廠 = 識別碼工廠 or (lambda: f"inv-{secrets.token_hex(16)}")

    def 建立已解析呼叫(
        self, endpoint_id: str, endpoint_version_id: str, request_id: str, input: object, *,
        credential_id: str | None = None, session_id: str | None = None,
        message_id: str | None = None, metadata: object | None = None,
        metadata_size_bytes: int | None = None, metadata_sha256: str | None = None,
    ) -> str:
        """slug解析成功後建立pending紀錄；回傳id，任何失敗完整回滾並丟固定錯誤。"""
        呼叫識別碼 = 建立時間原值 = 建立時間 = 輸入JSON = metadata_json = None
        try:
            呼叫識別碼 = self._識別碼工廠()
            self._驗證建立值((呼叫識別碼, endpoint_id, endpoint_version_id, request_id),
                         (credential_id, session_id, message_id), metadata_size_bytes, metadata_sha256)
            建立時間原值 = self._時鐘()
            if type(建立時間原值) not in (int, float) or not self._非負有限(建立時間原值):
                raise ValueError
            建立時間 = float(建立時間原值)
            輸入快照 = self._建立可信JSON樹(input)
            metadata快照 = None if metadata is None else self._建立可信JSON樹(metadata)
            input = metadata = None
            輸入JSON = 建立正規JSON(輸入快照)
            metadata_json = None if metadata快照 is None else 建立正規JSON(metadata快照)
            with closing(self._開啟連線()) as 連線, 連線:
                連線.execute("BEGIN IMMEDIATE")
                連線.execute(
                    "INSERT INTO endpoint_invocations("
                    "id,endpoint_id,endpoint_version_id,credential_id,request_id,session_id,message_id,status,"
                    "input_json,metadata_json,metadata_size_bytes,metadata_sha256,created_at) "
                    "VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?,?)",
                    (呼叫識別碼, endpoint_id, endpoint_version_id, credential_id, request_id, session_id,
                     message_id, 輸入JSON, metadata_json, metadata_size_bytes, metadata_sha256, 建立時間),
                )
            return 呼叫識別碼
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            endpoint_id = endpoint_version_id = request_id = input = credential_id = session_id = None
            message_id = metadata = metadata_sha256 = 呼叫識別碼 = 建立時間原值 = 建立時間 = None
            metadata_size_bytes = 輸入快照 = metadata快照 = 輸入JSON = metadata_json = 連線 = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("呼叫建立失敗") from None

    def 標記執行中(self, invocation_id: str) -> None:
        """將pending轉為running；不存在、畸形或其他狀態固定拒絕並回滾。"""
        self._更新狀態(invocation_id, {"pending"}, "running")

    def 附加執行事件(self, invocation_id: str, event_id: str, event_type: str,
               payload: object) -> int:
        """只對running呼叫原子配置單調序號並附加不可覆寫事件。"""
        try:
            return self._附加紀錄("事件", invocation_id, event_id, event_type, payload)
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            invocation_id = event_id = event_type = payload = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("執行事件附加失敗") from None

    def 原子記錄執行事件並結案(
        self, invocation_id: str, event_id: str, event_type: str, payload: object,
        expected_sequence: int, *, status: str | None = None, output: object | None = None,
        error: object | None = None, usage: object | None = None,
        session_pair: tuple[object, ...] | None = None,
    ) -> int:
        """以單一立即交易附加 expected event，並可同時完成 running invocation。

        相同 event 已完整提交時回放原序號；任何不完整、衝突或普通失敗皆回滾。
        """
        連線 = 呼叫列 = 事件列 = 最大序號列 = 游標 = None
        payload快照 = output快照 = error快照 = usage快照 = session快照 = None
        payload_json = output_json = error_json = usage_json = None
        session_user_json = session_assistant_json = None
        session_pair_size = None
        時間原值 = 時間 = 序號 = None
        try:
            if (any(type(值) is not str or not 值.strip() for 值 in (invocation_id, event_id, event_type))
                    or type(expected_sequence) is not int or expected_sequence < 1
                    or status not in (None, "succeeded", "failed")):
                raise ValueError
            if status is None:
                if output is not None or error is not None or usage is not None or session_pair is not None:
                    raise ValueError
            elif ((status == "succeeded" and (output is None or error is not None))
                  or (status == "failed" and (output is not None or error is None))):
                raise ValueError
            if session_pair is not None:
                if status != "succeeded" or type(session_pair) is not tuple or len(session_pair) != 8:
                    raise ValueError
                endpoint, account, session, version, session_sequence, user_message, assistant_message, token_count = session_pair
                if not all(type(值) is str for 值 in (endpoint, account, session, version)):
                    raise ValueError
                endpoint文字, account文字 = str(endpoint), str(account)
                session文字, version文字 = str(session), str(version)
                if (any(not 值 or 值 != 值.strip()
                        for 值 in (endpoint文字, account文字, session文字, version文字))
                        or len(session文字.encode("utf-8")) > 128
                        or any(ord(字元) < 32 or 127 <= ord(字元) <= 159 for 字元 in session文字)
                        or type(session_sequence) is not int or session_sequence < 1
                        or type(token_count) is not int or not 1 <= token_count <= 最大歷史TOKEN數
                        or type(user_message) is not dict or type(assistant_message) is not dict):
                    raise ValueError
                session快照 = (
                    endpoint文字, account文字, session文字, version文字, session_sequence,
                    self._建立可信JSON樹(user_message), self._建立可信JSON樹(assistant_message), token_count,
                )
                session_user_json = 建立正規JSON(session快照[5])
                session_assistant_json = 建立正規JSON(session快照[6])
                session_pair_size = len(session_user_json.encode("utf-8")) + len(session_assistant_json.encode("utf-8"))
                if not 0 < session_pair_size <= 最大歷史位元組:
                    raise ValueError
            with closing(self._開啟連線()) as 連線, 連線:
                連線.execute("BEGIN IMMEDIATE")
                payload快照 = self._建立可信JSON樹(payload)
                if type(payload快照) is not dict:
                    raise ValueError
                output快照 = None if output is None else self._建立可信JSON樹(output)
                error快照 = None if error is None else self._建立可信JSON樹(error)
                usage快照 = None if usage is None else self._建立可信JSON樹(usage)
                payload = output = error = usage = None
                payload_json = 建立正規JSON(payload快照)
                output_json = None if output快照 is None else 建立正規JSON(output快照)
                error_json = None if error快照 is None else 建立正規JSON(error快照)
                usage_json = None if usage快照 is None else 建立正規JSON(usage快照)
                呼叫列 = 連線.execute(
                    "SELECT status,output_json,error_json,usage_json,latency_ms,pricing_version,completed_at "
                    "FROM endpoint_invocations WHERE id=?", (invocation_id,),
                ).fetchone()
                事件列 = 連線.execute(
                    "SELECT invocation_id,sequence_number,event_type,payload_json FROM run_events WHERE id=?",
                    (event_id,),
                ).fetchone()
                if 事件列 is not None:
                    if 事件列 != (invocation_id, expected_sequence, event_type, payload_json):
                        raise ValueError
                    if status is None:
                        if 呼叫列 != ("running", None, None, None, None, None, None):
                            raise ValueError
                    elif (呼叫列 is None or len(呼叫列) != 7 or 呼叫列[0] != status
                          or 呼叫列[1:4] != (output_json, error_json, usage_json)
                          or 呼叫列[4] is not None or 呼叫列[5] is not None
                          or type(呼叫列[6]) not in (int, float)):
                        raise ValueError
                    if session快照 is not None:
                        session列 = 連線.execute(
                            "SELECT endpoint_version_id,user_message_json,assistant_message_json,pair_size_bytes,token_count "
                            "FROM published_session_turn_pairs WHERE endpoint_id=? AND service_account_id=? "
                            "AND session_id=? AND sequence_number=?",
                            session快照[:3] + (session快照[4],),
                        ).fetchone()
                        if session列 != (session快照[3], session_user_json, session_assistant_json,
                                         session_pair_size, session快照[7]):
                            raise ValueError
                    return expected_sequence
                if 呼叫列 != ("running", None, None, None, None, None, None):
                    raise ValueError
                最大序號列 = 連線.execute(
                    "SELECT MAX(sequence_number) FROM run_events WHERE invocation_id=?", (invocation_id,),
                ).fetchone()
                if (最大序號列 is None or len(最大序號列) != 1
                        or (最大序號列[0] is not None
                            and (type(最大序號列[0]) is not int or 最大序號列[0] < 1))):
                    raise ValueError
                序號 = 1 if 最大序號列[0] is None else 最大序號列[0] + 1
                if 序號 != expected_sequence:
                    raise ValueError
                時間原值 = self._時鐘()
                if type(時間原值) not in (int, float) or not self._非負有限(時間原值):
                    raise ValueError
                時間 = float(時間原值)
                連線.execute(
                    "INSERT INTO run_events(id,invocation_id,sequence_number,event_type,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (event_id, invocation_id, 序號, event_type, payload_json, 時間),
                )
                if session快照 is not None:
                    最大session = 連線.execute(
                        "SELECT MAX(sequence_number) FROM published_session_turn_pairs "
                        "WHERE endpoint_id=? AND service_account_id=? AND session_id=?",
                        session快照[:3],
                    ).fetchone()
                    下一session = 1 if 最大session[0] is None else 最大session[0] + 1
                    if 下一session != session快照[4]:
                        raise ValueError
                    連線.execute(
                        "INSERT INTO published_session_turn_pairs("
                        "endpoint_id,service_account_id,session_id,sequence_number,endpoint_version_id,"
                        "user_message_json,assistant_message_json,pair_size_bytes,token_count,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        session快照[:3] + (session快照[4], session快照[3],
                                           session_user_json, session_assistant_json,
                                           session_pair_size, session快照[7], 時間),
                    )
                if status is not None:
                    游標 = 連線.execute(
                        "UPDATE endpoint_invocations SET status=?,output_json=?,error_json=?,usage_json=?,"
                        "completed_at=? WHERE id=? AND status='running'",
                        (status, output_json, error_json, usage_json, 時間, invocation_id),
                    )
                    if 游標.rowcount != 1:
                        raise ValueError
            return expected_sequence
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            invocation_id = event_id = event_type = payload = expected_sequence = status = session_pair = None
            output = error = usage = 連線 = 呼叫列 = 事件列 = 最大序號列 = 游標 = None
            payload快照 = output快照 = error快照 = usage快照 = session快照 = None
            payload_json = output_json = error_json = usage_json = None
            session_user_json = session_assistant_json = session_pair_size = None
            時間原值 = 時間 = 序號 = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("執行事件原子提交失敗") from None

    def 附加工具呼叫(
        self, invocation_id: str, tool_call_id: str, tool_name: str, arguments: object,
        outcome: str, *, result: object = _未提供, error: object = _未提供,
        run_event_id: str | None = None, retry_of_tool_call_id: str | None = None,
        latency_ms: float | None = None,
    ) -> int:
        """依R80單層outcome矩陣附加工具input及唯一output或error。"""
        try:
            if (type(outcome) is not str or outcome not in ("success", "error")
                    or (outcome == "success") != (result is not _未提供)
                    or (outcome == "error") != (error is not _未提供)
                    or not self._非負有限(latency_ms)):
                raise ValueError
            return self._附加紀錄(
                "工具", invocation_id, tool_call_id, tool_name, arguments, outcome=outcome,
                result=result, error=error, run_event_id=run_event_id,
                retry_of_tool_call_id=retry_of_tool_call_id, latency_ms=latency_ms,
            )
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            invocation_id = tool_call_id = tool_name = arguments = outcome = result = error = None
            run_event_id = retry_of_tool_call_id = latency_ms = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("工具呼叫附加失敗") from None

    def _附加紀錄(
        self, 種類: str, invocation_id: str, record_id: str, name: str, input_value: object, *,
        outcome: str | None = None, result: object = _未提供, error: object = _未提供,
        run_event_id: str | None = None, retry_of_tool_call_id: str | None = None,
        latency_ms: float | None = None,
    ) -> int:
        """在BEGIN IMMEDIATE內驗證身分、取快照、配置序號並插入。"""
        連線 = 資料列 = 最大序號列 = 時間原值 = 時間 = 快照 = input_json = None
        result快照 = error快照 = result_json = error_json = 序號 = None
        try:
            if (種類 not in ("事件", "工具")
                    or any(type(值) is not str or not 值.strip()
                           for 值 in (invocation_id, record_id, name))
                    or any(值 is not None and (type(值) is not str or not 值.strip())
                           for 值 in (run_event_id, retry_of_tool_call_id))):
                raise ValueError
            表格 = "run_events" if 種類 == "事件" else "endpoint_tool_calls"
            with closing(self._開啟連線()) as 連線, 連線:
                連線.execute("BEGIN IMMEDIATE")
                資料列 = 連線.execute(
                    "SELECT id,status FROM endpoint_invocations WHERE id=?", (invocation_id,),
                ).fetchone()
                if (資料列 is None or len(資料列) != 2 or type(資料列[0]) is not str
                        or 資料列[0] != invocation_id or type(資料列[1]) is not str
                        or 資料列[1] != "running"):
                    raise ValueError
                for 參照表, 參照識別碼 in (("run_events", run_event_id),
                                     ("endpoint_tool_calls", retry_of_tool_call_id)):
                    if 參照識別碼 is not None:
                        參照列 = 連線.execute(
                            f"SELECT id,invocation_id FROM {參照表} WHERE id=?",
                            (參照識別碼,),
                        ).fetchone()
                        if 參照列 != (參照識別碼, invocation_id):
                            raise ValueError
                快照 = self._建立可信JSON樹(input_value)
                if type(快照) is not dict:
                    raise ValueError
                result快照 = None if result is _未提供 else self._建立可信JSON樹(result)
                error快照 = None if error is _未提供 else self._建立可信JSON樹(error)
                input_value = result = error = None
                input_json = 建立正規JSON(快照)
                result_json = None if result快照 is None and outcome != "success" else 建立正規JSON(result快照)
                error_json = None if error快照 is None and outcome != "error" else 建立正規JSON(error快照)
                時間原值 = self._時鐘()
                if type(時間原值) not in (int, float) or not self._非負有限(時間原值):
                    raise ValueError
                時間 = float(時間原值)
                最大序號列 = 連線.execute(
                    f"SELECT MAX(sequence_number) FROM {表格} WHERE invocation_id=?",
                    (invocation_id,),
                ).fetchone()
                if (最大序號列 is None or len(最大序號列) != 1
                        or (最大序號列[0] is not None and
                            (type(最大序號列[0]) is not int or 最大序號列[0] <= 0))):
                    raise ValueError
                序號 = 1 if 最大序號列[0] is None else 最大序號列[0] + 1
                if 種類 == "事件":
                    連線.execute(
                        "INSERT INTO run_events(id,invocation_id,sequence_number,event_type,payload_json,created_at) "
                        "VALUES (?,?,?,?,?,?)", (record_id, invocation_id, 序號, name, input_json, 時間),
                    )
                else:
                    連線.execute(
                        "INSERT INTO endpoint_tool_calls("
                        "id,invocation_id,run_event_id,sequence_number,tool_name,arguments_json,outcome,"
                        "result_json,error_json,latency_ms,retry_of_tool_call_id,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (record_id, invocation_id, run_event_id, 序號, name, input_json, outcome,
                         result_json, error_json, latency_ms, retry_of_tool_call_id, 時間),
                    )
            return 序號
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            種類 = invocation_id = record_id = name = input_value = outcome = result = error = None
            run_event_id = retry_of_tool_call_id = latency_ms = 連線 = 資料列 = 最大序號列 = None
            時間原值 = 時間 = 快照 = input_json = result快照 = error快照 = None
            result_json = error_json = 序號 = 表格 = 參照表 = 參照識別碼 = 參照列 = None
            if 是控制流程:
                raise
        raise ValueError from None

    def 完成呼叫(self, invocation_id: str, status: str, *, output: object | None = None,
             error: object | None = None, usage: object | None = None,
             latency_ms: float | None = None) -> None:
        """依明確矩陣一次結案；先授權既有狀態，再取時鐘及序列化內容。"""
        try:
            if type(invocation_id) is not str or type(status) is not str:
                raise ValueError
            允許來源 = {"succeeded": {"running"}, "failed": {"pending", "running"},
                    "rate_limited": {"pending"}, "invalid_api_key": {"pending"}}[status]
            是否成功 = status == "succeeded"
            if (是否成功 and (output is None or error is not None)) or (
                not 是否成功 and (output is not None or error is None)
            ) or not self._非負有限(latency_ms):
                raise ValueError
            self._更新狀態(invocation_id, 允許來源, status,
                       output=output, error=error, usage=usage, latency_ms=latency_ms)
            return
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            invocation_id = status = output = error = usage = latency_ms = 允許來源 = 是否成功 = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("呼叫結案失敗") from None

    def _更新狀態(self, invocation_id: str, 允許來源: set[str], status: str, *,
              output: object | None = None, error: object | None = None,
              usage: object | None = None, latency_ms: float | None = None) -> None:
        """在單一交易先授權未結案狀態，再保存 runtime 提供的精確計量。"""
        完成時間原值 = 完成時間 = output_json = error_json = usage_json = 資料列 = None
        pricing_version = 計量快照 = None
        是否結案 = type(status) is str and status != "running"
        try:
            if type(invocation_id) is not str or type(status) is not str:
                raise ValueError
            with closing(self._開啟連線()) as 連線, 連線:
                連線.execute("BEGIN IMMEDIATE")
                資料列 = 連線.execute(
                    "SELECT status,output_json,error_json,usage_json,latency_ms,pricing_version,completed_at "
                    "FROM endpoint_invocations WHERE id=?", (invocation_id,),
                ).fetchone()
                if (資料列 is None or len(資料列) != 7 or type(資料列[0]) is not str
                        or 資料列[0] not in 允許來源 or any(值 is not None for 值 in 資料列[1:])):
                    raise ValueError
                來源狀態 = 資料列[0]
                if status == "running":
                    游標 = 連線.execute(
                        "UPDATE endpoint_invocations SET status='running' WHERE id=? AND status=?",
                        (invocation_id, 來源狀態),
                    )
                else:
                    完成時間原值 = self._時鐘()
                    if type(完成時間原值) not in (int, float) or not self._非負有限(完成時間原值):
                        raise ValueError
                    完成時間 = float(完成時間原值)
                    output快照 = None if output is None else self._建立可信JSON樹(output)
                    error快照 = None if error is None else self._建立可信JSON樹(error)
                    if type(usage) is 呼叫計量:
                        計量快照 = _重建呼叫計量(usage)
                        usage快照 = {
                            "input_tokens": 計量快照.input_tokens,
                            "output_tokens": 計量快照.output_tokens,
                            "total_tokens": 計量快照.input_tokens + 計量快照.output_tokens,
                            "estimated_cost_usd": 計量快照.estimated_cost_usd,
                        }
                        pricing_version = 計量快照.pricing_version
                    else:
                        usage快照 = None if usage is None else self._建立可信JSON樹(usage)
                    output = error = usage = 計量快照 = None
                    output_json = None if output快照 is None else 建立正規JSON(output快照)
                    error_json = None if error快照 is None else 建立正規JSON(error快照)
                    usage_json = None if usage快照 is None else 建立正規JSON(usage快照)
                    游標 = 連線.execute(
                        "UPDATE endpoint_invocations SET status=?,output_json=?,error_json=?,usage_json=?,"
                        "latency_ms=?,pricing_version=?,completed_at=? WHERE id=? AND status=?",
                        (status, output_json, error_json, usage_json, latency_ms, pricing_version, 完成時間,
                         invocation_id, 來源狀態),
                    )
                if 游標.rowcount != 1:
                    raise ValueError
            return
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            invocation_id = 允許來源 = status = output = error = usage = latency_ms = None
            完成時間原值 = 完成時間 = output_json = error_json = usage_json = 資料列 = None
            來源狀態 = 游標 = 連線 = output快照 = error快照 = usage快照 = None
            pricing_version = 計量快照 = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("呼叫結案失敗" if 是否結案 else "呼叫狀態更新失敗") from None

    def _開啟連線(self) -> sqlite3.Connection:
        """只對釘住身分的既有非空一般檔，以rw模式開啟並驗證必要schema。"""
        連線 = 路徑 = uri = 開啟前 = 開啟後 = None
        try:
            路徑 = self._資料庫.absolute()
            開啟前 = os.lstat(路徑)
            if not stat.S_ISREG(開啟前.st_mode) or 開啟前.st_size <= 0:
                raise ValueError
            uri = 路徑.as_uri() + "?mode=rw"
            連線 = sqlite3.connect(uri, uri=True, isolation_level=None)
            開啟後 = os.lstat(路徑)
            if stat.S_ISLNK(開啟後.st_mode) or (開啟前.st_dev, 開啟前.st_ino) != (開啟後.st_dev, 開啟後.st_ino):
                raise ValueError
            連線.execute("PRAGMA foreign_keys=ON")
            if 連線.execute("PRAGMA foreign_keys").fetchone() != (1,):
                raise ValueError
            schema_version = 連線.execute("PRAGMA schema_version").fetchone()
            if (schema_version is None or len(schema_version) != 1
                    or type(schema_version[0]) is not int or schema_version[0] <= 0):
                raise ValueError
            呼叫結構 = tuple(連線.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE tbl_name='endpoint_invocations' ORDER BY type,name"
            ))
            事件結構 = tuple(連線.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE tbl_name='run_events' ORDER BY type,name"
            ))
            工具結構 = tuple(連線.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE tbl_name='endpoint_tool_calls' ORDER BY type,name"
            ))
            ledger = tuple(連線.execute(
                "SELECT version,name FROM published_api_schema_migrations ORDER BY version"
            ))
            if (呼叫結構 != _必要呼叫結構 or 事件結構 != _必要事件結構
                    or 工具結構 != _必要工具結構 or ledger != _必要遷移):
                raise ValueError
            return 連線
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            if 連線 is not None:
                try:
                    連線.close()
                except BaseException as 關閉錯誤:
                    關閉是控制流程 = type(關閉錯誤) in _控制流程例外
                    連線 = 路徑 = uri = 開啟前 = 開啟後 = None
                    schema_version = 呼叫結構 = 事件結構 = 工具結構 = ledger = 邊界錯誤 = None
                    if 關閉是控制流程:
                        raise
            連線 = 路徑 = uri = 開啟前 = 開啟後 = schema_version = None
            呼叫結構 = 事件結構 = 工具結構 = ledger = None
            if 是控制流程:
                raise
        raise 呼叫儲存錯誤("呼叫資料庫開啟失敗") from None

    @staticmethod
    def _非負有限(值: object) -> bool:
        """接受None或精確、有限、非負的int/float。"""
        try:
            return 值 is None or (type(值) in (int, float) and math.isfinite(float(值)) and float(值) >= 0)
        except OverflowError:
            return False

    @classmethod
    def _建立可信JSON樹(cls, 值: object, 路徑: set[int] | None = None) -> object:
        """單次走訪建立脫離呼叫者的精確內建 JSON 樹，拒絕子類、循環及非有限數。"""
        值型別 = type(值)
        if 值 is None or 值型別 in (bool, int, str):
            return 值
        if 值型別 is float:
            if math.isfinite(cast(float, 值)):
                return 值
            raise ValueError
        if 值型別 not in (list, dict):
            raise ValueError
        if 路徑 is None:
            路徑 = set()
        容器識別 = id(值)
        if 容器識別 in 路徑:
            raise ValueError
        路徑.add(容器識別)
        try:
            if 值型別 is list:
                結果串列: list[object] = []
                for 項目 in list.__iter__(cast(list[object], 值)):
                    結果串列.append(cls._建立可信JSON樹(項目, 路徑))
                return 結果串列
            結果字典: dict[str, object] = {}
            for 鍵, 項目 in dict.items(cast(dict[object, object], 值)):
                if type(鍵) is not str:
                    raise ValueError
                結果字典[鍵] = cls._建立可信JSON樹(項目, 路徑)
            return 結果字典
        except BaseException as 邊界錯誤:
            是控制流程 = type(邊界錯誤) in _控制流程例外
            值 = 項目 = 鍵 = 結果串列 = 結果字典 = None
            if 是控制流程:
                raise
            raise
        finally:
            路徑.remove(容器識別)

    @classmethod
    def _驗證建立值(cls, 必填識別碼, 可空識別碼, metadata_size_bytes, metadata_sha256) -> None:
        """驗證建立參數型別、nullable參照及摘要欄位；介面不接收API key。"""
        if (any(type(值) is not str or not 值.strip() for 值 in 必填識別碼)
                or any(值 is not None and (type(值) is not str or not 值.strip()) for 值 in 可空識別碼)
                or (metadata_size_bytes is not None and
                    (type(metadata_size_bytes) is not int or metadata_size_bytes < 0))
                or (metadata_sha256 is not None and
                    (type(metadata_sha256) is not str or len(metadata_sha256) != 64
                     or any(字元 not in "0123456789abcdef" for 字元 in metadata_sha256)))):
            raise ValueError
