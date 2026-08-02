"""PUB P07 使用者交易 failpoint、控制流與生命週期安全回歸。"""
import json
import sqlite3
import pytest

from 繁中代理 import 使用者 as 使用者模組
from 繁中代理.使用者 import 使用者庫, 權限更新錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃 import 權限協調 as 協調模組
from 繁中代理.發布介面.規劃.權限協調 import (
    SQLite發布權限協調器, 發布權限協調錯誤, 鎖定確認端點可執行,
)


from 繁中代理.儲存 import 建立使用者庫


class _自訂控制(BaseException):
    pass


class _鍵盤子類(KeyboardInterrupt):
    pass


class _協調器:
    failure: BaseException | None = None

    def 協調權限變更(self, *_args):
        if self.failure is not None:
            raise self.failure


class _連線代理:
    def __init__(self, connection):
        self.connection = connection
        self.stage: str | None = None
        self.failure: BaseException | None = None
        self.rollback_failure: BaseException | None = None
        self.calls = []
        self.commit_then_raise = False
        self.closed = False
        self.close_failure: BaseException | None = None

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        kind = None
        if sql == "BEGIN IMMEDIATE": kind = "begin"
        elif sql.startswith("SELECT u.id"): kind = "select"
        elif sql.startswith("UPDATE user_settings SET"): kind = "update"
        elif sql.startswith("UPDATE published_endpoints SET status="): kind = "status-update"
        elif sql == "COMMIT": kind = "commit"
        elif sql == "ROLLBACK": kind = "rollback"
        if kind: self.calls.append(kind)
        failure = self.rollback_failure if kind == "rollback" else self.failure
        應失敗 = kind == self.stage or (kind == "rollback" and self.rollback_failure is not None)
        if 應失敗 and failure is not None and not self.commit_then_raise:
            raise failure
        result = self.connection.execute(sql, parameters)
        if 應失敗 and failure is not None:
            raise failure
        return result

    def close(self):
        if self.close_failure is not None:
            raise self.close_failure
        self.closed = True
        self.connection.close()


def _建立資料庫(tmp_path, coordinator=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "p07-control.db"
    初始化發布介面資料庫(path)
    庫 = 使用者庫(path, coordinator or SQLite發布權限協調器())
    owner = 庫.建立使用者(
        "owner", enabled_tools=["*"], enabled_skills=["*"], skill_roots=["*"]
    )["id"]
    return 庫, owner


def _加端點(庫, owner, status="active"):
    庫.連線.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    庫.連線.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) VALUES('endpoint-1',?,'account-1','demo',?,NULL,1,1)",
        (owner, status),
    )
    庫.連線.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("version-1", "endpoint-1", 1, "req", "prompt", '["skill.a"]', '["tool.P07MARK"]', "{}",
         "runtime-r1", "{}", "{}", '{"permission_revision":"perm-r1","skills":[{"name":"skill.a","content_sha256_reference":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}', None, "{}", 0, owner, 1.0),
    )
    庫.連線.execute(
        "UPDATE published_endpoints SET current_version_id='version-1' WHERE id='endpoint-1'"
    )


def _設定(庫, owner):
    connection = getattr(庫, "連線", 庫)
    value = connection.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0]
    status = connection.execute(
        "SELECT status FROM published_endpoints WHERE id='endpoint-1'"
    ).fetchone()
    return value, None if status is None else status[0]


def _含標記(value, marker, visited):
    if value is None or id(value) in visited: return False
    visited.add(id(value))
    if type(value) is str: return marker in value
    if type(value) in (tuple, list, set):
        return any(_含標記(item, marker, visited) for item in value)
    if type(value) is dict:
        for key, item in value.items():
            if _含標記(key, marker, visited) or _含標記(item, marker, visited):
                return True
        return False
    if isinstance(value, BaseException):
        return (_含標記(value.args, marker, visited)
                or _含標記(value.__cause__, marker, visited)
                or _含標記(value.__context__, marker, visited))
    try: attributes = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError): attributes = None
    return type(attributes) is dict and _含標記(attributes, marker, visited)


def test_marker_oracle會追蹤direct_dict與self_dict():
    class Holder:
        payload: dict[str, list[str]]
    holder = Holder()
    holder.payload = {"nested": ["P07MARK"]}
    assert _含標記({"nested": "P07MARK"}, "P07MARK", set())
    assert _含標記(holder, "P07MARK", set())


def test_real_sqlite儲存工廠會自動停用受影響端點(monkeypatch, tmp_path):
    path = tmp_path / "factory.db"
    初始化發布介面資料庫(path)
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    庫 = 建立使用者庫(path)
    owner = 庫.建立使用者("owner", enabled_tools=["*"])["id"]
    _加端點(庫, owner)

    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _設定(庫, owner) == ('["tool.b"]', "disabled")


def test_hook在COMMIT前結束交易不得誤報成功(tmp_path):
    class Coordinator:
        def 協調權限變更(self, connection, *_args):
            connection.execute("ROLLBACK")
            raise RuntimeError("P07MARK")
    庫, owner = _建立資料庫(tmp_path, Coordinator())
    _加端點(庫, owner)

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _設定(庫, owner) == ('["*"]', "active")


def test_ordinary_rollback失敗會關閉仍持有交易的使用者連線(tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner)
    proxy = _連線代理(庫.連線)
    proxy.stage = "update"
    proxy.failure = sqlite3.OperationalError("primary")
    proxy.rollback_failure = sqlite3.OperationalError("cleanup")
    庫.連線 = proxy  # type: ignore[assignment]

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert proxy.closed is True


def test_lifecycle在COMMIT前交易消失不得誤報成功(monkeypatch, tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner)

    def rollback_then_fail(connection):
        connection.execute("ROLLBACK")
        raise sqlite3.DatabaseError("P07MARK")

    monkeypatch.setattr(協調模組, "_驗證發布資料表", rollback_then_fail)
    with pytest.raises(發布權限協調錯誤, match="^端點狀態變更失敗$"):
        SQLite發布權限協調器().封存端點(庫.連線, owner, "endpoint-1", 2.0)
    assert _設定(庫, owner)[1] == "active"


def test_lifecycle_rollback失敗會關閉caller連線(tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner)
    proxy = _連線代理(庫.連線)
    proxy.stage = "status-update"
    proxy.failure = sqlite3.OperationalError("primary")
    proxy.rollback_failure = sqlite3.OperationalError("cleanup")

    with pytest.raises(發布權限協調錯誤, match="^端點狀態變更失敗$"):
        SQLite發布權限協調器().封存端點(proxy, owner, "endpoint-1", 2.0)  # type: ignore[arg-type]
    assert proxy.closed is True


@pytest.mark.parametrize("control", [False, True])
def test_使用者close失敗會poison且cleanup控制精確優先(control, tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner)
    proxy = _連線代理(庫.連線)
    proxy.stage = "update"
    proxy.failure = sqlite3.OperationalError("primary")
    proxy.rollback_failure = sqlite3.OperationalError("rollback")
    close_error = KeyboardInterrupt("close-control") if control else RuntimeError("close-ordinary")
    proxy.close_failure = close_error
    庫.連線 = proxy  # type: ignore[assignment]

    expected = KeyboardInterrupt if control else 權限更新錯誤
    with pytest.raises(expected) as caught:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    if control:
        assert caught.value is close_error
    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    with pytest.raises(發布權限協調錯誤, match="^發布權限協調失敗$"):
        SQLite發布權限協調器().協調權限變更(
            proxy, owner, "enabled_tools_json", ("*",), ("tool.b",), 3.0,  # type: ignore[arg-type]
        )  # type: ignore[arg-type]
    with pytest.raises(發布權限協調錯誤, match="^端點目前不可執行$"):
        鎖定確認端點可執行(proxy, "endpoint-1", "version-1")  # type: ignore[arg-type]


@pytest.mark.parametrize("control", [False, True])
def test_lifecycle_close失敗會poison且cleanup控制精確優先(control, tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner)
    proxy = _連線代理(庫.連線)
    proxy.stage = "status-update"
    proxy.failure = sqlite3.OperationalError("primary")
    proxy.rollback_failure = sqlite3.OperationalError("rollback")
    close_error = SystemExit("close-control") if control else RuntimeError("close-ordinary")
    proxy.close_failure = close_error
    expected = SystemExit if control else 發布權限協調錯誤

    with pytest.raises(expected) as caught:
        SQLite發布權限協調器().封存端點(proxy, owner, "endpoint-1", 2.0)  # type: ignore[arg-type]
    if control:
        assert caught.value is close_error
    with pytest.raises(發布權限協調錯誤, match="^端點狀態變更失敗$"):
        SQLite發布權限協調器().封存端點(proxy, owner, "endpoint-1", 3.0)  # type: ignore[arg-type]
    庫.連線 = proxy  # type: ignore[assignment]
    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])


def _確認production_frames乾淨(error, expected):
    names, traceback = set(), error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith(("使用者.py", "權限協調.py")):
            names.add(frame.f_code.co_name)
            for value in tuple(frame.f_locals.values()):
                assert not _含標記(value, "P07MARK", set()), frame.f_code.co_name
        traceback = traceback.tb_next
    assert set(expected) <= names
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.parametrize("stage", ["begin", "select", "update", "commit"])
def test_使用者交易ordinary_failpoints固定無鏈且rollback(stage, tmp_path):
    庫, owner = _建立資料庫(tmp_path); _加端點(庫, owner)
    proxy = _連線代理(庫.連線); proxy.stage = stage
    proxy.failure = sqlite3.OperationalError("P07MARK")
    庫.連線 = proxy
    with pytest.raises(權限更新錯誤, match="^權限更新失敗$") as caught:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert _設定(proxy.connection, owner) == ('["*"]', "active")
    assert proxy.calls.count("rollback") == (stage != "begin")


def test_hook_ordinary與自訂BaseException皆固定rollback(tmp_path):
    for index, failure in enumerate((RuntimeError("P07MARK"), _自訂控制("P07MARK"))):
        coordinator = _協調器(); coordinator.failure = failure
        庫, owner = _建立資料庫(tmp_path / str(index), coordinator); _加端點(庫, owner)
        with pytest.raises(權限更新錯誤, match="^權限更新失敗$") as caught:
            庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert _設定(庫, owner) == ('["*"]', "active")


@pytest.mark.parametrize("stage", ["begin", "select", "update", "hook", "commit"])
@pytest.mark.parametrize("control_type", [KeyboardInterrupt, SystemExit, GeneratorExit, _鍵盤子類])
def test_使用者交易primary控制精確清鏈且frames乾淨(stage, control_type, tmp_path):
    coordinator = _協調器()
    庫, owner = _建立資料庫(tmp_path, coordinator); _加端點(庫, owner)
    control = control_type("P07MARK")
    if stage == "hook":
        coordinator.failure = control
    else:
        proxy = _連線代理(庫.連線); proxy.stage = stage; proxy.failure = control
        庫.連線 = proxy
    with pytest.raises(control_type) as caught:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    assert caught.value is control and _設定(
        庫.連線.connection if isinstance(庫.連線, _連線代理) else 庫, owner
    ) == ('["*"]', "active")
    _確認production_frames乾淨(control, ("設定權限欄位",))


@pytest.mark.parametrize("cleanup_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_ordinary_primary後ROLLBACK控制勝出且清鏈(cleanup_type, tmp_path):
    庫, owner = _建立資料庫(tmp_path); _加端點(庫, owner)
    proxy = _連線代理(庫.連線); proxy.stage = "update"
    proxy.failure = RuntimeError("PRIMARY"); control = cleanup_type("P07MARK")
    proxy.rollback_failure = control; 庫.連線 = proxy
    with pytest.raises(cleanup_type) as caught:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    assert caught.value is control
    _確認production_frames乾淨(control, ("_拋出權限清理控制",))


@pytest.mark.parametrize("control", [None, KeyboardInterrupt("P07MARK")])
def test_COMMIT已耐久後wrapper失敗依既定policy(control, tmp_path):
    庫, owner = _建立資料庫(tmp_path); _加端點(庫, owner)
    proxy = _連線代理(庫.連線); proxy.stage = "commit"; proxy.commit_then_raise = True
    proxy.failure = control or RuntimeError("ordinary"); 庫.連線 = proxy
    if control is None:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
        assert caught.value is control
    assert _設定(proxy.connection, owner) == ('["tool.b"]', "disabled")


def test_建構時callback擷取ordinary固定且控制精確(monkeypatch, tmp_path):
    class Hostile:
        @property
        def 協調權限變更(self):
            raise failure
    for failure in (RuntimeError("P07MARK"), KeyboardInterrupt("P07MARK")):
        if isinstance(failure, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt) as caught:
                使用者庫(tmp_path / "control.db", Hostile())
            assert caught.value is failure
            _確認production_frames乾淨(failure, ("_擷取發布協調目標",))
        else:
            with pytest.raises(TypeError, match="^發布權限協調器無效$") as caught:
                使用者庫(tmp_path / "ordinary.db", Hostile())
            assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_生命週期foreign_no_transaction_root_restricted_archived與CAS(tmp_path, monkeypatch):
    庫, owner = _建立資料庫(tmp_path); _加端點(庫, owner, "disabled")
    coordinator = SQLite發布權限協調器()
    with pytest.raises(發布權限協調錯誤):
        coordinator.重新確認端點(庫.連線, "foreign", "endpoint-1", 2.0)
    assert _設定(庫, owner)[1] == "disabled"
    庫.連線.execute(
        "UPDATE user_settings SET skill_roots_json='[\"restricted\"]' WHERE user_id=?", (owner,)
    )
    with pytest.raises(發布權限協調錯誤):
        coordinator.重新確認端點(庫.連線, owner, "endpoint-1", 3.0)
    庫.連線.execute("UPDATE published_endpoints SET status='archived' WHERE id='endpoint-1'")
    with pytest.raises(發布權限協調錯誤):
        coordinator.封存端點(庫.連線, owner, "endpoint-1", 4.0)
    with pytest.raises(發布權限協調錯誤):
        coordinator.協調權限變更(庫.連線, owner, "enabled_tools_json", ("*",), (), 5.0)
    with pytest.raises(發布權限協調錯誤):
        鎖定確認端點可執行(庫.連線, "endpoint-1", "version-1")


@pytest.mark.parametrize("entry", ["setting", "coordinate", "reconfirm"])
def test_真實JSONhelper控制精確且每層production_frame乾淨(entry, tmp_path, monkeypatch):
    庫, owner = _建立資料庫(tmp_path); _加端點(庫, owner, "disabled" if entry == "reconfirm" else "active")
    control = KeyboardInterrupt("P07MARK")
    module = 使用者模組 if entry == "setting" else 協調模組
    monkeypatch.setattr(module, "解析嚴格JSON", lambda _text: (_ for _ in ()).throw(control))
    with pytest.raises(KeyboardInterrupt) as caught:
        if entry == "setting":
            庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
        elif entry == "coordinate":
            庫.連線.execute("BEGIN IMMEDIATE")
            SQLite發布權限協調器().協調權限變更(
                庫.連線, owner, "enabled_tools_json", ("*",), ("tool.b",), 2.0
            )
        else:
            SQLite發布權限協調器().重新確認端點(庫.連線, owner, "endpoint-1", 2.0)
    assert caught.value is control
    _確認production_frames乾淨(control, ("_解析權限JSON",) if entry == "setting" else ("_解析名稱陣列",))
