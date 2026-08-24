"""發布端點擁有者與管理者的安全查詢路由。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import Field

from 繁中代理.使用者 import 使用者上下文

_識別碼最大長度 = 128
_文字最大長度 = 256
_狀態集合 = frozenset({"active", "disabled", "archived"})
_無效服務訊息 = "管理查詢服務回傳無效"
_游標錯誤訊息 = "端點查詢游標無效"


def _訊息錯誤文件(*訊息: str, 包含框架驗證: bool = False) -> dict[str, Any]:
    固定 = {
        "type": "object", "additionalProperties": False, "required": ["detail"],
        "properties": {"detail": {"type": "string", "enum": list(訊息)}},
    }
    schema = {"anyOf": [固定, {"$ref": "#/components/schemas/HTTPValidationError"}]} if 包含框架驗證 else 固定
    return {"content": {"application/json": {"schema": schema}}}


_列表錯誤文件: dict[int | str, dict[str, Any]] = {
    403: _訊息錯誤文件("只有管理者可查詢全部發布端點"),
    422: _訊息錯誤文件(_游標錯誤訊息, "查詢參數不符合契約", 包含框架驗證=True),
    500: _訊息錯誤文件(_無效服務訊息),
}
_詳情錯誤文件: dict[int | str, dict[str, Any]] = {
    404: _訊息錯誤文件("找不到發布端點"),
    422: _訊息錯誤文件("查詢參數不符合契約", 包含框架驗證=True),
    500: _訊息錯誤文件(_無效服務訊息),
}


class 端點查詢游標錯誤(ValueError):
    """production adapter 對不透明游標的固定、無洩漏拒絕。"""


@dataclass(frozen=True, slots=True)
class 端點列表項目:
    """單一發布端點的安全列表投影；欄位是外部 JSON 契約。"""

    端點識別碼: Annotated[str, Field(alias="endpoint_id")]
    短名: Annotated[str, Field(alias="slug")]
    狀態: Annotated[str, Field(alias="status")]
    目前版本識別碼: Annotated[str | None, Field(alias="current_version_id")]
    目前版本編號: Annotated[int | None, Field(alias="current_version_number")]
    更新時間: Annotated[float, Field(alias="updated_at")]


@dataclass(frozen=True, slots=True)
class 端點列表回應:
    """有界發布端點列表與下一頁游標。"""

    項目: Annotated[tuple[端點列表項目, ...], Field(alias="items")]
    下一頁游標: Annotated[str | None, Field(alias="next_cursor")]


@dataclass(frozen=True, slots=True)
class 端點安全詳情:
    """不含憑證、指標、文件、診斷或原始紀錄的端點投影。"""

    端點識別碼: Annotated[str, Field(alias="endpoint_id")]
    擁有者使用者識別碼: Annotated[str, Field(alias="owner_user_id")]
    短名: Annotated[str, Field(alias="slug")]
    狀態: Annotated[str, Field(alias="status")]
    目前版本識別碼: Annotated[str | None, Field(alias="current_version_id")]
    目前版本編號: Annotated[int | None, Field(alias="current_version_number")]
    建立時間: Annotated[float, Field(alias="created_at")]
    更新時間: Annotated[float, Field(alias="updated_at")]


class 端點管理查詢服務(Protocol):
    """整合層提供的權威 owner/admin 端點查詢介面。"""

    def 列出端點(
        self, *, 擁有者使用者識別碼: str, 管理者查詢全部: bool, 數量上限: int, 游標: str | None
    ) -> 端點列表回應:
        """依權威擁有者與管理範圍列出端點。"""
        ...

    def 讀取端點(
        self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 管理者查詢全部: bool
    ) -> 端點安全詳情 | None:
        """依權威身份讀取安全詳情；不可見與不存在皆回傳 None。"""
        ...


def 建立端點查詢路由器(
    查詢服務: 端點管理查詢服務,
    身份依賴,
) -> APIRouter:
    """建立 M01 路由；身份只由整合層注入，不讀取身份 header。"""
    路由器 = APIRouter(prefix="/api/published-endpoints")

    @路由器.get("", response_model=端點列表回應, responses=_列表錯誤文件)
    def 列出發布端點(
        請求: Request,
        範圍: Annotated[Literal["owner", "all"], Query(alias="scope")] = "owner",
        數量上限: Annotated[int, Query(alias="limit", ge=1, le=100)] = 20,
        游標: Annotated[str | None, Query(alias="cursor", min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$")] = None,
        身份: 使用者上下文 = Depends(身份依賴),
    ) -> dict[str, object]:
        """列出自己的端點；管理者明確指定 scope=all 才列出全部。"""
        _拒絕未知查詢參數(請求, {"scope", "limit", "cursor"})
        使用者識別碼, 是否管理者 = _重建身份(身份)
        if 範圍 == "all" and not 是否管理者:
            raise HTTPException(status_code=403, detail="只有管理者可查詢全部發布端點")
        try:
            原始結果 = 查詢服務.列出端點(
                擁有者使用者識別碼=使用者識別碼,
                管理者查詢全部=範圍 == "all",
                數量上限=數量上限,
                游標=游標,
            )
            return _序列化列表(_重建列表(原始結果, 數量上限))
        except 端點查詢游標錯誤:
            raise HTTPException(status_code=422, detail=_游標錯誤訊息) from None
        except Exception:
            raise HTTPException(status_code=500, detail=_無效服務訊息) from None

    @路由器.get("/{endpoint_id}", response_model=端點安全詳情, responses=_詳情錯誤文件)
    def 讀取發布端點(
        端點識別碼: Annotated[str, Path(alias="endpoint_id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
        請求: Request,
        身份: 使用者上下文 = Depends(身份依賴),
    ) -> dict[str, object]:
        """讀取安全基本詳情；管理者可讀全部，外人與缺少固定回傳 404。"""
        _拒絕未知查詢參數(請求, set())
        _驗證文字(端點識別碼, _識別碼最大長度)
        使用者識別碼, 是否管理者 = _重建身份(身份)
        安全結果: 端點安全詳情 | None = None
        try:
            原始結果 = 查詢服務.讀取端點(
                端點識別碼=端點識別碼,
                擁有者使用者識別碼=使用者識別碼,
                管理者查詢全部=是否管理者,
            )
            if 原始結果 is not None:
                安全結果 = _重建詳情(原始結果, 端點識別碼)
        except Exception:
            raise HTTPException(status_code=500, detail=_無效服務訊息) from None
        if 原始結果 is None:
            raise HTTPException(status_code=404, detail="找不到發布端點")
        if 安全結果 is None:
            raise HTTPException(status_code=500, detail=_無效服務訊息)
        return _序列化詳情(安全結果)

    return 路由器


def _拒絕未知查詢參數(請求: Request, 允許名稱: set[str]) -> None:
    """拒絕 owner_id 與所有未宣告 query，避免權限範圍可由請求偽造。"""
    if any(名稱 not in 允許名稱 or len(請求.query_params.getlist(名稱)) != 1 for 名稱 in 請求.query_params.keys()):
        raise HTTPException(status_code=422, detail="查詢參數不符合契約")


def _重建身份(身份: 使用者上下文) -> tuple[str, bool]:
    """只接受整合層注入的精確身份型別與精確權限欄位。"""
    if type(身份) is not 使用者上下文:
        raise HTTPException(status_code=500, detail="使用者身份不符合契約")
    使用者識別碼 = object.__getattribute__(身份, "user_id")
    是否管理者 = object.__getattribute__(身份, "is_admin")
    _驗證文字(使用者識別碼, _識別碼最大長度)
    if type(是否管理者) is not bool:
        raise HTTPException(status_code=500, detail="使用者身份不符合契約")
    return 使用者識別碼, 是否管理者


def _重建列表(原始結果: object, 數量上限: int) -> 端點列表回應:
    """驗證完整有界列表後重建模組自有 DTO。"""
    if type(原始結果) is not 端點列表回應:
        raise ValueError
    原始項目 = object.__getattribute__(原始結果, "項目")
    下一頁游標 = object.__getattribute__(原始結果, "下一頁游標")
    if type(原始項目) is not tuple or len(原始項目) > 數量上限:
        raise ValueError
    if 下一頁游標 is not None:
        _驗證游標(下一頁游標)
    結果項目: list[端點列表項目] = []
    for 項目 in 原始項目:
        結果項目.append(_重建列表項目(項目))
    return 端點列表回應(tuple(結果項目), 下一頁游標)


def _序列化列表(回應: 端點列表回應) -> dict[str, object]:
    """以固定英文鍵序列化列表外部契約。"""
    return {
        "items": [
            {
                "endpoint_id": 項目.端點識別碼,
                "slug": 項目.短名,
                "status": 項目.狀態,
                "current_version_id": 項目.目前版本識別碼,
                "current_version_number": 項目.目前版本編號,
                "updated_at": 項目.更新時間,
            }
            for 項目 in 回應.項目
        ],
        "next_cursor": 回應.下一頁游標,
    }


def _重建列表項目(項目: object) -> 端點列表項目:
    """逐槽驗證並重建一筆安全列表項目。"""
    if type(項目) is not 端點列表項目:
        raise ValueError
    值 = tuple(object.__getattribute__(項目, 名稱) for 名稱 in 端點列表項目.__slots__)
    _驗證共同欄位(*值)
    return 端點列表項目(*值)


def _重建詳情(項目: object, 端點識別碼: str) -> 端點安全詳情:
    """逐槽驗證詳情並確認服務沒有替換請求識別碼。"""
    if type(項目) is not 端點安全詳情:
        raise ValueError
    值 = tuple(object.__getattribute__(項目, 名稱) for 名稱 in 端點安全詳情.__slots__)
    項目識別碼, 擁有者識別碼, 短名, 狀態, 版本識別碼, 版本編號, 建立時間, 更新時間 = 值
    _驗證文字(擁有者識別碼, _識別碼最大長度)
    _驗證共同欄位(項目識別碼, 短名, 狀態, 版本識別碼, 版本編號, 更新時間)
    _驗證時間(建立時間)
    if 項目識別碼 != 端點識別碼:
        raise ValueError
    return 端點安全詳情(*值)


def _序列化詳情(詳情: 端點安全詳情) -> dict[str, object]:
    """以固定英文鍵序列化安全詳情外部契約。"""
    return {
        "endpoint_id": 詳情.端點識別碼,
        "owner_user_id": 詳情.擁有者使用者識別碼,
        "slug": 詳情.短名,
        "status": 詳情.狀態,
        "current_version_id": 詳情.目前版本識別碼,
        "current_version_number": 詳情.目前版本編號,
        "created_at": 詳情.建立時間,
        "updated_at": 詳情.更新時間,
    }


def _驗證共同欄位(端點識別碼, 短名, 狀態, 版本識別碼, 版本編號, 更新時間) -> None:
    """驗證列表與詳情共用的安全純量欄位。"""
    _驗證文字(端點識別碼, _識別碼最大長度)
    _驗證文字(短名, _文字最大長度)
    if type(狀態) is not str or 狀態 not in _狀態集合:
        raise ValueError
    if 版本識別碼 is not None:
        _驗證文字(版本識別碼, _識別碼最大長度)
    if 版本編號 is not None and (type(版本編號) is not int or not 1 <= 版本編號 <= 2_147_483_647):
        raise ValueError
    if (版本識別碼 is None) != (版本編號 is None):
        raise ValueError
    _驗證時間(更新時間)


def _驗證文字(值, 最大長度: int) -> None:
    """驗證精確非空有界字串且不含控制字元。"""
    if type(值) is not str or not 1 <= len(值) <= 最大長度 or 值.strip() != 值 or any(ord(字元) < 32 for 字元 in 值):
        raise ValueError


def _驗證時間(值) -> None:
    """驗證精確、有限且非負的時間數值。"""
    if type(值) not in (int, float) or not math.isfinite(值) or 值 < 0:
        raise ValueError


def _驗證游標(值) -> None:
    """驗證服務回傳的既定不透明游標語法。"""
    _驗證文字(值, 512)
    if not all(字元.isascii() and (字元.isalnum() or 字元 in "_-") for 字元 in 值):
        raise ValueError
