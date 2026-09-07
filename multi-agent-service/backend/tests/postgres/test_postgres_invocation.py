"""PostgreSQL governance repositories 的 causal fake-psycopg 契約測試。"""
from __future__ import annotations

from contextlib import contextmanager
import ast
import hashlib
import json
from pathlib import Path
import re

import pytest

from 繁中代理.發布介面.呼叫 import PostgreSQL儲存庫 as 呼叫模組
from 繁中代理.發布介面.呼叫 import PostgreSQL冪等 as 冪等模組
from 繁中代理.發布介面.呼叫 import PostgreSQL限流 as 限流模組
from 繁中代理.發布介面.呼叫.儲存庫 import 呼叫儲存錯誤
from 繁中代理.發布介面.呼叫.限流 import 限流計數錯誤
from 繁中代理.發布介面.治理 import PostgreSQL稽核 as 稽核模組
from 繁中代理.發布介面.治理 import PostgreSQL遮蔽 as 遮蔽模組
from 繁中代理.發布介面.治理 import PostgreSQL保存期限 as 保存模組
from 繁中代理.發布介面.治理 import PostgreSQL查詢投影 as 投影模組
from 繁中代理.發布介面.治理.保存期限 import 保存候選規劃錯誤, 保存清除錯誤
from 繁中代理.發布介面.治理.管理查詢契約 import (
    ADMIN_INVOCATION_DETAIL_FIELDS, 管理員呼叫查詢條件, 管理員呼叫游標位置,
    管理員呼叫查詢錯誤, 查詢投影錯誤,
)
from 繁中代理.發布介面.治理.遮蔽 import 不可逆遮蔽錯誤
from 繁中代理.發布介面.領域模型 import (
    AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef,
)
from 繁中代理.發布介面.契約 import AuditSinkError


class 結果:
    """實作 psycopg Cursor 於本候選使用的 exact surface。"""
    def __init__(self, rows=(), *, rowcount=None):
        self._rows = list(rows)
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


def dict列(欄名, *值):
    """模擬正式 pool ``dict_row``，不可依賴 Mapping iteration 順序。"""
    assert len(欄名) == len(值)
    return dict(zip(欄名, 值, strict=True))


class 連線:
    """依序回應 SQL；每次 execute 都保存 immutable params 快照。"""
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def execute(self, sql, params=()):
        assert type(sql) is str and "?" not in sql
        assert type(params) in (tuple, list)
        self.calls.append((sql, tuple(params)))
        if not self.results:
            raise AssertionError(f"未預期 SQL: {sql}")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def 安裝交易(monkeypatch, 模組, connection):
    state = {"entered": 0, "exited": 0, "settings": []}

    @contextmanager
    def tx(settings):
        state["entered"] += 1
        state["settings"].append(settings)
        try:
            yield connection
        finally:
            state["exited"] += 1

    monkeypatch.setattr(模組, "交易連線", tx)
    return state


def _切頂層逗號(text: str) -> list[str]:
    parts, start, depth, quoted = [], 0, 0, False
    for index, char in enumerate(text):
        if char == "'": quoted = not quoted
        elif not quoted and char == "(": depth += 1
        elif not quoted and char == ")": depth -= 1
        elif not quoted and char == "," and depth == 0:
            parts.append(text[start:index].strip()); start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _載入0001清冊() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    migration = (Path(__file__).resolve().parents[2] /
                 "繁中代理/postgres_migrations/versions/0001_full_product_schema.py")
    assert migration.is_file(), f"canonical 0001 migration 不存在: {migration}"
    tree = ast.parse(migration.read_text(encoding="utf-8"))
    table_ddls = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DDL" for t in node.targets):
            table_ddls = ast.literal_eval(node.value)
            break
    assert type(table_ddls) is list
    columns: dict[str, dict[str, str]] = {}
    raw: dict[str, str] = {}
    for ddl in table_ddls:
        match = re.match(r"CREATE TABLE (\w+) \((.*)\)\Z", ddl.strip(), re.S)
        if match is None:
            continue
        table, body = match.groups(); raw[table] = ddl
        columns[table] = {}
        for definition in _切頂層逗號(body):
            words = definition.split()
            if words and words[0] not in {"CHECK", "FOREIGN", "PRIMARY", "UNIQUE", "CONSTRAINT"}:
                columns[table][words[0]] = " ".join(words[1:])
    return columns, raw


def _SQL字串(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and type(node.value) is str
            and re.search(r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b", node.value, re.I)]


def _schema外欄(sql: str, inventory: dict[str, dict[str, str]]) -> set[str]:
    """抓 qualified、INSERT、UPDATE 及歷史 *_json SQL 欄名。"""
    unknown: set[str] = set()
    aliases: dict[str, str] = {}
    for table, alias in re.findall(r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM)\s+(\w+)(?:\s+(\w+))?", sql, re.I):
        if table in inventory:
            aliases[table] = table
            if alias and alias.upper() not in {"WHERE", "SET", "ON", "ORDER", "LIMIT", "VALUES", "FOR"}:
                aliases[alias] = table
    for alias, column in re.findall(r"\b(\w+)\.(\w+)\b", sql):
        if alias in aliases and column not in inventory[aliases[alias]]:
            unknown.add(f"{aliases[alias]}.{column}")
    for table, listed in re.findall(r"\bINSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)", sql, re.I | re.S):
        if table in inventory:
            for column in listed.split(","):
                column = column.strip()
                if column and column not in inventory[table]: unknown.add(f"{table}.{column}")
    update = re.search(r"\bUPDATE\s+(\w+)\s+SET\s+(.*?)(?:\bWHERE\b|\bRETURNING\b|\Z)", sql, re.I | re.S)
    if update and update.group(1) in inventory:
        table = update.group(1)
        for column in re.findall(r"(?:^|,)\s*(\w+)\s*=", update.group(2)):
            if column not in inventory[table]: unknown.add(f"{table}.{column}")
    all_columns = {column for table in inventory.values() for column in table}
    unknown.update(identifier for identifier in re.findall(r"\b\w+_json\b", sql) if identifier not in all_columns)
    return unknown


def test_0001清冊逐欄釘選JSONB時間inet_interval_identity與FK():
    columns, raw = _載入0001清冊()
    expected_jsonb = {
        "endpoint_invocations": {"input", "metadata", "output", "error", "usage"},
        "run_events": {"payload"}, "endpoint_tool_calls": {"arguments", "result", "error"},
        "audit_events": {"metadata"},
    }
    for table, names in expected_jsonb.items():
        assert all(columns[table][name].startswith("jsonb") for name in names)
        assert not any(name.endswith("_json") for name in columns[table])
    temporal = {
        "endpoint_invocations": {"created_at", "completed_at"}, "run_events": {"created_at"},
        "endpoint_tool_calls": {"created_at"}, "audit_events": {"occurred_at", "created_at"},
        "endpoint_redactions": {"redacted_at"}, "invocation_sensitive_hits": {"detected_at"},
        "rate_limit_counters": {"window_start", "updated_at"},
        "auth_failure_rate_counters": {"window_start", "updated_at"},
    }
    for table, names in temporal.items():
        assert all(columns[table][name].startswith("timestamptz") for name in names)
    assert columns["auth_failure_rate_counters"]["client_ip"].startswith("inet")
    assert columns["retention_policies"]["retention_interval"].startswith("interval")
    assert "GENERATED BY DEFAULT AS IDENTITY" in columns["messages"]["id"]
    assert "GENERATED BY DEFAULT AS IDENTITY" in columns["skill_usage_events"]["id"]
    assert "FOREIGN KEY(endpoint_version_id,endpoint_id) REFERENCES published_endpoint_versions(id,endpoint_id)" in raw["endpoint_invocations"]
    assert "FOREIGN KEY(run_event_id,invocation_id) REFERENCES run_events(id,invocation_id)" in raw["endpoint_tool_calls"]
    assert "audit_event_id text NOT NULL UNIQUE REFERENCES audit_events(id) ON DELETE CASCADE" in raw["invocation_sensitive_hits"]
    assert "redaction_id text PRIMARY KEY REFERENCES endpoint_redactions(id) ON DELETE RESTRICT" in raw["redaction_tombstones"]


def test_七repository_SQL直接拒絕0001_schema外欄():
    inventory, _ = _載入0001清冊()
    paths = tuple(Path(module.__file__ or "") for module in (
        呼叫模組, 冪等模組, 限流模組, 稽核模組, 遮蔽模組, 保存模組, 投影模組,
    ))
    findings = {(path.name, sql): _schema外欄(sql, inventory)
                for path in paths for sql in _SQL字串(path) if _schema外欄(sql, inventory)}
    assert findings == {}
    assert _schema外欄("SELECT usage_json FROM endpoint_invocations", inventory) == {"usage_json"}
    assert set(遮蔽模組._目標.values()) <= {
        (table, column) for table, table_columns in inventory.items() for column in table_columns
    }
    audit_sql = " ".join(_SQL字串(Path(稽核模組.__file__ or "")))
    assert "xmin" in audit_sql and "RETURNING (xmin::text)::bigint" in audit_sql


def test_invocation_missing_row由unique_conflict_winner仲裁且內容衝突fail_closed(monkeypatch):
    canonical = '{"x":1}'
    existing = ("inv-old", "ep", "ver", None, None, None, "pending", canonical, None, None, None)
    c = 連線(結果([], rowcount=0), 結果([existing]))
    state = 安裝交易(monkeypatch, 呼叫模組, c)
    repo = 呼叫模組.PostgreSQL呼叫儲存庫("settings", 時鐘=lambda: 7, 識別碼工廠=lambda: "candidate")
    assert repo.建立已解析呼叫("ep", "ver", "req", {"x": 1}) == "inv-old"
    assert state == {"entered": 1, "exited": 1, "settings": ["settings"]}
    assert "ON CONFLICT(request_id) DO NOTHING RETURNING id" in c.calls[0][0]
    assert "FOR UPDATE" not in c.calls[1][0] and c.calls[1][1] == ("req",)

    c = 連線(結果([], rowcount=0), 結果([existing]))
    安裝交易(monkeypatch, 呼叫模組, c)
    with pytest.raises(呼叫儲存錯誤, match="^呼叫建立失敗$") as caught:
        repo.建立已解析呼叫("other", "ver", "req", {"x": 1})
    assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True
    assert len(c.calls) == 2


def test_invocation新建與狀態CAS釘選rowcount(monkeypatch):
    c = 連線(結果([("inv-1",)], rowcount=1), 結果(rowcount=1), 結果(rowcount=1))
    state = 安裝交易(monkeypatch, 呼叫模組, c)
    repo = 呼叫模組.PostgreSQL呼叫儲存庫("s", 時鐘=lambda: 9, 識別碼工廠=lambda: "inv-1")
    assert repo.建立已解析呼叫("ep", "ver", "req", {"safe": True}) == "inv-1"
    repo.標記執行中("inv-1")
    repo.結案("inv-1", "succeeded", output={"ok": 1}, usage={"total_tokens": 2})
    assert state["entered"] == state["exited"] == 3
    inserts = [x for x in c.calls if x[0].startswith("INSERT")]
    assert inserts[0][1][-1] == 9.0
    updates = [x for x in c.calls if x[0].startswith("UPDATE")]
    assert "AND status=%s" in updates[0][0]
    assert "AND status='running'" in updates[1][0]

    c = 連線(結果(rowcount=0))
    安裝交易(monkeypatch, 呼叫模組, c)
    with pytest.raises(呼叫儲存錯誤, match="^呼叫狀態更新失敗$"):
        repo.標記執行中("inv-1")


def test_run與tool序號由鎖定根配置且寫入失敗關閉(monkeypatch):
    c = 連線(
        結果([("running",)]), 結果([(2,)]), 結果(rowcount=1),
        結果([("running",)]), 結果([(4,)]), 結果(rowcount=1),
    )
    安裝交易(monkeypatch, 呼叫模組, c)
    repo = 呼叫模組.PostgreSQL呼叫儲存庫("s", 時鐘=lambda: 10)
    assert repo.附加執行事件("inv", "event", "delta", {"n": 1}) == 3
    assert repo.附加工具呼叫(
        "inv", "tool", "lookup", {"q": 1}, outcome="success", result={"answer": 2},
        run_event_id="event", latency_ms=3,
    ) == 5
    assert c.calls[0][0].endswith("FOR UPDATE")
    assert "MAX(sequence_number)" in c.calls[1][0]
    assert c.calls[2][1][2] == 3
    assert c.calls[5][1][3] == 5

    c = 連線(結果([("running",)]), 結果([(0,)]), 結果(rowcount=0))
    安裝交易(monkeypatch, 呼叫模組, c)
    with pytest.raises(呼叫儲存錯誤, match="^執行事件附加失敗$"):
        repo.附加執行事件("inv", "event", "delta", {})


def test_atomic_graph依FK順序且拒絕任意表欄位(monkeypatch):
    c = 連線(*(結果(rowcount=1) for _ in range(5)))
    安裝交易(monkeypatch, 呼叫模組, c)
    呼叫模組.PostgreSQL呼叫儲存庫("s").原子寫入呼叫圖形(
        {"id": "i"}, 執行事件=({"id": "e"},), 工具呼叫=({"id": "t"},),
        稽核事件=({"id": "a"},), 敏感命中=({"id": "h"},),
    )
    assert [sql.split()[2].split("(")[0] for sql, _ in c.calls] == [
        "endpoint_invocations", "run_events", "endpoint_tool_calls", "audit_events",
        "invocation_sensitive_hits",
    ]
    marker = "PRIVATE_GRAPH_SECRET"
    c = 連線()
    安裝交易(monkeypatch, 呼叫模組, c)
    with pytest.raises(呼叫儲存錯誤) as caught:
        呼叫模組.PostgreSQL呼叫儲存庫("s").原子寫入呼叫圖形({"id": "i", marker: marker})
    assert marker not in repr(caught.value) and c.calls == []


def test_atomic_graph舊public_JSON鍵會翻譯成0001欄名(monkeypatch):
    c = 連線(結果(rowcount=1), 結果(rowcount=1))
    安裝交易(monkeypatch, 呼叫模組, c)
    呼叫模組.PostgreSQL呼叫儲存庫("s").原子寫入呼叫圖形(
        {"id": "i", "input_json": {"safe": True}},
        執行事件=({"id": "e", "payload_json": {"items": [1, 2]}},),
    )
    assert "input_json" not in c.calls[0][0] and "(id,input)" in c.calls[0][0]
    assert "payload_json" not in c.calls[1][0] and "(id,payload)" in c.calls[1][0]
    assert json.loads(c.calls[0][1][1]) == {"safe": True}

    c = 連線()
    安裝交易(monkeypatch, 呼叫模組, c)
    with pytest.raises(呼叫儲存錯誤):
        呼叫模組.PostgreSQL呼叫儲存庫("s").原子寫入呼叫圖形(
            {"id": "i", "input": {}, "input_json": {}},
        )
    assert c.calls == []


def test_idempotency一般讀與caller_owned鎖定讀皆驗證形狀(monkeypatch):
    c = 連線(結果([("inv", "succeeded", '{"ok":1}', None)]))
    安裝交易(monkeypatch, 冪等模組, c)
    got = 冪等模組.PostgreSQL呼叫冪等儲存庫("s").取得("req")
    assert got.狀態 is 冪等模組.冪等狀態.SUCCEEDED and got.呼叫識別碼 == "inv"
    assert "FOR UPDATE" not in c.calls[0][0]

    c = 連線(結果([("inv", "running", None, None)]))
    got = 冪等模組.PostgreSQL呼叫冪等儲存庫("s").鎖定取得(c, "req")
    assert got.狀態 is 冪等模組.冪等狀態.RUNNING
    assert "FOR UPDATE" in c.calls[0][0]

    c = 連線(結果([("short",)]))
    with pytest.raises(呼叫儲存錯誤, match="^冪等狀態無法取得$"):
        冪等模組.PostgreSQL呼叫冪等儲存庫("s").鎖定取得(c, "req")


def test_rate_limit端點與憑證scope同交易且來源counter獨立(monkeypatch):
    c = 連線(結果([(3,)]), 結果([(5,)]))
    decision = 限流模組.增加PostgreSQL雙層計數並判定(c, "ep", "cred", 10, 4, 61)
    assert decision.允許 is False and decision.超限範圍 == "credential"
    assert c.calls[0][1][:2] == ("endpoint", "ep")
    assert c.calls[1][1][:2] == ("credential", "cred")
    assert all("ON CONFLICT" in sql and "RETURNING" in sql for sql, _ in c.calls)
    assert all(
        "ON CONFLICT (scope_type,scope_id,window_start)" in sql
        and "request_count" not in sql.split("ON CONFLICT (", 1)[1].split(")", 1)[0]
        for sql, _ in c.calls
    )

    c = 連線(結果([(11,)]))
    state = 安裝交易(monkeypatch, 限流模組, c)
    got = 限流模組.PostgreSQL限流儲存庫("s").記錄來源驗證失敗("1.2.3.4", "slug", 61, 上限=10)
    assert got.已超限 is True and got.計數 == 11 and got.上限 == 10
    assert c.calls[0][1][:2] == ("1.2.3.4", "slug")
    assert state["entered"] == state["exited"] == 1

    with pytest.raises(限流計數錯誤):
        限流模組.增加PostgreSQL雙層計數並判定(連線(結果([])), "ep", "cred", 1, 1, 1)


def test_invocation_attempt1_schema_invalid只append事件並保持running(monkeypatch):
    c = 連線(
        結果([dict列(("status",), "running")]),
        結果([dict列(("n",), 0)]),
        結果(rowcount=1),
    )
    state = 安裝交易(monkeypatch, 呼叫模組, c)
    repo = 呼叫模組.PostgreSQL呼叫儲存庫("settings", 時鐘=lambda: 10.0)
    sequence = repo.原子記錄執行事件並結案(
        "inv-1", "inv-1:attempt:1", "model_attempt",
        {"attempt": 1, "kind": "success", "schema_valid": False}, 1,
    )
    assert sequence == 1
    assert state["entered"] == state["exited"] == 1
    assert len(c.calls) == 3
    assert c.calls[2][0].startswith("INSERT INTO run_events")
    assert not any(sql.startswith("UPDATE endpoint_invocations") for sql, _ in c.calls)


def test_invocation成功且零警告仍回傳帶空警告的提交收據(monkeypatch):
    c = 連線(
        結果([dict列(("status",), "running")]),
        結果([dict列(("n",), 0)]),
        結果(rowcount=1),
        結果(rowcount=1),
    )
    state = 安裝交易(monkeypatch, 呼叫模組, c)
    repo = 呼叫模組.PostgreSQL呼叫儲存庫("settings", 時鐘=lambda: 10.0)
    receipt = repo.原子記錄執行事件並結案(
        "inv-1", "inv-1:attempt:1", "model_attempt",
        {"attempt": 1, "kind": "success", "schema_valid": True}, 1,
        status="succeeded", output={"answer": "ok"}, warnings=(),
    )
    assert receipt == (1, ())
    assert state["entered"] == state["exited"] == 1


def audit_event():
    return AuditEvent(
        event_id="audit-1", occurred_at=5, action="endpoint.invoke", outcome="success",
        actor=AuditActorRef("system", None),
        resource=AuditResourceRef("endpoint.invocation", "inv-1"),
        request_id="req-1", endpoint_id="ep-1", invocation_id="inv-1",
        metadata=AuditMetadata({"retry": False}),
    )


def test_audit_append回傳durable_sequence且衝突內容fail_closed(monkeypatch):
    c = 連線(結果([(44,)], rowcount=1))
    安裝交易(monkeypatch, 稽核模組, c)
    receipt = 稽核模組.PostgreSQL稽核服務("s", 時鐘=lambda: 6).附加稽核事件(audit_event())
    assert receipt.to_json() == {"event_id": "audit-1", "committed": True, "sequence": 44}
    assert "ON CONFLICT (event_id) DO NOTHING" in c.calls[0][0]
    assert "RETURNING (xmin::text)::bigint" in c.calls[0][0]
    assert "PRIVATE" not in repr(c.calls[0][1])

    c = 連線(結果([], rowcount=0), 結果([("wrong",) * 13 + (44,)]))
    安裝交易(monkeypatch, 稽核模組, c)
    with pytest.raises(AuditSinkError, match="^稽核事件無法確認提交$") as caught:
        稽核模組.PostgreSQL稽核服務("s", 時鐘=lambda: 6).附加稽核事件(audit_event())
    assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True


def test_redaction鎖定scope寫canonical_tombstone且不洩漏原文(monkeypatch):
    raw = '{"keep":1,"secret":"PRIVATE_RAW_VALUE"}'
    c = 連線(結果([("ep",)]), 結果([(None,)]), 結果([]), 結果([("inv", raw)]),
           *(結果(rowcount=1) for _ in range(5)))
    state = 安裝交易(monkeypatch, 遮蔽模組, c)
    receipt = 遮蔽模組.PostgreSQL不可逆遮蔽服務("s").遮蔽(
        True, "red-1", "audit-1", "admin-1", "req-1", "inv",
        "tool_result", "tool-1", "/secret", "privacy request", 12.5,
    )
    assert receipt["redaction_id"] == "red-1"
    assert receipt["original_sha256"] == hashlib.sha256(b'"PRIVATE_RAW_VALUE"').hexdigest()
    assert "pg_advisory_xact_lock" in c.calls[1][0]
    assert "FOR UPDATE" in c.calls[3][0] and c.calls[3][1] == ("tool-1",)
    updated = json.loads(c.calls[4][1][0])
    assert updated["secret"] == {"$tombstone": {"redaction_id": "red-1", "redacted_at": 12.5}}
    assert "PRIVATE_RAW_VALUE" not in repr(c.calls[4:])
    assert [sql.split()[2].split("(")[0] for sql,_ in c.calls[5:]] == [
        "audit_events", "endpoint_redactions", "redaction_tombstones",
        "redaction_idempotency_commands",
    ]
    assert state["entered"] == state["exited"] == 1


def test_redaction非管理員在DB前拒絕且wrong_invocation_scope無mutation(monkeypatch):
    c = 連線()
    安裝交易(monkeypatch, 遮蔽模組, c)
    service = 遮蔽模組.PostgreSQL不可逆遮蔽服務("s")
    with pytest.raises(不可逆遮蔽錯誤):
        service.遮蔽(False, "r", "a", "u", "q", "i", "output", "i", "", "reason", 1)
    assert c.calls == []

    c = 連線(結果([("ep",)]), 結果([(None,)]), 結果([]), 結果([("foreign-inv", "{}")]))
    安裝交易(monkeypatch, 遮蔽模組, c)
    with pytest.raises(不可逆遮蔽錯誤):
        service.遮蔽(True, "r", "a", "u", "q", "i", "run_event", "run", "", "reason", 1)
    assert len(c.calls) == 4


def test_redaction_same_retry回傳canonical_receipt且conflict不mutation(monkeypatch):
    target=("tool_result","tool-1","/secret","privacy request")
    canonical=遮蔽模組._建立正規JSON({
        "endpoint_id":"ep","invocation_id":"inv","target_type":target[0],
        "target_row_id":target[1],"json_path":target[2],"reason":target[3],
    })
    fingerprint=hashlib.sha256(canonical.encode()).hexdigest()
    existing=(fingerprint,"red-old",*target[:3],"a"*64,target[3],"admin-1","audit-old",12.5)
    c=連線(結果([("ep",)]),結果([(None,)]),結果([existing]))
    安裝交易(monkeypatch,遮蔽模組,c)
    got=遮蔽模組.PostgreSQL不可逆遮蔽服務("s").遮蔽(
        True,"red-new","audit-new","admin-1","req-1","inv",*target,12.5)
    assert got["redaction_id"]=="red-old" and len(c.calls)==3

    conflict=("0"*64,*existing[1:])
    c=連線(結果([("ep",)]),結果([(None,)]),結果([conflict]))
    安裝交易(monkeypatch,遮蔽模組,c)
    with pytest.raises(不可逆遮蔽錯誤):
        遮蔽模組.PostgreSQL不可逆遮蔽服務("s").遮蔽(
            True,"red-new","audit-new","admin-1","req-1","inv",*target,12.5)
    assert len(c.calls)==3


def test_redaction_graph_rowcount_mismatch_fail_closed(monkeypatch):
    raw='{"secret":"x"}'
    c=連線(結果([("ep",)]),結果([(None,)]),結果([]),結果([("inv",raw)]),
           結果(rowcount=1),結果(rowcount=1),結果(rowcount=0))
    安裝交易(monkeypatch,遮蔽模組,c)
    with pytest.raises(不可逆遮蔽錯誤):
        遮蔽模組.PostgreSQL不可逆遮蔽服務("s").遮蔽(
            True,"red","audit","admin","req","inv","tool_result","tool","/secret","privacy",1)


def test_retention計畫共享相依預算並保留阻擋器(monkeypatch):
    # 2020-01-01 -> 2025-01-01，現在 2026 足以到期。
    c = 連線(
        結果([("inv", 1577836800.0)]),
        結果([("run",)]), 結果([("tool",)]),
        結果([("red", "tool_result")]), 結果([("audit",)]),
    )
    state = 安裝交易(monkeypatch, 保存模組, c)
    plans = 保存模組.PostgreSQL保存候選規劃器("s").規劃(1767225600.0, 相依上限=4)
    assert len(plans) == 1
    assert plans[0].呼叫識別碼 == "inv"
    assert plans[0].刪除阻擋器 == (
        "endpoint_redactions_no_delete", "audit_events_no_delete", "redacted_tool_call_no_delete",
    )
    limits = [params[-1] for _, params in c.calls[1:]]
    assert limits == [5, 4, 3, 2]
    assert "created_at + INTERVAL '5 years' <= to_timestamp(%s)" in c.calls[0][0]
    assert state["entered"] == state["exited"] == 1


def test_retention清除SKIP_LOCKED並依FK順序核對根CAS(monkeypatch):
    c = 連線(
        結果([("inv-1",), ("inv-2",)]),
        結果(rowcount=3), 結果(rowcount=4), 結果(rowcount=5), 結果(rowcount=2),
    )
    安裝交易(monkeypatch, 保存模組, c)
    result = 保存模組.PostgreSQL保存清除服務("s").清除(1767225600.0)
    assert result.呼叫數 == 2 and result.執行事件數 == 5 and result.工具呼叫數 == 4
    assert "FOR UPDATE SKIP LOCKED" in c.calls[0][0]
    assert "NOT EXISTS (SELECT 1 FROM endpoint_redactions" in c.calls[0][0]
    assert "NOT EXISTS (SELECT 1 FROM audit_events" in c.calls[0][0]
    assert [sql.split()[2] for sql, _ in c.calls[1:]] == [
        "invocation_sensitive_hits", "endpoint_tool_calls", "run_events", "endpoint_invocations",
    ]
    assert all(params == (["inv-1", "inv-2"],) for _, params in c.calls[1:])

    c = 連線(結果([("inv-1",)]), *(結果(rowcount=0) for _ in range(3)), 結果(rowcount=0))
    安裝交易(monkeypatch, 保存模組, c)
    with pytest.raises(保存清除錯誤):
        保存模組.PostgreSQL保存清除服務("s").清除(1767225600.0)


def admin_rows():
    invocation = (
        "inv", "ep", "ver", None, "req", None, None, "succeeded", "{}", None,
        '{"ok":true}', None, '{"total_tokens":2}', None, None, 3.0, None, 10.0, 11.0,
    )
    events = [("run", 1, "delta", "{}", 10.1)]
    tools = [("tool", "run", 1, "lookup", "{}", "success", "{}", None, 1.0, None, 10.2)]
    reds = []
    hits = [("hit", "tool", "tool_result", "pattern", "/value", 0, 2, "audit", 10.3)]
    return invocation, events, tools, reds, hits


def test_projection_admin_exact_pair與敏感命中transport_schema(monkeypatch):
    row, events, tools, reds, hits = admin_rows()
    c = 連線(結果([row]), 結果(events), 結果(tools), 結果(reds), 結果(hits))
    state = 安裝交易(monkeypatch, 投影模組, c)
    detail = 投影模組.PostgreSQL呼叫查詢投影("s").查詢管理員原始資料(True, "ep", "inv")
    assert set(detail) == ADMIN_INVOCATION_DETAIL_FIELDS
    assert detail["invocation"] == {"id": "inv", "request_id": "req", "session_id": None}
    assert detail["sensitive_hits"] == [{
        "id": "hit", "tool_call_id": "tool", "target": "tool_result",
        "detector_type": "pattern", "json_path": "/value", "start": 0, "end": 2,
        "detected_at": 10.3,
    }]
    assert c.calls[0][1] == ("ep", "inv")
    assert all(params == ("inv",) for _, params in c.calls[1:])
    assert state["entered"] == state["exited"] == 1


def test_projection_JSONB接受psycopg已解碼dict與list(monkeypatch):
    row, events, tools, reds, hits = admin_rows()
    row = row[:8] + ({"nested": [1]}, None, ["ok"], None, {"total_tokens": 2}) + row[13:]
    events = [("run", 1, "delta", {"parts": [1, 2]}, 10.1)]
    tools = [("tool", "run", 1, "lookup", ["arg"], "success", {"ok": True}, None, 1.0, None, 10.2)]
    c = 連線(結果([row]), 結果(events), 結果(tools), 結果(reds), 結果(hits))
    安裝交易(monkeypatch, 投影模組, c)
    detail = 投影模組.PostgreSQL呼叫查詢投影("s").查詢管理員原始資料(True, "ep", "inv")
    assert detail["input"] == {"nested": [1]}
    assert detail["output"] == ["ok"]
    assert detail["run_events"][0]["payload"] == {"parts": [1, 2]}
    assert detail["tool_calls"][0]["arguments"] == ["arg"]


def test_projection時間欄與游標epoch會轉換timestamptz(monkeypatch):
    c = 連線(結果([("inv", "ep", "ver", "req", "succeeded", None, 1.0, 10.0, 11.0, False)]))
    安裝交易(monkeypatch, 投影模組, c)
    condition = 管理員呼叫查詢條件("ep", 1.0, 20.0, "succeeded", None, 1)
    position = 管理員呼叫游標位置(15.0, "cursor-inv")
    page = 投影模組.PostgreSQL呼叫查詢投影("s").列出管理員安全呼叫(condition, position)
    assert len(page.項目) == 1
    sql, params = c.calls[0]
    assert "EXTRACT(EPOCH FROM i.created_at)::double precision" in sql
    assert "i.created_at>=to_timestamp(%s)" in sql
    assert "i.created_at<to_timestamp(%s)" in sql
    assert params[1:5] == (1.0, 1.0, 20.0, 20.0)


def test_projection_owner以owner_endpoint_invocation複合gate且不選raw_secret(monkeypatch):
    marker = "PRIVATE_OWNER_RAW_SECRET"
    owner_row = ("inv", "req", None, "ver", "failed", "safe_error", 2.0, '{"total_tokens":3}')
    c = 連線(結果([owner_row]), 結果([("lookup",)]))
    安裝交易(monkeypatch, 投影模組, c)
    detail = 投影模組.PostgreSQL呼叫查詢投影("s").查詢擁有者診斷("owner", "ep", "inv")
    assert detail["tool_names"] == ["lookup"] and detail["usage"] == {"total_tokens": 3}
    owner_sql = c.calls[0][0]
    assert "e.owner_user_id=%s AND e.id=%s AND i.id=%s" in owner_sql
    for forbidden in ("input_json", "metadata_json", "output_json", "arguments_json", marker):
        assert forbidden not in owner_sql and forbidden not in repr(detail)

    c = 連線(結果([]))
    安裝交易(monkeypatch, 投影模組, c)
    with pytest.raises(查詢投影錯誤, match="^呼叫紀錄不可取得$"):
        投影模組.PostgreSQL呼叫查詢投影("s").查詢擁有者診斷("foreign", "ep", "inv")
    assert len(c.calls) == 1


def test_projection拒絕非exact_admin且不接觸DB(monkeypatch):
    c = 連線()
    安裝交易(monkeypatch, 投影模組, c)
    with pytest.raises(管理員呼叫查詢錯誤):
        投影模組.PostgreSQL呼叫查詢投影("s").查詢管理員原始資料(1, "ep", "inv")
    assert c.calls == []


def test_正式dict_row因果涵蓋invocation冪等限流audit與retention(monkeypatch):
    existing = dict列(
        ("id", "endpoint_id", "endpoint_version_id", "credential_id", "session_id", "message_id",
         "status", "input", "metadata", "metadata_size_bytes", "metadata_sha256"),
        "inv-old", "ep", "ver", None, None, None, "pending", {"x": 1}, None, None, None,
    )
    c = 連線(結果([{"id": "inv-new"}], rowcount=1), 結果([], rowcount=0), 結果([existing]))
    安裝交易(monkeypatch, 呼叫模組, c)
    repo = 呼叫模組.PostgreSQL呼叫儲存庫(
        "s", 時鐘=lambda: 7, 識別碼工廠=iter(("inv-new", "candidate")).__next__)
    assert repo.建立已解析呼叫("ep", "ver", "req-new", {"x": 1}) == "inv-new"
    assert repo.建立已解析呼叫("ep", "ver", "req-old", {"x": 1}) == "inv-old"

    c = 連線(結果([{"id": "inv-old", "status": "succeeded", "output": {"ok": 1}, "error": None}]))
    安裝交易(monkeypatch, 冪等模組, c)
    got = 冪等模組.PostgreSQL呼叫冪等儲存庫("s").取得("req-old")
    assert got.呼叫識別碼 == "inv-old" and got.output_json == '{"ok":1}'

    c = 連線(結果([{"request_count": 3}]), 結果([{"request_count": 5}]))
    decision = 限流模組.增加PostgreSQL雙層計數並判定(c, "ep", "cred", 10, 4, 61)
    assert decision.端點計數 == 3 and decision.憑證計數 == 5

    event = audit_event(); canonical = 稽核模組._建立canonical列(event)
    audit_existing = dict列(
        ("id", "event_id", "occurred_at_epoch", "action", "outcome", "actor_type", "actor_id",
         "resource_type", "resource_id", "request_id", "endpoint_id", "invocation_id", "metadata",
         "durable_sequence"), *canonical[:12], json.loads(canonical[12]), 45)
    c = 連線(結果([{"durable_sequence": 44}], rowcount=1),
             結果([], rowcount=0), 結果([audit_existing]))
    安裝交易(monkeypatch, 稽核模組, c)
    service = 稽核模組.PostgreSQL稽核服務("s", 時鐘=lambda: 6)
    assert service.附加稽核事件(event).sequence == 44
    assert service.附加稽核事件(event).sequence == 45

    c = 連線(
        結果([{"id": "inv", "created_at_epoch": 1577836800.0}]),
        結果([{"id": "run"}]), 結果([{"id": "tool"}]),
        結果([{"id": "red", "target_type": "run_event"}]), 結果([{"id": "audit"}]))
    安裝交易(monkeypatch, 保存模組, c)
    plan = 保存模組.PostgreSQL保存候選規劃器("s").規劃(1767225600.0, 相依上限=4)[0]
    assert plan.執行事件識別碼 == ("run",) and "redacted_run_event_no_delete" in plan.刪除阻擋器
    c = 連線(結果([{"id": "inv"}]), 結果(rowcount=1), 結果(rowcount=1),
             結果(rowcount=1), 結果(rowcount=1))
    安裝交易(monkeypatch, 保存模組, c)
    assert 保存模組.PostgreSQL保存清除服務("s").清除(1767225600.0).呼叫數 == 1


def test_正式dict_row因果涵蓋redaction_same_new與owner_admin_nested(monkeypatch):
    target = ("tool_result", "tool-1", "/secret", "privacy request")
    canonical = 遮蔽模組._建立正規JSON({
        "endpoint_id": "ep", "invocation_id": "inv", "target_type": target[0],
        "target_row_id": target[1], "json_path": target[2], "reason": target[3]})
    existing = dict列(
        ("request_fingerprint", "id", "target_type", "target_row_id", "json_path",
         "original_sha256", "reason", "actor_id", "audit_event_id", "redacted_at"),
        hashlib.sha256(canonical.encode()).hexdigest(), "red-old", *target[:3], "a" * 64,
        target[3], "admin", "audit-old", 12.5)
    c = 連線(
        結果([{"endpoint_id": "ep"}]), 結果([{"pg_advisory_xact_lock": None}]), 結果([]),
        結果([{"invocation_id": "inv", "result": {"secret": "value"}}]),
        *(結果(rowcount=1) for _ in range(5)),
        結果([{"endpoint_id": "ep"}]), 結果([{"pg_advisory_xact_lock": None}]), 結果([existing]))
    安裝交易(monkeypatch, 遮蔽模組, c)
    service = 遮蔽模組.PostgreSQL不可逆遮蔽服務("s")
    new = service.遮蔽(True, "red-new", "audit-new", "admin", "req-new", "inv", *target, 12.5)
    same = service.遮蔽(True, "ignored", "ignored-audit", "admin", "req-same", "inv", *target, 12.5)
    assert new["redaction_id"] == "red-new" and same["redaction_id"] == "red-old"

    owner_names = ("id", "request_id", "session_id", "endpoint_version_id", "status",
                   "error_code", "latency_ms", "usage")
    c = 連線(結果([dict列(owner_names, "inv", "req", None, "ver", "succeeded", None, 2.0,
                              {"total_tokens": 3})]), 結果([{"tool_name": "lookup"}]))
    安裝交易(monkeypatch, 投影模組, c)
    owner = 投影模組.PostgreSQL呼叫查詢投影("s").查詢擁有者診斷("owner", "ep", "inv")
    assert owner["tool_names"] == ["lookup"]

    row, events, tools, _, hits = admin_rows()
    names = (
        ("id", "endpoint_id", "endpoint_version_id", "credential_id", "request_id", "session_id",
         "message_id", "status", "input", "metadata", "output", "error", "usage", "metadata_size_bytes",
         "metadata_sha256", "latency_ms", "pricing_version", "created_at_epoch", "completed_at_epoch"),
        ("id", "sequence_number", "event_type", "payload", "created_at_epoch"),
        ("id", "run_event_id", "sequence_number", "tool_name", "arguments", "outcome", "result", "error",
         "latency_ms", "retry_of_tool_call_id", "created_at_epoch"),
        ("id", "target_type", "target_row_id", "json_path", "original_sha256", "reason", "actor_type",
         "actor_id", "audit_event_id", "is_tombstone", "redacted_at_epoch"),
        ("id", "tool_call_id", "target_type", "detector_type", "json_path", "start_offset", "end_offset",
         "audit_event_id", "detected_at_epoch"))
    red = ("red", "output", "inv", "/secret", "a" * 64, "privacy", "admin", "u", "audit", True, 10.4)
    c = 連線(結果([dict列(names[0], *row)]), 結果([dict列(names[1], *events[0])]),
             結果([dict列(names[2], *tools[0])]), 結果([dict列(names[3], *red)]),
             結果([dict列(names[4], *hits[0])]))
    安裝交易(monkeypatch, 投影模組, c)
    admin = 投影模組.PostgreSQL呼叫查詢投影("s").查詢管理員原始資料(True, "ep", "inv")
    assert [admin[k][0]["id"] for k in ("run_events", "tool_calls", "redactions", "sensitive_hits")] == [
        "run", "tool", "red", "hit"]
