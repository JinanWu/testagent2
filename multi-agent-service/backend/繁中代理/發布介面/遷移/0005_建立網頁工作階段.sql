CREATE TABLE web_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL CHECK(trim(user_id) <> ''),
  session_token_hash BLOB NOT NULL UNIQUE CHECK(typeof(session_token_hash) = 'blob' AND length(session_token_hash) > 0),
  csrf_token_hash BLOB NOT NULL CHECK(typeof(csrf_token_hash) = 'blob' AND length(csrf_token_hash) > 0),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0),
  expires_at REAL NOT NULL CHECK(typeof(expires_at) IN ('real','integer') AND expires_at > created_at),
  last_seen_at REAL NOT NULL CHECK(typeof(last_seen_at) IN ('real','integer') AND last_seen_at >= created_at),
  revoked_at REAL CHECK(revoked_at IS NULL OR (typeof(revoked_at) IN ('real','integer') AND revoked_at >= created_at)),
  user_agent_hash BLOB CHECK(user_agent_hash IS NULL OR (typeof(user_agent_hash) = 'blob' AND length(user_agent_hash) > 0))
);

CREATE INDEX idx_web_sessions_user_revoked_expires
  ON web_sessions(user_id, revoked_at, expires_at);
