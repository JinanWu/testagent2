"""PostgreSQL 五年保存候選與原子清除。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from 繁中代理.PostgreSQL連線 import 交易連線
from .保存期限 import (保存候選計畫, 保存清除結果, 保存候選規劃錯誤, 保存清除錯誤,
                       保存刪除順序, 五年保存期限, 已達五年保存期限)

class PostgreSQL保存候選規劃器:
    __slots__=("_設定",)
    def __init__(self, 凍結設定: object)->None:self._設定=凍結設定
    def 規劃(self, 現在epoch秒:int|float,/,*,候選上限:int=100,相依上限:int=4096)->tuple[保存候選計畫,...]:
        try:
            if type(候選上限) is not int or not 1<=候選上限<=1000 or type(相依上限) is not int or not 1<=相依上限<=10000:raise ValueError
            with 交易連線(self._設定) as 連線:
                根列=連線.execute(
                    "SELECT id,EXTRACT(EPOCH FROM created_at)::double precision AS created_at_epoch FROM endpoint_invocations "
                    "WHERE created_at + INTERVAL '5 years' <= to_timestamp(%s) ORDER BY created_at,id LIMIT %s",
                    (現在epoch秒,候選上限)).fetchall()
                結果=[]
                for 原列 in 根列:
                    呼叫ID,建立 = _正規列(原列, ("id", "created_at_epoch"))
                    if not 已達五年保存期限(建立,現在epoch秒):raise ValueError
                    結果.append(_計畫(連線,呼叫ID,五年保存期限(建立),相依上限))
                return tuple(結果)
        except (KeyboardInterrupt,SystemExit,GeneratorExit):raise
        except BaseException:raise 保存候選規劃錯誤("五年保存候選無法規劃") from None

class PostgreSQL保存清除服務:
    __slots__=("_設定",)
    def __init__(self,凍結設定:object)->None:self._設定=凍結設定
    def 清除(self,現在epoch秒:int|float,/,*,批次上限:int=100)->保存清除結果:
        """以 FOR UPDATE SKIP LOCKED 選根並依 FK 順序在單一交易刪除。"""
        try:
            if type(批次上限) is not int or not 1<=批次上限<=1000:raise ValueError
            with 交易連線(self._設定) as 連線:
                rows=連線.execute(
                    "SELECT id FROM endpoint_invocations WHERE created_at + INTERVAL '5 years' <= to_timestamp(%s) "
                    "AND NOT EXISTS (SELECT 1 FROM endpoint_redactions r WHERE r.invocation_id=endpoint_invocations.id) "
                    "AND NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.invocation_id=endpoint_invocations.id) "
                    "ORDER BY created_at,id LIMIT %s FOR UPDATE SKIP LOCKED",(現在epoch秒,批次上限)).fetchall()
                ids=tuple(_正規列(r, ("id",))[0] for r in rows)
                if not ids:return 保存清除結果(0,0,0,0,0)
                counts={}
                for 表 in ("invocation_sensitive_hits","endpoint_tool_calls","run_events"):
                    cur=連線.execute(f"DELETE FROM {表} WHERE invocation_id = ANY(%s)",(list(ids),));counts[表]=cur.rowcount
                cur=連線.execute("DELETE FROM endpoint_invocations WHERE id = ANY(%s)",(list(ids),))
                if cur.rowcount!=len(ids):raise ValueError
                return 保存清除結果(cur.rowcount,counts["run_events"],counts["endpoint_tool_calls"],0,0)
        except (KeyboardInterrupt,SystemExit,GeneratorExit):raise
        except BaseException:raise 保存清除錯誤("五年保存資料無法清除") from None

def _ids(連線:Any,表:str,inv:str,上限:int)->tuple[str,...]:
    rows=連線.execute(f"SELECT id FROM {表} WHERE invocation_id=%s ORDER BY id LIMIT %s",(inv,上限+1)).fetchall()
    if len(rows)>上限:raise ValueError
    return tuple(_正規列(r, ("id",))[0] for r in rows)

def _計畫(連線:Any,inv:str,期限:float,上限:int)->保存候選計畫:
    e=_ids(連線,"run_events",inv,上限);上限-=len(e)
    t=_ids(連線,"endpoint_tool_calls",inv,上限);上限-=len(t)
    rrows=連線.execute("SELECT id,target_type FROM endpoint_redactions WHERE invocation_id=%s ORDER BY id LIMIT %s",(inv,上限+1)).fetchall()
    if len(rrows)>上限:raise ValueError
    rrows=tuple(_正規列(x, ("id", "target_type")) for x in rrows)
    r=tuple(x[0] for x in rrows);上限-=len(r)
    a=_ids(連線,"audit_events",inv,上限)
    阻擋=[]
    if r:阻擋.append("endpoint_redactions_no_delete")
    if a:阻擋.append("audit_events_no_delete")
    if any(x[1]=="run_event" for x in rrows):阻擋.append("redacted_run_event_no_delete")
    if any(x[1].startswith("tool_") for x in rrows):阻擋.append("redacted_tool_call_no_delete")
    return 保存候選計畫(inv,期限,e,t,r,a,len(e),len(t),len(r),len(a),保存刪除順序,tuple(阻擋))


def _正規列(列: object, 欄名: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(列, Mapping):
        if set(列) != set(欄名): raise ValueError
        return tuple(列[名稱] for 名稱 in 欄名)
    if type(列) is tuple and len(列) == len(欄名): return 列
    raise ValueError
