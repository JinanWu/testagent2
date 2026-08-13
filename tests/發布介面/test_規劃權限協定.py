"""Planner 權限查詢公開契約與 fail-closed 邊界。"""

from dataclasses import FrozenInstanceError

import pytest

import 繁中代理.發布介面.協定 as 協定模組
from 繁中代理.發布介面 import (
    Planner權限查詢,
    授權工具,
    授權技能,
    規劃權限快照,
    規劃權限查詢錯誤,
    安全查詢規劃權限,
)

雜湊 = "a" * 64


def _快照():
    return 規劃權限快照(
        "perm-r1",
        (授權技能("alpha", "第一個技能", 雜湊), 授權技能("beta", "第二個技能", "b" * 64)),
        (授權工具("read_file", "tool-r1"),),
    )


def _偽造技能(名稱="alpha", 摘要="摘要", 內容參照=雜湊):
    技能 = object.__new__(授權技能)
    object.__setattr__(技能, "名稱", 名稱)
    object.__setattr__(技能, "摘要", 摘要)
    object.__setattr__(技能, "內容sha256參照", 內容參照)
    return 技能


def _偽造工具(名稱="read_file", 釘選修訂="tool-r1"):
    工具 = object.__new__(授權工具)
    object.__setattr__(工具, "名稱", 名稱)
    object.__setattr__(工具, "釘選修訂", 釘選修訂)
    return 工具


def _偽造快照(技能=(), 工具=(), 權限修訂="perm-r1"):
    快照 = object.__new__(規劃權限快照)
    object.__setattr__(快照, "權限修訂", 權限修訂)
    object.__setattr__(快照, "技能", 技能)
    object.__setattr__(快照, "工具", 工具)
    return 快照


class _查詢器:
    def __init__(self, 結果=None, 錯誤=None):
        self.結果, self.錯誤, self.查找次數, self.呼叫次數 = 結果, 錯誤, 0, 0

    @property
    def 查詢規劃權限(self):
        self.查找次數 += 1

        def 呼叫(擁有者, /):
            self.呼叫次數 += 1
            if self.錯誤:
                raise self.錯誤
            return self.結果

        return 呼叫


class _敵對tuple(tuple):
    觸發次數 = 0

    def __iter__(self):
        type(self).觸發次數 += 1
        raise AssertionError("不可迭代非精確 tuple")


class _敵對字串(str):
    def __new__(cls, 值):
        結果 = super().__new__(cls, 值)
        結果.觸發次數 = 0
        return 結果

    def _觸發(self):
        self.觸發次數 += 1
        raise AssertionError("精確型別驗證前不可操作敵對值")

    def __hash__(self):
        return self._觸發()

    def __eq__(self, 其他):
        return self._觸發()

    def __le__(self, 其他):
        return self._觸發()

    def __len__(self):
        return self._觸發()

    def splitlines(self, *引數, **關鍵字):
        return self._觸發()


class _敵對基礎例外(BaseException):
    pass


def _物件含標記(值, 標記, 已看過):
    if id(值) in 已看過:
        return False
    已看過.add(id(值))
    if type(值) is str:
        return 值 == 標記
    if type(值) in (tuple, list, set, frozenset):
        for 項目 in 值:
            if _物件含標記(項目, 標記, 已看過):
                return True
        return False
    if type(值) is dict:
        for 鍵, 項目 in 值.items():
            if _物件含標記(鍵, 標記, 已看過) or _物件含標記(項目, 標記, 已看過):
                return True
        return False
    if type(值) in (KeyboardInterrupt, SystemExit, GeneratorExit, _敵對基礎例外, 規劃權限查詢錯誤):
        return _物件含標記(object.__getattribute__(值, "args"), 標記, 已看過)
    if type(值) in (授權技能, 授權工具, 規劃權限快照):
        for 欄位 in type(值).__slots__:
            if _物件含標記(object.__getattribute__(值, 欄位), 標記, 已看過):
                return True
    if type(值) is _查詢器:
        return _物件含標記(object.__getattribute__(值, "__dict__"), 標記, 已看過)
    return False


def _斷言協定traceback已清除(例外, 標記):
    框架名稱 = []
    追蹤 = object.__getattribute__(例外, "__traceback__")
    while 追蹤 is not None:
        框架 = 追蹤.tb_frame
        if 框架.f_code.co_filename.endswith("/協定.py"):
            框架名稱.append(框架.f_code.co_name)
            區域 = 框架.f_locals
            for 名稱 in 區域:
                值 = 區域[名稱]
                assert not _物件含標記(值, 標記, set())
        追蹤 = 追蹤.tb_next
    return 框架名稱


def test_DTO精確凍結且公開內容最小化():
    技能 = 授權技能("alpha", "摘要", 雜湊)
    工具 = 授權工具("terminal", "rev-1")
    with pytest.raises(FrozenInstanceError):
        技能.名稱 = "other"
    assert not hasattr(技能, "__dict__")
    assert tuple(技能.__dataclass_fields__) == ("名稱", "摘要", "內容sha256參照")
    assert tuple(工具.__dataclass_fields__) == ("名稱", "釘選修訂")
    assert tuple(規劃權限快照.__dataclass_fields__) == ("權限修訂", "技能", "工具")


def test_安全查詢接受production核准工具修訂中的at分隔符():
    原始 = 規劃權限快照(
        "perm-r1",
        (授權技能("alpha", "第一個技能", 雜湊),),
        (
            授權工具("skill_view", "skill_view@bundle-v1"),
            授權工具("skills_list", "skills_list@bundle-v1"),
        ),
    )
    結果 = 安全查詢規劃權限(_查詢器(原始), "owner-1")
    assert tuple(工具.釘選修訂 for 工具 in 結果.工具) == (
        "skill_view@bundle-v1",
        "skills_list@bundle-v1",
    )


def test_Protocol只有一個owner_scoped權威查詢():
    assert set(Planner權限查詢.__dict__) & {"查詢規劃權限"} == {"查詢規劃權限"}
    assert not getattr(Planner權限查詢, "_is_runtime_protocol", False)


def test_安全查詢重建完整快照且只查一次():
    原始 = _快照()
    查詢器 = _查詢器(原始)
    結果 = 安全查詢規劃權限(查詢器, "owner-1")
    assert 結果 == 原始 and 結果 is not 原始
    assert 結果.技能[0] is not 原始.技能[0]
    assert (查詢器.查找次數, 查詢器.呼叫次數) == (1, 1)


@pytest.mark.parametrize("擁有者", ["", " owner", "../owner", 7, str("x") * 129])
def test_owner預檢失敗完全不查provider(擁有者):
    查詢器 = _查詢器(_快照())
    with pytest.raises(規劃權限查詢錯誤, match="^無法取得規劃權限快照$"):
        安全查詢規劃權限(查詢器, 擁有者)
    assert (查詢器.查找次數, 查詢器.呼叫次數) == (0, 0)


@pytest.mark.parametrize(
    "結果",
    [
        object(),
        _偽造快照((授權技能("beta", "摘要", 雜湊), 授權技能("alpha", "摘要", 雜湊))),
        _偽造快照((授權技能("alpha", "摘要", 雜湊), 授權技能("alpha", "摘要", 雜湊))),
        _偽造快照(工具=(授權工具("z", "r1"), 授權工具("z", "r2"))),
    ],
)
def test_拒絕非精確重複或非正規順序(結果):
    查詢器 = _查詢器(結果)
    with pytest.raises(規劃權限查詢錯誤):
        安全查詢規劃權限(查詢器, "owner-1")
    assert (查詢器.查找次數, 查詢器.呼叫次數) == (1, 1)


@pytest.mark.parametrize(
    ("值", "建立快照"),
    [
        (_敵對字串("alpha"), lambda 值: _偽造快照((_偽造技能(名稱=值),))),
        (_敵對字串("摘要"), lambda 值: _偽造快照((_偽造技能(摘要=值),))),
        (_敵對字串(雜湊), lambda 值: _偽造快照((_偽造技能(內容參照=值),))),
        (_敵對字串("read_file"), lambda 值: _偽造快照(工具=(_偽造工具(名稱=值),))),
        (_敵對字串("tool-r1"), lambda 值: _偽造快照(工具=(_偽造工具(釘選修訂=值),))),
        (_敵對字串("perm-r1"), lambda 值: _偽造快照(權限修訂=值)),
    ],
)
def test_每個偽造exact_DTO固定欄位皆在敵對操作前拒絕(值, 建立快照):
    with pytest.raises(規劃權限查詢錯誤):
        安全查詢規劃權限(_查詢器(建立快照(值)), "owner-1")
    assert 值.觸發次數 == 0


@pytest.mark.parametrize(
    ("值", "建立"),
    [
        (_敵對字串("alpha"), lambda 值: 規劃權限快照("perm-r1", (_偽造技能(名稱=值),), ())),
        (_敵對字串("摘要"), lambda 值: 規劃權限快照("perm-r1", (_偽造技能(摘要=值),), ())),
        (_敵對字串(雜湊), lambda 值: 規劃權限快照("perm-r1", (_偽造技能(內容參照=值),), ())),
        (_敵對字串("read_file"), lambda 值: 規劃權限快照("perm-r1", (), (_偽造工具(名稱=值),))),
        (_敵對字串("tool-r1"), lambda 值: 規劃權限快照("perm-r1", (), (_偽造工具(釘選修訂=值),))),
        (_敵對字串("perm-r1"), lambda 值: 規劃權限快照(值, (), ())),
    ],
)
def test_快照建構亦完整驗證每個固定欄位且不操作敵對值(值, 建立):
    with pytest.raises(ValueError, match="^規劃權限快照無效$"):
        建立(值)
    assert 值.觸發次數 == 0


@pytest.mark.parametrize("建立快照", [lambda 值: _偽造快照(技能=值), lambda 值: _偽造快照(工具=值)])
def test_每個非精確容器在迭代前拒絕(建立快照):
    _敵對tuple.觸發次數 = 0
    with pytest.raises(規劃權限查詢錯誤):
        安全查詢規劃權限(_查詢器(建立快照(_敵對tuple())), "owner-1")
    assert _敵對tuple.觸發次數 == 0


def test_自訂BaseException固定淨化():
    marker = "SECRET_PROVIDER_MARKER"
    查詢器 = _查詢器(錯誤=_敵對基礎例外(marker))
    with pytest.raises(規劃權限查詢錯誤) as 捕捉:
        安全查詢規劃權限(查詢器, "owner-1")
    assert str(捕捉.value) == "無法取得規劃權限快照"
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    assert not _物件含標記(捕捉.value, marker, set())
    assert "安全查詢規劃權限" in _斷言協定traceback已清除(捕捉.value, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流保留原例外且FND_traceback區域已清除(例外型別):
    marker = "CONTROL_FLOW_SECRET"
    原例外 = 例外型別(marker)
    with pytest.raises(例外型別) as 捕捉:
        安全查詢規劃權限(_查詢器(錯誤=原例外), "owner-1")
    assert 捕捉.value is 原例外
    assert type(捕捉.value) is 例外型別 and 捕捉.value.args == (marker,)
    assert "安全查詢規劃權限" in _斷言協定traceback已清除(捕捉.value, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize(
    "建立DTO",
    [
        lambda 標記: 授權技能(標記, "摘要", 雜湊),
        lambda 標記: 授權工具(標記, "tool-r1"),
        lambda 標記: 規劃權限快照(標記, (), ()),
    ],
)
def test_每個DTO驗證器控制流皆清除self並原樣重拋(monkeypatch, 例外型別, 建立DTO):
    marker = "DTO_CONTROL_SECRET"
    原例外 = 例外型別(marker)

    def 注入(值):
        raise 原例外

    monkeypatch.setattr(協定模組, "_是識別", 注入)
    with pytest.raises(例外型別) as 捕捉:
        建立DTO(marker)
    assert 捕捉.value is 原例外
    assert type(捕捉.value) is 例外型別 and 捕捉.value.args == (marker,)
    assert "__post_init__" in _斷言協定traceback已清除(捕捉.value, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_重建DTO驗證器控制流清除所有協定frame(monkeypatch, 例外型別):
    marker = "REBUILD_CONTROL_SECRET"
    原例外 = 例外型別(marker)
    原驗證器 = 協定模組._是識別
    標記驗證次數 = 0

    def 注入(值):
        nonlocal 標記驗證次數
        if 值 is marker:
            標記驗證次數 += 1
            if 標記驗證次數 == 2:
                raise 原例外
        return 原驗證器(值)

    monkeypatch.setattr(協定模組, "_是識別", 注入)
    原始 = _偽造快照((_偽造技能(名稱=marker),))
    with pytest.raises(例外型別) as 捕捉:
        安全查詢規劃權限(_查詢器(原始), "owner-1")
    assert 標記驗證次數 == 2
    assert 捕捉.value is 原例外
    assert type(捕捉.value) is 例外型別 and 捕捉.value.args == (marker,)
    框架名稱 = _斷言協定traceback已清除(捕捉.value, marker)
    assert "安全查詢規劃權限" in 框架名稱 and "__post_init__" in 框架名稱


def test_重建驗證器自訂BaseException仍固定無鏈且清除區域(monkeypatch):
    marker = "REBUILD_BASE_SECRET"
    原驗證器 = 協定模組._是識別
    標記驗證次數 = 0

    def 注入(值):
        nonlocal 標記驗證次數
        if 值 is marker:
            標記驗證次數 += 1
            if 標記驗證次數 == 2:
                raise _敵對基礎例外(marker)
        return 原驗證器(值)

    monkeypatch.setattr(協定模組, "_是識別", 注入)
    原始 = _偽造快照((_偽造技能(名稱=marker),))
    with pytest.raises(規劃權限查詢錯誤) as 捕捉:
        安全查詢規劃權限(_查詢器(原始), "owner-1")
    assert 標記驗證次數 == 2
    assert type(捕捉.value) is 規劃權限查詢錯誤
    assert 捕捉.value.args == ("無法取得規劃權限快照",)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    assert "安全查詢規劃權限" in _斷言協定traceback已清除(捕捉.value, marker)
