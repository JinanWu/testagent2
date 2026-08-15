"""A21 Gate B browser fixture 不得退回dynamic SQL seed。"""

from pathlib import Path
import re


def test_A21_browser_fixture只由canonical_invoke建立dynamic_rows():
    根 = Path(__file__).resolve().parents[2]
    server = (根 / "apps/web-app/browser/啟動A21Browser伺服器.py").read_text(encoding="utf-8")
    spec = (根 / "apps/web-app/browser/tests/a21-admin-sensitive-hits.spec.ts").read_text(
        encoding="utf-8"
    )
    for table in (
        "endpoint_invocations", "run_events", "endpoint_tool_calls",
        "invocation_sensitive_hits", "audit_events",
    ):
        assert re.search(rf"INSERT\s+INTO\s+{table}\b", server, re.IGNORECASE) is None
    assert "request.post(`/v1/endpoints/${SLUG}/invoke`" in spec
    assert "response.text().catch" not in spec
    assert "page.waitForResponse" in spec
    assert "const detailBody = await (await detailResponse).text()" in spec
    assert "catch(() => '')" not in spec
    assert "SQLite不可逆遮蔽服務" in spec


def test_A21_restart階段不重新invoke或遮蔽():
    根 = Path(__file__).resolve().parents[2]
    spec = (根 / "apps/web-app/browser/tests/a21-admin-sensitive-hits.spec.ts").read_text(
        encoding="utf-8"
    )
    restart, primary = spec.split("if (PHASE === 'restart')", 1)[1].split("\n  }\n", 1)
    assert "/invoke" not in restart
    assert "redactCanonicalInvocation" not in restart
    assert "/invoke" in primary

