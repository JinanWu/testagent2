CREATE TABLE published_session_turn_pairs (
  endpoint_id TEXT NOT NULL,
  service_account_id TEXT NOT NULL,
  session_id TEXT NOT NULL
    CHECK(length(CAST(session_id AS BLOB)) BETWEEN 1 AND 128 AND trim(session_id) = session_id),
  sequence_number INTEGER NOT NULL
    CHECK(typeof(sequence_number) = 'integer' AND sequence_number > 0),
  endpoint_version_id TEXT NOT NULL,
  user_message_json TEXT NOT NULL
    CHECK(json_valid(user_message_json) AND json_type(user_message_json) = 'object'),
  assistant_message_json TEXT NOT NULL
    CHECK(json_valid(assistant_message_json) AND json_type(assistant_message_json) = 'object'),
  pair_size_bytes INTEGER NOT NULL
    CHECK(typeof(pair_size_bytes) = 'integer' AND pair_size_bytes > 0 AND pair_size_bytes <= 262144),
  token_count INTEGER NOT NULL
    CHECK(typeof(token_count) = 'integer' AND token_count > 0 AND token_count <= 32768),
  created_at REAL NOT NULL
    CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  PRIMARY KEY(endpoint_id, service_account_id, session_id, sequence_number),
  FOREIGN KEY(endpoint_id)
    REFERENCES published_endpoints(id) ON DELETE CASCADE,
  FOREIGN KEY(service_account_id)
    REFERENCES service_accounts(id) ON DELETE RESTRICT,
  FOREIGN KEY(endpoint_version_id, endpoint_id)
    REFERENCES published_endpoint_versions(id, endpoint_id) ON DELETE CASCADE
);

CREATE INDEX idx_published_session_turn_pairs_latest
  ON published_session_turn_pairs(endpoint_id, service_account_id, session_id, sequence_number DESC);

CREATE TRIGGER published_session_turn_pairs_scope_before_insert
BEFORE INSERT ON published_session_turn_pairs
WHEN NOT EXISTS (
  SELECT 1 FROM published_endpoints
  WHERE id=NEW.endpoint_id AND service_account_id=NEW.service_account_id
)
BEGIN
  SELECT RAISE(ABORT, 'published session scope mismatch');
END;

CREATE TRIGGER published_session_turn_pairs_no_update
BEFORE UPDATE ON published_session_turn_pairs
BEGIN
  SELECT RAISE(ABORT, 'published session history is append only');
END;
