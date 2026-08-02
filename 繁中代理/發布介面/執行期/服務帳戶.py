"""發布執行期專用服務帳戶上下文與 owner 資料拒絕邊界。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol


_安全識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_雜湊 = re.compile(r"[0-9a-f]{64}")
_唯一允許來源 = "endpoint_version_snapshot"
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class 服務帳戶上下文錯誤(RuntimeError):
    """服務帳戶快照無效或任何隔離邊界失敗時的固定公開錯誤。"""


class 服務帳戶上下文載入器(Protocol):
    """只允許依服務帳戶與端點版本讀取已發布快照的 adapter。"""

    def 載入服務帳戶上下文(
        self, service_account_id: str, endpoint_version_id: str, source: str
    ) -> object:
        """從 endpoint version snapshot 回傳上下文；不得讀取 owner 資料。"""


@dataclass(frozen=True, init=False, slots=True)
class ServiceAccountContext:
    """Published Runtime 的完整、不可變且不含 owner 檔案脈絡之權限快照。"""

    service_account_id: str
    endpoint_version_id: str
    permission_snapshot_digest: str
    allowed_tools: tuple[str, ...]
    skill_bundle_hash: str
    tool_handler_release: str

    def __init__(
        self,
        *,
        service_account_id: str,
        endpoint_version_id: str,
        permission_snapshot_digest: str,
        allowed_tools: tuple[str, ...],
        skill_bundle_hash: str,
        tool_handler_release: str,
    ) -> None:
        """以 exact 型別建立 frozen 快照；失敗不回顯任何輸入。"""
        值 = (
            service_account_id,
            endpoint_version_id,
            permission_snapshot_digest,
            allowed_tools,
            skill_bundle_hash,
            tool_handler_release,
        )
        合法 = False
        try:
            合法 = (
                type(self) is ServiceAccountContext
                and _是識別碼(service_account_id)
                and _是識別碼(endpoint_version_id)
                and type(permission_snapshot_digest) is str
                and _雜湊.fullmatch(permission_snapshot_digest) is not None
                and type(allowed_tools) is tuple
                and _工具名稱皆合法(allowed_tools)
                and type(skill_bundle_hash) is str
                and _雜湊.fullmatch(skill_bundle_hash) is not None
                and _是識別碼(tool_handler_release)
            )
        except _控制流程例外:
            del 值, service_account_id, endpoint_version_id
            del permission_snapshot_digest, allowed_tools, skill_bundle_hash
            del tool_handler_release
            raise
        except BaseException:
            合法 = False
        if not 合法:
            值 = service_account_id = endpoint_version_id = None
            permission_snapshot_digest = allowed_tools = skill_bundle_hash = None
            tool_handler_release = None
            raise 服務帳戶上下文錯誤("發布服務帳戶上下文無效") from None
        for 欄位, 欄位值 in zip(self.__dataclass_fields__, 值, strict=True):
            object.__setattr__(self, 欄位, 欄位值)


def 載入服務帳戶上下文或失敗關閉(
    載入器: 服務帳戶上下文載入器,
    service_account_id: str,
    endpoint_version_id: str,
    *,
    source: str = _唯一允許來源,
) -> ServiceAccountContext:
    """只讀取版本快照；禁止 memory/session/global/workdir fallback。"""
    if (
        not _是識別碼(service_account_id)
        or not _是識別碼(endpoint_version_id)
        or type(source) is not str
        or source != _唯一允許來源
    ):
        del 載入器, service_account_id, endpoint_version_id, source
        raise 服務帳戶上下文錯誤("發布服務帳戶上下文不可用") from None

    結果 = None
    失敗 = False
    try:
        結果 = 載入器.載入服務帳戶上下文(
            service_account_id, endpoint_version_id, _唯一允許來源
        )
    except _控制流程例外:
        del 載入器, 結果, service_account_id, endpoint_version_id, source
        raise
    except BaseException:
        失敗 = True

    正規化結果 = None
    try:
        if not 失敗:
            正規化結果 = _正規化上下文(結果)
    except _控制流程例外:
        del 載入器, 結果, 正規化結果
        del service_account_id, endpoint_version_id, source
        raise
    if (
        正規化結果 is None
        or 正規化結果.service_account_id != service_account_id
        or 正規化結果.endpoint_version_id != endpoint_version_id
    ):
        del 載入器, 結果, 正規化結果
        del service_account_id, endpoint_version_id, source
        raise 服務帳戶上下文錯誤("發布服務帳戶上下文不可用") from None
    del 載入器, 結果, service_account_id, endpoint_version_id, source
    return 正規化結果


def _正規化上下文(不可信結果: object) -> ServiceAccountContext | None:
    """exact guard 後以 trusted slot 讀取全欄，並重新執行所有 DTO invariants。"""
    欄位值 = None
    欄位值清單 = None
    名稱 = None
    正規化結果 = None
    if type(不可信結果) is ServiceAccountContext:
        try:
            欄位值清單 = []
            for 名稱 in ServiceAccountContext.__dataclass_fields__:
                欄位值清單.append(object.__getattribute__(不可信結果, 名稱))
            欄位值 = tuple(欄位值清單)
            欄位值清單 = 名稱 = None
            正規化結果 = ServiceAccountContext(
                **dict(zip(ServiceAccountContext.__dataclass_fields__, 欄位值, strict=True))
            )
        except _控制流程例外:
            不可信結果 = 欄位值 = 欄位值清單 = 名稱 = 正規化結果 = None
            raise
        except BaseException:
            正規化結果 = None
    不可信結果 = 欄位值 = 欄位值清單 = 名稱 = None
    return 正規化結果


def _是識別碼(值: object) -> bool:
    """只接受可安全記錄的 exact str identifier。"""
    return type(值) is str and _安全識別碼.fullmatch(值) is not None


def _工具名稱皆合法(工具名稱們: tuple[str, ...]) -> bool:
    """逐項驗證工具名稱與唯一性，並在離開前清除受檢值。"""
    合法 = True
    名稱 = None
    已看 = set()
    try:
        for 名稱 in 工具名稱們:
            if not _是識別碼(名稱) or 名稱 in 已看:
                合法 = False
                break
            已看.add(名稱)
    except _控制流程例外:
        del 工具名稱們, 名稱, 已看
        raise
    except BaseException:
        合法 = False
    del 工具名稱們, 名稱, 已看
    return 合法
