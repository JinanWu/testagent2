CREATE TRIGGER endpoint_redactions_require_tombstone
BEFORE INSERT ON endpoint_redactions
WHEN NEW.is_tombstone <> 1
BEGIN
  SELECT RAISE(ABORT, 'redaction must be a tombstone');
END;

CREATE TRIGGER endpoint_redactions_target_before_insert
BEFORE INSERT ON endpoint_redactions
WHEN NOT (
  (NEW.target_type IN ('invocation_input','metadata','output','error')
    AND NEW.target_row_id = NEW.invocation_id)
  OR (NEW.target_type = 'run_event' AND EXISTS (
    SELECT 1 FROM run_events
    WHERE id = NEW.target_row_id AND invocation_id = NEW.invocation_id
  ))
  OR (NEW.target_type IN ('tool_arguments','tool_result','tool_error') AND EXISTS (
    SELECT 1 FROM endpoint_tool_calls
    WHERE id = NEW.target_row_id AND invocation_id = NEW.invocation_id
  ))
)
BEGIN
  SELECT RAISE(ABORT, 'redaction target ownership mismatch');
END;

CREATE TRIGGER endpoint_redactions_no_update
BEFORE UPDATE ON endpoint_redactions
BEGIN
  SELECT RAISE(ABORT, 'redaction tombstones are append only');
END;

CREATE TRIGGER endpoint_redactions_no_delete
BEFORE DELETE ON endpoint_redactions
BEGIN
  SELECT RAISE(ABORT, 'redaction tombstones are append only');
END;

CREATE TRIGGER redacted_invocation_payload_no_update
BEFORE UPDATE OF input_json,metadata_json,output_json,error_json ON endpoint_invocations
WHEN
  (NEW.input_json IS NOT OLD.input_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='invocation_input' AND target_row_id=OLD.id
  )) OR
  (NEW.metadata_json IS NOT OLD.metadata_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='metadata' AND target_row_id=OLD.id
  )) OR
  (NEW.output_json IS NOT OLD.output_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='output' AND target_row_id=OLD.id
  )) OR
  (NEW.error_json IS NOT OLD.error_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE invocation_id=OLD.id
      AND target_type='error' AND target_row_id=OLD.id
  ))
BEGIN
  SELECT RAISE(ABORT, 'redacted invocation payload is immutable');
END;

CREATE TRIGGER redacted_run_event_no_update
BEFORE UPDATE OF id,invocation_id,payload_json ON run_events
WHEN EXISTS (
  SELECT 1 FROM endpoint_redactions
  WHERE target_type='run_event' AND target_row_id=OLD.id
)
BEGIN
  SELECT RAISE(ABORT, 'redacted run event is immutable');
END;

CREATE TRIGGER redacted_tool_call_no_update
BEFORE UPDATE OF id,invocation_id,arguments_json,result_json,error_json ON endpoint_tool_calls
WHEN
  (NEW.id IS NOT OLD.id OR NEW.invocation_id IS NOT OLD.invocation_id) AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id
      AND target_type IN ('tool_arguments','tool_result','tool_error')
  ) OR
  (NEW.arguments_json IS NOT OLD.arguments_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id AND target_type='tool_arguments'
  )) OR
  (NEW.result_json IS NOT OLD.result_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id AND target_type='tool_result'
  )) OR
  (NEW.error_json IS NOT OLD.error_json AND EXISTS (
    SELECT 1 FROM endpoint_redactions WHERE target_row_id=OLD.id AND target_type='tool_error'
  ))
BEGIN
  SELECT RAISE(ABORT, 'redacted tool payload is immutable');
END;

CREATE TRIGGER redacted_run_event_no_delete
BEFORE DELETE ON run_events
WHEN EXISTS (
  SELECT 1 FROM endpoint_redactions
  WHERE target_type='run_event' AND target_row_id=OLD.id
)
BEGIN
  SELECT RAISE(ABORT, 'redacted run event identity is retained');
END;

CREATE TRIGGER redacted_tool_call_no_delete
BEFORE DELETE ON endpoint_tool_calls
WHEN EXISTS (
  SELECT 1 FROM endpoint_redactions
  WHERE target_row_id=OLD.id
    AND target_type IN ('tool_arguments','tool_result','tool_error')
)
BEGIN
  SELECT RAISE(ABORT, 'redacted tool identity is retained');
END;
