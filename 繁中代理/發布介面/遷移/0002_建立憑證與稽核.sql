ALTER TABLE published_endpoints ADD COLUMN rate_limit_requests INTEGER NOT NULL DEFAULT 60;
ALTER TABLE published_endpoints ADD COLUMN rate_limit_window_seconds INTEGER NOT NULL DEFAULT 60;

CREATE TRIGGER published_endpoints_rate_limit_positive_before_insert
BEFORE INSERT ON published_endpoints
WHEN typeof(NEW.rate_limit_requests) <> 'integer'
  OR NEW.rate_limit_requests <= 0
  OR typeof(NEW.rate_limit_window_seconds) <> 'integer'
  OR NEW.rate_limit_window_seconds <= 0
BEGIN
  SELECT RAISE(ABORT, 'published endpoint rate limits must be positive integers');
END;

CREATE TRIGGER published_endpoints_rate_limit_positive_before_update
BEFORE UPDATE OF rate_limit_requests, rate_limit_window_seconds ON published_endpoints
WHEN typeof(NEW.rate_limit_requests) <> 'integer'
  OR NEW.rate_limit_requests <= 0
  OR typeof(NEW.rate_limit_window_seconds) <> 'integer'
  OR NEW.rate_limit_window_seconds <= 0
BEGIN
  SELECT RAISE(ABORT, 'published endpoint rate limits must be positive integers');
END;

CREATE TABLE endpoint_credentials (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  name TEXT NOT NULL CHECK(trim(name) <> '' AND length(name) <= 120),
  purpose TEXT NOT NULL CHECK(trim(purpose) <> '' AND length(purpose) <= 1000),
  secret_ciphertext BLOB NOT NULL CHECK(typeof(secret_ciphertext) = 'blob' AND length(secret_ciphertext) > 0),
  encryption_key_id TEXT NOT NULL CHECK(trim(encryption_key_id) <> ''),
  verification_hash BLOB NOT NULL CHECK(typeof(verification_hash) = 'blob' AND length(verification_hash) > 0),
  key_prefix TEXT NOT NULL CHECK(trim(key_prefix) <> '' AND length(key_prefix) <= 32),
  key_last4 TEXT NOT NULL CHECK(length(key_last4) = 4),
  expires_at REAL NOT NULL CHECK(typeof(expires_at) IN ('real','integer') AND expires_at >= 0),
  last_used_at REAL CHECK(last_used_at IS NULL OR (typeof(last_used_at) IN ('real','integer') AND last_used_at >= 0)),
  revoked_at REAL CHECK(revoked_at IS NULL OR (typeof(revoked_at) IN ('real','integer') AND revoked_at >= 0)),
  inactive_disabled_at REAL CHECK(inactive_disabled_at IS NULL OR (typeof(inactive_disabled_at) IN ('real','integer') AND inactive_disabled_at >= 0)),
  ip_allowlist_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(ip_allowlist_json) AND json_type(ip_allowlist_json) = 'array'),
  rate_limit_requests INTEGER NOT NULL CHECK(typeof(rate_limit_requests) = 'integer' AND rate_limit_requests > 0),
  rate_limit_window_seconds INTEGER NOT NULL CHECK(typeof(rate_limit_window_seconds) = 'integer' AND rate_limit_window_seconds > 0),
  created_by_user_id TEXT NOT NULL CHECK(trim(created_by_user_id) <> ''),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  UNIQUE(endpoint_id, verification_hash)
);

CREATE INDEX idx_endpoint_credentials_endpoint_list ON endpoint_credentials(endpoint_id, revoked_at, inactive_disabled_at, expires_at);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  actor_type TEXT NOT NULL CHECK(actor_type IN ('user','admin','service_account','system')),
  actor_id TEXT CHECK(actor_type = 'system' OR (actor_id IS NOT NULL AND trim(actor_id) <> '')),
  action TEXT NOT NULL CHECK(trim(action) <> ''),
  target_type TEXT NOT NULL CHECK(trim(target_type) <> ''),
  target_id TEXT NOT NULL CHECK(trim(target_id) <> ''),
  endpoint_id TEXT REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  request_id TEXT,
  metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json) = 'object'),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0)
);

CREATE INDEX idx_audit_events_target_time ON audit_events(target_type, target_id, created_at);
CREATE INDEX idx_audit_events_endpoint_time ON audit_events(endpoint_id, created_at);

CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END;

CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END;
