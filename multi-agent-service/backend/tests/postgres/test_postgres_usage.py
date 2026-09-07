from __future__ import annotations
from contextlib import contextmanager
from typing import Any

from 繁中代理.交易儲存設定 import 交易儲存設定
import 繁中代理.PostgreSQL工作階段庫 as 模組

設定=交易儲存設定("postgres","postgresql:///db?host=/cloudsql/proj:region:inst","proj:region:inst")
class Cursor:
    def __init__(self, rows=()): self.rows=list(rows)
    def fetchone(self): return self.rows.pop(0) if self.rows else None
    def fetchall(self): return list(self.rows)
class Conn:
    def __init__(self,replies): self.replies=list(replies); self.calls=[]
    def execute(self,query: str,params: Any=None,*,prepare=None,binary=None):
        self.calls.append((query,params)); return Cursor(self.replies.pop(0) if self.replies else ())

def test_usage每次呼叫append事件並同交易更新session精確累計(monkeypatch):
    conn=Conn([[{"user_id":"u","model":"gemini-2.5-flash-lite","billing_provider":"gemini-adc"}], []])
    @contextmanager
    def tx(_): yield conn
    monkeypatch.setattr(模組,"交易連線",tx)
    模組.PostgreSQL工作階段庫(設定).更新模型使用量("s",{
        "prompt_tokens":100,"completion_tokens":20,"cached_content_token_count":3,
        "thoughts_token_count":4,
    })
    inserts=[(q,p) for q,p in conn.calls if "INSERT INTO session_usage_events" in q]
    assert len(inserts)==1
    params=inserts[0][1]
    assert params[1:3]==("s","u") and params[5:11]==(100,100,20,3,0,4)
    update=next((q,p) for q,p in conn.calls if "UPDATE sessions SET input_tokens" in q)
    assert update[1][:6] == (100,20,3,0,4,1)
    assert update[1][7:9] == ("gemini-adc","local-pricing-v1")


def test_usage零呼叫增量不寫事件或aggregate且負數fail_closed(monkeypatch):
    conn=Conn([[{"user_id":"u","model":"m","billing_provider":"p"}]])
    @contextmanager
    def tx(_): yield conn
    monkeypatch.setattr(模組,"交易連線",tx)
    repo=模組.PostgreSQL工作階段庫(設定)
    repo.更新模型使用量("s", {"input_tokens":2}, api呼叫增量=0)
    assert len(conn.calls)==1
    import pytest
    with pytest.raises(ValueError): repo.更新模型使用量("s", {"input_tokens":-1})
