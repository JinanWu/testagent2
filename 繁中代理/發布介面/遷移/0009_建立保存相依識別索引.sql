CREATE INDEX idx_run_events_retention_invocation_id
  ON run_events(invocation_id, id);
CREATE INDEX idx_endpoint_tool_calls_retention_invocation_id
  ON endpoint_tool_calls(invocation_id, id);
CREATE INDEX idx_endpoint_redactions_retention_invocation_id
  ON endpoint_redactions(invocation_id, id);
CREATE INDEX idx_audit_events_retention_invocation_id
  ON audit_events(invocation_id, id);
