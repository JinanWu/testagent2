DROP TRIGGER audit_events_no_update;
DROP TRIGGER audit_events_no_delete;
DROP INDEX idx_audit_events_target_time;
DROP INDEX idx_audit_events_endpoint_time;

CREATE TABLE audit_events_v6 (
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
);

INSERT INTO audit_events_v6(
  id,event_id,occurred_at,action,outcome,actor_type,actor_id,
  resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at
)
SELECT
  id,id,created_at,action,'legacy_unknown',actor_type,actor_id,
  target_type,target_id,request_id,endpoint_id,NULL,metadata_json,created_at
FROM audit_events;

CREATE TABLE endpoint_redactions_v6 (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  target_type TEXT NOT NULL CHECK(target_type IN (
    'invocation_input','metadata','output','error',
    'run_event','tool_arguments','tool_result','tool_error'
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
  audit_event_id TEXT NOT NULL REFERENCES audit_events_v6(id) ON DELETE RESTRICT,
  is_tombstone INTEGER NOT NULL CHECK(typeof(is_tombstone) = 'integer' AND is_tombstone IN (0, 1)),
  redacted_at REAL NOT NULL CHECK(typeof(redacted_at) IN ('real','integer') AND redacted_at >= 0),
  UNIQUE(target_type, target_row_id, json_path)
);

INSERT INTO endpoint_redactions_v6(
  id,invocation_id,target_type,target_row_id,json_path,original_sha256,
  reason,actor_type,actor_id,audit_event_id,is_tombstone,redacted_at
)
SELECT
  id,invocation_id,target_type,target_row_id,json_path,original_sha256,
  reason,actor_type,actor_id,audit_event_id,is_tombstone,redacted_at
FROM endpoint_redactions;

DROP TABLE endpoint_redactions;
DROP TABLE audit_events;
ALTER TABLE audit_events_v6 RENAME TO audit_events;
ALTER TABLE endpoint_redactions_v6 RENAME TO endpoint_redactions;

CREATE INDEX idx_audit_events_resource_time
  ON audit_events(resource_type, resource_id, occurred_at);
CREATE INDEX idx_audit_events_endpoint_time
  ON audit_events(endpoint_id, occurred_at);
CREATE INDEX idx_audit_events_invocation_time
  ON audit_events(invocation_id, occurred_at);
CREATE INDEX idx_endpoint_redactions_invocation_time
  ON endpoint_redactions(invocation_id, redacted_at);
CREATE INDEX idx_endpoint_redactions_audit
  ON endpoint_redactions(audit_event_id);

CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END;
