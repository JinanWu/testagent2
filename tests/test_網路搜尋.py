"""測試 web_search 與 web_extract 工具。"""

import sys
import types

import httpx
import pytest

from 繁中代理.工具集 import 網路搜尋 as 模組
from 繁中代理.工具集.網路搜尋 import (
    DDGS後端,
    Tavily後端,
    後端呼叫失敗,
    後端未設定,
    擷取結果項目,
    搜尋結果項目,
    網頁擷取,
    網路搜尋,
    轉純文字,
    確認非內部位址,
)


class 假回應:
    """模擬 httpx 回應。"""

    def __init__(self, 狀態碼, 資料=None, 是否合法JSON=True, 標頭=None, 文字=""):
        """保存狀態碼、JSON 本文、標頭與純文字本文。"""
        self.status_code = 狀態碼
        self.headers = 標頭 or {}
        self.text = 文字
        self._資料 = 資料
        self._是否合法JSON = 是否合法JSON

    def json(self):
        """回傳預設資料；模擬非 JSON 時丟出 ValueError。"""
        if not self._是否合法JSON:
            raise ValueError("not json")
        return self._資料


class 假後端:
    """記錄呼叫參數的 fake 後端。"""

    名稱 = "假後端"

    def __init__(self, 搜尋回應=None, 擷取回應=None):
        """保存預設回應與呼叫紀錄。"""
        self.搜尋回應 = 搜尋回應 or []
        self.擷取回應 = 擷取回應 or ([], [])
        self.收到查詢 = None
        self.收到限制 = None
        self.收到網址清單 = None

    def 搜尋(self, 查詢, 限制):
        """記錄參數並回傳預設搜尋結果。"""
        self.收到查詢 = 查詢
        self.收到限制 = 限制
        return self.搜尋回應

    def 擷取(self, 網址清單):
        """記錄參數並回傳預設擷取結果。"""
        self.收到網址清單 = 網址清單
        return self.擷取回應


def 釘住後端(monkeypatch, 後端):
    """讓 建立搜尋後端 固定回傳指定 fake 後端。"""
    monkeypatch.setattr(模組, "建立搜尋後端", lambda: 後端)
    return 後端


def test_搜尋把後端結果整理成統一欄位(monkeypatch):
    """確認回傳 title/url/description 與 total_count。"""
    後端 = 釘住後端(monkeypatch, 假後端(搜尋回應=[
        搜尋結果項目("標題A", "https://a.example", "摘要A"),
        搜尋結果項目("標題B", "https://b.example", "摘要B"),
    ]))

    結果 = 網路搜尋({"query": "  台灣 勞基法  "})

    assert 後端.收到查詢 == "台灣 勞基法"
    assert 後端.收到限制 == 5
    assert 結果["backend"] == "假後端"
    assert 結果["total_count"] == 2
    assert 結果["results"][0] == {
        "title": "標題A", "url": "https://a.example", "description": "摘要A",
    }


def test_搜尋limit夾在合法範圍(monkeypatch):
    """確認 limit 超過上限會被夾到 100，低於 1 會被拉到 1。"""
    後端 = 釘住後端(monkeypatch, 假後端())

    網路搜尋({"query": "x", "limit": 9999})
    assert 後端.收到限制 == 100

    網路搜尋({"query": "x", "limit": 0})
    assert 後端.收到限制 == 1


def test_搜尋空查詢遭拒(monkeypatch):
    """確認空白 query 直接拒絕。"""
    釘住後端(monkeypatch, 假後端())

    with pytest.raises(ValueError):
        網路搜尋({"query": "   "})


def test_設定缺漏標記為不可恢復(monkeypatch):
    """確認缺金鑰不是暫時性失敗，模型不該重試。"""
    def 拋出未設定():
        raise 後端未設定("網路搜尋尚未設定：缺少 TAVILY_API_KEY")

    monkeypatch.setattr(模組, "建立搜尋後端", 拋出未設定)

    結果 = 網路搜尋({"query": "x"})

    assert 結果["success"] is False
    assert 結果["tool"] == "web_search"
    assert 結果["recoverable"] is False


def test_連線失敗標記為可恢復(monkeypatch):
    """確認暫時性失敗會讓模型知道值得重試。"""
    def 拋出失敗():
        raise 後端呼叫失敗("連線 Tavily 失敗：ConnectError")

    monkeypatch.setattr(模組, "建立搜尋後端", 拋出失敗)

    結果 = 網頁擷取({"urls": ["https://a.example"]})

    assert 結果["success"] is False
    assert 結果["tool"] == "web_extract"
    assert 結果["recoverable"] is True


def test_經由工具登錄器時可恢復旗標會傳到模型(monkeypatch):
    """確認 recoverable 是唯一能穿過 工具結果 契約層的失敗訊號。"""
    import json

    from 繁中代理.工具註冊 import 建立預設工具登錄器

    def 拋出失敗():
        raise 後端呼叫失敗("連線 Tavily 失敗：ConnectError")

    monkeypatch.setattr(模組, "建立搜尋後端", 拋出失敗)
    登錄器 = 建立預設工具登錄器()

    結果 = json.loads(登錄器.呼叫工具("web_search", {"query": "x"}))

    assert 結果["success"] is False
    assert 結果["recoverable"] is True
    # 契約層以固定訊息取代 handler 字串，後端細節不會外洩給模型。
    assert "Tavily" not in json.dumps(結果, ensure_ascii=False)


def test_擷取拒絕非http協定(monkeypatch):
    """確認 file:// 與 data: 這類協定被擋下。"""
    釘住後端(monkeypatch, 假後端())

    for 壞網址 in ["file:///etc/passwd", "data:text/html,<h1>x", "ftp://a.example/x"]:
        with pytest.raises(ValueError):
            網頁擷取({"urls": [壞網址]})


def test_擷取拒絕缺主機的網址(monkeypatch):
    """確認 https:/// 這種缺 netloc 的網址被擋下。"""
    釘住後端(monkeypatch, 假後端())

    with pytest.raises(ValueError):
        網頁擷取({"urls": ["https:///no-host"]})


def test_擷取限制網址數量並去重(monkeypatch):
    """確認超過 5 個拒絕，重複網址只留一份。"""
    後端 = 釘住後端(monkeypatch, 假後端())

    with pytest.raises(ValueError):
        網頁擷取({"urls": [f"https://a{序號}.example" for 序號 in range(6)]})

    網頁擷取({"urls": ["https://a.example", "https://a.example", "https://b.example"]})
    assert 後端.收到網址清單 == ["https://a.example", "https://b.example"]


def test_擷取空清單遭拒(monkeypatch):
    """確認 urls 必須是非空陣列。"""
    釘住後端(monkeypatch, 假後端())

    with pytest.raises(ValueError):
        網頁擷取({"urls": []})
    with pytest.raises(ValueError):
        網頁擷取({"urls": "https://a.example"})


def test_擷取回傳截斷旗標(monkeypatch):
    """確認超過單頁上限的內容會標記 truncated 與原始長度。"""
    釘住後端(monkeypatch, 假後端(擷取回應=(
        [擷取結果項目("https://a.example", "x" * 5000, True, 12345)],
        [{"url": "https://b.example", "error": "timeout"}],
    )))

    結果 = 網頁擷取({"urls": ["https://a.example", "https://b.example"]})

    assert 結果["total_count"] == 1
    assert 結果["results"][0]["truncated"] is True
    assert 結果["results"][0]["original_length"] == 12345
    assert 結果["failed"][0]["error"] == "timeout"


def test_建立擷取項目套用上限():
    """確認短內容不截斷、長內容截斷、超大內容拒絕。"""
    短項目 = 模組.建立擷取項目("https://a.example", "短內容")
    assert 短項目.是否截斷 is False
    assert 短項目.內容 == "短內容"

    長項目 = 模組.建立擷取項目("https://a.example", "y" * (模組.單頁最大字元 + 100))
    assert 長項目.是否截斷 is True
    assert len(長項目.內容) == 模組.單頁最大字元
    assert 長項目.原始字元數 == 模組.單頁最大字元 + 100

    assert 模組.建立擷取項目("https://a.example", "z" * (模組.拒絕擷取字元上限 + 1)) is None


def test_未知後端名稱給出可用清單(monkeypatch):
    """確認 WEB_SEARCH_BACKEND 打錯時錯誤訊息列出可用後端。"""
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "不存在的後端")

    with pytest.raises(後端未設定) as 錯誤資訊:
        模組.建立搜尋後端()

    assert "tavily" in str(錯誤資訊.value)


def test_缺金鑰時建立後端拋出未設定(monkeypatch):
    """確認沒有 TAVILY_API_KEY 時明確指出缺哪一個變數。"""
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    with pytest.raises(後端未設定) as 錯誤資訊:
        模組.建立搜尋後端()

    assert "TAVILY_API_KEY" in str(錯誤資訊.value)


def test_tavily搜尋送出正確請求並解析結果(monkeypatch):
    """確認帶 Bearer 標頭、送出 max_results，並略過缺網址的項目。"""
    紀錄 = {}

    def 假post(網址, json=None, headers=None, timeout=None):
        """記錄請求並回傳 fake 搜尋回應。"""
        紀錄.update(網址=網址, 本文=json, 標頭=headers, 逾時=timeout)
        return 假回應(200, {"results": [
            {"title": "T1", "url": "https://a.example", "content": "C1"},
            {"title": "缺網址", "url": "", "content": "略過"},
        ]})

    monkeypatch.setattr(httpx, "post", 假post)

    結果清單 = Tavily後端("tvly-test", 20).搜尋("查詢字", 3)

    assert 紀錄["網址"] == 模組.Tavily搜尋網址
    assert 紀錄["本文"] == {"query": "查詢字", "max_results": 3}
    assert 紀錄["標頭"]["Authorization"] == "Bearer tvly-test"
    assert 紀錄["逾時"] == 20
    assert len(結果清單) == 1
    assert 結果清單[0].網址 == "https://a.example"


def test_tavily金鑰遭拒轉成未設定(monkeypatch):
    """確認 401 歸類為設定問題而非暫時性失敗。"""
    monkeypatch.setattr(httpx, "post", lambda *參數, **具名參數: 假回應(401))

    with pytest.raises(後端未設定):
        Tavily後端("tvly-bad", 20).搜尋("x", 5)


def test_tavily伺服器錯誤轉成呼叫失敗(monkeypatch):
    """確認 5xx 歸類為呼叫失敗並帶狀態碼。"""
    monkeypatch.setattr(httpx, "post", lambda *參數, **具名參數: 假回應(503))

    with pytest.raises(後端呼叫失敗) as 錯誤資訊:
        Tavily後端("tvly-test", 20).搜尋("x", 5)

    assert "503" in str(錯誤資訊.value)


def test_tavily連線錯誤轉成呼叫失敗(monkeypatch):
    """確認 httpx 例外不會直接外漏。"""
    def 假post(*參數, **具名參數):
        """模擬連線失敗。"""
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", 假post)

    with pytest.raises(後端呼叫失敗):
        Tavily後端("tvly-test", 20).搜尋("x", 5)


def test_tavily非JSON回應轉成呼叫失敗(monkeypatch):
    """確認回應不是 JSON 時給出明確錯誤。"""
    monkeypatch.setattr(httpx, "post", lambda *參數, **具名參數: 假回應(200, 是否合法JSON=False))

    with pytest.raises(後端呼叫失敗):
        Tavily後端("tvly-test", 20).搜尋("x", 5)


def test_tavily擷取分開回傳成功與失敗(monkeypatch):
    """確認 results 與 failed_results 分流，且超大內容轉為失敗。"""
    def 假post(網址, json=None, headers=None, timeout=None):
        """回傳含成功、超大與失敗三種項目的擷取回應。"""
        return 假回應(200, {
            "results": [
                {"url": "https://ok.example", "raw_content": "正常內容"},
                {"url": "https://huge.example", "raw_content": "z" * (模組.拒絕擷取字元上限 + 1)},
            ],
            "failed_results": [{"url": "https://bad.example", "error": "404"}],
        })

    monkeypatch.setattr(httpx, "post", 假post)

    成功清單, 失敗清單 = Tavily後端("tvly-test", 20).擷取(["https://ok.example"])

    assert [項目.網址 for 項目 in 成功清單] == ["https://ok.example"]
    失敗網址表 = {項目["url"]: 項目["error"] for 項目 in 失敗清單}
    assert "上限" in 失敗網址表["https://huge.example"]
    assert 失敗網址表["https://bad.example"] == "404"


def test_逾時秒數設定無效時退回預設(monkeypatch):
    """確認 WEB_SEARCH_TIMEOUT 亂填不會炸掉。"""
    monkeypatch.setenv("WEB_SEARCH_TIMEOUT", "abc")
    assert 模組.讀取逾時秒數() == 模組.預設逾時秒數

    monkeypatch.setenv("WEB_SEARCH_TIMEOUT", "45")
    assert 模組.讀取逾時秒數() == 45

    monkeypatch.setenv("WEB_SEARCH_TIMEOUT", "0")
    assert 模組.讀取逾時秒數() == 1


def test_預設後端為ddgs(monkeypatch):
    """確認未設定 WEB_SEARCH_BACKEND 時走免金鑰的 ddgs。"""
    monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
    assert 模組.預設後端 == "ddgs"
    assert 模組.建立搜尋後端().名稱 == "ddgs"


def test_ddgs搜尋整理title_href_body(monkeypatch):
    """確認 ddgs 的欄位名被對應到統一格式，並略過缺網址的項目。"""
    紀錄 = {}

    class 假DDGS:
        """模擬 ddgs.DDGS。"""

        def __init__(self, timeout=None):
            """記錄逾時參數。"""
            紀錄["逾時"] = timeout

        def text(self, query, max_results=None):
            """記錄查詢並回傳 fake 結果。"""
            紀錄.update(查詢=query, 筆數=max_results)
            return [
                {"title": "T1", "href": "https://a.example", "body": "B1"},
                {"title": "缺網址", "href": "", "body": "略過"},
            ]

    模擬模組 = types.ModuleType("ddgs")
    模擬模組.DDGS = 假DDGS
    monkeypatch.setitem(sys.modules, "ddgs", 模擬模組)

    結果清單 = DDGS後端(20).搜尋("查詢字", 3)

    assert 紀錄 == {"逾時": 20, "查詢": "查詢字", "筆數": 3}
    assert len(結果清單) == 1
    assert 結果清單[0].標題 == "T1"
    assert 結果清單[0].網址 == "https://a.example"
    assert 結果清單[0].摘要 == "B1"


def test_ddgs套件缺失轉成未設定(monkeypatch):
    """確認沒裝 ddgs 時給出可安裝的指示，且歸類為不可恢復。"""
    monkeypatch.setitem(sys.modules, "ddgs", None)

    with pytest.raises(後端未設定) as 錯誤資訊:
        DDGS後端(20).搜尋("x", 5)

    assert "pip install ddgs" in str(錯誤資訊.value)


def test_ddgs查詢例外轉成呼叫失敗(monkeypatch):
    """確認 ddgs 內部例外不外漏，並歸類為可恢復。"""
    class 假DDGS:
        """查詢時丟例外的 fake。"""

        def __init__(self, timeout=None):
            """不做事。"""

        def text(self, query, max_results=None):
            """模擬被限流。"""
            raise RuntimeError("ratelimit")

    模擬模組 = types.ModuleType("ddgs")
    模擬模組.DDGS = 假DDGS
    monkeypatch.setitem(sys.modules, "ddgs", 模擬模組)

    with pytest.raises(後端呼叫失敗):
        DDGS後端(20).搜尋("x", 5)


def test_SSRF擋掉內部位址():
    """確認 localhost 與私有網段被拒絕，公開網域放行。"""
    for 內部主機 in ["localhost", "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254"]:
        with pytest.raises(ValueError, match="內部位址"):
            確認非內部位址(內部主機)

    確認非內部位址("example.com")


def test_SSRF無法解析的主機被拒絕():
    """確認解析不到的主機直接拒絕，而不是放行。"""
    with pytest.raises(ValueError, match="無法解析"):
        確認非內部位址("this-host-should-not-exist.invalid")


def test_轉純文字丟棄script與style():
    """確認 script/style 內容不會混進擷取結果。"""
    html = (
        "<html><head><style>.a{color:red}</style>"
        "<script>var x=1;</script></head>"
        "<body><h1>標題</h1><p>第一段</p><p>第二段</p></body></html>"
    )

    文字 = 轉純文字(html)

    assert "color:red" not in 文字
    assert "var x" not in 文字
    assert "標題" in 文字
    assert "第一段" in 文字
    assert "第二段" in 文字


class 假網路串流:
    """模擬 httpx 的 network_stream，回報實際連線位址。"""

    def __init__(self, 位址):
        """保存要回報的伺服器位址。"""
        self._位址 = 位址

    def get_extra_info(self, 名稱):
        """只回應 server_addr，其餘回 None。"""
        return (self._位址, 443) if 名稱 == "server_addr" else None


class 假串流回應:
    """模擬 httpx 串流回應；body 需要明確 read 才可用。"""

    def __init__(self, 狀態碼, 標頭=None, 文字="", 連線位址="93.184.216.34"):
        """保存狀態碼、標頭、body 與實際連線位址。"""
        self.status_code = 狀態碼
        self.headers = 標頭 or {}
        self._文字 = 文字
        self.text = ""
        self.已讀取 = False
        self.extensions = {"network_stream": 假網路串流(連線位址)} if 連線位址 else {}

    def read(self):
        """把 body 讀進來，模擬串流語意。"""
        self.已讀取 = True
        self.text = self._文字
        return self.text

    def __enter__(self):
        """進入 context。"""
        return self

    def __exit__(self, *參數):
        """離開 context。"""
        return False


class 假客戶端:
    """模擬 httpx.Client，依網址查表回傳串流回應。"""

    def __init__(self, 回應表):
        """保存網址對回應的對照表與造訪紀錄。"""
        self.回應表 = 回應表
        self.造訪順序 = []

    def __enter__(self):
        """進入 context。"""
        return self

    def __exit__(self, *參數):
        """離開 context。"""
        return False

    def stream(self, 方法, 網址, headers=None):
        """記錄造訪並回傳預設回應；查無對應時模擬連線失敗。"""
        self.造訪順序.append(網址)
        回應 = self.回應表.get(網址)
        if 回應 is None:
            raise httpx.ConnectError("no route")
        return 回應


def 釘住客戶端(monkeypatch, 回應表, 內部主機=()):
    """安裝假 httpx.Client 並讓網域預檢只擋指定主機。"""
    客戶端 = 假客戶端(回應表)
    monkeypatch.setattr(httpx, "Client", lambda **具名參數: 客戶端)
    def 假預檢(主機):
        """指定主機視為內部，其餘放行。"""
        if 主機 in 內部主機:
            raise ValueError(f"拒絕存取內部位址：{主機}")
    monkeypatch.setattr(模組, "確認非內部位址", 假預檢)
    return 客戶端


def test_ddgs擷取成功轉純文字(monkeypatch):
    """確認抓取成功會轉純文字並套用長度規則。"""
    釘住客戶端(monkeypatch, {"https://a.example": 假串流回應(
        200, 標頭={"content-type": "text/html; charset=utf-8"},
        文字="<html><body><p>內文在這</p><script>x</script></body></html>",
    )})

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://a.example"])

    assert 失敗清單 == []
    assert "內文在這" in 成功清單[0].內容
    assert "x" not in 成功清單[0].內容


def test_ddgs擷取拒絕非文字內容型別(monkeypatch):
    """確認 PDF 這類自建擷取處理不了的型別回報明確原因。"""
    回應 = 假串流回應(200, 標頭={"content-type": "application/pdf"}, 文字="%PDF-1.4")
    釘住客戶端(monkeypatch, {"https://a.example/x.pdf": 回應})

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://a.example/x.pdf"])

    assert 成功清單 == []
    assert "application/pdf" in 失敗清單[0]["error"]
    assert 回應.已讀取 is False, "不支援的型別不應把 body 讀進記憶體"


def test_ddgs擷取回報HTTP錯誤與內部位址(monkeypatch):
    """確認 4xx 與網域預檢攔截都進失敗清單、不中斷其他網址。"""
    釘住客戶端(
        monkeypatch,
        {"https://a.example/y": 假串流回應(404, 標頭={"content-type": "text/html"})},
        內部主機=("internal",),
    )

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://internal/x", "https://a.example/y"])

    assert 成功清單 == []
    失敗表 = {項目["url"]: 項目["error"] for 項目 in 失敗清單}
    assert "內部位址" in 失敗表["https://internal/x"]
    assert "404" in 失敗表["https://a.example/y"]


def test_DNS_rebinding_被實際連線檢查擋下(monkeypatch):
    """核心迴歸：網域預檢放行，但真正連到內網時仍須中止且不讀 body。"""
    回應 = 假串流回應(
        200, 標頭={"content-type": "text/html"},
        文字="<html><body>雲端金鑰</body></html>",
        連線位址="169.254.169.254",
    )
    釘住客戶端(monkeypatch, {"https://looks-safe.example": 回應})

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://looks-safe.example"])

    assert 成功清單 == []
    assert "169.254.169.254" in 失敗清單[0]["error"]
    assert 回應.已讀取 is False, "內部位址的內容不應被讀進記憶體"


def test_轉址每一跳都重新檢查(monkeypatch):
    """確認轉址由本模組處理，且中途轉進內網會被擋下。"""
    客戶端 = 釘住客戶端(monkeypatch, {
        "https://a.example/start": 假串流回應(
            302, 標頭={"location": "https://a.example/next"}),
        "https://a.example/next": 假串流回應(
            302, 標頭={"location": "https://evil.example/inner"}),
        "https://evil.example/inner": 假串流回應(
            200, 標頭={"content-type": "text/html"},
            文字="<html><body>內網資料</body></html>", 連線位址="10.0.0.5"),
    })

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://a.example/start"])

    assert 成功清單 == []
    assert "10.0.0.5" in 失敗清單[0]["error"]
    assert 客戶端.造訪順序 == [
        "https://a.example/start", "https://a.example/next", "https://evil.example/inner",
    ]


def test_轉址成功時回傳最終頁面(monkeypatch):
    """確認正常轉址仍能取得最終內容。"""
    釘住客戶端(monkeypatch, {
        "https://a.example/start": 假串流回應(301, 標頭={"location": "/final"}),
        "https://a.example/final": 假串流回應(
            200, 標頭={"content-type": "text/html"}, 文字="<p>最終內容</p>"),
    })

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://a.example/start"])

    assert 失敗清單 == []
    assert "最終內容" in 成功清單[0].內容


def test_轉址超過上限被中止(monkeypatch):
    """確認轉址迴圈不會無限跟下去。"""
    釘住客戶端(monkeypatch, {
        "https://a.example/loop": 假串流回應(302, 標頭={"location": "https://a.example/loop"}),
    })

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://a.example/loop"])

    assert 成功清單 == []
    assert "轉址超過" in 失敗清單[0]["error"]


def test_轉址目標協定也要驗證(monkeypatch):
    """確認轉址不能把我們導去 file:// 這類協定。"""
    釘住客戶端(monkeypatch, {
        "https://a.example/start": 假串流回應(302, 標頭={"location": "file:///etc/passwd"}),
    })

    成功清單, 失敗清單 = DDGS後端(20).擷取(["https://a.example/start"])

    assert 成功清單 == []
    assert "http" in 失敗清單[0]["error"]


def test_是否內部位址涵蓋各類保留範圍():
    """確認 IP 判斷本身涵蓋 loopback、私有、link-local 與 IPv6。"""
    for 內部 in ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.0.1", "169.254.169.254", "::1"]:
        assert 模組.是否內部位址(內部) is True, 內部
    for 外部 in ["93.184.216.34", "8.8.8.8", "2001:4860:4860::8888"]:
        assert 模組.是否內部位址(外部) is False, 外部
    assert 模組.是否內部位址("not-an-ip") is False


def test_分類連線錯誤區分憑證與網路():
    """確認憑證失敗被單獨標示，避免誤導成可重試的網路問題。"""
    憑證錯誤 = httpx.ConnectError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "Missing Subject Key Identifier"
    )
    訊息 = 模組.分類連線錯誤(憑證錯誤)
    assert "憑證驗證失敗" in 訊息
    assert "Missing Subject Key Identifier" in 訊息

    assert "逾時" in 模組.分類連線錯誤(httpx.ConnectTimeout("timed out"))
    assert 模組.分類連線錯誤(httpx.ConnectError("boom")) == "連線失敗：ConnectError"
