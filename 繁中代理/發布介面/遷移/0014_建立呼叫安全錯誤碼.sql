CREATE TABLE endpoint_invocation_safe_errors (
  invocation_id TEXT PRIMARY KEY
    REFERENCES endpoint_invocations(id) ON DELETE CASCADE,
  error_code TEXT NOT NULL
    CHECK(
      typeof(error_code) = 'text'
      AND length(error_code) BETWEEN 1 AND 128
      AND substr(error_code, 1, 1) GLOB '[a-z]'
      AND error_code NOT GLOB '*[^a-z0-9_.-]*'
    )
);

CREATE INDEX idx_endpoint_invocation_safe_errors_code
  ON endpoint_invocation_safe_errors(error_code, invocation_id);

INSERT INTO endpoint_invocation_safe_errors(invocation_id, error_code)
SELECT id, json_extract(error_json, '$.code')
FROM endpoint_invocations
WHERE error_json IS NOT NULL
  AND json_valid(error_json)
  AND json_type(error_json, '$.code') = 'text'
  AND length(json_extract(error_json, '$.code')) BETWEEN 1 AND 128
  AND substr(json_extract(error_json, '$.code'), 1, 1) GLOB '[a-z]'
  AND json_extract(error_json, '$.code') NOT GLOB '*[^a-z0-9_.-]*';

CREATE TRIGGER endpoint_invocation_safe_error_after_insert
AFTER INSERT ON endpoint_invocations
WHEN NEW.error_json IS NOT NULL
  AND json_valid(NEW.error_json)
  AND json_type(NEW.error_json, '$.code') = 'text'
  AND length(json_extract(NEW.error_json, '$.code')) BETWEEN 1 AND 128
  AND substr(json_extract(NEW.error_json, '$.code'), 1, 1) GLOB '[a-z]'
  AND json_extract(NEW.error_json, '$.code') NOT GLOB '*[^a-z0-9_.-]*'
BEGIN
  INSERT INTO endpoint_invocation_safe_errors(invocation_id, error_code)
  VALUES(NEW.id, json_extract(NEW.error_json, '$.code'));
END;

CREATE TRIGGER endpoint_invocation_safe_error_after_update
AFTER UPDATE OF error_json ON endpoint_invocations
BEGIN
  DELETE FROM endpoint_invocation_safe_errors WHERE invocation_id = NEW.id;
  INSERT INTO endpoint_invocation_safe_errors(invocation_id, error_code)
  SELECT NEW.id, json_extract(NEW.error_json, '$.code')
  WHERE NEW.error_json IS NOT NULL
    AND json_valid(NEW.error_json)
    AND json_type(NEW.error_json, '$.code') = 'text'
    AND length(json_extract(NEW.error_json, '$.code')) BETWEEN 1 AND 128
    AND substr(json_extract(NEW.error_json, '$.code'), 1, 1) GLOB '[a-z]'
    AND json_extract(NEW.error_json, '$.code') NOT GLOB '*[^a-z0-9_.-]*';
END;
