CREATE TABLE endpoint_credentials_0011_empty_guard (
  must_be_empty INTEGER NOT NULL CHECK(must_be_empty = 0)
);

INSERT INTO endpoint_credentials_0011_empty_guard(must_be_empty)
SELECT 1 FROM endpoint_credentials LIMIT 1;

DROP TABLE endpoint_credentials_0011_empty_guard;

CREATE TABLE endpoint_credentials_0011_new (
  id TEXT NOT NULL PRIMARY KEY,
  endpoint_id TEXT NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  name TEXT NOT NULL CHECK(
    typeof(name) = 'text' AND trim(name) <> '' AND length(name) <= 256
    AND instr(lower(name), 'pk_') = 0 AND instr(lower(name), 'sk_') = 0
    AND instr(lower(name), 'sk-') = 0 AND instr(lower(name), 'bearer') = 0
    AND NOT (length(name) = 64 AND lower(name) NOT GLOB '*[^0-9a-f]*')
  ),
  purpose TEXT NOT NULL CHECK(
    typeof(purpose) = 'text' AND trim(purpose) <> '' AND length(purpose) <= 2048
    AND instr(lower(purpose), 'pk_') = 0 AND instr(lower(purpose), 'sk_') = 0
    AND instr(lower(purpose), 'sk-') = 0 AND instr(lower(purpose), 'bearer') = 0
    AND NOT (length(purpose) = 64 AND lower(purpose) NOT GLOB '*[^0-9a-f]*')
  ),
  key_version INTEGER NOT NULL CHECK(typeof(key_version) = 'integer' AND key_version > 0),
  key_nonce BLOB NOT NULL CHECK(typeof(key_nonce) = 'blob' AND length(key_nonce) = 12),
  key_ciphertext BLOB NOT NULL CHECK(typeof(key_ciphertext) = 'blob' AND length(key_ciphertext) = 62),
  key_hash TEXT NOT NULL UNIQUE CHECK(
    typeof(key_hash) = 'text' AND length(key_hash) = 64
    AND key_hash NOT GLOB '*[^0-9a-f]*'
  ),
  key_prefix TEXT NOT NULL CHECK(
    typeof(key_prefix) = 'text' AND length(key_prefix) BETWEEN 1 AND 32
    AND key_prefix NOT GLOB '*[^A-Za-z0-9_-]*'
  ),
  key_last4 TEXT NOT NULL CHECK(
    typeof(key_last4) = 'text' AND length(key_last4) = 4
    AND key_last4 NOT GLOB '*[^A-Za-z0-9_-]*'
  ),
  expires_at REAL NOT NULL CHECK(typeof(expires_at) IN ('real','integer') AND expires_at >= 0),
  last_used_at REAL CHECK(last_used_at IS NULL OR (typeof(last_used_at) IN ('real','integer') AND last_used_at >= 0)),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  updated_at REAL NOT NULL CHECK(typeof(updated_at) IN ('real','integer') AND updated_at >= 0),
  revoked_at REAL CHECK(revoked_at IS NULL OR (typeof(revoked_at) IN ('real','integer') AND revoked_at >= 0)),
  ip_allowlist_json TEXT NOT NULL CHECK(
    typeof(ip_allowlist_json) = 'text'
    AND json_valid(ip_allowlist_json)
    AND json_type(ip_allowlist_json) = 'array'
  ),
  rate_limit_requests INTEGER NOT NULL CHECK(typeof(rate_limit_requests) = 'integer' AND rate_limit_requests BETWEEN 1 AND 10000),
  created_by_user_id TEXT NOT NULL CHECK(
    typeof(created_by_user_id) = 'text'
    AND length(created_by_user_id) BETWEEN 1 AND 128
    AND substr(created_by_user_id,1,1) GLOB '[A-Za-z0-9]'
    AND created_by_user_id NOT GLOB '*[^A-Za-z0-9._:-]*'
    AND instr(lower(created_by_user_id), 'pk_') = 0
    AND instr(lower(created_by_user_id), 'sk_') = 0
    AND instr(lower(created_by_user_id), 'sk-') = 0
    AND instr(lower(created_by_user_id), 'bearer') = 0
    AND NOT (length(created_by_user_id) = 64
             AND lower(created_by_user_id) NOT GLOB '*[^0-9a-f]*')
  ),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(revision) = 'integer' AND revision >= 0),
  UNIQUE(key_version, key_nonce),
  CHECK(expires_at >= created_at),
  CHECK(updated_at >= created_at),
  CHECK(last_used_at IS NULL OR (created_at <= last_used_at AND last_used_at <= updated_at)),
  CHECK(revoked_at IS NULL OR revoked_at >= created_at)
);

DROP TABLE endpoint_credentials;
ALTER TABLE endpoint_credentials_0011_new RENAME TO endpoint_credentials;

CREATE UNIQUE INDEX uq_endpoint_credentials_id_endpoint
  ON endpoint_credentials(id, endpoint_id);

CREATE INDEX idx_endpoint_credentials_endpoint_lifecycle
  ON endpoint_credentials(endpoint_id, revoked_at, expires_at);

CREATE TRIGGER endpoint_credentials_allowlist_insert_check
BEFORE INSERT ON endpoint_credentials
WHEN published_ip_allowlist_valid(NEW.ip_allowlist_json) <> 1
BEGIN
  SELECT RAISE(ABORT, 'credential allowlist violates contract');
END;

CREATE TRIGGER endpoint_credentials_allowlist_update_check
BEFORE UPDATE OF ip_allowlist_json ON endpoint_credentials
WHEN published_ip_allowlist_valid(NEW.ip_allowlist_json) <> 1
BEGIN
  SELECT RAISE(ABORT, 'credential allowlist violates contract');
END;

CREATE TRIGGER finite_endpoint_credentials_insert
BEFORE INSERT ON endpoint_credentials WHEN NOT(NEW.expires_at < 1e999) OR (NEW.last_used_at IS NOT NULL AND NOT(NEW.last_used_at < 1e999)) OR NOT(NEW.created_at < 1e999) OR NOT(NEW.updated_at < 1e999) OR (NEW.revoked_at IS NOT NULL AND NOT(NEW.revoked_at < 1e999))
BEGIN SELECT RAISE(ABORT, 'non-finite credential real'); END;

CREATE TRIGGER finite_endpoint_credentials_update
BEFORE UPDATE ON endpoint_credentials WHEN NOT(NEW.expires_at < 1e999) OR (NEW.last_used_at IS NOT NULL AND NOT(NEW.last_used_at < 1e999)) OR NOT(NEW.created_at < 1e999) OR NOT(NEW.updated_at < 1e999) OR (NEW.revoked_at IS NOT NULL AND NOT(NEW.revoked_at < 1e999))
BEGIN SELECT RAISE(ABORT, 'non-finite credential real'); END;
