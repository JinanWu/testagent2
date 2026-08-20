"""A22 read-only durable tombstone與credential safe-summary checker。"""
from __future__ import annotations
import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in {"primary", "restart"}:
        raise RuntimeError("A22 checker設定無效") from None
    path = Path(sys.argv[1]).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("A22 checker失敗") from None
        endpoints = connection.execute("SELECT count(*) FROM published_endpoints WHERE slug IN ('stable','browser-a22-b')").fetchone()[0]
        owner_endpoint = connection.execute("SELECT endpoint_id FROM endpoint_invocations WHERE id='invocation-browser-a22-a'").fetchone()[0]
        invocations = connection.execute("SELECT count(*) FROM endpoint_invocations WHERE id='invocation-browser-a22-a'").fetchone()[0]
        redactions = connection.execute("SELECT target_type,target_row_id,json_path FROM endpoint_redactions WHERE invocation_id='invocation-browser-a22-a'").fetchall()
        value = json.loads(connection.execute("SELECT input_json FROM endpoint_invocations WHERE id='invocation-browser-a22-a'").fetchone()[0])["secret"]
        tombstone = isinstance(value, dict) and set(value) == {"$tombstone"} and isinstance(value["$tombstone"], dict) and set(value["$tombstone"]) == {"redaction_id", "redacted_at"}
        credentials = connection.execute("SELECT name,purpose,key_prefix,key_last4,expires_at,created_at,revoked_at,key_ciphertext FROM endpoint_credentials WHERE endpoint_id=? AND name='browser-created' ORDER BY created_at", (owner_endpoint,)).fetchall()
        print(f"A22_DB_OBS endpoints={endpoints} invocations={invocations} redactions={len(redactions)} tombstone={int(tombstone)} credentials={len(credentials)}")
        if endpoints != 2 or invocations != 1 or redactions != [("invocation_input", "invocation-browser-a22-a", "/secret")] or not tombstone or len(credentials) != 1:
            raise RuntimeError("A22 checker失敗") from None
        name, purpose, prefix, last4, expires_at, created_at, revoked_at, ciphertext = credentials[0]
        if (name, purpose) != ("browser-created", "canonical browser") or revoked_at is not None or expires_at <= created_at or not prefix.startswith("pk_") or len(last4) != 4 or not ciphertext:
            raise RuntimeError("A22 checker失敗") from None
        print("A22_DB_CHECK=PASS endpoints=2 invocations=1 redactions=1 tombstones=1 credentials=1")
    finally:
        connection.rollback(); connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
