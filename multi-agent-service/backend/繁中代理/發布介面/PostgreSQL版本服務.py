"""Published immutable versions 與 current pointer 的 PostgreSQL 服務。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb

from ..環境設定 import 交易儲存設定
from .PostgreSQL工作單元 import PostgreSQL工作單元
from .規劃.端點發布 import 發布版本快照, _重建版本快照, _正規JSON, _是識別, _是有限非負
from .規劃.版本服務 import _重建原子套件收據
from .規劃.版本服務 import (
    下一版本準備, 版本配置結果, 版本配置輸入錯誤, 版本存取錯誤, 版本配置錯誤,
    版本啟用結果, 版本啟用輸入錯誤, 版本啟用存取錯誤, 版本啟用錯誤,
    已釘選版本, 目前版本不存在錯誤, 目前版本解析錯誤,
)
from .規劃.綱要 import _slug格式


def _get(row: Any, key: str, index: int) -> Any:
    del index
    if not isinstance(row, dict) or key not in row:
        raise TypeError("PostgreSQL row shape invalid")
    return row[key]


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _時間戳(epoch秒: float) -> datetime:
    return datetime.fromtimestamp(float(epoch秒), timezone.utc)


def _epoch(value: Any) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.timestamp()
    return float(value)


def _JSONB(value: Any) -> Jsonb:
    return Jsonb(json.loads(_正規JSON(value)))


def _snapshot_from_row(
    row: Any, start: int = 0, *, created_by_index: int | None = None,
) -> 發布版本快照:
    names = (
        "original_requirement_text", "system_prompt", "allowed_skills", "allowed_tools",
        "tool_schema_snapshot", "tool_runtime_revision", "model_config_snapshot",
        "retry_policy", "skill_bundle_manifest", "input_schema",
        "response_schema", "created_by_user_id",
    )
    vals = [_get(row, name, start + i) for i, name in enumerate(names)]
    del created_by_index
    return 發布版本快照(
        original_requirement_text=vals[0], system_prompt=vals[1],
        allowed_skills=_json_value(vals[2]), allowed_tools=_json_value(vals[3]),
        tool_schema_snapshot=_json_value(vals[4]), tool_runtime_revision=vals[5],
        model_config_snapshot=_json_value(vals[6]), retry_policy=_json_value(vals[7]),
        skill_bundle_manifest=_json_value(vals[8]),
        input_schema=None if vals[9] is None else _json_value(vals[9]),
        response_schema=_json_value(vals[10]), created_by_user_id=vals[11],
    )


class PostgreSQL版本配置服務:
    """以 endpoint row lock 配置 immutable version，並以 CAS 切換 current pointer。"""

    __slots__ = ("_工作單元", "_版本識別工廠", "_時鐘")

    def __init__(
        self, 設定: 交易儲存設定, version_id_factory: Callable[[], str],
        clock: Callable[[], float],
    ) -> None:
        if not callable(version_id_factory) or not callable(clock):
            raise 版本配置錯誤("版本配置失敗") from None
        self._工作單元 = PostgreSQL工作單元(設定)
        self._版本識別工廠 = version_id_factory
        self._時鐘 = clock

    def 準備下一版本(self, owner_user_id: str, endpoint_id: str) -> 下一版本準備:
        if not _是識別(owner_user_id) or not _是識別(endpoint_id):
            raise 版本配置輸入錯誤("版本配置輸入無效") from None
        try:
            with self._工作單元.交易() as conn:
                endpoint = conn.execute(
                    "SELECT owner_user_id,status,current_version_id FROM published_endpoints "
                    "WHERE id=%s FOR SHARE", (endpoint_id,),
                ).fetchone()
                if (endpoint is None or _get(endpoint, "owner_user_id", 0) != owner_user_id
                        or _get(endpoint, "status", 1) != "active"
                        or not _是識別(_get(endpoint, "current_version_id", 2))):
                    raise 版本存取錯誤("版本配置存取遭拒")
                current_id = _get(endpoint, "current_version_id", 2)
                row = conn.execute(
                    "SELECT version_number,original_requirement_text,system_prompt,allowed_skills,"
                    "allowed_tools,tool_schema_snapshot,tool_runtime_revision,model_config_snapshot,"
                    "retry_policy,skill_bundle_manifest,input_schema,response_schema,"
                    "created_by_user_id FROM published_endpoint_versions "
                    "WHERE id=%s AND endpoint_id=%s", (current_id, endpoint_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError
                number = _get(row, "version_number", 0)
                if type(number) is not int or number < 1:
                    raise RuntimeError
                return 下一版本準備(
                    endpoint_id, owner_user_id, current_id, number + 1, _snapshot_from_row(row, 1),
                )
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 版本存取錯誤):
            raise
        except BaseException:
            raise 版本配置錯誤("版本配置失敗") from None

    def 配置(
        self, owner_user_id: str, endpoint_id: str, prepared_snapshot: 發布版本快照,
    ) -> 版本配置結果:
        try:
            if not _是識別(owner_user_id) or not _是識別(endpoint_id):
                raise 版本配置輸入錯誤("版本配置輸入無效")
            snapshot = _重建版本快照(prepared_snapshot)
            if snapshot.created_by_user_id != owner_user_id:
                raise 版本配置輸入錯誤("版本配置輸入無效")
            version_id, created_at = self._版本識別工廠(), float(self._時鐘())
            if not _是識別(version_id) or not _是有限非負(created_at):
                raise 版本配置錯誤("版本配置失敗")
            with self._工作單元.交易() as conn:
                endpoint = conn.execute(
                    "SELECT owner_user_id,status,current_version_id FROM published_endpoints "
                    "WHERE id=%s FOR UPDATE", (endpoint_id,),
                ).fetchone()
                if (endpoint is None or _get(endpoint, "owner_user_id", 0) != owner_user_id
                        or _get(endpoint, "status", 1) != "active"):
                    raise 版本存取錯誤("版本配置存取遭拒")
                current_id = _get(endpoint, "current_version_id", 2)
                previous = conn.execute(
                    "SELECT version_number,input_schema,response_schema "
                    "FROM published_endpoint_versions WHERE id=%s AND endpoint_id=%s FOR SHARE",
                    (current_id, endpoint_id),
                ).fetchone()
                if previous is None:
                    raise RuntimeError
                number = _get(previous, "version_number", 0)
                if type(number) is not int or number < 1:
                    raise RuntimeError
                input_json = None if snapshot.input_schema is None else _正規JSON(snapshot.input_schema)
                response_json = _正規JSON(snapshot.response_schema)
                old_input = _get(previous, "input_schema", 1)
                old_response = _get(previous, "response_schema", 2)
                changed = not (
                    (None if old_input is None else _正規JSON(_json_value(old_input))) == input_json
                    and _正規JSON(_json_value(old_response)) == response_json
                )
                conn.execute(
                    "INSERT INTO published_endpoint_versions("
                    "id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills,"
                    "allowed_tools,tool_schema_snapshot,tool_runtime_revision,model_config_snapshot,"
                    "retry_policy,skill_bundle_manifest,input_schema,response_schema,"
                    "schema_changed,created_by_user_id,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (version_id, endpoint_id, number + 1, snapshot.original_requirement_text,
                     snapshot.system_prompt, _JSONB(snapshot.allowed_skills),
                     _JSONB(snapshot.allowed_tools), _JSONB(snapshot.tool_schema_snapshot),
                     snapshot.tool_runtime_revision, _JSONB(snapshot.model_config_snapshot),
                     _JSONB(snapshot.retry_policy), _JSONB(snapshot.skill_bundle_manifest),
                     None if snapshot.input_schema is None else _JSONB(snapshot.input_schema),
                     _JSONB(snapshot.response_schema), changed, owner_user_id, _時間戳(created_at)),
                )
            return 版本配置結果(version_id, endpoint_id, number + 1, changed, created_at)
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 版本配置輸入錯誤, 版本存取錯誤):
            raise
        except ValueError:
            raise 版本配置輸入錯誤("版本配置輸入無效") from None
        except BaseException:
            raise 版本配置錯誤("版本配置失敗") from None

    建立版本 = 配置

    def 啟用(
        self, owner_user_id: str, endpoint_id: str, version_id: str, *,
        request_id: str | None = None, bundle_verifier: Callable[..., Any] | None = None,
        audit_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> 版本啟用結果:
        if (not _是識別(owner_user_id) or not _是識別(endpoint_id) or not _是識別(version_id)
                or (request_id is not None and not _是識別(request_id))
                or (bundle_verifier is not None and not callable(bundle_verifier))
                or (audit_id_factory is not None and not callable(audit_id_factory))
                or (clock is not None and not callable(clock))):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None
        try:
            with self._工作單元.交易() as conn:
                endpoint = conn.execute(
                    "SELECT owner_user_id,status,current_version_id FROM published_endpoints "
                    "WHERE id=%s FOR UPDATE", (endpoint_id,),
                ).fetchone()
                if (endpoint is None or _get(endpoint, "owner_user_id", 0) != owner_user_id
                        or _get(endpoint, "status", 1) != "active"):
                    raise 版本啟用存取錯誤("版本啟用存取遭拒")
                old_id = _get(endpoint, "current_version_id", 2)
                current = conn.execute(
                    "SELECT version_number FROM published_endpoint_versions "
                    "WHERE id=%s AND endpoint_id=%s", (old_id, endpoint_id),
                ).fetchone()
                target = conn.execute(
                    "SELECT version_number,skill_bundle_manifest FROM published_endpoint_versions "
                    "WHERE id=%s AND endpoint_id=%s FOR SHARE", (version_id, endpoint_id),
                ).fetchone()
                old_number = _get(current, "version_number", 0)
                number = _get(target, "version_number", 0)
                if type(old_number) is not int or type(number) is not int or number != old_number + 1:
                    raise 版本啟用錯誤("版本啟用失敗")
                manifest = _json_value(_get(target, "skill_bundle_manifest", 1))
                if bundle_verifier is not None and bundle_verifier(manifest, version_id, endpoint_id) is not True:
                    raise 版本啟用錯誤("版本啟用失敗")
                audit_id = (audit_id_factory or (lambda: f"version-switch-{version_id}"))()
                activated_at = float((clock or self._時鐘)())
                if not _是識別(audit_id) or not _是有限非負(activated_at):
                    raise 版本啟用錯誤("版本啟用失敗")
                metadata = _正規JSON({
                    "old_version_id": old_id, "new_version_id": version_id,
                    "version_number": number,
                })
                conn.execute(
                    "INSERT INTO audit_events("
                    "id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,"
                    "request_id,endpoint_id,invocation_id,metadata,created_at) "
                    "VALUES(%s,%s,%s,'endpoint_version_activated','success','user',%s,"
                    "'published_endpoint_version',%s,%s,%s,NULL,%s,%s)",
                    (audit_id, audit_id, _時間戳(activated_at), owner_user_id, version_id, request_id,
                     endpoint_id, _JSONB(_json_value(metadata)), _時間戳(activated_at)),
                )
                updated = conn.execute(
                    "UPDATE published_endpoints SET current_version_id=%s,updated_at=%s "
                    "WHERE id=%s AND owner_user_id=%s AND status='active' AND current_version_id=%s "
                    "RETURNING current_version_id",
                    (version_id, _時間戳(activated_at), endpoint_id, owner_user_id, old_id),
                ).fetchone()
                if updated is None or _get(updated, "current_version_id", 0) != version_id:
                    raise 版本啟用錯誤("版本啟用失敗")
            return 版本啟用結果(endpoint_id, old_id, version_id, number, audit_id, activated_at)
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 版本啟用存取錯誤):
            raise
        except 版本啟用錯誤:
            raise
        except BaseException:
            raise 版本啟用錯誤("版本啟用失敗") from None

    切換目前版本 = 啟用

    def 配置並啟用(self, *, 執行者使用者識別碼: str, 執行者類型: str,
                  端點識別碼: str, 已準備快照: 發布版本快照,
                  已準備版本識別碼: str, 已準備時間: float,
                  套件收據: Any, 稽核識別碼: str, 請求識別碼: str | None = None,
                  套件驗證器: Callable[..., Any] | None = None) -> 版本啟用結果:
        """Atomic-protocol facade used by the controller coordinator."""
        if 執行者類型 not in ("user", "admin"):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None
        if not all(type(x) is str and _是識別(x) for x in (執行者使用者識別碼, 端點識別碼, 已準備版本識別碼, 稽核識別碼)):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None
        if type(已準備時間) not in (int, float) or not _是有限非負(已準備時間) or not isinstance(已準備快照, 發布版本快照):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None
        if 套件驗證器 is not None and not callable(套件驗證器):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None
        完整寫入 = False
        try:
            snapshot = _重建版本快照(已準備快照)
            with self._工作單元.交易() as conn:
                endpoint = conn.execute("SELECT owner_user_id,status,current_version_id FROM published_endpoints WHERE id=%s FOR UPDATE", (端點識別碼,)).fetchone()
                if endpoint is None or _get(endpoint, "owner_user_id", 0) != 執行者使用者識別碼 or _get(endpoint, "status", 1) != "active":
                    raise 版本啟用存取錯誤("版本啟用存取遭拒")
                old_id = _get(endpoint, "current_version_id", 2)
                old = conn.execute("SELECT version_number,input_schema,response_schema FROM published_endpoint_versions WHERE id=%s AND endpoint_id=%s FOR SHARE", (old_id, 端點識別碼)).fetchone()
                if old is None or _get(old, "version_number", 0) + 1 < 2:
                    raise 版本啟用錯誤("版本啟用失敗")
                number = _get(old, "version_number", 0) + 1
                if 套件驗證器 is not None and 套件驗證器(套件收據, 已準備版本識別碼, 端點識別碼) is not True:
                    raise 版本啟用錯誤("版本啟用失敗")
                conn.execute("INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills,allowed_tools,tool_schema_snapshot,tool_runtime_revision,model_config_snapshot,retry_policy,skill_bundle_manifest,input_schema,response_schema,schema_changed,created_by_user_id,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s)", (已準備版本識別碼, 端點識別碼, number, snapshot.original_requirement_text, snapshot.system_prompt, _JSONB(snapshot.allowed_skills), _JSONB(snapshot.allowed_tools), _JSONB(snapshot.tool_schema_snapshot), snapshot.tool_runtime_revision, _JSONB(snapshot.model_config_snapshot), _JSONB(snapshot.retry_policy), _JSONB(snapshot.skill_bundle_manifest), None if snapshot.input_schema is None else _JSONB(snapshot.input_schema), _JSONB(snapshot.response_schema), 執行者使用者識別碼, _時間戳(已準備時間)))
                if 套件收據 is not None:
                    conn.execute("INSERT INTO published_skill_bundles(bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at) VALUES(%s,%s,%s,%s,%s,%s,'published',%s)", (套件收據.套件識別碼, 已準備版本識別碼, 套件收據.清單參照, 套件收據.清單摘要, 套件收據.套件雜湊, 套件收據.總位元組數, _時間戳(已準備時間)))
                conn.execute("INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata,created_at) VALUES(%s,%s,%s,'endpoint_version_activated','success',%s,%s,'published_endpoint_version',%s,%s,%s,NULL,%s,%s)", (稽核識別碼, 稽核識別碼, _時間戳(已準備時間), 執行者類型, 執行者使用者識別碼, 已準備版本識別碼, 請求識別碼, 端點識別碼, _JSONB({"old_version_id": old_id, "new_version_id": 已準備版本識別碼, "version_number": number}), _時間戳(已準備時間)))
                result = conn.execute("UPDATE published_endpoints SET current_version_id=%s,updated_at=%s WHERE id=%s AND owner_user_id=%s AND status='active' AND current_version_id=%s RETURNING current_version_id", (已準備版本識別碼, _時間戳(已準備時間), 端點識別碼, 執行者使用者識別碼, old_id)).fetchone()
                if result is None or _get(result, "current_version_id", 0) != 已準備版本識別碼:
                    raise 版本啟用錯誤("版本啟用失敗")
                完整寫入 = True
            return 版本啟用結果(端點識別碼, old_id, 已準備版本識別碼, number, 稽核識別碼, float(已準備時間))
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 版本啟用存取錯誤, 版本啟用錯誤): raise
        except BaseException as error:
            if 完整寫入:
                判定 = self._判定配置並啟用提交(
                    執行者使用者識別碼, 執行者類型, 端點識別碼,
                    已準備版本識別碼, number, 套件收據, 稽核識別碼,
                    已準備時間, 請求識別碼,
                )
                if 判定 == "committed":
                    return 版本啟用結果(端點識別碼, old_id, 已準備版本識別碼, number, 稽核識別碼, float(已準備時間))
                if 判定 == "unknown":
                    raise 版本配置錯誤("版本配置耐久性未知") from None
            raise 版本啟用錯誤("版本啟用失敗") from None

    def _判定配置並啟用提交(self, actor: str, actor_type: str, endpoint_id: str,
                         version_id: str, number: int, receipt: Any, audit_id: str,
                         created_at: float, request_id: str | None) -> str:
        """以 fresh transaction 對 endpoint/current、version、bundle、audit 做三態判定。"""
        try:
            with self._工作單元.交易() as conn:
                row = conn.execute(
                    "SELECT ("
                    "EXISTS(SELECT 1 FROM published_endpoints WHERE id=%s AND owner_user_id=%s AND status='active' AND current_version_id=%s) AND "
                    "EXISTS(SELECT 1 FROM published_endpoint_versions WHERE id=%s AND endpoint_id=%s AND version_number=%s AND created_by_user_id=%s AND created_at=%s) AND "
                    "EXISTS(SELECT 1 FROM published_skill_bundles WHERE bundle_id=%s AND version_id=%s AND manifest_reference=%s AND manifest_digest=%s AND bundle_hash=%s AND total_bytes=%s AND state='published' AND published_at=%s) AND "
                    "EXISTS(SELECT 1 FROM audit_events WHERE id=%s AND event_id=%s AND action='endpoint_version_activated' AND outcome='success' AND actor_type=%s AND actor_id=%s AND resource_id=%s AND request_id IS NOT DISTINCT FROM %s AND endpoint_id=%s AND occurred_at=%s AND created_at=%s)"
                    ") AS graph_matches, ("
                    "EXISTS(SELECT 1 FROM published_endpoints WHERE id=%s OR current_version_id=%s) OR "
                    "EXISTS(SELECT 1 FROM published_endpoint_versions WHERE id=%s) OR "
                    "EXISTS(SELECT 1 FROM published_skill_bundles WHERE bundle_id=%s) OR "
                    "EXISTS(SELECT 1 FROM audit_events WHERE id=%s)"
                    ") AS any_candidate",
                    (endpoint_id, actor, version_id, version_id, endpoint_id, number, actor, _時間戳(created_at),
                     receipt.套件識別碼, version_id, receipt.清單參照, receipt.清單摘要,
                     receipt.套件雜湊, receipt.總位元組數, _時間戳(created_at), audit_id, audit_id,
                     actor_type, actor, version_id, request_id, endpoint_id, _時間戳(created_at),
                     _時間戳(created_at), endpoint_id, version_id, version_id, receipt.套件識別碼, audit_id),
                ).fetchone()
            if _get(row, "graph_matches", 0) is True:
                return "committed"
            if _get(row, "any_candidate", 1) is False:
                return "not_committed"
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            return "unknown"
        return "unknown"

    def 判定版本配置提交結果(self, **kwargs: Any) -> Any:
        """以 fresh exact graph readback 回傳版本配置的三態結果。"""
        from .規劃.版本服務 import 版本配置提交判定
        try:
            outcome = self._判定配置並啟用提交(
                kwargs["執行者使用者識別碼"], kwargs["執行者類型"],
                kwargs["端點識別碼"], kwargs["版本識別碼"], kwargs["版本號碼"],
                kwargs["套件收據"], kwargs["稽核識別碼"], kwargs["建立時間"],
                kwargs.get("請求識別碼"),
            )
        except BaseException:
            return 版本配置提交判定.無法判定
        return {
            "committed": 版本配置提交判定.已提交,
            "not_committed": 版本配置提交判定.未提交,
            "unknown": 版本配置提交判定.無法判定,
        }[outcome]


class PostgreSQL目前版本解析器:
    __slots__ = ("_工作單元",)

    def __init__(self, 設定: 交易儲存設定) -> None:
        self._工作單元 = PostgreSQL工作單元(設定)

    def 依slug解析(self, slug: str) -> 已釘選版本:
        if type(slug) is not str or _slug格式.fullmatch(slug) is None:
            raise 目前版本解析錯誤("目前版本解析失敗") from None
        try:
            with self._工作單元.交易() as conn:
                row = conn.execute(
                    "SELECT e.id AS endpoint_id,e.service_account_id,e.status,v.id AS version_id,"
                    "v.version_number,v.original_requirement_text,v.system_prompt,v.allowed_skills,"
                    "v.allowed_tools,v.tool_schema_snapshot,v.tool_runtime_revision,"
                    "v.model_config_snapshot,v.retry_policy,v.skill_bundle_manifest,"
                    "v.input_schema,v.response_schema,v.schema_changed,v.created_by_user_id,v.created_at "
                    "FROM published_endpoints e JOIN published_endpoint_versions v "
                    "ON v.id=e.current_version_id AND v.endpoint_id=e.id "
                    "WHERE e.slug=%s AND e.status='active'", (slug,),
                ).fetchone()
                if row is None:
                    raise 目前版本不存在錯誤("目前版本不存在")
                snapshot = _snapshot_from_row(row, 5, created_by_index=17)
                payload = {name: getattr(snapshot, name) for name in snapshot.__dataclass_fields__}
                return 已釘選版本(
                    _get(row, "endpoint_id", 0), _get(row, "service_account_id", 1),
                    _get(row, "version_id", 3), _get(row, "version_number", 4),
                    bool(_get(row, "schema_changed", 16)), _epoch(_get(row, "created_at", 18)),
                    _正規JSON(payload),
                )
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 目前版本不存在錯誤):
            raise
        except BaseException:
            raise 目前版本解析錯誤("目前版本解析失敗") from None


PostgreSQL版本服務 = PostgreSQL版本配置服務
