from __future__ import annotations
from contextlib import contextmanager
from typing import Any
import json

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
def setup(monkeypatch,replies):
    c=Conn(replies)
    @contextmanager
    def tx(_): yield c
    monkeypatch.setattr(模組,"交易連線",tx); return c

def test_append_first只插入尚未保存尾端且鎖session(monkeypatch):
    # append transaction: locked session, MAX=0, INSERT, session UPDATE
    c=setup(monkeypatch, [[{"id":"s"}], [{"m":0}], [{"id":7}], []])
    repo=模組.PostgreSQL工作階段庫(設定)
    repo.寫入訊息清單("s", [{"role":"user","content":"old"},{"role":"assistant","content":"new"}])
    inserts=[(q,p) for q,p in c.calls if "INSERT INTO messages" in q]
    assert len(inserts)==1 and inserts[0][1][1]==1
    assert inserts[0][1][4].obj["content"]=="new"
    assert any("FOR UPDATE" in q for q,_ in c.calls)
    assert not any("DELETE FROM messages" in q for q,_ in c.calls)
    update=next((q,p) for q,p in c.calls if "message_count=message_count" in q)
    assert update[1][:2] == (1, 0)


def test_replace先停用並重置計數後從零插入精確工具增量(monkeypatch):
    messages=[{"role":"assistant","tool_calls":[{"id":"a"},{"id":"b"}]},
              {"role":"tool","tool_call_id":"a","content":"ok"}]
    c=setup(monkeypatch, [[{"id":"s"}], [], [], [{"id":10}], [{"id":11}], []])
    模組.PostgreSQL工作階段庫(設定).替換訊息清單("s", messages)
    sql=[q for q,_ in c.calls]
    assert "active=FALSE" in sql[1]
    assert "message_count=0" in sql[2] and "tool_call_count=0" in sql[2]
    inserts=[p for q,p in c.calls if "INSERT INTO messages" in q]
    assert [p[1] for p in inserts] == [0,1]
    counter=next(p for q,p in c.calls if "message_count=message_count" in q)
    assert counter[:2] == (2,3)

def test_read_messages還原canonical欄位(monkeypatch):
    row={"id":"m","role":"assistant","content":"ok","content_json":"{}","tool_calls":None,
         "tool_name":None,"observed":False}
    setup(monkeypatch, [[{"id":"s","user_id":"u"}], [row]])
    got=模組.PostgreSQL工作階段庫(設定).讀取訊息("s")
    assert got==[{"role":"assistant","content":"ok"}]
