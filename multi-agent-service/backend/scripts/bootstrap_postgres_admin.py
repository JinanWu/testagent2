"""以可重跑 postcondition 協定建立 PostgreSQL 管理員及其 Secret。

明文密碼只存在本函式的記憶體區域；所有公開錯誤與 receipt 均刻意不含密碼。
"""
from __future__ import annotations

import argparse
import os
import secrets
import uuid
from typing import Any, Callable, Iterable

_重試次數 = 3
_版本上限 = 100
_管理員工作目錄 = "/tmp/agent-service/workspaces"
_管理員工作目錄JSON = '["/tmp/agent-service/workspaces"]'
_管理員技能根目錄JSON = '["/app/skills"]'


class _未知Postcondition(RuntimeError):
    pass


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _version_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name is None and isinstance(value, dict):
        name = value.get("name")
    return name if type(name) is str and name else None


def _enabled(value: Any) -> bool:
    state = getattr(value, "state", None)
    if state is None and isinstance(value, dict):
        state = value.get("state")
    name = getattr(state, "name", state)
    return name == "ENABLED" or name == 1 or str(name).endswith(".ENABLED")


def _bounded(operation: Callable[[], Any]) -> Any:
    last: BaseException | None = None
    for _ in range(_重試次數):
        try:
            return operation()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException as exc:
            last = exc
    raise _未知Postcondition("postcondition unavailable") from None


def _list_enabled(client: Any, parent: str) -> list[str]:
    def call() -> list[str]:
        response: Iterable[Any] = client.list_secret_versions(
            request={"parent": parent, "filter": "state:ENABLED", "page_size": _版本上限}
        )
        names: list[str] = []
        for version in response:
            if not _enabled(version):
                continue
            name = _version_name(version)
            if name is None or not name.startswith(parent + "/versions/"):
                raise _未知Postcondition("invalid secret version")
            names.append(name)
            if len(names) > _版本上限:
                raise _未知Postcondition("too many enabled versions")
        if len(names) != len(set(names)):
            raise _未知Postcondition("duplicate version listing")
        return sorted(names)
    return _bounded(call)


def _access(client: Any, name: str) -> bytes:
    def call() -> bytes:
        response = client.access_secret_version(request={"name": name})
        payload = getattr(response, "payload", None)
        if payload is None and isinstance(response, dict):
            payload = response.get("payload")
        data = getattr(payload, "data", None)
        if data is None and isinstance(payload, dict):
            data = payload.get("data")
        if type(data) is not bytes:
            raise _未知Postcondition("secret payload unavailable")
        return data
    return _bounded(call)


def _read_admin(connection_factory: Callable[[Any], Any], settings: Any, username: str) -> dict[str, str] | None:
    def call() -> dict[str, str] | None:
        with connection_factory(settings) as conn:
            result = conn.execute(
                "SELECT id,username,password_hash,auth_provider,roles::text,disabled "
                "FROM users WHERE username=%s",
                (username,),
            )
            rows = result.fetchall()
        if len(rows) > 1:
            raise _未知Postcondition("admin identity is not unique")
        if not rows:
            return None
        row = rows[0]
        user_id = _row_value(row, "id", 0)
        actual_username = _row_value(row, "username", 1)
        password_hash = _row_value(row, "password_hash", 2)
        provider = _row_value(row, "auth_provider", 3)
        roles = _row_value(row, "roles", 4)
        disabled = _row_value(row, "disabled", 5)
        if not (
            type(user_id) is str and user_id
            and actual_username == username
            and type(password_hash) is str and password_hash
            and provider == "local"
            and disabled is False
            and isinstance(roles, str) and '"admin"' in roles
        ):
            raise _未知Postcondition("admin row is not authoritative")
        return {"user_id": user_id, "password_hash": password_hash}
    return _bounded(call)


def _matching_versions(client: Any, parent: str, password_hash: str, verifier: Callable[[str, str], bool]) -> tuple[list[str], list[str]]:
    enabled = _list_enabled(client, parent)
    matches: list[str] = []
    for name in enabled:
        payload = _access(client, name)
        try:
            candidate = payload.decode("utf-8")
        except UnicodeDecodeError:
            candidate = ""
        try:
            if candidate and verifier(candidate, password_hash) is True:
                matches.append(name)
        finally:
            candidate = ""
            payload = b""
    return enabled, matches


def _destroy_enabled(client: Any, parent: str, name: str) -> None:
    for _ in range(_重試次數):
        try:
            client.destroy_secret_version(request={"name": name})
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            pass
        if name not in _list_enabled(client, parent):
            return
    raise _未知Postcondition("secret destroy postcondition unavailable")


def _cleanup_candidate_payload(client: Any, parent: str, password: str) -> None:
    target = password.encode("utf-8")
    for name in _list_enabled(client, parent):
        if secrets.compare_digest(_access(client, name), target):
            _destroy_enabled(client, parent, name)


def _add_version(client: Any, parent: str, password: str) -> str | None:
    try:
        result = client.add_secret_version(
            request={"parent": parent, "payload": {"data": password.encode("utf-8")}}
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        return None
    name = _version_name(result)
    if name is None or not name.startswith(parent + "/versions/"):
        return None
    return name


def _write_admin(
    connection_factory: Callable[[Any], Any], settings: Any, username: str,
    display_name: str, user_id: str, password_hash: str,
) -> None:
    with connection_factory(settings) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username=%s FOR UPDATE", (username,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users(id,username,display_name,password_hash,auth_provider,roles,disabled) "
                "VALUES(%s,%s,%s,%s,'local','[\"admin\"]'::jsonb,FALSE)",
                (user_id, username, display_name, password_hash),
            )
        else:
            user_id = str(_row_value(row, "id", 0))
            conn.execute(
                "UPDATE users SET display_name=%s,password_hash=%s,auth_provider='local',"
                "roles='[\"admin\"]'::jsonb,disabled=FALSE,updated_at=now() WHERE id=%s",
                (display_name, password_hash, user_id),
            )
        conn.execute(
            "INSERT INTO user_settings(user_id,enabled_tools,enabled_skills,skill_roots,allowed_workdirs,memory_home,settings) "
            "VALUES(%s,'[\"*\"]'::jsonb,'[\"*\"]'::jsonb,%s::jsonb,%s::jsonb,%s,'{}'::jsonb) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "skill_roots=CASE WHEN user_settings.skill_roots IS NULL OR user_settings.skill_roots IN ('[]'::jsonb,'[\"*\"]'::jsonb) THEN EXCLUDED.skill_roots ELSE user_settings.skill_roots END,"
            "allowed_workdirs=CASE WHEN user_settings.allowed_workdirs IS NULL OR user_settings.allowed_workdirs IN ('[]'::jsonb,'[\"*\"]'::jsonb) THEN EXCLUDED.allowed_workdirs ELSE user_settings.allowed_workdirs END,"
            "updated_at=now() WHERE "
            "user_settings.skill_roots IS NULL OR user_settings.skill_roots IN ('[]'::jsonb,'[\"*\"]'::jsonb) OR "
            "user_settings.allowed_workdirs IS NULL OR user_settings.allowed_workdirs IN ('[]'::jsonb,'[\"*\"]'::jsonb)",
            (user_id, _管理員技能根目錄JSON, _管理員工作目錄JSON, f"/tmp/agent-service/memory/{user_id}"),
        )


def bootstrap(
    *, username: str, display_name: str, secret_id: str,
    project: str | None = None, settings: Any = None, secret_client: Any = None,
    connection_factory: Callable[[Any], Any] | None = None,
    password_factory: Callable[[], str] | None = None,
) -> dict[str, str]:
    """收斂 DB 與 Secret 的 direct postcondition，unknown 一律失敗。"""
    values = (username, display_name, secret_id)
    if not all(type(v) is str and v.strip() == v and v for v in values):
        raise ValueError("admin bootstrap arguments invalid")
    if project is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if type(project) is not str or not project or project.strip() != project:
        raise RuntimeError("admin bootstrap configuration unavailable")
    if settings is None:
        from 繁中代理.環境設定 import 讀取交易儲存設定
        settings = 讀取交易儲存設定()
    if secret_client is None:
        from google.cloud import secretmanager
        secret_client = secretmanager.SecretManagerServiceClient()
    if connection_factory is None:
        from 繁中代理.PostgreSQL連線 import 交易連線
        connection_factory = 交易連線
    if password_factory is None:
        password_factory = lambda: secrets.token_urlsafe(32)
    from 繁中代理.使用者 import 產生密碼雜湊, 驗證密碼雜湊

    parent = f"projects/{project}/secrets/{secret_id}"
    password = ""
    try:
        current = _read_admin(connection_factory, settings, username)
        if current is not None:
            enabled, matches = _matching_versions(
                secret_client, parent, current["password_hash"], 驗證密碼雜湊
            )
            if matches:
                keep = matches[0]
                for name in enabled:
                    if name != keep:
                        _destroy_enabled(secret_client, parent, name)
                final = _read_admin(connection_factory, settings, username)
                final_enabled, final_matches = _matching_versions(
                    secret_client, parent, final["password_hash"], 驗證密碼雜湊
                ) if final else ([], [])
                if final == current and final_enabled == [keep] and final_matches == [keep]:
                    return {"user_id": current["user_id"], "secret_version": keep}
                raise _未知Postcondition("postcondition changed")

        password = password_factory()
        if type(password) is not str or not password:
            raise _未知Postcondition("password generation failed")
        candidate_hash = 產生密碼雜湊(password)
        _add_version(secret_client, parent, password)
        proposed_id = current["user_id"] if current else f"admin-{uuid.uuid4().hex}"
        write_error = False
        try:
            _write_admin(
                connection_factory, settings, username, display_name, proposed_id, candidate_hash
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            write_error = True

        readback = _read_admin(connection_factory, settings, username)
        if readback is None or not 驗證密碼雜湊(password, readback["password_hash"]):
            _cleanup_candidate_payload(secret_client, parent, password)
            raise _未知Postcondition("database postcondition failed")
        # write_error may be commit acknowledgement loss; matching readback is authoritative.
        del write_error
        enabled, matches = _matching_versions(
            secret_client, parent, readback["password_hash"], 驗證密碼雜湊
        )
        if not matches:
            _add_version(secret_client, parent, password)
            enabled, matches = _matching_versions(
                secret_client, parent, readback["password_hash"], 驗證密碼雜湊
            )
        if not matches:
            raise _未知Postcondition("secret postcondition failed")
        keep = matches[0]
        for name in enabled:
            if name != keep:
                _destroy_enabled(secret_client, parent, name)
        final = _read_admin(connection_factory, settings, username)
        final_enabled, final_matches = _matching_versions(
            secret_client, parent, final["password_hash"], 驗證密碼雜湊
        ) if final else ([], [])
        if (
            final is None or final["user_id"] != readback["user_id"]
            or final_enabled != [keep] or final_matches != [keep]
        ):
            raise _未知Postcondition("final postcondition failed")
        return {"user_id": final["user_id"], "secret_version": keep}
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise RuntimeError("admin bootstrap failed") from None
    finally:
        password = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap PostgreSQL admin")
    parser.add_argument("--project", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--secret-id", required=True)
    args = parser.parse_args(argv)
    receipt = bootstrap(
        project=args.project, username=args.username,
        display_name=args.display_name, secret_id=args.secret_id,
    )
    print(receipt["user_id"] + " " + receipt["secret_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
