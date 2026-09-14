"""使用者上傳圖片的 Cloud Storage 存放層。

功能：
    集中「使用者上傳的圖片放哪裡、怎麼引用」的決定。上傳端點呼叫 `保存圖片`
    取得一個 `gs://` 參照，該參照隨聊天請求送回後端，再由模型供應商直接交給
    Vertex AI —— 圖片位元組不會經過對話歷史，也不會進資料庫。

    參照格式固定為：

        gs://<bucket>/images/<user_id>/<uuid>.<副檔名>

    路徑帶 user_id 是刻意的：`驗證圖片參照` 用它確認引用者就是上傳者，
    避免使用者猜別人的參照來讀取他人圖片。

環境變數：
    IMAGE_BUCKET: 存放上傳圖片的 GCS bucket 名稱；未設定時上傳功能停用。
"""

from __future__ import annotations

import os
import re
import uuid

from .環境設定 import 檢查資源名稱, 載入本機環境檔

# Vertex AI Gemini 接受的圖片格式。刻意不含 GIF —— Vertex 不保證支援動畫 GIF。
支援的圖片類型: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

# 單張圖片大小上限。Vertex 對單一請求有總量限制，20 MB 已遠超一般截圖／照片。
圖片大小上限 = 20 * 1024 * 1024

圖片路徑前綴 = "images"

# 參照白名單：只允許我們自己發出的形狀，任何多餘路徑片段都會被擋下。
_參照格式 = re.compile(
    r"^gs://(?P<bucket>[a-z0-9][a-z0-9._-]{1,61}[a-z0-9])/"
    r"images/(?P<user_id>[A-Za-z0-9_-]{1,128})/"
    r"(?P<檔名>[0-9a-f]{32}\.(?:png|jpg|webp))$"
)


class 圖片存放未設定(RuntimeError):
    """IMAGE_BUCKET 未設定時，讓呼叫端能明確回報功能停用。"""


def 取得圖片Bucket() -> str:
    """讀取上傳圖片的 bucket 名稱。

    參數：無。
    返回值：str。bucket 名稱。
    例外：未設定 IMAGE_BUCKET 時丟出 圖片存放未設定。
    """
    載入本機環境檔()
    名稱 = (os.getenv("IMAGE_BUCKET") or "").strip()
    if not 名稱:
        raise 圖片存放未設定("尚未設定 IMAGE_BUCKET，圖片上傳功能停用")
    檢查資源名稱(名稱.replace(".", "-"), "IMAGE_BUCKET")
    return 名稱


def 圖片存放是否可用() -> bool:
    """回報目前環境是否已設定圖片存放。

    參數：無。
    返回值：bool。
    """
    try:
        取得圖片Bucket()
        return True
    except 圖片存放未設定:
        return False


def 偵測圖片類型(位元組: bytes) -> str | None:
    """以檔頭 magic bytes 判斷圖片類型。

    參數：
        位元組: 上傳檔案的完整內容。
    返回值：
        str | None。支援的 MIME 類型；不是支援的圖片時回傳 None。

    刻意不看副檔名或 client 宣告的 Content-Type —— 兩者都由上傳端控制，
    無法用來擋「把任意檔案改名成 .png」。
    """
    if not isinstance(位元組, bytes) or len(位元組) < 12:
        return None
    if 位元組.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if 位元組.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if 位元組[:4] == b"RIFF" and 位元組[8:12] == b"WEBP":
        return "image/webp"
    return None


def 建立圖片參照(bucket: str, 使用者識別碼: str, 副檔名: str) -> str:
    """組出 canonical `gs://` 參照。

    參數：
        bucket: GCS bucket 名稱。
        使用者識別碼: 上傳者。
        副檔名: png / jpg / webp。
    返回值：str。
    """
    return f"gs://{bucket}/{圖片路徑前綴}/{使用者識別碼}/{uuid.uuid4().hex}.{副檔名}"


def 驗證圖片參照(參照: object, 使用者識別碼: str) -> bool:
    """確認參照是我們發出的、且屬於這位使用者。

    參數：
        參照: 待驗證值；型別不符一律視為無效。
        使用者識別碼: 目前登入者。
    返回值：
        bool。True 表示可安全交給 Vertex 讀取。

    這是唯一擋住「使用者自己編一個 gs:// 路徑」的地方：bucket 必須是我們設定的
    那個，路徑必須落在 images/<自己的 user_id>/ 底下，檔名必須是我們產生的 uuid。
    """
    if type(參照) is not str:
        return False
    比對 = _參照格式.match(參照)
    if 比對 is None:
        return False
    if 比對.group("user_id") != 使用者識別碼:
        return False
    try:
        return 比對.group("bucket") == 取得圖片Bucket()
    except 圖片存放未設定:
        return False


def 取得圖片MIME(參照: str) -> str:
    """由參照副檔名回推 MIME 類型，供 Vertex Part 使用。

    參數：
        參照: 已通過 `驗證圖片參照` 的參照。
    返回值：str。
    """
    副檔名 = 參照.rsplit(".", 1)[-1]
    for mime, 對應副檔名 in 支援的圖片類型.items():
        if 對應副檔名 == 副檔名:
            return mime
    return "image/png"


def 保存圖片(位元組: bytes, 使用者識別碼: str) -> str:
    """把上傳圖片寫進 GCS 並回傳參照。

    參數：
        位元組: 上傳檔案的完整內容。
        使用者識別碼: 上傳者；會成為物件路徑的一段。
    返回值：
        str。`gs://` 參照。
    例外：
        ValueError —— 不是支援的圖片或超過大小上限。
        圖片存放未設定 —— 尚未設定 IMAGE_BUCKET。
    副作用：寫入一個 GCS 物件。
    """
    if not isinstance(位元組, bytes) or not 位元組:
        raise ValueError("圖片內容為空")
    if len(位元組) > 圖片大小上限:
        raise ValueError("圖片超過大小上限")
    mime類型 = 偵測圖片類型(位元組)
    if mime類型 is None:
        raise ValueError("不支援的圖片格式")

    bucket名稱 = 取得圖片Bucket()
    參照 = 建立圖片參照(bucket名稱, 使用者識別碼, 支援的圖片類型[mime類型])

    from google.cloud import storage

    客戶端 = storage.Client()
    儲存桶 = 客戶端.bucket(bucket名稱)
    物件 = 儲存桶.blob(參照.split(f"gs://{bucket名稱}/", 1)[1])
    物件.upload_from_string(位元組, content_type=mime類型)
    return 參照
