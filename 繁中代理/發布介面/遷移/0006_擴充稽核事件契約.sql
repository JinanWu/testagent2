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

DROP TABLE audit_events;
ALTER TABLE audit_events_v6 RENAME TO audit_events;

CREATE INDEX idx_audit_events_resource_time
  ON audit_events(resource_type, resource_id, occurred_at);
CREATE INDEX idx_audit_events_endpoint_time
  ON audit_events(endpoint_id, occurred_at);
CREATE INDEX idx_audit_events_invocation_time
  ON audit_events(invocation_id, occurred_at);

CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END;
