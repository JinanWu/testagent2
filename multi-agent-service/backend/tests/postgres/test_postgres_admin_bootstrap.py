"""Admin bootstrap 跨 DB/Secret postcondition 的因果測試。"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import bootstrap_postgres_admin as subject
from 繁中代理.使用者 import 產生密碼雜湊


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)
    def fetchall(self):
        return deepcopy(self._rows)
    def fetchone(self):
        return deepcopy(self._rows[0]) if self._rows else None


class _DB:
    """Transaction fake；commit 前失敗與 side-effect-then-raise 分開模擬。"""
    def __init__(self, row=None):
        self.row = deepcopy(row)
        self.fail_precommit = False
        self.lose_commit_ack = False
        self.read_failures = 0
        self.events = []

    def factory(self, _settings):
        return _DBContext(self)


class _DBContext:
    def __init__(self, db):
        self.db = db
        self.pending = deepcopy(db.row)
        self.mutated = False
    def __enter__(self):
        return self
    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            return False
        if self.mutated:
            if self.db.fail_precommit:
                self.db.fail_precommit = False
                raise RuntimeError("precommit payload must never escape")
            self.db.row = deepcopy(self.pending)
            self.db.events.append("commit")
            if self.db.lose_commit_ack:
                self.db.lose_commit_ack = False
                raise RuntimeError("commit acknowledgement lost")
        return False
    def execute(self, sql, params=()):
        self.db.events.append(sql.split()[0])
        if sql.startswith("SELECT id,username"):
            if self.db.read_failures:
                self.db.read_failures -= 1
                raise RuntimeError("read unavailable")
            return _Result([self.pending] if self.pending else [])
        if sql.startswith("SELECT id FROM"):
            return _Result([{"id": self.pending["id"]}] if self.pending else [])
        if sql.startswith("INSERT INTO users("):
            user_id, username, display_name, password_hash = params
            self.pending = {
                "id": user_id, "username": username, "display_name": display_name,
                "password_hash": password_hash, "auth_provider": "local",
                "roles": '["admin"]', "disabled": False,
            }
            self.mutated = True
            return _Result()
        if sql.startswith("UPDATE users SET"):
            display_name, password_hash, user_id = params
            assert self.pending and self.pending["id"] == user_id
            self.pending.update(
                display_name=display_name, password_hash=password_hash,
                auth_provider="local", roles='["admin"]', disabled=False,
            )
            self.mutated = True
            return _Result()
        if sql.startswith("INSERT INTO user_settings"):
            assert self.pending and params[0] == self.pending["id"]
            assert params[1] == '["/app/skills"]'
            assert params[2] == '["/tmp/agent-service/workspaces"]'
            assert params[3] == f"/tmp/agent-service/memory/{self.pending['id']}"
            assert "ON CONFLICT (user_id) DO UPDATE" in sql
            assert "skill_roots=CASE" in sql
            assert "allowed_workdirs=CASE" in sql
            self.mutated = True
            return _Result()
        raise AssertionError(f"unexpected SQL shape: {sql}")


class _Secrets:
    """嚴格追蹤 enabled/destroyed 狀態，支援真正 side-effect-then-raise。"""
    def __init__(self):
        self.versions = {}
        self.next_id = 1
        self.add_mode = "success"
        self.list_failures = 0
        self.access_failures = {}
        self.destroy_ack_loss = set()
        self.add_calls = 0

    def seed(self, data, state="ENABLED"):
        name = f"projects/p/secrets/admin/versions/{self.next_id}"
        self.next_id += 1
        self.versions[name] = {"data": data.encode(), "state": state}
        return name

    def list_secret_versions(self, *, request):
        assert request == {
            "parent": "projects/p/secrets/admin", "filter": "state:ENABLED",
            "page_size": subject._版本上限,
        }
        if self.list_failures:
            self.list_failures -= 1
            raise RuntimeError("list unknown")
        return [SimpleNamespace(name=n, state=v["state"]) for n, v in self.versions.items()]

    def access_secret_version(self, *, request):
        name = request["name"]
        assert self.versions[name]["state"] == "ENABLED"
        if self.access_failures.get(name, 0):
            self.access_failures[name] -= 1
            raise RuntimeError("access unknown")
        return SimpleNamespace(payload=SimpleNamespace(data=self.versions[name]["data"]))

    def add_secret_version(self, *, request):
        assert request["parent"] == "projects/p/secrets/admin"
        data = request["payload"]["data"]
        assert type(data) is bytes
        self.add_calls += 1
        if self.add_mode == "raise":
            raise RuntimeError("add failed")
        name = self.seed(data.decode())
        if self.add_mode == "ack_loss":
            self.add_mode = "success"
            raise RuntimeError("add acknowledgement lost")
        return SimpleNamespace(name=name)

    def destroy_secret_version(self, *, request):
        name = request["name"]
        assert self.versions[name]["state"] == "ENABLED"
        self.versions[name]["state"] = "DESTROYED"
        if name in self.destroy_ack_loss:
            self.destroy_ack_loss.remove(name)
            raise RuntimeError("destroy acknowledgement lost")
        return SimpleNamespace(name=name)

    def enabled(self):
        return {n: v["data"] for n, v in self.versions.items() if v["state"] == "ENABLED"}


def _row(password="known-password"):
    return {
        "id": "admin-fixed", "username": "lab-admin", "display_name": "LAB",
        "password_hash": 產生密碼雜湊(password), "auth_provider": "local",
        "roles": '["admin"]', "disabled": False,
    }


def _run(db, client, password_factory=lambda: "new-password"):
    return subject.bootstrap(
        project="p", username="lab-admin", display_name="LAB Admin",
        secret_id="admin", settings=object(), secret_client=client,
        connection_factory=db.factory, password_factory=password_factory,
    )


def test_fresh成功receipt最小且rerun冪等():
    db, client = _DB(), _Secrets()
    first = _run(db, client)
    assert set(first) == {"user_id", "secret_version"}
    assert first["user_id"] == db.row["id"]
    assert client.enabled() == {first["secret_version"]: b"new-password"}
    add_calls = client.add_calls
    second = _run(db, client, lambda: (_ for _ in ()).throw(AssertionError("must not rotate")))
    assert second == first
    assert client.add_calls == add_calls
    assert "password" not in repr(first)


def test_existing會保留唯一matching並銷毀不匹配與重複version():
    db, client = _DB(_row()) , _Secrets()
    keep = client.seed("known-password")
    duplicate = client.seed("known-password")
    stale = client.seed("wrong-password")
    client.destroy_ack_loss.add(stale)
    receipt = _run(db, client, lambda: (_ for _ in ()).throw(AssertionError("no password")))
    assert receipt == {"user_id": "admin-fixed", "secret_version": keep}
    assert client.enabled() == {keep: b"known-password"}
    assert client.versions[duplicate]["state"] == "DESTROYED"
    assert client.versions[stale]["state"] == "DESTROYED"


def test_secret_add_ack_loss由direct_postcondition成功判定():
    db, client = _DB(), _Secrets()
    client.add_mode = "ack_loss"
    receipt = _run(db, client)
    assert client.enabled() == {receipt["secret_version"]: b"new-password"}
    assert client.add_calls == 1


def test_db_commit_ack_loss由direct_readback成功判定():
    db, client = _DB(), _Secrets()
    db.lose_commit_ack = True
    receipt = _run(db, client)
    assert receipt["user_id"] == db.row["id"]
    assert len(client.enabled()) == 1


def test_secret_success_db_precommit_failure會銷毀本次version且不成功():
    db, client = _DB(), _Secrets()
    db.fail_precommit = True
    with pytest.raises(RuntimeError, match="^admin bootstrap failed$") as caught:
        _run(db, client, lambda: "never-in-error")
    assert db.row is None
    assert client.enabled() == {}
    assert "never-in-error" not in repr(caught.value)


def test_secret完全失敗後db已提交仍unknown不得成功且可重跑修復():
    db, client = _DB(), _Secrets()
    client.add_mode = "raise"
    with pytest.raises(RuntimeError, match="^admin bootstrap failed$"):
        _run(db, client)
    assert db.row is not None and client.enabled() == {}
    client.add_mode = "success"
    repaired = _run(db, client, lambda: "new-password")
    assert len(client.enabled()) == 1
    assert repaired["user_id"] == db.row["id"]


def test_bounded_db_list_access重試後以direct_readback成功():
    db, client = _DB(_row()), _Secrets()
    name = client.seed("known-password")
    db.read_failures = subject._重試次數 - 1
    client.list_failures = subject._重試次數 - 1
    client.access_failures[name] = subject._重試次數 - 1
    assert _run(db, client) == {"user_id": "admin-fixed", "secret_version": name}
    assert db.read_failures == client.list_failures == client.access_failures[name] == 0


def test_bounded_list_unknown永不回成功():
    db, client = _DB(_row()), _Secrets()
    client.seed("known-password")
    client.list_failures = subject._重試次數
    with pytest.raises(RuntimeError, match="^admin bootstrap failed$"):
        _run(db, client)
    assert client.list_failures == 0


def test_bounded_access_unknown永不回成功():
    db, client = _DB(_row()), _Secrets()
    name = client.seed("known-password")
    client.access_failures[name] = subject._重試次數
    with pytest.raises(RuntimeError, match="^admin bootstrap failed$"):
        _run(db, client)
    assert client.access_failures[name] == 0


def test_cli只定義non_secret參數且stdout只含receipt(monkeypatch, capsys):
    seen = {}
    def fake_bootstrap(**kwargs):
        seen.update(kwargs)
        return {"user_id": "uid", "secret_version": "projects/p/secrets/s/versions/7"}
    monkeypatch.setattr(subject, "bootstrap", fake_bootstrap)
    assert subject.main([
        "--project", "p", "--username", "u", "--display-name", "U",
        "--secret-id", "s",
    ]) == 0
    assert set(seen) == {"project", "username", "display_name", "secret_id"}
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "uid projects/p/secrets/s/versions/7\n"
