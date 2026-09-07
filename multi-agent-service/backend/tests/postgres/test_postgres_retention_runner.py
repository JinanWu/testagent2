from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from 繁中代理.發布介面 import PostgreSQL資源 as 資源模組
from 繁中代理.發布介面 import 維護
from 繁中代理.環境設定 import 讀取交易儲存設定


@pytest.fixture
def postgres環境(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql:///private_dsn_c1d?host=/cloudsql/project:region:instance",
    )
    monkeypatch.setenv(
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME", "project:region:instance"
    )
    return 讀取交易儲存設定(os.environ)


def _參數(模式: str) -> list[str]:
    return [
        "retention",
        "--backend",
        "postgres",
        "--now-epoch",
        "1",
        "--batch-limit",
        "7",
        模式,
    ]


def test_postgres依序使用validated環境設定_open_ready_action_close且每次一批(
    postgres環境, capsys
):
    事件: list[object] = []

    def 設定工廠(環境):
        assert 環境 is os.environ
        事件.append("settings")
        return postgres環境

    class 資源:
        async def 關閉(self):
            事件.append("close")

    async def 資源工廠(設定):
        assert 設定 is postgres環境
        事件.extend(("open", "ready"))
        return 資源()

    class 規劃器:
        def __init__(self, 設定):
            assert 設定 is postgres環境
            事件.append("planner")

        def 規劃(self, now, *, 候選上限):
            事件.append(("plan", now, 候選上限))
            return ()

    assert 維護.執行主程式(
        _參數("--dry-run"),
        交易設定工廠=設定工廠,
        PostgreSQL資源工廠=資源工廠,
        PostgreSQL規劃器工廠=規劃器,
    ) == 0
    assert 事件 == [
        "settings",
        "open",
        "ready",
        "planner",
        ("plan", 1.0, 7),
        "close",
    ]
    assert capsys.readouterr().err == ""

    事件.clear()

    class 清除器:
        def __init__(self, 設定):
            assert 設定 is postgres環境
            事件.append("cleaner")

        def 清除(self, now, *, 批次上限):
            事件.append(("clean", now, 批次上限))
            return SimpleNamespace(
                呼叫數=0,
                執行事件數=0,
                工具呼叫數=0,
                遮蔽數=0,
                稽核事件數=0,
            )

    assert 維護.執行主程式(
        _參數("--execute"),
        交易設定工廠=設定工廠,
        PostgreSQL資源工廠=資源工廠,
        PostgreSQL清除服務工廠=清除器,
    ) == 0
    assert 事件 == [
        "settings",
        "open",
        "ready",
        "cleaner",
        ("clean", 1.0, 7),
        "close",
    ]
    assert capsys.readouterr().err == ""


def test_postgres_exact_readiness漂移零action且partial_startup只close一次(
    postgres環境, monkeypatch, capsys
):
    事件: list[str] = []

    class 連線情境:
        def __enter__(self):
            return object()

        def __exit__(self, *_):
            return False

    class Pool:
        def connection(self):
            return 連線情境()

    monkeypatch.setattr(
        資源模組.PostgreSQL連線,
        "啟動共用連線池",
        lambda 設定: (事件.append("open"), Pool())[1],
    )

    def readiness(_連線):
        事件.append("ready")
        raise RuntimeError("PRIVATE_DSN_C1D readiness drift")

    monkeypatch.setattr(資源模組, "檢查PostgreSQL就緒", readiness)
    monkeypatch.setattr(
        資源模組.PostgreSQL連線,
        "關閉共用連線池",
        lambda: 事件.append("close"),
    )

    def 不得action(_設定):
        事件.append("action")
        raise AssertionError

    assert 維護.執行主程式(
        _參數("--dry-run"),
        交易設定工廠=lambda _環境: postgres環境,
        PostgreSQL資源工廠=資源模組.建立PostgreSQL資源,
        PostgreSQL規劃器工廠=不得action,
    ) == 1
    assert 事件 == ["open", "ready", "close"]
    捕捉 = capsys.readouterr()
    assert 捕捉.out == ""
    assert 捕捉.err == "retention maintenance failed\n"
    assert "PRIVATE_DSN_C1D" not in 捕捉.err


class OrdinaryFailure(BaseException):
    pass


@pytest.mark.parametrize("模式", ["--dry-run", "--execute"])
def test_postgres_planner或cleaner普通失敗固定化且close_once(
    postgres環境, capsys, 模式
):
    事件: list[str] = []

    class 資源:
        async def 關閉(self):
            事件.append("close")

    async def 資源工廠(_設定):
        事件.append("open-ready")
        return 資源()

    class 失敗服務:
        def __init__(self, _設定):
            事件.append("service")

        def 規劃(self, *_args, **_kwargs):
            事件.append("action")
            raise OrdinaryFailure("PRIVATE_DSN_C1D")

        def 清除(self, *_args, **_kwargs):
            事件.append("action")
            raise OrdinaryFailure("PRIVATE_DSN_C1D")

    kwargs = {
        "PostgreSQL規劃器工廠": 失敗服務,
        "PostgreSQL清除服務工廠": 失敗服務,
    }
    assert 維護.執行主程式(
        _參數(模式),
        交易設定工廠=lambda _環境: postgres環境,
        PostgreSQL資源工廠=資源工廠,
        **kwargs,
    ) == 1
    assert 事件 == ["open-ready", "service", "action", "close"]
    捕捉 = capsys.readouterr()
    assert 捕捉.out == ""
    assert 捕捉.err == "retention maintenance failed\n"
    assert "PRIVATE_DSN_C1D" not in 捕捉.err


@pytest.mark.parametrize(
    "錯誤工廠",
    [
        lambda: KeyboardInterrupt("CONTROL-C1D"),
        lambda: SystemExit("CONTROL-C1D"),
        lambda: GeneratorExit("CONTROL-C1D"),
    ],
)
def test_postgres_action控制流程保持exact且close_once(
    postgres環境, 錯誤工廠
):
    事件: list[str] = []
    原始 = 錯誤工廠()

    class 資源:
        async def 關閉(self):
            事件.append("close")

    async def 資源工廠(_設定):
        事件.append("open-ready")
        return 資源()

    class 服務:
        def __init__(self, _設定):
            pass

        def 規劃(self, *_args, **_kwargs):
            事件.append("action")
            raise 原始

    with pytest.raises(type(原始)) as 捕捉:
        維護.執行主程式(
            _參數("--dry-run"),
            交易設定工廠=lambda _環境: postgres環境,
            PostgreSQL資源工廠=資源工廠,
            PostgreSQL規劃器工廠=服務,
        )
    assert 捕捉.value is 原始
    assert 事件 == ["open-ready", "action", "close"]


def test_sqlite仍傳入未正規化的exact_path且不開postgres資源(tmp_path, capsys):
    路徑 = str((tmp_path / "retention.sqlite").resolve())
    事件: list[object] = []

    class 規劃器:
        def 規劃(self, now, *, 候選上限):
            事件.append(("plan", now, 候選上限))
            return ()

    def sqlite工廠(value):
        事件.append(("sqlite", value))
        return 規劃器()

    async def 不得開啟(_設定):
        raise AssertionError

    assert 維護.執行主程式(
        [
            "retention",
            "--backend",
            "sqlite",
            "--database",
            路徑,
            "--now-epoch",
            "2",
            "--dry-run",
        ],
        規劃器工廠=sqlite工廠,
        PostgreSQL資源工廠=不得開啟,
    ) == 0
    assert 事件 == [("sqlite", 路徑), ("plan", 2.0, 100)]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "參數",
    [
        ["retention", "--backend", "sqlite", "--now-epoch", "1", "--dry-run"],
        [
            "retention",
            "--backend",
            "postgres",
            "--database",
            "/tmp/private.sqlite",
            "--now-epoch",
            "1",
            "--dry-run",
        ],
        [
            "retention",
            "--backend",
            "postgres",
            "--dsn",
            "postgresql://PRIVATE_DSN_C1D@host/db",
            "--now-epoch",
            "1",
            "--dry-run",
        ],
    ],
)
def test_backend_database_shape與argv_DSN皆由parser拒絕(參數):
    with pytest.raises(SystemExit) as 捕捉:
        維護.執行主程式(參數)
    assert 捕捉.value.code == 2
