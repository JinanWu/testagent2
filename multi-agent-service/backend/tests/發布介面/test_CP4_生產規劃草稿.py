"""A3-02 Production Planner Resource 的生命週期與組裝因果測試。

用途：驗證 per-app Lazy Draft Proxy、共用工具發布庫、唯一草稿 Aggregate 與失敗清理。
參數：測試透過 pytest fixture 注入暫存目錄，沒有公開參數。
返回值：無；斷言失敗時由 pytest 回報。
例外：預期的生命週期拒絕以 ``pytest.raises`` 驗證。
副作用：部分測試只在 pytest 暫存目錄建立 SQLite 與技能檔案。
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.工具 import 工具定義
import 繁中代理.發布介面.生產Published管理 as 生產Published管理模組
import 繁中代理.發布介面.生產Published執行 as 生產Published執行模組
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.生產Published執行 import (
    Published生產設定,
    延遲外部呼叫編排器,
    生產Published執行資源,
    生產Published執行建構器,
    _建立Published資源,
)
from 繁中代理.發布介面.生產Published管理 import (
    Planner生產設定,
    延遲草稿規劃服務,
    草稿規劃服務不可用,
    建立生產Planner資源,
)
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.規劃.規劃器服務 import 伺服器端草稿規劃服務
from 繁中代理.發布介面.規劃.綱要 import 規劃服務


class _固定使用者來源:
    """回傳單一權威使用者上下文且不執行額外 I/O。

    參數：建構時接收完整使用者上下文。
    返回值：``建立使用者上下文`` 回傳該上下文。
    例外：要求其他使用者時拋 ``ValueError``。
    副作用：只累計查詢次數。
    """

    def __init__(self, 上下文: 使用者上下文) -> None:
        """保存測試權威上下文。

        參數：``上下文`` 為預期使用者。
        返回值：None。
        例外：無。
        副作用：初始化查詢計數。
        """
        self.上下文 = 上下文
        self.查詢次數 = 0

    def 建立使用者上下文(self, user_id: str | None = None) -> 使用者上下文:
        """依 exact 使用者識別回傳權威上下文。

        參數：``user_id`` 為待查使用者識別。
        返回值：保存的使用者上下文。
        例外：識別不一致時拋 ``ValueError``。
        副作用：成功前先累計一次查詢。
        """
        self.查詢次數 += 1
        if user_id != self.上下文.user_id:
            raise ValueError("未知使用者")
        return self.上下文


class _空權限查詢:
    """提供只供 proxy lifecycle 測試使用的不可呼叫權限查詢。

    參數：無。
    返回值：無正常查詢結果。
    例外：查詢時固定拋 ``AssertionError``。
    副作用：無。
    """

    def 查詢規劃權限(self, 擁有者識別碼: str, /):
        """拒絕非預期的權限查詢。

        參數：``擁有者識別碼`` 不使用。
        返回值：不會正常回傳。
        例外：固定拋 ``AssertionError``。
        副作用：無。
        """
        raise AssertionError(擁有者識別碼)


def _建立服務() -> 伺服器端草稿規劃服務:
    """建立不觸發 I/O 的 exact 草稿規劃服務。

    參數：無。
    返回值：使用假規劃器與單一 Aggregate 的伺服器端服務。
    例外：底層建構錯誤原樣傳出。
    副作用：只配置記憶體物件。
    """
    return 伺服器端草稿規劃服務(_空權限查詢(), 決定性假規劃器(), 草稿服務=規劃服務())


def _登錄測試工具(發布庫: 工具發布庫) -> None:
    """在指定發布庫安裝一個 exact 測試 release。

    參數：``發布庫`` 為 startup 共用工具發布庫。
    返回值：None。
    例外：重複發布或契約錯誤原樣傳出。
    副作用：在行程內發布表登錄 ``release-1``。
    """
    發布庫.登錄發布(工具發布描述("release-1", (工具發布註冊(
        "rev-1",
        工具定義(
            "lookup", "查詢資料",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda 參數: {"ok": True},
        ),
    ),)))


def _建立Planner設定(來源工廠, 規劃器工廠=lambda: 決定性假規劃器()) -> Planner生產設定:
    """建立測試使用的 explicit Planner 生產設定。

    參數：``來源工廠`` 建立權威來源；``規劃器工廠`` 建立 Fake 或 Gemini planner。
    返回值：TTL 為一小時且釘選 ``release-1`` 的設定。
    例外：設定驗證錯誤原樣傳出。
    副作用：只保存 callbacks，不呼叫它們。
    """
    return Planner生產設定("release-1", 來源工廠, 規劃器工廠, 草稿存續秒數=3600.0)


def test_建構零IO且啟動前與關閉後fail_closed(tmp_path: Path) -> None:
    """A3-02-01：建構不呼叫工廠，proxy 在 authority window 外固定拒絕。

    參數：``tmp_path`` 提供尚不存在的 Web／Published 路徑。
    返回值：None。
    例外：服務可用性錯誤為預期結果。
    副作用：只修改本測試記憶體清單。
    """
    呼叫: list[str] = []
    設定 = _建立Planner設定(
        lambda _: 呼叫.append("authority") or _固定使用者來源(使用者上下文()),
        lambda: 呼叫.append("planner") or 決定性假規劃器(),
    )
    代理 = 延遲草稿規劃服務()
    assert 呼叫 == [] and 設定.草稿存續秒數 == 3600.0
    生產 = 生產設定(tmp_path / "web.sqlite3", ("https://client.example",), "fake", "fake", None, None)
    發布 = Published生產設定(
        tmp_path / "published.sqlite3", tmp_path / "missing-bundles",
        lambda _: 呼叫.append("installer"), lambda: 呼叫.append("models") or {"fake": object()},
        Planner設定=設定,
    )
    建構器 = 生產Published執行建構器(發布)
    建構器.建立附加相依項(生產, lambda: None, lambda: None)
    assert 呼叫 == []
    assert not 生產.資料庫路徑.exists() and not 發布.發布資料庫路徑.exists()
    assert isinstance(建構器.取得草稿規劃代理(), 延遲草稿規劃服務)
    with pytest.raises(草稿規劃服務不可用, match="草稿規劃服務不可用"):
        代理.建立草稿("owner", "需求", ("skill",), "text", 現在=1.0)
    服務 = _建立服務()
    代理.安裝(服務)
    代理.清除(服務)
    with pytest.raises(草稿規劃服務不可用, match="草稿規劃服務不可用"):
        代理.建立草稿("owner", "需求", ("skill",), "text", 現在=1.0)
    assert 呼叫 == []


def test_double_install與wrong_clear固定拒絕() -> None:
    """A3-02-02：exact-once slot 不接受第二服務或錯誤 identity 清除。

    參數：無。
    返回值：None。
    例外：兩個違約操作預期拋 ``ValueError``。
    副作用：安裝後再正確清除第一個服務。
    """
    代理, 第一服務, 第二服務 = 延遲草稿規劃服務(), _建立服務(), _建立服務()
    代理.安裝(第一服務)
    with pytest.raises(ValueError, match="草稿規劃生產組裝無效"):
        代理.安裝(第二服務)
    with pytest.raises(ValueError, match="草稿規劃生產組裝無效"):
        代理.清除(第二服務)
    代理.清除(第一服務)


def test_shutdown先拒絕新租借並等待active_lease_drain() -> None:
    """A3-02-03：shutdown 清 slot 後等待既有完整委派結束。

    參數：無。
    返回值：None。
    例外：新呼叫在 drain 期間預期失敗。
    副作用：啟動兩條短生命週期測試執行緒。
    """
    代理, 服務 = 延遲草稿規劃服務(), _建立服務()
    已進入, 可返回, 已清除 = threading.Event(), threading.Event(), threading.Event()

    def 阻塞建立(*參數, **關鍵字):
        """持有 proxy lease 直到測試允許返回。

        參數：位置與關鍵字參數由 proxy 委派但不使用。
        返回值：固定字串 ``done``。
        例外：等待逾時時拋 ``AssertionError``。
        副作用：設定同步事件並阻塞目前執行緒。
        """
        del 參數, 關鍵字
        已進入.set()
        assert 可返回.wait(2)
        return "done"

    服務.建立草稿 = 阻塞建立  # type: ignore[method-assign]
    代理.安裝(服務)
    結果: list[str] = []
    工作者 = threading.Thread(target=lambda: 結果.append(代理.建立草稿("o", "r", (), "text", 現在=1.0)))
    工作者.start()
    assert 已進入.wait(1)
    清除者 = threading.Thread(target=lambda: (代理.清除(服務), 已清除.set()))
    清除者.start()
    for _ in range(100):
        try:
            代理.建立草稿("o", "r", (), "text", 現在=1.0)
        except 草稿規劃服務不可用:
            break
        time.sleep(0.005)
    else:
        raise AssertionError("drain 未停止新租借")
    assert not 已清除.is_set()
    可返回.set()
    工作者.join(2)
    清除者.join(2)
    assert 已清除.is_set() and 結果 == ["done"]


def test_fake_factory建立唯一aggregate且輸出決定性(tmp_path: Path) -> None:
    """A3-02-04：startup 組裝唯一 Aggregate，Fake planner 預覽可重現。

    參數：``tmp_path`` 提供一個權威技能根。
    返回值：None。
    例外：組裝或規劃失敗會使測試失敗。
    副作用：在暫存技能根寫入一個 ``SKILL.md``。
    """
    技能根 = tmp_path / "skills"
    技能目錄 = 技能根 / "order-query"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text("# Order Query\n查詢訂單。", encoding="utf-8")
    來源 = _固定使用者來源(使用者上下文(
        user_id="owner-1", username="owner", roles=["user"], enabled_tools={"lookup"},
        enabled_skills={"order-query"}, skill_roots=[技能根], is_admin=False,
    ))
    發布庫 = 工具發布庫()
    _登錄測試工具(發布庫)
    代理 = 延遲草稿規劃服務()
    資源 = 建立生產Planner資源(_建立Planner設定(lambda _: 來源), tmp_path / "web.sqlite3", 發布庫, 代理)
    assert 資源.取得規劃服務() is 資源.取得規劃服務()
    assert 資源.取得工具發布庫() is 發布庫
    第一 = 代理.建立草稿("owner-1", "建立訂單查詢 API", ("order-query",), "structured", 現在=10.0)
    第二 = 代理.建立草稿("owner-1", "建立訂單查詢 API", ("order-query",), "structured", 現在=11.0)
    assert 第一.綱要 == 第二.綱要
    assert 第一.到期時間 - 第一.建立時間 == 3600.0
    asyncio.run(資源.關閉())
    with pytest.raises(草稿規劃服務不可用):
        代理.建立草稿("owner-1", "建立訂單查詢 API", ("order-query",), "structured", 現在=12.0)
    assert 資源.取得規劃服務() is None


def test_planner_factory_failure不留下部分authority(tmp_path: Path) -> None:
    """A3-02-05：planner factory ordinary failure 不安裝 proxy 或保存 authority。

    參數：``tmp_path`` 提供不會被讀取的 Web DB 路徑。
    返回值：None。
    例外：factory 的 ``LookupError`` 必須保持原型別傳出。
    副作用：只記錄 authority factory 被呼叫一次。
    """
    呼叫: list[str] = []
    來源 = _固定使用者來源(使用者上下文(user_id="owner-1"))

    def 規劃器失敗():
        """注入 ordinary planner factory failure。

        參數：無。
        返回值：不會正常回傳。
        例外：固定拋 ``LookupError``。
        副作用：無。
        """
        raise LookupError("provider setup failed")

    設定 = _建立Planner設定(lambda _: 呼叫.append("authority") or 來源, 規劃器失敗)
    發布庫, 代理 = 工具發布庫(), 延遲草稿規劃服務()
    _登錄測試工具(發布庫)
    with pytest.raises(LookupError, match="provider setup failed"):
        建立生產Planner資源(設定, tmp_path / "web.sqlite3", 發布庫, 代理)
    assert 呼叫 == ["authority"]
    with pytest.raises(草稿規劃服務不可用):
        代理.建立草稿("owner-1", "需求", ("skill",), "text", 現在=1.0)


def test_Published_startup共用工具庫且installer只執行一次(tmp_path: Path) -> None:
    """A3-02-06：Planner 使用 Published startup 的同一工具發布庫且不重跑 installer。

    參數：``tmp_path`` 提供隔離 Web／Published DB 與 bundle 根。
    返回值：None。
    例外：正式 startup 組裝失敗會使測試失敗。
    副作用：建立暫存 SQLite schema，結束時關閉完整 Published 資源。
    """
    Web資料庫, 發布資料庫, 套件根 = tmp_path / "web.sqlite3", tmp_path / "published.sqlite3", tmp_path / "bundles"
    套件根.mkdir()
    安裝庫: list[工具發布庫] = []

    def 安裝器(發布庫: 工具發布庫) -> None:
        """記錄並安裝唯一 release。

        參數：``發布庫`` 是 Published startup 建立的 registry。
        返回值：None。
        例外：工具登錄錯誤原樣傳出。
        副作用：登錄 release 並保存 identity 供斷言。
        """
        安裝庫.append(發布庫)
        _登錄測試工具(發布庫)

    生產 = 生產設定(Web資料庫, ("https://client.example",), "fake", "fake", None, None)
    Planner設定 = _建立Planner設定(lambda _: _固定使用者來源(使用者上下文(user_id="owner-1")))
    發布 = Published生產設定(
        發布資料庫, 套件根, 安裝器, lambda: {"fake": object()},
        Planner設定=Planner設定,
    )
    草稿代理 = 延遲草稿規劃服務()
    資源 = _建立Published資源(生產, 發布, 延遲外部呼叫編排器(), 草稿代理)
    assert 安裝庫 == [資源._工具庫]
    assert 資源.取得Planner資源().取得工具發布庫() is 安裝庫[0]
    assert 資源.取得Planner資源().取得規劃服務() is 資源.取得規劃服務()
    asyncio.run(資源.關閉())
    assert 資源.取得Planner資源() is None


def test_builder實際startup_closure安裝同一個per_app_proxy(tmp_path: Path) -> None:
    """A3-02-07：builder resource factory 必須安裝先前公開的同一個 proxy identity。

    參數：``tmp_path`` 提供隔離 Web／Published DB、bundle 與技能根。
    返回值：None。
    例外：正式 builder wiring、規劃或關閉失敗會使測試失敗。
    副作用：建立暫存 SQLite schema、技能檔案並啟停完整 Published resource。
    """
    Web資料庫 = tmp_path / "web.sqlite3"
    發布資料庫 = tmp_path / "published.sqlite3"
    套件根 = tmp_path / "bundles"
    套件根.mkdir()
    技能根 = tmp_path / "skills"
    技能目錄 = 技能根 / "order-query"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text("# Order Query\n查詢訂單。", encoding="utf-8")
    使用者來源 = _固定使用者來源(使用者上下文(
        user_id="owner-1", username="owner", roles=["user"], enabled_tools={"lookup"},
        enabled_skills={"order-query"}, skill_roots=[技能根], is_admin=False,
    ))
    生產 = 生產設定(Web資料庫, ("https://client.example",), "fake", "fake", None, None)
    發布 = Published生產設定(
        發布資料庫, 套件根, _登錄測試工具, lambda: {"fake": object()},
        Planner設定=_建立Planner設定(lambda _: 使用者來源),
    )
    建構器 = 生產Published執行建構器(發布)
    相依項 = 建構器.建立附加相依項(生產, lambda: None, lambda: None)
    草稿代理 = 建構器.取得草稿規劃代理()
    with pytest.raises(草稿規劃服務不可用):
        草稿代理.建立草稿("owner-1", "需求", ("order-query",), "text", 現在=1.0)

    async def 啟動資源():
        """等待 builder 的真實 async resource factory。

        參數：無。
        返回值：startup 完成的 Published lifespan resource。
        例外：factory 的 startup 例外原樣傳出。
        副作用：建立 SQLite schema、Published 與 Planner resources。
        """
        return await 相依項.資源工廠清單[0]()

    資源 = asyncio.run(啟動資源())
    assert isinstance(資源, 生產Published執行資源)
    草稿 = 草稿代理.建立草稿(
        "owner-1", "建立訂單查詢 API", ("order-query",), "structured", 現在=10.0,
    )
    assert 草稿.擁有者識別碼 == "owner-1"
    Planner資源 = 資源.取得Planner資源()
    assert Planner資源 is not None
    assert 資源.取得規劃服務() is Planner資源.取得規劃服務()

    async def 關閉資源() -> None:
        """等待完整 Published 與 Planner shutdown。

        參數：無。
        返回值：None。
        例外：shutdown 例外原樣傳出。
        副作用：撤銷兩個 proxy 並釋放模型、工具及 Planner authority。
        """
        await 資源.關閉()

    asyncio.run(關閉資源())
    with pytest.raises(草稿規劃服務不可用):
        草稿代理.建立草稿("owner-1", "需求", ("order-query",), "text", 現在=11.0)


def test_shutdown_clear_failure仍撤銷authority(tmp_path: Path) -> None:
    """A3-02-08：公開 clear failure 後仍撤銷 proxy 並清除資源參照。

    參數：``tmp_path`` 提供不會被權威查詢的 Web DB 路徑。
    返回值：None。
    例外：注入的 ``RuntimeError`` 必須在完整清理後傳出。
    副作用：建立並清除一個記憶體 Planner resource。
    """
    發布庫, 代理 = 工具發布庫(), 延遲草稿規劃服務()
    _登錄測試工具(發布庫)
    資源 = 建立生產Planner資源(
        _建立Planner設定(lambda _: _固定使用者來源(使用者上下文(user_id="owner-1"))),
        tmp_path / "web.sqlite3", 發布庫, 代理,
    )

    def 清除失敗(服務) -> None:
        """在未撤銷 slot 前注入 shutdown ordinary failure。

        參數：``服務`` 為資源要求清除的 exact 服務但刻意不使用。
        返回值：不會正常回傳。
        例外：固定拋 ``RuntimeError``。
        副作用：無。
        """
        del 服務
        raise RuntimeError("clear failed")

    代理.清除 = 清除失敗  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="clear failed"):
        asyncio.run(資源.關閉())
    assert 資源.取得規劃服務() is None
    with pytest.raises(草稿規劃服務不可用):
        代理.建立草稿("owner-1", "需求", ("skill",), "text", 現在=1.0)


def test_TTL不得超過二十四小時() -> None:
    """A3-02-08：production setting 拒絕超過 24 小時的 ephemeral draft TTL。

    參數：無。
    返回值：None。
    例外：超限設定預期拋 ``ValueError``。
    副作用：不得呼叫任一 factory。
    """
    with pytest.raises(ValueError, match="Planner生產設定無效"):
        Planner生產設定(
            "release-1", lambda _: _固定使用者來源(使用者上下文()), lambda: 決定性假規劃器(),
            草稿存續秒數=86400.1,
        )


def test_超大整數TTL固定拒絕而不洩漏OverflowError() -> None:
    """A3-02-09：敵對精確整數先依上限拒絕，不轉換成浮點造成額外例外。

    參數：無。
    返回值：None。
    例外：超大整數設定預期固定拋 ``ValueError``。
    副作用：不得呼叫任一 factory。
    """
    with pytest.raises(ValueError, match="Planner生產設定無效"):
        Planner生產設定(
            "release-1", lambda _: _固定使用者來源(使用者上下文()), lambda: 決定性假規劃器(),
            草稿存續秒數=10 ** 10_000,
        )


@pytest.mark.parametrize("控制流程類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_Planner關閉普通錯誤後仍優先保留清理控制流程(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, 控制流程類型: type[BaseException],
) -> None:
    """A3-02-10：清理控制流程不可被較早的 ordinary proxy failure 吞掉。

    參數：暫存路徑、pytest monkeypatch 與待驗證控制流程型別。
    返回值：None。
    例外：預期傳出注入的 exact 控制流程物件。
    副作用：建立並完整撤銷一個記憶體 Planner resource。
    """
    發布庫, 代理 = 工具發布庫(), 延遲草稿規劃服務()
    _登錄測試工具(發布庫)
    資源 = 建立生產Planner資源(
        _建立Planner設定(lambda _: _固定使用者來源(使用者上下文(user_id="owner-1"))),
        tmp_path / "web.sqlite3", 發布庫, 代理,
    )
    控制流程 = 控制流程類型("STOP", "cleanup")

    def 清除失敗(服務) -> None:
        """以 ordinary failure 模擬公開 proxy clear。

        參數：``服務`` 是待清除的服務但刻意不使用。
        返回值：不會正常回傳。
        例外：固定拋 ``RuntimeError``。
        副作用：無。
        """
        del 服務
        raise RuntimeError("clear failed")

    def 關閉中斷(來源) -> None:
        """在後續 authority cleanup 傳出 exact 控制流程。

        參數：``來源`` 是待關閉的權威來源但刻意不使用。
        返回值：不會正常回傳。
        例外：傳出測試建立的 exact 控制流程物件。
        副作用：無。
        """
        del 來源
        raise 控制流程

    monkeypatch.setattr(代理, "清除", 清除失敗)
    monkeypatch.setattr(生產Published管理模組, "_關閉自有使用者來源", 關閉中斷)
    with pytest.raises(控制流程類型) as 捕捉:
        資源._清除同步()
    assert 捕捉.value is 控制流程 and 捕捉.value.args == ("STOP", "cleanup")
    assert 資源.取得規劃服務() is None
    with pytest.raises(草稿規劃服務不可用):
        代理.建立草稿("owner-1", "需求", ("skill",), "text", 現在=1.0)


@pytest.mark.parametrize("控制流程類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_Published關閉優先保留Invocation控制流程且清空共享authority(
    monkeypatch: pytest.MonkeyPatch, 控制流程類型: type[BaseException],
) -> None:
    """A3-02-11：Planner ordinary failure 不得吞掉後續 Invocation 控制流程。

    參數：pytest monkeypatch 與待驗證控制流程型別。
    返回值：None。
    例外：預期傳出 Invocation drain 的 exact 控制流程物件。
    副作用：關閉一個只含測試替身的 Published resource 並清空模型／工具 authority。
    """
    class _Planner關閉失敗:
        """模擬 Planner 關閉 ordinary failure；建構與持有皆無外部副作用。"""

        def _清除同步(self) -> None:
            """固定拋出 ordinary failure。

            參數：除目前實例外無參數。
            返回值：不會正常回傳。
            例外：固定拋 ``RuntimeError``。
            副作用：無。
            """
            raise RuntimeError("planner close failed")

    代理 = 延遲外部呼叫編排器()
    控制流程 = 控制流程類型("STOP", "invocation")

    def Invocation清除中斷(編排器) -> None:
        """模擬 Invocation drain 傳出 exact 控制流程。

        參數：``編排器`` 是待清除物件但刻意不使用。
        返回值：不會正常回傳。
        例外：傳出測試建立的 exact 控制流程物件。
        副作用：無。
        """
        del 編排器
        raise 控制流程

    monkeypatch.setattr(代理, "清除", Invocation清除中斷)
    模型表 = {"fake": object()}
    工具庫 = 工具發布庫()
    _登錄測試工具(工具庫)
    資源 = 生產Published執行資源(
        代理, object(), 工具庫, 模型表, _Planner關閉失敗(),  # type: ignore[arg-type]
    )
    with pytest.raises(控制流程類型) as 捕捉:
        asyncio.run(資源.關閉())
    assert 捕捉.value is 控制流程 and 捕捉.value.args == ("STOP", "invocation")
    assert 模型表 == {} and 工具庫.取得發布("release-1") is None


def test_Planner並行關閉caller必須等待同一active_lease_drain(tmp_path: Path) -> None:
    """A3-02-12：第二位 Planner shutdown caller 不得在唯一清理完成前返回。

    參數：``tmp_path`` 提供不會被權威查詢的 Web DB 路徑。
    返回值：None。
    例外：執行緒內任何非預期 shutdown 例外都使測試失敗。
    副作用：啟動一個阻塞 request 與兩個並行 shutdown 執行緒。
    """
    發布庫, 代理 = 工具發布庫(), 延遲草稿規劃服務()
    _登錄測試工具(發布庫)
    資源 = 建立生產Planner資源(
        _建立Planner設定(lambda _: _固定使用者來源(使用者上下文(user_id="owner-1"))),
        tmp_path / "web.sqlite3", 發布庫, 代理,
    )
    已進入, 可返回 = threading.Event(), threading.Event()
    關閉已開始 = threading.Event()
    第一完成, 第二完成 = threading.Event(), threading.Event()
    執行緒錯誤: list[BaseException] = []

    def 阻塞建立(*參數, **關鍵字):
        """持有一個 request lease 直到測試明確允許返回。

        參數：位置與關鍵字參數由 proxy 委派但不使用。
        返回值：固定字串 ``done``。
        例外：等待逾時時拋 ``AssertionError``。
        副作用：設定進入事件並阻塞目前執行緒。
        """
        del 參數, 關鍵字
        已進入.set()
        assert 可返回.wait(2)
        return "done"

    資源._服務.建立草稿 = 阻塞建立  # type: ignore[method-assign]
    原始執行清除 = 資源._執行清除同步

    def 記錄執行清除() -> None:
        """在唯一 shutdown owner 進入實際清理時發出同步訊號。

        參數：無。
        返回值：原始實際清理方法的 None。
        例外：原始清理例外原樣傳出。
        副作用：先設定關閉已開始事件，再委派完整 proxy drain。
        """
        關閉已開始.set()
        return 原始執行清除()

    資源._執行清除同步 = 記錄執行清除  # type: ignore[method-assign]
    Request執行緒 = threading.Thread(
        target=lambda: 代理.建立草稿("owner-1", "需求", (), "text", 現在=1.0),
    )
    Request執行緒.start()
    assert 已進入.wait(1)

    def 執行關閉(完成事件: threading.Event) -> None:
        """執行一次 async close 並記錄完成或非預期錯誤。

        參數：``完成事件`` 在 close 確實返回後設定。
        返回值：None。
        例外：捕捉所有錯誤後保存，供主測試執行緒斷言。
        副作用：建立短生命週期 event loop 並呼叫同一 Planner resource close。
        """
        try:
            asyncio.run(資源.關閉())
        except BaseException as 錯誤:
            執行緒錯誤.append(錯誤)
        finally:
            完成事件.set()

    第一關閉 = threading.Thread(target=執行關閉, args=(第一完成,))
    第二關閉 = threading.Thread(target=執行關閉, args=(第二完成,))
    第一關閉.start()
    assert 關閉已開始.wait(1)
    第二關閉.start()
    assert not 第二完成.wait(0.05)
    可返回.set()
    Request執行緒.join(2)
    第一關閉.join(2)
    第二關閉.join(2)
    assert 第一完成.is_set() and 第二完成.is_set() and 執行緒錯誤 == []


def test_Published並行關閉caller等待同一結果且保留錯誤identity() -> None:
    """A3-02-13：所有 Published shutdown caller 等待同一 task 並取得 exact error。

    參數：無。
    返回值：None。
    例外：測試 coroutine 內預期收集錯誤，不向外傳出。
    副作用：建立短生命週期 event loop、兩個 close tasks 並清空測試 registries。
    """

    async def 驗證並行關閉() -> None:
        """在單一 production event loop 驗證 concurrent close 完成語意。

        參數：無。
        返回值：None。
        例外：斷言不成立時由 pytest 回報。
        副作用：建立並關閉一個只含測試替身的 Published resource。
        """
        已進入, 可返回 = threading.Event(), threading.Event()
        預期錯誤 = RuntimeError("planner close failed")

        class _阻塞Planner關閉:
            """以兩個 threading events 模擬阻塞後失敗的 Planner shutdown。"""

            def _清除同步(self) -> None:
                """等待測試允許後傳出 exact ordinary failure。

                參數：除目前實例外無參數。
                返回值：不會正常回傳。
                例外：傳出測試建立的 exact ``RuntimeError``。
                副作用：設定已進入事件並等待可返回事件。
                """
                已進入.set()
                assert 可返回.wait(2)
                raise 預期錯誤

        模型表 = {"fake": object()}
        工具庫 = 工具發布庫()
        _登錄測試工具(工具庫)
        資源 = 生產Published執行資源(
            延遲外部呼叫編排器(), object(),  # type: ignore[arg-type]
            工具庫, 模型表, _阻塞Planner關閉(),  # type: ignore[arg-type]
        )
        第一關閉 = asyncio.create_task(資源.關閉())
        assert await asyncio.to_thread(已進入.wait, 1)
        第二關閉 = asyncio.create_task(資源.關閉())
        await asyncio.sleep(0.05)
        assert not 第一關閉.done() and not 第二關閉.done()
        可返回.set()
        第一結果, 第二結果 = await asyncio.gather(
            第一關閉, 第二關閉, return_exceptions=True,
        )
        assert 第一結果 is 預期錯誤 and 第二結果 is 預期錯誤
        assert 模型表 == {} and 工具庫.取得發布("release-1") is None

    asyncio.run(驗證並行關閉())


def test_Published_caller取消必須延後到cleanup完成並保留args() -> None:
    """A3-02-14：caller cancellation 不得讓 shutdown 在同步清理完成前返回。

    參數：無。
    返回值：None。
    例外：測試 coroutine 捕捉預期 ``CancelledError`` 並驗證 args。
    副作用：建立一條清理執行緒與短生命週期 event loop。
    """

    async def 驗證取消延後() -> None:
        """取消 caller 後確認其保持 pending，直到唯一 cleanup 完成。

        參數：無。
        返回值：None。
        例外：斷言不成立時由 pytest 回報。
        副作用：啟動、取消並等待一個 Published close task。
        """
        已進入, 可返回 = threading.Event(), threading.Event()

        class _阻塞Planner清理:
            """以 threading events 模擬不可由 caller cancellation 中止的同步清理。"""

            def _清除同步(self) -> None:
                """等待測試明確允許後成功返回。

                參數：除目前實例外無參數。
                返回值：None。
                例外：等待逾時時拋 ``AssertionError``。
                副作用：設定已進入事件並阻塞清理執行緒。
                """
                已進入.set()
                assert 可返回.wait(2)

        資源 = 生產Published執行資源(
            延遲外部呼叫編排器(), object(),  # type: ignore[arg-type]
            工具發布庫(), {}, _阻塞Planner清理(),  # type: ignore[arg-type]
        )
        關閉工作 = asyncio.create_task(資源.關閉())
        assert await asyncio.to_thread(已進入.wait, 1)
        關閉工作.cancel("caller-cancelled")
        await asyncio.sleep(0.05)
        assert not 關閉工作.done()
        可返回.set()
        with pytest.raises(asyncio.CancelledError) as 捕捉:
            await 關閉工作
        assert 捕捉.value.args == ("caller-cancelled",)

    asyncio.run(驗證取消延後())


def test_Published_cleanup取消對所有caller保留exact_identity與args() -> None:
    """A3-02-15：同步 cleanup 的 CancelledError 是共享結果，不被 wrapper 重建。

    參數：無。
    返回值：None。
    例外：兩個 caller 各自捕捉預期 cleanup cancellation。
    副作用：啟動唯一清理執行緒並在同一 event loop 建立兩個 caller tasks。
    """

    async def 驗證cleanup取消() -> None:
        """以 caller 內直接捕捉方式驗證 exact cleanup error identity。

        參數：無。
        返回值：None。
        例外：斷言不成立時由 pytest 回報。
        副作用：關閉一個測試 Published resource。
        """
        已進入, 可返回 = threading.Event(), threading.Event()
        預期取消 = asyncio.CancelledError("cleanup-cancelled", "exact")

        class _取消Planner清理:
            """在同步清理執行緒傳出預先建立的 exact CancelledError。"""

            def _清除同步(self) -> None:
                """等待第二 caller 就緒後傳出 exact cleanup cancellation。

                參數：除目前實例外無參數。
                返回值：不會正常回傳。
                例外：傳出測試預先建立的 ``CancelledError``。
                副作用：設定已進入事件並等待可返回事件。
                """
                已進入.set()
                assert 可返回.wait(2)
                raise 預期取消

        資源 = 生產Published執行資源(
            延遲外部呼叫編排器(), object(),  # type: ignore[arg-type]
            工具發布庫(), {}, _取消Planner清理(),  # type: ignore[arg-type]
        )

        async def 呼叫並捕捉() -> BaseException | None:
            """呼叫共享 close 並直接回傳其 exact BaseException。

            參數：無。
            返回值：捕捉的 exact error；意外成功則回傳 None。
            例外：不向外傳出，所有 BaseException 都轉為測試結果。
            副作用：等待同一 Published cleanup completion。
            """
            try:
                await 資源.關閉()
            except BaseException as 錯誤:
                return 錯誤
            return None

        第一呼叫 = asyncio.create_task(呼叫並捕捉())
        assert await asyncio.to_thread(已進入.wait, 1)
        第二呼叫 = asyncio.create_task(呼叫並捕捉())
        await asyncio.sleep(0)
        可返回.set()
        第一結果, 第二結果 = await asyncio.gather(第一呼叫, 第二呼叫)
        assert 第一結果 is 預期取消
        assert 第二結果 is 預期取消
        assert 第一結果.args == ("cleanup-cancelled", "exact")

    asyncio.run(驗證cleanup取消())


def test_Published跨event_loop並行caller等待同一cleanup() -> None:
    """A3-02-16：不同執行緒 event loop 的 callers 可共同等待而不產生 loop affinity 錯誤。

    參數：無。
    返回值：None。
    例外：任一 caller 錯誤都保存後由主測試斷言失敗。
    副作用：建立兩個短生命週期 event loops 與一條唯一清理執行緒。
    """
    已進入, 可返回 = threading.Event(), threading.Event()
    第一完成, 第二完成 = threading.Event(), threading.Event()
    執行緒錯誤: list[BaseException] = []

    class _跨Loop阻塞清理:
        """在唯一同步清理執行緒阻塞，供兩個 event loops 同時等待。"""

        def _清除同步(self) -> None:
            """等待測試允許後成功完成。

            參數：除目前實例外無參數。
            返回值：None。
            例外：等待逾時時拋 ``AssertionError``。
            副作用：設定已進入事件並阻塞清理執行緒。
            """
            已進入.set()
            assert 可返回.wait(2)

    資源 = 生產Published執行資源(
        延遲外部呼叫編排器(), object(),  # type: ignore[arg-type]
        工具發布庫(), {}, _跨Loop阻塞清理(),  # type: ignore[arg-type]
    )

    def 執行關閉(完成事件: threading.Event) -> None:
        """在獨立 event loop 呼叫共享 close 並記錄完成。

        參數：``完成事件`` 在 close 確實返回後設定。
        返回值：None。
        例外：捕捉所有錯誤後保存供主測試斷言。
        副作用：建立及關閉目前執行緒的短生命週期 event loop。
        """
        try:
            asyncio.run(資源.關閉())
        except BaseException as 錯誤:
            執行緒錯誤.append(錯誤)
        finally:
            完成事件.set()

    第一執行緒 = threading.Thread(target=執行關閉, args=(第一完成,))
    第二執行緒 = threading.Thread(target=執行關閉, args=(第二完成,))
    第一執行緒.start()
    assert 已進入.wait(1)
    第二執行緒.start()
    assert not 第一完成.wait(0.05) and not 第二完成.is_set()
    可返回.set()
    第一執行緒.join(2)
    第二執行緒.join(2)
    assert 第一完成.is_set() and 第二完成.is_set() and 執行緒錯誤 == []


def test_Published_threadpool尚未取得owner就失敗時caller原地清理且不留authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3-02-17：dispatch 未進入 owner 時必須 fallback 清理而非快取未清理錯誤。

    參數：暫存路徑與 pytest monkeypatch。
    返回值：None。
    例外：fallback 清理若失敗會使測試失敗。
    副作用：注入 threadpool dispatch failure，原地撤銷 Planner 並清空模型／工具 authority。
    """
    工具庫, 草稿代理 = 工具發布庫(), 延遲草稿規劃服務()
    _登錄測試工具(工具庫)
    Planner資源 = 建立生產Planner資源(
        _建立Planner設定(lambda _: _固定使用者來源(使用者上下文(user_id="owner-1"))),
        tmp_path / "web.sqlite3", 工具庫, 草稿代理,
    )
    模型表 = {"fake": object()}
    資源 = 生產Published執行資源(
        延遲外部呼叫編排器(), object(), 工具庫, 模型表, Planner資源,  # type: ignore[arg-type]
    )
    清理次數: list[str] = []
    原始清理 = 資源._執行關閉同步

    def 記錄清理() -> None:
        """記錄並委派 fallback 的完整同步清理。

        參數：無。
        返回值：原始同步清理的 None。
        例外：原始清理例外原樣傳出。
        副作用：增加一次呼叫紀錄並撤銷全部測試 authority。
        """
        清理次數.append("cleanup")
        return 原始清理()

    async def 拒絕派送(函式, *參數, **關鍵字):
        """模擬 threadpool 尚未呼叫同步函式前即無法派送。

        參數：同步函式、位置與關鍵字參數都刻意不使用。
        返回值：不會正常回傳。
        例外：固定拋 ``RuntimeError``。
        副作用：不呼叫同步 cleanup。
        """
        del 函式, 參數, 關鍵字
        raise RuntimeError("cannot dispatch worker")

    資源._執行關閉同步 = 記錄清理  # type: ignore[method-assign]
    monkeypatch.setattr(生產Published執行模組, "run_in_threadpool", 拒絕派送)
    asyncio.run(資源.關閉())
    asyncio.run(資源.關閉())
    assert 清理次數 == ["cleanup"]
    assert 資源.取得Planner資源() is None and 資源.取得規劃服務() is None
    assert 模型表 == {} and 工具庫.取得發布("release-1") is None
    with pytest.raises(草稿規劃服務不可用):
        草稿代理.建立草稿("owner-1", "需求", (), "text", 現在=1.0)


def test_Published_worker已取得owner後派送控制流程必須等待cleanup且不得雙重清理(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A3-02-18：dispatch 後段控制流程不可造成第二 owner 或提前返回。

    參數：pytest monkeypatch 用來注入 worker 已啟動後的 exact 控制流程。
    返回值：None。
    例外：預期在唯一 cleanup 完成後傳出注入的 ``KeyboardInterrupt``。
    副作用：建立一條真實 worker、一個 timer 並清空測試 registries。
    """
    已進入, 可返回 = threading.Event(), threading.Event()
    清理次數: list[str] = []
    預期控制流程 = KeyboardInterrupt("STOP", "dispatch")

    class _阻塞一次清理:
        """記錄唯一 cleanup 並阻塞至 timer 允許返回。"""

        def _清除同步(self) -> None:
            """持有 cleanup owner window 後成功完成。

            參數：除目前實例外無參數。
            返回值：None。
            例外：等待逾時時拋 ``AssertionError``。
            副作用：記錄次數、設定已進入事件並阻塞 worker。
            """
            清理次數.append("cleanup")
            已進入.set()
            assert 可返回.wait(2)

    工具庫, 模型表 = 工具發布庫(), {"fake": object()}
    _登錄測試工具(工具庫)
    資源 = 生產Published執行資源(
        延遲外部呼叫編排器(), object(),  # type: ignore[arg-type]
        工具庫, 模型表, _阻塞一次清理(),  # type: ignore[arg-type]
    )

    async def 已啟動後中斷派送(函式, *參數, **關鍵字):
        """先啟動 worker 並確認取得 owner，再傳出 exact 控制流程。

        參數：同步函式及其位置／關鍵字參數。
        返回值：不會正常回傳。
        例外：傳出預先建立的 exact ``KeyboardInterrupt``。
        副作用：啟動一條 worker 執行緒執行同步函式。
        """
        Worker = threading.Thread(target=函式, args=參數, kwargs=關鍵字)
        Worker.start()
        assert await asyncio.to_thread(已進入.wait, 1)
        raise 預期控制流程

    monkeypatch.setattr(生產Published執行模組, "run_in_threadpool", 已啟動後中斷派送)
    threading.Timer(0.1, 可返回.set).start()
    開始 = time.monotonic()
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        asyncio.run(資源.關閉())
    assert time.monotonic() - 開始 >= 0.05
    assert 捕捉.value is 預期控制流程 and 捕捉.value.args == ("STOP", "dispatch")
    assert 清理次數 == ["cleanup"]
    assert 模型表 == {} and 工具庫.取得發布("release-1") is None
