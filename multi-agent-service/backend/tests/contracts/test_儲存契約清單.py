"""凍結 Cloud SQL Block 1 的耐久權威分母。"""

from types import MappingProxyType
import sqlite3

from 繁中代理.儲存契約 import (
    BigQuery分析工具必須保留,
    PostgreSQL連線策略,
    現行權威反向清單,
    耐久領域,
    耐久領域權威,
    非PostgreSQL領域,
)


def test_耐久領域分母與雙後端權威完整且不可變():
    assert 耐久領域 == (
        "users", "user_settings", "cli_auth_sessions", "web_sessions",
        "sessions", "messages", "usage_events",
        "session_lineage", "compression_leases", "user_memories", "user_skills", "skill_usage",
        "skill_events", "service_accounts", "endpoints", "endpoint_versions", "credentials",
        "invocations", "run_events", "tool_calls", "published_sessions", "bundle_receipts",
        "published_draft_consumptions", "published_endpoint_version_metadata",
        "endpoint_invocation_safe_errors", "audit_events", "sensitive_hits", "redaction_commands",
        "tombstones", "retention", "rate_limits", "auth_failure_rate_counters",
        "idempotency_operations",
    )
    assert isinstance(耐久領域權威, MappingProxyType)
    assert tuple(耐久領域權威) == 耐久領域
    assert all(耐久領域權威[領域] == {"sqlite": "sqlite", "postgres": "postgres"} for 領域 in 耐久領域)


def test_非PostgreSQL權威與BigQuery分析保留明確分類():
    assert dict(非PostgreSQL領域) == {
        "immutable_bundles": "cloud_storage",
        "administrative_search": "bigquery",
        "analytics": "bigquery",
        "todo": "ephemeral_or_user_file",
        "runtime_cache": "ephemeral_or_user_file",
        "workdir_and_downloads": "ephemeral_or_user_file",
    }
    assert BigQuery分析工具必須保留 == (
        "繁中代理.工具集.管理部_bigquery", "繁中代理.工具集.BigQuery查詢",
    )
    assert dict(PostgreSQL連線策略) == {
        "runtime": "cloud_run_cloud_sql_attachment",
        "transport": "unix_socket",
        "socket_root": "/cloudsql",
        "secret_source": "DATABASE_URL",
        "python_connector": "forbidden",
    }


def test_現行SQLite_BigQuery與本機檔案權威皆有反向分類():
    預期耐久 = {
        "sqlite:users": "users", "sqlite:user_settings": "user_settings",
        "sqlite:auth_sessions": "cli_auth_sessions", "sqlite:web_sessions": "web_sessions",
        "sqlite:sessions": "sessions", "sqlite:messages": "messages",
        "sqlite:compression_locks": "compression_leases",
        "sqlite:service_accounts": "service_accounts", "sqlite:published_endpoints": "endpoints",
        "sqlite:published_endpoint_versions": "endpoint_versions",
        "sqlite:endpoint_credentials": "credentials", "sqlite:endpoint_invocations": "invocations",
        "sqlite:run_events": "run_events", "sqlite:endpoint_tool_calls": "tool_calls",
        "sqlite:published_session_turn_pairs": "published_sessions",
        "sqlite:published_skill_bundles": "bundle_receipts",
        "sqlite:published_draft_consumptions": "published_draft_consumptions",
        "sqlite:published_endpoint_version_metadata": "published_endpoint_version_metadata",
        "sqlite:endpoint_invocation_safe_errors": "endpoint_invocation_safe_errors",
        "sqlite:audit_events": "audit_events", "sqlite:invocation_sensitive_hits": "sensitive_hits",
        "sqlite:endpoint_redactions": "redaction_commands", "sqlite:rate_limit_counters": "rate_limits",
        "sqlite:auth_failure_rate_counters": "auth_failure_rate_counters",
        "sqlite:redaction_idempotency_commands": "idempotency_operations",
        "bigquery:users": "users", "bigquery:user_settings": "user_settings",
        "bigquery:auth_sessions": "cli_auth_sessions", "bigquery:sessions": "sessions",
        "bigquery:messages": "messages", "bigquery:session_usage_events": "usage_events",
        "bigquery:user_skills": "user_skills", "bigquery:skill_usage": "skill_usage",
        "bigquery:skill_usage_events": "skill_events", "json:skill_usage.json": "skill_usage",
        "jsonl:skill_usage_events.jsonl": "skill_events", "filesystem:user_skill": "user_skills",
    }
    for authority, domain in 預期耐久.items():
        assert 現行權威反向清單[authority].分類 == "durable"
        assert 現行權威反向清單[authority].領域 == domain
    for authority, 分類 in {
        "sqlite:schema_version": "migration_or_runtime_metadata",
        "sqlite:state_meta": "migration_or_runtime_metadata",
        "sqlite:published_api_schema_migrations": "migration_or_runtime_metadata",
        "sqlite:messages_fts": "derived_index", "sqlite:messages_fts_trigram": "derived_index",
        "json:auth.json": "operational", "json:todo": "ephemeral_or_user_file",
        "json:.skills_prompt_snapshot.json": "ephemeral_or_user_file",
        "filesystem:immutable_bundles": "non_postgres",
        "filesystem:workdir_and_downloads": "ephemeral_or_user_file",
    }.items():
        條目 = 現行權威反向清單[authority]
        assert 條目.分類 == 分類
        assert 條目.理由


def test_反向coverage由現行SQLite與BigQuery表面出發不得漏表(tmp_path):
    from 繁中代理.BigQuery工作階段庫 import 會話表, 訊息表, 用量事件表
    from 繁中代理.BigQuery使用者庫 import 使用者表, 使用者設定表, 認證表
    from 繁中代理.BigQuery技能庫 import 內容表, 使用量表, 事件表
    from 繁中代理.工作階段庫 import 工作階段庫
    from 繁中代理.使用者 import 使用者庫
    from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

    核心路徑 = tmp_path / "core.sqlite3"
    核心 = 工作階段庫(核心路徑)
    使用者 = 使用者庫(核心路徑)
    Published路徑 = tmp_path / "published.sqlite3"
    初始化發布介面資料庫(Published路徑)
    Published連線 = sqlite3.connect(Published路徑)
    try:
        SQLite表 = {
            列[0]
            for 連線 in (核心.連線, 使用者.連線, Published連線)
            for 列 in 連線.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
    finally:
        核心.連線.close()
        使用者.連線.close()
        Published連線.close()
    assert {f"sqlite:{名稱}" for 名稱 in SQLite表} <= set(現行權威反向清單)
    BigQuery表 = {會話表, 訊息表, 用量事件表, 使用者表, 使用者設定表, 認證表, 內容表, 使用量表, 事件表}
    assert {f"bigquery:{名稱}" for 名稱 in BigQuery表} <= set(現行權威反向清單)


def test_技能摘要JSON_runtime_cache由實際路徑反向分類(monkeypatch, tmp_path):
    from 繁中代理.技能索引器 import 取得技能摘要快取路徑

    快取路徑 = tmp_path / ".skills_prompt_snapshot.json"
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(快取路徑))
    實際路徑 = 取得技能摘要快取路徑()
    assert 實際路徑 == 快取路徑.resolve()
    條目 = 現行權威反向清單[f"json:{實際路徑.name}"]
    assert 條目.分類 == "ephemeral_or_user_file"
    assert 條目.理由
