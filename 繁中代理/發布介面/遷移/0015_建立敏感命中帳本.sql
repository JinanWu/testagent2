CREATE TABLE invocation_sensitive_hits (
  id TEXT PRIMARY KEY
    CHECK(
      typeof(id) = 'text'
      AND length(CAST(id AS BLOB)) BETWEEN 1 AND 256
      AND trim(id) = id
    ),
  invocation_id TEXT NOT NULL
    REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  tool_call_id TEXT,
  target_type TEXT NOT NULL
    CHECK(target_type IN (
      'input','metadata','response_data','tool_arguments','tool_result'
    )),
  detector_type TEXT NOT NULL
    CHECK(
      typeof(detector_type) = 'text'
      AND length(CAST(detector_type AS BLOB)) BETWEEN 1 AND 128
      AND substr(detector_type, 1, 1) GLOB '[a-z]'
      AND detector_type NOT GLOB '*[^a-z0-9_]*'
    ),
  json_path TEXT NOT NULL
    CHECK(
      typeof(json_path) = 'text'
      AND length(CAST(json_path AS BLOB)) <= 8192
      AND (
        json_path = ''
        OR (
          substr(json_path, 1, 1) = '/'
          AND instr(replace(replace(json_path, '~0', ''), '~1', ''), '~') = 0
        )
      )
    ),
  start_offset INTEGER NOT NULL
    CHECK(typeof(start_offset) = 'integer' AND start_offset >= 0),
  end_offset INTEGER NOT NULL
    CHECK(typeof(end_offset) = 'integer' AND end_offset > start_offset),
  audit_event_id TEXT NOT NULL UNIQUE
    REFERENCES audit_events(id) ON DELETE CASCADE,
  detected_at REAL NOT NULL
    CHECK(
      typeof(detected_at) IN ('real','integer')
      AND detected_at >= 0
      AND detected_at <= 1.7976931348623157e+308
    ),
  FOREIGN KEY(tool_call_id, invocation_id)
    REFERENCES endpoint_tool_calls(id, invocation_id) ON DELETE RESTRICT,
  CHECK(
    (target_type IN ('input','metadata','response_data') AND tool_call_id IS NULL)
    OR (target_type IN ('tool_arguments','tool_result') AND tool_call_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_invocation_sensitive_hits_without_tool
  ON invocation_sensitive_hits(
    invocation_id, target_type, json_path, start_offset, end_offset, detector_type
  )
  WHERE tool_call_id IS NULL;

CREATE UNIQUE INDEX uq_invocation_sensitive_hits_with_tool
  ON invocation_sensitive_hits(
    invocation_id, tool_call_id, target_type, json_path,
    start_offset, end_offset, detector_type
  )
  WHERE tool_call_id IS NOT NULL;

CREATE INDEX idx_invocation_sensitive_hits_admin_sort
  ON invocation_sensitive_hits(
    invocation_id, target_type, tool_call_id, json_path,
    start_offset, end_offset, detector_type, id
  );

CREATE TRIGGER invocation_sensitive_hits_audit_scope_before_insert
BEFORE INSERT ON invocation_sensitive_hits
WHEN NOT EXISTS (
  SELECT 1 FROM audit_events
  WHERE id = NEW.audit_event_id AND invocation_id = NEW.invocation_id
)
BEGIN
  SELECT RAISE(ABORT, 'sensitive hit audit invocation mismatch');
END;

CREATE TRIGGER invocation_sensitive_hits_no_update
BEFORE UPDATE ON invocation_sensitive_hits
BEGIN
  SELECT RAISE(ABORT, 'sensitive hits are append only');
END;

CREATE TRIGGER invocation_sensitive_hits_no_delete
BEFORE DELETE ON invocation_sensitive_hits
WHEN EXISTS (
  SELECT 1 FROM audit_events WHERE id = OLD.audit_event_id
)
BEGIN
  SELECT RAISE(ABORT, 'sensitive hits are append only');
END;
