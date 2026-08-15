"""驗證 CP4 遷移 0012 與唯一權威資料庫結構契約。"""

import sqlite3

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.資料庫結構契約 import (
    資料庫結構契約錯誤,
    資料庫結構指紋,
    計算資料庫結構指紋,
    遷移帳本,
    驗證資料庫結構,
)


def _建立資料庫(tmp_path):
    """建立已套用十二版遷移的測試資料庫。

    參數：``tmp_path`` 為 pytest 提供的隔離暫存目錄。
    回傳：已開啟外鍵檢查且含必要端點與版本資料的 SQLite 連線。
    例外：建庫或種子資料寫入失敗時原樣傳出 SQLite 或斷言錯誤。
    副作用：在暫存目錄建立資料庫檔案並寫入測試種子資料。
    """
    路徑 = tmp_path / "db.sqlite3"
    assert 初始化發布介面資料庫(路徑) == tuple(range(1, 16))
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
    return 連線


def test_第十二版帳本指紋資料表限制與不可變性(tmp_path):
    """確認第十二版物件、限制與不可變觸發器完整存在。

    參數：``tmp_path`` 為 pytest 提供的隔離暫存目錄。
    回傳：無；所有契約以斷言表示。
    例外：契約不符時由 pytest 回報斷言或預期外例外。
    副作用：建立暫存資料庫、寫入測試列並關閉連線。
    """
    連線 = _建立資料庫(tmp_path)
    assert 遷移帳本[11] == (12, "0012_建立技能套件收據.sql")
    assert 計算資料庫結構指紋(連線) == 資料庫結構指紋
    驗證資料庫結構(連線)
    名稱 = {列[0] for 列 in 連線.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "published_skill_bundles",
        "published_draft_consumptions",
        "published_endpoint_version_metadata",
    } <= 名稱
    連線.execute("INSERT INTO published_draft_consumptions VALUES('draft-1','ep-1',2)")
    連線.execute(
        "INSERT INTO published_endpoint_version_metadata VALUES('ver-1','initial_draft',0,0,0,0,0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        連線.execute("UPDATE published_draft_consumptions SET consumed_at=3")
    with pytest.raises(sqlite3.IntegrityError):
        連線.execute("DELETE FROM published_endpoint_version_metadata")
    連線.close()


def test_權威資料庫結構遭竄改時拒絕(tmp_path):
    """確認新增未知物件會使權威結構驗證關閉失敗。

    參數：``tmp_path`` 為 pytest 提供的隔離暫存目錄。
    回傳：無；所有契約以斷言表示。
    例外：契約不符時由 pytest 回報斷言或預期外例外。
    副作用：建立暫存資料庫、加入未知資料表並關閉連線。
    """
    連線 = _建立資料庫(tmp_path)
    連線.execute("CREATE TABLE unexpected(id INTEGER)")
    with pytest.raises(sqlite3.DatabaseError):
        驗證資料庫結構(連線)
    連線.close()


@pytest.mark.parametrize("破壞", ["帳本型別", "結構SQL型別", "結構SQL超限"])
def test_敵對或毀損中繼資料以固定結構錯誤關閉失敗(tmp_path, 破壞):
    """帳本及 sqlite_master 的 hostile type 或預算超限皆固定拒絕。

    參數：``tmp_path`` 是隔離目錄；``破壞`` 選擇中繼資料毀損方式。
    回傳：無；固定錯誤訊息以斷言表示。
    例外：契約不符時由 pytest 回報斷言失敗。
    副作用：建立暫存資料庫並透過 SQLite 測試介面毀損其中繼資料。
    """
    連線 = _建立資料庫(tmp_path)
    if 破壞 == "帳本型別":
        連線.execute(
            "UPDATE published_api_schema_migrations SET name=zeroblob(4) WHERE version=12"
        )
    else:
        連線.execute("PRAGMA writable_schema=ON")
        內容 = "x" if 破壞 == "結構SQL型別" else "x" * 65537
        表達式 = "zeroblob(1)" if 破壞 == "結構SQL型別" else "?"
        參數 = () if 破壞 == "結構SQL型別" else (內容,)
        連線.execute(
            f"UPDATE sqlite_master SET sql={表達式} WHERE name='published_skill_bundles'",
            參數,
        )
    with pytest.raises(資料庫結構契約錯誤, match="^資料庫結構契約錯誤$"):
        驗證資料庫結構(連線)
    連線.close()
