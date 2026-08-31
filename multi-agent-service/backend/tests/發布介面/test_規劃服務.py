"""Planner 草稿 aggregate 的擁有者、期限與不可執行契約測試。"""

import concurrent.futures
import threading

import pytest

from 繁中代理.發布介面.規劃 import 綱要 as 綱要模組
from 繁中代理.發布介面.規劃.綱要 import 規劃服務, 草稿不可執行錯誤, 草稿存取錯誤


class _惡意字串(str):
    呼叫次數 = 0

    def strip(self, *args, **kwargs):
        type(self).呼叫次數 += 1
        return super().strip(*args, **kwargs)

    def __hash__(self):
        type(self).呼叫次數 += 1
        return super().__hash__()


class _惡意整數(int):
    呼叫次數 = 0

    def __float__(self):
        type(self).呼叫次數 += 1
        return super().__float__()


class _惡意浮點(float):
    呼叫次數 = 0

    def __float__(self):
        type(self).呼叫次數 += 1
        return super().__float__()


class _惡意串列(list):
    呼叫次數 = 0

    def __iter__(self):
        type(self).呼叫次數 += 1
        return super().__iter__()


class _惡意字典(dict):
    呼叫次數 = 0

    def items(self):
        type(self).呼叫次數 += 1
        return super().items()


class _工廠基底錯誤(BaseException):
    pass


def _建立服務與草稿():
    服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-001")
    綱要 = {"steps": [{"name": "蒐集資料"}], "enabled": True}
    草稿 = 服務.建立草稿("owner-a", "建立客服代理", 綱要, 現在=100)
    return 服務, 草稿, 綱要


def _斷言綱要frame未保留(錯誤, *敏感值):
    for frame, _ in __import__("traceback").walk_tb(錯誤.__traceback__):
        if frame.f_code.co_filename.endswith("綱要.py"):
            for 區域值 in frame.f_locals.values():
                assert all(區域值 is not 敏感值項目 for 敏感值項目 in 敏感值)


def test_草稿有獨立身分且不是端點或版本():
    """Planner 產物只具 draft identity，不可冒充 endpoint/version。"""
    _, 草稿, _ = _建立服務與草稿()

    assert 草稿.草稿識別碼 == "draft-001"
    assert 草稿.擁有者識別碼 == "owner-a"
    assert 草稿.狀態 == "draft"
    assert 草稿.建立時間 == 100
    assert 草稿.到期時間 == 160
    assert not hasattr(草稿, "端點識別碼")
    assert not hasattr(草稿, "版本識別碼")


def test_草稿綱要採嚴格JSON快照且不受呼叫端修改():
    """Aggregate 不持有呼叫端可變 JSON，讀取結果也只是副本。"""
    服務, 草稿, 原始綱要 = _建立服務與草稿()
    原始綱要["steps"][0]["name"] = "竄改"
    讀取綱要 = 草稿.綱要
    讀取綱要["steps"].append({"name": "再竄改"})

    assert 草稿.綱要 == {"enabled": True, "steps": [{"name": "蒐集資料"}]}
    assert 服務.讀取草稿("owner-a", "draft-001", 現在=159).綱要 == 草稿.綱要


def test_綱要只canonicalize可信副本且同步修改呼叫端不影響快照(monkeypatch):
    """Canonicalizer 阻塞期間修改原物件，也不能產生 preflight 後的 TOCTOU。"""
    原建立正規JSON = 綱要模組.建立正規JSON
    已進入 = threading.Event()
    可繼續 = threading.Event()
    原綱要 = {"steps": ["原值"]}

    def 阻塞canonicalizer(值):
        assert 值 is not 原綱要 and 值["steps"] is not 原綱要["steps"]
        已進入.set()
        assert 可繼續.wait(2)
        return 原建立正規JSON(值)

    monkeypatch.setattr(綱要模組, "建立正規JSON", 阻塞canonicalizer)
    服務 = 規劃服務(識別碼產生器=lambda: "draft-race")
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(服務.建立草稿, "owner", "需求", 原綱要, 現在=1)
        assert 已進入.wait(2)
        原綱要["steps"][0] = "競態竄改"
        可繼續.set()
        草稿 = future.result(timeout=2)

    assert 草稿.綱要 == {"steps": ["原值"]}


def test_草稿不可執行且回傳固定錯誤():
    """即使 owner 與期限有效，也不可從 draft 進入 invoke。"""
    服務, _, _ = _建立服務與草稿()

    with pytest.raises(草稿不可執行錯誤, match="^規劃草稿不可執行$") as 錯誤:
        服務.呼叫草稿("owner-a", "draft-001", 現在=159)

    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None


def test_建立草稿拒絕無限期限():
    """極大但有限的 now/TTL 相加溢位時不可建立永不到期草稿。"""
    服務 = 規劃服務(存續秒數=1e308)
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$"):
        服務.建立草稿("owner-a", "需求", {}, 現在=1e308)


def test_建構子拒絕超大精確整數TTL且固定錯誤不保留輸入():
    超大TTL = 10**400
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$") as 錯誤:
        規劃服務(存續秒數=超大TTL)

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    _斷言綱要frame未保留(錯誤.value, 超大TTL)


def test_建立拒絕超大精確整數現在且固定錯誤清除敏感locals():
    超大現在 = 10**400
    marker = "OVERSIZED-CREATE-MARKER"
    綱要 = {"marker": marker}
    服務 = 規劃服務()
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$") as 錯誤:
        服務.建立草稿(marker, marker, 綱要, 現在=超大現在)

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    _斷言綱要frame未保留(錯誤.value, 超大現在, marker, 綱要)
    assert 服務._草稿 == {}


@pytest.mark.parametrize("操作", ["讀取", "更新", "呼叫"])
def test_草稿操作拒絕超大精確整數現在且不改aggregate或建立快照(monkeypatch, 操作):
    服務, 草稿, _ = _建立服務與草稿()
    原aggregate = dict(服務._草稿)
    超大現在 = 10**400
    快照呼叫次數 = 0

    def 不應建立快照(_綱要):
        nonlocal 快照呼叫次數
        快照呼叫次數 += 1
        return "{}"

    monkeypatch.setattr(綱要模組, "_建立綱要快照", 不應建立快照)
    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$") as 錯誤:
        if 操作 == "讀取":
            服務.讀取草稿("owner-a", "draft-001", 現在=超大現在)
        elif 操作 == "更新":
            服務.更新草稿("owner-a", "draft-001", {"hostile": object()}, 現在=超大現在)
        else:
            服務.呼叫草稿("owner-a", "draft-001", 現在=超大現在)

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    _斷言綱要frame未保留(錯誤.value, 超大現在)
    assert 快照呼叫次數 == 0
    assert 服務._草稿 == 原aggregate == {"draft-001": 草稿}
    assert 服務._鎖.acquire(blocking=False)
    服務._鎖.release()


@pytest.mark.parametrize("操作", ["讀取", "更新", "呼叫"])
def test_所有草稿操作皆隔離owner且錯誤不洩漏識別資訊(操作):
    """外部 owner 對 read/update/invoke 都只能得到同一不可用錯誤。"""
    服務, _, _ = _建立服務與草稿()
    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$") as 錯誤:
        if 操作 == "讀取":
            服務.讀取草稿("other-owner-SECRET", "draft-001", 現在=120)
        elif 操作 == "更新":
            服務.更新草稿("other-owner-SECRET", "draft-001", {"x": 1}, 現在=120)
        else:
            服務.呼叫草稿("other-owner-SECRET", "draft-001", 現在=120)

    assert "other-owner-SECRET" not in str(錯誤.value)
    assert "draft-001" not in str(錯誤.value)


@pytest.mark.parametrize("操作", ["讀取", "更新", "呼叫"])
def test_外部owner未授權不得藉未來時間刪除草稿(操作):
    """授權必須先於 expiry mutation，外部探測後 owner 仍可在有效時間使用。"""
    服務, _, _ = _建立服務與草稿()
    with pytest.raises(草稿存取錯誤):
        if 操作 == "讀取":
            服務.讀取草稿("other-owner", "draft-001", 現在=999)
        elif 操作 == "更新":
            服務.更新草稿("other-owner", "draft-001", {}, 現在=999)
        else:
            服務.呼叫草稿("other-owner", "draft-001", 現在=999)

    assert len(服務._草稿) == 1
    assert 服務.讀取草稿("owner-a", "draft-001", 現在=159).草稿識別碼 == "draft-001"


def test_攻擊者型別在任何strip_hash_math前即遭拒絕():
    """公開輸入僅接受精確 built-in 型別，不呼叫 subclass 覆寫方法。"""
    _惡意字串.呼叫次數 = _惡意整數.呼叫次數 = _惡意浮點.呼叫次數 = 0
    服務 = 規劃服務(存續秒數=60)
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$"):
        服務.建立草稿(_惡意字串("owner"), "需求", {}, 現在=1)
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$"):
        服務.建立草稿("owner", "需求", {}, 現在=_惡意整數(1))
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$"):
        規劃服務(存續秒數=_惡意浮點(60))
    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
        服務.讀取草稿("owner", _惡意字串("missing"), 現在=1)
    assert (_惡意字串.呼叫次數, _惡意整數.呼叫次數, _惡意浮點.呼叫次數) == (0, 0, 0)


@pytest.mark.parametrize(
    ("惡意值", "陷阱"),
    [
        (_惡意串列([1]), _惡意串列),
        (_惡意字典({"x": 1}), _惡意字典),
        ({"nested": [_惡意字串("x")]}, _惡意字串),
        ({"nested": [_惡意整數(1)]}, _惡意整數),
        ({"nested": [_惡意浮點(1.0)]}, _惡意浮點),
    ],
)
def test_綱要遞迴拒絕JSON_subclass且不觸發陷阱(惡意值, 陷阱):
    """快照 preflight 遞迴限定 exact JSON built-ins，bool 仍為合法 JSON。"""
    陷阱.呼叫次數 = 0
    服務 = 規劃服務(存續秒數=60)
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$"):
        服務.建立草稿("owner", "需求", {"ok": True, "payload": 惡意值}, 現在=1)
    assert 陷阱.呼叫次數 == 0


def test_識別碼工廠拒絕subclass且不strip_hash並清除traceback_marker():
    """不可信工廠回值先作 exact-str gate，公開錯誤 frame 不保留 marker。"""
    marker = _惡意字串("FACTORY-MARKER")
    _惡意字串.呼叫次數 = 0
    服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: marker)
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$") as 錯誤:
        服務.建立草稿("owner", "需求", {}, 現在=1)
    assert _惡意字串.呼叫次數 == 0
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    for frame, _ in __import__("traceback").walk_tb(錯誤.value.__traceback__):
        if frame.f_code.co_filename.endswith("綱要.py"):
            assert marker not in frame.f_locals.values()


def test_識別碼工廠自訂BaseException正規化且不鏈結或洩漏():
    marker = "BASE-EXCEPTION-MARKER"

    def 工廠():
        raise _工廠基底錯誤(marker)

    服務 = 規劃服務(存續秒數=60, 識別碼產生器=工廠)
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$") as 錯誤:
        服務.建立草稿("owner", "需求", {}, 現在=1)
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert marker not in str(錯誤.value)
    for frame, _ in __import__("traceback").walk_tb(錯誤.value.__traceback__):
        if frame.f_code.co_filename.endswith("綱要.py"):
            assert marker not in frame.f_locals.values()


@pytest.mark.parametrize("情境", ["綱要", "工廠例外", "工廠回值", "重複", "期限溢位"])
def test_所有建立固定錯誤清除綱要模組frame敏感locals(情境):
    """每個 normalized creation failure 都不保留 owner/request/raw/copy/id marker。"""
    marker = f"CREATION-{情境}-MARKER"

    def 例外工廠():
        raise _工廠基底錯誤(marker)

    if 情境 == "工廠例外":
        服務 = 規劃服務(識別碼產生器=例外工廠)
    elif 情境 == "工廠回值":
        服務 = 規劃服務(識別碼產生器=lambda: _惡意字串(marker))
    else:
        服務 = 規劃服務(存續秒數=1e308 if 情境 == "期限溢位" else 60, 識別碼產生器=lambda: marker)
    if 情境 == "重複":
        服務.建立草稿("safe-owner", "safe-request", {}, 現在=1)
    outline = {"marker": marker, "bad": object()} if 情境 == "綱要" else {"marker": marker}
    with pytest.raises(ValueError, match="^規劃草稿輸入無效$") as 錯誤:
        服務.建立草稿(marker, marker, outline, 現在=1e308 if 情境 == "期限溢位" else 1)

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    for frame, _ in __import__("traceback").walk_tb(錯誤.value.__traceback__):
        if frame.f_code.co_filename.endswith("綱要.py"):
            assert marker not in repr(frame.f_locals)


@pytest.mark.parametrize("控制流", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_識別碼工廠保留Python控制流並清除所有輸入locals(控制流):
    marker = f"{控制流.__name__}-CONTROL-MARKER"
    原始引數 = (marker, 7)

    def 工廠():
        raise 控制流(*原始引數)

    服務 = 規劃服務(存續秒數=60, 識別碼產生器=工廠)
    with pytest.raises(BaseException) as 錯誤:
        服務.建立草稿(marker, marker, {"marker": marker}, 現在=1)

    assert type(錯誤.value) is 控制流
    assert 錯誤.value.args == 原始引數
    生產frames = [
        frame
        for frame, _ in __import__("traceback").walk_tb(錯誤.value.__traceback__)
        if frame.f_code.co_filename.endswith("綱要.py")
    ]
    assert 生產frames
    for frame in 生產frames:
        assert marker not in repr(frame.f_locals)


def test_更新保留草稿身分owner與期限並建立新快照():
    """更新仍是同一 owner 的 draft，不會產生 endpoint/version。"""
    服務, 原草稿, _ = _建立服務與草稿()

    新草稿 = 服務.更新草稿("owner-a", "draft-001", {"steps": []}, 現在=159)

    assert 新草稿 is not 原草稿
    assert 新草稿.草稿識別碼 == 原草稿.草稿識別碼
    assert 新草稿.擁有者識別碼 == 原草稿.擁有者識別碼
    assert 新草稿.到期時間 == 原草稿.到期時間
    assert 新草稿.綱要 == {"steps": []}


def test_相同識別碼並行建立只會原子插入一次():
    """工廠碰撞時 ID absence check 與 insert 必須在同一服務鎖內。"""
    同步點 = threading.Barrier(2)

    def 工廠():
        同步點.wait(timeout=2)
        return "collision-id"

    服務 = 規劃服務(識別碼產生器=工廠)

    def 建立(序號):
        try:
            return 服務.建立草稿(f"owner-{序號}", "需求", {"n": 序號}, 現在=1)
        except ValueError as 錯誤:
            return 錯誤

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as 執行器:
        結果 = list(執行器.map(建立, (1, 2)))

    assert sum(type(值).__name__ == "規劃草稿" for 值 in 結果) == 1
    assert sum(type(值) is ValueError for 值 in 結果) == 1
    assert len(服務._草稿) == 1


@pytest.mark.parametrize("錯誤", [RuntimeError("INSERT-MARKER", 7), KeyboardInterrupt("INSERT-CONTROL", 9)])
def test_backing_mapping_insert失敗不殘留且保留例外identity_args(錯誤):
    """Backing mapping 自身拒絕 insert 時，不得留下半成品或改寫原例外。"""
    class _插入失敗字典(dict):
        def __setitem__(self, key, value):
            raise 錯誤

    服務 = 規劃服務(識別碼產生器=lambda: "insert-failure")
    服務._草稿 = _插入失敗字典()

    with pytest.raises(type(錯誤)) as 捕捉:
        服務.建立草稿("owner", "需求", {"safe": True}, 現在=1)

    assert 捕捉.value is 錯誤
    assert 捕捉.value.args == 錯誤.args
    assert 服務._草稿 == {}


@pytest.mark.parametrize("錯誤", [RuntimeError("REBUILD-MARKER", 3), KeyboardInterrupt("REBUILD-CONTROL", 5)])
def test_insert後公開重建失敗會identity安全回滾且保留例外(monkeypatch, 錯誤):
    """公開 DTO 重建失敗時，剛插入的 exact aggregate 必須在方法返回前移除。"""
    服務 = 規劃服務(識別碼產生器=lambda: "rebuild-failure")

    def 重建失敗(_草稿):
        raise 錯誤

    monkeypatch.setattr(綱要模組, "_必須重建公開草稿", 重建失敗)
    with pytest.raises(type(錯誤)) as 捕捉:
        服務.建立草稿("owner", "需求", {"safe": True}, 現在=1)

    assert 捕捉.value is 錯誤
    assert 捕捉.value.args == 錯誤.args
    assert 服務._草稿 == {}


def test_insert後重建失敗rollback不刪除並行replacement(monkeypatch):
    """Rollback 必須以 object identity 作 CAS，不能只憑相同 draft id 刪除別人的 replacement。"""
    服務 = 規劃服務(識別碼產生器=lambda: "replaced-draft")
    替代 = []
    原錯誤 = RuntimeError("REPLACEMENT-MUST-SURVIVE")

    def 替換後失敗(剛插入):
        新值 = __import__("dataclasses").replace(剛插入, 原始需求="replacement")
        with 服務._鎖:
            assert 服務._草稿[剛插入.草稿識別碼] is 剛插入
            服務._草稿[剛插入.草稿識別碼] = 新值
        替代.append(新值)
        raise 原錯誤

    monkeypatch.setattr(綱要模組, "_必須重建公開草稿", 替換後失敗)
    with pytest.raises(RuntimeError) as 捕捉:
        服務.建立草稿("owner", "需求", {"safe": True}, 現在=1)

    assert 捕捉.value is 原錯誤
    assert 服務._草稿 == {"replaced-draft": 替代[0]}


def test_更新快照與到期刪除交錯不會復活草稿(monkeypatch):
    """更新在 snapshot 完成後鎖內 gate+replace，過期刪除不能被舊讀取復活。"""
    服務, _, _ = _建立服務與草稿()
    原建立正規JSON = 綱要模組.建立正規JSON
    已進入 = threading.Event()
    可繼續 = threading.Event()

    def 阻塞canonicalizer(值):
        已進入.set()
        assert 可繼續.wait(2)
        return 原建立正規JSON(值)

    monkeypatch.setattr(綱要模組, "建立正規JSON", 阻塞canonicalizer)
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(服務.更新草稿, "owner-a", "draft-001", {"new": True}, 現在=159)
        assert 已進入.wait(2)
        with pytest.raises(草稿存取錯誤):
            服務.讀取草稿("owner-a", "draft-001", 現在=160)
        可繼續.set()
        with pytest.raises(草稿存取錯誤):
            future.result(timeout=2)

    assert 服務._草稿 == {}


@pytest.mark.parametrize("現在", [160, 161])
def test_到期邊界確定且所有後續操作fail_closed(現在):
    """now >= expires_at 即失效，過期後不得更新或利用 invoke 探測。"""
    服務, _, _ = _建立服務與草稿()

    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
        服務.讀取草稿("owner-a", "draft-001", 現在=現在)
    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
        服務.更新草稿("owner-a", "draft-001", {"secret": "不可洩漏"}, 現在=現在)
    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
        服務.呼叫草稿("owner-a", "draft-001", 現在=現在)
