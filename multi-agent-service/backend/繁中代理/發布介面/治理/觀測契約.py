"""GOV 端點 metrics 與 owner-safe diagnostics 的 transport-neutral 契約。"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import math
import re
from typing import Protocol, TypeAlias

_最大計數 = 2**63 - 1
_狀態 = frozenset(("pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key"))
_聚合成本格式 = re.compile(r"(?:0|[1-9][0-9]{0,36})(?:\.[0-9]{0,27}[1-9])?\Z")
_最大聚合成本 = Decimal("9223372036854775806999999999999999999.9999999990776627963145224193")
_定價格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_日期格式 = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_錯誤碼格式 = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")


def _識別碼(值: object) -> bool:
    """檢查 bounded exact identifier。"""
    return type(值) is str and 0 < len(值) <= 128 and not any(字.isspace() for 字 in 值)


def _計數(值: object) -> bool:
    """檢查 SQLite-compatible nonnegative 64-bit count。"""
    return type(值) is int and 0 <= 值 <= _最大計數


def _數值(值: object, 可空: bool = False) -> bool:
    """檢查 finite nonnegative exact number。"""
    return (可空 and 值 is None) or (type(值) in (int, float) and math.isfinite(值) and 值 >= 0)


def _聚合成本(值: object) -> bool:
    """檢查 canonical、非 exponent 且不超過 SQLite 最大列數的成本總和。"""
    if type(值) is not str or not _聚合成本格式.fullmatch(值):
        return False
    try:
        數值 = Decimal(值)
    except (InvalidOperation, ValueError):
        return False
    return 數值.is_finite() and 數值 >= 0 and 數值 <= _最大聚合成本


@dataclass(frozen=True, slots=True, repr=False)
class 觀測視窗:
    """查詢實際採用的半開時間窗 [start_at, end_at)。"""

    start_at: float
    end_at: float
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        """拒絕非有限或反向視窗。"""
        if not (_數值(self.start_at) and _數值(self.end_at) and self.start_at <= self.end_at
                and type(self.timezone) is str and self.timezone == "UTC"):
            raise ValueError("觀測視窗不符合契約")


@dataclass(frozen=True, slots=True, repr=False)
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


@dataclass(frozen=True, slots=True, repr=False)
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


@dataclass(frozen=True, slots=True, repr=False)
class 定價版本成本:
    """單一 historical pricing version 的 exact decimal cost。"""

    pricing_version: str
    estimated_cost_usd: str

    def __post_init__(self) -> None:
        """驗證 ASCII version 與 canonical decimal。"""
        if type(self.pricing_version) is not str or not _定價格式.fullmatch(self.pricing_version):
            raise ValueError("定價版本成本不符合契約")
        if not _聚合成本(self.estimated_cost_usd):
            raise ValueError("定價版本成本不符合契約")


@dataclass(frozen=True, slots=True, repr=False)
class 每日端點指標:
    """單一有資料 UTC calendar date 的安全聚合。"""

    date: str
    invocation_count: int
    terminal_count: int
    error_count: int
    usage_total_tokens: int
    estimated_cost_usd: str

    def __post_init__(self) -> None:
        """驗證 canonical UTC date、counts、tokens 與 exact cost。"""
        try:
            日期有效 = type(self.date) is str and bool(_日期格式.fullmatch(self.date))
            if 日期有效:
                日期有效 = date.fromisoformat(self.date).isoformat() == self.date
        except (OverflowError, TypeError, ValueError):
            日期有效 = False
        計數們 = (self.invocation_count, self.terminal_count, self.error_count, self.usage_total_tokens)
        if (not 日期有效 or not all(_計數(值) for 值 in 計數們)
                or self.error_count > self.terminal_count
                or self.terminal_count > self.invocation_count
                or not _聚合成本(self.estimated_cost_usd)):
            raise ValueError("每日端點指標不符合契約")


@dataclass(frozen=True, slots=True, repr=False)
class 安全錯誤排行:
    """只含 canonical safe error code 與非零計數的排行項目。"""

    error_code: str
    count: int

    def __post_init__(self) -> None:
        """拒絕 raw、非canonical error code 與零值項目。"""
        if (type(self.error_code) is not str or not _錯誤碼格式.fullmatch(self.error_code)
                or not _計數(self.count) or self.count == 0):
            raise ValueError("安全錯誤排行不符合契約")


@dataclass(frozen=True, slots=True, repr=False)
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
    daily: tuple[每日端點指標, ...]
    top_errors: tuple[安全錯誤排行, ...]

    def __post_init__(self) -> None:
        """驗證 exact nested DTO、counts、rate 與排序。"""
        if (type(self.window) is not 觀測視窗 or type(self.latency_ms) is not 延遲摘要
                or type(self.usage) is not 用量摘要 or type(self.cost_by_pricing_version) is not tuple
                or type(self.daily) is not tuple or type(self.top_errors) is not tuple
                or not all(type(項) is 定價版本成本 for 項 in self.cost_by_pricing_version)
                or not all(type(項) is 每日端點指標 for 項 in self.daily)
                or not all(type(項) is 安全錯誤排行 for 項 in self.top_errors)):
            raise TypeError("端點指標不符合契約")
        try:
            視窗 = 觀測視窗(self.window.start_at, self.window.end_at, self.window.timezone)
            延遲 = 延遲摘要(
                self.latency_ms.sample_count, self.latency_ms.average, self.latency_ms.p50,
                self.latency_ms.p95, self.latency_ms.maximum,
            )
            用量 = 用量摘要(
                self.usage.sample_count, self.usage.input_tokens,
                self.usage.output_tokens, self.usage.total_tokens,
            )
            成本分項 = tuple(定價版本成本(項.pricing_version, 項.estimated_cost_usd)
                         for 項 in self.cost_by_pricing_version)
            每日分項 = tuple(每日端點指標(
                項.date, 項.invocation_count, 項.terminal_count, 項.error_count,
                項.usage_total_tokens, 項.estimated_cost_usd,
            ) for 項 in self.daily)
            錯誤分項 = tuple(安全錯誤排行(項.error_code, 項.count) for 項 in self.top_errors)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("端點指標不符合契約") from None
        object.__setattr__(self, "window", 視窗)
        object.__setattr__(self, "latency_ms", 延遲)
        object.__setattr__(self, "usage", 用量)
        object.__setattr__(self, "cost_by_pricing_version", 成本分項)
        object.__setattr__(self, "daily", 每日分項)
        object.__setattr__(self, "top_errors", 錯誤分項)
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
        if (type(self.cost_by_pricing_version) is not tuple
                or not all(type(項) is 定價版本成本 for 項 in self.cost_by_pricing_version)
                or len(self.cost_by_pricing_version) > 4096
                or type(self.daily) is not tuple or len(self.daily) > 31
                or not all(type(項) is 每日端點指標 for 項 in self.daily)
                or type(self.top_errors) is not tuple or len(self.top_errors) > 10
                or not all(type(項) is 安全錯誤排行 for 項 in self.top_errors)):
            raise TypeError("端點指標不符合契約")
        版本們 = tuple(項.pricing_version for 項 in self.cost_by_pricing_version)
        日期們 = tuple(項.date for 項 in self.daily)
        錯誤排序 = tuple((項.count, 項.error_code) for 項 in self.top_errors)
        預期錯誤排序 = tuple(sorted(錯誤排序, key=lambda 項: (-項[0], 項[1])))
        try:
            起始日 = date(1970, 1, 1) + timedelta(days=math.floor(self.window.start_at / 86400))
            結束日 = date(1970, 1, 1) + timedelta(days=math.floor(
                math.nextafter(self.window.end_at, -math.inf) / 86400
            )) if self.window.end_at > self.window.start_at else 起始日
        except (OverflowError, ValueError):
            raise ValueError("端點指標不符合契約") from None
        with localcontext() as 小數環境:
            小數環境.prec = 80
            分項成本 = sum((Decimal(項.estimated_cost_usd) for 項 in self.cost_by_pricing_version), Decimal(0))
            每日成本 = sum((Decimal(項.estimated_cost_usd) for 項 in self.daily), Decimal(0))
        if (版本們 != tuple(sorted(set(版本們))) or not _聚合成本(self.estimated_cost_usd)
                or 分項成本 != Decimal(self.estimated_cost_usd)
                or 日期們 != tuple(sorted(set(日期們)))
                or any(not 起始日 <= date.fromisoformat(日期) <= 結束日 for 日期 in 日期們)
                or sum(項.invocation_count for 項 in self.daily) != self.invocation_count
                or sum(項.terminal_count for 項 in self.daily) != self.terminal_count
                or sum(項.error_count for 項 in self.daily) != self.error_count
                or sum(項.usage_total_tokens for 項 in self.daily) != self.usage.total_tokens
                or 每日成本 != Decimal(self.estimated_cost_usd)
                or 錯誤排序 != 預期錯誤排序
                or len({項.error_code for 項 in self.top_errors}) != len(self.top_errors)
                or sum(項.count for 項 in self.top_errors) > self.error_count
                or self.latency_ms.sample_count > self.invocation_count
                or self.usage.sample_count > self.invocation_count):
            raise ValueError("端點指標不符合契約")
        if (self.window.start_at == self.window.end_at
                and (self.invocation_count != 0 or self.daily or self.top_errors
                     or self.cost_by_pricing_version or self.estimated_cost_usd != "0"
                     or self.latency_ms.sample_count != 0 or self.usage.sample_count != 0)):
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

    def __post_init__(self) -> None:
        """只接受 exact module-owned metrics DTO。"""
        if type(self.指標) is not 端點指標:
            raise TypeError("指標查詢成功不符合契約")


@dataclass(frozen=True, slots=True)
class 診斷查詢成功:
    """Typed diagnostics success outcome。"""
    頁: 診斷頁

    def __post_init__(self) -> None:
        """只接受 exact module-owned diagnostics DTO。"""
        if type(self.頁) is not 診斷頁:
            raise TypeError("診斷查詢成功不符合契約")


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
