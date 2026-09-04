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
def setup(monkeypatch,replies):
    c=Conn(replies)
    @contextmanager
    def tx(_): yield c
    monkeypatch.setattr(模組,"交易連線",tx); return c

def test_lease以原子upsert與holder條件提供安全接管(monkeypatch):
    c=setup(monkeypatch, [[{"holder":"h"}]])
    assert 模組.PostgreSQL工作階段庫(設定).取得壓縮鎖("s","h",30)=="h"
    sql=c.calls[0][0]
    assert "ON CONFLICT (session_id) DO UPDATE" in sql
    assert "compression_leases.expires_at < %s" in sql
    assert "compression_leases.holder=EXCLUDED.holder" in sql
    assert "RETURNING holder" in sql

def test_release具holder_fencing且context只在取得後釋放(monkeypatch):
    c=setup(monkeypatch, [[{"holder":"h"}], []])
    repo=模組.PostgreSQL工作階段庫(設定)
    monkeypatch.setattr(repo, "建立壓縮鎖Holder", lambda agent標籤=None: "h")
    with repo.壓縮鎖("s") as acquired: assert acquired
    delete=[(q,p) for q,p in c.calls if "DELETE FROM compression_leases" in q]
    assert len(delete)==1 and delete[0][1][1]=="h"
    assert "holder=%s" in delete[0][0]

def test_lineage使用recursive_cte且compression以parent_row_lock序列化(monkeypatch):
    c=setup(monkeypatch, [[{"ids":["root","tip"]}], [{"id":"tip"}]])
    repo=模組.PostgreSQL工作階段庫(設定)
    assert repo.取得工作階段譜系("tip")==["root","tip"]
    assert repo.取得壓縮Tip("root")=="tip"
    assert all("WITH RECURSIVE" in q for q,_ in c.calls)

    parent={"id":"root","source":"cli","compression_count":0}
    c=setup(monkeypatch, [[parent], [], [], []])
    child=repo.建立壓縮後工作階段("root",[],"system")
    assert child.startswith("session-")
    assert "FOR UPDATE" in c.calls[0][0]
    assert any("end_reason='compression'" in q for q,_ in c.calls)
