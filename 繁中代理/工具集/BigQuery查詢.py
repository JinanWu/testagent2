"""通用 BigQuery 唯讀查詢工具。

功能：
    提供 `bigquery_query` 的 handler。此工具不綁定特定資料領域：要查哪些
    dataset / 資料表由 skill 提供知識，工具只負責「安全地把 SQL 送進
    BigQuery 並取回結果」。

    四個 action：
        list_datasets   列出 allowlist 內可查的 dataset。
        list_tables     列出指定 dataset 的資料表。
        describe_table  列出指定資料表的欄位名稱與型別。
        query           執行唯讀 SQL。

防護：
    本工具只讀不寫，沒有任何寫入 API 的呼叫路徑。三道關卡：
        1. action 白名單，只有上面四個動作。
        2. 每句 SQL 先 dry run，由 BigQuery 自己判定語句型別，非 SELECT
           一律拒絕（DML、DDL、EXPORT DATA、CALL、多句 SCRIPT 都擋掉）。
        3. dry run 回報的引用資料表必須全部落在 allowlist 內。
    另建議把執行用的憑證權限降到唯讀（dataViewer + jobUser），讓 IAM 成為
    程式碼之外的第二道保險。

環境變數：
    BQTOOL_PROJECT           專案 ID；未設定時使用部署預設專案。
    BQTOOL_ALLOWED_DATASETS  可查的 dataset，逗號分隔；預設本專案全部。
    BQTOOL_LOCATION          job location。
    BQTOOL_MAX_BYTES         單次掃描上限，預設 2 GiB。
    BQTOOL_MAX_ROWS          單次回傳列數上限，預設 200。
    BQTOOL_TIMEOUT           逾時秒數，預設 60。
"""

from __future__ import annotations

import datetime
import decimal
import os
import re
from dataclasses import dataclass
from typing import Any

from ..環境設定 import 檢查資源名稱, 載入本機環境檔

預設可掃描位元組 = 2 * 1024 * 1024 * 1024
預設回傳列數 = 200
預設逾時秒數 = 60
可用動作集合 = {"list_datasets", "list_tables", "describe_table", "query"}


class BigQuery查詢錯誤(RuntimeError):
    """工具層可預期的錯誤；訊息會原樣回給模型，用於自我修正。"""


@dataclass(frozen=True)
class BigQuery查詢設定:
    """保存通用 BigQuery 查詢工具的設定。"""

    專案: str
    允許資料集: tuple[str, ...]
    地區: str | None
    可掃描位元組: int
    回傳列數上限: int
    逾時秒數: int

    def 是否允許資料集(self, 專案: str, 資料集: str) -> bool:
        """判斷 `專案.資料集` 是否落在 allowlist 內。

        `專案.*` 代表該專案底下所有 dataset 都可查；跨專案仍一律擋下。
        """
        return f"{專案}.{資料集}" in self.允許資料集 or f"{專案}.*" in self.允許資料集


def 正規化資料集項目(項目: str, 預設專案: str) -> str:
    """把 allowlist 的單一項目正規化成 `專案.資料集`。

    參數：
        項目: allowlist 原始字串，`dataset` 或 `project.dataset`。
        預設專案: 項目未帶專案時要補上的專案 ID。
    返回值：str，正規化後的 `專案.資料集`。
    """
    片段 = [片段.strip() for 片段 in 項目.split(".") if 片段.strip()]
    if len(片段) == 1:
        專案, 資料集 = 預設專案, 片段[0]
    elif len(片段) == 2:
        專案, 資料集 = 片段
    else:
        raise ValueError(f"BQTOOL_ALLOWED_DATASETS 項目格式錯誤：{項目}")
    檢查資源名稱(專案, "project")
    if 資料集 != "*":  # `*` 代表整個專案，不是資料集名稱，不套用命名檢查
        檢查資源名稱(資料集, "dataset")
    return f"{專案}.{資料集}"


def 讀取BigQuery查詢設定() -> BigQuery查詢設定:
    """從環境變數讀取通用 BigQuery 查詢設定。

    參數：無。
    返回值：BigQuery查詢設定；未提供專案時使用部署預設專案。
    """
    載入本機環境檔()
    專案 = os.getenv("BQTOOL_PROJECT", "").strip() or 預設BigQuery專案
    # 未設 allowlist 時預設為本專案全部 dataset；跨專案仍擋下，要更嚴再逐一列出。
    允許原文 = os.getenv("BQTOOL_ALLOWED_DATASETS", "").strip() or "*"
    檢查資源名稱(專案, "project")
    允許資料集 = tuple(
        dict.fromkeys(正規化資料集項目(項目, 專案) for 項目 in 允許原文.split(",") if 項目.strip())
    )
    if not 允許資料集:
        raise ValueError("BQTOOL_ALLOWED_DATASETS 未包含任何有效 dataset")
    return BigQuery查詢設定(
        專案=專案,
        允許資料集=允許資料集,
        地區=os.getenv("BQTOOL_LOCATION", "").strip() or None,
        可掃描位元組=int(os.getenv("BQTOOL_MAX_BYTES", "").strip() or 預設可掃描位元組),
        回傳列數上限=int(os.getenv("BQTOOL_MAX_ROWS", "").strip() or 預設回傳列數),
        逾時秒數=int(os.getenv("BQTOOL_TIMEOUT", "").strip() or 預設逾時秒數),
    )


def 建立BigQuery客戶端(設定: BigQuery查詢設定):
    """建立 BigQuery client；套件缺失時回報清楚錯誤。"""
    try:
        from google.cloud import bigquery
    except ImportError as 錯誤:
        raise RuntimeError("缺少 google-cloud-bigquery，無法執行 bigquery_query") from 錯誤
    return bigquery.Client(project=設定.專案, location=設定.地區)


def 摘要錯誤訊息(錯誤: Exception) -> str:
    """把 BigQuery 例外壓成一行可讀訊息。

    模型要的是「哪裡寫錯」而不是整串 traceback；保留 BigQuery 的原始描述
    （含 `at [行:欄]` 位置）即可讓它自行修正 SQL。

    參數：
        錯誤: google.api_core 或其他來源的例外。
    返回值：str，單行錯誤摘要。
    """
    訊息 = str(getattr(錯誤, "message", "") or 錯誤).strip()
    首行 = 訊息.split("\n", 1)[0]
    # 去掉 "400 POST https://.../jobs?prettyPrint=false: " 這段前綴，只留錯誤本體
    首行 = re.sub(r"^\d{3}\s+[A-Z]+\s+https?://\S+:\s*", "", 首行)
    return 首行[:500]


def 轉成可序列化(值: Any) -> Any:
    """把 BigQuery 回傳值轉成可 JSON 序列化的型別。

    參數：
        值: BigQuery Row 的單一欄位值。
    返回值：可被 json.dumps 處理的值。
    """
    if isinstance(值, (datetime.datetime, datetime.date, datetime.time)):
        return 值.isoformat()
    if isinstance(值, decimal.Decimal):
        return float(值)
    if isinstance(值, bytes):
        return 值.decode("utf-8", errors="replace")
    if isinstance(值, dict):
        return {鍵: 轉成可序列化(子值) for 鍵, 子值 in 值.items()}
    if isinstance(值, (list, tuple)):
        return [轉成可序列化(子值) for 子值 in 值]
    return 值


def 解析資料集參數(參數: dict[str, Any], 設定: BigQuery查詢設定) -> tuple[str, str]:
    """從工具參數取出並驗證 `專案 / 資料集`。

    參數：
        參數: 工具收到的原始參數，讀取 dataset 與可選的 project。
        設定: 目前工具設定，用於補預設專案與檢查 allowlist。
    返回值：tuple[str, str]，(專案, 資料集)。
    """
    資料集 = str(參數.get("dataset") or "").strip()
    if not 資料集:
        raise BigQuery查詢錯誤("dataset 不可為空")
    專案 = str(參數.get("project") or 設定.專案).strip()
    檢查資源名稱(專案, "project")
    檢查資源名稱(資料集, "dataset")
    if not 設定.是否允許資料集(專案, 資料集):
        raise BigQuery查詢錯誤(
            f"dataset 不在允許範圍：{專案}.{資料集}。可查範圍：{', '.join(設定.允許資料集)}"
        )
    return 專案, 資料集


def 是否含萬用字元(設定: BigQuery查詢設定) -> bool:
    """判斷 allowlist 是否有 `專案.*` 項目（需要實際向 BigQuery 列出）。"""
    return any(項目.endswith(".*") for 項目 in 設定.允許資料集)


def 列出可查資料集(設定: BigQuery查詢設定, 客戶端=None) -> dict[str, Any]:
    """列出可查的 dataset。

    參數：
        設定: 目前工具設定。
        客戶端: 僅在 allowlist 含 `專案.*` 時需要，用於實際列出該專案的 dataset。
    返回值：dict，含展開後的 datasets 清單。
    """
    資料集清單: list[str] = []
    for 項目 in 設定.允許資料集:
        專案, _, 資料集 = 項目.rpartition(".")
        if 資料集 != "*":
            資料集清單.append(項目)
            continue
        if 客戶端 is None:  # 無法展開時退回原樣，至少讓模型知道範圍
            資料集清單.append(項目)
            continue
        資料集清單.extend(f"{專案}.{項.dataset_id}" for 項 in 客戶端.list_datasets(專案))
    去重清單 = list(dict.fromkeys(資料集清單))
    return {
        "action": "list_datasets",
        "datasets": 去重清單,
        "total_count": len(去重清單),
    }


def 列出資料表(客戶端, 參數: dict[str, Any], 設定: BigQuery查詢設定) -> dict[str, Any]:
    """列出指定 dataset 底下的資料表名稱。"""
    專案, 資料集 = 解析資料集參數(參數, 設定)
    前綴 = str(參數.get("name_prefix") or "").strip()
    表清單 = [
        表.table_id
        for 表 in 客戶端.list_tables(f"{專案}.{資料集}")
        if not 前綴 or 表.table_id.startswith(前綴)
    ]
    return {
        "action": "list_tables",
        "project": 專案,
        "dataset": 資料集,
        "name_prefix": 前綴 or None,
        "tables": sorted(表清單),
        "total_count": len(表清單),
    }


def 描述資料表(客戶端, 參數: dict[str, Any], 設定: BigQuery查詢設定) -> dict[str, Any]:
    """列出指定資料表的欄位名稱、型別與列數。"""
    專案, 資料集 = 解析資料集參數(參數, 設定)
    資料表 = str(參數.get("table") or "").strip()
    if not 資料表:
        raise BigQuery查詢錯誤("describe_table 需要 table")
    檢查資源名稱(資料表, "table")
    表物件 = 客戶端.get_table(f"{專案}.{資料集}.{資料表}")
    return {
        "action": "describe_table",
        "project": 專案,
        "dataset": 資料集,
        "table": 資料表,
        "num_rows": 表物件.num_rows,
        "columns": [
            {"name": 欄位.name, "type": 欄位.field_type, "mode": 欄位.mode}
            for 欄位 in 表物件.schema
        ],
    }


def 驗證查詢(客戶端, sql: str, 設定: BigQuery查詢設定) -> dict[str, Any]:
    """以 dry run 驗證 SQL：語句型別、引用資料表、掃描量。

    參數：
        客戶端: BigQuery client。
        sql: 待驗證的 SQL。
        設定: 目前工具設定。
    返回值：dict，含 bytes_processed 與 referenced_tables。
    """
    from google.cloud import bigquery

    試跑設定 = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    try:
        試跑工作 = 客戶端.query(sql, job_config=試跑設定)
    except Exception as 錯誤:  # 只把 BigQuery 的訊息帶回去，避免整串 traceback 灌進 context
        raise BigQuery查詢錯誤(f"SQL 無法通過 BigQuery 驗證：{摘要錯誤訊息(錯誤)}") from 錯誤

    # 交給 BigQuery 判定語句型別，而不是自己用關鍵字比對：關鍵字比對會被註解、
    # 大小寫或字串繞過，dry run 的判定繞不掉。多句偷渡會被判為 SCRIPT，同樣擋下。
    語句型別 = (試跑工作.statement_type or "").upper()
    if 語句型別 != "SELECT":
        raise BigQuery查詢錯誤(
            f"只允許唯讀 SELECT 查詢，這句被 BigQuery 判定為 {語句型別 or '未知語句'}"
        )

    引用資料表 = [
        f"{表.project}.{表.dataset_id}.{表.table_id}" for 表 in (試跑工作.referenced_tables or [])
    ]
    越界 = [
        名稱
        for 名稱, 表 in zip(引用資料表, 試跑工作.referenced_tables or [])
        if not 設定.是否允許資料集(表.project, 表.dataset_id)
    ]
    if 越界:
        raise BigQuery查詢錯誤(
            f"查詢引用了允許範圍外的資料表：{', '.join(越界)}。可查範圍：{', '.join(設定.允許資料集)}"
        )

    掃描位元組 = int(試跑工作.total_bytes_processed or 0)
    if 掃描位元組 > 設定.可掃描位元組:
        raise BigQuery查詢錯誤(
            f"查詢預估掃描 {掃描位元組} bytes，超過上限 {設定.可掃描位元組} bytes。"
            "請縮小日期範圍、減少欄位或加上更嚴格的 WHERE 條件。"
        )
    return {"bytes_processed": 掃描位元組, "referenced_tables": 引用資料表}


def 執行查詢(客戶端, 參數: dict[str, Any], 設定: BigQuery查詢設定) -> dict[str, Any]:
    """驗證並執行唯讀 SQL，回傳截斷後的結果列。"""
    from google.cloud import bigquery

    sql = str(參數.get("sql") or "").strip()
    if not sql:
        raise BigQuery查詢錯誤("query 需要 sql")
    列數上限 = max(1, min(int(參數.get("max_rows") or 設定.回傳列數上限), 設定.回傳列數上限))

    驗證結果 = 驗證查詢(客戶端, sql, 設定)  # 與下方正式查詢送的是同一個 sql，沒有掉包空隙

    正式設定 = bigquery.QueryJobConfig(maximum_bytes_billed=設定.可掃描位元組)
    try:
        結果 = 客戶端.query(sql, job_config=正式設定).result(timeout=設定.逾時秒數)
    except Exception as 錯誤:
        raise BigQuery查詢錯誤(f"查詢執行失敗：{摘要錯誤訊息(錯誤)}") from 錯誤

    欄位清單 = [欄位.name for 欄位 in 結果.schema]
    列清單: list[dict[str, Any]] = []
    是否截斷 = False
    for 列 in 結果:
        if len(列清單) >= 列數上限:
            是否截斷 = True
            break
        列清單.append({欄位: 轉成可序列化(列.get(欄位)) for 欄位 in 欄位清單})

    return {
        "action": "query",
        "sql": sql,
        "columns": 欄位清單,
        "rows": 列清單,
        "row_count": len(列清單),
        "truncated": 是否截斷,
        "max_rows": 列數上限,
        "bytes_processed": 驗證結果["bytes_processed"],
        "referenced_tables": 驗證結果["referenced_tables"],
    }


def BigQuery查詢工具(參數: dict[str, Any]) -> dict[str, Any]:
    """`bigquery_query` 的 handler：依 action 分派到對應處理。

    參數：
        參數: 工具參數，必含 action；其餘依 action 而定。
    返回值：dict，可 JSON 序列化的查詢結果。
    """
    動作 = str(參數.get("action") or "").strip().lower()
    if 動作 not in 可用動作集合:
        raise BigQuery查詢錯誤(f"action 必須是 {'、'.join(sorted(可用動作集合))} 其中之一")

    設定 = 讀取BigQuery查詢設定()
    if 動作 == "list_datasets" and not 是否含萬用字元(設定):
        return 列出可查資料集(設定)  # 名單是靜態的，不必連線

    客戶端 = 建立BigQuery客戶端(設定)
    if 動作 == "list_datasets":
        return 列出可查資料集(設定, 客戶端)
    if 動作 == "list_tables":
        return 列出資料表(客戶端, 參數, 設定)
    if 動作 == "describe_table":
        return 描述資料表(客戶端, 參數, 設定)
    return 執行查詢(客戶端, 參數, 設定)
