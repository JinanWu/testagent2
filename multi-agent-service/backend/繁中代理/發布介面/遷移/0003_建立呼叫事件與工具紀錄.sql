CREATE UNIQUE INDEX uq_endpoint_credentials_id_endpoint
  ON endpoint_credentials(id, endpoint_id);

CREATE TABLE endpoint_invocations (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  endpoint_version_id TEXT NOT NULL,
  credential_id TEXT,
  request_id TEXT NOT NULL UNIQUE CHECK(trim(request_id) <> ''),
  session_id TEXT,
  message_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','rate_limited','invalid_api_key')),
  input_json TEXT NOT NULL CHECK(json_valid(input_json)),
  metadata_json TEXT CHECK(metadata_json IS NULL OR json_valid(metadata_json)),
  output_json TEXT CHECK(output_json IS NULL OR json_valid(output_json)),
  error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
  usage_json TEXT CHECK(usage_json IS NULL OR json_valid(usage_json)),
  metadata_size_bytes INTEGER CHECK(metadata_size_bytes IS NULL OR (typeof(metadata_size_bytes) = 'integer' AND metadata_size_bytes >= 0)),
  metadata_sha256 TEXT,
  latency_ms REAL CHECK(latency_ms IS NULL OR (typeof(latency_ms) IN ('real','integer') AND latency_ms >= 0)),
  pricing_version TEXT,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  completed_at REAL CHECK(completed_at IS NULL OR (typeof(completed_at) IN ('real','integer') AND completed_at >= created_at)),
  FOREIGN KEY(endpoint_version_id, endpoint_id) REFERENCES published_endpoint_versions(id, endpoint_id),
  FOREIGN KEY(credential_id, endpoint_id) REFERENCES endpoint_credentials(id, endpoint_id)
);

CREATE INDEX idx_endpoint_invocations_endpoint_created
  ON endpoint_invocations(endpoint_id, created_at);
CREATE INDEX idx_endpoint_invocations_status_created
  ON endpoint_invocations(status, created_at);
CREATE INDEX idx_endpoint_invocations_credential_created
  ON endpoint_invocations(credential_id, created_at);

CREATE TABLE run_events (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  sequence_number INTEGER NOT NULL CHECK(typeof(sequence_number) = 'integer' AND sequence_number > 0),
  event_type TEXT NOT NULL CHECK(trim(event_type) <> ''),
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json) = 'object'),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  UNIQUE(invocation_id, sequence_number),
  UNIQUE(id, invocation_id)
);

CREATE TABLE endpoint_tool_calls (
  id TEXT PRIMARY KEY,
  invocation_id TEXT NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  run_event_id TEXT,
  sequence_number INTEGER NOT NULL CHECK(typeof(sequence_number) = 'integer' AND sequence_number > 0),
  tool_name TEXT NOT NULL CHECK(trim(tool_name) <> ''),
  arguments_json TEXT NOT NULL CHECK(json_valid(arguments_json) AND json_type(arguments_json) = 'object'),
  outcome TEXT NOT NULL CHECK(outcome IN ('success','error')),
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
  latency_ms REAL CHECK(latency_ms IS NULL OR (typeof(latency_ms) IN ('real','integer') AND latency_ms >= 0)),
  retry_of_tool_call_id TEXT,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  UNIQUE(invocation_id, sequence_number),
  UNIQUE(id, invocation_id),
  FOREIGN KEY(run_event_id, invocation_id) REFERENCES run_events(id, invocation_id),
  FOREIGN KEY(retry_of_tool_call_id, invocation_id) REFERENCES endpoint_tool_calls(id, invocation_id),
  CHECK(
    (outcome = 'success' AND result_json IS NOT NULL AND error_json IS NULL)
    OR (outcome = 'error' AND result_json IS NULL AND error_json IS NOT NULL)
  )
);

CREATE INDEX idx_endpoint_tool_calls_invocation_created
  ON endpoint_tool_calls(invocation_id, created_at);
