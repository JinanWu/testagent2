CREATE TABLE service_accounts (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  disabled_at REAL CHECK(disabled_at IS NULL OR (typeof(disabled_at) IN ('real','integer') AND disabled_at >= 0))
);

CREATE TABLE published_endpoints (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  service_account_id TEXT NOT NULL UNIQUE REFERENCES service_accounts(id),
  slug TEXT NOT NULL UNIQUE CHECK(trim(slug) <> ''),
  status TEXT NOT NULL CHECK(status IN ('active','disabled','archived')),
  current_version_id TEXT NULL,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  updated_at REAL NOT NULL CHECK(typeof(updated_at) IN ('real','integer') AND updated_at >= 0),
  FOREIGN KEY(current_version_id, id) REFERENCES published_endpoint_versions(id, endpoint_id)
);

CREATE TABLE published_endpoint_versions (
  id TEXT PRIMARY KEY,
  endpoint_id TEXT NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  version_number INTEGER NOT NULL CHECK(typeof(version_number) = 'integer' AND version_number > 0),
  original_requirement_text TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  allowed_skills_json TEXT NOT NULL,
  allowed_tools_json TEXT NOT NULL,
  tool_schema_snapshot_json TEXT NOT NULL,
  tool_runtime_revision TEXT NOT NULL,
  model_config_snapshot_json TEXT NOT NULL,
  retry_policy_json TEXT NOT NULL,
  skill_bundle_manifest_json TEXT NOT NULL,
  input_schema_json TEXT,
  response_schema_json TEXT NOT NULL,
  schema_changed INTEGER NOT NULL CHECK(typeof(schema_changed) = 'integer' AND schema_changed IN (0, 1)),
  created_by_user_id TEXT NOT NULL,
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  UNIQUE(endpoint_id, version_number),
  UNIQUE(id, endpoint_id),
  CHECK(version_number <> 1 OR schema_changed = 0)
);

CREATE INDEX idx_published_endpoints_owner_status
  ON published_endpoints(owner_user_id, status);

CREATE TRIGGER published_endpoint_versions_no_update
BEFORE UPDATE ON published_endpoint_versions
BEGIN
  SELECT RAISE(ABORT, 'published endpoint versions are immutable');
END;

CREATE TRIGGER published_endpoint_versions_no_delete
BEFORE DELETE ON published_endpoint_versions
BEGIN
  SELECT RAISE(ABORT, 'published endpoint versions are immutable');
END;
