"""發布介面稽核 sink 協定。"""

from __future__ import annotations

from typing import Protocol

from .領域模型 import AuditAppendReceipt
from .領域模型 import AuditEvent


class AuditEventSink(Protocol):
    """接收並持久化公開稽核事件的協定。

    event 是 AuditEvent exact type；回傳 AuditAppendReceipt。持久化是外部副作用；
    caller 若無法取得 committed=True 且 event_id 相符的 receipt，必須 fail closed。
    """

    def append_audit_event(self, event: AuditEvent, /) -> AuditAppendReceipt:
        """附加單一稽核事件並回傳 append receipt。

        參數: event 為要持久化的 AuditEvent exact type。
        回傳: AuditAppendReceipt，表示事件是否已提交以及提交序號。
        副作用: 將 event 附加至持久稽核紀錄；不可靠時 caller 必須 fail closed。
        """
        ...
