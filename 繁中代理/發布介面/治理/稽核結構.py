"""SQLite稽核v6的固定schema fingerprints。"""

_LEDGER = (
    (1, "0001_建立發布端點核心.sql"),
    (2, "0002_建立憑證與稽核.sql"),
    (3, "0003_建立呼叫事件與工具紀錄.sql"),
    (4, "0004_建立限流與遮蔽資料.sql"),
    (5, "0005_建立網頁工作階段.sql"),
    (6, "0006_擴充稽核事件契約.sql"),
    (7, "0007_建立不可逆遮蔽墓碑.sql"),
    (8, "0008_建立五年保存候選索引.sql"),
    (9, "0009_建立保存相依識別索引.sql"),
    (10, "0010_建立來源驗證失敗節流.sql"),
    (11, "0011_重建空憑證為CRED結構.sql"),
)
_TABLE_INFO = (
    (0, "id", "TEXT", 0, None, 1),
    (1, "event_id", "TEXT", 1, None, 0),
    (2, "occurred_at", "REAL", 1, None, 0),
    (3, "action", "TEXT", 1, None, 0),
    (4, "outcome", "TEXT", 1, None, 0),
    (5, "actor_type", "TEXT", 1, None, 0),
    (6, "actor_id", "TEXT", 0, None, 0),
    (7, "resource_type", "TEXT", 1, None, 0),
    (8, "resource_id", "TEXT", 1, None, 0),
    (9, "request_id", "TEXT", 0, None, 0),
    (10, "endpoint_id", "TEXT", 0, None, 0),
    (11, "invocation_id", "TEXT", 0, None, 0),
    (12, "metadata_json", "TEXT", 1, None, 0),
    (13, "created_at", "REAL", 1, None, 0),
)
_FOREIGN_KEYS = (
    (0, 0, "endpoint_invocations", "invocation_id", "id", "NO ACTION", "RESTRICT", "NONE"),
    (1, 0, "published_endpoints", "endpoint_id", "id", "NO ACTION", "RESTRICT", "NONE"),
)
_INDEXES = (
    ("idx_audit_events_endpoint_time", 0, "c", ("endpoint_id", "occurred_at")),
    ("idx_audit_events_invocation_time", 0, "c", ("invocation_id", "occurred_at")),
    ("idx_audit_events_resource_time", 0, "c", ("resource_type", "resource_id", "occurred_at")),
    ("idx_audit_events_retention_invocation_id", 0, "c", ("invocation_id", "id")),
    ("sqlite_autoindex_audit_events_1", 1, "pk", ("id",)),
    ("sqlite_autoindex_audit_events_2", 1, "u", ("event_id",)),
)
_TABLE_SQL = """CREATE TABLE \"audit_events\" (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  occurred_at REAL NOT NULL CHECK(typeof(occurred_at) IN ('real','integer') AND occurred_at >= 0),
  action TEXT NOT NULL CHECK(trim(action) <> ''),
  outcome TEXT NOT NULL CHECK(outcome IN ('success','denied','failed','legacy_unknown')),
  actor_type TEXT NOT NULL CHECK(actor_type IN ('user','admin','service_account','system')),
  actor_id TEXT CHECK(actor_type = 'system' OR (actor_id IS NOT NULL AND trim(actor_id) <> '')),
  resource_type TEXT NOT NULL CHECK(trim(resource_type) <> ''),
  resource_id TEXT NOT NULL CHECK(trim(resource_id) <> ''),
  request_id TEXT,
  endpoint_id TEXT REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  invocation_id TEXT REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json) = 'object'),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0)
)"""
_OBJECT_SQL = (
    ("table", "audit_events", _TABLE_SQL),
    ("trigger", "audit_events_no_delete", "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN\n  SELECT RAISE(ABORT, 'audit events are append only');\nEND"),
    ("trigger", "audit_events_no_update", "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN\n  SELECT RAISE(ABORT, 'audit events are append only');\nEND"),
)
