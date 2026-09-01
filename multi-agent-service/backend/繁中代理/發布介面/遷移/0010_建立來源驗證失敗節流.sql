CREATE TABLE auth_failure_rate_counters (
  client_ip TEXT NOT NULL CHECK(
    typeof(client_ip) = 'text'
    AND length(client_ip) BETWEEN 2 AND 45
    AND trim(client_ip) = client_ip
  ),
  endpoint_slug TEXT NOT NULL CHECK(
    typeof(endpoint_slug) = 'text'
    AND length(endpoint_slug) BETWEEN 1 AND 128
    AND trim(endpoint_slug) = endpoint_slug
  ),
  window_start INTEGER NOT NULL CHECK(
    typeof(window_start) = 'integer' AND window_start >= 0
  ),
  failure_count INTEGER NOT NULL CHECK(
    typeof(failure_count) = 'integer'
    AND failure_count >= 0
    AND failure_count <= 9223372036854775807
  ),
  updated_at REAL NOT NULL CHECK(
    typeof(updated_at) IN ('real','integer')
    AND updated_at >= 0
    AND updated_at <= 253402300799
  ),
  PRIMARY KEY(client_ip, endpoint_slug, window_start)
);

CREATE INDEX idx_auth_failure_rate_counters_window_start
  ON auth_failure_rate_counters(window_start);
