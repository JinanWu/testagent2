"""PostgreSQL append-only 稽核事件儲存庫。"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from 繁中代理.PostgreSQL連線 import 交易連線
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from ..領域模型 import AuditAppendReceipt, AuditEvent
from .稽核 import _建立canonical列, _讀取時鐘


class PostgreSQL稽核服務:
    """鏡射 SQLite DTO/錯誤，寫入現行 ``audit_events``。"""
    __slots__=("_設定","_時鐘")
    def __init__(self, 凍結設定: object, *, 時鐘: Callable[[],float]=time.time) -> None:
        if not callable(時鐘): raise ValueError("稽核服務設定無效") from None
        self._設定=凍結設定; self._時鐘=時鐘

    def 附加稽核事件(self, 事件: AuditEvent, /) -> AuditAppendReceipt:
        try:
            列=_建立canonical列(事件)
            created_at=_讀取時鐘(self._時鐘)
            with 交易連線(self._設定) as 連線:
                游標=連線.execute(
                    "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
                    "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata,created_at) "
                    "VALUES (%s,%s,to_timestamp(%s),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,to_timestamp(%s)) "
                    "ON CONFLICT (event_id) DO NOTHING RETURNING (xmin::text)::bigint AS durable_sequence",
                    列+(created_at,))
                if getattr(游標,"rowcount",None)==0:
                    已有=連線.execute(
                        "SELECT id,event_id,EXTRACT(EPOCH FROM occurred_at)::double precision AS occurred_at_epoch,action,outcome,actor_type,actor_id,resource_type,"
                        "resource_id,request_id,endpoint_id,invocation_id,metadata,(xmin::text)::bigint AS durable_sequence FROM audit_events "
                        "WHERE event_id=%s",(列[1],)).fetchone()
                    if 已有 is not None:
                        已有 = _正規列(已有, ("id","event_id","occurred_at_epoch","action","outcome","actor_type",
                            "actor_id","resource_type","resource_id","request_id","endpoint_id","invocation_id",
                            "metadata","durable_sequence"))
                    if (已有 is None
                            or tuple(已有[:12])+(建立正規JSON(解析嚴格JSON(已有[12]) if type(已有[12]) is str else 已有[12]),) != 列): raise ValueError
                    序號=已有[-1]
                else:
                    新增=游標.fetchone()
                    if 新增 is None:raise ValueError
                    新增 = _正規列(新增, ("durable_sequence",))
                    序號=新增[0]
                if type(序號) is not int or not 1<=序號<=2**63-1:raise ValueError
            return AuditAppendReceipt(事件.event_id, True, 序號)
        except (KeyboardInterrupt,SystemExit,GeneratorExit): raise
        except BaseException:
            from ..契約 import AuditSinkError
            raise AuditSinkError("稽核事件無法確認提交") from None

    append_audit_event=附加稽核事件


def _正規列(列: object, 欄名: tuple[str, ...]) -> tuple[object, ...]:
    if isinstance(列, Mapping):
        if set(列) != set(欄名): raise ValueError
        return tuple(列[名稱] for 名稱 in 欄名)
    if type(列) is tuple and len(列) == len(欄名): return 列
    raise ValueError
