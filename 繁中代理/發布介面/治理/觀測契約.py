"""GOV 端點 metrics 與 owner-safe diagnostics 的 transport-neutral 契約。"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
import re
from typing import Protocol, TypeAlias

_最大計數 = 2**63 - 1
_狀態 = frozenset(("pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key"))
_成本格式 = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{0,27}[1-9])?\Z")
_定價格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _識別碼(值: object) -> bool:
    """檢查 bounded exact identifier。"""
    return type(值) is str and 0 < len(值) <= 128 and not any(字.isspace() for 字 in 值)


def _計數(值: object) -> bool:
    """檢查 SQLite-compatible nonnegative 64-bit count。"""
    return type(值) is int and 0 <= 值 <= _最大計數


def _數值(值: object, 可空: bool = False) -> bool:
    """檢查 finite nonnegative exact number。"""
    return (可空 and 值 is None) or (type(值) in (int, float) and math.isfinite(值) and 值 >= 0)


@dataclass(frozen=True, slots=True)
class 觀測視窗:
    """查詢實際採用的半開時間窗 [start_at, end_at)。"""

    start_at: float
    end_at: float

    def __post_init__(self) -> None:
        """拒絕非有限或反向視窗。"""
        if not (_數值(self.start_at) and _數值(self.end_at) and self.start_at <= self.end_at):
            raise ValueError("觀測視窗不符合契約")


@dataclass(frozen=True, slots=True)
class 延遲摘要:
    """合法 latency samples 的 deterministic aggregate。"""

    sample_count: int
    average: float | None
    p50: float | None
    p95: float | None
    maximum: float | None

    def __post_init__(self) -> None:
        """要求空樣本全空，非空樣本全部有值。"""
        值們 = (self.average, self.p50, self.p95, self.maximum)
        if not _計數(self.sample_count) or ((self.sample_count == 0) != all(值 is None for 值 in 值們)):
            raise ValueError("延遲摘要不符合契約")
        if self.sample_count and not all(_數值(值) for 值 in 值們):
            raise ValueError("延遲摘要不符合契約")


@dataclass(frozen=True, slots=True)
class 用量摘要:
    """合法 persisted invocation usage 的一次性總和。"""

    sample_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        """驗證 counts 與 token 加總一致。"""
        值們 = (self.sample_count, self.input_tokens, self.output_tokens, self.total_tokens)
        if not all(_計數(值) for 值 in 值們) or self.input_tokens + self.output_tokens != self.total_tokens:
            raise ValueError("用量摘要不符合契約")


@dataclass(frozen=True, slots=True)
class 定價版本成本:
    """單一 historical pricing version 的 exact decimal cost。"""

    pricing_version: str
    estimated_cost_usd: str

    def __post_init__(self) -> None:
        """驗證 ASCII version 與 canonical decimal。"""
        if type(self.pricing_version) is not str or not _定價格式.fullmatch(self.pricing_version):
            raise ValueError("定價版本成本不符合契約")
        if type(self.estimated_cost_usd) is not str or not _成本格式.fullmatch(self.estimated_cost_usd):
            raise ValueError("定價版本成本不符合契約")


@dataclass(frozen=True, slots=True)
class 端點指標:
    """單一 owner-visible endpoint 的完整 aggregate。"""

    endpoint_id: str
    window: 觀測視窗
    invocation_count: int
    terminal_count: int
    error_count: int
    error_rate: float
    latency_ms: 延遲摘要
    usage: 用量摘要
    estimated_cost_usd: str
    cost_by_pricing_version: tuple[定價版本成本, ...]

    def __post_init__(self) -> None:
        """驗證 exact nested DTO、counts、rate 與排序。"""
        計數們 = (self.invocation_count, self.terminal_count, self.error_count)
        if not _識別碼(self.endpoint_id) or type(self.window) is not 觀測視窗:
            raise ValueError("端點指標不符合契約")
        if (not all(_計數(值) for 值 in 計數們) or self.error_count > self.terminal_count
                or self.terminal_count > self.invocation_count):
            raise ValueError("端點指標不符合契約")
        預期率 = 0.0 if self.terminal_count == 0 else self.error_count / self.terminal_count
        if type(self.error_rate) is not float or self.error_rate != 預期率:
            raise ValueError("端點指標不符合契約")
        if type(self.latency_ms) is not 延遲摘要 or type(self.usage) is not 用量摘要:
            raise ValueError("端點指標不符合契約")
        if type(self.cost_by_pricing_version) is not tuple or not all(type(項) is 定價版本成本 for 項 in self.cost_by_pricing_version):
            raise TypeError("端點指標不符合契約")
        版本們 = tuple(項.pricing_version for 項 in self.cost_by_pricing_version)
        if 版本們 != tuple(sorted(set(版本們))) or type(self.estimated_cost_usd) is not str or not _成本格式.fullmatch(self.estimated_cost_usd):
            raise ValueError("端點指標不符合契約")


@dataclass(frozen=True, slots=True)
class 診斷用量:
    """Owner diagnostics 唯一允許的 usage 欄位。"""

    total_tokens: int

    def __post_init__(self) -> None:
        """驗證 token count。"""
        if not _計數(self.total_tokens):
            raise ValueError("診斷用量不符合契約")


@dataclass(frozen=True, slots=True)
class 診斷項目:
    """不含 raw payload、metadata 或內部錯誤的 safe diagnostic。"""

    invocation_id: str
    request_id: str
    endpoint_version_id: str
    status: str
    error_code: str | None
    schema_path: str | None
    latency_ms: float | None
    usage: 診斷用量 | None
    tool_names: tuple[str, ...]
    created_at: float
    completed_at: float | None
    redacted_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        """逐欄驗證 allowlist 與 immutable containers。"""
        if not all(_識別碼(值) for 值 in (self.invocation_id, self.request_id, self.endpoint_version_id)):
            raise ValueError("診斷項目不符合契約")
        if type(self.status) is not str or self.status not in _狀態:
            raise ValueError("診斷項目不符合契約")
        if any(值 is not None and (type(值) is not str or len(值) > 512) for 值 in (self.error_code, self.schema_path)):
            raise ValueError("診斷項目不符合契約")
        if not _數值(self.latency_ms, True) or not _數值(self.created_at) or not _數值(self.completed_at, True):
            raise ValueError("診斷項目不符合契約")
        if self.usage is not None and type(self.usage) is not 診斷用量:
            raise TypeError("診斷項目不符合契約")
        if type(self.tool_names) is not tuple or any(not _識別碼(值) for 值 in self.tool_names):
            raise TypeError("診斷項目不符合契約")
        if self.tool_names != tuple(sorted(set(self.tool_names))):
            raise ValueError("診斷項目不符合契約")
        if type(self.redacted_fields) is not tuple or self.redacted_fields not in ((), ("error_code", "schema_path")):
            raise ValueError("診斷項目不符合契約")


@dataclass(frozen=True, slots=True)
class 診斷頁:
    """Descending diagnostics page 與 opaque next cursor。"""

    items: tuple[診斷項目, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        """驗證 exact tuple、最多 100 items 與 bounded cursor。"""
        if type(self.items) is not tuple or len(self.items) > 100 or not all(type(項) is 診斷項目 for 項 in self.items):
            raise TypeError("診斷頁不符合契約")
        if self.next_cursor is not None and (type(self.next_cursor) is not str or not 1 <= len(self.next_cursor) <= 1024):
            raise ValueError("診斷頁不符合契約")


@dataclass(frozen=True, slots=True)
class 指標查詢成功:
    """Typed metrics success outcome。"""
    指標: 端點指標


@dataclass(frozen=True, slots=True)
class 診斷查詢成功:
    """Typed diagnostics success outcome。"""
    頁: 診斷頁


@dataclass(frozen=True, slots=True)
class 端點不可見結果:
    """Missing 與 foreign endpoint 共用的 anti-enumeration outcome。"""


指標查詢結果: TypeAlias = 指標查詢成功 | 端點不可見結果
診斷查詢結果: TypeAlias = 診斷查詢成功 | 端點不可見結果


class 端點觀測查詢服務(Protocol):
    """Owner/admin metrics 與 safe diagnostics 的 transport-neutral provider。"""

    def 讀取端點指標(self, *, 擁有者使用者識別碼: str, 是否管理者: bool,
                 端點識別碼: str, 視窗秒數: int) -> 指標查詢結果:
        """以單一 authoritative operation 讀取 endpoint metrics。"""
        ...

    def 列出端點診斷(self, *, 擁有者使用者識別碼: str, 是否管理者: bool,
                 端點識別碼: str, 視窗秒數: int, 數量上限: int,
                 游標: str | None) -> 診斷查詢結果:
        """以單一 authoritative operation 讀取 paginated safe diagnostics。"""
        ...
