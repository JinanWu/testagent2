"""發布介面公開參照 DTO 領域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


JsonObject = dict[str, Any]


class _公開DTO:
    """提供公開 DTO 共用的 JSON 輸出行為。"""

    def to_json(self) -> JsonObject:
        """回傳公開契約 JSON 物件。

        這個方法沒有參數，會依 dataclass 欄位順序產生新的 dict；不會修改實例，
        也不會觸發外部副作用。實例若不是 dataclass，會由 dataclasses.asdict
        拋出 TypeError。
        """
        return asdict(self)


@dataclass(frozen=True)
class EndpointRef(_公開DTO):
    """公開端點版本的最小參照。"""

    id: str
    slug: str
    version: int


@dataclass(frozen=True)
class InvocationRef(_公開DTO):
    """公開呼叫紀錄的最小參照。"""

    id: str
    request_id: str
    session_id: str | None = None


@dataclass(frozen=True)
class PublishedUsage(_公開DTO):
    """公開回應中的用量摘要。"""

    total_tokens: int | None = None


@dataclass(frozen=True)
class PublishedWarning(_公開DTO):
    """公開回應中的非致命警告。"""

    code: str
    message: str


@dataclass(frozen=True)
class PublishedError(_公開DTO):
    """公開回應中的錯誤摘要。"""

    code: str
    message: str


@dataclass(frozen=True)
class ServiceAccountSnapshotRef(_公開DTO):
    """服務帳號權限快照的公開參照。"""

    service_account_id: str
    endpoint_version_id: str
    permission_snapshot_digest: str
