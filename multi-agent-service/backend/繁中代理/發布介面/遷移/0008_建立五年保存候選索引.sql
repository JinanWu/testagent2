CREATE INDEX idx_endpoint_invocations_retention_candidates
  ON endpoint_invocations(created_at, id);
