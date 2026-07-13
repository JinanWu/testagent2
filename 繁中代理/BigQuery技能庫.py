"""BigQuery 技能庫。

功能：
    以 BigQuery 儲存技能的三張表，與本機（檔案 / 硬碟）版維持相同的 I/O 出入口
    語意，供 `STORAGE_BACKEND=bigquery` 時切換：

      skill_usage_events  僅追加使用事件。每輪結束時把用過的技能各追加
                          一列，只加不改。
      skill_usage         使用量快照（可變列）。策展器讀事件彙總後**覆寫**
                          use_count / last_used_at；pinned / state / created_at
                          等非事件推導的欄位一起放這張，逐次整表覆寫。
      user_skills         技能內容本體（每技能一列，content 存 SKILL.md 全文）。
                          生命週期 state 只住 skill_usage，列技能時 JOIN 兩表濾掉
                          archived（一個事實一個家）。

    對應的 I/O 出入口——事件：`記錄多筆事件` / `讀取所有事件`；使用量：
    `讀取全部使用量` / `寫入全部使用量`；內容：`建立技能` / `讀取技能內容` /
    `更新技能內容` / `刪除技能` / `列出技能身分`。上層邏輯（彙總、變更、設定釘選、
    設定狀態、遺忘，以及技能的建立 / 編輯 / 刪除 / 列出）完全沿用本機版，只在這些
    出入口依 `STORAGE_BACKEND` 分流，不因後端而異。

環境變數：見 `環境設定.讀取核心BigQuery設定`（CORE_BQ_PROJECT 等）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from .環境設定 import 讀取核心BigQuery設定, 應跳過建表

記錄器 = logging.getLogger(__name__)

事件表 = "skill_usage_events"
使用量表 = "skill_usage"
內容表 = "user_skills"

事件型別 = {"skill_id": "STRING", "user_id": "STRING", "used_at": "STRING"}
內容型別 = {
    "skill_id": "STRING", "user_id": "STRING", "name": "STRING", "category": "STRING",
    "content": "STRING", "created_at": "STRING", "updated_at": "STRING",
}
使用量型別 = {
    "skill_id": "STRING", "user_id": "STRING", "use_count": "INT64",
    "last_used_at": "STRING", "state": "STRING", "pinned": "BOOL", "created_at": "STRING",
}
# 對照 技能使用量._空白記錄()：每列（除主鍵 skill_id 外）的欄位名稱
使用量欄位 = ("user_id", "use_count", "last_used_at", "state", "pinned", "created_at")

# 每個行程已確保過資料表的資料集（避免重複執行建表語句）
已確保技能資料表: set[str] = set()


class BigQuery技能庫:
    """以 BigQuery 儲存 skill_usage_events / skill_usage / user_skills 的技能庫。

    只負責把上層要求的讀寫翻譯成 BigQuery 查詢；決策邏輯（要記哪些、如何彙總、
    釘選規則、技能的建立 / 編輯 / 刪除）仍留在 `工具集.技能使用事件`、
    `工具集.技能使用量`、`工具集.技能管理` 與 `基本工具`，本類一概不碰。
    """

    def __init__(self) -> None:
        """初始化技能庫並確保三張資料表存在。

        參數：無。
        返回值：無。副作用為讀取核心 BigQuery 設定並（必要時）建立資料表。
        """
        self.設定 = 讀取核心BigQuery設定()
        self._已建立用戶端 = None
        self.確保資料表()

    @property
    def 用戶端(self):
        """延遲建立並快取 BigQuery 用戶端。

        參數：無。
        返回值：google.cloud.bigquery.Client。缺少套件時丟出 RuntimeError。
        """
        if self._已建立用戶端 is None:
            try:
                from google.cloud import bigquery
            except ImportError as 錯誤:
                raise RuntimeError("缺少 google-cloud-bigquery，無法使用 BigQuery 儲存後端") from 錯誤
            self._已建立用戶端 = bigquery.Client(project=self.設定["project"], location=self.設定["location"])
        return self._已建立用戶端

    def 組出完整表名(self, 表: str) -> str:
        """把資料表名稱組成 `專案.資料集.資料表` 的完整名稱。

        參數：
            表: 資料表短名稱（例如 skill_usage）。
        返回值：str，可直接放進 SQL 的完整表名。
        """
        return f"{self.設定['project']}.{self.設定['dataset']}.{表}"

    # ── 建立資料表 ──────────────────────────────────────────────────────────
    def 確保資料表(self) -> None:
        """建立 skill_usage_events / skill_usage / user_skills（若尚未存在）。

        每個行程對同一資料集只實際執行一次建表語句；`CORE_BQ_SKIP_DDL` 為真時
        直接跳過（前提是資料表已存在）。

        參數：無。
        返回值：無。
        """
        資料集鍵 = f"{self.設定['project']}.{self.設定['dataset']}"
        if 資料集鍵 in 已確保技能資料表:
            return
        if 應跳過建表():
            已確保技能資料表.add(資料集鍵)
            return
        from google.cloud import bigquery

        資料集參照 = bigquery.Dataset(資料集鍵)
        if self.設定["location"]:
            資料集參照.location = self.設定["location"]
        self.用戶端.create_dataset(資料集參照, exists_ok=True)
        for 語句 in (self.組出事件表建表語句(), self.組出使用量表建表語句(), self.組出內容表建表語句()):
            self.用戶端.query(語句).result()
        已確保技能資料表.add(資料集鍵)

    def 組出事件表建表語句(self) -> str:
        """組出 skill_usage_events 的建表 SQL（每次技能使用追加一列，只加不改）。

        參數：無。
        返回值：str，CREATE TABLE 語句。
        """
        return f"""
        CREATE TABLE IF NOT EXISTS `{self.組出完整表名(事件表)}` (
            skill_id STRING NOT NULL,
            user_id STRING,
            used_at STRING
        )
        """

    def 組出使用量表建表語句(self) -> str:
        """組出 skill_usage 的建表 SQL（每技能一列的可變快照，以 skill_id 為鍵）。

        參數：無。
        返回值：str，CREATE TABLE 語句。
        """
        return f"""
        CREATE TABLE IF NOT EXISTS `{self.組出完整表名(使用量表)}` (
            skill_id STRING NOT NULL,
            user_id STRING,
            use_count INT64,
            last_used_at STRING,
            state STRING,
            pinned BOOL,
            created_at STRING
        )
        """

    def 組出內容表建表語句(self) -> str:
        """組出 user_skills 的建表 SQL（每技能一列，content 存 SKILL.md 全文）。

        注意：**封存狀態不放這張表**。生命週期 state（active/stale/archived）只住
        表三 skill_usage；列技能時 JOIN 表一＋表三並濾掉 archived。一個事實一個家，
        避免兩張表各記一份封存狀態而不同步。

        參數：無。
        返回值：str，CREATE TABLE 語句。
        """
        return f"""
        CREATE TABLE IF NOT EXISTS `{self.組出完整表名(內容表)}` (
            skill_id STRING NOT NULL,
            user_id STRING,
            name STRING,
            category STRING,
            content STRING,
            created_at STRING,
            updated_at STRING
        )
        """

    # ── 事件表（僅追加）I/O 出入口 ──────────────────────────────────────────
    def 記錄多筆事件(self, 技能識別碼清單: Iterable[str], 使用者識別碼: str | None, 使用時間: str) -> int:
        """把多個技能各追加一列使用事件到 skill_usage_events。

        參數：
            技能識別碼清單: 該輪用過的技能 skill_id（呼叫端應先去重）。
            使用者識別碼: 觸發使用的使用者 user_id，可為 None。
            使用時間: 使用時間 ISO 字串。
        返回值：int，實際寫入筆數；輸入為空或寫入失敗時回 0。
        """
        識別碼清單 = [str(識別碼) for 識別碼 in 技能識別碼清單 if 識別碼]
        if not 識別碼清單:
            return 0
        使用者 = str(使用者識別碼) if 使用者識別碼 else None
        列清單 = [{"skill_id": 識別碼, "user_id": 使用者, "used_at": 使用時間} for 識別碼 in 識別碼清單]
        try:
            self.插入多列(事件表, 列清單, 事件型別)
        except Exception as 錯誤:
            記錄器.debug("BigQuery 記錄多筆事件失敗：%s", 錯誤, exc_info=True)
            return 0
        return len(識別碼清單)

    def 讀取所有事件(self) -> list[dict[str, Any]]:
        """讀取整張事件表。

        參數：無。
        返回值：list[dict]，每筆為 {skill_id, user_id, used_at}；失敗時回空清單。
        """
        try:
            return self.查詢多列(
                f"SELECT skill_id, user_id, used_at FROM `{self.組出完整表名(事件表)}`"
            )
        except Exception as 錯誤:
            記錄器.debug("BigQuery 讀取所有事件失敗：%s", 錯誤, exc_info=True)
            return []

    # ── 使用量表（可變快照）I/O 出入口 ──────────────────────────────────────
    def 讀取全部使用量(self) -> dict[str, dict[str, Any]]:
        """讀取整張使用量表。

        參數：無。
        返回值：dict，鍵為 skill_id、值為不含 skill_id 的記錄 dict；失敗時回空 dict。
        """
        try:
            列清單 = self.查詢多列(f"SELECT * FROM `{self.組出完整表名(使用量表)}`")
        except Exception as 錯誤:
            記錄器.debug("BigQuery 讀取全部使用量失敗：%s", 錯誤, exc_info=True)
            return {}
        結果: dict[str, dict[str, Any]] = {}
        for 列 in 列清單:
            技能識別碼 = 列.get("skill_id")
            if not 技能識別碼:
                continue
            結果[str(技能識別碼)] = {欄位: 列.get(欄位) for 欄位 in 使用量欄位}
        return 結果

    def 寫入全部使用量(self, 使用量資料: dict[str, dict[str, Any]]) -> None:
        """整表覆寫使用量快照；盡力而為，失敗只記錄日誌不丟出。

        呼叫端（技能使用量.變更 / 遺忘）已在檔鎖內完成「讀取-修改-寫回」，這裡
        以「清空後整批寫入」落地整份快照。寫入量低（工作階段結束、策展器每 72
        小時），整表覆寫可接受。

        參數：
            使用量資料: 鍵為 skill_id、值為記錄 dict 的完整快照。
        返回值：無。
        """
        try:
            self.執行DML(f"TRUNCATE TABLE `{self.組出完整表名(使用量表)}`")
            列清單 = [
                {"skill_id": str(技能識別碼), **{欄位: 記錄.get(欄位) for 欄位 in 使用量欄位}}
                for 技能識別碼, 記錄 in 使用量資料.items()
                if isinstance(記錄, dict)
            ]
            if 列清單:
                self.插入多列(使用量表, 列清單, 使用量型別)
        except Exception as 錯誤:
            記錄器.debug("BigQuery 寫入全部使用量失敗：%s", 錯誤, exc_info=True)

    # ── 內容表（技能全文）I/O 出入口 ────────────────────────────────────────
    def 建立技能(self, skill_id: str, 名稱: str, 內容: str, 分類: str | None = None,
               user_id: str | None = None, 建立時間: str | None = None) -> None:
        """把新技能寫入 user_skills（content 存 SKILL.md 全文）。

        參數：
            skill_id: 技能穩定 UUID。
            名稱: 技能名稱（= 目錄名語意）。
            內容: SKILL.md 全文。
            分類: 可選分類（單層目錄名語意）；無分類時為 None。
            user_id: 建立者的使用者識別碼。
            建立時間: 建立時間的 ISO 8601 字串；None 表示不填該欄位。
        返回值：無（直接寫入 BigQuery）。
        """
        self.插入多列(內容表, [{
            "skill_id": skill_id, "user_id": user_id, "name": 名稱, "category": 分類,
            "content": 內容, "created_at": 建立時間, "updated_at": 建立時間,
        }], 內容型別)

    def 讀取技能內容(self, 名稱: str, user_id: str | None = None,
                 限定使用者: bool = False) -> dict[str, Any] | None:
        """依名稱讀取單一技能列（含 content）；找不到回 None。

        參數：
            名稱: 技能名稱。
            user_id: 擁有者識別碼；`限定使用者=True` 時據此過濾（None 代表 NULL 擁有者）。
            限定使用者: True 時只找該 user_id 的技能，達成跨使用者隔離；False 為全域查。
        返回值：dict | None，欄位同 user_skills。
        """
        from google.cloud import bigquery

        條件 = ["name=@name"]
        參數 = [bigquery.ScalarQueryParameter("name", "STRING", 名稱)]
        if 限定使用者:
            if user_id is None:
                條件.append("user_id IS NULL")
            else:
                條件.append("user_id=@uid")
                參數.append(bigquery.ScalarQueryParameter("uid", "STRING", user_id))
        列清單 = self.查詢多列(
            f"SELECT * FROM `{self.組出完整表名(內容表)}` WHERE {' AND '.join(條件)} LIMIT 1",
            參數,
        )
        return 列清單[0] if 列清單 else None

    def 更新技能內容(self, skill_id: str, 內容: str, 更新時間: str | None = None) -> None:
        """整段覆寫某技能的 content（skill_id 不變）。

        參數：
            skill_id: 技能 UUID。
            內容: 新的 SKILL.md 全文。
            更新時間: ISO 字串。
        返回值：無。
        """
        from google.cloud import bigquery

        self.執行DML(
            f"UPDATE `{self.組出完整表名(內容表)}` SET content=@content, updated_at=@now WHERE skill_id=@sid",
            [
                bigquery.ScalarQueryParameter("content", "STRING", 內容),
                bigquery.ScalarQueryParameter("now", "STRING", 更新時間),
                bigquery.ScalarQueryParameter("sid", "STRING", skill_id),
            ],
        )

    def 刪除技能(self, skill_id: str) -> None:
        """從 user_skills 刪除某技能列（使用量/事件由呼叫端另行處理）。

        參數：
            skill_id: 技能 UUID。
        返回值：無。
        """
        from google.cloud import bigquery

        self.執行DML(
            f"DELETE FROM `{self.組出完整表名(內容表)}` WHERE skill_id=@sid",
            [bigquery.ScalarQueryParameter("sid", "STRING", skill_id)],
        )

    def 列出技能身分(self, user_id: str | None = None, 是否包含封存: bool = False,
                 限定使用者: bool = False) -> list[dict[str, Any]]:
        """列出技能身分（skill_id/name/category…），預設濾掉封存。

        封存狀態不在本表：以 skill_id LEFT JOIN 表三 skill_usage 取得 state，
        state=archived 者預設排除（對照硬碟版把封存技能搬到 .archive 而不列出）。

        參數：
            user_id: 擁有者識別碼；`限定使用者=True` 時據此過濾（None 代表 NULL 擁有者）。
            是否包含封存: True 時連 archived 一起列出。
            限定使用者: True 時只列該 user_id 的技能，達成跨使用者隔離；False 為全域（供策展器/使用量）。
        返回值：list[dict]，每筆為 user_skills 的欄位。
        """
        from google.cloud import bigquery

        條件: list[str] = []
        參數: list[Any] = []
        if not 是否包含封存:
            條件.append("COALESCE(u.state, 'active') != 'archived'")
        if 限定使用者:
            if user_id is None:
                條件.append("s.user_id IS NULL")
            else:
                條件.append("s.user_id = @user_id")
                參數.append(bigquery.ScalarQueryParameter("user_id", "STRING", user_id))
        where = (" WHERE " + " AND ".join(條件)) if 條件 else ""
        return self.查詢多列(
            f"""
            SELECT s.skill_id, s.user_id, s.name, s.category, s.content,
                   s.created_at, s.updated_at
            FROM `{self.組出完整表名(內容表)}` s
            LEFT JOIN `{self.組出完整表名(使用量表)}` u ON u.skill_id = s.skill_id
            {where}
            ORDER BY s.created_at
            """,
            參數,
        )

    # ── BigQuery 查詢／寫入小工具 ──────────────────────────────────────────
    def 查詢多列(self, 語句: str, 參數清單: list[Any] | None = None) -> list[dict[str, Any]]:
        """執行查詢並回傳所有列。

        參數：
            語句: SQL 查詢字串。
            參數清單: 查詢參數（bigquery.ScalarQueryParameter）清單，可為 None。
        返回值：list[dict]，每列轉成 dict。
        """
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數清單 or [])
        return [dict(列) for 列 in self.用戶端.query(語句, job_config=工作設定).result()]

    def 執行DML(self, 語句: str, 參數清單: list[Any] | None = None) -> None:
        """執行 INSERT / UPDATE / DELETE / TRUNCATE 等 DML 語句。

        參數：
            語句: SQL DML 字串。
            參數清單: 查詢參數清單，可為 None。
        返回值：無。
        """
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數清單 or [])
        self.用戶端.query(語句, job_config=工作設定).result()

    def 插入多列(self, 表: str, 列清單: list[dict[str, Any]], 型別對應: dict[str, str]) -> None:
        """以單一 DML 一次插入多列（每列參數名加序號後綴以避免衝突）。

        參數：
            表: 目標資料表短名稱。
            列清單: 待插入的列（每列為欄位對值的 dict）。
            型別對應: 欄位名對 BigQuery 型別字串的對照。
        返回值：無。列清單為空時不動作。
        """
        from google.cloud import bigquery

        if not 列清單:
            return
        欄位清單 = list(型別對應.keys())
        欄位字串 = ", ".join(欄位清單)
        群組清單: list[str] = []
        參數清單 = []
        for 序號, 列 in enumerate(列清單):
            群組清單.append("(" + ", ".join(f"@{欄位}_{序號}" for 欄位 in 欄位清單) + ")")
            參數清單.extend(
                bigquery.ScalarQueryParameter(f"{欄位}_{序號}", 型別對應[欄位], 列.get(欄位))
                for 欄位 in 欄位清單
            )
        self.執行DML(
            f"INSERT INTO `{self.組出完整表名(表)}` ({欄位字串}) VALUES {', '.join(群組清單)}",
            參數清單,
        )


# 每個行程快取單一技能庫實例（確保資料表只建立一次）
_技能庫實例: BigQuery技能庫 | None = None


def 取得技能庫() -> BigQuery技能庫:
    """回傳行程內共用的 BigQuery 技能庫實例（延遲建立）。

    參數：無。
    返回值：BigQuery技能庫 單例。
    """
    global _技能庫實例
    if _技能庫實例 is None:
        _技能庫實例 = BigQuery技能庫()
    return _技能庫實例


def 取得啟用中的技能庫() -> "BigQuery技能庫 | None":
    """依 STORAGE_BACKEND 回傳雲端技能庫，或 None（sqlite 模式走本機）。

    集中「載入 .env + 判斷後端」的唯一邏輯，供各技能模組共用，避免各自重寫。

    參數：無。
    返回值：BigQuery技能庫 實例（STORAGE_BACKEND=bigquery 時）；否則 None。
    """
    from .環境設定 import 載入本機環境檔

    載入本機環境檔()
    if (os.getenv("STORAGE_BACKEND") or "sqlite").strip().lower() != "bigquery":
        return None
    return 取得技能庫()
