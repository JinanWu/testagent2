CREATE TABLE published_skill_bundles (
  bundle_id TEXT PRIMARY KEY CHECK(trim(bundle_id)<>''),
  version_id TEXT NOT NULL UNIQUE REFERENCES published_endpoint_versions(id) ON DELETE RESTRICT,
  manifest_reference TEXT NOT NULL UNIQUE CHECK(trim(manifest_reference)<>''),
  manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64 AND manifest_digest NOT GLOB '*[^0-9a-f]*'),
  bundle_hash TEXT NOT NULL CHECK(length(bundle_hash)=64 AND bundle_hash NOT GLOB '*[^0-9a-f]*'),
  total_bytes INTEGER NOT NULL CHECK(typeof(total_bytes)='integer' AND total_bytes>=0 AND total_bytes<=4194304),
  state TEXT NOT NULL CHECK(state IN ('published','reconciled')),
  published_at REAL NOT NULL CHECK(typeof(published_at) IN ('real','integer') AND published_at>=0),
  reconciled_at REAL CHECK(reconciled_at IS NULL OR (typeof(reconciled_at) IN ('real','integer') AND reconciled_at>=published_at))
);

CREATE TABLE published_draft_consumptions (
  draft_id TEXT PRIMARY KEY CHECK(trim(draft_id)<>''),
  endpoint_id TEXT NOT NULL UNIQUE REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  consumed_at REAL NOT NULL CHECK(typeof(consumed_at) IN ('real','integer') AND consumed_at>=0)
);

CREATE TABLE published_endpoint_version_metadata (
  version_id TEXT PRIMARY KEY REFERENCES published_endpoint_versions(id) ON DELETE RESTRICT,
  publication_source TEXT NOT NULL CHECK(publication_source IN ('initial_draft','new_draft','prepared_configuration')),
  prompt_changed INTEGER NOT NULL CHECK(prompt_changed IN (0,1)),
  skills_changed INTEGER NOT NULL CHECK(skills_changed IN (0,1)),
  tools_changed INTEGER NOT NULL CHECK(tools_changed IN (0,1)),
  model_changed INTEGER NOT NULL CHECK(model_changed IN (0,1)),
  docs_changed INTEGER NOT NULL CHECK(docs_changed IN (0,1)),
  CHECK(publication_source<>'initial_draft' OR (prompt_changed=0 AND skills_changed=0 AND tools_changed=0 AND model_changed=0 AND docs_changed=0))
);

CREATE INDEX idx_published_skill_bundles_state_time ON published_skill_bundles(state,published_at);
CREATE INDEX idx_published_draft_consumptions_time ON published_draft_consumptions(consumed_at);

CREATE TRIGGER published_skill_bundles_no_update BEFORE UPDATE ON published_skill_bundles
BEGIN SELECT RAISE(ABORT,'published skill bundles are immutable'); END;
CREATE TRIGGER published_skill_bundles_no_delete BEFORE DELETE ON published_skill_bundles
BEGIN SELECT RAISE(ABORT,'published skill bundles are immutable'); END;
CREATE TRIGGER published_draft_consumptions_no_update BEFORE UPDATE ON published_draft_consumptions
BEGIN SELECT RAISE(ABORT,'published draft consumptions are immutable'); END;
CREATE TRIGGER published_draft_consumptions_no_delete BEFORE DELETE ON published_draft_consumptions
BEGIN SELECT RAISE(ABORT,'published draft consumptions are immutable'); END;
CREATE TRIGGER published_endpoint_version_metadata_no_update BEFORE UPDATE ON published_endpoint_version_metadata
BEGIN SELECT RAISE(ABORT,'published endpoint version metadata is immutable'); END;
CREATE TRIGGER published_endpoint_version_metadata_no_delete BEFORE DELETE ON published_endpoint_version_metadata
BEGIN SELECT RAISE(ABORT,'published endpoint version metadata is immutable'); END;
