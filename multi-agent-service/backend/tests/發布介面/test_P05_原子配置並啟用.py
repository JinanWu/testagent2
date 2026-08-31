"""CP4 P05 新版本、套件收據、稽核與目前指標的單一交易。"""
import json
import hashlib
import os
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from 繁中代理.發布介面.規劃 import 版本服務 as 版本服務模組
from 繁中代理.發布介面.技能套件 import 協調器 as 協調器模組
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.技能套件.發布器 import (
    套件發布收據, 技能套件發布器, 驗證已發布技能套件清單, _重驗最終內容,
)
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.規劃.版本服務 import (
    SQLite版本配置服務, 版本存取錯誤, 版本配置結果, 版本配置錯誤,
)


def _資料庫(tmp_path):
    path = tmp_path / "atomic-version.db"
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
        db.execute("INSERT INTO published_endpoints VALUES('endpoint-1','owner','account-1','demo','active','version-1',1,1,60,60)")
        db.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("version-1", "endpoint-1", 1, "需求一", "提示一", "[]", "[]", "{}",
             "runtime-1", "{}", "{}", '{"sha256":"' + "1" * 64 + '"}',
             None, '{"type":"string"}', 0, "owner", 1.0),
        )
    return path


def _快照(actor="owner", bundle="bundle-2"):
    return 發布版本快照(
        original_requirement_text="需求二", system_prompt="提示二",
        allowed_skills=["skill.one"], allowed_tools=["tool.one"],
        tool_schema_snapshot={"tool.one": {"revision": "r1"}},
        tool_runtime_revision="runtime-2", model_config_snapshot={"model": "m2"},
        retry_policy={"max_attempts": 2},
        skill_bundle_manifest={
            "bundle_id": bundle, "manifest_reference": f"{bundle}/manifest.json",
            "manifest_digest": "a" * 64, "sha256": "b" * 64,
        },
        input_schema=None, response_schema={"type": "number"},
        created_by_user_id=actor,
    )


def _收據(tmp_path, bundle="bundle-2", version="version-2", actor="owner"):
    source = tmp_path / f"source-{bundle}"
    source.mkdir(exist_ok=True)
    (source / "SKILL.md").write_text("---\nname: skill.one\n---\n", encoding="utf-8")
    return 技能套件發布器(tmp_path / "published").發布(
        套件識別碼=bundle, 端點識別碼="endpoint-1", 端點版本識別碼=version,
        版本號碼=2, 建立時間=20.0, 建立者識別碼=actor,
        技能表={"skill.one": source},
    )


def _執行(path, tmp_path, *, actor="owner", actor_type="user", version="version-2",
        audit="audit-2", verifier=lambda *_: True, connection_factory=sqlite3.connect,
        prepared_receipt=None):
    bundle = f"bundle-{version.rsplit('-', 1)[-1]}"
    receipt = prepared_receipt or _收據(tmp_path, bundle, version, actor)
    snapshot = _快照(actor, bundle)
    snapshot.skill_bundle_manifest.update({
        "manifest_digest": receipt.清單摘要, "sha256": receipt.套件雜湊,
    })
    service = SQLite版本配置服務(path, lambda: "legacy-id", lambda: 99.0, connection_factory)
    return service.配置並啟用(
        執行者使用者識別碼=actor, 執行者類型=actor_type, 端點識別碼="endpoint-1",
        已準備快照=snapshot, 已準備版本識別碼=version, 已準備時間=20.0,
        套件收據=receipt, 稽核識別碼=audit, 請求識別碼="request-2",
        套件驗證器=verifier,
    )


def _狀態(path):
    with sqlite3.connect(path) as db:
        return (
            db.execute("SELECT id,version_number,schema_changed FROM published_endpoint_versions ORDER BY version_number").fetchall(),
            db.execute("SELECT bundle_id,version_id,state FROM published_skill_bundles").fetchall(),
            db.execute("SELECT action,actor_type,actor_id,resource_id,request_id FROM audit_events").fetchall(),
            db.execute("SELECT current_version_id,updated_at FROM published_endpoints").fetchone(),
        )


def test_owner原子建立收據audit並切換且驗證器收到脫離清單(tmp_path):
    path = _資料庫(tmp_path); seen = []
    def verify(projection, version_id, endpoint_id):
        seen.append((projection, version_id, endpoint_id)); return True
    result = _執行(path, tmp_path, verifier=verify)
    assert result == 版本配置結果("version-2", "endpoint-1", 2, True, 20.0)
    assert len(seen) == 1
    assert (seen[0][0].bundle_id, seen[0][0].endpoint_version_id,
            seen[0][1:]) == ("bundle-2", "version-2", ("version-2", "endpoint-1"))
    assert _狀態(path) == (
        [("version-1", 1, 0), ("version-2", 2, 1)],
        [("bundle-2", "version-2", "published")],
        [("endpoint_version_activated", "user", "owner", "version-2", "request-2")],
        ("version-2", 20.0),
    )


def test_admin可替擁有者發布但user冒用與非active固定拒絕(tmp_path):
    path = _資料庫(tmp_path)
    assert _執行(path, tmp_path, actor="admin-1", actor_type="admin").version_id == "version-2"
    other = tmp_path / "other"; other.mkdir()
    path = _資料庫(other)
    with pytest.raises(版本存取錯誤, match="^版本配置存取遭拒$"):
        _執行(path, tmp_path, actor="foreign", actor_type="user")
    assert len(_狀態(path)[0]) == 1


class _失敗連線(sqlite3.Connection):
    stage = None
    def execute(self, sql, parameters=()):
        if type(self).stage == "receipt" and sql.startswith("INSERT INTO published_skill_bundles"):
            raise sqlite3.OperationalError
        if type(self).stage == "audit" and sql.startswith("INSERT INTO audit_events"):
            raise sqlite3.OperationalError
        if type(self).stage == "pointer" and sql.startswith("UPDATE published_endpoints"):
            return type("Cursor", (), {"rowcount": 0})()
        return super().execute(sql, parameters)


@pytest.mark.parametrize("stage", ["receipt", "audit", "pointer"])
def test_任一後段失敗version收據audit與pointer全部rollback(tmp_path, stage):
    path = _資料庫(tmp_path); before = _狀態(path)
    _失敗連線.stage = stage
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_失敗連線)
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        _執行(path, tmp_path, connection_factory=connect)
    assert _狀態(path) == before


def test_兩個writer僅連續下一版成功且沒有孤立副作用(tmp_path):
    path = _資料庫(tmp_path)
    def write(index):
        try:
            return _執行(path, tmp_path, version=f"version-{index}", audit=f"audit-{index}")
        except 版本配置錯誤:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (2, 3)))
    assert sum(value is not None for value in results) == 1
    state = _狀態(path)
    assert len(state[0]) == len(state[1]) + 1 == len(state[2]) + 1 == 2


class _生命週期連線(sqlite3.Connection):
    模式 = ""
    回滾控制 = 關閉控制 = 提交控制 = None
    事件 = None
    def execute(self, sql, parameters=()):
        if self.模式.startswith("receipt") and sql.startswith("INSERT INTO published_skill_bundles"):
            raise sqlite3.OperationalError("receipt")
        if sql == "COMMIT" and self.模式 == "commit-control-before":
            assert isinstance(self.提交控制, BaseException)
            raise self.提交控制
        if sql == "COMMIT" and self.模式.startswith("commit"):
            result = super().execute(sql, parameters)
            if self.模式 == "commit-mutate":
                super().execute("UPDATE published_endpoints SET updated_at=21 WHERE id='endpoint-1'")
            if self.模式 == "commit-control-after":
                assert isinstance(self.提交控制, BaseException)
                raise self.提交控制
            raise sqlite3.OperationalError("commit acknowledgement")
        if sql == "ROLLBACK":
            self.事件.append(("rollback", self.in_transaction))
            if self.模式.startswith("receipt"):
                if self.回滾控制 is not None:
                    raise self.回滾控制
                raise sqlite3.OperationalError("rollback")
        return super().execute(sql, parameters)
    def close(self):
        if self.模式 in {"commit-close", "receipt-cleanup"}:
            self.事件.append(("close", self.in_transaction))
            if self.關閉控制 is not None:
                raise self.關閉控制
            raise sqlite3.OperationalError("close")
        return super().close()


def _生命週期工廠(mode, *, rollback_control=None, close_control=None,
             commit_control=None, events=None):
    events = [] if events is None else events
    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs, factory=_生命週期連線)
        connection.模式 = mode
        connection.回滾控制 = rollback_control
        connection.關閉控制 = close_control
        connection.提交控制 = commit_control
        connection.事件 = events
        return connection
    return connect


def test_COMMIT已耐久但ack失敗以exact四投影回傳成功(tmp_path):
    path = _資料庫(tmp_path)
    result = _執行(path, tmp_path, connection_factory=_生命週期工廠("commit-ack"))
    assert result.version_id == "version-2"
    assert _狀態(path)[3] == ("version-2", 20.0)


@pytest.mark.parametrize("control_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("durable", [False, True])
def test_COMMIT控制例外不論是否耐久皆保留identity與args且正確cleanup(
    tmp_path, control_type, durable,
):
    path = _資料庫(tmp_path); before = _狀態(path); events = []
    control = control_type("commit-control", 17)
    mode = "commit-control-after" if durable else "commit-control-before"
    with pytest.raises(control_type) as caught:
        _執行(path, tmp_path, connection_factory=_生命週期工廠(
            mode, commit_control=control, events=events,
        ))
    assert caught.value is control
    assert caught.value.args == ("commit-control", 17)
    assert _狀態(path) == (before if not durable else (
        [("version-1", 1, 0), ("version-2", 2, 1)],
        [("bundle-2", "version-2", "published")],
        [("endpoint_version_activated", "user", "owner", "version-2", "request-2")],
        ("version-2", 20.0),
    ))
    assert [event[0] for event in events] == ([] if durable else ["rollback"])


class _敵對交易狀態連線(_生命週期連線):
    @property
    def in_transaction(self):
        raise AssertionError("不得 dispatch hostile override")


def test_COMMIT_ack遺失只讀base_in_transaction_descriptor一次(tmp_path):
    path = _資料庫(tmp_path)
    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs, factory=_敵對交易狀態連線)
        connection.模式 = "commit-ack"; connection.事件 = []
        return connection
    assert _執行(path, tmp_path, connection_factory=connect).version_id == "version-2"


def test_COMMIT後投影遭改動不得誤認為成功(tmp_path):
    path = _資料庫(tmp_path); events = []
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        _執行(path, tmp_path, connection_factory=_生命週期工廠("commit-mutate", events=events))
    assert _狀態(path)[3] == ("version-2", 21.0)
    assert not any(event[0] == "rollback" for event in events)


def test_COMMIT成功後ordinary_close失敗仍釋放且outcome為committed(tmp_path):
    path = _資料庫(tmp_path)
    result = _執行(path, tmp_path, connection_factory=_生命週期工廠("commit-close"))
    assert result.version_id == "version-2"
    with sqlite3.connect(path, timeout=0) as db:
        db.execute("BEGIN IMMEDIATE"); db.execute("ROLLBACK")


def test_receipt失敗且rollback與close皆ordinary失敗仍釋放鎖(tmp_path):
    path = _資料庫(tmp_path); before = _狀態(path); events = []
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        _執行(path, tmp_path, connection_factory=_生命週期工廠("receipt-cleanup", events=events))
    assert _狀態(path) == before
    assert events == [("rollback", True), ("close", False)]
    with sqlite3.connect(path, timeout=0) as db:
        db.execute("BEGIN IMMEDIATE"); db.execute("ROLLBACK")


def test_receipt清理控制以rollback_identity與args優先且仍釋放鎖(tmp_path):
    path = _資料庫(tmp_path)
    rollback_control = KeyboardInterrupt("rollback-control", 7)
    close_control = SystemExit("close-control", 9)
    with pytest.raises(KeyboardInterrupt) as caught:
        _執行(path, tmp_path, connection_factory=_生命週期工廠(
            "receipt-cleanup", rollback_control=rollback_control, close_control=close_control,
        ))
    assert caught.value is rollback_control
    assert caught.value.args == ("rollback-control", 7)
    with sqlite3.connect(path, timeout=0) as db:
        db.execute("BEGIN IMMEDIATE"); db.execute("ROLLBACK")


def test_verifier普通失敗與三種控制流突變皆完整rollback並保留identity(tmp_path):
    controls = (
        None, KeyboardInterrupt("verify-control", 3),
        SystemExit("verify-control", 3), GeneratorExit("verify-control", 3),
    )
    for index, control in enumerate(controls):
        area = tmp_path / f"verify-{index}"; area.mkdir()
        path = _資料庫(area); before = _狀態(path)
        def verify(_projection, *_):
            if control is None:
                raise RuntimeError("verify")
            raise control
        if control is None:
            with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
                _執行(path, area, verifier=verify)
        else:
            with pytest.raises(type(control)) as caught:
                _執行(path, area, verifier=verify)
            assert caught.value is control and caught.value.args == ("verify-control", 3)
        assert _狀態(path) == before


@pytest.mark.parametrize("control_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("close_is_control", [False, True])
def test_verifier控制為primary且fd_close普通或控制皆不得覆蓋identity與args(
    tmp_path, monkeypatch, control_type, close_is_control,
):
    path = _資料庫(tmp_path); before = _狀態(path)
    primary = control_type("verifier-primary", 31)
    close_failure = (SystemExit("fd-close-control", 32) if close_is_control
                     else OSError("fd-close-ordinary"))
    original_close = 協調器模組._系統關閉
    closed = []
    receipt = _收據(tmp_path)
    verifier_started = False

    def close_once(fd):
        if not verifier_started:
            return original_close(fd)
        closed.append(fd)
        original_close(fd)
        raise close_failure

    def fail_verifier(*_):
        nonlocal verifier_started
        verifier_started = True
        raise primary

    monkeypatch.setattr(協調器模組, "_系統關閉", close_once)
    with pytest.raises(control_type) as caught:
        _執行(path, tmp_path, verifier=fail_verifier, prepared_receipt=receipt)
    assert caught.value is primary
    assert caught.value.args == ("verifier-primary", 31)
    assert len(closed) == 1 and len(set(closed)) == 1
    assert _狀態(path) == before


def _真實套件(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "SKILL.md").write_text("---\nname: skill.one\n---\n", encoding="utf-8")
    publisher = 技能套件發布器(tmp_path / "published")
    receipt = publisher.發布(
        套件識別碼="bundle-2", 端點識別碼="endpoint-1", 端點版本識別碼="version-2",
        版本號碼=2, 建立時間=20.0, 建立者識別碼="owner", 技能表={"skill.one": source},
    )
    manifest_bytes = (receipt.路徑 / "manifest.json").read_bytes()
    projection = 驗證已發布技能套件清單(manifest_bytes)
    index = {item.path: {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
             for item in projection.copied_files}
    return receipt, projection, index


def test_真實published_bundle以pinned_projection驗證且transient替換四項rollback(tmp_path):
    receipt, projection, index = _真實套件(tmp_path)
    path = _資料庫(tmp_path); before = _狀態(path)
    snapshot = _快照()
    snapshot.skill_bundle_manifest.update({
        "manifest_digest": receipt.清單摘要, "sha256": receipt.套件雜湊,
    })
    service = SQLite版本配置服務(path, lambda: "legacy", lambda: 0)
    def verify(pinned_projection, version, endpoint):
        assert (version, endpoint) == (projection.endpoint_version_id, projection.endpoint_id)
        assert pinned_projection == projection
        return True
    result = service.配置並啟用(
        執行者使用者識別碼="owner", 執行者類型="user", 端點識別碼="endpoint-1",
        已準備快照=snapshot, 已準備版本識別碼="version-2", 已準備時間=20.0,
        套件收據=receipt, 稽核識別碼="audit-2", 請求識別碼="request-2", 套件驗證器=verify,
    )
    assert result.version_id == "version-2"

    area = tmp_path / "race"; area.mkdir()
    receipt, projection, index = _真實套件(area)
    path = _資料庫(area); before = _狀態(path)
    snapshot = _快照(); snapshot.skill_bundle_manifest.update({
        "manifest_digest": receipt.清單摘要, "sha256": receipt.套件雜湊,
    })
    replacement = receipt.路徑.with_name("replacement")
    shutil.copytree(receipt.路徑, replacement, copy_function=shutil.copy2)
    evil_file = replacement / "skill.one" / "SKILL.md"
    os.chmod(evil_file, 0o644)
    evil_file.write_bytes(b"evil replacement marker")
    os.chmod(evil_file, 0o444)

    def raced_verify(pinned_projection, *_):
        replacement = receipt.路徑.with_name("replacement")
        original = receipt.路徑.with_name("original")
        receipt.路徑.rename(original)
        replacement.rename(receipt.路徑)
        try:
            observed = hashlib.sha256(
                (receipt.路徑 / "skill.one" / "SKILL.md").read_bytes()
            ).hexdigest()
            expected = next(
                item.sha256 for item in pinned_projection.copied_files
                if item.path == "skill.one/SKILL.md"
            )
            return observed == expected
        finally:
            receipt.路徑.rename(replacement)
            original.rename(receipt.路徑)
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        SQLite版本配置服務(path, lambda: "legacy", lambda: 0).配置並啟用(
            執行者使用者識別碼="owner", 執行者類型="user", 端點識別碼="endpoint-1",
            已準備快照=snapshot, 已準備版本識別碼="version-2", 已準備時間=20.0,
            套件收據=receipt, 稽核識別碼="audit-2", 請求識別碼="request-2",
            套件驗證器=raced_verify,
        )
    assert _狀態(path) == before


def test_verifier永久替換bundle即使回傳True仍依callback後identity完整rollback(tmp_path):
    receipt, _, _ = _真實套件(tmp_path)
    path = _資料庫(tmp_path); before = _狀態(path)
    replacement = receipt.路徑.with_name("replacement")
    shutil.copytree(receipt.路徑, replacement, copy_function=shutil.copy2)

    def replace_forever(*_):
        receipt.路徑.rename(receipt.路徑.with_name("original"))
        replacement.rename(receipt.路徑)
        return True

    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        _執行(path, tmp_path, verifier=replace_forever, prepared_receipt=receipt)
    assert _狀態(path) == before


def test_收據parent含中間symlink即使leaf_inode有效仍拒絕(tmp_path):
    receipt, _, _ = _真實套件(tmp_path)
    path = _資料庫(tmp_path); before = _狀態(path)
    alias = tmp_path / "published-alias"
    alias.symlink_to(receipt.路徑.parent, target_is_directory=True)
    alias_receipt = 套件發布收據(
        receipt.套件識別碼, receipt.清單參照, receipt.清單摘要,
        receipt.套件雜湊, receipt.總位元組數, alias / receipt.套件識別碼,
    )
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        _執行(path, tmp_path, prepared_receipt=alias_receipt)
    assert _狀態(path) == before


def test_第257個共享列舉項目立即拒絕且不讀covered_file(tmp_path, monkeypatch):
    receipt, _, _ = _真實套件(tmp_path)
    path = _資料庫(tmp_path); before = _狀態(path)
    os.chmod(receipt.路徑, 0o755)
    try:
        for index in range(255):
            extra = receipt.路徑 / f"extra-{index:03d}"
            extra.write_bytes(b"")
            os.chmod(extra, 0o444)
    finally:
        os.chmod(receipt.路徑, 0o555)
    original_read = 協調器模組._讀取有界檔案
    reads = []

    def record_read(directory_fd, name, maximum):
        reads.append(name)
        return original_read(directory_fd, name, maximum)

    monkeypatch.setattr(協調器模組, "_讀取有界檔案", record_read)
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$"):
        _執行(path, tmp_path, prepared_receipt=receipt)
    assert reads == ["manifest.json"]
    assert _狀態(path) == before
