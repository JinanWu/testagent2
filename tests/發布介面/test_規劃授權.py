"""PUB P02 使用 FND Protocol 與 P01 單一草稿生命週期的整合測試。"""

import ast
from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import threading

import pytest

from 繁中代理.發布介面 import 授權工具, 授權技能, 規劃權限快照
from 繁中代理.發布介面.規劃.服務 import 發布規劃服務, 規劃已過時錯誤
from 繁中代理.發布介面.規劃.權限協調 import 授權選擇錯誤, 權限協調器, 能力摘要
from 繁中代理.發布介面.規劃.綱要 import 規劃服務, 規劃草稿, 草稿存取錯誤


class _查詢器:
    def __init__(self, 快照):
        self.快照, self.查找, self.呼叫 = 快照, 0, 0

    @property
    def 查詢規劃權限(self):
        self.查找 += 1

        def 查詢(_擁有者, /):
            self.呼叫 += 1
            return self.快照

        return 查詢


class _阻塞查詢器(_查詢器):
    def __init__(self, 快照):
        super().__init__(快照)
        self.已進入, self.可繼續 = threading.Event(), threading.Event()
        self.開始阻塞 = False

    @property
    def 查詢規劃權限(self):
        self.查找 += 1

        def 查詢(_擁有者, /):
            self.呼叫 += 1
            if self.開始阻塞:
                self.已進入.set()
                assert self.可繼續.wait(2)
            return self.快照

        return 查詢


def _啟動(操作):
    結果 = []

    def 執行():
        try:
            結果.append(操作())
        except BaseException as 錯誤:
            結果.append(錯誤)

    執行緒 = threading.Thread(target=執行)
    執行緒.start()
    return 執行緒, 結果


class _敏感基底錯誤(BaseException):
    pass


class _錯誤查詢器(_查詢器):
    def __init__(self, 快照):
        super().__init__(快照)
        self.錯誤 = None

    @property
    def 查詢規劃權限(self):
        self.查找 += 1

        def 查詢(_擁有者, /):
            self.呼叫 += 1
            if self.錯誤 is not None:
                raise self.錯誤
            return self.快照

        return 查詢


def _含標記(值, 標記, 已看過):
    if id(值) in 已看過:
        return False
    已看過.add(id(值))
    if type(值) is str:
        return 值 == 標記
    if type(值) in (tuple, list, set, frozenset):
        for 項目 in 值:
            if _含標記(項目, 標記, 已看過):
                return True
        return False
    if type(值) is dict:
        for 鍵, 項目 in dict.items(值):
            if _含標記(鍵, 標記, 已看過) or _含標記(項目, 標記, 已看過):
                return True
        return False
    if type(值) in (KeyboardInterrupt, SystemExit, GeneratorExit, _敏感基底錯誤):
        return _含標記(object.__getattribute__(值, "args"), 標記, 已看過)
    if type(值) in (授權技能, 授權工具, 規劃權限快照, 能力摘要, 規劃草稿):
        for 欄位 in type(值).__slots__:
            if _含標記(object.__getattribute__(值, 欄位), 標記, 已看過):
                return True
    return False


def _斷言P02生產frame已清除(錯誤, 標記):
    名稱 = []
    追蹤 = 錯誤.__traceback__
    while 追蹤 is not None:
        frame = 追蹤.tb_frame
        if frame.f_code.co_filename.endswith(("/權限協調.py", "/服務.py", "/綱要.py")):
            名稱.append(frame.f_code.co_name)
            for 值 in frame.f_locals.values():
                assert not _含標記(值, 標記, set())
        追蹤 = 追蹤.tb_next
    assert 名稱
    return 名稱


def _快照(修訂="perm-v1", 技能=None):
    return 規劃權限快照(
        修訂,
        技能 if 技能 is not None else (
            授權技能("research", "可信研究", "a" * 64),
            授權技能("writing", "寫作", "b" * 64),
        ),
        (授權工具("read_file", "tool-r2"), 授權工具("web_search", "tool-r7")),
    )


def _服務(查詢器, 草稿服務=None):
    return 發布規劃服務(
        權限協調器(查詢器),
        草稿服務=草稿服務 or 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-p02"),
    )


def test_從FND完整快照建立canonical摘要並釘選於P01草稿():
    查詢器 = _查詢器(_快照())
    服務 = _服務(查詢器)
    草稿 = 服務.建立草稿(
        "owner-a", "研究並寫作", {"steps": ["research"]}, ("research",), ("web_search",), 現在=100
    )
    assert type(草稿) is 規劃草稿 and 草稿.狀態 == "draft" and 草稿.到期時間 == 160
    assert (查詢器.查找, 查詢器.呼叫) == (1, 1)
    assert 草稿.能力摘要.正規JSON == (
        '{"permission_revision":"perm-v1","skills":[{"content_sha256_reference":"' + "a" * 64
        + '","name":"research","summary":"可信研究"}],"tools":[{"name":"web_search","revision":"tool-r7"}]}'
    )
    assert not hasattr(服務, "_草稿") and not hasattr(服務, "_已過時")
    with pytest.raises(FrozenInstanceError):
        草稿.能力摘要.技能 = ()


@pytest.mark.parametrize("技能,工具,呼叫次數", [((), (), 0), (("unknown",), (), 1), (("research", "research"), (), 0)])
def test_無效選擇在可判定時零查詢並fail_closed(技能, 工具, 呼叫次數):
    查詢器 = _查詢器(_快照())
    with pytest.raises(授權選擇錯誤, match="^規劃能力未獲授權$"):
        權限協調器(查詢器).建立能力摘要("owner-a", 技能, 工具)
    assert 查詢器.呼叫 == 呼叫次數


def test_撤銷使同一P01草稿原子永久stale且不再查詢():
    查詢器 = _查詢器(_快照())
    服務 = _服務(查詢器)
    服務.建立草稿("owner-a", "需求", {}, ("research",), (), 現在=100)
    查詢器.快照 = _快照("perm-v2", 技能=(授權技能("writing", "寫作", "b" * 64),))
    with pytest.raises(規劃已過時錯誤, match="^規劃權限已變更$"):
        服務.規劃草稿("owner-a", "draft-p02", 現在=110)
    assert 服務._草稿服務._草稿["draft-p02"].狀態 == "stale"
    assert 查詢器.呼叫 == 2
    with pytest.raises(草稿存取錯誤):
        服務.產生內容("owner-a", "draft-p02", 現在=111)
    assert 查詢器.呼叫 == 2


@pytest.mark.parametrize("舊參數", ["能力摘要", "_能力摘要產生器"])
def test_P01建立拒絕舊摘要入口且不呼叫產生器(舊參數):
    呼叫 = 0

    def 不可信產生器():
        nonlocal 呼叫
        呼叫 += 1
        return object()

    服務 = 規劃服務(識別碼產生器=lambda: "forged")
    關鍵字 = {舊參數: 不可信產生器 if 舊參數.startswith("_") else object()}
    with pytest.raises(TypeError):
        服務.建立草稿("owner", "request", {}, 現在=1, **關鍵字)
    assert 呼叫 == 0 and 服務._草稿 == {}
    assert tuple(inspect.signature(服務.建立草稿).parameters) == ("擁有者識別碼", "原始需求", "綱要", "現在")


def test_P01舊重驗方法完全不存在且單一入口拒絕注入摘要():
    查詢器 = _查詢器(_快照())
    草稿服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-closed")
    服務 = _服務(查詢器, 草稿服務)
    草稿 = 服務.建立草稿("owner-a", "需求", {}, ("research",), (), 現在=100)
    for 舊名稱 in ("取得權限重驗目標", "完成權限重驗"):
        assert not hasattr(草稿服務, 舊名稱)
        with pytest.raises(AttributeError):
            getattr(草稿服務, 舊名稱)
    assert tuple(inspect.signature(草稿服務.重驗授權草稿).parameters) == (
        "協調器", "擁有者識別碼", "草稿識別碼", "現在"
    )
    with pytest.raises(TypeError):
        草稿服務.重驗授權草稿(
            權限協調器(查詢器), "owner-a", "draft-closed", 草稿, 草稿.能力摘要, 現在=110
        )
    with pytest.raises(TypeError):
        草稿服務.重驗授權草稿(
            權限協調器(查詢器), "owner-a", "draft-closed", 現在=110, 目前摘要=草稿.能力摘要
        )
    assert 查詢器.呼叫 == 1
    assert 草稿服務._草稿["draft-closed"] is not 草稿
    assert 草稿服務._草稿["draft-closed"] == 草稿


def test_所有公開草稿與巢狀能力DTO皆detached且修改不影響留存():
    查詢器 = _查詢器(_快照())
    草稿服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-detached")
    服務 = _服務(查詢器, 草稿服務)
    建立結果 = 服務.建立草稿("owner-a", "需求", {"v": 1}, ("research",), ("web_search",), 現在=100)
    讀取結果 = 草稿服務.讀取草稿("owner-a", "draft-detached", 現在=101)

    object.__setattr__(建立結果, "草稿識別碼", "evil-id")
    object.__setattr__(建立結果, "擁有者識別碼", "evil-owner")
    object.__setattr__(建立結果, "原始需求", "evil-request")
    object.__setattr__(建立結果, "_綱要正規JSON", '{"v":99}')
    object.__setattr__(建立結果, "建立時間", 999)
    object.__setattr__(建立結果, "到期時間", 999)
    object.__setattr__(建立結果, "能力摘要", None)
    object.__setattr__(建立結果, "發布確認", object())
    object.__setattr__(建立結果, "狀態", "stale")
    object.__setattr__(建立結果, "_世代", 999)
    object.__setattr__(讀取結果.能力摘要, "權限修訂", "evil-revision")
    object.__setattr__(讀取結果.能力摘要, "正規JSON", "{}")
    object.__setattr__(讀取結果.能力摘要.技能[0], "名稱", "evil-skill")
    object.__setattr__(讀取結果.能力摘要.技能[0], "摘要", "evil-summary")
    object.__setattr__(讀取結果.能力摘要.技能[0], "內容sha256參照", "f" * 64)
    object.__setattr__(讀取結果.能力摘要.工具[0], "名稱", "evil-tool")
    object.__setattr__(讀取結果.能力摘要.工具[0], "釘選修訂", "evil-tool-r")

    留存 = 草稿服務.讀取草稿("owner-a", "draft-detached", 現在=102)
    assert 留存.原始需求 == "需求" and 留存.綱要 == {"v": 1}
    assert 留存.能力摘要.權限修訂 == "perm-v1"
    assert 留存.能力摘要.技能[0].名稱 == "research"
    assert 留存.能力摘要.工具[0].名稱 == "web_search"
    assert 留存 is not 草稿服務._草稿["draft-detached"]
    assert 留存.能力摘要 is not 草稿服務._草稿["draft-detached"].能力摘要

    更新結果 = 草稿服務.更新草稿("owner-a", "draft-detached", {"v": 2}, 現在=103)
    重驗結果 = 服務.規劃草稿("owner-a", "draft-detached", 現在=104)
    assert 更新結果 is not 草稿服務._草稿["draft-detached"]
    assert 重驗結果 is not 草稿服務._草稿["draft-detached"]
    object.__setattr__(更新結果, "_綱要正規JSON", '{"v":999}')
    object.__setattr__(更新結果.能力摘要.技能[0], "摘要", "update-evil")
    object.__setattr__(重驗結果, "擁有者識別碼", "revalidate-evil")
    object.__setattr__(重驗結果.能力摘要.工具[0], "釘選修訂", "revalidate-evil")
    最終留存 = 草稿服務.讀取草稿("owner-a", "draft-detached", 現在=105)
    assert 最終留存.綱要 == {"v": 2}
    assert 最終留存.能力摘要.技能[0].摘要 == "可信研究"
    assert 最終留存.能力摘要.工具[0].釘選修訂 == "tool-r7"


def test_更新與阻塞重驗以CAS拒絕且provider期間P01不持鎖():
    查詢器 = _阻塞查詢器(_快照())
    草稿服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-race-update")
    服務 = _服務(查詢器, 草稿服務)
    服務.建立草稿("owner-a", "需求", {"v": 1}, ("research",), (), 現在=100)
    查詢器.開始阻塞 = True
    重驗執行緒, 重驗結果 = _啟動(lambda: 服務.規劃草稿("owner-a", "draft-race-update", 現在=110))
    assert 查詢器.已進入.wait(2)

    讀取執行緒, 讀取結果 = _啟動(lambda: 草稿服務.讀取草稿("owner-a", "draft-race-update", 現在=111))
    更新執行緒, 更新結果 = _啟動(lambda: 草稿服務.更新草稿("owner-a", "draft-race-update", {"v": 2}, 現在=111))
    讀取執行緒.join(2)
    更新執行緒.join(2)
    assert not 讀取執行緒.is_alive() and not 更新執行緒.is_alive()
    assert type(讀取結果[0]) is 規劃草稿 and type(更新結果[0]) is 規劃草稿

    查詢器.可繼續.set()
    重驗執行緒.join(2)
    assert not 重驗執行緒.is_alive() and type(重驗結果[0]) is 草稿存取錯誤
    留存 = 草稿服務._草稿["draft-race-update"]
    assert 留存.狀態 == "draft" and 留存.綱要 == {"v": 2}


def test_到期與阻塞重驗刪除後完成不得復活():
    查詢器 = _阻塞查詢器(_快照())
    草稿服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-race-expiry")
    服務 = _服務(查詢器, 草稿服務)
    服務.建立草稿("owner-a", "需求", {}, ("research",), (), 現在=100)
    查詢器.開始阻塞 = True
    執行緒, 結果 = _啟動(lambda: 服務.規劃草稿("owner-a", "draft-race-expiry", 現在=159))
    assert 查詢器.已進入.wait(2)
    with pytest.raises(草稿存取錯誤):
        草稿服務.讀取草稿("owner-a", "draft-race-expiry", 現在=160)
    查詢器.可繼續.set()
    執行緒.join(2)
    assert not 執行緒.is_alive() and type(結果[0]) is 草稿存取錯誤
    assert 草稿服務._草稿 == {}


def test_撤銷重驗完成後stale永久抵抗更新與再次重驗():
    查詢器 = _阻塞查詢器(_快照())
    草稿服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-race-revoke")
    服務 = _服務(查詢器, 草稿服務)
    服務.建立草稿("owner-a", "需求", {}, ("research",), (), 現在=100)
    查詢器.快照 = _快照("perm-v2", 技能=(授權技能("writing", "寫作", "b" * 64),))
    查詢器.開始阻塞 = True
    執行緒, 結果 = _啟動(lambda: 服務.規劃草稿("owner-a", "draft-race-revoke", 現在=110))
    assert 查詢器.已進入.wait(2)
    查詢器.可繼續.set()
    執行緒.join(2)
    assert not 執行緒.is_alive() and type(結果[0]) is 規劃已過時錯誤
    assert 草稿服務._草稿["draft-race-revoke"].狀態 == "stale"
    with pytest.raises(草稿存取錯誤):
        草稿服務.更新草稿("owner-a", "draft-race-revoke", {}, 現在=111)
    with pytest.raises(草稿存取錯誤):
        服務.規劃草稿("owner-a", "draft-race-revoke", 現在=111)
    assert 查詢器.呼叫 == 2 and 草稿服務._草稿["draft-race-revoke"].狀態 == "stale"


@pytest.mark.parametrize("階段", ["建立", "規劃", "內容"])
@pytest.mark.parametrize("來源", ["provider", "coordinator"])
@pytest.mark.parametrize("例外型別", [_敏感基底錯誤, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_建立與重驗之provider及coordinator例外矩陣(monkeypatch, 階段, 來源, 例外型別):
    marker = "a" * 32
    原始例外 = 例外型別(marker, 7)
    標記快照 = _快照(技能=(授權技能(marker, marker, marker * 2),))
    查詢器 = _錯誤查詢器(標記快照)
    草稿服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-trace")
    服務 = _服務(查詢器, 草稿服務)
    if 階段 != "建立":
        草稿 = 服務.建立草稿(marker, "需求", {}, (marker,), (), 現在=100)
        assert 草稿.擁有者識別碼 == marker
        assert 草稿.能力摘要.技能[0].名稱 == marker
        assert 草稿.能力摘要.技能[0].摘要 == marker
        assert 草稿.能力摘要.技能[0].內容sha256參照 == marker * 2
    if 來源 == "provider":
        查詢器.錯誤 = 原始例外
    else:
        def 注入(_self, _擁有者, _技能, _工具):
            raise 原始例外

        monkeypatch.setattr(權限協調器, "建立能力摘要", 注入)

    with pytest.raises(BaseException) as 捕捉:
        if 階段 == "建立":
            服務.建立草稿(marker, "需求", {"marker": marker}, (marker,), (), 現在=100)
        elif 階段 == "規劃":
            服務.規劃草稿(marker, "draft-trace", 現在=110)
        else:
            服務.產生內容(marker, "draft-trace", 現在=110)
    if 例外型別 is _敏感基底錯誤:
        預期 = 授權選擇錯誤 if 階段 == "建立" else 規劃已過時錯誤
        assert type(捕捉.value) is 預期
        assert 捕捉.value.args == (("規劃能力未獲授權",) if 階段 == "建立" else ("規劃權限已變更",))
        assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    else:
        assert 捕捉.value is 原始例外
        assert type(捕捉.value) is 例外型別 and 捕捉.value.args == (marker, 7)
    _斷言P02生產frame已清除(捕捉.value, marker)


def test_P02敏感模組無comprehension且無可注入摘要之草稿建構器():
    根目錄 = Path(__file__).resolve().parents[2]
    模組 = 根目錄 / "繁中代理" / "發布介面" / "規劃"
    for 檔名 in ("權限協調.py", "服務.py", "綱要.py"):
        樹 = ast.parse((模組 / 檔名).read_text(encoding="utf-8"))
        for 節點 in ast.walk(樹):
            assert not isinstance(節點, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            if isinstance(節點, (ast.FunctionDef, ast.AsyncFunctionDef)):
                摘要參數 = False
                for 參數 in 節點.args.args + 節點.args.kwonlyargs:
                    if "摘要" in 參數.arg:
                        摘要參數 = True
                if 摘要參數:
                    for 子節點 in ast.walk(節點):
                        if isinstance(子節點, ast.Call) and isinstance(子節點.func, ast.Name):
                            assert 子節點.func.id != "規劃草稿"
    for 名稱, 方法 in inspect.getmembers(規劃服務, inspect.isfunction):
        assert 名稱 not in ("取得權限重驗目標", "完成權限重驗")
        for 參數 in inspect.signature(方法).parameters.values():
            assert "摘要" not in 參數.name
