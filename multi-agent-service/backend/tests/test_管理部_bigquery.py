"""測試管理部 BigQuery 搜尋工具。"""

from 繁中代理.工具集.管理部_bigquery import 管理部BigQuery設定, 查詢關鍵字候選


class 假查詢工作:
    """保存 fake 查詢結果。"""

    def result(self):
        """回傳空結果，避免連線 BigQuery。"""
        return []


class 假BigQuery客戶端:
    """攔截 SQL 與 job_config 的 fake client。"""

    def __init__(self):
        """初始化紀錄欄位。"""
        self.sql = ""
        self.job_config = None

    def query(self, sql, job_config=None):
        """保存查詢參數並回傳 fake job。"""
        self.sql = sql
        self.job_config = job_config
        return 假查詢工作()


def test_關鍵字搜尋不把百分比與底線當_like_wildcard():
    """確認 keyword search 使用 STRPOS substring，比對原始使用者輸入。"""
    客戶端 = 假BigQuery客戶端()
    設定 = 管理部BigQuery設定(
        專案="p",
        資料集="d",
        文件表="documents",
        圖片表="images",
        地區=None,
        embedding模型="gemini-embedding-001",
        embedding維度=768,
    )

    查詢關鍵字候選(客戶端, 設定, "100%_補助", {}, 5)

    assert " LIKE " not in 客戶端.sql.upper()
    assert "STRPOS(LOWER(COALESCE(title, '')), LOWER(@keyword)) > 0" in 客戶端.sql
    assert 客戶端.job_config is not None
    參數表 = {參數.name: 參數.value for 參數 in 客戶端.job_config.query_parameters}
    assert 參數表["keyword"] == "100%_補助"
    assert "%100%_補助%" not in 參數表.values()
