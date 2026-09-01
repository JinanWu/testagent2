"""管理部 BigQuery 文件搜尋工具。

功能：
    提供 `administrative_search` 的 handler。此工具以管理部文件為邊界，
    預設同時執行語意搜尋與關鍵字搜尋，合併排序後補上命中文件相關圖片。

環境變數：
    ADMIN_BQ_PROJECT: BigQuery 專案 ID。
    ADMIN_BQ_DATASET: BigQuery dataset。
    ADMIN_BQ_DOCUMENTS_TABLE: 文件表名，預設 documents。
    ADMIN_BQ_IMAGES_TABLE: 圖片表名，預設 images。
    ADMIN_BQ_LOCATION: BigQuery job location，可選。
    ADMIN_EMBEDDING_MODEL: Google GenAI embedding model，預設 gemini-embedding-001。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from ..環境設定 import 檢查資源名稱, 載入本機環境檔

預設語意權重 = 0.65
預設關鍵字權重 = 0.35
預設結果數量 = 5
預設候選倍數 = 4
預設文件表 = "documents"
預設圖片表 = "images"
預設Embedding模型 = "gemini-embedding-001"
預設Embedding維度 = 768


@dataclass(frozen=True)
class 管理部BigQuery設定:
    """保存管理部文件索引所需的 BigQuery 設定。"""

    專案: str
    資料集: str
    文件表: str
    圖片表: str
    地區: str | None
    embedding模型: str
    embedding維度: int

    @property
    def 文件表完整名稱(self) -> str:
        """回傳可放進 SQL 的文件表完整名稱。"""
        return f"{self.專案}.{self.資料集}.{self.文件表}"

    @property
    def 圖片表完整名稱(self) -> str:
        """回傳可放進 SQL 的圖片表完整名稱。"""
        return f"{self.專案}.{self.資料集}.{self.圖片表}"


def 讀取管理部BigQuery設定() -> 管理部BigQuery設定:
    """從環境變數讀取管理部 BigQuery 設定。"""
    載入本機環境檔()
    專案 = os.getenv("ADMIN_BQ_PROJECT", "").strip()
    資料集 = os.getenv("ADMIN_BQ_DATASET", "").strip()
    if not 專案 or not 資料集:
        缺少 = []
        if not 專案:
            缺少.append("ADMIN_BQ_PROJECT")
        if not 資料集:
            缺少.append("ADMIN_BQ_DATASET")
        raise ValueError(f"管理部 BigQuery tool 尚未設定：缺少 {', '.join(缺少)}")
    return 管理部BigQuery設定(
        專案=專案,
        資料集=資料集,
        文件表=os.getenv("ADMIN_BQ_DOCUMENTS_TABLE", 預設文件表).strip() or 預設文件表,
        圖片表=os.getenv("ADMIN_BQ_IMAGES_TABLE", 預設圖片表).strip() or 預設圖片表,
        地區=os.getenv("ADMIN_BQ_LOCATION", "").strip() or None,
        embedding模型=os.getenv("ADMIN_EMBEDDING_MODEL", 預設Embedding模型).strip() or 預設Embedding模型,
        embedding維度=int(os.getenv("ADMIN_EMBEDDING_DIM", "").strip() or 預設Embedding維度),
    )


def 建立BigQuery客戶端(設定: 管理部BigQuery設定):
    """建立 BigQuery client；套件缺失時回報清楚錯誤。"""
    try:
        from google.cloud import bigquery
    except ImportError as 錯誤:
        raise RuntimeError("缺少 google-cloud-bigquery，無法執行 administrative_search") from 錯誤
    return bigquery.Client(project=設定.專案, location=設定.地區)


def 建立查詢Embedding(查詢: str, 設定: 管理部BigQuery設定) -> list[float]:
    """使用 Google GenAI 產生查詢 embedding。"""
    try:
        from google import genai
        from google.genai import types
    except ImportError as 錯誤:
        raise RuntimeError("缺少 google-genai，無法產生查詢 embedding") from 錯誤
    客戶端 = genai.Client(vertexai=True, project=設定.專案, location=設定.地區 or "us-central1")
    回應 = 客戶端.models.embed_content(
        model=設定.embedding模型,
        contents=查詢,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=設定.embedding維度,
        ),
    )
    向量 = getattr(回應.embeddings[0], "values", None) if getattr(回應, "embeddings", None) else None
    if not 向量:
        raise RuntimeError("查詢 embedding 產生失敗：回應沒有 embedding values")
    return [float(值) for 值 in 向量]


def 正規化分數(列清單: list[dict[str, Any]], 欄位: str, 反向: bool = False) -> dict[str, float]:
    """把指定分數欄位正規化到 0~1。"""
    值清單 = [float(列.get(欄位) or 0.0) for 列 in 列清單]
    if not 值清單:
        return {}
    最小值 = min(值清單)
    最大值 = max(值清單)
    分數表: dict[str, float] = {}
    for 列 in 列清單:
        文件識別碼 = str(列.get("document_id") or "")
        原始值 = float(列.get(欄位) or 0.0)
        if math.isclose(最大值, 最小值):
            正規化值 = 1.0
        else:
            正規化值 = (原始值 - 最小值) / (最大值 - 最小值)
        if 反向:
            正規化值 = 1.0 - 正規化值
        分數表[文件識別碼] = max(0.0, min(1.0, 正規化值))
    return 分數表


def 合併候選文件(
    語意列清單: list[dict[str, Any]],
    關鍵字列清單: list[dict[str, Any]],
    語意權重: float,
    關鍵字權重: float,
    限制: int,
) -> list[dict[str, Any]]:
    """合併語意與關鍵字候選文件，依混合分數排序。"""
    def 是否空值(值: Any) -> bool:
        return 值 is None or 值 == ""

    語意分數表 = 正規化分數(語意列清單, "semantic_distance", 反向=True)
    關鍵字分數表 = 正規化分數(關鍵字列清單, "keyword_score")
    合併表: dict[str, dict[str, Any]] = {}
    for 來源名稱, 列清單 in [("semantic", 語意列清單), ("keyword", 關鍵字列清單)]:
        for 列 in 列清單:
            文件識別碼 = str(列.get("document_id") or "")
            if not 文件識別碼:
                continue
            既有 = 合併表.setdefault(文件識別碼, dict(列))
            既有.setdefault("matched_by", [])
            if 來源名稱 not in 既有["matched_by"]:
                既有["matched_by"].append(來源名稱)
            for 鍵, 值 in 列.items():
                if 是否空值(既有.get(鍵)) and not 是否空值(值):
                    既有[鍵] = 值
    for 文件識別碼, 列 in 合併表.items():
        語意分數 = 語意分數表.get(文件識別碼, 0.0)
        關鍵字分數 = 關鍵字分數表.get(文件識別碼, 0.0)
        同時命中加成 = 0.05 if {"semantic", "keyword"}.issubset(set(列.get("matched_by", []))) else 0.0
        列["semantic_score"] = round(語意分數, 6)
        列["keyword_score"] = round(關鍵字分數, 6)
        列["final_score"] = round(min(1.0, 語意分數 * 語意權重 + 關鍵字分數 * 關鍵字權重 + 同時命中加成), 6)
    return sorted(合併表.values(), key=lambda 列: 列.get("final_score", 0), reverse=True)[:限制]


def 建立查詢參數(bigquery, 名稱: str, 型別: str, 值: Any):
    """建立 BigQuery scalar query parameter。"""
    return bigquery.ScalarQueryParameter(名稱, 型別, 值)


def 建立陣列查詢參數(bigquery, 名稱: str, 型別: str, 值: list[Any]):
    """建立 BigQuery array query parameter。"""
    return bigquery.ArrayQueryParameter(名稱, 型別, 值)


def 查詢語意候選(客戶端, 設定: 管理部BigQuery設定, 查詢向量: list[float], 過濾: dict[str, Any], 候選數量: int) -> list[dict[str, Any]]:
    """用 documents.embedding 執行語意候選查詢。"""
    from google.cloud import bigquery

    條件 = ["embedding IS NOT NULL"]
    參數 = [
        建立陣列查詢參數(bigquery, "query_embedding", "FLOAT64", 查詢向量),
        建立查詢參數(bigquery, "limit", "INT64", 候選數量),
    ]
    for 欄位 in ["category", "version", "source_file"]:
        if 過濾.get(欄位):
            條件.append(f"{欄位} = @{欄位}")
            參數.append(建立查詢參數(bigquery, 欄位, "STRING", str(過濾[欄位])))
    sql = f"""
    SELECT
      doc_id AS document_id, title, source_file, version, category, headings, content,
      ML.DISTANCE(embedding, @query_embedding, 'COSINE') AS semantic_distance
    FROM `{設定.文件表完整名稱}`
    WHERE {' AND '.join(條件)}
    ORDER BY semantic_distance ASC
    LIMIT @limit
    """
    工作設定 = bigquery.QueryJobConfig(query_parameters=參數)
    return [dict(列) for 列 in 客戶端.query(sql, job_config=工作設定).result()]


def 建立包含關鍵字條件(欄位表達式: str) -> str:
    """建立不把使用者輸入當 LIKE 萬用字元的 BigQuery substring 條件。"""
    return f"STRPOS(LOWER({欄位表達式}), LOWER(@keyword)) > 0"


def 查詢關鍵字候選(客戶端, 設定: 管理部BigQuery設定, 查詢: str, 過濾: dict[str, Any], 候選數量: int) -> list[dict[str, Any]]:
    """查詢 title/content/category/headings 的關鍵字候選。"""
    from google.cloud import bigquery

    標題包含 = 建立包含關鍵字條件("COALESCE(title, '')")
    內容包含 = 建立包含關鍵字條件("COALESCE(content, '')")
    分類包含 = 建立包含關鍵字條件("COALESCE(category, '')")
    標題階層包含 = 建立包含關鍵字條件("ARRAY_TO_STRING(IFNULL(headings, []), ' ')")
    條件 = [f"""
    (
      {標題包含}
      OR {內容包含}
      OR {分類包含}
      OR {標題階層包含}
    )
    """]
    參數 = [
        建立查詢參數(bigquery, "keyword", "STRING", 查詢),
        建立查詢參數(bigquery, "limit", "INT64", 候選數量),
    ]
    for 欄位 in ["category", "version", "source_file"]:
        if 過濾.get(欄位):
            條件.append(f"{欄位} = @{欄位}")
            參數.append(建立查詢參數(bigquery, 欄位, "STRING", str(過濾[欄位])))
    sql = f"""
    SELECT
      doc_id AS document_id, title, source_file, version, category, headings, content,
      (
        IF({標題包含}, 4, 0)
        + IF({標題階層包含}, 3, 0)
        + IF({分類包含}, 2, 0)
        + IF({內容包含}, 1, 0)
      ) AS keyword_score
    FROM `{設定.文件表完整名稱}`
    WHERE {' AND '.join(條件)}
    ORDER BY keyword_score DESC
    LIMIT @limit
    """
    工作設定 = bigquery.QueryJobConfig(query_parameters=參數)
    return [dict(列) for 列 in 客戶端.query(sql, job_config=工作設定).result()]


def 補查圖片(客戶端, 設定: 管理部BigQuery設定, 文件清單: list[dict[str, Any]]) -> None:
    """依 document_id 對命中文件補上圖片資訊。"""
    if not 文件清單:
        return
    from google.cloud import bigquery

    文件識別碼清單 = [str(列.get("document_id")) for 列 in 文件清單 if 列.get("document_id")]
    if not 文件識別碼清單:
        return
    sql = f"""
    SELECT doc_id AS document_id, image_filename, image_path, alt_text
    FROM `{設定.圖片表完整名稱}`
    WHERE doc_id IN UNNEST(@document_ids)
    ORDER BY doc_id, image_filename
    """
    工作設定 = bigquery.QueryJobConfig(query_parameters=[
        建立陣列查詢參數(bigquery, "document_ids", "STRING", 文件識別碼清單)
    ])
    圖片表: dict[str, list[dict[str, Any]]] = {}
    for 原始列 in 客戶端.query(sql, job_config=工作設定).result():
        列 = dict(原始列)
        圖片表.setdefault(str(列["document_id"]), []).append({
            "image_filename": 列.get("image_filename"),
            "image_path": 列.get("image_path"),
            "alt_text": 列.get("alt_text"),
        })
    for 文件 in 文件清單:
        文件["images"] = 圖片表.get(str(文件.get("document_id")), [])


def 清理文件列(列: dict[str, Any]) -> dict[str, Any]:
    """整理回傳給 LLM 的文件欄位，避免內容過長。"""
    內容 = str(列.get("content") or "")
    摘要長度 = 1800
    return {
        "document_id": 列.get("document_id"),
        "title": 列.get("title"),
        "source_file": 列.get("source_file"),
        "version": 列.get("version"),
        "category": 列.get("category"),
        "headings": 列.get("headings") or [],
        "content_excerpt": 內容[:摘要長度],
        "matched_by": 列.get("matched_by") or [],
        "semantic_score": 列.get("semantic_score", 0.0),
        "keyword_score": 列.get("keyword_score", 0.0),
        "final_score": 列.get("final_score", 0.0),
        "images": 列.get("images") or [],
    }


def 解析混合權重(參數: dict[str, Any]) -> tuple[float, float]:
    """解析 hybrid_weights，並保證兩者合計為 1。"""
    權重 = 參數.get("hybrid_weights") or {}
    語意權重 = float(權重.get("semantic", 預設語意權重))
    關鍵字權重 = float(權重.get("keyword", 預設關鍵字權重))
    合計 = 語意權重 + 關鍵字權重
    if 合計 <= 0:
        return 預設語意權重, 預設關鍵字權重
    return 語意權重 / 合計, 關鍵字權重 / 合計


def 管理部文件搜尋(參數: dict[str, Any]) -> dict[str, Any]:
    """查詢管理部 BigQuery 文件索引，支援 hybrid / semantic / keyword。"""
    查詢 = str(參數.get("query") or "").strip()
    if not 查詢:
        raise ValueError("query 不可為空")
    搜尋模式 = str(參數.get("search_mode") or "hybrid").strip().lower()
    if 搜尋模式 not in {"hybrid", "semantic", "keyword"}:
        raise ValueError("search_mode 必須是 hybrid、semantic 或 keyword")
    限制 = max(1, min(int(參數.get("limit", 預設結果數量) or 預設結果數量), 20))
    候選數量 = max(限制, 限制 * 預設候選倍數)
    是否包含圖片 = bool(參數.get("include_images", True))
    過濾 = dict(參數.get("filters") or {})
    語意權重, 關鍵字權重 = 解析混合權重(參數)

    設定 = 讀取管理部BigQuery設定()
    for 名稱, 值 in [("project", 設定.專案), ("dataset", 設定.資料集), ("documents_table", 設定.文件表), ("images_table", 設定.圖片表)]:
        檢查資源名稱(值, 名稱)
    客戶端 = 建立BigQuery客戶端(設定)

    語意列清單: list[dict[str, Any]] = []
    關鍵字列清單: list[dict[str, Any]] = []
    if 搜尋模式 in {"hybrid", "semantic"}:
        查詢向量 = 建立查詢Embedding(查詢, 設定)
        語意列清單 = 查詢語意候選(客戶端, 設定, 查詢向量, 過濾, 候選數量)
    if 搜尋模式 in {"hybrid", "keyword"}:
        關鍵字列清單 = 查詢關鍵字候選(客戶端, 設定, 查詢, 過濾, 候選數量)

    if 搜尋模式 == "semantic":
        文件清單 = 合併候選文件(語意列清單, [], 1.0, 0.0, 限制)
    elif 搜尋模式 == "keyword":
        文件清單 = 合併候選文件([], 關鍵字列清單, 0.0, 1.0, 限制)
    else:
        文件清單 = 合併候選文件(語意列清單, 關鍵字列清單, 語意權重, 關鍵字權重, 限制)

    if 是否包含圖片:
        補查圖片(客戶端, 設定, 文件清單)

    return {
        "query": 查詢,
        "search_mode": 搜尋模式,
        "hybrid_weights": {"semantic": round(語意權重, 4), "keyword": round(關鍵字權重, 4)},
        "filters": 過濾,
        "results": [清理文件列(列) for 列 in 文件清單],
        "total_count": len(文件清單),
        "citation_fields": ["title", "source_file", "version", "category"],
    }
