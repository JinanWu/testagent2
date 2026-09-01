"""交易型耐久資料的完整權威清單與 PostgreSQL Block 1 readiness gate。

本模組只宣告契約，不建立資料庫、連線池、檔案或雲端資源。PostgreSQL adapter
尚未完成期間，所有必要耐久領域一律視為 unavailable，呼叫端必須 fail closed。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

耐久領域 = (
    "users", "user_settings", "cli_auth_sessions", "web_sessions",
    "sessions", "messages", "usage_events", "session_lineage", "compression_leases",
    "user_memories", "user_skills", "skill_usage", "skill_events", "service_accounts",
    "endpoints", "endpoint_versions", "credentials", "invocations", "run_events", "tool_calls",
    "published_sessions", "bundle_receipts", "published_draft_consumptions",
    "published_endpoint_version_metadata", "endpoint_invocation_safe_errors", "audit_events",
    "sensitive_hits", "redaction_commands", "tombstones", "retention", "rate_limits",
    "auth_failure_rate_counters", "idempotency_operations",
)

耐久領域權威: Mapping[str, Mapping[str, str]] = MappingProxyType({
    領域: MappingProxyType({"sqlite": "sqlite", "postgres": "postgres"})
    for 領域 in 耐久領域
})

非PostgreSQL領域: Mapping[str, str] = MappingProxyType({
    "immutable_bundles": "cloud_storage",
    "administrative_search": "bigquery",
    "analytics": "bigquery",
    "todo": "ephemeral_or_user_file",
    "runtime_cache": "ephemeral_or_user_file",
    "workdir_and_downloads": "ephemeral_or_user_file",
})

BigQuery分析工具必須保留 = (
    "繁中代理.工具集.管理部_bigquery",
    "繁中代理.工具集.BigQuery查詢",
)

PostgreSQL連線策略: Mapping[str, str] = MappingProxyType({
    "runtime": "cloud_run_cloud_sql_attachment",
    "transport": "unix_socket",
    "socket_root": "/cloudsql",
    "secret_source": "DATABASE_URL",
    "python_connector": "forbidden",
})


@dataclass(frozen=True, slots=True)
class 權威分類:
    """把現行 persistence surface 反向映射到領域或明確排除理由。"""

    分類: Literal[
        "durable", "migration_or_runtime_metadata", "derived_index", "operational",
        "ephemeral_or_user_file", "non_postgres",
    ]
    領域: str | None
    理由: str


def _耐久(領域: str) -> 權威分類:
    """建立已驗證的交易型耐久權威分類。"""
    if 領域 not in 耐久領域:
        raise ValueError("未知耐久領域")
    return 權威分類("durable", 領域, "交易型耐久資料，PostgreSQL 模式必須由 PostgreSQL 擁有")


# 反向清單凍結目前 SQLite、BigQuery OLTP、JSON/JSONL 與可寫 user-skill surface。
# 這份清單是 coverage gate，不代表 Block 1 已實作 PostgreSQL schema 或 adapter。
現行權威反向清單: Mapping[str, 權威分類] = MappingProxyType({
    "sqlite:users": _耐久("users"),
    "sqlite:user_settings": _耐久("user_settings"),
    "sqlite:auth_sessions": _耐久("cli_auth_sessions"),
    "sqlite:web_sessions": _耐久("web_sessions"),
    "sqlite:sessions": _耐久("sessions"),
    "sqlite:messages": _耐久("messages"),
    "sqlite:compression_locks": _耐久("compression_leases"),
    "sqlite:service_accounts": _耐久("service_accounts"),
    "sqlite:published_endpoints": _耐久("endpoints"),
    "sqlite:published_endpoint_versions": _耐久("endpoint_versions"),
    "sqlite:endpoint_credentials": _耐久("credentials"),
    "sqlite:endpoint_invocations": _耐久("invocations"),
    "sqlite:run_events": _耐久("run_events"),
    "sqlite:endpoint_tool_calls": _耐久("tool_calls"),
    "sqlite:published_session_turn_pairs": _耐久("published_sessions"),
    "sqlite:published_skill_bundles": _耐久("bundle_receipts"),
    "sqlite:published_draft_consumptions": _耐久("published_draft_consumptions"),
    "sqlite:published_endpoint_version_metadata": _耐久("published_endpoint_version_metadata"),
    "sqlite:endpoint_invocation_safe_errors": _耐久("endpoint_invocation_safe_errors"),
    "sqlite:audit_events": _耐久("audit_events"),
    "sqlite:invocation_sensitive_hits": _耐久("sensitive_hits"),
    "sqlite:endpoint_redactions": _耐久("redaction_commands"),
    "sqlite:rate_limit_counters": _耐久("rate_limits"),
    "sqlite:auth_failure_rate_counters": _耐久("auth_failure_rate_counters"),
    "sqlite:redaction_idempotency_commands": _耐久("idempotency_operations"),
    "sqlite:schema_version": 權威分類(
        "migration_or_runtime_metadata", None, "核心 SQLite schema/FTS 重建版本 metadata",
    ),
    "sqlite:state_meta": 權威分類(
        "migration_or_runtime_metadata", None, "核心 SQLite runtime migration metadata",
    ),
    "sqlite:published_api_schema_migrations": 權威分類(
        "migration_or_runtime_metadata", None, "Published SQLite migration ledger",
    ),
    "sqlite:messages_fts": 權威分類("derived_index", "messages", "由 messages 可重建的 FTS index"),
    "sqlite:messages_fts_config": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_content": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_data": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_docsize": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_idx": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_trigram": 權威分類("derived_index", "messages", "由 messages 可重建的 trigram FTS index"),
    "sqlite:messages_fts_trigram_config": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_trigram_content": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_trigram_data": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_trigram_docsize": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "sqlite:messages_fts_trigram_idx": 權威分類("derived_index", "messages", "FTS5 shadow table"),
    "bigquery:users": _耐久("users"),
    "bigquery:user_settings": _耐久("user_settings"),
    "bigquery:auth_sessions": _耐久("cli_auth_sessions"),
    "bigquery:sessions": _耐久("sessions"),
    "bigquery:messages": _耐久("messages"),
    "bigquery:session_usage_events": _耐久("usage_events"),
    "bigquery:user_skills": _耐久("user_skills"),
    "bigquery:skill_usage": _耐久("skill_usage"),
    "bigquery:skill_usage_events": _耐久("skill_events"),
    "json:skill_usage.json": _耐久("skill_usage"),
    "jsonl:skill_usage_events.jsonl": _耐久("skill_events"),
    "filesystem:user_skill": _耐久("user_skills"),
    "json:auth.json": 權威分類(
        "operational", "cli_auth_sessions", "CLI 本機 bearer token cache；server-side token authority 在 auth_sessions",
    ),
    "json:todo": 權威分類("ephemeral_or_user_file", None, "任務進行期暫存狀態"),
    "json:.skills_prompt_snapshot.json": 權威分類(
        "ephemeral_or_user_file", None, "可由技能索引重建的 runtime prompt snapshot cache",
    ),
    "filesystem:immutable_bundles": 權威分類(
        "non_postgres", "bundle_receipts", "bundle bytes 屬 Cloud Storage；PostgreSQL 只保存 receipt metadata",
    ),
    "filesystem:workdir_and_downloads": 權威分類(
        "ephemeral_or_user_file", None, "使用者檔案工具語意，不是交易資料權威",
    ),
})

必要PostgreSQL耐久領域 = frozenset(耐久領域)
PostgreSQL已接線領域 = frozenset[str]()
PostgreSQL尚未接線錯誤 = "PostgreSQL 儲存後端尚未接線"


def 確認PostgreSQL全域就緒() -> None:
    """在任何可能建立 local/BigQuery authority 前執行全域 readiness gate。"""
    if PostgreSQL已接線領域 != 必要PostgreSQL耐久領域:
        raise RuntimeError(PostgreSQL尚未接線錯誤)
