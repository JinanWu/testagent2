import ast
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面 import PostgreSQL端點庫 as module


_SCHEMA = Path(__file__).resolve().parents[2] / "繁中代理/postgres_migrations/versions/0001_full_product_schema.py"
_ADAPTERS = (
    Path(module.__file__),
    Path(module.__file__).with_name("PostgreSQL版本服務.py"),
    Path(module.__file__).with_name("憑證") / "PostgreSQL儲存庫.py",
)


def _settings():
    return 交易儲存設定(
        "postgres", "postgresql:///app?host=/cloudsql/proj:region:db", "proj:region:db", 0, 1, 1,
    )


def _ddl_inventory():
    tree = ast.parse(_SCHEMA.read_text(encoding="utf-8"))
    ddl = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DDL" for target in node.targets)
    )
    inventory = {}
    for statement in ddl:
        match = re.match(r"CREATE TABLE\s+(\w+)\s*\((.*)\)\s*$", statement, re.I | re.S)
        if match is None:
            continue
        table, body = match.groups()
        chunks, start, depth = [], 0, 0
        for index, char in enumerate(body):
            depth += char == "("
            depth -= char == ")"
            if char == "," and depth == 0:
                chunks.append(body[start:index])
                start = index + 1
        chunks.append(body[start:])
        columns = set()
        for chunk in chunks:
            token = chunk.strip().split(None, 1)[0].strip('"').lower()
            if token not in {"primary", "unique", "check", "foreign", "constraint"}:
                columns.add(token)
        inventory[table.lower()] = columns
    return inventory


def _sql_literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\b(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", node.value, re.I)
    )


def _assert_sql_columns_exist(sql, inventory):
    for match in re.finditer(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)", sql, re.I | re.S):
        table, raw_columns = match.groups()
        assert table.lower() in inventory
        columns = {column.strip().strip('"').lower() for column in raw_columns.split(",")}
        assert columns <= inventory[table.lower()], (table, columns - inventory[table.lower()], sql)

    update = re.search(r"UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\s+WHERE\s+(.*))?$", sql, re.I | re.S)
    if update:
        table, assignments, where = update.groups()
        assert table.lower() in inventory
        def split_assignments(text):
            parts, start, depth, quote = [], 0, 0, None
            for index, char in enumerate(text):
                if quote:
                    if char == quote and (index == 0 or text[index - 1] != "\\"):
                        quote = None
                elif char in "'\"":
                    quote = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                elif char == "," and depth == 0:
                    parts.append(text[start:index]); start = index + 1
            parts.append(text[start:])
            return parts

        referenced = {
            item.split("=", 1)[0].strip().split(".")[-1].lower()
            for item in split_assignments(assignments)
        }
        if where:
            referenced.update(
                name.lower()
                for name in re.findall(r"(?:^|\b(?:WHERE|AND|OR)\s+)(?:\w+\.)?(\w+)\s*(?:=|IS\b)", "WHERE " + where, re.I)
            )
        assert referenced <= inventory[table.lower()], (table, referenced - inventory[table.lower()], sql)

    select = re.search(r"SELECT\s+(.*?)\s+FROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?", sql, re.I | re.S)
    if select:
        selected, first_table, alias = select.groups()
        tables = {first_table.lower()}
        aliases = {first_table.lower(): first_table.lower()}
        if alias and alias.upper() not in {"WHERE", "JOIN", "FOR", "ORDER", "ON"}:
            aliases[alias.lower()] = first_table.lower()
        for table, joined_alias in re.findall(r"\bJOIN\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?", sql, re.I):
            tables.add(table.lower())
            aliases[table.lower()] = table.lower()
            if joined_alias and joined_alias.upper() not in {"WHERE", "JOIN", "FOR", "ORDER", "ON"}:
                aliases[joined_alias.lower()] = table.lower()
        for qualifier, column in re.findall(r"\b(\w+)\.(\w+)\b", sql):
            if qualifier.lower() in aliases:
                table = aliases[qualifier.lower()]
                assert column.lower() in inventory[table], (table, column, sql)
        union = set().union(*(inventory[table] for table in tables))
        for expression in selected.split(","):
            expression = re.sub(r"\s+AS\s+\w+\s*$", "", expression.strip(), flags=re.I)
            if re.fullmatch(r"\w+", expression):
                assert expression.lower() in union, (tables, expression, sql)


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row, self.rowcount = row, rowcount

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "SELECT id FROM published_endpoints" in sql:
            return Cursor(None)
        return Cursor(rowcount=1)


class Unit:
    def __init__(self, conn):
        self.conn = conn
        self.transactions = 0

    @contextmanager
    def 交易(self):
        self.transactions += 1
        yield self.conn


def test_0001_inventory_rejects_every_unknown_adapter_insert_update_and_projection_column():
    inventory = _ddl_inventory()
    assert inventory["published_endpoint_versions"] >= {
        "allowed_skills", "allowed_tools", "tool_schema_snapshot", "model_config_snapshot",
        "retry_policy", "skill_bundle_manifest", "input_schema", "response_schema",
    }
    assert "allowed_skills_json" not in inventory["published_endpoint_versions"]
    assert "ip_allowlist" in inventory["endpoint_credentials"]
    assert "ip_allowlist_json" not in inventory["endpoint_credentials"]
    for adapter in _ADAPTERS:
        for sql in _sql_literals(adapter):
            _assert_sql_columns_exist(sql, inventory)
            assert not re.search(
                r"\b(?:allowed_skills_json|allowed_tools_json|tool_schema_snapshot_json|"
                r"model_config_snapshot_json|retry_policy_json|skill_bundle_manifest_json|"
                r"input_schema_json|response_schema_json|ip_allowlist_json|metadata_json)\b",
                sql,
            )
            assert not re.search(r"\b(?:CREATE|ALTER|DROP|TRUNCATE)\b", sql, re.I)


def test_endpoint_graph_uses_one_postgres_transaction_owner_fk_jsonb_aware_time_and_cas(monkeypatch):
    draft = SimpleNamespace(草稿識別碼="draft-1")
    snapshot = SimpleNamespace(
        original_requirement_text="r", system_prompt="p", allowed_skills=[], allowed_tools=[],
        tool_schema_snapshot={}, tool_runtime_revision="runtime-1", model_config_snapshot={},
        retry_policy={}, skill_bundle_manifest={}, input_schema=None, response_schema={},
        created_by_user_id="owner",
    )
    credential = SimpleNamespace(
        name="key", purpose="test", key_version=1, key_nonce=b"n" * 12,
        key_ciphertext=b"c" * 62, key_hash="a" * 64, key_prefix="pk_preview",
        key_last4="1234", expires_at=99.0, ip_allowlist=[], rate_limit_requests=10,
        created_by_user_id="owner",
    )
    confirm = SimpleNamespace(slug="demo", endpoint_limit=60, window_seconds=60)
    monkeypatch.setattr(module, "_發布前驗證", lambda *_: (draft, snapshot, credential, confirm))
    monkeypatch.setattr(module, "_呼叫發布callbacks", lambda *_: ("ep-1", "v-1", "c-1", "sa-1", 10.0))
    service = module.PostgreSQL端點庫(
        _settings(), lambda: "ep-1", lambda: "v-1", lambda: "c-1", lambda: "sa-1", lambda: 10.0,
    )
    conn = Connection()
    unit = Unit(conn)
    service._工作單元 = unit
    result = service.發布("owner", draft, snapshot, credential, 1.0)
    sql = "\n".join(call[0] for call in conn.calls)
    assert result.endpoint_id == "ep-1" and unit.transactions == 1
    assert "pg_advisory_xact_lock" in sql and "FOR UPDATE" in sql
    assert "current_version_id IS NULL" in sql
    assert all("?" not in statement for statement, _ in conn.calls)
    assert all(statement.count("%s") == len(params) for statement, params in conn.calls)
    service_insert = next(call for call in conn.calls if call[0].startswith("INSERT INTO service_accounts"))
    assert service_insert[1][1] == "owner"
    all_params = tuple(value for _, params in conn.calls for value in params)
    aware = [value for value in all_params if isinstance(value, datetime)]
    assert aware and all(value.tzinfo == timezone.utc for value in aware)
    assert any(isinstance(value, Jsonb) for value in all_params)
    credential_insert = next(call for call in conn.calls if call[0].startswith("INSERT INTO endpoint_credentials"))
    assert "rate_limit_window_seconds" in credential_insert[0]
    assert isinstance(credential_insert[1][5], bytes) and isinstance(credential_insert[1][6], bytes)
