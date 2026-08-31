CREATE TABLE redaction_idempotency_commands (
  principal_id TEXT NOT NULL
    CHECK(
      typeof(principal_id) = 'text'
      AND length(CAST(principal_id AS BLOB)) BETWEEN 1 AND 128
      AND principal_id GLOB '[A-Za-z0-9]*'
      AND principal_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  idempotency_key TEXT NOT NULL
    CHECK(
      typeof(idempotency_key) = 'text'
      AND length(CAST(idempotency_key AS BLOB)) BETWEEN 1 AND 128
      AND idempotency_key GLOB '[A-Za-z0-9]*'
      AND idempotency_key NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  request_fingerprint TEXT NOT NULL
    CHECK(
      typeof(request_fingerprint) = 'text'
      AND length(request_fingerprint) = 64
      AND request_fingerprint = lower(request_fingerprint)
      AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
  redaction_id TEXT NOT NULL UNIQUE
    CHECK(
      typeof(redaction_id) = 'text'
      AND length(CAST(redaction_id AS BLOB)) BETWEEN 1 AND 128
      AND redaction_id GLOB '[A-Za-z0-9]*'
      AND redaction_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  audit_event_id TEXT NOT NULL UNIQUE
    CHECK(
      typeof(audit_event_id) = 'text'
      AND length(CAST(audit_event_id AS BLOB)) BETWEEN 1 AND 128
      AND audit_event_id GLOB '[A-Za-z0-9]*'
      AND audit_event_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  request_id TEXT NOT NULL UNIQUE
    CHECK(
      typeof(request_id) = 'text'
      AND length(CAST(request_id AS BLOB)) BETWEEN 1 AND 128
      AND request_id GLOB '[A-Za-z0-9]*'
      AND request_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  endpoint_id TEXT NOT NULL
    REFERENCES published_endpoints(id) ON DELETE RESTRICT
    CHECK(
      typeof(endpoint_id) = 'text'
      AND length(CAST(endpoint_id AS BLOB)) BETWEEN 1 AND 128
      AND endpoint_id GLOB '[A-Za-z0-9]*'
      AND endpoint_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  invocation_id TEXT NOT NULL
    REFERENCES endpoint_invocations(id) ON DELETE CASCADE
    CHECK(
      typeof(invocation_id) = 'text'
      AND length(CAST(invocation_id AS BLOB)) BETWEEN 1 AND 128
      AND invocation_id GLOB '[A-Za-z0-9]*'
      AND invocation_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  target_type TEXT NOT NULL
    CHECK(target_type IN (
      'invocation_input',
      'metadata',
      'output',
      'error',
      'run_event',
      'tool_arguments',
      'tool_result',
      'tool_error'
    )),
  target_row_id TEXT NOT NULL
    CHECK(
      typeof(target_row_id) = 'text'
      AND length(CAST(target_row_id AS BLOB)) BETWEEN 1 AND 128
      AND target_row_id GLOB '[A-Za-z0-9]*'
      AND target_row_id NOT GLOB '*[^-A-Za-z0-9._:]*'
    ),
  json_path TEXT NOT NULL
    CHECK(
      typeof(json_path) = 'text'
      AND length(CAST(json_path AS BLOB)) <= 4096
      AND (
        json_path = ''
        OR (
          substr(json_path, 1, 1) = '/'
          AND instr(replace(replace(json_path, '~0', ''), '~1', ''), '~') = 0
        )
      )
    ),
  reason TEXT NOT NULL
    CHECK(
      typeof(reason) = 'text'
      AND length(CAST(reason AS BLOB)) BETWEEN 1 AND 256
      AND trim(reason) <> ''
    ),
  first_seen_at REAL NOT NULL
    CHECK(
      typeof(first_seen_at) IN ('real','integer')
      AND first_seen_at >= 0
      AND first_seen_at <= 253402300799
    ),
  PRIMARY KEY(principal_id, idempotency_key)
);
