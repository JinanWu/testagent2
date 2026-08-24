"""無頭瀏覽器網頁渲染工具

簡介：
    這個工具用真正的瀏覽器（如 Playwright）去跑網頁，讓 JavaScript 跑完再抓內容，
    可拿到動態渲染的資料，以及頁面上的圖片與連結網址。

重點特色：
    - 支援跨多種後端（預設為 playwright），只要設定 `WEB_RENDER_BACKEND` 環境變數。
    - 可避免 SSRF：會檢查網址是否安全，不讓內部網路被存取。
    - 適合 Cloud Run 等容器化環境：記憶體至少 2GB，細節見 `瀏覽器啟動參數`。

環境變數說明：
    - WEB_RENDER_BACKEND: 選哪個後端（預設 playwright）
    - WEB_RENDER_TIMEOUT: 單頁逾時秒數（預設 30）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from ..環境設定 import 載入本機環境檔
from .網路搜尋 import 後端呼叫失敗, 後端未設定, 確認非內部位址, 驗證網址

logger = logging.getLogger(__name__)

預設後端 = "playwright"
預設逾時秒數 = 30
最大逾時秒數 = 120
最大網址數量 = 3
單頁最大字元 = 20000
最大字元上限 = 100000
最大圖片數量 = 50
最大連結數量 = 100
允許等待策略 = ("load", "domcontentloaded", "networkidle")
預設等待策略 = "load"

# Cloud Run／一般容器內的 Chromium 必備參數：容器沒有 user namespace 給 sandbox 用，
# 且 /dev/shm 預設只有 64MB，渲染大頁面會直接讓瀏覽器 crash。
瀏覽器啟動參數 = ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu")
瀏覽器視窗大小 = {"width": 1440, "height": 900}
使用者代理 = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# 注入頁面取素材。取 currentSrc 是為了拿到 srcset 實際選中的那張；連結取 href
# property（非 attribute）讓瀏覽器幫忙把相對路徑解析成絕對網址。
取圖片腳本 = """
() => Array.from(document.querySelectorAll('img'))
    .map(節點 => ({url: 節點.currentSrc || 節點.src || '', alt: (節點.alt || '').trim()}))
    .filter(項目 =>項目.url.startsWith('http'))
"""
取連結腳本 = """
() => Array.from(document.querySelectorAll('a[href]'))
    .map(節點 => ({url: 節點.href || '', text: (節點.innerText || '').trim()}))
    .filter(項目 => 項目.url.startsWith('http'))
"""


@dataclass(frozen=True)
class 素材項目:
    """頁面上的一個圖片或連結。"""

    網址: str
    說明: str

    def 轉成字典(self, 說明欄位名稱: str) -> dict[str, str]:
        """轉成回傳給模型的欄位。

        參數：
            說明欄位名稱: 圖片用 "alt"，連結用 "text"。
        返回值：dict。
        """
        return {"url": self.網址, 說明欄位名稱: self.說明}


@dataclass(frozen=True)
class 渲染結果項目:
    """單一網址的渲染結果。"""

    網址: str
    最終網址: str
    標題: str
    文字: str
    圖片清單: list[素材項目]
    連結清單: list[素材項目]
    是否截斷: bool
    原始字元數: int

    def 轉成字典(self) -> dict[str, Any]:
        """轉成回傳給模型的欄位。"""
        return {
            "url": self.網址,
            "final_url": self.最終網址,
            "title": self.標題,
            "text": self.文字,
            "images": [項目.轉成字典("alt") for 項目 in self.圖片清單],
            "links": [項目.轉成字典("text") for 項目 in self.連結清單],
            "truncated": self.是否截斷,
            "original_length": self.原始字元數,
        }


class 渲染後端(Protocol):
    """web_render 的後端協定。"""

    名稱: str

    def 渲染(
        self, 網址清單: list[str], 等待策略: str, 字元上限: int,
    ) -> tuple[list[渲染結果項目], list[dict[str, str]]]:
        """渲染網頁，回傳（成功清單, 失敗清單）。"""
        ...


def 讀取逾時秒數() -> int:
    """讀取單頁導覽逾時秒數；設定無效時退回預設值。

    參數：無。
    返回值：int，介於 1 與 最大逾時秒數 之間。
    """
    載入本機環境檔()
    原始值 = os.getenv("WEB_RENDER_TIMEOUT", "").strip()
    if not 原始值:
        return 預設逾時秒數
    try:
        return max(1, min(int(原始值), 最大逾時秒數))
    except ValueError:
        return 預設逾時秒數


def 確認可render網址(網址: str) -> None:
    """在導覽前擋掉指向內部位址的網址。

    參數：
        網址: 已通過 `驗證網址` 的網址。
    返回值：None；指向內部位址時丟出 ValueError。
    """
    主機 = urlsplit(網址).hostname
    if not 主機:
        raise ValueError(f"網址缺少主機名稱：{網址}")
    確認非內部位址(主機)


def 整理素材(原始清單: Any, 數量上限: int, 說明鍵: str) -> list[素材項目]:
    """把注入腳本回傳的陣列整理成素材項目並去重。

    參數：
        原始清單: 腳本回傳值；型別不符時視為空。
        數量上限: 保留筆數上限。
        說明鍵: 說明文字在原始項目中的鍵名（alt / text）。
    返回值：素材項目清單，依出現順序去重且不超過數量上限。
    """
    if type(原始清單) is not list:
        return []
    已見網址: set[str] = set()
    素材清單: list[素材項目] = []
    for 原始項目 in 原始清單:
        if type(原始項目) is not dict:
            continue
        網址 = str(原始項目.get("url") or "").strip()
        if not 網址 or 網址 in 已見網址:
            continue
        已見網址.add(網址)
        素材清單.append(素材項目(網址, str(原始項目.get(說明鍵) or "").strip()))
        if len(素材清單) >= 數量上限:
            break
    return 素材清單


def 建立渲染項目(
    網址: str, 最終網址: str, 標題: str, 文字: str,
    圖片清單: list[素材項目], 連結清單: list[素材項目], 字元上限: int,
) -> 渲染結果項目:
    """套用字元上限並標記是否截斷。

    參數：
        網址: 請求的網址。
        最終網址: 導覽結束後的網址（可能經過轉址）。
        標題: 頁面標題。
        文字: 渲染後的可見文字。
        圖片清單: 已整理的圖片素材。
        連結清單: 已整理的連結素材。
        字元上限: 文字保留上限。
    返回值：渲染結果項目。
    """
    原始字元數 = len(文字)
    是否截斷 = 原始字元數 > 字元上限
    return 渲染結果項目(
        網址=網址,
        最終網址=最終網址,
        標題=標題,
        文字=文字[:字元上限] if 是否截斷 else 文字,
        圖片清單=圖片清單,
        連結清單=連結清單,
        是否截斷=是否截斷,
        原始字元數=原始字元數,
    )


class Playwright後端:
    """本機無頭 Chromium 後端。

    每次 `渲染` 開一個瀏覽器行程、跑完所有網址再關掉。不做跨呼叫的行程重用：
    工具 handler 是無狀態的，留著行程等下一次呼叫會在 Cloud Run 的請求模型下
    變成洩漏。代價是每次呼叫多約一秒的啟動時間。
    """

    名稱 = "playwright"

    def __init__(self, 逾時秒數: int) -> None:
        """保存導覽逾時秒數。"""
        self.逾時秒數 = 逾時秒數

    def 渲染(
        self, 網址清單: list[str], 等待策略: str, 字元上限: int,
    ) -> tuple[list[渲染結果項目], list[dict[str, str]]]:
        """逐一渲染網址並收集文字、圖片與連結。

        參數：
            網址清單: 已驗證的網址。
            等待策略: playwright 的 wait_until 值。
            字元上限: 單頁文字保留上限。
        返回值：（成功清單, 失敗清單）；瀏覽器起不來時丟出 後端呼叫失敗。
        """
        from playwright.sync_api import Error as Playwright錯誤
        from playwright.sync_api import sync_playwright

        成功清單: list[渲染結果項目] = []
        失敗清單: list[dict[str, str]] = []
        逾時毫秒 = self.逾時秒數 * 1000
        try:
            with sync_playwright() as 執行環境:
                瀏覽器 = 執行環境.chromium.launch(
                    headless=True, args=list(瀏覽器啟動參數),
                )
                try:
                    情境 = 瀏覽器.new_context(
                        viewport=dict(瀏覽器視窗大小), user_agent=使用者代理,
                    )
                    try:
                        for 網址 in 網址清單:
                            項目, 失敗 = self._渲染單頁(
                                情境, 網址, 等待策略, 逾時毫秒, 字元上限, Playwright錯誤,
                            )
                            if 項目 is not None:
                                成功清單.append(項目)
                            if 失敗 is not None:
                                失敗清單.append(失敗)
                    finally:
                        情境.close()
                finally:
                    瀏覽器.close()
        except Playwright錯誤 as 錯誤:
            raise 後端呼叫失敗(f"瀏覽器啟動失敗：{錯誤}") from None
        return 成功清單, 失敗清單

    def _渲染單頁(
        self, 情境: Any, 網址: str, 等待策略: str, 逾時毫秒: int,
        字元上限: int, Playwright錯誤: type[Exception],
    ) -> tuple[渲染結果項目 | None, dict[str, str] | None]:
        """渲染單一網址；單頁失敗不影響其他網址。

        參數：
            情境: playwright browser context。
            網址: 目標網址。
            等待策略: wait_until 值。
            逾時毫秒: 導覽逾時。
            字元上限: 文字保留上限。
            Playwright錯誤: playwright 的例外基底類別。
        返回值：（渲染結果項目 或 None, 失敗描述 或 None）。
        """
        try:
            確認可render網址(網址)
        except ValueError as 錯誤:
            return None, {"url": 網址, "error": str(錯誤)}
        頁面 = 情境.new_page()
        try:
            頁面.goto(網址, wait_until=等待策略, timeout=逾時毫秒)
            最終網址 = str(頁面.url)
            # 轉址可能把我們帶進內網，導覽後必須再擋一次。
            確認可render網址(最終網址)
            標題 = str(頁面.title() or "")
            文字 = str(頁面.inner_text("body") or "")
            圖片清單 = 整理素材(頁面.evaluate(取圖片腳本), 最大圖片數量, "alt")
            連結清單 = 整理素材(頁面.evaluate(取連結腳本), 最大連結數量, "text")
        except ValueError as 錯誤:
            return None, {"url": 網址, "error": str(錯誤)}
        except Playwright錯誤 as 錯誤:
            return None, {"url": 網址, "error": f"渲染失敗：{str(錯誤).splitlines()[0][:120]}"}
        finally:
            頁面.close()
        項目 = 建立渲染項目(
            網址, 最終網址, 標題, 文字, 圖片清單, 連結清單, 字元上限,
        )
        return 項目, None


def 建立Playwright後端(逾時秒數: int) -> 渲染後端:
    """建立本機無頭瀏覽器後端。

    參數：
        逾時秒數: 單頁導覽逾時。
    返回值：渲染後端；未安裝 playwright 或缺 chromium 時丟出 後端未設定。
    """
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        raise 後端未設定(
            "網頁渲染尚未可用：未安裝 playwright。請執行 "
            "`pip install playwright && playwright install chromium`"
        ) from None
    return Playwright後端(逾時秒數)


後端建構表 = {"playwright": 建立Playwright後端}


def 建立渲染後端() -> 渲染後端:
    """依 WEB_RENDER_BACKEND 建立後端實例。

    參數：無。
    返回值：渲染後端；後端名稱未知或缺少設定時丟出 後端未設定。
    """
    載入本機環境檔()
    名稱 = (os.getenv("WEB_RENDER_BACKEND", "").strip() or 預設後端).lower()
    建構函數 = 後端建構表.get(名稱)
    if 建構函數 is None:
        可用 = "、".join(sorted(後端建構表))
        raise 後端未設定(f"WEB_RENDER_BACKEND 不支援的後端：{名稱}（可用：{可用}）")
    return 建構函數(讀取逾時秒數())


def 解析網址清單(參數: dict[str, Any]) -> list[str]:
    """解析並驗證 urls 參數。

    參數：
        參數: 工具呼叫參數。
    返回值：已驗證且去重的網址清單；不合法時丟出 ValueError。
    """
    原始清單 = 參數.get("urls")
    if type(原始清單) is not list or not 原始清單:
        raise ValueError("urls 必須是非空陣列")
    if len(原始清單) > 最大網址數量:
        raise ValueError(f"urls 一次最多 {最大網址數量} 個（渲染成本遠高於純擷取）")
    網址清單: list[str] = []
    for 原始網址 in 原始清單:
        網址 = 驗證網址(原始網址)
        if 網址 not in 網址清單:
            網址清單.append(網址)
    return 網址清單


def 解析等待策略(參數: dict[str, Any]) -> str:
    """解析 wait_until 參數。

    參數：
        參數: 工具呼叫參數。
    返回值：合法的等待策略字串；不合法時丟出 ValueError。
    """
    原始值 = 參數.get("wait_until")
    if 原始值 is None:
        return 預設等待策略
    策略 = str(原始值).strip().lower()
    if 策略 not in 允許等待策略:
        可用 = "、".join(允許等待策略)
        raise ValueError(f"wait_until 只接受：{可用}")
    return 策略


def 解析字元上限(參數: dict[str, Any]) -> int:
    """解析 max_chars 參數並夾在合法範圍內。

    參數：
        參數: 工具呼叫參數。
    返回值：int，介於 1 與 最大字元上限 之間。
    """
    原始值 = 參數.get("max_chars")
    if 原始值 is None:
        return 單頁最大字元
    try:
        上限 = int(原始值)
    except (TypeError, ValueError):
        raise ValueError("max_chars 必須是整數") from None
    return max(1, min(上限, 最大字元上限))


def 建立後端失敗結果(錯誤: Exception) -> dict[str, Any]:
    """把後端設定或呼叫失敗轉成 canonical 失敗結果。

    與 `網路搜尋.建立後端失敗結果` 同一套判準：設定缺漏不可恢復（重試不會變好），
    瀏覽器層的呼叫失敗可恢復。

    參數：
        錯誤: 後端未設定或後端呼叫失敗。
    返回值：dict，含 success=False 與 recoverable 旗標。
    """
    可恢復 = type(錯誤) is 後端呼叫失敗
    logger.warning("web_render 失敗（可恢復=%s）：%s", 可恢復, 錯誤)
    return {
        "success": False,
        "tool": "web_render",
        "error": str(錯誤),
        "recoverable": 可恢復,
    }


def 網頁渲染(參數: dict[str, Any]) -> dict[str, Any]:
    """執行 `web_render`：用無頭瀏覽器渲染網頁並取出文字、圖片與連結。

    參數：
        參數: 工具呼叫參數，需含 urls（最多 最大網址數量 個），
            可選 wait_until、max_chars。
    返回值：dict，含 backend、results、failed、total_count；
        後端未設定或呼叫失敗時回傳 success=False 與訊息。
    """
    網址清單 = 解析網址清單(參數)
    等待策略 = 解析等待策略(參數)
    字元上限 = 解析字元上限(參數)
    try:
        後端 = 建立渲染後端()
        成功清單, 失敗清單 = 後端.渲染(網址清單, 等待策略, 字元上限)
    except (後端未設定, 後端呼叫失敗) as 錯誤:
        return 建立後端失敗結果(錯誤)
    return {
        "backend": 後端.名稱,
        "results": [項目.轉成字典() for 項目 in 成功清單],
        "failed": 失敗清單,
        "total_count": len(成功清單),
    }
