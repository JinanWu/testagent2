"""網路搜尋與網頁擷取工具。

功能：
    提供 `web_search` 與 `web_extract` 的 handler。代理本身不決定去哪裡查，兩個
    工具都把實際查詢委派給可切換的搜尋後端；後端以 `WEB_SEARCH_BACKEND` 選擇，
    預設 ddgs。新增後端只需實作 `搜尋後端` 協定並登記到 `後端建構表`。

後端差異：
    ddgs: 免金鑰。搜尋走 DuckDuckGo（非官方介面），擷取由本機自行抓取並轉純
        文字。因為抓取發生在本機，此路徑會檢查 SSRF（見 `確認非內部位址`），
        且不支援 PDF 與需要 JavaScript 渲染的頁面。伺服器部署下全公司共用單一
        出口 IP，較容易被判定為機器人而限流。
    tavily: 需 `TAVILY_API_KEY`。搜尋與擷取都在 Tavily 端執行，本機不對外抓網
        頁，故無 SSRF 面；支援 PDF。

環境變數：
    WEB_SEARCH_BACKEND: 後端名稱（ddgs / tavily），預設 ddgs。
    TAVILY_API_KEY: Tavily 金鑰（tavily 後端必要）。
    WEB_SEARCH_TIMEOUT: 單次請求逾時秒數，預設 20。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from ..環境設定 import 載入本機環境檔

logger = logging.getLogger(__name__)

預設後端 = "ddgs"
預設結果數量 = 5
最大結果數量 = 100
最大網址數量 = 5
預設逾時秒數 = 20
單頁最大字元 = 5000
拒絕擷取字元上限 = 2_000_000
允許協定 = ("http", "https")

Tavily搜尋網址 = "https://api.tavily.com/search"
Tavily擷取網址 = "https://api.tavily.com/extract"

# 自建擷取用：Tavily 在自己的伺服器抓網頁，ddgs 後端則由本機抓，因此需要自備
# SSRF 防線與 HTML 轉純文字。
自建擷取使用者代理 = "Mozilla/5.0 (compatible; testagent2/0.1; +https://example.invalid/bot)"
可擷取內容型別 = ("text/html", "application/xhtml+xml", "text/plain")
略過內文標籤 = frozenset({"script", "style", "noscript", "template", "svg"})
換行標籤 = frozenset({
    "p", "br", "div", "section", "article", "header", "footer", "li", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
})
連續空白樣式 = re.compile(r"[ \t　]+")
連續換行樣式 = re.compile(r"\n{3,}")
最大轉址次數 = 3
轉址狀態碼 = frozenset({301, 302, 303, 307, 308})


class 後端未設定(RuntimeError):
    """後端缺少必要環境變數時拋出；handler 會轉成可讀的失敗結果。"""


class 後端呼叫失敗(RuntimeError):
    """後端連線或回應異常時拋出；handler 會轉成可讀的失敗結果。"""


@dataclass(frozen=True)
class 搜尋結果項目:
    """單筆搜尋結果。"""

    標題: str
    網址: str
    摘要: str

    def 轉成字典(self) -> dict[str, Any]:
        """轉成回傳給模型的欄位。"""
        return {"title": self.標題, "url": self.網址, "description": self.摘要}


@dataclass(frozen=True)
class 擷取結果項目:
    """單一網址的擷取結果。"""

    網址: str
    內容: str
    是否截斷: bool
    原始字元數: int

    def 轉成字典(self) -> dict[str, Any]:
        """轉成回傳給模型的欄位。"""
        return {
            "url": self.網址,
            "content": self.內容,
            "truncated": self.是否截斷,
            "original_length": self.原始字元數,
        }


class 搜尋後端(Protocol):
    """web_search 與 web_extract 共用的後端協定。"""

    名稱: str

    def 搜尋(self, 查詢: str, 限制: int) -> list[搜尋結果項目]:
        """執行搜尋並回傳結果清單。"""
        ...

    def 擷取(self, 網址清單: list[str]) -> tuple[list[擷取結果項目], list[dict[str, str]]]:
        """擷取網頁內容，回傳（成功清單, 失敗清單）。"""
        ...


def 讀取逾時秒數() -> int:
    """讀取單次請求逾時秒數；設定無效時退回預設值。

    參數：無。
    返回值：int，至少 1 秒。
    """
    載入本機環境檔()
    原始值 = os.getenv("WEB_SEARCH_TIMEOUT", "").strip()
    if not 原始值:
        return 預設逾時秒數
    try:
        return max(1, int(原始值))
    except ValueError:
        return 預設逾時秒數


class Tavily後端:
    """Tavily 後端；同一把金鑰同時支援搜尋與擷取。"""

    名稱 = "tavily"

    def __init__(self, 金鑰: str, 逾時秒數: int) -> None:
        """保存金鑰與逾時設定。

        參數：
            金鑰: Tavily API key。
            逾時秒數: 單次請求逾時。
        返回值：無。
        """
        self.金鑰 = 金鑰
        self.逾時秒數 = 逾時秒數

    def _送出(self, 網址: str, 本文: dict[str, Any]) -> dict[str, Any]:
        """對 Tavily 發出 POST 並回傳已解析的 JSON 物件。

        參數：
            網址: Tavily 端點。
            本文: 請求 JSON。
        返回值：dict；連線失敗、非 2xx 或非物件回應時丟出 後端呼叫失敗。
        """
        標頭 = {"Authorization": f"Bearer {self.金鑰}", "Content-Type": "application/json"}
        try:
            回應 = httpx.post(網址, json=本文, headers=標頭, timeout=self.逾時秒數)
        except httpx.HTTPError as 錯誤:
            raise 後端呼叫失敗(f"連線 Tavily 失敗：{type(錯誤).__name__}") from None
        if 回應.status_code == 401:
            raise 後端未設定("TAVILY_API_KEY 遭 Tavily 拒絕（401）")
        if 回應.status_code >= 400:
            raise 後端呼叫失敗(f"Tavily 回應狀態碼 {回應.status_code}")
        try:
            資料 = 回應.json()
        except ValueError:
            raise 後端呼叫失敗("Tavily 回應不是合法 JSON") from None
        if type(資料) is not dict:
            raise 後端呼叫失敗("Tavily 回應格式非預期")
        return 資料

    def 搜尋(self, 查詢: str, 限制: int) -> list[搜尋結果項目]:
        """呼叫 Tavily search 並整理成統一結果格式。

        參數：
            查詢: 使用者查詢字串。
            限制: 最多回傳幾筆。
        返回值：搜尋結果項目清單。
        """
        資料 = self._送出(Tavily搜尋網址, {"query": 查詢, "max_results": 限制})
        原始清單 = 資料.get("results")
        if type(原始清單) is not list:
            return []
        結果清單: list[搜尋結果項目] = []
        for 原始項目 in 原始清單[:限制]:
            if type(原始項目) is not dict:
                continue
            網址 = str(原始項目.get("url") or "").strip()
            if not 網址:
                continue
            結果清單.append(搜尋結果項目(
                標題=str(原始項目.get("title") or "").strip(),
                網址=網址,
                摘要=str(原始項目.get("content") or "").strip(),
            ))
        return 結果清單

    def 擷取(self, 網址清單: list[str]) -> tuple[list[擷取結果項目], list[dict[str, str]]]:
        """呼叫 Tavily extract 並套用長度上限。

        參數：
            網址清單: 已驗證的網址清單。
        返回值：（成功項目清單, 失敗項目清單）。
        """
        資料 = self._送出(Tavily擷取網址, {"urls": 網址清單})
        成功清單: list[擷取結果項目] = []
        失敗清單: list[dict[str, str]] = []
        原始成功 = 資料.get("results")
        if type(原始成功) is list:
            for 原始項目 in 原始成功:
                if type(原始項目) is not dict:
                    continue
                網址 = str(原始項目.get("url") or "").strip()
                內容 = str(原始項目.get("raw_content") or "")
                if not 網址:
                    continue
                項目 = 建立擷取項目(網址, 內容)
                if 項目 is None:
                    失敗清單.append({"url": 網址, "error": f"內容超過 {拒絕擷取字元上限} 字元上限，已拒絕"})
                    continue
                成功清單.append(項目)
        原始失敗 = 資料.get("failed_results")
        if type(原始失敗) is list:
            for 原始項目 in 原始失敗:
                if type(原始項目) is not dict:
                    continue
                失敗清單.append({
                    "url": str(原始項目.get("url") or "").strip(),
                    "error": str(原始項目.get("error") or "擷取失敗"),
                })
        return 成功清單, 失敗清單


class 純文字擷取器(HTMLParser):
    """把 HTML 轉成可讀純文字；丟棄 script/style 等非內文標籤。"""

    def __init__(self) -> None:
        """初始化輸出緩衝與略過深度。"""
        super().__init__(convert_charrefs=True)
        self.片段清單: list[str] = []
        self.略過深度 = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        """進入標籤；非內文標籤會提高略過深度。"""
        if tag in 略過內文標籤:
            self.略過深度 += 1
        elif tag in 換行標籤:
            self.片段清單.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """離開標籤；非內文標籤會降低略過深度。"""
        if tag in 略過內文標籤:
            self.略過深度 = max(0, self.略過深度 - 1)
        elif tag in 換行標籤:
            self.片段清單.append("\n")

    def handle_data(self, data: str) -> None:
        """收集內文文字；位於略過區塊時丟棄。"""
        if self.略過深度 == 0 and data.strip():
            self.片段清單.append(data)

    def 取得文字(self) -> str:
        """組合並正規化收集到的文字。"""
        原始 = "".join(self.片段清單)
        整理 = 連續空白樣式.sub(" ", 原始)
        整理 = "\n".join(行.strip() for 行 in 整理.split("\n"))
        return 連續換行樣式.sub("\n\n", 整理).strip()


def 轉純文字(html內容: str) -> str:
    """把 HTML 轉成純文字。

    參數：
        html內容: 原始 HTML。
    返回值：str。解析失敗時退回原始字串。
    """
    擷取器 = 純文字擷取器()
    try:
        擷取器.feed(html內容)
        擷取器.close()
    except Exception:
        return html內容
    return 擷取器.取得文字()


def 分類連線錯誤(錯誤: Exception) -> str:
    """把 httpx 連線例外轉成能指出成因的訊息。

    憑證失敗與網路不通在 httpx 都是 ConnectError，但處置方式完全不同：前者要嘛
    對方修憑證、要嘛改用在別處抓取的後端（如 tavily），重試永遠不會成功。實測
    多個 `.gov.tw` 站憑證缺少 Subject Key Identifier，屬於此類。

    參數：
        錯誤: httpx 丟出的例外。
    返回值：str，供失敗清單顯示的原因。
    """
    描述 = str(錯誤)
    if "CERTIFICATE_VERIFY_FAILED" in 描述 or "SSLError" in type(錯誤).__name__:
        原因 = 描述.split("certificate verify failed:")[-1].strip() or "憑證驗證失敗"
        return f"憑證驗證失敗（對方憑證不符合現行標準，非本機網路問題）：{原因[:80]}"
    if isinstance(錯誤, httpx.TimeoutException):
        return "逾時：對方未在時限內回應"
    return f"連線失敗：{type(錯誤).__name__}"


def 是否內部位址(位址字串: str) -> bool:
    """判斷單一 IP 是否屬於不可從工具存取的內部範圍。

    參數：
        位址字串: IPv4 或 IPv6 位址字串。
    返回值：bool。無法解析成 IP 時回傳 False。
    """
    try:
        位址 = ipaddress.ip_address(位址字串)
    except ValueError:
        return False
    return bool(位址.is_private or 位址.is_loopback or 位址.is_link_local
                or 位址.is_reserved or 位址.is_multicast or 位址.is_unspecified)


def 確認非內部位址(主機: str) -> None:
    """在送出請求前先擋掉解析結果指向內部的主機。

    這是便宜的第一道關卡，會擋掉直接填內網位址或內網網域的情況。它單獨無法防
    DNS rebinding（解析與實際連線是兩次獨立查詢），因此建立連線後必須再以
    `確認連線對象非內部` 檢查真正連到的位址。

    參數：
        主機: 網址中的主機名稱。
    返回值：None；指向內部位址時丟出 ValueError。
    """
    try:
        位址資訊 = socket.getaddrinfo(主機, None)
    except OSError:
        raise ValueError(f"無法解析主機名稱：{主機}") from None
    for 項目 in 位址資訊:
        if 是否內部位址(項目[4][0]):
            raise ValueError(f"拒絕存取內部位址：{主機}")


def 取得連線對象位址(回應: Any) -> str | None:
    """從已建立的連線取出實際連到的伺服器 IP。

    參數：
        回應: httpx 串流回應。
    返回值：IP 字串；取不到時回傳 None。
    """
    串流 = 回應.extensions.get("network_stream")
    if 串流 is None:
        return None
    try:
        位址 = 串流.get_extra_info("server_addr")
    except OSError:
        return None
    if isinstance(位址, (tuple, list)) and 位址:
        return str(位址[0])
    return None


def 確認連線對象非內部(回應: Any) -> None:
    """檢查實際建立的連線是否指向內部位址。

    這道關卡問的是「這條連線真正接到哪台機器」，答案來自 socket 本身而非再一次
    DNS 查詢，因此 DNS rebinding 騙不過它。檢查發生在讀取 body 之前，內部內容
    連進入記憶體的機會都沒有。

    參數：
        回應: 尚未讀取 body 的 httpx 串流回應。
    返回值：None；連線指向內部位址時丟出 ValueError。
    """
    位址 = 取得連線對象位址(回應)
    if 位址 is not None and 是否內部位址(位址):
        raise ValueError(f"拒絕存取內部位址：連線實際指向 {位址}")


def 建立擷取項目(網址: str, 內容: str) -> 擷取結果項目 | None:
    """套用字元上限並標記是否截斷。

    參數：
        網址: 來源網址。
        內容: 後端回傳的原始內容。
    返回值：擷取結果項目；超過拒絕上限時回傳 None。
    """
    原始字元數 = len(內容)
    if 原始字元數 > 拒絕擷取字元上限:
        return None
    if 原始字元數 > 單頁最大字元:
        return 擷取結果項目(網址, 內容[:單頁最大字元], True, 原始字元數)
    return 擷取結果項目(網址, 內容, False, 原始字元數)


class DDGS後端:
    """免金鑰後端：搜尋走 DuckDuckGo，擷取由本機自行抓取轉純文字。

    取捨：不需要任何金鑰，但 ddgs 是非官方介面，DuckDuckGo 改版或判定為機器人
    時會失效；伺服器部署下全公司共用單一出口 IP，較容易觸發限流。擷取端不支援
    PDF 與需要 JavaScript 才渲染的頁面。
    """

    名稱 = "ddgs"

    def __init__(self, 逾時秒數: int) -> None:
        """保存逾時設定。

        參數：
            逾時秒數: 單次請求逾時。
        返回值：無。
        """
        self.逾時秒數 = 逾時秒數

    def 搜尋(self, 查詢: str, 限制: int) -> list[搜尋結果項目]:
        """呼叫 DuckDuckGo 並整理成統一結果格式。

        參數：
            查詢: 使用者查詢字串。
            限制: 最多回傳幾筆。
        返回值：搜尋結果項目清單；套件缺失或查詢失敗時丟出對應錯誤。
        """
        try:
            from ddgs import DDGS
        except ImportError:
            raise 後端未設定("ddgs 後端需要 ddgs 套件：pip install ddgs") from None
        try:
            原始清單 = DDGS(timeout=self.逾時秒數).text(查詢, max_results=限制)
        except Exception as 錯誤:
            raise 後端呼叫失敗(f"DuckDuckGo 查詢失敗：{type(錯誤).__name__}") from None
        if type(原始清單) is not list:
            return []
        結果清單: list[搜尋結果項目] = []
        for 原始項目 in 原始清單[:限制]:
            if type(原始項目) is not dict:
                continue
            網址 = str(原始項目.get("href") or "").strip()
            if not 網址:
                continue
            結果清單.append(搜尋結果項目(
                標題=str(原始項目.get("title") or "").strip(),
                網址=網址,
                摘要=str(原始項目.get("body") or "").strip(),
            ))
        return 結果清單

    def _抓取單頁(self, 網址: str) -> str:
        """抓取單一網址並轉成純文字。

        轉址由本方法自行處理而非交給 httpx，因為每一跳都必須重新驗證目標網址與
        實際連線對象；交給 httpx 自動跟隨會讓中間跳躍逃過檢查。

        參數：
            網址: 已通過協定驗證的網址。
        返回值：純文字內容；不可擷取時丟出 ValueError。
        """
        目前網址 = 網址
        標頭 = {"User-Agent": 自建擷取使用者代理}
        with httpx.Client(timeout=self.逾時秒數, follow_redirects=False) as 客戶端:
            for 剩餘轉址 in range(最大轉址次數, -1, -1):
                確認非內部位址(urlsplit(目前網址).hostname or "")
                try:
                    with 客戶端.stream("GET", 目前網址, headers=標頭) as 回應:
                        確認連線對象非內部(回應)
                        if 回應.status_code in 轉址狀態碼:
                            下一站 = (回應.headers.get("location") or "").strip()
                            if not 下一站:
                                raise ValueError(f"HTTP {回應.status_code} 但未提供轉址目標")
                            if 剩餘轉址 <= 0:
                                raise ValueError(f"轉址超過 {最大轉址次數} 次上限")
                            目前網址 = 驗證網址(urljoin(目前網址, 下一站))
                            continue
                        if 回應.status_code >= 400:
                            raise ValueError(f"HTTP {回應.status_code}")
                        內容型別 = (回應.headers.get("content-type") or "").split(";")[0].strip().lower()
                        if 內容型別 and not 內容型別.startswith(可擷取內容型別):
                            raise ValueError(
                                f"不支援的內容型別：{內容型別}（自建擷取不處理 PDF 或二進位檔）"
                            )
                        回應.read()
                        return 回應.text if 內容型別 == "text/plain" else 轉純文字(回應.text)
                except httpx.HTTPError as 錯誤:
                    raise ValueError(分類連線錯誤(錯誤)) from None
        raise ValueError(f"轉址超過 {最大轉址次數} 次上限")

    def 擷取(self, 網址清單: list[str]) -> tuple[list[擷取結果項目], list[dict[str, str]]]:
        """逐一抓取網址，套用長度上限並分流成功與失敗。

        參數：
            網址清單: 已驗證的網址清單。
        返回值：（成功項目清單, 失敗項目清單）。
        """
        成功清單: list[擷取結果項目] = []
        失敗清單: list[dict[str, str]] = []
        for 網址 in 網址清單:
            try:
                內容 = self._抓取單頁(網址)
            except ValueError as 錯誤:
                失敗清單.append({"url": 網址, "error": str(錯誤)})
                continue
            項目 = 建立擷取項目(網址, 內容)
            if 項目 is None:
                失敗清單.append({"url": 網址, "error": f"內容超過 {拒絕擷取字元上限} 字元上限，已拒絕"})
                continue
            成功清單.append(項目)
        return 成功清單, 失敗清單


def 建立DDGS後端(逾時秒數: int) -> 搜尋後端:
    """建立免金鑰的 DuckDuckGo 後端。

    參數：
        逾時秒數: 單次請求逾時。
    返回值：搜尋後端。此後端不需要任何環境變數。
    """
    return DDGS後端(逾時秒數)


def 建立Tavily後端(逾時秒數: int) -> 搜尋後端:
    """依環境變數建立 Tavily 後端。

    參數：
        逾時秒數: 單次請求逾時。
    返回值：搜尋後端；缺少金鑰時丟出 後端未設定。
    """
    金鑰 = os.getenv("TAVILY_API_KEY", "").strip()
    if not 金鑰:
        raise 後端未設定("網路搜尋尚未設定：缺少 TAVILY_API_KEY")
    return Tavily後端(金鑰, 逾時秒數)


後端建構表 = {"ddgs": 建立DDGS後端, "tavily": 建立Tavily後端}


def 建立搜尋後端() -> 搜尋後端:
    """依 WEB_SEARCH_BACKEND 建立後端實例。

    參數：無。
    返回值：搜尋後端；後端名稱未知或缺少設定時丟出 後端未設定。
    """
    載入本機環境檔()
    名稱 = (os.getenv("WEB_SEARCH_BACKEND", "").strip() or 預設後端).lower()
    建構函數 = 後端建構表.get(名稱)
    if 建構函數 is None:
        可用 = "、".join(sorted(後端建構表))
        raise 後端未設定(f"WEB_SEARCH_BACKEND 不支援的後端：{名稱}（可用：{可用}）")
    return 建構函數(讀取逾時秒數())


def 解析結果限制(參數: dict[str, Any]) -> int:
    """解析 limit 參數並夾在合法範圍內。

    參數：
        參數: 工具呼叫參數。
    返回值：int，介於 1 與 最大結果數量 之間。
    """
    原始值 = 參數.get("limit", 預設結果數量)
    if 原始值 is None:
        return 預設結果數量
    try:
        限制 = int(原始值)
    except (TypeError, ValueError):
        raise ValueError("limit 必須是整數") from None
    return max(1, min(限制, 最大結果數量))


def 驗證網址(網址值: Any) -> str:
    """驗證單一網址協定與形狀。

    參數：
        網址值: 工具參數中的網址。
    返回值：整理後的網址字串；不合法時丟出 ValueError。
    """
    if type(網址值) is not str:
        raise ValueError("urls 只接受字串")
    網址 = 網址值.strip()
    if not 網址:
        raise ValueError("urls 不可包含空字串")
    切分 = urlsplit(網址)
    if 切分.scheme.lower() not in 允許協定:
        raise ValueError(f"只接受 http 或 https 網址：{網址}")
    if not 切分.netloc:
        raise ValueError(f"網址缺少主機名稱：{網址}")
    return 網址


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
        raise ValueError(f"urls 一次最多 {最大網址數量} 個")
    網址清單: list[str] = []
    for 原始網址 in 原始清單:
        網址 = 驗證網址(原始網址)
        if 網址 not in 網址清單:
            網址清單.append(網址)
    return 網址清單


def 建立後端失敗結果(工具名稱: str, 錯誤: Exception) -> dict[str, Any]:
    """把後端設定或呼叫失敗轉成 canonical 失敗結果。

    `工具結果._正規化回傳` 只讓 recoverable 與 permission_denied 兩個旗標通過，
    handler 自訂的錯誤字串一律被固定訊息取代。因此原因寫進 log 給維運看，
    給模型的訊號只有「是否值得重試」：設定缺漏不可恢復，連線失敗可恢復。

    參數：
        工具名稱: 發生失敗的工具。
        錯誤: 後端未設定或後端呼叫失敗。
    返回值：dict，含 success=False 與 recoverable 旗標。
    """
    可恢復 = type(錯誤) is 後端呼叫失敗
    logger.warning("%s 失敗（可恢復=%s）：%s", 工具名稱, 可恢復, 錯誤)
    return {
        "success": False,
        "tool": 工具名稱,
        "error": str(錯誤),
        "recoverable": 可恢復,
    }


def 網路搜尋(參數: dict[str, Any]) -> dict[str, Any]:
    """執行 `web_search`：查詢網路並回傳標題、網址與摘要。

    參數：
        參數: 工具呼叫參數，需含 query，可選 limit。
    返回值：dict，含 query、backend、results、total_count；
        後端未設定或呼叫失敗時回傳 success=False 與訊息。
    """
    查詢 = str(參數.get("query") or "").strip()
    if not 查詢:
        raise ValueError("query 不可為空")
    限制 = 解析結果限制(參數)
    try:
        後端 = 建立搜尋後端()
        結果清單 = 後端.搜尋(查詢, 限制)
    except (後端未設定, 後端呼叫失敗) as 錯誤:
        return 建立後端失敗結果("web_search", 錯誤)
    return {
        "query": 查詢,
        "backend": 後端.名稱,
        "results": [項目.轉成字典() for 項目 in 結果清單],
        "total_count": len(結果清單),
    }


def 網頁擷取(參數: dict[str, Any]) -> dict[str, Any]:
    """執行 `web_extract`：抓取網址內容並轉成純文字。

    參數：
        參數: 工具呼叫參數，需含 urls（最多 最大網址數量 個）。
    返回值：dict，含 backend、results、failed、total_count；
        後端未設定或呼叫失敗時回傳 success=False 與訊息。
    """
    網址清單 = 解析網址清單(參數)
    try:
        後端 = 建立搜尋後端()
        成功清單, 失敗清單 = 後端.擷取(網址清單)
    except (後端未設定, 後端呼叫失敗) as 錯誤:
        return 建立後端失敗結果("web_extract", 錯誤)
    return {
        "backend": 後端.名稱,
        "results": [項目.轉成字典() for 項目 in 成功清單],
        "failed": 失敗清單,
        "total_count": len(成功清單),
    }
