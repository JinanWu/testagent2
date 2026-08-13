"""Acceptance 07 canonical credential management production wiring。"""

from __future__ import annotations

from types import MappingProxyType, MethodType

from fastapi.testclient import TestClient

from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.設定 import 生產設定


def _設定(tmp_path, 封套工廠):
    """建立只啟用 credential management 的 explicit CP4 settings。

    描述：建立只啟用 credential management 的 explicit CP4 settings。
    參數：``tmp_path``、``封套工廠``。
    返回值：無；完成指定操作或更新可觀測測試狀態。
    """
    呼叫: list[str] = []
    web = 生產設定(
        tmp_path / "web.sqlite3", ("https://client.example",), "fake", "fake", None, None,
    )
    published = Published生產設定(
        tmp_path / "published.sqlite3", tmp_path / "bundles",
        lambda _工具庫: 呼叫.append("installer"),
        lambda: 呼叫.append("models") or {"fake": object()},
        憑證封套工廠=封套工廠,
    )
    return web, published, 呼叫


def test_construction不呼叫keyring且canonical_OpenAPI含三條憑證路由(tmp_path) -> None:
    """固定 construction zero-I/O 與 canonical credential route inventory。

    描述：固定 construction zero-I/O 與 canonical credential route inventory。
    參數：``tmp_path``。
    返回值：無；所有驗收結果由assertions表達。
    """
    keyring呼叫: list[str] = []
    web, published, 呼叫 = _設定(
        tmp_path,
        lambda: keyring呼叫.append("keyring") or AESGCM憑證封套({1: b"k" * 32}, 1),
    )
    app = 建立CP4ASGI應用程式(web, published)
    paths = app.openapi()["paths"]
    assert keyring呼叫 == 呼叫 == []
    assert set(paths["/api/published-endpoints/{endpoint_id}/credentials"]) == {"get", "post"}
    assert set(paths["/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke"]) == {"post"}


def test_startup_exact_once建立管理provider且shutdown清除reference(tmp_path) -> None:
    """證明 envelope factory 只在 startup 呼叫一次且 resource shutdown detach provider。

    描述：證明 envelope factory 只在 startup 呼叫一次且 resource shutdown detach provider。
    參數：``tmp_path``。
    返回值：無；所有驗收結果由assertions表達。
    """
    keyring呼叫: list[str] = []
    web, published, 呼叫 = _設定(
        tmp_path,
        lambda: keyring呼叫.append("keyring") or AESGCM憑證封套({1: b"k" * 32}, 1),
    )
    published.技能套件發布根.mkdir()
    app = 建立CP4ASGI應用程式(web, published)
    with TestClient(app):
        resource = app.state.發布介面資源[1]
        assert keyring呼叫 == ["keyring"]
        assert 呼叫 == ["installer", "models"]
        assert resource._憑證管理服務 is not None
        assert resource._憑證管理代理._服務 is resource._憑證管理服務
        assert not hasattr(resource, "_憑證封套")
    assert resource._憑證管理服務 is None
    assert resource._憑證管理代理 is None


def test_master_key_bytes無法從app_state資源圖走訪(tmp_path) -> None:
    """deployment master key 只轉成 opaque crypto primitive，不留在 app state object graph。

    描述：deployment master key 只轉成 opaque crypto primitive，不留在 app state object graph。
    參數：``tmp_path``。
    返回值：無；所有驗收結果由assertions表達。
    """
    marker = b"A07-master-key-marker-value-1234"
    assert len(marker) == 32
    web, published, *_ = _設定(tmp_path, lambda: AESGCM憑證封套({1: marker}, 1))
    published.技能套件發布根.mkdir()
    app = 建立CP4ASGI應用程式(web, published)
    with TestClient(app):
        seen: set[int] = set()

        def 可達(value) -> bool:
            """循環安全地檢查 marker 是否存在於 Python-visible app state graph。

            描述：循環安全地檢查 marker 是否存在於 Python-visible app state graph。
            參數：``value``。
            返回值：依函式型別標註或既有協定回傳結果。
            """
            identity = id(value)
            if identity in seen:
                return False
            seen.add(identity)
            if type(value) is bytes:
                return value == marker
            if type(value) in (str, int, float, bool, type(None), type, bytes):
                return False
            if type(value) in (tuple, list, set, frozenset):
                return any(可達(item) for item in value)
            if type(value) in (dict, MappingProxyType):
                return any(可達(item) for pair in value.items() for item in pair)
            if type(value) is MethodType:
                return 可達(value.__self__)
            state = getattr(value, "__dict__", None)
            return type(state) is dict and 可達(state)

        assert not 可達(app.state.發布介面資源)


def test_invalid_keyring_factory於startup_fail_closed(tmp_path) -> None:
    """缺少 genuine AES-GCM envelope 時 app construction 成功但 startup 固定拒絕。

    描述：缺少 genuine AES-GCM envelope 時 app construction 成功但 startup 固定拒絕。
    參數：``tmp_path``。
    返回值：無；所有驗收結果由assertions表達。
    """
    web, published, 呼叫 = _設定(tmp_path, lambda: object())
    published.技能套件發布根.mkdir()
    app = 建立CP4ASGI應用程式(web, published)
    try:
        with TestClient(app):
            raise AssertionError("invalid keyring 不得啟動")
    except RuntimeError as 錯誤:
        assert str(錯誤) == "發布介面啟動失敗"
    assert 呼叫 == ["installer", "models"]
