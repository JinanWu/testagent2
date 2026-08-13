"""Acceptance 07 端點憑證管理 HTTP 契約與路由驗證。"""

from __future__ import annotations

from 繁中代理.發布介面.憑證管理契約 import (
    一次性憑證建立收據,
    建立憑證請求欄位,
    憑證列表結果,
    憑證摘要,
    憑證管理HTTP錯誤碼,
    憑證管理狀態,
    序列化一次性憑證建立收據,
    序列化憑證列表,
    序列化憑證摘要,
)


def _建立摘要() -> 憑證摘要:
    """建立不含秘密材料的固定憑證摘要。

    描述：提供 exact-key serializer 測試使用的安全投影。
    參數：無。
    返回值：欄位完整且生命週期有效的 ``憑證摘要``。
    """
    return 憑證摘要(
        "cred-example", "production", "partner integration", "public-prefix", "last",
        憑證管理狀態.有效, 200.0, None, 100.0, None, (), 60,
    )


def test_建立請求與固定HTTP錯誤碼形成封閉契約() -> None:
    """凍結 create exact keys 與 public failure codes。"""
    assert 建立憑證請求欄位 == (
        "name", "purpose", "expires_at", "ip_allowlist", "rate_limit_requests",
    )
    assert tuple(項目.value for 項目 in 憑證管理HTTP錯誤碼) == (
        "credential_not_found", "endpoint_status_conflict", "invalid_request",
        "credential_management_failed",
    )


def test_安全摘要與列表只序列化凍結英文鍵() -> None:
    """證明 ordinary projections 無法攜帶 create-only 或 crypto 欄位。"""
    摘要 = _建立摘要()
    內容 = 序列化憑證摘要(摘要)
    assert tuple(內容) == (
        "credential_id", "name", "purpose", "key_prefix", "key_last4", "status",
        "expires_at", "last_used_at", "created_at", "revoked_at", "ip_allowlist",
        "rate_limit_requests",
    )
    assert 序列化憑證列表(憑證列表結果((摘要,))) == {"items": [內容]}
    禁止欄位 = {
        "initial_api_key", "api_key", "key_hash", "key_nonce", "key_ciphertext",
        "key_version", "revision", "proof", "master_key",
    }
    assert 禁止欄位.isdisjoint(內容)
    assert 禁止欄位.isdisjoint(序列化憑證列表(憑證列表結果((摘要,))))


def test_只有建立收據可序列化一次性明文() -> None:
    """固定 create 201 是唯一具有 ``initial_api_key`` 的成功 DTO。"""
    摘要 = _建立摘要()
    收據 = 一次性憑證建立收據(
        摘要.憑證識別碼, 摘要.名稱, 摘要.用途, 摘要.金鑰前綴, 摘要.金鑰末四碼,
        摘要.狀態, 摘要.到期時間, 摘要.最後使用時間, 摘要.建立時間,
        摘要.撤銷時間, 摘要.IP允許清單, 摘要.速率限制請求數, "[REDACTED]",
    )
    內容 = 序列化一次性憑證建立收據(收據)
    assert tuple(內容) == (
        "credential_id", "name", "purpose", "key_prefix", "key_last4", "status",
        "expires_at", "last_used_at", "created_at", "revoked_at", "ip_allowlist",
        "rate_limit_requests", "initial_api_key",
    )
    assert 內容["initial_api_key"] == "[REDACTED]"
    assert "[REDACTED]" not in repr(收據)
