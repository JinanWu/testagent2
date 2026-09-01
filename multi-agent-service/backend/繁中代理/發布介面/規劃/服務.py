"""PUB P02 對 FND 權限查詢與 P01 單一草稿生命週期的薄協調層。"""

from __future__ import annotations

from typing import Any, Callable

from .權限協調 import 權限協調器
from .綱要 import 發布值確認, 規劃已過時錯誤, 規劃服務, 規劃草稿


class 發布規劃服務:
    """查詢權威能力後委派同一個 P01 aggregate/store 管理所有草稿狀態。"""

    def __init__(
        self,
        協調器: 權限協調器,
        *,
        草稿服務: 規劃服務 | None = None,
        存續秒數: float = 86400,
        識別碼產生器: Callable[[], str] | None = None,
    ) -> None:
        """組合既有 P01 服務；wrapper 本身不持有草稿字典或 stale 集合。"""
        if 草稿服務 is not None and (存續秒數 != 86400 or 識別碼產生器 is not None):
            raise ValueError("規劃草稿輸入無效") from None
        self._協調器 = 協調器
        self._草稿服務 = 草稿服務 or 規劃服務(存續秒數=存續秒數, 識別碼產生器=識別碼產生器)

    def 建立草稿(
        self,
        擁有者識別碼: str,
        原始需求: str,
        綱要: Any,
        技能名稱: tuple[str, ...],
        工具名稱: tuple[str, ...],
        *,
        現在: float,
    ) -> 規劃草稿:
        """由 P01 先可信複製綱要，再鎖外查詢一次 FND 並原子建立草稿。"""
        結果 = None
        try:
            結果 = self._草稿服務.建立授權草稿(
                self._協調器,
                擁有者識別碼,
                原始需求,
                綱要,
                技能名稱,
                工具名稱,
                現在=現在,
            )
        except BaseException:
            del self, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 結果
            raise
        del 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在
        return 結果

    def 確認發布值(
        self,
        擁有者識別碼: str,
        草稿識別碼: str,
        *,
        slug: str,
        response_schema: Any,
        docs: str,
        endpoint_limit: int,
        credential_limit: int,
        現在: float,
    ) -> 發布值確認:
        """委派 P01 單一 aggregate 原子確認 P03 發布值。"""
        結果 = None
        try:
            結果 = self._草稿服務.確認發布值(
                擁有者識別碼,
                草稿識別碼,
                slug=slug,
                response_schema=response_schema,
                docs=docs,
                endpoint_limit=endpoint_limit,
                credential_limit=credential_limit,
                現在=現在,
            )
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 結果
            raise
        del 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在
        return 結果

    def 讀取已確認草稿(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> 規劃草稿:
        """委派 P01 讀取仍綁定目前 identity/generation 的確認草稿。"""
        結果 = None
        try:
            結果 = self._草稿服務.讀取已確認草稿(擁有者識別碼, 草稿識別碼, 現在=現在)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 結果
            raise
        del 擁有者識別碼, 草稿識別碼, 現在
        return 結果

    def 規劃草稿(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> 規劃草稿:
        """規劃前在鎖外查詢一次權威快照，再由 P01 鎖內完成比較。"""
        結果 = None
        try:
            結果 = self._重新驗證(擁有者識別碼, 草稿識別碼, 現在=現在)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 結果
            raise
        del 擁有者識別碼, 草稿識別碼, 現在
        return 結果

    def 產生內容(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> 規劃草稿:
        """內容產生前執行與規劃相同的單次權威重驗。"""
        結果 = None
        try:
            結果 = self._重新驗證(擁有者識別碼, 草稿識別碼, 現在=現在)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 結果
            raise
        del 擁有者識別碼, 草稿識別碼, 現在
        return 結果

    def _重新驗證(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> 規劃草稿:
        """只委派 P01 的封閉式重驗，不接觸或轉送任何摘要。"""
        結果 = None
        try:
            結果 = self._草稿服務.重驗授權草稿(
                self._協調器,
                擁有者識別碼,
                草稿識別碼,
                現在=現在,
            )
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 結果
            raise
        del 擁有者識別碼, 草稿識別碼, 現在
        return 結果


__all__ = ["發布規劃服務", "發布值確認", "規劃已過時錯誤"]
