"""使用者上傳圖片路徑的邊界測試。

涵蓋三個層次：
    1. 圖片存放：型別偵測只信檔頭、參照歸屬只認上傳者本人。
    2. 模型供應商：user 訊息帶參照時真的轉成 Vertex 的圖片 Part。
    3. 聊天請求：images 欄位的形狀界線。

不呼叫 GCS，也不呼叫 Vertex；保存與上傳的真實 I/O 由 e2e 涵蓋。
"""

from __future__ import annotations

import pytest

from 繁中代理.模型供應商 import GeminiADC供應商, 圖片參照欄位
from 繁中代理.圖片存放 import (
    偵測圖片類型,
    取得圖片MIME,
    建立圖片參照,
    保存圖片,
    圖片大小上限,
    圖片存放未設定,
    驗證圖片參照,
)

_PNG檔頭 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG檔頭 = b"\xff\xd8\xff\xe0" + b"\x00" * 32
_WEBP檔頭 = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


@pytest.fixture()
def 已設定Bucket(monkeypatch):
    """把 IMAGE_BUCKET 釘在測試值，避免讀到開發者 .env。"""
    monkeypatch.setenv("IMAGE_BUCKET", "test-images")
    monkeypatch.setattr("繁中代理.圖片存放.載入本機環境檔", lambda: None)
    return "test-images"


def test_偵測圖片類型只認檔頭不認副檔名():
    """確認型別判斷來自 magic bytes——client 宣告的檔名與 MIME 都不可信。"""
    assert 偵測圖片類型(_PNG檔頭) == "image/png"
    assert 偵測圖片類型(_JPEG檔頭) == "image/jpeg"
    assert 偵測圖片類型(_WEBP檔頭) == "image/webp"
    # 這段是可執行檔開頭，就算被改名成 .png 也必須擋下。
    assert 偵測圖片類型(b"\x7fELF" + b"\x00" * 32) is None
    assert 偵測圖片類型(b"GIF89a" + b"\x00" * 32) is None
    assert 偵測圖片類型(b"") is None
    assert 偵測圖片類型("不是位元組") is None


def test_保存圖片拒絕非圖片與超大檔(已設定Bucket):
    """確認大小與格式兩道界線都在真正上傳 GCS 之前擋下。"""
    with pytest.raises(ValueError):
        保存圖片(b"\x7fELF" + b"\x00" * 32, "user-1")
    with pytest.raises(ValueError):
        保存圖片(b"", "user-1")
    with pytest.raises(ValueError):
        保存圖片(_PNG檔頭 + b"\x00" * 圖片大小上限, "user-1")


def test_未設定Bucket時保存圖片明確失敗(monkeypatch):
    """確認缺少 IMAGE_BUCKET 是明確錯誤，而非默默寫到別的地方。"""
    monkeypatch.delenv("IMAGE_BUCKET", raising=False)
    monkeypatch.setattr("繁中代理.圖片存放.載入本機環境檔", lambda: None)
    with pytest.raises(圖片存放未設定):
        保存圖片(_PNG檔頭, "user-1")


def test_驗證圖片參照只接受本人上傳的參照(已設定Bucket):
    """確認使用者無法引用他人的圖片——這是圖片歸屬的唯一把關點。"""
    我的參照 = 建立圖片參照(已設定Bucket, "user-1", "png")
    assert 驗證圖片參照(我的參照, "user-1") is True
    # 同一個參照換一位使用者來引用就必須失敗。
    assert 驗證圖片參照(我的參照, "user-2") is False


def test_驗證圖片參照拒絕自行編造的路徑(已設定Bucket):
    """確認任何非 canonical 形狀都被擋下，包含跨 bucket 與路徑穿越。"""
    無效值 = [
        None,
        123,
        "",
        "https://example.com/x.png",
        # 換一個 bucket——不能讓使用者指定 Vertex 去讀任意 bucket。
        "gs://別的bucket/images/user-1/" + "a" * 32 + ".png",
        "gs://other-bucket/images/user-1/" + "a" * 32 + ".png",
        # 路徑穿越回到別人的目錄。
        f"gs://{已設定Bucket}/images/user-1/../user-2/" + "a" * 32 + ".png",
        # 檔名不是我們產生的 uuid 形狀。
        f"gs://{已設定Bucket}/images/user-1/任意檔名.png",
        # 副檔名不在支援清單。
        f"gs://{已設定Bucket}/images/user-1/" + "a" * 32 + ".svg",
        # 前綴不是 images/。
        f"gs://{已設定Bucket}/secrets/user-1/" + "a" * 32 + ".png",
    ]
    for 值 in 無效值:
        assert 驗證圖片參照(值, "user-1") is False, 值


def test_取得圖片MIME依副檔名回推(已設定Bucket):
    """確認交給 Vertex 的 mime_type 與實際存檔格式一致。"""
    assert 取得圖片MIME(建立圖片參照(已設定Bucket, "u", "png")) == "image/png"
    assert 取得圖片MIME(建立圖片參照(已設定Bucket, "u", "jpg")) == "image/jpeg"
    assert 取得圖片MIME(建立圖片參照(已設定Bucket, "u", "webp")) == "image/webp"


def test_轉成Gemini內容把圖片參照掛成Part(已設定Bucket):
    """確認帶圖的 user 訊息會多出對應的 file_data Part，且文字仍在第一個 Part。"""
    供應商 = GeminiADC供應商("gemini-2.5-flash-lite", "測試專案")
    參照 = 建立圖片參照(已設定Bucket, "user-1", "png")
    內容清單 = 供應商.轉成Gemini內容([
        {"role": "user", "content": "這張圖是什麼？", 圖片參照欄位: [參照]},
    ])

    assert len(內容清單) == 1
    零件清單 = 內容清單[0].parts
    assert len(零件清單) == 2
    assert 零件清單[0].text == "這張圖是什麼？"
    assert 零件清單[1].file_data.file_uri == 參照
    assert 零件清單[1].file_data.mime_type == "image/png"


def test_轉成Gemini內容沒有圖片時維持原行為(已設定Bucket):
    """確認未帶圖的訊息轉換結果與加這個功能之前完全相同。"""
    供應商 = GeminiADC供應商("gemini-2.5-flash-lite", "測試專案")
    內容清單 = 供應商.轉成Gemini內容([{"role": "user", "content": "純文字"}])
    assert len(內容清單) == 1
    assert len(內容清單[0].parts) == 1
    assert 內容清單[0].parts[0].text == "純文字"


def test_轉成Gemini內容忽略非gs參照(已設定Bucket):
    """確認即使有值漏過上層驗證，供應商也不會把它當成檔案交給 Vertex。"""
    供應商 = GeminiADC供應商("gemini-2.5-flash-lite", "測試專案")
    內容清單 = 供應商.轉成Gemini內容([
        {"role": "user", "content": "文字", 圖片參照欄位: ["https://example.com/x.png", None, 123]},
    ])
    assert len(內容清單[0].parts) == 1


def test_聊天請求images欄位界線():
    """確認 route 層擋掉空清單、超量與帶邊界空白的參照。"""
    from 繁中代理.發布介面.路由.聊天 import 每則訊息圖片上限, 聊天請求

    合法 = 聊天請求.model_validate({"message": "嗨", "images": ["gs://b/images/u/x.png"]})
    assert 合法.圖片參照清單 == ["gs://b/images/u/x.png"]
    assert 聊天請求.model_validate({"message": "嗨"}).圖片參照清單 is None

    for 無效本文 in (
        {"message": "嗨", "images": []},
        {"message": "嗨", "images": ["gs://b/images/u/x.png"] * (每則訊息圖片上限 + 1)},
        {"message": "嗨", "images": [" gs://b/images/u/x.png"]},
        {"message": "嗨", "images": [""]},
        {"message": "嗨", "images": "gs://b/images/u/x.png"},
        {"message": "嗨", "images": [123]},
    ):
        with pytest.raises(Exception):
            聊天請求.model_validate(無效本文)


def test_Web代理服務拒絕引用他人圖片(已設定Bucket):
    """確認歸屬檢查真的接在服務入口——route 只驗形狀，攔截必須發生在這裡。"""
    from types import SimpleNamespace

    from 繁中代理.使用者 import 使用者上下文
    from 繁中代理.發布介面.Web代理服務 import Web代理服務, Web請求無效

    class _假工作階段庫:
        def 檢查工作階段存取(self, 工作階段識別碼, user_id=None, source=None):
            return {"id": 工作階段識別碼, "source": "web", "user_id": user_id}

        def 取得工作階段譜系(self, 工作階段識別碼):
            return [工作階段識別碼]

    class _假使用者庫:
        def 建立使用者上下文(self, user_id=None):
            return 使用者上下文(user_id=user_id, username="alice", roles=["user"], skill_roots=[])

    def _工廠(*, 使用者上下文物件, source):
        return SimpleNamespace(
            執行使用者訊息=lambda 訊息, 工作階段識別碼=None, 圖片參照清單=None: SimpleNamespace(
                最終回答="不該執行到這裡", 工作階段識別碼="tip-1",
            )
        )

    服務 = Web代理服務(_假工作階段庫(), _假使用者庫(), _工廠)
    他人參照 = 建立圖片參照(已設定Bucket, "user-2", "png")

    with pytest.raises(Web請求無效):
        服務.聊天("user-1", "這張圖是什麼", None, [他人參照])
