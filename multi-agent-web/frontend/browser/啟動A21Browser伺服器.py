"""A21 canonical invoke 與 production SPA 的 hermetic browser server。"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

import uvicorn

_專案根 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_專案根))
sys.path.insert(0, str(_專案根 / "tests" / "發布介面"))

import asgi as root_asgi
from a08_3_formal_publish import 建立正式v1
from 繁中代理.使用者 import 使用者庫
from 繁中代理.模型供應商 import GeminiADC供應商
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照

_管理員帳號 = "browser-admin-a21"


def _必要路徑(名稱: str) -> Path:
    文字 = os.environ.get(名稱)
    路徑 = Path(文字) if type(文字) is str else Path()
    if not 文字 or not 路徑.is_absolute() or ".." in 路徑.parts:
        raise RuntimeError("A21 browser fixture設定無效") from None
    return 路徑


def _必要文字(名稱: str, 最小: int, 最大: int) -> str:
    值 = os.environ.get(名稱)
    if type(值) is not str or not 最小 <= len(值) <= 最大 or not 值.isascii():
        raise RuntimeError("A21 browser fixture設定無效") from None
    return 值


def _合成標記們() -> tuple[str, str, str, str, str]:
    """只在 fixture 內合成，不輸出內容。"""
    郵件 = lambda 前綴: 前綴 + chr(64) + "safe.invalid"
    return (郵件("input"), "0912" + "-345-678", "4" + "1" * 15,
            郵件("arguments"), 郵件("result"))


class _五目標模型:
    """Deterministic adapter：經兩次真實skill tool call產生工具列與最終回應。"""

    def __init__(self, 工具參數標記: str, 回應標記: str) -> None:
        self._工具參數標記 = 工具參數標記
        self._回應標記 = 回應標記

    def 產生(self, **參數: object) -> 模型回應快照:
        工具訊息 = [項 for 項 in 參數["messages"] if 項["role"] == "tool"]
        if not 工具訊息:
            名稱, 工具參數 = "skills_list", {"category": self._工具參數標記}
        elif len(工具訊息) == 1:
            名稱, 工具參數 = "skill_view", {"name": "stable"}
        else:
            return 模型回應快照(
                json.dumps({"answer": self._回應標記}, separators=(",", ":")),
                "stop", {"total_tokens": 3}, [],
            )
        呼叫 = {
            "id": f"call-{len(工具訊息) + 1}", "type": "function",
            "function": {"name": 名稱, "arguments": json.dumps(工具參數, separators=(",", ":"))},
        }
        return 模型回應快照("", "tool_calls", {}, [呼叫])


def _建立靜態先決圖形(
    根: Path, 管理員密碼: str, API金鑰: str, 憑證加密金鑰: str,
) -> None:
    """重用A21-06的production publish authority；不建立dynamic invocation列。"""
    根.mkdir(parents=True, exist_ok=False)
    _, _, _, _, 結果 = _合成標記們()
    身分 = 建立正式v1(
        web=根 / "web.sqlite3", db=根 / "published.sqlite3",
        bundles=根 / "bundles", skill_root=根 / "skills",
        skill_body="BUNDLE-V1 " + 結果,
    )
    金鑰材料 = base64.urlsafe_b64decode(憑證加密金鑰 + "=")
    已加密 = None
    with sqlite3.connect(根 / "published.sqlite3") as 連線:
        憑證列 = 連線.execute(
            "SELECT id FROM endpoint_credentials WHERE endpoint_id=?", (身分["endpoint"],)
        ).fetchall()
        if len(憑證列) != 1:
            raise RuntimeError("A21 browser fixture設定無效") from None
        憑證ID = 憑證列[0][0]
        已加密 = AESGCM憑證封套({1: 金鑰材料}, 1).加密(
            API金鑰, 身分["endpoint"], 憑證ID
        )
        更新 = 連線.execute(
            "UPDATE endpoint_credentials SET key_version=?,key_nonce=?,key_ciphertext=?,"
            "key_hash=?,key_prefix=?,key_last4=? WHERE id=? AND endpoint_id=?",
            (已加密.envelope.key_version, 已加密.envelope.nonce,
             已加密.envelope.ciphertext, 已加密.key_hash, 已加密.key_prefix,
             已加密.key_last4, 憑證ID, 身分["endpoint"]),
        )
        if 更新.rowcount != 1:
            raise RuntimeError("A21 browser fixture設定無效") from None
    API金鑰 = 憑證加密金鑰 = 金鑰材料 = 已加密 = None
    使用者 = 使用者庫(根 / "web.sqlite3")
    try:
        使用者.建立使用者(_管理員帳號, 管理員密碼, roles=["admin"])
    finally:
        使用者.連線.close()


def _設定生產環境(根: Path, Dist根: Path, Port: str) -> None:
    設定 = {
        "TESTAGENT2_DB_PATH": str(根 / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(根 / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(根 / "bundles"),
        "TESTAGENT2_WEB_DIST_ROOT": str(Dist根),
        "TESTAGENT2_WEB_ORIGINS": json.dumps([f"http://127.0.0.1:{Port}"], separators=(",", ":")),
        "TESTAGENT2_COOKIE_SECURE": "false", "TESTAGENT2_SESSION_TTL_SECONDS": "300",
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc", "TESTAGENT2_MODEL_NAME": "gemini-browser-a21",
        "AIAGENT_GCP_PROJECT": "browser-project", "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({
            "1": _必要文字("A21_BROWSER_CREDENTIAL_KEY", 43, 43),
        }, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": _必要文字(
            "A21_BROWSER_OWNER_CURSOR_KEY", 43, 43
        ),
    }
    for 名稱 in tuple(os.environ):
        if 名稱.startswith(("TESTAGENT2_", "AIAGENT_")):
            del os.environ[名稱]
    os.environ.update(設定)


def main() -> int:
    根 = _必要路徑("A21_BROWSER_ROOT")
    Dist根 = _必要路徑("A21_BROWSER_DIST_ROOT")
    Port = _必要文字("A21_BROWSER_PORT", 4, 5)
    if not Port.isdecimal() or not 1024 <= int(Port) <= 65535:
        raise RuntimeError("A21 browser fixture設定無效") from None
    密碼 = _必要文字("A21_BROWSER_PASSWORD", 32, 128)
    API金鑰 = _必要文字("A21_BROWSER_API_KEY", 46, 46)
    憑證加密金鑰 = _必要文字("A21_BROWSER_CREDENTIAL_KEY", 43, 43)
    if not 根.exists():
        _建立靜態先決圖形(根, 密碼, API金鑰, 憑證加密金鑰)
    _設定生產環境(根, Dist根, Port)
    _, _, 回應標記, 參數標記, _ = _合成標記們()
    模型 = _五目標模型(參數標記, 回應標記)
    GeminiADC供應商.產生發布回應 = lambda self, **kw: 模型.產生(**kw)
    密碼 = API金鑰 = 憑證加密金鑰 = ""
    for 名稱 in (
        "A21_BROWSER_PASSWORD", "A21_BROWSER_API_KEY", "A21_BROWSER_CREDENTIAL_KEY",
        "A21_BROWSER_OWNER_CURSOR_KEY",
    ):
        os.environ.pop(名稱, None)
    uvicorn.run(
        root_asgi.建立應用程式(), host="127.0.0.1", port=int(Port),
        log_level="warning", access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
