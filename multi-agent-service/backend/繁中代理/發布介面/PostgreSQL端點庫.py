"""Published endpoint、v1、service account 與 initial credential 的 PostgreSQL 實作。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb

from ..環境設定 import 交易儲存設定
from .PostgreSQL工作單元 import PostgreSQL工作單元
from .規劃.端點發布 import (
    已準備初始憑證, 已準備發布識別, 發布版本快照, 端點發布結果, 端點發布輸入錯誤,
    端點發布錯誤, 端點發布衝突, 端點發布耐久性未知, _發布前驗證,
    _呼叫發布callbacks, _正規JSON, _驗證預配識別,
)
from .規劃.綱要 import 規劃草稿
from .技能套件.CloudStorage權威 import (
    CloudStorage套件發布收據, 解析CloudStorage清單參照,
)


def _值(列: Any, 名稱: str, 索引: int = 0) -> Any:
    """只接受共用連線池承諾的 dict_row，禁止位置式欄位猜測。"""
    del 索引
    if not isinstance(列, dict) or 名稱 not in 列:
        raise TypeError("PostgreSQL row shape invalid")
    return 列[名稱]


def _唯一衝突(錯誤: BaseException) -> bool:
    """只把 PostgreSQL unique violation 映射為公開 slug conflict。"""
    seen: set[int] = set()
    current: BaseException | None = 錯誤
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "sqlstate", None) == "23505" or getattr(current, "pgcode", None) == "23505":
            return True
        current = current.__cause__ or current.__context__
    return False


def _時間戳(epoch秒: float) -> datetime:
    """將領域 epoch 秒轉成 PostgreSQL timestamptz 所需的 aware UTC datetime。"""
    return datetime.fromtimestamp(float(epoch秒), timezone.utc)


def _JSONB(value: Any) -> Jsonb:
    """以 psycopg Jsonb adapter 傳送 canonical JSON，而非依賴 text 隱式轉型。"""
    return Jsonb(json.loads(_正規JSON(value)))


def _驗證CloudStorage預配關係(
    識別碼: tuple[Any, ...], 快照: 發布版本快照,
    收據: CloudStorage套件發布收據, 請求識別碼: str | None,
) -> CloudStorage套件發布收據:
    """重建並驗證generation-pinned GCS收據與初始發布圖形的關係。"""
    if type(收據) is not CloudStorage套件發布收據:
        raise 端點發布輸入錯誤("端點發布輸入無效") from None
    if 請求識別碼 is not None and (
        type(請求識別碼) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", 請求識別碼) is None
    ):
        raise 端點發布輸入錯誤("端點發布輸入無效") from None
    try:
        欄位 = tuple(
            object.__getattribute__(收據, 名稱)
            for 名稱 in CloudStorage套件發布收據.__dataclass_fields__
        )
        副本 = CloudStorage套件發布收據(*欄位)
        物件鍵, 世代 = 解析CloudStorage清單參照(副本.清單參照)
        清單 = object.__getattribute__(快照, "skill_bundle_manifest")
        合法 = (
            type(清單) is dict
            and len(識別碼) == 7
            and 副本.套件識別碼 == 識別碼[4]
            and 清單.get("bundle_id") == 識別碼[4]
            and 清單.get("manifest_reference") == 副本.清單參照
            and 清單.get("manifest_digest") == 副本.清單摘要
            and 清單.get("sha256") == 副本.套件雜湊
            and type(副本.bucket) is str and bool(副本.bucket)
            and 物件鍵 == 副本.object_key
            and 世代 == 副本.generation
            and re.fullmatch(r"[0-9a-f]{64}", 副本.清單摘要) is not None
            and re.fullmatch(r"[0-9a-f]{64}", 副本.套件雜湊) is not None
            and type(副本.總位元組數) is int
            and 0 <= 副本.總位元組數 <= 6 * 1024 * 1024
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise 端點發布輸入錯誤("端點發布輸入無效") from None
    if not 合法:
        raise 端點發布輸入錯誤("端點發布輸入無效") from None
    return 副本


class PostgreSQL端點庫:
    """單一 PostgreSQL transaction 建立不可分割 published endpoint graph。"""

    __slots__ = ("_工作單元", "_識別工廠", "_時鐘")

    def __init__(
        self, 設定: 交易儲存設定, endpoint_id_factory: Callable[[], str],
        version_id_factory: Callable[[], str], credential_id_factory: Callable[[], str],
        service_account_id_factory: Callable[[], str], clock: Callable[[], float],
    ) -> None:
        if not all(callable(x) for x in (
            endpoint_id_factory, version_id_factory, credential_id_factory,
            service_account_id_factory, clock,
        )):
            raise 端點發布錯誤("端點發布失敗") from None
        self._工作單元 = PostgreSQL工作單元(設定)
        self._識別工廠 = (
            endpoint_id_factory, version_id_factory, credential_id_factory,
            service_account_id_factory,
        )
        self._時鐘 = clock

    def 發布(
        self, owner_user_id: str, draft: 規劃草稿, version_snapshot: 發布版本快照,
        prepared_credential: 已準備初始憑證, now: float,
    ) -> 端點發布結果:
        """重用 SQLite DTO preflight，以 row lock 與 unique constraint 建立固定 v1。"""
        try:
            草稿, 快照, 憑證, 確認 = _發布前驗證(
                owner_user_id, draft, version_snapshot, prepared_credential, now,
            )
            ids = _呼叫發布callbacks(self._識別工廠, self._時鐘)
            endpoint_id, version_id, credential_id, account_id, created_at = ids
            建立時間 = _時間戳(created_at)
            with self._工作單元.交易() as 連線:
                # PostgreSQL 無法鎖不存在的 key；advisory xact lock 將相同 slug 串行化，
                # unique constraint 仍是最終 authority。
                連線.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (確認.slug,))
                if 連線.execute(
                    "SELECT id FROM published_endpoints WHERE slug=%s FOR UPDATE", (確認.slug,),
                ).fetchone() is not None:
                    raise 端點發布衝突("端點發布失敗")
                連線.execute(
                    "INSERT INTO service_accounts(id,owner_user_id,created_at,disabled_at) "
                    "VALUES(%s,%s,%s,NULL)",
                    (account_id, owner_user_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_endpoints("
                    "id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
                    "VALUES(%s,%s,%s,%s,'active',NULL,%s,%s)",
                    (endpoint_id, owner_user_id, account_id, 確認.slug, 建立時間, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_endpoint_versions("
                    "id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills,"
                    "allowed_tools,tool_schema_snapshot,tool_runtime_revision,model_config_snapshot,"
                    "retry_policy,skill_bundle_manifest,input_schema,response_schema,"
                    "schema_changed,created_by_user_id,created_at) "
                    "VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s)",
                    (version_id, endpoint_id, 快照.original_requirement_text, 快照.system_prompt,
                     _JSONB(快照.allowed_skills), _JSONB(快照.allowed_tools),
                     _JSONB(快照.tool_schema_snapshot), 快照.tool_runtime_revision,
                     _JSONB(快照.model_config_snapshot), _JSONB(快照.retry_policy),
                     _JSONB(快照.skill_bundle_manifest),
                     None if 快照.input_schema is None else _JSONB(快照.input_schema),
                     _JSONB(快照.response_schema), 快照.created_by_user_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_draft_consumptions(draft_id,endpoint_id,consumed_at) VALUES(%s,%s,%s)",
                    (草稿.草稿識別碼, endpoint_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_endpoint_version_metadata("
                    "version_id,publication_source,prompt_changed,skills_changed,tools_changed,model_changed,docs_changed) "
                    "VALUES(%s,'initial_draft',FALSE,FALSE,FALSE,FALSE,FALSE)", (version_id,),
                )
                連線.execute(
                    "INSERT INTO endpoint_credentials("
                    "id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,"
                    "expires_at,last_used_at,revoked_at,inactive_disabled_at,ip_allowlist,rate_limit_requests,"
                    "rate_limit_window_seconds,created_by_user_id,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,%s,%s,%s,%s)",
                    (credential_id, endpoint_id, 憑證.name, 憑證.purpose, 憑證.key_version,
                     bytes(憑證.key_nonce), bytes(憑證.key_ciphertext), 憑證.key_hash, 憑證.key_prefix,
                     憑證.key_last4, _時間戳(憑證.expires_at), _JSONB(憑證.ip_allowlist),
                     憑證.rate_limit_requests, 確認.window_seconds, 憑證.created_by_user_id, 建立時間),
                )
                cursor = 連線.execute(
                    "UPDATE published_endpoints SET current_version_id=%s "
                    "WHERE id=%s AND owner_user_id=%s AND current_version_id IS NULL",
                    (version_id, endpoint_id, owner_user_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError
            return 端點發布結果(endpoint_id, version_id, credential_id, account_id)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except 端點發布輸入錯誤:
            raise
        except 端點發布衝突:
            raise
        except BaseException as error:
            if _唯一衝突(error):
                raise 端點發布衝突("端點發布失敗") from None
            raise 端點發布錯誤("端點發布失敗") from None

    建立 = 發布

    def 發布已準備圖形(
        self, owner_user_id: str, draft: 規劃草稿, version_snapshot: 發布版本快照,
        prepared_credential: 已準備初始憑證, prepared_ids: 已準備發布識別,
        bundle_receipt: Any, *, 請求識別碼: str | None = None,
        寫入前權威確認: Callable[[], Any] | None = None,
    ) -> 端點發布結果:
        """鎖後確認 authority，並在唯一 transaction 寫入完整初始發布圖形。"""
        寫入完成 = False
        try:
            ids = _驗證預配識別(prepared_ids)
            草稿, 快照, 憑證, 確認 = _發布前驗證(
                owner_user_id, draft, version_snapshot, prepared_credential, ids[6],
            )
            收據 = _驗證CloudStorage預配關係(ids, 快照, bundle_receipt, 請求識別碼)
            if 寫入前權威確認 is not None and not callable(寫入前權威確認):
                raise 端點發布輸入錯誤("端點發布輸入無效")
            endpoint_id, version_id, credential_id, account_id, bundle_id, audit_id, created_at = ids
            建立時間 = _時間戳(created_at)
            metadata = {
                "version_id": version_id, "version_number": 1,
                "bundle_id": bundle_id, "bundle_hash": 收據.套件雜湊,
                "credential_id": credential_id, "service_account_id": account_id,
            }
            with self._工作單元.交易() as 連線:
                連線.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (確認.slug,))
                if 連線.execute(
                    "SELECT id FROM published_endpoints WHERE slug=%s FOR UPDATE", (確認.slug,),
                ).fetchone() is not None:
                    raise 端點發布衝突("端點發布失敗")
                # callback 必須在 transaction lock 後且任何 INSERT 前。
                if 寫入前權威確認 is not None:
                    寫入前權威確認()
                連線.execute(
                    "INSERT INTO service_accounts(id,owner_user_id,created_at,disabled_at) VALUES(%s,%s,%s,NULL)",
                    (account_id, owner_user_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,"
                    "rate_limit_requests,rate_limit_window_seconds,created_at,updated_at) "
                    "VALUES(%s,%s,%s,%s,'active',NULL,%s,%s,%s,%s)",
                    (endpoint_id, owner_user_id, account_id, 確認.slug, 確認.endpoint_limit,
                     確認.window_seconds, 建立時間, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,"
                    "system_prompt,allowed_skills,allowed_tools,tool_schema_snapshot,tool_runtime_revision,"
                    "model_config_snapshot,retry_policy,skill_bundle_manifest,input_schema,response_schema,"
                    "schema_changed,created_by_user_id,created_at) "
                    "VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s)",
                    (version_id, endpoint_id, 快照.original_requirement_text, 快照.system_prompt,
                     _JSONB(快照.allowed_skills), _JSONB(快照.allowed_tools), _JSONB(快照.tool_schema_snapshot),
                     快照.tool_runtime_revision, _JSONB(快照.model_config_snapshot), _JSONB(快照.retry_policy),
                     _JSONB(快照.skill_bundle_manifest), None if 快照.input_schema is None else _JSONB(快照.input_schema),
                     _JSONB(快照.response_schema), 快照.created_by_user_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_draft_consumptions(draft_id,endpoint_id,consumed_at) VALUES(%s,%s,%s)",
                    (草稿.草稿識別碼, endpoint_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_endpoint_version_metadata(version_id,publication_source,prompt_changed,"
                    "skills_changed,tools_changed,model_changed,docs_changed) "
                    "VALUES(%s,'initial_draft',FALSE,FALSE,FALSE,FALSE,FALSE)", (version_id,),
                )
                連線.execute(
                    "INSERT INTO endpoint_credentials(id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,"
                    "key_hash,key_prefix,key_last4,expires_at,last_used_at,revoked_at,inactive_disabled_at,ip_allowlist,"
                    "rate_limit_requests,rate_limit_window_seconds,created_by_user_id,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,%s,%s,%s,%s)",
                    (credential_id, endpoint_id, 憑證.name, 憑證.purpose, 憑證.key_version,
                     bytes(憑證.key_nonce), bytes(憑證.key_ciphertext), 憑證.key_hash, 憑證.key_prefix,
                     憑證.key_last4, _時間戳(憑證.expires_at), _JSONB(憑證.ip_allowlist),
                     憑證.rate_limit_requests, 確認.window_seconds, 憑證.created_by_user_id, 建立時間),
                )
                連線.execute(
                    "INSERT INTO published_skill_bundles(bundle_id,version_id,manifest_reference,manifest_digest,"
                    "bundle_hash,total_bytes,state,published_at) VALUES(%s,%s,%s,%s,%s,%s,'published',%s)",
                    (bundle_id, version_id, 收據.清單參照, 收據.清單摘要, 收據.套件雜湊,
                     收據.總位元組數, 建立時間),
                )
                連線.execute(
                    "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,"
                    "resource_id,request_id,endpoint_id,invocation_id,metadata,created_at) "
                    "VALUES(%s,%s,%s,'endpoint_published','success','user',%s,'published_endpoint',%s,%s,%s,NULL,%s,%s)",
                    (audit_id, audit_id, 建立時間, owner_user_id, endpoint_id, 請求識別碼,
                     endpoint_id, _JSONB(metadata), 建立時間),
                )
                updated = 連線.execute(
                    "UPDATE published_endpoints SET current_version_id=%s WHERE id=%s AND owner_user_id=%s "
                    "AND current_version_id IS NULL RETURNING current_version_id",
                    (version_id, endpoint_id, owner_user_id),
                ).fetchone()
                if updated is None or _值(updated, "current_version_id") != version_id:
                    raise RuntimeError
                寫入完成 = True
            return 端點發布結果(endpoint_id, version_id, credential_id, account_id)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except (端點發布輸入錯誤, 端點發布衝突):
            raise
        except BaseException as error:
            if 寫入完成:
                判定 = self._判定初始提交(
                    owner_user_id, 草稿, 快照, 憑證, 確認, ids, 收據, 請求識別碼, metadata,
                )
                if 判定 == "committed":
                    return 端點發布結果(endpoint_id, version_id, credential_id, account_id)
                if 判定 == "unknown":
                    raise 端點發布耐久性未知("端點發布耐久性未知") from None
            if _唯一衝突(error):
                raise 端點發布衝突("端點發布失敗") from None
            raise 端點發布錯誤("端點發布失敗") from None

    def _判定初始提交(self, owner: str, 草稿: Any, 快照: Any, 憑證: Any,
                       確認: Any, ids: tuple[Any, ...], 收據: Any,
                       request_id: str | None, metadata: dict[str, Any]) -> str:
        """用 fresh transaction 對八表與 current pointer 做 exact 三態判定。"""
        endpoint_id, version_id, credential_id, account_id, bundle_id, audit_id, created_at = ids
        ts = _時間戳(created_at)
        try:
            with self._工作單元.交易() as conn:
                row = conn.execute(
                    "SELECT ("
                    "EXISTS(SELECT 1 FROM service_accounts WHERE id=%s AND owner_user_id=%s AND created_at=%s AND disabled_at IS NULL) AND "
                    "EXISTS(SELECT 1 FROM published_endpoints WHERE id=%s AND owner_user_id=%s AND service_account_id=%s AND slug=%s AND status='active' AND current_version_id=%s AND rate_limit_requests=%s AND rate_limit_window_seconds=%s AND created_at=%s AND updated_at=%s) AND "
                    "EXISTS(SELECT 1 FROM published_endpoint_versions WHERE id=%s AND endpoint_id=%s AND version_number=1 AND original_requirement_text=%s AND system_prompt=%s AND allowed_skills=%s::jsonb AND allowed_tools=%s::jsonb AND tool_schema_snapshot=%s::jsonb AND tool_runtime_revision=%s AND model_config_snapshot=%s::jsonb AND retry_policy=%s::jsonb AND skill_bundle_manifest=%s::jsonb AND input_schema IS NOT DISTINCT FROM %s::jsonb AND response_schema=%s::jsonb AND schema_changed=FALSE AND created_by_user_id=%s AND created_at=%s) AND "
                    "EXISTS(SELECT 1 FROM published_draft_consumptions WHERE draft_id=%s AND endpoint_id=%s AND consumed_at=%s) AND "
                    "EXISTS(SELECT 1 FROM published_endpoint_version_metadata WHERE version_id=%s AND publication_source='initial_draft' AND NOT prompt_changed AND NOT skills_changed AND NOT tools_changed AND NOT model_changed AND NOT docs_changed) AND "
                    "EXISTS(SELECT 1 FROM endpoint_credentials WHERE id=%s AND endpoint_id=%s AND name=%s AND purpose=%s AND key_version=%s AND key_nonce=%s AND key_ciphertext=%s AND key_hash=%s AND key_prefix=%s AND key_last4=%s AND expires_at=%s AND last_used_at IS NULL AND revoked_at IS NULL AND inactive_disabled_at IS NULL AND ip_allowlist=%s::jsonb AND rate_limit_requests=%s AND rate_limit_window_seconds=%s AND created_by_user_id=%s AND created_at=%s) AND "
                    "EXISTS(SELECT 1 FROM published_skill_bundles WHERE bundle_id=%s AND version_id=%s AND manifest_reference=%s AND manifest_digest=%s AND bundle_hash=%s AND total_bytes=%s AND state='published' AND published_at=%s AND reconciled_at IS NULL) AND "
                    "EXISTS(SELECT 1 FROM audit_events WHERE id=%s AND event_id=%s AND occurred_at=%s AND action='endpoint_published' AND outcome='success' AND actor_type='user' AND actor_id=%s AND resource_type='published_endpoint' AND resource_id=%s AND request_id IS NOT DISTINCT FROM %s AND endpoint_id=%s AND invocation_id IS NULL AND metadata=%s::jsonb AND created_at=%s)"
                    ") AS graph_matches, ("
                    "EXISTS(SELECT 1 FROM service_accounts WHERE id=%s) OR EXISTS(SELECT 1 FROM published_endpoints WHERE id=%s OR slug=%s) OR "
                    "EXISTS(SELECT 1 FROM published_endpoint_versions WHERE id=%s) OR EXISTS(SELECT 1 FROM endpoint_credentials WHERE id=%s) OR "
                    "EXISTS(SELECT 1 FROM published_draft_consumptions WHERE draft_id=%s) OR EXISTS(SELECT 1 FROM published_skill_bundles WHERE bundle_id=%s) OR EXISTS(SELECT 1 FROM audit_events WHERE id=%s)"
                    ") AS any_candidate",
                    (account_id, owner, ts, endpoint_id, owner, account_id, 確認.slug, version_id,
                     確認.endpoint_limit, 確認.window_seconds, ts, ts, version_id, endpoint_id,
                     快照.original_requirement_text, 快照.system_prompt, _正規JSON(快照.allowed_skills),
                     _正規JSON(快照.allowed_tools), _正規JSON(快照.tool_schema_snapshot),
                     快照.tool_runtime_revision, _正規JSON(快照.model_config_snapshot),
                     _正規JSON(快照.retry_policy), _正規JSON(快照.skill_bundle_manifest),
                     None if 快照.input_schema is None else _正規JSON(快照.input_schema),
                     _正規JSON(快照.response_schema), 快照.created_by_user_id, ts,
                     草稿.草稿識別碼, endpoint_id, ts, version_id,
                     credential_id, endpoint_id, 憑證.name, 憑證.purpose, 憑證.key_version,
                     bytes(憑證.key_nonce), bytes(憑證.key_ciphertext), 憑證.key_hash,
                     憑證.key_prefix, 憑證.key_last4, _時間戳(憑證.expires_at),
                     _正規JSON(憑證.ip_allowlist), 憑證.rate_limit_requests, 確認.window_seconds,
                     憑證.created_by_user_id, ts, bundle_id, version_id, 收據.清單參照,
                     收據.清單摘要, 收據.套件雜湊, 收據.總位元組數, ts,
                     audit_id, audit_id, ts, owner, endpoint_id, request_id, endpoint_id,
                     _正規JSON(metadata), ts, account_id, endpoint_id, 確認.slug, version_id,
                     credential_id, 草稿.草稿識別碼, bundle_id, audit_id),
                ).fetchone()
            if _值(row, "graph_matches") is True:
                return "committed"
            if _值(row, "any_candidate") is False:
                return "not_committed"
            return "unknown"
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            return "unknown"


PostgreSQL端點發布服務 = PostgreSQL端點庫
