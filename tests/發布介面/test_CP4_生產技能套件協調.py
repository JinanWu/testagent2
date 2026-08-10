"""驗證正式 Published Startup 在安裝 Runtime Handler 前完成技能套件協調。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import 繁中代理.發布介面.生產Published執行 as 生產組裝
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.設定 import 生產設定


def _建立測試設定(
    暫存路徑: Path,
    工具安裝器,
    模型工廠,
    *,
    孤兒保留秒數: float = 60.0,
) -> tuple[生產設定, 生產組裝.Published生產設定]:
    """建立具有獨立資料庫與既存技能套件根的正式設定。

    參數：
        暫存路徑: pytest 提供的案例隔離根目錄。
        工具安裝器: Runtime startup 的工具發布 callback。
        模型工廠: Runtime startup 的模型註冊表 callback。
        孤兒保留秒數: 傳給技能套件協調器的保存期限。
    返回值：
        CP3 Web 與 CP4 Published 的不可變設定二元組。
    例外：
        目錄建立或設定驗證失敗時原樣傳出。
    副作用：
        建立空的技能套件根；不建立資料庫或執行 callback。
    """
    技能套件根 = 暫存路徑 / "bundles"
    技能套件根.mkdir()
    網頁設定 = 生產設定(
        暫存路徑 / "web.sqlite3",
        ("https://client.example",),
        "fake",
        "fake",
        None,
        None,
    )
    發布設定 = 生產組裝.Published生產設定(
        暫存路徑 / "published.sqlite3",
        技能套件根,
        工具安裝器,
        模型工廠,
        孤兒保留秒數,
    )
    return 網頁設定, 發布設定


def test_啟動先提交協調再安裝執行期並關閉短連線(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """協調修復必須先提交，Runtime callbacks 才能取得 authority。

    參數：
        tmp_path: 提供隔離資料庫與技能套件根。
        monkeypatch: 以可觀測協調器替換正式檔案系統掃描。
    返回值：
        順序、提交與連線關閉皆符合契約時回傳 ``None``。
    例外：
        測試不預期例外；任何組裝失敗直接使案例失敗。
    副作用：
        建立 Published SQLite，寫入一筆協調標記並建立後關閉 Runtime 資源。
    """
    事件清單: list[str] = []
    連線清單: list[sqlite3.Connection] = []

    class 可觀測協調器:
        """記錄正式組裝傳入的根與保存期限，並寫入交易標記。"""

        def __init__(self, 根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """保存建構參數；不存取外部資源。"""
            assert 根目錄 == tmp_path / "bundles"
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, 現在: float, 資料庫: sqlite3.Connection):
            """寫入可觀測標記並保存短連線供關閉驗證。"""
            assert 現在 >= 0
            事件清單.append("協調")
            連線清單.append(資料庫)
            資料庫.execute("CREATE TABLE startup_reconciliation_marker(value TEXT)")
            資料庫.execute("INSERT INTO startup_reconciliation_marker VALUES('committed')")
            return object()

    monkeypatch.setattr(生產組裝, "技能套件協調器", 可觀測協調器)
    網頁設定, 發布設定 = _建立測試設定(
        tmp_path,
        lambda _工具庫: 事件清單.append("工具安裝"),
        lambda: 事件清單.append("模型安裝") or {"fake": object()},
    )
    代理 = 生產組裝.延遲外部呼叫編排器()

    資源 = 生產組裝._建立Published資源(網頁設定, 發布設定, 代理)

    assert 事件清單 == ["協調", "工具安裝", "模型安裝"]
    with sqlite3.connect(發布設定.發布資料庫路徑) as 驗證資料庫:
        assert 驗證資料庫.execute(
            "SELECT value FROM startup_reconciliation_marker"
        ).fetchone() == ("committed",)
    with pytest.raises(sqlite3.ProgrammingError):
        連線清單[0].execute("SELECT 1")
    asyncio.run(資源.關閉())


def test_協調失敗時不安裝執行期且代理維持不可用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任一 reconciliation failure 必須在工具、模型與 Handler 安裝前 fail closed。

    參數：
        tmp_path: 提供隔離資料庫與技能套件根。
        monkeypatch: 注入固定協調失敗。
    返回值：
        原錯誤、零 callback 與不可用代理皆成立時回傳 ``None``。
    例外：
        案例捕捉自行建立的 ``LookupError``；其他例外使測試失敗。
    副作用：
        初始化 Published DB，但不建立任何 Runtime authority。
    """
    事件清單: list[str] = []
    原始錯誤 = LookupError("reconciliation failed")

    class 失敗協調器:
        """在正式協調入口拋出同一個測試錯誤物件。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限後建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, _資料庫: sqlite3.Connection):
            """不寫入任何狀態並拋出固定錯誤。"""
            raise 原始錯誤

    monkeypatch.setattr(生產組裝, "技能套件協調器", 失敗協調器)
    網頁設定, 發布設定 = _建立測試設定(
        tmp_path,
        lambda _工具庫: 事件清單.append("工具安裝"),
        lambda: 事件清單.append("模型安裝") or {"fake": object()},
    )
    代理 = 生產組裝.延遲外部呼叫編排器()

    with pytest.raises(LookupError) as 捕捉:
        生產組裝._建立Published資源(網頁設定, 發布設定, 代理)

    assert 捕捉.value is 原始錯誤
    assert 事件清單 == []
    with pytest.raises(RuntimeError, match="^Published服務不可用$"):
        代理.執行("demo", "request-1", "key", {}, None, 1.0)


def test_協調失敗使正式應用啟動失敗且不執行執行期回呼(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical lifespan 必須把協調錯誤固定映射為不 Ready。

    參數：
        tmp_path: 提供隔離 Web／Published DB 與技能套件根。
        monkeypatch: 注入正式協調入口的一般失敗。
    返回值：
        App startup 固定失敗且 Runtime callbacks 未執行時回傳 ``None``。
    例外：
        捕捉 lifespan 公開的固定 ``RuntimeError``。
    副作用：
        建立並回滾一次失敗的 FastAPI lifespan，不接受任何 HTTP 請求。
    """
    事件清單: list[str] = []

    class 失敗協調器:
        """模擬無法證明 Bundle／DB 一致性的正式協調器。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限後建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, _資料庫: sqlite3.Connection):
            """固定拒絕啟動，不建立 Runtime authority。"""
            raise OSError("internal reconciliation detail")

    monkeypatch.setattr(生產組裝, "技能套件協調器", 失敗協調器)
    網頁設定, 發布設定 = _建立測試設定(
        tmp_path,
        lambda _工具庫: 事件清單.append("工具安裝"),
        lambda: 事件清單.append("模型安裝") or {"fake": object()},
    )
    應用程式 = 建立CP4ASGI應用程式(網頁設定, 發布設定)

    with pytest.raises(RuntimeError, match="^發布介面啟動失敗$") as 捕捉:
        with TestClient(應用程式):
            raise AssertionError("協調失敗的應用程式不得 Ready")

    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    assert 事件清單 == []


def test_真實啟動在commit確認遺失後補寫收據且重新啟動保持冪等(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正式 Startup 應提交 receipt repair，下一次啟動不得重複新增。

    參數：
        tmp_path: 提供真實技能來源、Bundle、Published DB 與 Web DB 路徑。
        monkeypatch: 令第一次提交成功後回報 acknowledgment 遺失。
    返回值：
        首次補據、重新啟動及關閉皆成功且資料列唯一時回傳 ``None``。
    例外：
        發布、資料庫、組裝或關閉失敗時原樣傳出並使案例失敗。
    副作用：
        建立真實不可變 Bundle、缺 Receipt 的 Version，並執行兩次 Published startup。
    """
    技能來源 = tmp_path / "source"
    技能來源.mkdir()
    (技能來源 / "SKILL.md").write_text("# startup reconciliation", encoding="utf-8")
    技能套件根 = tmp_path / "bundles"
    技能套件發布器(技能套件根).發布(
        套件識別碼="bundle-1",
        端點識別碼="endpoint-1",
        端點版本識別碼="version-1",
        版本號碼=1,
        建立時間=1.0,
        建立者識別碼="owner-1",
        技能表={"startup": 技能來源},
    )
    發布資料庫 = tmp_path / "published.sqlite3"
    初始化發布介面資料庫(發布資料庫)
    with sqlite3.connect(發布資料庫) as 資料庫:
        資料庫.execute("INSERT INTO service_accounts VALUES('service-1',1,NULL)")
        資料庫.execute(
            "INSERT INTO published_endpoints("
            "id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,"
            "rate_limit_requests,rate_limit_window_seconds) "
            "VALUES('endpoint-1','owner-1','service-1','demo','active',NULL,1,1,60,60)"
        )
        資料庫.execute(
            "INSERT INTO published_endpoint_versions VALUES("
            "'version-1','endpoint-1',1,'requirement','prompt','[]','[]','{}','release-1',"
            "'{}','{}','{}',NULL,'{}',0,'owner-1',1)"
        )
    網頁設定 = 生產設定(
        tmp_path / "web.sqlite3",
        ("https://client.example",),
        "fake",
        "fake",
        None,
        None,
    )
    發布設定 = 生產組裝.Published生產設定(
        發布資料庫,
        技能套件根,
        lambda _工具庫: None,
        lambda: {"fake": object()},
        60.0,
    )
    提交次數 = 0

    def 第一次提交後遺失確認(資料庫: sqlite3.Connection) -> None:
        """第一次先真實提交再拋錯，後續提交正常完成。"""
        nonlocal 提交次數
        提交次數 += 1
        sqlite3.Connection.commit(資料庫)
        if 提交次數 == 1:
            raise sqlite3.OperationalError("real coordinator commit acknowledgment lost")

    monkeypatch.setattr(生產組裝, "_提交協調資料庫", 第一次提交後遺失確認)

    for _啟動次數 in range(2):
        代理 = 生產組裝.延遲外部呼叫編排器()
        資源 = 生產組裝._建立Published資源(網頁設定, 發布設定, 代理)
        asyncio.run(資源.關閉())

    with sqlite3.connect(發布資料庫) as 驗證資料庫:
        收據資料列 = 驗證資料庫.execute(
            "SELECT version_id,bundle_id,state,reconciled_at FROM published_skill_bundles"
        ).fetchall()
    assert len(收據資料列) == 1
    assert 提交次數 == 3
    assert 收據資料列[0][:3] == ("version-1", "bundle-1", "reconciled")
    assert type(收據資料列[0][3]) is float and 收據資料列[0][3] > 0


def test_敵對rollback與close覆寫不會攔截基底清理或留下寫入鎖(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """協調 body 失敗後必須保留原錯誤並以基底 primitive 釋放 writer authority。

    參數：
        tmp_path: 提供真實 Published DB 與空技能套件根。
        monkeypatch: 令 connect 回傳覆寫 rollback／close 的敵對連線 subclass。
    返回值：
        原錯誤 identity、覆寫零呼叫及第二 writer 成功時回傳 ``None``。
    例外：
        捕捉測試注入的 ``LookupError``；其他例外使案例失敗。
    副作用：
        開啟真實寫入交易後失敗，再由第二條連線取得並釋放寫鎖。
    """
    _, 發布設定 = _建立測試設定(tmp_path, lambda _工具庫: None, lambda: {"fake": object()})
    初始化發布介面資料庫(發布設定.發布資料庫路徑)
    真實連線工廠 = 生產組裝.sqlite3.connect
    覆寫呼叫 = {"rollback": 0, "close": 0}
    原始錯誤 = LookupError("body failed")

    class 敵對連線(sqlite3.Connection):
        """若 production 動態委派 cleanup，便拒絕釋放 transaction authority。"""

        def rollback(self) -> None:
            """記錄並拒絕動態 rollback；基底 primitive 不會呼叫本方法。"""
            覆寫呼叫["rollback"] += 1
            raise OSError("hostile rollback")

        def close(self) -> None:
            """記錄並拒絕動態 close；基底 primitive 不會呼叫本方法。"""
            覆寫呼叫["close"] += 1
            raise OSError("hostile close")

    class 寫入後失敗協調器:
        """持有真實 IMMEDIATE writer lock 後拋出原始錯誤。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限並建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, 資料庫: sqlite3.Connection):
            """建立 writer transaction、修改資料後拋出固定錯誤。"""
            資料庫.execute("BEGIN IMMEDIATE")
            資料庫.execute("UPDATE service_accounts SET disabled_at=disabled_at WHERE id='missing'")
            raise 原始錯誤

    monkeypatch.setattr(生產組裝, "技能套件協調器", 寫入後失敗協調器)
    monkeypatch.setattr(
        生產組裝,
        "_回滾協調資料庫",
        lambda _資料庫: (_ for _ in ()).throw(OSError("rollback primitive failed")),
    )
    monkeypatch.setattr(
        生產組裝.sqlite3,
        "connect",
        lambda 路徑: 真實連線工廠(路徑, factory=敵對連線),
    )

    with pytest.raises(LookupError) as 捕捉:
        生產組裝._執行技能套件啟動協調(發布設定)

    assert 捕捉.value is 原始錯誤 and 捕捉.value.args == ("body failed",)
    assert 覆寫呼叫 == {"rollback": 0, "close": 0}
    with 真實連線工廠(發布設定.發布資料庫路徑, timeout=0) as 第二寫入者:
        第二寫入者.execute("BEGIN IMMEDIATE")
        第二寫入者.rollback()


def test_commit確認遺失會用新連線重驗並保持單一耐久結果(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第一次 commit 已完成但回報錯誤時，第二次冪等協調應驗證 durable state。

    參數：
        tmp_path: 提供真實 Published DB 與空技能套件根。
        monkeypatch: 令第一次提交先成功再拋出 acknowledgment error。
    返回值：
        第二次協調成功且資料列保持唯一時回傳 ``None``。
    例外：
        若重驗、提交或資料一致性失敗，例外原樣使案例失敗。
    副作用：
        以兩條短連線執行兩次協調，第一次實際提交一筆測試修復資料。
    """
    _, 發布設定 = _建立測試設定(tmp_path, lambda _工具庫: None, lambda: {"fake": object()})
    初始化發布介面資料庫(發布設定.發布資料庫路徑)
    協調次數 = 0
    提交次數 = 0

    class 冪等協調器:
        """以唯一鍵模擬可安全重入的 canonical receipt repair。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限並建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, 資料庫: sqlite3.Connection):
            """建立唯一修復列；重跑時不重複新增。"""
            nonlocal 協調次數
            協調次數 += 1
            資料庫.execute("CREATE TABLE IF NOT EXISTS reconciliation_probe(id TEXT PRIMARY KEY)")
            資料庫.execute("INSERT OR IGNORE INTO reconciliation_probe VALUES('repair-1')")
            return object()

    def 第一次提交後遺失確認(資料庫: sqlite3.Connection) -> None:
        """第一次先真實提交再回報錯誤，第二次正常提交。"""
        nonlocal 提交次數
        提交次數 += 1
        sqlite3.Connection.commit(資料庫)
        if 提交次數 == 1:
            raise sqlite3.OperationalError("commit acknowledgment lost")

    monkeypatch.setattr(生產組裝, "技能套件協調器", 冪等協調器)
    monkeypatch.setattr(生產組裝, "_提交協調資料庫", 第一次提交後遺失確認)

    生產組裝._執行技能套件啟動協調(發布設定)

    assert 協調次數 == 2 and 提交次數 == 2
    with sqlite3.connect(發布設定.發布資料庫路徑) as 驗證資料庫:
        assert 驗證資料庫.execute("SELECT id FROM reconciliation_probe").fetchall() == [("repair-1",)]


def test_控制流程例外經基底回滾關閉後保持identity且不留寫入鎖(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """協調控制例外必須保持同一物件，同時讓第二 writer 立即取得 authority。

    參數：
        tmp_path: 提供真實 Published DB 與空技能套件根。
        monkeypatch: 注入持鎖後拋出 ``KeyboardInterrupt`` 的協調器。
    返回值：
        identity／args／例外鏈與 writer lock 釋放皆成立時回傳 ``None``。
    例外：
        捕捉測試建立的控制例外；其他例外使案例失敗。
    副作用：
        開啟並回滾一個真實 IMMEDIATE transaction。
    """
    _, 發布設定 = _建立測試設定(tmp_path, lambda _工具庫: None, lambda: {"fake": object()})
    初始化發布介面資料庫(發布設定.發布資料庫路徑)
    原始控制 = KeyboardInterrupt("startup interrupted", 47)

    class 控制失敗協調器:
        """取得真實 writer lock 後拋出同一個控制例外物件。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限並建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, 資料庫: sqlite3.Connection):
            """建立 writer transaction 後拋出同一個控制例外。"""
            資料庫.execute("BEGIN IMMEDIATE")
            raise 原始控制

    monkeypatch.setattr(生產組裝, "技能套件協調器", 控制失敗協調器)
    monkeypatch.setattr(
        生產組裝,
        "_回滾協調資料庫",
        lambda _資料庫: (_ for _ in ()).throw(OSError("control rollback failed")),
    )

    with pytest.raises(KeyboardInterrupt) as 捕捉:
        生產組裝._執行技能套件啟動協調(發布設定)

    assert 捕捉.value is 原始控制 and 捕捉.value.args == ("startup interrupted", 47)
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    with sqlite3.connect(發布設定.發布資料庫路徑, timeout=0) as 第二寫入者:
        第二寫入者.execute("BEGIN IMMEDIATE")
        第二寫入者.rollback()


@pytest.mark.parametrize(
    "原始錯誤",
    (
        LookupError("ordinary primary", 11),
        KeyboardInterrupt("keyboard primary", 13),
        SystemExit("system primary", 17),
        GeneratorExit("generator primary", 19),
    ),
    ids=("ordinary", "keyboard", "system-exit", "generator-exit"),
)
def test_close釋放後失敗不覆蓋主要例外且第二writer可立即取得鎖(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    原始錯誤: BaseException,
) -> None:
    """close 已釋放 authority 後的次要錯誤不得覆蓋 ordinary／control primary。

    參數：
        tmp_path: 提供真實 Published DB 與空技能套件根。
        monkeypatch: 注入持鎖主要失敗，以及先關閉再拋錯的 cleanup seam。
        原始錯誤: 依序覆蓋普通錯誤與三種控制流程例外。
    返回值：
        identity、args、例外鏈及第二 writer authority 全部成立時回傳 ``None``。
    例外：
        捕捉參數指定的同一主要例外；其他例外使案例失敗。
    副作用：
        建立真實 IMMEDIATE transaction，關閉短連線後由第二 writer 立即取鎖。
    """
    _, 發布設定 = _建立測試設定(tmp_path, lambda _工具庫: None, lambda: {"fake": object()})
    初始化發布介面資料庫(發布設定.發布資料庫路徑)
    真實關閉 = 生產組裝._關閉協調資料庫

    class 持鎖失敗協調器:
        """取得真實 writer lock 後拋出測試指定的主要例外。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限並建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, 資料庫: sqlite3.Connection):
            """建立 writer transaction 後拋出同一個主要例外物件。"""
            資料庫.execute("BEGIN IMMEDIATE")
            資料庫.execute("UPDATE service_accounts SET disabled_at=disabled_at WHERE id='missing'")
            raise 原始錯誤

    def 關閉後回報次要失敗(資料庫: sqlite3.Connection) -> None:
        """先透過 production 基底 primitive 真實關閉，再模擬 cleanup acknowledgment 失敗。"""
        真實關閉(資料庫)
        raise OSError("close cleanup failed after release")

    monkeypatch.setattr(生產組裝, "技能套件協調器", 持鎖失敗協調器)
    monkeypatch.setattr(生產組裝, "_關閉協調資料庫", 關閉後回報次要失敗)

    with pytest.raises(type(原始錯誤)) as 捕捉:
        生產組裝._執行技能套件啟動協調(發布設定)

    assert 捕捉.value is 原始錯誤 and 捕捉.value.args == 原始錯誤.args
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    with sqlite3.connect(發布設定.發布資料庫路徑, timeout=0) as 第二寫入者:
        第二寫入者.execute("BEGIN IMMEDIATE")
        第二寫入者.rollback()


def test_成功協調後close失敗仍使startup關閉失敗(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒有主要錯誤時，close cleanup 失敗不得被靜默忽略。

    參數：
        tmp_path: 提供真實 Published DB 與空技能套件根。
        monkeypatch: 令 close 先真實釋放連線後再拋固定錯誤。
    返回值：
        Startup 固定傳出 cleanup 錯誤後回傳 ``None``。
    例外：
        捕捉預期的 ``OSError``；錯誤遭抑制或被轉換時使案例失敗。
    副作用：
        執行一次無資料變更的協調與真實連線關閉。
    """
    _, 發布設定 = _建立測試設定(tmp_path, lambda _工具庫: None, lambda: {"fake": object()})
    初始化發布介面資料庫(發布設定.發布資料庫路徑)
    真實關閉 = 生產組裝._關閉協調資料庫

    class 成功協調器:
        """回傳成功結果且不建立額外交易狀態。"""

        def __init__(self, _根目錄: Path, *, 孤兒保留秒數: float) -> None:
            """驗證保存期限並建立無狀態測試協調器。"""
            assert 孤兒保留秒數 == 60.0

        def 啟動協調(self, _現在: float, _資料庫: sqlite3.Connection):
            """不寫入資料並回傳成功結果。"""
            return object()

    def 關閉後回報失敗(資料庫: sqlite3.Connection) -> None:
        """先真實關閉短連線，再回報固定 cleanup failure。"""
        真實關閉(資料庫)
        raise OSError("successful startup close failed")

    monkeypatch.setattr(生產組裝, "技能套件協調器", 成功協調器)
    monkeypatch.setattr(生產組裝, "_關閉協調資料庫", 關閉後回報失敗)

    with pytest.raises(OSError, match="^successful startup close failed$"):
        生產組裝._執行技能套件啟動協調(發布設定)


@pytest.mark.parametrize(
    "孤兒保留秒數",
    (-1.0, float("nan"), float("inf"), "60", False, 10**10_000, -(10**10_000)),
    ids=("negative", "nan", "infinity", "string", "boolean", "huge-positive", "huge-negative"),
)
def test_發布設定拒絕不安全孤兒保存期限(
    tmp_path: Path,
    孤兒保留秒數: object,
) -> None:
    """保存期限只接受有限且非負的整數或浮點秒數。

    參數：
        tmp_path: 提供合法絕對路徑。
        孤兒保留秒數: 本案例注入的非法外部設定值。
    返回值：
        設定固定拒絕後回傳 ``None``。
    例外：
        捕捉預期的固定 ``ValueError``。
    副作用：
        無；只建構不可變設定，不建立檔案。
    """
    with pytest.raises(ValueError, match="^Published生產設定無效$"):
        生產組裝.Published生產設定(
            tmp_path / "published.sqlite3",
            tmp_path / "bundles",
            lambda _工具庫: None,
            lambda: {"fake": object()},
            孤兒保留秒數,  # type: ignore[arg-type]
        )
