"""PUB P07 持久 JSON 邊界、完整 schema 指紋與原子 rollback 回歸。"""

import json

import pytest

from 繁中代理.使用者 import 使用者庫, 權限更新錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.權限協調 import SQLite發布權限協調器


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "p07-json.db"
    初始化發布介面資料庫(路徑)
    庫 = 使用者庫(路徑, SQLite發布權限協調器())
    owner = 庫.建立使用者(
        "owner", enabled_tools=["*"], enabled_skills=["*"], skill_roots=["*"]
    )["id"]
    return 庫, owner


def _加端點(庫, owner, endpoint, tool, skill=None):
    account, version = f"account-{endpoint}", f"version-{endpoint}"
    庫.連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", (account, 1.0))
    庫.連線.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?)",
        (endpoint, owner, account, endpoint, "active", 1.0, 1.0),
    )
    skills = [] if skill is None else [skill]
    manifest = json.dumps({
        "permission_revision": "perm-r1",
        "skills": [] if skill is None else [{
            "name": skill, "content_sha256_reference": "a" * 64,
        }],
    }, separators=(",", ":"))
    庫.連線.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (version, endpoint, 1, "req", "prompt", json.dumps(skills), json.dumps([tool]), "{}", "runtime-r1",
         "{}", "{}", manifest, None, "{}", 0, owner, 1.0),
    )
    庫.連線.execute(
        "UPDATE published_endpoints SET current_version_id=? WHERE id=?", (version, endpoint)
    )
    return version


def _狀態與設定(庫, owner):
    狀態 = dict(庫.連線.execute("SELECT id,status FROM published_endpoints ORDER BY id"))
    設定 = 庫.連線.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0]
    return 狀態, 設定


def _竄改不可變版本(庫, version, column, payload):
    trigger = 庫.連線.execute(
        "SELECT sql FROM sqlite_master WHERE name='published_endpoint_versions_no_update'"
    ).fetchone()[0]
    庫.連線.execute("DROP TRIGGER published_endpoint_versions_no_update")
    庫.連線.execute(
        f"UPDATE published_endpoint_versions SET {column}=? WHERE id=?", (payload, version)
    )
    庫.連線.execute(trigger)


def _畸形JSON(case):
    if case == "duplicate":
        return '{"skills":[],"skills":[]}'
    if case == "nonfinite":
        return '{"skills":[],"number":NaN}'
    if case == "depth":
        return '{"skills":[],"deep":' + "[" * 64 + "0" + "]" * 64 + "}"
    if case == "wide":
        return '{"skills":[],"wide":[' + ",".join("0" for _ in range(10_001)) + "]}"
    return '{"skills":[],"huge":"' + "x" * (1024 * 1024) + '"}'


@pytest.mark.parametrize("case", ["duplicate", "nonfinite", "depth", "wide", "huge"])
def test_持久舊設定畸形使設定與所有端點狀態rollback(case, tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner, "endpoint-a", "tool.a")
    _加端點(庫, owner, "endpoint-b", "tool.b")
    payload = _畸形JSON(case)
    庫.連線.execute(
        "UPDATE user_settings SET enabled_tools_json=? WHERE user_id=?", (payload, owner)
    )

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$") as caught:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert _狀態與設定(庫, owner) == (
        {"endpoint-a": "active", "endpoint-b": "active"}, payload,
    )


@pytest.mark.parametrize("case", ["duplicate", "nonfinite", "depth", "wide", "huge"])
def test_後段端點畸形使先前停用與設定一起rollback(case, tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner, "endpoint-a", "tool.a")
    version = _加端點(庫, owner, "endpoint-b", "tool.b")
    _竄改不可變版本(庫, version, "skill_bundle_manifest_json", _畸形JSON(case))

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$") as caught:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert _狀態與設定(庫, owner) == (
        {"endpoint-a": "active", "endpoint-b": "active"}, '["*"]',
    )


@pytest.mark.parametrize("tamper", ["update-trigger", "delete-trigger", "index", "ledger"])
def test_完整schema或ledger漂移使權限設定rollback且漂移保留(tamper, tmp_path):
    name = None
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner, "endpoint-a", "tool.a")
    if tamper == "ledger":
        庫.連線.execute(
            "UPDATE published_api_schema_migrations SET name='tampered' WHERE version=5"
        )
    else:
        name = {
            "update-trigger": "published_endpoint_versions_no_update",
            "delete-trigger": "published_endpoint_versions_no_delete",
            "index": "idx_published_endpoints_owner_status",
        }[tamper]
        kind = "INDEX" if tamper == "index" else "TRIGGER"
        庫.連線.execute(f"DROP {kind} {name}")

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _狀態與設定(庫, owner) == ({"endpoint-a": "active"}, '["*"]')
    if tamper == "ledger":
        assert 庫.連線.execute(
            "SELECT name FROM published_api_schema_migrations WHERE version=5"
        ).fetchone()[0] == "tampered"
    else:
        assert 庫.連線.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (name,)
        ).fetchone() is None


def test_quote_escape內括號不增加深度且可正常停用(tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    version = _加端點(庫, owner, "endpoint-a", "tool.a")
    manifest = json.dumps({
        "permission_revision": "perm-r1", "skills": [],
    })
    _竄改不可變版本(庫, version, "skill_bundle_manifest_json", manifest)

    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _狀態與設定(庫, owner) == ({"endpoint-a": "disabled"}, '["tool.b"]')


def test_active端點缺current_pin使設定rollback(tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    _加端點(庫, owner, "endpoint-a", "tool.a")
    庫.連線.execute("UPDATE published_endpoints SET current_version_id=NULL")

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _狀態與設定(庫, owner) == ({"endpoint-a": "active"}, '["*"]')


@pytest.mark.parametrize("case", ["missing-revision", "extra-key", "bad-hash", "extra-skill-key"])
def test_不完整P04_manifest使設定與狀態rollback(case, tmp_path):
    庫, owner = _建立資料庫(tmp_path)
    skill = None if case in {"missing-revision", "extra-key"} else "skill.a"
    version = _加端點(庫, owner, "endpoint-a", "tool.a", skill)
    manifests = {
        "missing-revision": {"skills": []},
        "extra-key": {"permission_revision": "perm-r1", "skills": [], "extra": 1},
        "bad-hash": {"permission_revision": "perm-r1", "skills": [
            {"name": "skill.a", "content_sha256_reference": "bad"},
        ]},
        "extra-skill-key": {"permission_revision": "perm-r1", "skills": [
            {"name": "skill.a", "content_sha256_reference": "a" * 64, "extra": 1},
        ]},
    }
    _竄改不可變版本(
        庫, version, "skill_bundle_manifest_json",
        json.dumps(manifests[case], separators=(",", ":")),
    )

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _狀態與設定(庫, owner) == ({"endpoint-a": "active"}, '["*"]')
