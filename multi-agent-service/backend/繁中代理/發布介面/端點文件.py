"""A23 發布端點文件的 immutable projection 與 deterministic deep renderer。

本模組只接受公開、安全、已釘選的文件投影，輸出 exact canonical UTF-8 JSON bytes。
不讀取資料庫、環境、時鐘或任何 runtime authority。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

_狀態 = frozenset(("active", "disabled", "archived"))
_識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_短名 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_最大綱要位元組 = 65_536
_最大深度 = 32
_最大節點 = 4096
_敏感欄位片段 = (
    "authorization", "api_key", "apikey", "cookie", "csrf", "password", "passwd",
    "secret", "token", "private_key", "database_url", "dsn", "credential",
)
_敏感值關鍵字 = frozenset(("default", "const", "example", "examples", "enum"))
_敏感值樣式 = (
    re.compile(r"(?i)(?:ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    re.compile(r"(?i)(?:glpat-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{35}|npm_[A-Za-z0-9_-]{36,})"),
    re.compile(r"(?i)(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|pk_[A-Za-z0-9_-]{32,})"),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"(?i)xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)\s*[:=]\s*[^\s,;]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_.-])(?:/(?:users|home|etc|private|var|opt|srv|root|tmp|usr|library|"
        r"system|applications|volumes|mnt|media|proc|sys|dev|run)/|"
        r"[a-z]:\\(?:users|programdata|windows)\\)"
    ),
    re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])"),
)

_錯誤契約 = (
    ("endpoint_not_found", 404, "找不到 endpoint slug。"),
    ("invalid_api_key", 401, "API key 無效。"),
    ("api_key_expired", 401, "API key 已過期。"),
    ("endpoint_disabled", 403, "Endpoint 已停用。"),
    ("endpoint_archived", 410, "Endpoint 已封存。"),
    ("input_schema_invalid", 422, "Input 不符合 schema。"),
    ("model_output_schema_invalid", 502, "模型輸出不符合 response schema。"),
    ("rate_limit_exceeded", 429, "呼叫頻率超過限制。"),
    ("model_timeout", 504, "模型供應商逾時。"),
    ("tool_execution_failed", 502, "工具執行失敗。"),
    ("tool_timeout", 504, "工具執行逾時。"),
    ("endpoint_misconfigured", 500, "Endpoint 設定錯誤。"),
    ("internal_error", 500, "伺服器內部錯誤。"),
)


@dataclass(frozen=True, slots=True)
class _綱要走訪上下文:
    """跨dict/list獨立傳遞祖先敏感taint與properties-map走訪狀態。"""

    敏感屬性: bool = False
    屬性映射: bool = False

    def __post_init__(self) -> None:
        if type(self.敏感屬性) is not bool or type(self.屬性映射) is not bool:
            raise ValueError


def _正規綱要(值: object) -> bytes:
    """一次走訪 JSON object，允許schema名稱但拒絕實際敏感值，再保存快照。"""
    節點 = 0

    def 是敏感欄位(鍵: str) -> bool:
        正規鍵 = 鍵.casefold().replace("-", "_")
        return any(片段 in 正規鍵 for 片段 in _敏感欄位片段)

    def 含敏感literal(目前: object) -> bool:
        if 目前 is None:
            return False
        if type(目前) is str:
            return bool(目前.strip())
        if type(目前) is list:
            return any(含敏感literal(項目) for 項目 in 目前)
        if type(目前) is dict:
            return any(含敏感literal(項目) for 項目 in 目前.values())
        return type(目前) in (bool, int, float)

    def 走訪(目前: object, 深度: int, 上下文: _綱要走訪上下文) -> None:
        nonlocal 節點
        節點 += 1
        if 節點 > _最大節點 or 深度 > _最大深度:
            raise ValueError
        if 目前 is None or type(目前) in (bool, int):
            return
        if type(目前) is float:
            if not math.isfinite(目前):
                raise ValueError
            return
        if type(目前) is str:
            if any(樣式.search(目前) is not None for 樣式 in _敏感值樣式):
                raise ValueError
            return
        if type(目前) is list:
            for item in 目前:
                走訪(item, 深度 + 1, 上下文)
            return
        if type(目前) is dict:
            for key, item in 目前.items():
                if type(key) is not str:
                    raise ValueError
                if 上下文.屬性映射:
                    走訪(
                        item, 深度 + 1,
                        _綱要走訪上下文(
                            敏感屬性=上下文.敏感屬性 or 是敏感欄位(key),
                        ),
                    )
                    continue
                normalized = key.casefold().replace("-", "_")
                if 上下文.敏感屬性 and normalized in _敏感值關鍵字 and 含敏感literal(item):
                    raise ValueError
                走訪(
                    item, 深度 + 1,
                    _綱要走訪上下文(
                        敏感屬性=上下文.敏感屬性,
                        屬性映射=normalized == "properties",
                    ),
                )
            return
        raise ValueError

    if type(值) is not dict:
        raise ValueError
    走訪(值, 0, _綱要走訪上下文())
    encoded = json.dumps(
        值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if not 2 <= len(encoded) <= _最大綱要位元組:
        raise ValueError
    return encoded


@dataclass(frozen=True, slots=True, init=False)
class 端點文件投影:
    """不含秘密與內部 authority 的 immutable current-version docs projection。"""

    端點識別碼: str
    短名: str
    版本: int
    狀態: str
    _輸入綱要位元: bytes = field(repr=False)
    _回應綱要位元: bytes = field(repr=False)
    端點請求上限: int
    端點窗口秒數: int

    def __init__(self, *, 端點識別碼: str, 短名: str, 版本: int, 狀態: str,
                 輸入綱要: dict[str, Any] | None, 回應綱要: dict[str, Any],
                 端點請求上限: int, 端點窗口秒數: int) -> None:
        try:
            if (_識別碼.fullmatch(端點識別碼) is None or _短名.fullmatch(短名) is None
                    or type(版本) is not int or not 1 <= 版本 <= 2_147_483_647
                    or type(狀態) is not str or 狀態 not in _狀態
                    or type(端點請求上限) is not int or not 1 <= 端點請求上限 <= 10_000
                    or type(端點窗口秒數) is not int or not 1 <= 端點窗口秒數 <= 86_400):
                raise ValueError
            input_bytes = _正規綱要({} if 輸入綱要 is None else 輸入綱要)
            response_bytes = _正規綱要(回應綱要)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise ValueError("端點文件投影無效") from None
        object.__setattr__(self, "端點識別碼", 端點識別碼)
        object.__setattr__(self, "短名", 短名)
        object.__setattr__(self, "版本", 版本)
        object.__setattr__(self, "狀態", 狀態)
        object.__setattr__(self, "_輸入綱要位元", input_bytes)
        object.__setattr__(self, "_回應綱要位元", response_bytes)
        object.__setattr__(self, "端點請求上限", 端點請求上限)
        object.__setattr__(self, "端點窗口秒數", 端點窗口秒數)

    @property
    def 輸入綱要(self) -> dict[str, Any]:
        """回傳 fresh JSON tree，caller 無法改寫保存的 canonical bytes。"""
        return json.loads(self._輸入綱要位元)

    @property
    def 回應綱要(self) -> dict[str, Any]:
        """回傳 fresh JSON tree，caller 無法改寫保存的 canonical bytes。"""
        return json.loads(self._回應綱要位元)


def 渲染端點文件(投影: 端點文件投影) -> bytes:
    """重新建構投影並輸出固定 key order/separators/newline 的 UTF-8 JSON。"""
    if type(投影) is not 端點文件投影:
        raise ValueError("端點文件投影無效") from None
    try:
        safe = 端點文件投影(
            端點識別碼=投影.端點識別碼, 短名=投影.短名, 版本=投影.版本, 狀態=投影.狀態,
            輸入綱要=投影.輸入綱要, 回應綱要=投影.回應綱要,
            端點請求上限=投影.端點請求上限, 端點窗口秒數=投影.端點窗口秒數,
        )
        request_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["input"],
            "properties": {
                "input": safe.輸入綱要,
                "session_id": {
                    "anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}],
                    "x-utf8-max-bytes": 128,
                    "description": "Optional Published session identifier；上限 128 UTF-8 bytes。",
                },
                "metadata": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
        }
        document = {
            "endpoint": {"id": safe.端點識別碼, "slug": safe.短名, "version": safe.版本, "status": safe.狀態},
            "invoke_url": "${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke",
            "authentication": {"scheme": "bearer", "header": "Authorization"},
            "request_schema": request_schema,
            "response_schema": safe.回應綱要,
            "rate_limit": {"requests": safe.端點請求上限, "window_seconds": safe.端點窗口秒數},
            "examples": {
                "curl": "curl -X POST '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke' -H 'Authorization: Bearer ${API_KEY}' -H 'Content-Type: application/json' --data '{\"input\":{},\"session_id\":\"${SESSION_ID}\",\"metadata\":{\"endpoint_id\":\"${ENDPOINT_ID}\"}}'",
                "python": "import json\nimport urllib.request\nurl = '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke'\npayload = {'input': {}, 'session_id': '${SESSION_ID}', 'metadata': {'endpoint_id': '${ENDPOINT_ID}'}}\nrequest = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer ${API_KEY}', 'Content-Type': 'application/json'}, method='POST')\nwith urllib.request.urlopen(request) as response:\n    print(response.read().decode('utf-8'))",
            },
            "errors": [{"code": code, "status": status, "message": message} for code, status, message in _錯誤契約],
        }
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise ValueError("端點文件投影無效") from None


EndpointDocsProjection = 端點文件投影
render_endpoint_docs = 渲染端點文件

__all__ = ("端點文件投影", "渲染端點文件", "EndpointDocsProjection", "render_endpoint_docs")
