"""凍結每個不可變版本各自擁有技能套件的 v1／v2 整合契約。

參數：測試以真實 SQLite、owner authority、發布器、協調器與 P04／P05 服務組裝環境。
回傳：不適用；各測試以斷言固定版本、套件、收據、稽核與 current pointer 關係。
例外：產品違反原子性、隔離、權限或資訊遮蔽契約時由 pytest 回報。
副作用：只在 pytest 暫存目錄建立技能來源、資料庫與不可變套件。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from 繁中代理.發布介面.路由.規劃發布 import 端點發布結果, 管理操作錯誤, 版本建立結果
from 繁中代理.發布介面.規劃.版本服務 import SQLite目前版本解析器

from test_CP4_發布管理協調器 import _建立環境, _確認


def _發布第一版(環境: dict) -> 端點發布結果:
    """透過 production 協調器建立 v1 並要求回傳正式發布收據。

    參數：``環境`` 是共享整合 fixture 字典。
    回傳：成功的 ``端點發布結果``。
    例外：結果不是正式收據時以斷言失敗。
    副作用：發布 v1 套件並提交初始端點圖形。
    """
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert type(結果) is 端點發布結果
    return 結果


def _第二版配置() -> dict:
    """建立只含允許變更版本欄位的 detached v2 配置。

    參數：無。
    回傳：新的版本配置字典。
    例外：無預期例外。
    副作用：只配置新的 JSON 容器。
    """
    return {
        "original_requirement_text": "建立 Alpha API 第二版",
        "system_prompt": "第二版只根據技能回答",
        "model_config_snapshot": {"model": "published-v2", "temperature": 0},
        "retry_policy": {"max_attempts": 2},
        "input_schema": None,
        "response_schema": {
            "type": "object", "properties": {"answer": {"type": "string"}},
            "required": ["answer"], "additionalProperties": False,
        },
    }


def test_P05失敗時current保持v1且已發布v2套件成為孤兒(tmp_path: Path, monkeypatch) -> None:
    """檔案系統成功而資料庫失敗時不得留下 v2 圖形或切換 current。

    參數：``tmp_path`` 提供環境；``monkeypatch`` 注入 P05 ordinary failure。
    回傳：無。
    例外：失敗未固定映射或孤兒／資料庫狀態不符時斷言失敗。
    副作用：發布 v1 與 v2 套件，第二個套件最後移入孤兒目錄。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)

    def 資料庫失敗(**_參數):
        """在 v2 bundle 已耐久後模擬 P05 ordinary failure。

        參數：接收 production P05 的關鍵字輸入但不使用。
        回傳：不會正常回傳。
        例外：固定拋出不含敏感資料的執行期錯誤。
        副作用：不寫資料庫。
        """
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(環境["版本服務"], "配置並啟用", 資料庫失敗)
    結果 = 環境["服務"].原子建立並切換版本(
        擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
        配置=_第二版配置(),
    )
    assert 結果 == 管理操作錯誤("internal")
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute(
            "SELECT current_version_id FROM published_endpoints WHERE id='endpoint-1'"
        ).fetchone() == ("version-1",)
        assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (1,)
    assert not (環境["套件根"] / "bundle-2").exists()
    assert (環境["套件根"] / ".orphaned" / "bundle-2" / "manifest.json").is_file()


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_v2控制流程保留同一物件並隔離套件(
    tmp_path: Path, monkeypatch, 控制型別: type[BaseException],
) -> None:
    """P05 控制流程必須原樣傳出，且清理錯誤不得覆蓋主要例外。

    參數：暫存根、patch fixture 與三種控制流程例外型別。
    回傳：無。
    例外：預期傳出注入的同一控制流程物件。
    副作用：發布 v1 與 v2 套件，並將 v2 隔離為孤兒。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    主要 = 控制型別("v2-control", 23)

    def 交易控制(**_參數):
        """模擬 P05 在 bundle 耐久後發生控制流程。

        參數：接收但不使用 P05 關鍵字輸入。
        回傳：不會正常回傳。
        例外：拋出測試建立的主要控制流程物件。
        副作用：不寫資料庫。
        """
        raise 主要

    monkeypatch.setattr(環境["版本服務"], "配置並啟用", 交易控制)
    with pytest.raises(控制型別) as 捕捉:
        環境["服務"].原子建立並切換版本(
            擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
            配置=_第二版配置(),
        )
    assert 捕捉.value is 主要 and 捕捉.value.args == ("v2-control", 23)
    assert (環境["套件根"] / ".orphaned" / "bundle-2").is_dir()


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_v2提交已耐久後控制流程保留active套件與current一致(
    tmp_path: Path, 控制型別: type[BaseException],
) -> None:
    """COMMIT 已耐久後的控制流程不得把 current 引用的 v2 套件隔離。

    參數：``tmp_path`` 提供真實環境；``控制型別`` 依序覆蓋三種控制流程例外。
    回傳：無。
    例外：預期 P05 在耐久提交後傳出測試建立的同一控制流程物件。
    副作用：發布 v1，再以自訂 SQLite 連線完成 v2 COMMIT 後注入控制流程。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    主要 = 控制型別("durable-v2-control", 29)

    class 提交後控制連線(sqlite3.Connection):
        """只在 v2 current 指標已更新的 COMMIT 完成後拋出指定控制流程。"""

        是否已更新目前指標 = False
        是否已拋出控制流程 = False

        def execute(self, sql, parameters=()):
            """委派真實 SQLite，記錄 v2 指標更新並在其 COMMIT 後注入控制流程。

            參數：``sql`` 與 ``parameters`` 是 SQLite execute 的既有公開契約。
            回傳：非目標 COMMIT 時回傳真實 cursor。
            例外：目標 COMMIT 已完成後拋出測試建立的主要控制流程物件。
            副作用：執行真實 SQL，並在 v2 指標更新時設定類別旗標。
            """
            if sql.startswith("UPDATE published_endpoints SET current_version_id="):
                type(self).是否已更新目前指標 = True
            結果 = super().execute(sql, parameters)
            if (
                sql == "COMMIT" and type(self).是否已更新目前指標
                and not type(self).是否已拋出控制流程
            ):
                type(self).是否已拋出控制流程 = True
                raise 主要
            return 結果

    def 建立提交後控制連線(*參數, **選項):
        """建立只在 v2 耐久提交後注入控制流程的 SQLite 連線。

        參數：沿用 ``sqlite3.connect`` 的位置與關鍵字參數。
        回傳：新的 ``提交後控制連線``。
        例外：真實 SQLite 連線錯誤原樣傳出。
        副作用：開啟呼叫端指定的 SQLite 資料庫。
        """
        return sqlite3.connect(*參數, **選項, factory=提交後控制連線)

    環境["版本服務"]._連線工廠 = 建立提交後控制連線
    with pytest.raises(控制型別) as 捕捉:
        環境["服務"].原子建立並切換版本(
            擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
            配置=_第二版配置(),
        )
    assert 捕捉.value is 主要 and 捕捉.value.args == ("durable-v2-control", 29)
    assert (環境["套件根"] / "bundle-2" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-2").exists()
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute(
            "SELECT current_version_id FROM published_endpoints WHERE id='endpoint-1'"
        ).fetchone() == ("version-2",)
        assert 連線.execute(
            "SELECT version_id,state FROM published_skill_bundles WHERE bundle_id='bundle-2'"
        ).fetchone() == ("version-2", "published")


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_v2正常COMMIT返回後關閉控制仍以readback保留active套件(
    tmp_path: Path, 控制型別: type[BaseException],
) -> None:
    """正常 COMMIT 返回後的 close 控制流程仍應由 SQLite readback 證明已提交。

    參數：``tmp_path`` 提供真實環境；``控制型別`` 覆蓋三種控制流程例外。
    回傳：無。
    例外：預期 P05 在 COMMIT 成功後關閉連線時傳出同一控制流程物件。
    副作用：第一條連線準備版本、第二條提交 v2 後注入 close 控制、第三條唯讀判定。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    主要 = 控制型別("post-commit-close-control", 41)
    連線次數 = 0

    class 提交後關閉控制連線(sqlite3.Connection):
        """在正常 COMMIT 返回後的第一次 close 注入指定控制流程。"""

        是否已拋出控制流程 = False

        def close(self):
            """第一次關閉已提交連線時拋控制流程，其餘關閉委派真實 SQLite。

            參數：無額外參數。
            回傳：非注入路徑沿用 SQLite ``close`` 回傳值。
            例外：第一次呼叫固定拋出測試建立的主要控制流程物件。
            副作用：注入後由 production base fallback 釋放連線。
            """
            if not type(self).是否已拋出控制流程:
                type(self).是否已拋出控制流程 = True
                raise 主要
            return super().close()

    def 建立分階段連線(*參數, **選項):
        """依準備、P05 與 readback 順序建立 normal／control／normal 連線。

        參數：沿用 ``sqlite3.connect`` 的位置與關鍵字參數。
        回傳：第二條為 ``提交後關閉控制連線``，其餘為標準 SQLite 連線。
        例外：真實 SQLite 連線錯誤原樣傳出。
        副作用：每次呼叫遞增本測試連線序號並開啟資料庫。
        """
        nonlocal 連線次數
        連線次數 += 1
        if 連線次數 == 2:
            return sqlite3.connect(*參數, **選項, factory=提交後關閉控制連線)
        return sqlite3.connect(*參數, **選項)

    環境["版本服務"]._連線工廠 = 建立分階段連線
    with pytest.raises(控制型別) as 捕捉:
        環境["服務"].原子建立並切換版本(
            擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
            配置=_第二版配置(),
        )
    assert 捕捉.value is 主要 and 捕捉.value.args == ("post-commit-close-control", 41)
    assert 連線次數 == 3
    assert (環境["套件根"] / "bundle-2" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-2").exists()
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute(
            "SELECT current_version_id FROM published_endpoints WHERE id='endpoint-1'"
        ).fetchone() == ("version-2",)
        assert 連線.execute(
            "SELECT version_id,state FROM published_skill_bundles WHERE bundle_id='bundle-2'"
        ).fetchone() == ("version-2", "published")


def test_提交判定遭次要控制流程時保留active套件與主要例外(
    tmp_path: Path, monkeypatch,
) -> None:
    """權威 readback 無法判定時不得隔離候選，也不得覆蓋主要控制流程。

    參數：``tmp_path`` 提供真實環境；``monkeypatch`` 注入主要 P05 與次要 readback 控制。
    回傳：無。
    例外：預期最終仍傳出原始主要 ``SystemExit`` 物件。
    副作用：發布 v1 與 v2 套件；資料庫未提交 v2，候選因無法判定而保持 active。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    主要 = SystemExit("primary-control", 31)
    次要 = KeyboardInterrupt("readback-control", 37)
    連線次數 = 0

    def 提交前主要控制(**_參數):
        """在 v2 bundle 已耐久但 P05 尚未寫入前拋出主要控制流程。

        參數：接收但不使用 P05 關鍵字參數。
        回傳：不會正常回傳。
        例外：固定拋出測試建立的主要控制流程物件。
        副作用：不寫入資料庫。
        """
        raise 主要

    def 讀取判定控制(*_參數, **_選項):
        """模擬權威 readback 開啟 SQLite 時收到次要控制流程。

        參數：接收但不使用 SQLite 連線參數。
        回傳：第一次呼叫回傳正常 SQLite 連線供版本準備，第二次不正常回傳。
        例外：第二次呼叫固定拋出測試建立的次要控制流程物件。
        副作用：第一次開啟資料庫；第二次不開啟資源。
        """
        nonlocal 連線次數
        連線次數 += 1
        if 連線次數 == 1:
            return sqlite3.connect(*_參數, **_選項)
        raise 次要

    monkeypatch.setattr(環境["版本服務"], "配置並啟用", 提交前主要控制)
    環境["版本服務"]._連線工廠 = 讀取判定控制
    with pytest.raises(SystemExit) as 捕捉:
        環境["服務"].原子建立並切換版本(
            擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
            配置=_第二版配置(),
        )
    assert 捕捉.value is 主要 and 捕捉.value.args == ("primary-control", 31)
    assert 連線次數 == 2
    assert (環境["套件根"] / "bundle-2" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-2").exists()
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute(
            "SELECT current_version_id FROM published_endpoints WHERE id='endpoint-1'"
        ).fetchone() == ("version-1",)
        assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)


def test_inflight保留v1釘選而下一次解析取得v2(tmp_path: Path) -> None:
    """切換 current 不得突變已取得的 v1 DTO，後續解析才看見 v2。

    參數：``tmp_path`` 是 pytest 暫存根目錄。
    回傳：無。
    例外：解析版本身分或快照內容漂移時斷言失敗。
    副作用：發布兩版並以唯讀解析器各解析一次 current。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    解析器 = SQLite目前版本解析器(環境["資料庫"])
    執行中舊釘選 = 解析器.依slug解析("alpha-api")
    結果 = 環境["服務"].原子建立並切換版本(
        擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
        配置=_第二版配置(),
    )
    下一請求釘選 = 解析器.依slug解析("alpha-api")
    assert type(結果) is 版本建立結果
    assert (執行中舊釘選.version_id, 執行中舊釘選.version_number) == ("version-1", 1)
    assert 執行中舊釘選.取得版本快照().system_prompt == "只根據技能回答"
    assert (下一請求釘選.version_id, 下一請求釘選.version_number) == ("version-2", 2)
    assert 下一請求釘選.取得版本快照().system_prompt == "第二版只根據技能回答"


def test_owner撤權在預配前固定拒絕且不建立v2套件(tmp_path: Path) -> None:
    """Canonical owner 已停用時必須在識別與 bundle 副作用之前 fail closed。

    參數：``tmp_path`` 是 pytest 暫存根目錄。
    回傳：無。
    例外：仍建立 v2 或錯誤分類不固定時斷言失敗。
    副作用：發布 v1、停用真實 Web owner，然後嘗試建立 v2。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    環境["使用者庫"].設定使用者停用("owner", True)
    結果 = 環境["服務"].原子建立並切換版本(
        擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
        配置=_第二版配置(),
    )
    assert 結果 == 管理操作錯誤("forbidden")
    assert not (環境["套件根"] / "bundle-2").exists()
    assert not (環境["套件根"] / ".orphaned" / "bundle-2").exists()
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)
        assert 連線.execute(
            "SELECT current_version_id FROM published_endpoints WHERE id='endpoint-1'"
        ).fetchone() == ("version-1",)


def test_owner解析後技能hash漂移拒絕v2並隔離且錯誤不洩漏(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """Authority 與 publisher 掃描間的 skill 漂移不得提交，且公開面不含路徑或內容。

    參數：暫存根、patch fixture 與日誌擷取 fixture。
    回傳：無。
    例外：漂移未拒絕、孤兒未隔離或敏感 marker 外洩時斷言失敗。
    副作用：發布 v1，在 v2 publisher 掃描窗口改寫 live ``SKILL.md`` 並隔離成果。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    原發布 = 環境["服務"]._套件發布器.發布
    敏感內容 = "PRIVATE-SKILL-CONTENT-MARKER"
    敏感路徑 = str(環境["技能主檔"])

    def 漂移後發布(**選項):
        """在 authority 已解析後改寫來源，再委派真實不可變發布器。

        參數：``選項`` 是 production publisher 的完整關鍵字輸入。
        回傳：真實 publisher 的套件收據。
        例外：真實 publisher 例外原樣傳出。
        副作用：覆寫 live skill，再建立 v2 bundle。
        """
        環境["技能主檔"].write_text(敏感內容, encoding="utf-8")
        return 原發布(**選項)

    monkeypatch.setattr(環境["服務"]._套件發布器, "發布", 漂移後發布)
    結果 = 環境["服務"].原子建立並切換版本(
        擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
        配置=_第二版配置(),
    )
    assert 結果 == 管理操作錯誤("internal")
    assert (環境["套件根"] / ".orphaned" / "bundle-2").is_dir()
    公開文字 = repr(結果) + "\n" + "\n".join(紀錄.getMessage() for 紀錄 in caplog.records)
    assert 敏感內容 not in 公開文字 and 敏感路徑 not in 公開文字
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)
        稽核文字 = "\n".join(列[0] for 列 in 連線.execute("SELECT metadata_json FROM audit_events"))
    assert 敏感內容 not in 稽核文字 and 敏感路徑 not in 稽核文字


def test_客戶端owner與path欄位在任何v2副作用前拒絕(tmp_path: Path) -> None:
    """版本 body 不得成為 owner 或技能來源 locator authority。

    參數：``tmp_path`` 是 pytest 暫存根目錄。
    回傳：無。
    例外：未知敏感欄位未拒絕或產生 bundle 時斷言失敗。
    副作用：發布 v1，然後提交帶偽造 owner／path 的無效 v2 配置。
    """
    環境 = _建立環境(tmp_path)
    第一版 = _發布第一版(環境)
    偽造配置 = _第二版配置() | {
        "owner_user_id": "foreign-owner", "source_path": "/private/forged/SKILL.md",
    }
    結果 = 環境["服務"].原子建立並切換版本(
        擁有者使用者識別碼=環境["擁有者"], 端點識別碼=第一版.端點識別碼,
        配置=偽造配置,
    )
    assert 結果 == 管理操作錯誤("invalid")
    assert not (環境["套件根"] / "bundle-2").exists()
    assert not (環境["套件根"] / ".orphaned" / "bundle-2").exists()