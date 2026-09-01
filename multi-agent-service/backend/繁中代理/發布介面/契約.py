"""發布介面 InvokeEnvelope factory constructors。"""

from __future__ import annotations

from typing import Any

from .協定 import AuditEventSink
from .領域模型 import AuditActorRef
from .領域模型 import AuditAppendReceipt
from .領域模型 import AuditEvent
from .領域模型 import AuditMetadata
from .領域模型 import AuditResourceRef
from .領域模型 import EndpointRef
from .領域模型 import InvokeEnvelope
from .領域模型 import InvocationRef
from .領域模型 import PublishedError
from .領域模型 import PublishedUsage
from .領域模型 import PublishedWarning
from .領域模型 import _重建PublishedError


class AuditSinkError(RuntimeError):
    """稽核 sink 無法確認提交時使用的固定錯誤型別。"""


def 附加稽核事件或失敗關閉(
    sink: AuditEventSink,
    event: AuditEvent,
) -> AuditAppendReceipt:
    """附加稽核事件，無法確認提交時固定失敗關閉。

    參數:
        sink: 提供 append_audit_event 的稽核事件 sink。
        event: 要附加的 AuditEvent，必須是 AuditEvent exact type；成功時
            會先深層重建為 canonical event，再傳給 sink。
    回傳:
        重新建構且 committed=True、event_id 與 canonical event 相同的
        AuditAppendReceipt。
    例外:
        AuditSinkError: event 型別、event canonicalization、sink lookup/call、
        receipt 型別或提交狀態不符合契約。
    副作用:
        完全 canonicalize 成功後才呼叫 sink 一次，且傳入 canonical event；
        失敗時清除本函式 frame locals，
        且不保留原始例外鏈。
    """
    失敗 = False
    raw_event = event
    canonical_actor = None
    canonical_resource = None
    canonical_metadata = None
    canonical_event = None
    raw_actor = None
    raw_resource = None
    raw_metadata = None
    append_audit_event = None
    raw_receipt = None
    canonical_receipt = None
    try:
        if type(raw_event) is not AuditEvent:
            失敗 = True
        else:
            raw_actor = raw_event.actor
            raw_resource = raw_event.resource
            raw_metadata = raw_event.metadata
            if type(raw_actor) is not AuditActorRef:
                失敗 = True
            elif type(raw_resource) is not AuditResourceRef:
                失敗 = True
            elif type(raw_metadata) is not AuditMetadata:
                失敗 = True

        if not 失敗:
            assert type(raw_actor) is AuditActorRef
            assert type(raw_resource) is AuditResourceRef
            assert type(raw_metadata) is AuditMetadata
            canonical_actor = AuditActorRef(
                raw_actor.actor_type,
                raw_actor.actor_id,
            )
            canonical_resource = AuditResourceRef(
                raw_resource.resource_type,
                raw_resource.resource_id,
            )
            canonical_metadata = AuditMetadata(AuditMetadata.to_json(raw_metadata))
            canonical_event = AuditEvent(
                event_id=raw_event.event_id,
                occurred_at=raw_event.occurred_at,
                action=raw_event.action,
                outcome=raw_event.outcome,
                actor=canonical_actor,
                resource=canonical_resource,
                request_id=raw_event.request_id,
                endpoint_id=raw_event.endpoint_id,
                invocation_id=raw_event.invocation_id,
                metadata=canonical_metadata,
            )
            append_audit_event = sink.append_audit_event
            raw_receipt = append_audit_event(canonical_event)
            if type(raw_receipt) is not AuditAppendReceipt:
                失敗 = True
            else:
                canonical_receipt = AuditAppendReceipt(
                    raw_receipt.event_id,
                    raw_receipt.committed,
                    raw_receipt.sequence,
                )
                if not canonical_receipt.committed:
                    失敗 = True
                elif canonical_receipt.event_id != canonical_event.event_id:
                    失敗 = True
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del sink, event, raw_event, canonical_actor, canonical_resource
        del raw_actor, raw_resource, raw_metadata
        del canonical_metadata, canonical_event, append_audit_event
        del raw_receipt, canonical_receipt
        raise
    except BaseException:
        失敗 = True

    if 失敗 or type(canonical_receipt) is not AuditAppendReceipt:
        sink = event = raw_event = canonical_actor = canonical_resource = None
        raw_actor = raw_resource = raw_metadata = None
        canonical_metadata = canonical_event = append_audit_event = None
        raw_receipt = canonical_receipt = None
        raise AuditSinkError("稽核事件無法確認提交") from None

    result = canonical_receipt
    sink = event = raw_event = canonical_actor = canonical_resource = None
    raw_actor = raw_resource = raw_metadata = None
    canonical_metadata = canonical_event = append_audit_event = None
    raw_receipt = canonical_receipt = None
    assert type(result) is AuditAppendReceipt
    return result


def 建立成功信封(
    endpoint: EndpointRef,
    invocation: InvocationRef,
    data: Any,
    *,
    usage: PublishedUsage | None = None,
    warnings: tuple[PublishedWarning, ...] | list[PublishedWarning] | None = None,
) -> InvokeEnvelope:
    """建立公開成功呼叫信封。

    參數:
        endpoint: 公開端點版本參照，必須是 EndpointRef exact type。
        invocation: 公開呼叫紀錄參照，必須是 InvocationRef exact type。
        data: 成功回傳 JSON value，由領域模型建立 defensive snapshot。
        usage: 選用用量摘要，必須是 PublishedUsage exact type 或 None。
        warnings: 選用非致命警告集合，內容由領域模型驗證與正規化。
    回傳:
        ok 固定為 True 的 InvokeEnvelope。
    例外:
        ValueError: DTO 型別或成功信封 invariant 不符合公開契約。
        嚴格JSON錯誤: data 不是嚴格 JSON value。
    副作用:
        不修改輸入物件；失敗時清除本函式 frame locals 後重新拋出原例外。
    """
    try:
        _確認_exact_type(endpoint, EndpointRef)
        _確認_exact_type(invocation, InvocationRef)
        if usage is not None:
            _確認_exact_type(usage, PublishedUsage)
        return InvokeEnvelope(
            ok=True,
            endpoint=endpoint,
            invocation=invocation,
            data=data,
            usage=usage,
            warnings=warnings,
        )
    except Exception:
        endpoint = invocation = data = usage = warnings = None
        raise


def 建立失敗信封(
    error: PublishedError,
    *,
    endpoint: EndpointRef | None = None,
    invocation: InvocationRef | None = None,
    warnings: tuple[PublishedWarning, ...] | list[PublishedWarning] | None = None,
) -> InvokeEnvelope:
    """建立公開失敗呼叫信封。

    參數:
        error: 公開錯誤摘要，必須是 PublishedError exact type。
        endpoint: 非 endpoint_not_found 錯誤必填的 EndpointRef exact type。
        invocation: 非 endpoint_not_found 錯誤必填的 InvocationRef exact type。
        warnings: 選用非致命警告集合，內容由領域模型驗證與正規化。
    回傳:
        ok 固定為 False、data 與 usage 固定為 None 的 InvokeEnvelope。
    例外:
        ValueError: DTO 型別、R84/R93 參照規則或失敗信封 invariant 不符合公開契約。
    副作用:
        不修改輸入物件；失敗時清除本函式 frame locals 後重新拋出原例外。
    """
    正規錯誤 = None
    try:
        _確認_exact_type(error, PublishedError)
        正規錯誤 = _重建PublishedError(error)
        if 正規錯誤.code == "endpoint_not_found":
            if endpoint is not None or invocation is not None:
                raise ValueError("失敗信封參照不符合公開契約")
        else:
            _確認_exact_type(endpoint, EndpointRef)
            _確認_exact_type(invocation, InvocationRef)
        return InvokeEnvelope(
            ok=False,
            endpoint=endpoint,
            invocation=invocation,
            error=正規錯誤,
            warnings=warnings,
        )
    except Exception:
        del error
        正規錯誤 = endpoint = invocation = warnings = None
        raise


def _確認_exact_type(值: object, 型別: type[object]) -> None:
    """確認公開 DTO 使用 exact type，拒絕 subclass 擴張公開邊界。

    參數為待驗證值與允許型別；驗證成功不回傳資料，失敗時清除區域參照並拋出
    固定 ``ValueError``。除拋出例外外沒有外部副作用。
    """
    if type(值) is not 型別:
        值 = 型別 = None
        raise ValueError("公開 DTO 型別不符合契約")
