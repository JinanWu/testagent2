"""使用者圖片上傳的 exact-prefix FastAPI 路由工廠。

本端點刻意收「原始位元組 body」而非 multipart form：圖片型別一律由檔頭
magic bytes 判定，multipart 只會多帶一個 `python-multipart` 相依與一組
可由 client 任意宣告、無法信任的 filename／Content-Type 欄位。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..網頁工作階段 import 網頁使用者
from .回應模型 import 圖片上傳成功回應
from 繁中代理.圖片存放 import (
    保存圖片, 圖片大小上限, 圖片存放未設定, 驗證圖片參照, 取得圖片Bucket, 取得圖片MIME,
)


def 建立圖片上傳路由器(目前工作階段相依, csrf相依) -> APIRouter:
    """注入 caller 的 canonical session/CSRF dependencies 並建立 exact POST route。

    參數：
        目前工作階段相依: 解析登入身分的 dependency。
        csrf相依: CSRF 驗證 dependency。
    返回值：APIRouter。
    """
    from fastapi import Depends

    路由器 = APIRouter(prefix="/api/uploads")

    @路由器.post("/image", response_model=圖片上傳成功回應, responses={400: {}, 413: {}, 422: {}, 503: {}})
    async def 上傳圖片(
        請求: Request,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
    ) -> dict[str, object]:
        """把登入者上傳的圖片存進 GCS，回傳可放進聊天請求的 gs:// 參照。

        圖片位元組只在這裡經過本服務一次；之後的對話一律只帶參照，Vertex 自己
        去 Cloud Storage 讀取。
        """
        # 先看 Content-Length 擋掉大檔，避免把整包讀進記憶體才發現超過上限。
        宣告長度 = 請求.headers.get("content-length")
        if 宣告長度 is not None:
            try:
                if int(宣告長度) > 圖片大小上限:
                    raise HTTPException(status_code=413, detail={"code": "image_too_large"})
            except ValueError:
                raise HTTPException(status_code=422, detail={"code": "invalid_request"}) from None

        位元組 = await 請求.body()
        if len(位元組) > 圖片大小上限:
            raise HTTPException(status_code=413, detail={"code": "image_too_large"})

        try:
            參照 = 保存圖片(位元組, 使用者.識別碼)
        except 圖片存放未設定:
            raise HTTPException(status_code=503, detail={"code": "upload_unavailable"}) from None
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "invalid_image"}) from None
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            raise HTTPException(status_code=503, detail={"code": "upload_unavailable"}) from None

        return {"image": 參照}

    @路由器.get("/image", responses={404: {}, 503: {}})
    def 讀取圖片(
        image: str,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ) -> Response:
        """安全代理目前登入者自己的私有 GCS 圖片，供聊天紀錄預覽。"""
        if not 驗證圖片參照(image, 使用者.識別碼):
            raise HTTPException(status_code=404, detail={"code": "image_not_found"})
        try:
            from google.api_core.exceptions import NotFound
            from google.cloud import storage

            bucket = 取得圖片Bucket()
            物件名稱 = image.split(f"gs://{bucket}/", 1)[1]
            位元組 = storage.Client().bucket(bucket).blob(物件名稱).download_as_bytes()
        except NotFound:
            raise HTTPException(status_code=404, detail={"code": "image_not_found"}) from None
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            raise HTTPException(status_code=503, detail={"code": "image_unavailable"}) from None
        if len(位元組) > 圖片大小上限:
            raise HTTPException(status_code=404, detail={"code": "image_not_found"})
        return Response(
            content=位元組,
            media_type=取得圖片MIME(image),
            headers={"Cache-Control": "private, max-age=3600"},
        )

    return 路由器
