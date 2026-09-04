"""PostgreSQL runtime snapshot/service-account/bundle projection。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Callable

from 繁中代理.PostgreSQL連線 import 交易連線
from 繁中代理.交易儲存設定 import 交易儲存設定
from .快照儲存庫 import 發布快照儲存庫錯誤, _重建工具, _正規JSON
from .執行器 import 發布執行快照
from .模型契約 import 設定鍵, 重建設定
from .服務帳戶 import ServiceAccountContext
from ..技能套件.安全複製 import 技能套件最大總位元組數
from ..技能套件.CloudStorage權威 import 解析CloudStorage清單參照
from ..技能套件.載入器 import 技能套件定位


_識別 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_工具修訂 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")
_雜湊 = re.compile(r"[0-9a-f]{64}\Z")
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_JSON上限 = 1_000_000


class PostgreSQL發布快照儲存庫:
    """從同一 exact-version PostgreSQL authority 重建三個 canonical consumer DTO。"""

    def __init__(self, 設定: 交易儲存設定, 工具摘要計算器: Callable[..., str]) -> None:
        if type(設定) is not 交易儲存設定 or 設定.後端 != "postgres" or not callable(工具摘要計算器):
            raise ValueError("PostgreSQL發布快照設定無效")
        self._設定 = 設定
        self._工具摘要 = 工具摘要計算器

    @staticmethod
    def _JSON值(value: Any) -> Any:
        """接受 psycopg JSONB 或 canonical JSON text，並拒絕非 canonical/nonfinite 資料。"""
        if isinstance(value, str):
            if len(value.encode("utf-8")) > _JSON上限:
                raise ValueError
            parsed = json.loads(
                value,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if _正規JSON(parsed) != value:
                raise ValueError
            return parsed
        canonical = _正規JSON(value)
        if len(canonical.encode("utf-8")) > _JSON上限:
            raise ValueError
        return value

    @classmethod
    def _JSON原文(cls, value: Any) -> str:
        return _正規JSON(cls._JSON值(value))

    def _列(self, version: str) -> dict[str, Any]:
        try:
            if not _是識別(version):
                raise ValueError
            with 交易連線(self._設定) as connection:
                rows = connection.execute(
                    "SELECT v.id,v.endpoint_id,e.service_account_id,e.status,sa.disabled_at,"
                    "v.system_prompt,v.allowed_tools,v.tool_schema_snapshot,v.tool_runtime_revision,"
                    "v.model_config_snapshot,v.response_schema,v.skill_bundle_manifest,"
                    "b.manifest_reference,b.manifest_digest,b.bundle_hash,b.state,b.bundle_id,"
                    "b.total_bytes,b.published_at,b.reconciled_at "
                    "FROM published_endpoint_versions v "
                    "JOIN published_endpoints e ON e.id=v.endpoint_id "
                    "JOIN service_accounts sa ON sa.id=e.service_account_id "
                    "JOIN published_skill_bundles b ON b.version_id=v.id "
                    "WHERE v.id=%s LIMIT 2",
                    (version,),
                ).fetchall()
            if len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError
            row = dict(rows[0])
            self._驗證列(row, version)
            return row
        except _控制流程:
            raise
        except BaseException:
            raise 發布快照儲存庫錯誤("發布快照不可用") from None

    def _驗證列(self, row: dict[str, Any], version: str) -> None:
        required = {
            "id", "endpoint_id", "service_account_id", "status", "disabled_at",
            "system_prompt", "allowed_tools", "tool_schema_snapshot", "tool_runtime_revision",
            "model_config_snapshot", "response_schema", "manifest_reference", "manifest_digest",
            "bundle_hash", "state", "bundle_id", "total_bytes",
        }
        # 真實 psycopg dict_row 由上方固定 SELECT 保證包含全部 optional lifecycle
        # columns；保留舊的最小 fake injection compatibility，不以 mapping 插入順序為契約。
        if not required.issubset(row) or row["id"] != version:
            raise ValueError
        if any(not _是識別(row[name]) for name in ("id", "endpoint_id", "service_account_id", "bundle_id")):
            raise ValueError
        if row["status"] != "active" or row["disabled_at"] is not None:
            raise ValueError
        if (type(row["system_prompt"]) is not str or not row["system_prompt"].strip()
                or len(row["system_prompt"].encode("utf-8")) > 500_000
                or not _是工具修訂(row["tool_runtime_revision"])):
            raise ValueError
        try:
            物件鍵, 世代 = 解析CloudStorage清單參照(row["manifest_reference"])
        except _控制流程:
            raise
        except BaseException:
            raise ValueError from None
        if (物件鍵 != f'bundles/v1/{row["bundle_id"]}/manifest.json'
                or type(世代) is not int or 世代 <= 0
                or not _是雜湊(row["manifest_digest"])
                or not _是雜湊(row["bundle_hash"])):
            raise ValueError
        if type(row["total_bytes"]) is not int or not 0 < row["total_bytes"] <= 技能套件最大總位元組數:
            raise ValueError
        if row["state"] not in ("published", "reconciled"):
            raise ValueError
        if "published_at" in row or "reconciled_at" in row:
            published_at, reconciled_at = row.get("published_at"), row.get("reconciled_at")
            if not _是PG時間(published_at):
                raise ValueError
            if row["state"] == "published":
                if reconciled_at is not None:
                    raise ValueError
            elif not _是PG時間(reconciled_at) or reconciled_at < published_at:
                raise ValueError

        allowed = self._JSON值(row["allowed_tools"])
        schema = self._JSON值(row["tool_schema_snapshot"])
        model = self._JSON值(row["model_config_snapshot"])
        response = self._JSON值(row["response_schema"])
        manifest = self._JSON值(row["skill_bundle_manifest"]) if "skill_bundle_manifest" in row else {}
        if (type(allowed) is not list or len(allowed) > 256
                or any(not _是識別(item) for item in allowed) or len(set(allowed)) != len(allowed)
                or type(schema) is not dict
                or type(model) is not dict or frozenset(model) != 設定鍵
                or response is not None and type(response) is not dict
                or type(manifest) is not dict):
            raise ValueError
        # 驗證 exact allowlist/schema inventory、每個 tool revision 與 digest helper。
        _重建工具(_正規JSON(allowed), _正規JSON(schema), self._工具摘要)
        重建設定(model)

    @staticmethod
    def _權限摘要(row: dict[str, Any], allowed: list[str]) -> str:
        projection = {
            "allowed_tools": allowed,
            "skill_bundle_hash": row["bundle_hash"],
            "tool_handler_release": row["tool_runtime_revision"],
        }
        return hashlib.sha256(_正規JSON(projection).encode("utf-8")).hexdigest()

    def 取得發布執行快照(self, endpoint_version_id: str) -> 發布執行快照:
        try:
            row = self._列(endpoint_version_id)
            model_data = self._JSON值(row["model_config_snapshot"])
            response = self._JSON值(row["response_schema"])
            allowed = self._JSON值(row["allowed_tools"])
            tools = _重建工具(
                _正規JSON(allowed), self._JSON原文(row["tool_schema_snapshot"]), self._工具摘要,
            )
            return 發布執行快照(
                endpoint_id=row["endpoint_id"], version_id=row["id"],
                service_account_id=row["service_account_id"], system_prompt=row["system_prompt"],
                permission_snapshot_digest=self._權限摘要(row, allowed),
                skill_bundle_hash=row["bundle_hash"],
                tool_handler_release=row["tool_runtime_revision"], tool_snapshot=tools,
                model_config=重建設定(model_data), response_schema=response,
                manifest_reference=row["manifest_reference"],
            )
        except _控制流程:
            raise
        except BaseException:
            raise 發布快照儲存庫錯誤("發布快照不可用") from None

    def 載入服務帳戶上下文(
        self, service_account_id: str, endpoint_version_id: str, source: str,
    ) -> ServiceAccountContext:
        try:
            if source != "endpoint_version_snapshot" or not _是識別(service_account_id):
                raise ValueError
            row = self._列(endpoint_version_id)
            if row["service_account_id"] != service_account_id:
                raise ValueError
            allowed = self._JSON值(row["allowed_tools"])
            return ServiceAccountContext(
                service_account_id=service_account_id,
                endpoint_version_id=endpoint_version_id,
                permission_snapshot_digest=self._權限摘要(row, allowed),
                allowed_tools=tuple(allowed),
                skill_bundle_hash=row["bundle_hash"],
                tool_handler_release=row["tool_runtime_revision"],
            )
        except _控制流程:
            raise
        except BaseException:
            raise 發布快照儲存庫錯誤("發布快照不可用") from None

    def 取得技能套件定位(self, endpoint_version_id: str) -> 技能套件定位:
        try:
            row = self._列(endpoint_version_id)
            return 技能套件定位(
                version_id=row["id"], bundle_id=row["bundle_id"],
                manifest_reference=row["manifest_reference"],
                manifest_digest=row["manifest_digest"], bundle_hash=row["bundle_hash"],
                total_bytes=row["total_bytes"],
            )
        except _控制流程:
            raise
        except BaseException:
            raise 發布快照儲存庫錯誤("發布快照不可用") from None


def _是識別(value: object) -> bool:
    return type(value) is str and _識別.fullmatch(value) is not None


def _是工具修訂(value: object) -> bool:
    return type(value) is str and _工具修訂.fullmatch(value) is not None


def _是雜湊(value: object) -> bool:
    return type(value) is str and _雜湊.fullmatch(value) is not None


def _是PG時間(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None
