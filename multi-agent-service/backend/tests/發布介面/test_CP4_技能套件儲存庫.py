"""驗證技能套件收據交易所有權與協調查詢。"""

import sqlite3

import pytest

import 繁中代理.發布介面.技能套件.儲存庫 as 儲存庫模組
from 繁中代理.發布介面.技能套件.儲存庫 import 套件收據儲存庫, 套件收據錯誤
from 繁中代理.發布介面.技能套件.發布器 import 套件發布收據
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _建立版本資料庫(暫存路徑):
    """建立已套用第十二版遷移且含單一端點版本的資料庫。"""
    路徑 = 暫存路徑 / "bundle-receipt.sqlite3"
    初始化發布介面資料庫(路徑)
    連線 = sqlite3.connect(路徑)
    連線.execute("PRAGMA foreign_keys=ON")
    連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
    連線.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
        "current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) "
        "VALUES('ep-1','owner-1','sa-1','slug','active',NULL,1,1,60,60)"
    )
    連線.execute(
        "INSERT INTO published_endpoint_versions VALUES("
        "'ver-1','ep-1',1,'r','p','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner-1',1)"
    )
    連線.commit()
    return 路徑, 連線


def _收據(暫存路徑, *, 清單摘要="a" * 64):
    """建立不要求實體檔案存在的有效收據投影。"""
    return 套件發布收據(
        "bundle-1",
        "bundle-1/manifest.json",
        清單摘要,
        "b" * 64,
        12,
        暫存路徑 / "bundle-1",
    )


def test_新增查詢與待協調投影不代替呼叫端提交(tmp_path):
    """新增只參與目前交易，rollback後不得留下資料。"""
    路徑, 連線 = _建立版本資料庫(tmp_path)
    儲存庫 = 套件收據儲存庫(連線)
    收據 = 儲存庫.新增(版本識別碼="ver-1", 收據=_收據(tmp_path), 發布時間=2.0)
    assert 收據.版本識別碼 == "ver-1"
    assert 儲存庫.依版本查詢("ver-1") == 收據
    assert 儲存庫.查詢待協調收據() == (收據,)
    assert 儲存庫.查詢未參照清單([
        "orphan/manifest.json", "bundle-1/manifest.json", "orphan/manifest.json"
    ]) == ("orphan/manifest.json",)
    連線.rollback()
    連線.close()
    with sqlite3.connect(路徑) as 驗證連線:
        assert 驗證連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)


def test_呼叫端提交後新連線可見不可變收據(tmp_path):
    """呼叫端明確commit後，另一連線可依版本取得相同收據。"""
    路徑, 連線 = _建立版本資料庫(tmp_path)
    預期 = 套件收據儲存庫(連線).新增(
        版本識別碼="ver-1", 收據=_收據(tmp_path), 發布時間=2.0
    )
    連線.commit()
    連線.close()
    with sqlite3.connect(路徑) as 驗證連線:
        assert 套件收據儲存庫(驗證連線).依版本查詢("ver-1") == 預期


def test_無效摘要與重複版本固定拒絕(tmp_path):
    """不合法摘要在SQL前拒絕，重複版本寫入映射固定錯誤。"""
    _, 連線 = _建立版本資料庫(tmp_path)
    儲存庫 = 套件收據儲存庫(連線)
    with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
        儲存庫.新增(版本識別碼="ver-1", 收據=_收據(tmp_path, 清單摘要="bad"), 發布時間=2.0)
    儲存庫.新增(版本識別碼="ver-1", 收據=_收據(tmp_path), 發布時間=2.0)
    with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
        儲存庫.新增(版本識別碼="ver-1", 收據=_收據(tmp_path), 發布時間=3.0)
    連線.rollback()
    連線.close()


def test_未參照清單hostile元素與上限加一皆固定拒絕(tmp_path):
    """重現 R2：不可雜湊元素不得洩漏 TypeError，iterable 只能耗用上限加一項。"""
    _, 連線 = _建立版本資料庫(tmp_path)
    儲存庫 = 套件收據儲存庫(連線)
    with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
        儲存庫.查詢未參照清單([[]])  # type: ignore[list-item]
    耗用數 = 0

    def 過大輸入():
        """產生超限合法參照並記錄耗用量；回傳 iterable，例外與副作用僅測試計數。"""
        nonlocal 耗用數
        for 索引 in range(儲存庫模組._最大清單參照數 + 100):
            耗用數 += 1
            yield f"bundle-{索引}/manifest.json"

    with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
        儲存庫.查詢未參照清單(過大輸入())
    assert 耗用數 == 儲存庫模組._最大清單參照數 + 1
    連線.close()


def test_published帶協調時間與reconciled缺時間雙向拒絕(tmp_path):
    """重現 R2：狀態與協調時間須雙向一致，避免新增查詢不可達收據。"""
    _, 連線 = _建立版本資料庫(tmp_path)
    儲存庫 = 套件收據儲存庫(連線)
    for 狀態, 協調時間 in (("published", 2.0), ("reconciled", None)):
        with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
            儲存庫.新增(
                版本識別碼="ver-1", 收據=_收據(tmp_path), 發布時間=1.0,
                狀態=狀態, 協調時間=協調時間,
            )
    連線.close()


def test_待協調查詢逐列重驗hostile_row_factory(tmp_path):
    """重現 R2：hostile SQLite row_factory 錯誤欄數須映射固定收據錯誤。"""
    _, 連線 = _建立版本資料庫(tmp_path)
    套件收據儲存庫(連線).新增(
        版本識別碼="ver-1", 收據=_收據(tmp_path), 發布時間=1.0
    )
    連線.row_factory = lambda _游標, _資料: ("hostile",)
    with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
        套件收據儲存庫(連線).查詢待協調收據()
    連線.close()


def test_待協調查詢超限固定拒絕且SQL有界(tmp_path):
    """重現 R2：超過固定上限的 pending rows 不得無界物化或部分回傳。"""
    _, 連線 = _建立版本資料庫(tmp_path)
    連線.execute("PRAGMA foreign_keys=OFF")
    欄位列 = (
        (
            f"bundle-{索引}", f"ver-{索引}", f"bundle-{索引}/manifest.json",
            "a" * 64, "b" * 64, 1, "published", 1.0, None,
        )
        for 索引 in range(儲存庫模組._最大清單參照數 + 1)
    )
    連線.executemany(
        "INSERT INTO published_skill_bundles(bundle_id,version_id,manifest_reference,manifest_digest,"
        "bundle_hash,total_bytes,state,published_at,reconciled_at) VALUES(?,?,?,?,?,?,?,?,?)",
        欄位列,
    )
    with pytest.raises(套件收據錯誤, match="^套件收據錯誤$"):
        套件收據儲存庫(連線).查詢待協調收據()
    連線.close()
