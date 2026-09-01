"""Read-only驗證A20 browser三個durable tombstone。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("A20 checker設定無效") from None
    路徑 = Path(sys.argv[1]).resolve()
    連線 = sqlite3.connect(f"file:{路徑}?mode=ro", uri=True)
    try:
        連線.execute("PRAGMA query_only=ON")
        連線.execute("BEGIN")
        if 連線.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("A20 checker失敗") from None
        列 = 連線.execute(
            "SELECT target_type,target_row_id,json_path FROM endpoint_redactions "
            "WHERE invocation_id=? ORDER BY target_type,target_row_id,json_path",
            ("invocation-browser-a20",),
        ).fetchall()
        預期 = sorted([
            ("invocation_input", "invocation-browser-a20", "/payload/value"),
            ("run_event", "event-browser-a20", "/payload/value"),
            ("tool_result", "tool-browser-a20", "/payload/value"),
        ])
        if 列 != 預期:
            raise RuntimeError("A20 checker失敗") from None
        input_json = 連線.execute(
            "SELECT input_json FROM endpoint_invocations WHERE id='invocation-browser-a20'"
        ).fetchone()[0]
        event_json = 連線.execute(
            "SELECT payload_json FROM run_events WHERE id='event-browser-a20'"
        ).fetchone()[0]
        tool_json = 連線.execute(
            "SELECT result_json FROM endpoint_tool_calls WHERE id='tool-browser-a20'"
        ).fetchone()[0]
        for 文字 in (input_json, event_json, tool_json):
            內容 = json.loads(文字)
            tombstone = 內容["payload"]["value"]
            if set(tombstone) != {"$tombstone"} or set(tombstone["$tombstone"]) != {"redaction_id", "redacted_at"}:
                raise RuntimeError("A20 checker失敗") from None
        print("A20_DB_CHECK=PASS redactions=3 tombstones=3")
    finally:
        連線.rollback()
        連線.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
