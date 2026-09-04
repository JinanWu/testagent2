"""建立完整產品 PostgreSQL schema。

Revision ID: 0001_full_product_schema
Revises:
"""
from __future__ import annotations

from alembic import op

revision = "0001_full_product_schema"
down_revision = None
branch_labels = None
depends_on = None

DOMAIN_TABLES = {
    "users": "users", "user_settings": "user_settings", "cli_auth_sessions": "auth_sessions",
    "web_sessions": "web_sessions", "sessions": "sessions", "messages": "messages",
    "usage_events": "session_usage_events", "session_lineage": "session_lineage",
    "compression_leases": "compression_leases", "user_memories": "user_memories",
    "user_skills": "user_skills", "skill_usage": "skill_usage", "skill_events": "skill_usage_events",
    "service_accounts": "service_accounts", "endpoints": "published_endpoints",
    "endpoint_versions": "published_endpoint_versions", "credentials": "endpoint_credentials",
    "invocations": "endpoint_invocations", "run_events": "run_events", "tool_calls": "endpoint_tool_calls",
    "published_sessions": "published_session_turn_pairs", "bundle_receipts": "published_skill_bundles",
    "published_draft_consumptions": "published_draft_consumptions",
    "published_endpoint_version_metadata": "published_endpoint_version_metadata",
    "endpoint_invocation_safe_errors": "endpoint_invocation_safe_errors", "audit_events": "audit_events",
    "sensitive_hits": "invocation_sensitive_hits", "redaction_commands": "endpoint_redactions",
    "tombstones": "redaction_tombstones", "retention": "retention_policies",
    "rate_limits": "rate_limit_counters", "auth_failure_rate_counters": "auth_failure_rate_counters",
    "idempotency_operations": "redaction_idempotency_commands",
}

DDL = [
"""CREATE TABLE users (
 id text PRIMARY KEY, username text NOT NULL UNIQUE CHECK (btrim(username) <> ''), display_name text,
 password_hash text, auth_provider text NOT NULL DEFAULT 'local', external_subject text,
 roles jsonb NOT NULL DEFAULT '["user"]'::jsonb CHECK (jsonb_typeof(roles)='array'), disabled boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (auth_provider, external_subject)
)""",
"""CREATE TABLE user_settings (
 user_id text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, enabled_tools jsonb, enabled_skills jsonb,
 skill_roots jsonb, allowed_workdirs jsonb, memory_home text, settings jsonb NOT NULL DEFAULT '{}'::jsonb,
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK (enabled_tools IS NULL OR jsonb_typeof(enabled_tools)='array'), CHECK (enabled_skills IS NULL OR jsonb_typeof(enabled_skills)='array'),
 CHECK (skill_roots IS NULL OR jsonb_typeof(skill_roots)='array'), CHECK (allowed_workdirs IS NULL OR jsonb_typeof(allowed_workdirs)='array'),
 CHECK (jsonb_typeof(settings)='object')
)""",
"""CREATE TABLE auth_sessions (
 token_hash text PRIMARY KEY CHECK (length(token_hash)>=32), user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz, last_used_at timestamptz, revoked_at timestamptz,
 CHECK (expires_at IS NULL OR expires_at > created_at), CHECK (revoked_at IS NULL OR revoked_at >= created_at)
)""",
"""CREATE TABLE web_sessions (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 session_token_hash bytea NOT NULL UNIQUE CHECK (octet_length(session_token_hash)>0), csrf_token_hash bytea NOT NULL CHECK (octet_length(csrf_token_hash)>0),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL DEFAULT now(),
 revoked_at timestamptz, user_agent_hash bytea, CHECK (expires_at > created_at), CHECK (last_seen_at >= created_at)
)""",
"""CREATE TABLE sessions (
 id text PRIMARY KEY, source text NOT NULL DEFAULT 'cli', user_id text REFERENCES users(id) ON DELETE SET NULL, model text,
 model_config jsonb, system_prompt text, parent_session_id text REFERENCES sessions(id) ON DELETE SET NULL,
 title text, end_reason text, compressed_from_session_id text REFERENCES sessions(id) ON DELETE SET NULL,
 prompt_tokens bigint NOT NULL DEFAULT 0 CHECK(prompt_tokens>=0), input_tokens bigint NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
 output_tokens bigint NOT NULL DEFAULT 0 CHECK(output_tokens>=0), cache_read_tokens bigint NOT NULL DEFAULT 0 CHECK(cache_read_tokens>=0),
 cache_write_tokens bigint NOT NULL DEFAULT 0 CHECK(cache_write_tokens>=0), reasoning_tokens bigint NOT NULL DEFAULT 0 CHECK(reasoning_tokens>=0),
 message_count bigint NOT NULL DEFAULT 0 CHECK(message_count>=0), tool_call_count bigint NOT NULL DEFAULT 0 CHECK(tool_call_count>=0),
 api_call_count bigint NOT NULL DEFAULT 0 CHECK(api_call_count>=0), compression_count bigint NOT NULL DEFAULT 0 CHECK(compression_count>=0),
 cwd text, billing_provider text, billing_base_url text, billing_mode text, estimated_cost_usd numeric(20,8), actual_cost_usd numeric(20,8),
 cost_status text, cost_source text, pricing_version text, handoff_state text, handoff_platform text, handoff_error text,
 rewind_count integer NOT NULL DEFAULT 0 CHECK(rewind_count>=0), archived boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, ended_at timestamptz,
 CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at>=started_at)
)""",
"""CREATE TABLE messages (
 id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, session_id text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 message_index integer NOT NULL CHECK(message_index>=0), role text NOT NULL CHECK(role IN ('system','user','assistant','tool')),
 content text, content_json jsonb NOT NULL DEFAULT '{}'::jsonb, tool_call_id text, tool_calls jsonb, tool_name text,
 token_count bigint CHECK(token_count IS NULL OR token_count>=0), finish_reason text, reasoning text, reasoning_content text,
 reasoning_details jsonb, codex_reasoning_items jsonb, codex_message_items jsonb, platform_message_id text,
 observed boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
 timestamp timestamptz
)""",
"""CREATE TABLE session_usage_events (
 id text PRIMARY KEY, session_id text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, user_id text REFERENCES users(id) ON DELETE SET NULL,
 model text, prompt_tokens bigint NOT NULL DEFAULT 0 CHECK(prompt_tokens>=0), input_tokens bigint NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
 output_tokens bigint NOT NULL DEFAULT 0 CHECK(output_tokens>=0), cache_read_tokens bigint NOT NULL DEFAULT 0 CHECK(cache_read_tokens>=0),
 cache_write_tokens bigint NOT NULL DEFAULT 0 CHECK(cache_write_tokens>=0), reasoning_tokens bigint NOT NULL DEFAULT 0 CHECK(reasoning_tokens>=0),
 estimated_cost_usd numeric(20,8), actual_cost_usd numeric(20,8), metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), CHECK(jsonb_typeof(metadata)='object')
)""",
"""CREATE TABLE session_lineage (
 parent_session_id text NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, child_session_id text NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
 relation text NOT NULL CHECK(relation IN ('fork','compression','rewind','handoff')), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(parent_session_id,child_session_id), CHECK(parent_session_id<>child_session_id)
)""",
"""CREATE TABLE compression_leases (
 session_id text PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE, holder text NOT NULL CHECK(btrim(holder)<>''),
 acquired_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL, CHECK(expires_at>acquired_at)
)""",
"""CREATE TABLE user_memories (
 id text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, namespace text NOT NULL DEFAULT 'default',
 memory_key text NOT NULL, content text NOT NULL, content_json jsonb, source_session_id text REFERENCES sessions(id) ON DELETE SET NULL,
 metadata jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,namespace,memory_key), CHECK(btrim(memory_key)<>''), CHECK(jsonb_typeof(metadata)='object')
)""",
"""CREATE TABLE user_skills (
 skill_id text NOT NULL, user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, name text NOT NULL, description text,
 content text NOT NULL, frontmatter jsonb NOT NULL DEFAULT '{}'::jsonb, content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,skill_id), UNIQUE(user_id,name),
 CHECK(btrim(name)<>''), CHECK(jsonb_typeof(frontmatter)='object')
)""",
"""CREATE TABLE skill_usage (
 user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, skill_id text NOT NULL, use_count bigint NOT NULL DEFAULT 0 CHECK(use_count>=0),
 last_used_at timestamptz, state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','stale','archived')), pinned boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,skill_id),
 FOREIGN KEY(user_id,skill_id) REFERENCES user_skills(user_id,skill_id) ON DELETE CASCADE
)""",
"""CREATE TABLE skill_usage_events (
 id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id text NOT NULL, skill_id text NOT NULL,
 session_id text REFERENCES sessions(id) ON DELETE SET NULL, used_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 FOREIGN KEY(user_id,skill_id) REFERENCES user_skills(user_id,skill_id) ON DELETE CASCADE, CHECK(jsonb_typeof(metadata)='object')
)""",
"""CREATE TABLE service_accounts (
 id text PRIMARY KEY, owner_user_id text REFERENCES users(id) ON DELETE RESTRICT, created_at timestamptz NOT NULL DEFAULT now(), disabled_at timestamptz,
 CHECK(disabled_at IS NULL OR disabled_at>=created_at)
)""",
"""CREATE TABLE published_endpoints (
 id text PRIMARY KEY, owner_user_id text NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 service_account_id text NOT NULL UNIQUE REFERENCES service_accounts(id) ON DELETE RESTRICT, slug text NOT NULL UNIQUE CHECK(btrim(slug)<>''),
 status text NOT NULL CHECK(status IN ('active','disabled','archived')), current_version_id text,
 rate_limit_requests integer NOT NULL DEFAULT 60 CHECK(rate_limit_requests>0),
 rate_limit_window_seconds integer NOT NULL DEFAULT 60 CHECK(rate_limit_window_seconds>0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
)""",
"""CREATE TABLE published_endpoint_versions (
 id text PRIMARY KEY, endpoint_id text NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT, version_number integer NOT NULL CHECK(version_number>0),
 original_requirement_text text NOT NULL, system_prompt text NOT NULL, allowed_skills jsonb NOT NULL, allowed_tools jsonb NOT NULL,
 tool_schema_snapshot jsonb NOT NULL, tool_runtime_revision text NOT NULL, model_config_snapshot jsonb NOT NULL, retry_policy jsonb NOT NULL,
 skill_bundle_manifest jsonb NOT NULL, input_schema jsonb, response_schema jsonb NOT NULL, schema_changed boolean NOT NULL DEFAULT false,
 created_by_user_id text NOT NULL REFERENCES users(id) ON DELETE RESTRICT, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(endpoint_id,version_number), UNIQUE(id,endpoint_id), CHECK(version_number<>1 OR schema_changed=false)
)""",
"""ALTER TABLE published_endpoints ADD CONSTRAINT published_endpoints_current_version_fk
 FOREIGN KEY(current_version_id,id) REFERENCES published_endpoint_versions(id,endpoint_id) DEFERRABLE INITIALLY DEFERRED""",
"""CREATE TABLE endpoint_credentials (
 id text PRIMARY KEY, endpoint_id text NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT, name text NOT NULL CHECK(length(name)<=256 AND btrim(name)<>''),
 purpose text NOT NULL CHECK(length(purpose)<=2048 AND btrim(purpose)<>''), key_version integer NOT NULL CHECK(key_version>0),
 key_nonce bytea NOT NULL CHECK(octet_length(key_nonce)=12), key_ciphertext bytea NOT NULL CHECK(octet_length(key_ciphertext)>0),
 key_hash text NOT NULL UNIQUE CHECK(key_hash ~ '^[0-9a-f]{64}$'), key_prefix text NOT NULL CHECK(length(key_prefix) BETWEEN 1 AND 32),
 key_last4 text NOT NULL CHECK(length(key_last4)=4), expires_at timestamptz NOT NULL, last_used_at timestamptz, revoked_at timestamptz,
 inactive_disabled_at timestamptz, ip_allowlist jsonb NOT NULL DEFAULT '[]'::jsonb, rate_limit_requests integer NOT NULL CHECK(rate_limit_requests>0),
 rate_limit_window_seconds integer NOT NULL CHECK(rate_limit_window_seconds>0), created_by_user_id text NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,endpoint_id), CHECK(jsonb_typeof(ip_allowlist)='array')
)""",
"""CREATE TABLE endpoint_invocations (
 id text PRIMARY KEY, endpoint_id text NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT, endpoint_version_id text NOT NULL,
 credential_id text, request_id text NOT NULL UNIQUE CHECK(btrim(request_id)<>''), session_id text, message_id text,
 status text NOT NULL CHECK(status IN ('pending','running','succeeded','failed','rate_limited','invalid_api_key')),
 input jsonb NOT NULL, metadata jsonb, output jsonb, error jsonb, usage jsonb, metadata_size_bytes bigint CHECK(metadata_size_bytes IS NULL OR metadata_size_bytes>=0),
 metadata_sha256 text, latency_ms numeric CHECK(latency_ms IS NULL OR latency_ms>=0), pricing_version text,
 created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 FOREIGN KEY(endpoint_version_id,endpoint_id) REFERENCES published_endpoint_versions(id,endpoint_id) ON DELETE RESTRICT,
 FOREIGN KEY(credential_id,endpoint_id) REFERENCES endpoint_credentials(id,endpoint_id) ON DELETE RESTRICT,
 CHECK(completed_at IS NULL OR completed_at>=created_at)
)""",
"""CREATE TABLE run_events (
 id text PRIMARY KEY, invocation_id text NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT, sequence_number integer NOT NULL CHECK(sequence_number>0),
 event_type text NOT NULL CHECK(btrim(event_type)<>''), payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(invocation_id,sequence_number), UNIQUE(id,invocation_id)
)""",
"""CREATE TABLE endpoint_tool_calls (
 id text PRIMARY KEY, invocation_id text NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT, run_event_id text,
 sequence_number integer NOT NULL CHECK(sequence_number>0), tool_name text NOT NULL CHECK(btrim(tool_name)<>''), arguments jsonb NOT NULL,
 outcome text NOT NULL CHECK(outcome IN ('success','error')), result jsonb, error jsonb, latency_ms numeric CHECK(latency_ms IS NULL OR latency_ms>=0),
 retry_of_tool_call_id text, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(invocation_id,sequence_number), UNIQUE(id,invocation_id),
 FOREIGN KEY(run_event_id,invocation_id) REFERENCES run_events(id,invocation_id) ON DELETE RESTRICT,
 FOREIGN KEY(retry_of_tool_call_id,invocation_id) REFERENCES endpoint_tool_calls(id,invocation_id) ON DELETE RESTRICT,
 CHECK((outcome='success' AND result IS NOT NULL AND error IS NULL) OR (outcome='error' AND result IS NULL AND error IS NOT NULL))
)""",
"""CREATE TABLE published_session_turn_pairs (
 endpoint_id text NOT NULL REFERENCES published_endpoints(id) ON DELETE CASCADE, service_account_id text NOT NULL REFERENCES service_accounts(id) ON DELETE RESTRICT,
 session_id text NOT NULL CHECK(length(session_id) BETWEEN 1 AND 128 AND btrim(session_id)=session_id), sequence_number integer NOT NULL CHECK(sequence_number>0),
 endpoint_version_id text NOT NULL, user_message jsonb NOT NULL CHECK(jsonb_typeof(user_message)='object'),
 assistant_message jsonb NOT NULL CHECK(jsonb_typeof(assistant_message)='object'), pair_size_bytes integer NOT NULL CHECK(pair_size_bytes BETWEEN 1 AND 262144),
 token_count integer NOT NULL CHECK(token_count BETWEEN 1 AND 32768), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(endpoint_id,service_account_id,session_id,sequence_number),
 FOREIGN KEY(endpoint_version_id,endpoint_id) REFERENCES published_endpoint_versions(id,endpoint_id) ON DELETE CASCADE
)""",
"""CREATE TABLE published_skill_bundles (
 bundle_id text PRIMARY KEY CHECK(btrim(bundle_id)<>''), version_id text NOT NULL UNIQUE REFERENCES published_endpoint_versions(id) ON DELETE RESTRICT,
 manifest_reference text NOT NULL UNIQUE CHECK(btrim(manifest_reference)<>''), manifest_digest text NOT NULL CHECK(manifest_digest ~ '^[0-9a-f]{64}$'),
 bundle_hash text NOT NULL CHECK(bundle_hash ~ '^[0-9a-f]{64}$'), total_bytes integer NOT NULL CHECK(total_bytes BETWEEN 0 AND 4194304),
 state text NOT NULL CHECK(state IN ('published','reconciled')), published_at timestamptz NOT NULL DEFAULT now(), reconciled_at timestamptz,
 CHECK(reconciled_at IS NULL OR reconciled_at>=published_at)
)""",
"""CREATE TABLE published_draft_consumptions (
 draft_id text PRIMARY KEY CHECK(btrim(draft_id)<>''), endpoint_id text NOT NULL UNIQUE REFERENCES published_endpoints(id) ON DELETE RESTRICT,
 consumed_at timestamptz NOT NULL DEFAULT now()
)""",
"""CREATE TABLE published_endpoint_version_metadata (
 version_id text PRIMARY KEY REFERENCES published_endpoint_versions(id) ON DELETE RESTRICT,
 publication_source text NOT NULL CHECK(publication_source IN ('initial_draft','new_draft','prepared_configuration')),
 prompt_changed boolean NOT NULL, skills_changed boolean NOT NULL, tools_changed boolean NOT NULL, model_changed boolean NOT NULL, docs_changed boolean NOT NULL,
 CHECK(publication_source<>'initial_draft' OR NOT(prompt_changed OR skills_changed OR tools_changed OR model_changed OR docs_changed))
)""",
"""CREATE TABLE endpoint_invocation_safe_errors (
 invocation_id text PRIMARY KEY REFERENCES endpoint_invocations(id) ON DELETE CASCADE,
 error_code text NOT NULL CHECK(error_code ~ '^[a-z][a-z0-9_.-]{0,127}$')
)""",
"""CREATE TABLE audit_events (
 id text PRIMARY KEY, event_id text NOT NULL UNIQUE, occurred_at timestamptz NOT NULL, action text NOT NULL CHECK(btrim(action)<>''),
 outcome text NOT NULL CHECK(outcome IN ('success','denied','failed','legacy_unknown')), actor_type text NOT NULL CHECK(actor_type IN ('user','admin','service_account','system')),
 actor_id text, resource_type text NOT NULL, resource_id text NOT NULL, request_id text, endpoint_id text REFERENCES published_endpoints(id) ON DELETE RESTRICT,
 invocation_id text REFERENCES endpoint_invocations(id) ON DELETE RESTRICT, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), CHECK(actor_type='system' OR actor_id IS NOT NULL), CHECK(jsonb_typeof(metadata)='object')
)""",
"""CREATE TABLE invocation_sensitive_hits (
 id text PRIMARY KEY, invocation_id text NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT, tool_call_id text,
 target_type text NOT NULL CHECK(target_type IN ('input','metadata','response_data','tool_arguments','tool_result')), detector_type text NOT NULL CHECK(detector_type ~ '^[a-z][a-z0-9_]{0,127}$'),
 json_path text NOT NULL, start_offset integer NOT NULL CHECK(start_offset>=0), end_offset integer NOT NULL CHECK(end_offset>start_offset),
 audit_event_id text NOT NULL UNIQUE REFERENCES audit_events(id) ON DELETE CASCADE, detected_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tool_call_id,invocation_id) REFERENCES endpoint_tool_calls(id,invocation_id) ON DELETE RESTRICT,
 CHECK((target_type IN ('input','metadata','response_data') AND tool_call_id IS NULL) OR (target_type IN ('tool_arguments','tool_result') AND tool_call_id IS NOT NULL))
)""",
"""CREATE TABLE endpoint_redactions (
 id text PRIMARY KEY, invocation_id text NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
 target_type text NOT NULL CHECK(target_type IN ('invocation_input','metadata','output','error','run_event','tool_arguments','tool_result','tool_error')),
 target_row_id text NOT NULL, json_path text NOT NULL DEFAULT '', original_sha256 text NOT NULL CHECK(original_sha256 ~ '^[0-9a-f]{64}$'),
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000), actor_type text NOT NULL CHECK(actor_type IN ('user','admin','system')), actor_id text,
 audit_event_id text NOT NULL REFERENCES audit_events(id) ON DELETE RESTRICT, is_tombstone boolean NOT NULL DEFAULT true CHECK(is_tombstone),
 redacted_at timestamptz NOT NULL DEFAULT now(), UNIQUE(target_type,target_row_id,json_path), CHECK(actor_type='system' OR actor_id IS NOT NULL)
)""",
"""CREATE TABLE redaction_tombstones (
 redaction_id text PRIMARY KEY REFERENCES endpoint_redactions(id) ON DELETE RESTRICT, invocation_id text NOT NULL REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
 target_identity_sha256 text NOT NULL UNIQUE CHECK(target_identity_sha256 ~ '^[0-9a-f]{64}$'), retained_until timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), CHECK(retained_until>=created_at)
)""",
"""CREATE TABLE retention_policies (
 resource_type text PRIMARY KEY, retention_interval interval NOT NULL CHECK(retention_interval>interval '0 seconds'), legal_hold boolean NOT NULL DEFAULT false,
 policy_version integer NOT NULL CHECK(policy_version>0), updated_at timestamptz NOT NULL DEFAULT now()
)""",
"""CREATE TABLE rate_limit_counters (
 scope_type text NOT NULL CHECK(scope_type IN ('endpoint','credential')), scope_id text NOT NULL, window_start timestamptz NOT NULL,
 request_count bigint NOT NULL DEFAULT 0 CHECK(request_count>=0), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(scope_type,scope_id,window_start)
)""",
"""CREATE TABLE auth_failure_rate_counters (
 client_ip inet NOT NULL, endpoint_slug text NOT NULL CHECK(length(endpoint_slug) BETWEEN 1 AND 128), window_start timestamptz NOT NULL,
 failure_count bigint NOT NULL DEFAULT 0 CHECK(failure_count>=0), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(client_ip,endpoint_slug,window_start)
)""",
"""CREATE TABLE redaction_idempotency_commands (
 principal_id text NOT NULL CHECK(length(principal_id) BETWEEN 1 AND 128), idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 request_fingerprint text NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'), redaction_id text NOT NULL UNIQUE REFERENCES endpoint_redactions(id) ON DELETE RESTRICT,
 audit_event_id text NOT NULL UNIQUE REFERENCES audit_events(id) ON DELETE RESTRICT, request_id text NOT NULL UNIQUE,
 endpoint_id text NOT NULL REFERENCES published_endpoints(id) ON DELETE RESTRICT, invocation_id text NOT NULL REFERENCES endpoint_invocations(id) ON DELETE CASCADE,
 target_type text NOT NULL CHECK(target_type IN ('invocation_input','metadata','output','error','run_event','tool_arguments','tool_result','tool_error')),
 target_row_id text NOT NULL, json_path text NOT NULL, reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 256), first_seen_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(principal_id,idempotency_key)
)""",
]

INDEX_DDL = [
 "CREATE INDEX idx_auth_sessions_user_active ON auth_sessions(user_id,expires_at) WHERE revoked_at IS NULL",
 "CREATE INDEX idx_web_sessions_user_active ON web_sessions(user_id,expires_at) WHERE revoked_at IS NULL",
 "CREATE INDEX idx_sessions_user_started ON sessions(user_id,started_at DESC)",
 "CREATE INDEX idx_sessions_parent ON sessions(parent_session_id)",
 "CREATE INDEX idx_messages_session_active ON messages(session_id,active,id)",
 "CREATE UNIQUE INDEX uq_messages_session_active_index ON messages(session_id,message_index) WHERE active=TRUE",
 "CREATE INDEX idx_messages_search ON messages USING gin(to_tsvector('simple',coalesce(content,'')))",
 "CREATE INDEX idx_session_usage_user_created ON session_usage_events(user_id,created_at DESC)",
 "CREATE INDEX idx_session_lineage_parent ON session_lineage(parent_session_id,created_at)",
 "CREATE INDEX idx_compression_leases_expires ON compression_leases(expires_at)",
 "CREATE INDEX idx_memories_user_updated ON user_memories(user_id,updated_at DESC)",
 "CREATE INDEX idx_memories_metadata_gin ON user_memories USING gin(metadata)",
 "CREATE INDEX idx_skills_usage_state ON skill_usage(user_id,state,pinned,last_used_at)",
 "CREATE INDEX idx_skill_events_skill_used ON skill_usage_events(user_id,skill_id,used_at DESC)",
 "CREATE INDEX idx_endpoint_versions_endpoint ON published_endpoint_versions(endpoint_id,version_number DESC)",
 "CREATE INDEX idx_credentials_active ON endpoint_credentials(endpoint_id,expires_at) WHERE revoked_at IS NULL",
 "CREATE INDEX idx_invocations_endpoint_created ON endpoint_invocations(endpoint_id,created_at DESC)",
 "CREATE INDEX idx_invocations_session_created ON endpoint_invocations(session_id,created_at DESC)",
 "CREATE INDEX idx_invocations_input_gin ON endpoint_invocations USING gin(input)",
 "CREATE INDEX idx_run_events_invocation_sequence ON run_events(invocation_id,sequence_number)",
 "CREATE INDEX idx_tool_calls_invocation_sequence ON endpoint_tool_calls(invocation_id,sequence_number)",
 "CREATE INDEX idx_published_sessions_lookup ON published_session_turn_pairs(endpoint_id,service_account_id,session_id,sequence_number)",
 "CREATE INDEX idx_audit_resource_time ON audit_events(resource_type,resource_id,occurred_at DESC)",
 "CREATE INDEX idx_audit_endpoint_time ON audit_events(endpoint_id,occurred_at DESC)",
 "CREATE INDEX idx_sensitive_hits_invocation ON invocation_sensitive_hits(invocation_id,detected_at DESC)",
 "CREATE INDEX idx_redactions_invocation ON endpoint_redactions(invocation_id,redacted_at DESC)",
 "CREATE INDEX idx_tombstones_retained_until ON redaction_tombstones(retained_until)",
 "CREATE INDEX idx_invocations_retention ON endpoint_invocations(created_at,id)",
 "CREATE INDEX idx_audit_retention ON audit_events(occurred_at,id)",
 "CREATE INDEX idx_published_sessions_retention ON published_session_turn_pairs(created_at,endpoint_id)",
 "CREATE INDEX idx_rate_limits_updated ON rate_limit_counters(updated_at)",
 "CREATE INDEX idx_auth_failures_updated ON auth_failure_rate_counters(updated_at)",
]


def upgrade() -> None:
    for statement in DDL:
        op.execute(statement)
    for statement in INDEX_DDL:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE published_endpoints DROP CONSTRAINT IF EXISTS published_endpoints_current_version_fk")
    for table in reversed(tuple(dict.fromkeys(DOMAIN_TABLES.values()))):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
