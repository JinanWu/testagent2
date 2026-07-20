"""發布介面共同公開契約。

參數／欄位：不適用；本模組彙整協定、資料型別、錯誤與工廠名稱。
回傳：不適用；公開名稱各自提供其回傳契約。
例外：必要子模組無法載入時原樣傳出匯入例外。
副作用：匯入時載入共同契約；部分網頁工廠只在查詢名稱時延遲載入，不建立服務。
"""

from .協定 import (
    AuditEventSink,
    Planner權限查詢,
    安全查詢規劃權限,
    授權工具,
    授權技能,
    規劃權限快照,
    規劃權限查詢錯誤,
)
from .契約 import AuditSinkError
from .契約 import 建立失敗信封, 建立成功信封
from .契約 import 附加稽核事件或失敗關閉
from .嚴格JSON import 嚴格JSON錯誤, 建立正規JSON, 解析嚴格JSON, 計算正規JSON雜湊
from .資料庫結構契約 import (
    資料庫結構契約錯誤,
    資料庫結構指紋,
    計算資料庫結構指紋,
    遷移帳本,
    驗證資料庫結構,
)

from .領域模型 import (
    AuditActorRef,
    AuditAppendReceipt,
    AuditEvent,
    AuditEventError,
    AuditMetadata,
    AuditMetadataError,
    AuditReceiptError,
    AuditReferenceError,
    AuditResourceRef,
    WebOwnerPrincipal,
    WebSessionContractError,
)

__all__ = [
    "AuditActorRef",
    "AuditAppendReceipt",
    "AuditEvent",
    "AuditEventError",
    "AuditEventSink",
    "AuditMetadata",
    "AuditMetadataError",
    "AuditReceiptError",
    "AuditReferenceError",
    "AuditResourceRef",
    "AuditSinkError",
    "WebOwnerPrincipal",
    "WebSessionContractError",
    "Planner權限查詢",
    "授權技能",
    "授權工具",
    "規劃權限快照",
    "規劃權限查詢錯誤",
    "安全查詢規劃權限",
    "嚴格JSON錯誤",
    "解析嚴格JSON",
    "建立正規JSON",
    "計算正規JSON雜湊",
    "建立成功信封",
    "建立失敗信封",
    "附加稽核事件或失敗關閉",
    "資料庫結構契約錯誤",
    "資料庫結構指紋",
    "計算資料庫結構指紋",
    "遷移帳本",
    "驗證資料庫結構",
    "建立目前工作階段相依項",
    "建立ASGI應用程式",
    "建立CP4ASGI應用程式",
    "建立環境應用程式",
    "Published生產設定",
    "生產Published執行建構器",
    "生產Controller建構器",
]


def __getattr__(名稱: str):
    """延遲公開網頁路由工廠，避免使用者模組初始化期間形成循環匯入。"""
    if 名稱 == "建立目前工作階段相依項":
        from .路由 import 建立目前工作階段相依項

        return 建立目前工作階段相依項
    if 名稱 in {"建立ASGI應用程式", "建立CP4ASGI應用程式", "建立環境應用程式"}:
        from .asgi import 建立ASGI應用程式, 建立CP4ASGI應用程式, 建立環境應用程式

        return {
            "建立ASGI應用程式": 建立ASGI應用程式,
            "建立CP4ASGI應用程式": 建立CP4ASGI應用程式,
            "建立環境應用程式": 建立環境應用程式,
        }[名稱]
    if 名稱 in {"Published生產設定", "生產Published執行建構器", "生產Controller建構器"}:
        from .生產Published執行 import Published生產設定, 生產Controller建構器, 生產Published執行建構器
        return {"Published生產設定": Published生產設定,
                "生產Published執行建構器": 生產Published執行建構器,
                "生產Controller建構器": 生產Controller建構器}[名稱]
    raise AttributeError(名稱)
