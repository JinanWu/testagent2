CREATE TABLE rate_limit_counters (
  scope_type TEXT NOT NULL CHECK(scope_type IN ('endpoint','credential')),
  scope_id TEXT NOT NULL CHECK(trim(scope_id) <> ''),
  window_start INTEGER NOT NULL CHECK(typeof(window_start) = 'integer' AND window_start >= 0),
  request_count INTEGER NOT NULL CHECK(typeof(request_count) = 'integer' AND request_count >= 0),
  updated_at REAL NOT NULL CHECK(typeof(updated_at) IN ('real','integer') AND updated_at >= 0),
  PRIMARY KEY(scope_type, scope_id, window_start)
);

CREATE TABLE endpoint_redactions (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  target_type TEXT NOT NULL CHECK(target_type IN (
    'invocation_input',
    'metadata',
    'output',
    'error',
    'run_event',
    'tool_arguments',
    'tool_result',
    'tool_error'
  )),
  target_row_id TEXT NOT NULL CHECK(trim(target_row_id) <> ''),
  json_path TEXT NOT NULL DEFAULT '',
  original_sha256 TEXT NOT NULL CHECK(
    length(original_sha256) = 64
    AND original_sha256 = lower(original_sha256)
    AND original_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  reason TEXT NOT NULL CHECK(trim(reason) <> '' AND length(reason) <= 1000),
  actor_type TEXT NOT NULL CHECK(actor_type IN ('user','admin','system')),
  actor_id TEXT CHECK(actor_type = 'system' OR (actor_id IS NOT NULL AND trim(actor_id) <> '')),
  audit_event_id TEXT NOT NULL REFERENCES audit_events(id) ON DELETE RESTRICT,
  is_tombstone INTEGER NOT NULL CHECK(typeof(is_tombstone) = 'integer' AND is_tombstone IN (0, 1)),
  redacted_at REAL NOT NULL CHECK(typeof(redacted_at) IN ('real','integer') AND redacted_at >= 0),
  UNIQUE(target_type, target_row_id, json_path)
);

CREATE INDEX idx_endpoint_redactions_invocation_time
  ON endpoint_redactions(invocation_id, redacted_at);
CREATE INDEX idx_endpoint_redactions_audit
  ON endpoint_redactions(audit_event_id);
