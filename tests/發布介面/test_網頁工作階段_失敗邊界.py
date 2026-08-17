"""AUTH A02 真實 SQLite intermediate failure 與 traceback privacy 回歸。"""
from pathlib import Path
import os
import sqlite3

import pytest
from fastapi import HTTPException, Request, Response

import 繁中代理.發布介面.網頁工作階段 as 工作階段模組
from 繁中代理.使用者 import 使用者庫

from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import (
    網頁CSRF無效, 網頁使用者, 網頁未授權, 網頁工作階段服務,
    網頁工作階段結果, 網頁認證不可用,
)
from 繁中代理.發布介面.路由 import 建立CSRF相依項

標記 = "ROUND2-SECRET-MARKER"


class 自訂基底錯誤(BaseException):
    """驗證非 KISG BaseException 仍固定正規化。"""


class 連線代理:
    """保留真實 SQLite 語意，只在指定 production operation 後注入失敗。"""
    def __init__(self, 連線, 階段, 錯誤, 提交後=None):
        self._連線 = 連線
        self._階段 = 階段
        self._錯誤 = 錯誤
        self._提交後 = 提交後
        self._命中 = 0

    def __setattr__(self, 名稱, 值):
        if 名稱 == "row_factory" and "_連線" in self.__dict__:
            self._連線.row_factory = 值
        object.__setattr__(self, 名稱, 值)

    @property
    def in_transaction(self):
        return self._連線.in_transaction

    def execute(self, sql, 參數=()):
        operation = sql.split()[0].upper()
        if self._階段 == "COMMIT_AFTER" and operation == "COMMIT":
            結果 = self._連線.execute(sql, 參數)
            self._命中 += 1
            if self._提交後 is not None:
                self._提交後()
            raise self._錯誤
        if self._階段 == operation:
            self._命中 += 1
            raise self._錯誤
        return self._連線.execute(sql, 參數)

    def close(self):
        self._連線.close()
        if self._階段 == "CLOSE":
            self._命中 += 1
            raise self._錯誤


class 資料列代理:
    """保留 sqlite3.Row backing，讓 row access 成為真實 intermediate failpoint。"""
    def __init__(self, 資料列, 階段, 錯誤, 擁有者):
        self._資料列, self._階段, self._錯誤, self._擁有者 = 資料列, 階段, 錯誤, 擁有者
    def __getitem__(self, 鍵):
        if self._階段 == "ROW":
            self._擁有者._命中 += 1
            raise self._錯誤
        return self._資料列[鍵]


class 游標代理:
    """保留 sqlite3.Cursor backing，覆蓋 fetch/rowcount intermediate frames。"""
    def __init__(self, 游標, 階段, 錯誤, 擁有者):
        self._游標, self._階段, self._錯誤, self._擁有者 = 游標, 階段, 錯誤, 擁有者
    def fetchone(self):
        if self._階段 == "FETCH":
            self._擁有者._命中 += 1
            raise self._錯誤
        資料列 = self._游標.fetchone()
        return 資料列代理(資料列, self._階段, self._錯誤, self._擁有者) if 資料列 is not None else None
    @property
    def rowcount(self):
        if self._階段 == "ROWCOUNT":
            self._擁有者._命中 += 1
            raise self._錯誤
        return self._游標.rowcount


class 完整連線代理(連線代理):
    """辨認每個 SQL boundary，且 execute 成功後仍回傳 cursor proxy。"""
    def execute(self, sql, 參數=()):
        operation = sql.split()[0].upper()
        階段 = operation
        if operation == "PRAGMA":
            階段 = "PRAGMA"
        elif operation == "UPDATE" and "session_token_hash" in sql:
            階段 = "FIXATION"
        elif operation == "UPDATE" and "csrf_token_hash=?" in sql:
            階段 = "CAS"
        if self._階段 == "ROLLBACK" and 階段 == "CAS":
            raise RuntimeError(f"{標記}-PRIMARY")
        if self._階段 == 階段:
            self._命中 += 1
            raise self._錯誤
        游標 = self._連線.execute(sql, 參數)
        if self._階段 in ("FETCH", "ROW", "ROWCOUNT") and operation in ("SELECT", "UPDATE"):
            return 游標代理(游標, self._階段, self._錯誤, self)
        return 游標


def _建立資料庫(tmp_path):
    """建立含真實 AUTH A02 migration 的 marker-bearing DB 路徑。"""
    路徑 = tmp_path / f"{標記}.sqlite3"
    使用者們 = 使用者庫(路徑)
    alice = 使用者們.建立使用者(f"alice-{標記}", "password-marker")
    migration = (Path(__file__).parents[2] / "繁中代理/發布介面/遷移/0005_建立網頁工作階段.sql").read_text()
    使用者們.連線.executescript(migration)
    使用者們.連線.close()
    return 路徑, alice


def _含標記(值, 已見=None):
    """只走已知 DTO/service/container/exception/path，避免 repr 假陰性。"""
    if 已見 is None:
        已見 = set()
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is bytes:
        return 標記.encode() in 值
    if isinstance(值, Path):
        return 標記 in str(值)
    if type(值) in (tuple, list, set, frozenset):
        return any(_含標記(item, 已見) for item in 值)
    if type(值) is dict:
        return any(_含標記(key, 已見) or _含標記(item, 已見) for key, item in 值.items())
    if isinstance(值, BaseException):
        return (_含標記(值.args, 已見) or _含標記(值.__cause__, 已見)
                or _含標記(值.__context__, 已見))
    if type(值) in (網頁使用者, 網頁工作階段結果, 網頁工作階段服務):
        names = getattr(type(值), "__slots__", ()) or getattr(值, "__dict__", {})
        return any(_含標記(getattr(值, name), 已見) for name in names if hasattr(值, name))
    return False


def _斷言乾淨(錯誤):
    """檢查 exception graph 與每個 production traceback local。"""
    assert not _含標記(錯誤)
    assert 錯誤.__cause__ is None and 錯誤.__context__ is None
    框 = 錯誤.__traceback__
    while 框 is not None:
        if 框.tb_frame.f_code.co_filename.endswith("網頁工作階段.py"):
            for 值 in tuple(框.tb_frame.f_locals.values()):
                assert not _含標記(值, set()), 框.tb_frame.f_code.co_name
        框 = 框.tb_next


def test_管理操作COMMIT_ack遺失重建原successor且舊csrf失效(tmp_path, monkeypatch):
    """CAS已durable但COMMIT回應遺失時，不可503鎖死或再配置另一個successor。"""
    路徑, alice = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE users SET roles_json='[\"admin\"]' WHERE id=?", (alice["id"],))
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], "alice", "admin"))
    assert type(發行.工作階段權杖) is type(發行.CSRF權杖) is str
    原連線 = sqlite3.connect
    代理清單 = []

    def 連線工廠(*args, **kwargs):
        連線 = 原連線(*args, **kwargs)
        if not 代理清單:
            代理 = 連線代理(連線, "COMMIT_AFTER", RuntimeError("lost-ack"))
            代理清單.append(代理)
            return 代理
        return 連線

    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    successor = 服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)
    assert 代理清單[0]._命中 == 1
    assert successor.CSRF權杖 and successor.CSRF權杖 != 發行.CSRF權杖
    with pytest.raises(網頁CSRF無效):
        服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)
    assert 服務.授權管理操作(
        發行.工作階段權杖, successor.CSRF權杖,
    ).CSRF權杖 not in (None, successor.CSRF權杖)


def test_COMMIT_ack遺失後fresh_reconcile普通close失敗不覆蓋durable_successor(tmp_path, monkeypatch):
    路徑, alice = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE users SET roles_json='[\"admin\"]' WHERE id=?", (alice["id"],))
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], "alice", "admin"))
    assert isinstance(發行.工作階段權杖, str) and isinstance(發行.CSRF權杖, str)
    原連線 = sqlite3.connect
    次數 = 0

    def 連線工廠(*args, **kwargs):
        nonlocal 次數
        次數 += 1
        連線 = 原連線(*args, **kwargs)
        if 次數 == 1:
            return 連線代理(連線, "COMMIT_AFTER", RuntimeError("lost-ack"))
        return 連線代理(連線, "CLOSE", RuntimeError("reconcile-close"))

    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    successor = 服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)
    assert successor.CSRF權杖 not in (None, 發行.CSRF權杖)


def test_COMMIT_ack遺失後fresh_reconcile_control_close保留同一identity(tmp_path, monkeypatch):
    路徑, alice = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE users SET roles_json='[\"admin\"]' WHERE id=?", (alice["id"],))
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], "alice", "admin"))
    assert isinstance(發行.工作階段權杖, str) and isinstance(發行.CSRF權杖, str)
    原連線 = sqlite3.connect
    控制 = KeyboardInterrupt("reconcile-close-control")
    次數 = 0

    def 連線工廠(*args, **kwargs):
        nonlocal 次數
        次數 += 1
        連線 = 原連線(*args, **kwargs)
        if 次數 == 1:
            return 連線代理(連線, "COMMIT_AFTER", RuntimeError("lost-ack"))
        return 連線代理(連線, "CLOSE", 控制)

    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    with pytest.raises(KeyboardInterrupt) as captured:
        服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)
    assert captured.value is 控制


def test_disabled撤銷COMMIT_ack遺失仍永久失效(tmp_path, monkeypatch):
    """disabled-owner revocation已durable時，ack遺失仍回401且re-enable不可復活。"""
    路徑, alice = _建立資料庫(tmp_path)
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], "alice", "admin"))
    assert type(發行.工作階段權杖) is type(發行.CSRF權杖) is str
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE users SET disabled=1 WHERE id=?", (alice["id"],))
    原連線 = sqlite3.connect
    代理清單 = []

    def 連線工廠(*args, **kwargs):
        連線 = 原連線(*args, **kwargs)
        if not 代理清單:
            代理 = 連線代理(連線, "COMMIT_AFTER", RuntimeError("lost-revoke-ack"))
            代理清單.append(代理)
            return 代理
        return 連線

    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    with pytest.raises(網頁未授權):
        服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)
    with 原連線(路徑) as 連線:
        assert 連線.execute(
            "SELECT revoked_at FROM web_sessions WHERE id=?", (發行.識別碼,),
        ).fetchone()[0] is not None
        連線.execute("UPDATE users SET disabled=0 WHERE id=?", (alice["id"],))
    with pytest.raises(網頁未授權):
        服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)


def test_COMMIT_ack遺失後canonical_path換成不同inode不得以外來graph確認(tmp_path, monkeypatch):
    """fresh reconciliation必須綁定transaction opener凍結pin，而非只信目前pathname。"""
    路徑, alice = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE users SET roles_json='[\"admin\"]' WHERE id=?", (alice["id"],))
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], "alice", "admin"))
    assert isinstance(發行.工作階段權杖, str)
    assert isinstance(發行.CSRF權杖, str)
    replacement = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(路徑) as source, sqlite3.connect(replacement) as target:
        source.backup(target)
    owner = tmp_path / "owner-committed.sqlite3"
    原連線 = sqlite3.connect
    代理清單 = []

    def 換成外來inode():
        os.replace(路徑, owner)
        os.replace(replacement, 路徑)

    def 連線工廠(*args, **kwargs):
        連線 = 原連線(*args, **kwargs)
        if not 代理清單:
            代理 = 連線代理(
                連線, "COMMIT_AFTER", RuntimeError("lost-ack"), 換成外來inode,
            )
            代理清單.append(代理)
            return 代理
        return 連線

    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    with pytest.raises(網頁認證不可用, match="^auth_unavailable$"):
        服務.授權管理操作(發行.工作階段權杖, 發行.CSRF權杖)
    assert os.stat(owner).st_ino != os.stat(路徑).st_ino


def test_traceback_scanner_positive_oracle(tmp_path):
    """證明 scanner 確實能由 service self 與 DTO 找到 marker。"""
    服務 = 網頁工作階段服務(tmp_path / f"{標記}.sqlite3")
    使用者 = 網頁使用者(f"u-{標記}", "alice", "member")
    assert _含標記(服務) and _含標記(網頁工作階段結果("id", 使用者, 標記, 標記))


@pytest.mark.parametrize("錯誤型別", [RuntimeError, 自訂基底錯誤])
def test_發行generator普通與custom失敗固定且traceback乾淨(tmp_path, 錯誤型別):
    """production generator frame 的 ordinary/custom BaseException 都固定且無鏈。"""
    路徑, alice = _建立資料庫(tmp_path)
    def 失敗工廠():
        raise 錯誤型別(標記)
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0, 密鑰工廠=失敗工廠)
    with pytest.raises(網頁認證不可用, match="^auth_unavailable$") as 捕獲:
        服務.發行(網頁使用者(alice["id"], f"alice-{標記}", "member"), 使用者代理=標記)
    _斷言乾淨(捕獲.value)


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_發行generator_KISG保留identity_args且traceback乾淨(tmp_path, 控制型別):
    """KISG 不被 auth error 吞掉且所有 production frames 已清值。"""
    路徑, alice = _建立資料庫(tmp_path)
    控制 = 控制型別("control-flow")
    def 失敗工廠():
        raise 控制
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0, 密鑰工廠=失敗工廠)
    with pytest.raises(控制型別) as 捕獲:
        服務.發行(網頁使用者(alice["id"], f"alice-{標記}", "member"), 使用者代理=標記)
    assert 捕獲.value is 控制 and 捕獲.value.args == ("control-flow",)
    _斷言乾淨(捕獲.value)


@pytest.mark.parametrize("階段", ["BEGIN", "INSERT", "COMMIT"])
def test_發行真實SQLite階段普通失敗固定且rollback_close不外洩(tmp_path, monkeypatch, 階段):
    """open 後 BEGIN/INSERT/COMMIT failpoint 走真實 production transaction。"""
    路徑, alice = _建立資料庫(tmp_path)
    原連線 = sqlite3.connect
    代理清單 = []
    def 連線工廠(*args, **kwargs):
        代理 = 連線代理(原連線(*args, **kwargs), 階段, RuntimeError(f"{標記}-{階段}"))
        代理清單.append(代理)
        return 代理
    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    with pytest.raises(網頁認證不可用) as 捕獲:
        網頁工作階段服務(路徑, 時鐘=lambda: 1000.0).發行(
            網頁使用者(alice["id"], f"alice-{標記}", "member"), 使用者代理=標記)
    assert代理 = 代理清單[0]
    assert assert代理._命中 == 1
    _斷言乾淨(捕獲.value)


@pytest.mark.parametrize(
    "錯誤型別", [RuntimeError, 自訂基底錯誤, KeyboardInterrupt, SystemExit, GeneratorExit],
)
@pytest.mark.parametrize(
    "階段", ["OPEN", "PRAGMA", "FETCH", "ROW", "FIXATION", "CAS", "ROWCOUNT", "ROLLBACK", "CLOSE"],
)
def test_交易失敗矩陣覆蓋真實中介與例外家族(tmp_path, monkeypatch, 錯誤型別, 階段):
    """open/setup/read/fixation/CAS/cleanup 對 ordinary、custom、KISG 都有真實證據。"""
    路徑, alice = _建立資料庫(tmp_path)
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    使用者 = 網頁使用者(alice["id"], f"alice-{標記}", "member")
    發行 = 服務.發行(使用者)
    assert 發行.工作階段權杖 is not None and 發行.CSRF權杖 is not None
    工作階段權杖, CSRF權杖 = 發行.工作階段權杖, 發行.CSRF權杖
    原連線 = sqlite3.connect
    代理清單 = []
    命中 = []
    是控制流程 = issubclass(錯誤型別, (KeyboardInterrupt, SystemExit, GeneratorExit))
    錯誤訊息 = f"control-{階段}" if 是控制流程 else f"{標記}-{階段}"
    注入錯誤 = 錯誤型別(錯誤訊息)

    def 連線工廠(*args, **kwargs):
        if 階段 == "OPEN":
            命中.append("OPEN")
            raise 注入錯誤
        代理 = 完整連線代理(原連線(*args, **kwargs), 階段, 注入錯誤)
        代理清單.append(代理)
        return 代理

    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    if 階段 == "FIXATION":
        def 呼叫():
            return 服務.發行(使用者, 工作階段權杖, 使用者代理=標記)
    else:
        def 呼叫():
            return 服務.輪替(工作階段權杖, CSRF權杖)
    if 是控制流程:
        with pytest.raises(錯誤型別) as 捕獲:
            呼叫()
        assert 捕獲.value is 注入錯誤 and 捕獲.value.args == (錯誤訊息,)
        _斷言乾淨(捕獲.value)
    elif 階段 == "CLOSE":
        結果 = 呼叫()
        assert type(結果) is 網頁工作階段結果 and 結果.csrf已輪替 is True
    else:
        with pytest.raises(網頁認證不可用, match="^auth_unavailable$") as 捕獲:
            呼叫()
        _斷言乾淨(捕獲.value)
    if 階段 == "OPEN":
        assert 命中 == ["OPEN"]
    else:
        assert 代理清單[0]._命中 == 1


def test_CSRF相依項將不可用精確映射503(tmp_path):
    """canonical dependency 不讓 auth_unavailable 逃成 500。"""
    路徑, _ = _建立資料庫(tmp_path)
    設定 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False)
    服務 = 網頁工作階段服務(路徑)
    def 不可用(*args):
        raise 網頁認證不可用("auth_unavailable")
    服務.輪替 = 不可用
    相依 = 建立CSRF相依項(服務, 設定)
    請求 = Request({"type": "http", "headers": [
        (b"cookie", b"published_web_session=" + b"a" * 32),
        (b"x-csrf-token", b"b" * 32),
    ]})
    with pytest.raises(HTTPException) as 捕獲:
        相依(請求, Response())
    assert 捕獲.value.status_code == 503
    assert 捕獲.value.detail == {"code": "auth_unavailable"}


@pytest.mark.parametrize("動作", ["恢復", "輪替", "撤銷"])
@pytest.mark.parametrize("階段", ["SELECT", "UPDATE", "COMMIT"])
def test_處理矩陣真實SQLite普通失敗固定且traceback乾淨(tmp_path, monkeypatch, 動作, 階段):
    """restore/consume/logout 在 read/write/commit real frames 一律固定不可用。"""
    路徑, alice = _建立資料庫(tmp_path)
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], f"alice-{標記}", "member"))
    原連線 = sqlite3.connect
    代理清單 = []
    def 連線工廠(*args, **kwargs):
        代理 = 連線代理(原連線(*args, **kwargs), 階段, RuntimeError(f"{標記}-{階段}"))
        代理清單.append(代理)
        return 代理
    monkeypatch.setattr(工作階段模組.sqlite3, "connect", 連線工廠)
    呼叫 = getattr(服務, 動作)
    with pytest.raises(網頁認證不可用) as 捕獲:
        呼叫(發行.工作階段權杖, 發行.CSRF權杖)
    assert 代理清單[0]._命中 == 1
    _斷言乾淨(捕獲.value)


def test_普通primary後rollback控制優先close控制且exact_identity(tmp_path, monkeypatch):
    """primary control > rollback control > close control 的 cleanup 次序不被 finally 覆蓋。"""
    路徑, alice = _建立資料庫(tmp_path)
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0)
    發行 = 服務.發行(網頁使用者(alice["id"], f"alice-{標記}", "member"))
    原連線 = sqlite3.connect
    回滾控制 = KeyboardInterrupt("rollback-control")
    關閉控制 = SystemExit("close-control")
    class precedence代理(連線代理):
        """在 write primary 後讓 rollback/close 各自拋 distinct control。"""
        def execute(self, sql, 參數=()):
            operation = sql.split()[0].upper()
            if operation == "UPDATE":
                raise RuntimeError(標記)
            if operation == "ROLLBACK":
                raise 回滾控制
            return self._連線.execute(sql, 參數)
        def close(self):
            self._連線.close()
            raise 關閉控制
    monkeypatch.setattr(
        工作階段模組.sqlite3, "connect",
        lambda *args, **kwargs: precedence代理(原連線(*args, **kwargs), "none", RuntimeError()),
    )
    with pytest.raises(KeyboardInterrupt) as 捕獲:
        服務.輪替(發行.工作階段權杖, 發行.CSRF權杖)
    assert 捕獲.value is 回滾控制 and 捕獲.value.args == ("rollback-control",)
    _斷言乾淨(捕獲.value)
