"""驗證已發布技能套件載入器的 exact 定位、清單與 DTO 敵對契約。"""

import os
from pathlib import Path
import shutil
import tempfile
import traceback


import pytest

from 繁中代理.發布介面.技能套件.清單 import 正規JSON
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.載入器 import (
    已發布技能套件載入器, 技能套件定位, 技能套件載入錯誤,
)


@pytest.fixture
def 本機根() -> Path:
    """在非 symlink 的使用者目錄建立隔離樹，並修復唯讀發布樹後清除。"""
    根 = Path(tempfile.mkdtemp(prefix="loader-test-", dir="/Users/wujinan"))
    yield 根
    for 目前, 目錄們, _檔案們 in os.walk(根):
        os.chmod(目前, 0o700)
        for 名稱 in 目錄們:
            try:
                os.chmod(Path(目前) / 名稱, 0o700, follow_symlinks=False)
            except OSError:
                pass
    shutil.rmtree(根, ignore_errors=True)


class 提供者:
    """記錄 exact lookup 次數的最小 authoritative provider。"""
    def __init__(self, 定位們: dict[str, object]):
        self.定位們 = 定位們
        self.呼叫 = []

    def 取得技能套件定位(self, 版本: str) -> object:
        self.呼叫.append(版本)
        return self.定位們[版本]


def _發布(根: Path, *, 版本: str = "v1", 套件: str = "bundle1", 內容: bytes = b"alpha"):
    """建立含多技能、script 與 asset 的真實唯讀發布成果。"""
    技能們 = {}
    for 名稱 in ("alpha", "beta"):
        技能 = 根 / f"source-{版本}-{名稱}"
        (技能 / "scripts").mkdir(parents=True)
        (技能 / "assets").mkdir()
        (技能 / "SKILL.md").write_bytes((名稱 + " prompt").encode())
        (技能 / "scripts" / "run.py").write_bytes(內容 + 名稱.encode())
        (技能 / "assets" / "data.bin").write_bytes(名稱.encode())
        技能們[名稱] = 技能
    收據 = 技能套件發布器(根 / "published").發布(
        套件識別碼=套件, 端點識別碼="endpoint", 端點版本識別碼=版本,
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner", 技能表=技能們,
    )
    定位 = 技能套件定位(
        version_id=版本, bundle_id=套件, manifest_reference=收據.清單參照,
        manifest_digest=收據.清單摘要, bundle_hash=收據.套件雜湊,
        total_bytes=收據.總位元組數,
    )
    return 收據, 定位


def _載入(根: Path, 提供: 提供者, 定位: 技能套件定位):
    return 已發布技能套件載入器(根 / "published", 提供).載入技能套件快照(
        定位.version_id, 定位.bundle_hash, 定位.manifest_reference,
        "endpoint_version_snapshot",
    )


def _固定失敗(動作) -> None:
    with pytest.raises(技能套件載入錯誤) as 資訊:
        動作()
    assert str(資訊.value) == "技能套件載入失敗。"
    assert 資訊.value.__cause__ is None and 資訊.value.__suppress_context__


def test_成功重建多技能完整DTO且兩版本互不串流(本機根: Path) -> None:
    """快照含 prompt、scripts、assets；同一 provider 的兩版本只讀各自內容。"""
    _, 定位一 = _發布(本機根, 版本="v1", 套件="bundle1", 內容=b"one")
    _, 定位二 = _發布(本機根, 版本="v2", 套件="bundle2", 內容=b"two")
    提供 = 提供者({"v1": 定位一, "v2": 定位二})
    快照二 = _載入(本機根, 提供, 定位二)
    快照一 = _載入(本機根, 提供, 定位一)
    assert 提供.呼叫 == ["v2", "v1"]
    assert 快照一.endpoint_version_id == "v1" and 快照二.endpoint_version_id == "v2"
    assert {檔案.path for 檔案 in 快照一.files} == {
        "alpha/SKILL.md", "alpha/assets/data.bin", "alpha/scripts/run.py",
        "beta/SKILL.md", "beta/assets/data.bin", "beta/scripts/run.py",
    }
    assert b"onealpha" in {檔案.content for 檔案 in 快照一.files}
    assert b"twoalpha" in {檔案.content for 檔案 in 快照二.files}


@pytest.mark.parametrize("欄位,惡意", [
    ("version", "v/1"), ("version", str("v1")), ("hash", "A" * 64),
    ("reference", "../manifest.json"), ("reference", "/bundle1/manifest.json"),
    ("reference", "bundle1\\manifest.json"), ("source", "current"),
])
def test_輸入預檢失敗不觸發定位callback(本機根: Path, 欄位: str, 惡意: str) -> None:
    """invalid scalar 在 provider lookup 前關閉失敗。"""
    _, 定位 = _發布(本機根)
    class 字串子類(str):
        pass
    值 = 字串子類("v1") if 欄位 == "version" and 惡意 == "v1" else 惡意
    參數 = {"version": "v1", "hash": 定位.bundle_hash,
          "reference": 定位.manifest_reference, "source": "endpoint_version_snapshot"}
    參數[欄位] = 值
    提供 = 提供者({"v1": 定位})
    載入器 = 已發布技能套件載入器(本機根 / "published", 提供)
    _固定失敗(lambda: 載入器.載入技能套件快照(
        參數["version"], 參數["hash"], 參數["reference"], 參數["source"]))
    assert 提供.呼叫 == []


@pytest.mark.parametrize("覆寫", [
    {"version_id": "other"}, {"bundle_id": "other"},
    {"manifest_reference": "bundle1/other.json"}, {"manifest_digest": "1" * 64},
    {"bundle_hash": "2" * 64}, {"total_bytes": 1},
])
def test_定位列逐欄重建並與呼叫及清單exact比對(本機根: Path, 覆寫: dict) -> None:
    """hostile row 的 version/ref/hash/total 任一漂移都固定拒絕。"""
    _, 定位 = _發布(本機根)
    class 列:
        pass
    列值 = 列()
    for 名稱 in 定位.__dataclass_fields__:
        setattr(列值, 名稱, 覆寫.get(名稱, getattr(定位, 名稱)))
    提供 = 提供者({"v1": 列值})
    _固定失敗(lambda: _載入(本機根, 提供, 定位))
    assert 提供.呼叫 == ["v1"]


@pytest.mark.parametrize("欄位,值", [
    ("version_id", "v/1"), ("bundle_id", "b/1"),
    ("manifest_reference", "b/manifest.json"), ("manifest_digest", "A" * 64),
    ("bundle_hash", "0" * 63), ("total_bytes", True), ("total_bytes", 0),
])
def test_定位建構拒絕invalid與subclass_scalar(欄位: str, 值: object) -> None:
    """公開定位只接受 exact scalar 與欄間一致的 canonical reference。"""
    參數 = dict(version_id="v1", bundle_id="bundle1",
              manifest_reference="bundle1/manifest.json", manifest_digest="0" * 64,
              bundle_hash="1" * 64, total_bytes=1)
    參數[欄位] = 值
    _固定失敗(lambda: 技能套件定位(**參數))


def test_cwd_HOME與全域技能陷阱不會成為fallback(本機根: Path, monkeypatch) -> None:
    """缺失 authoritative bundle 時不從 cwd、HOME 或全域 skills 補讀。"""
    _, 定位 = _發布(本機根)
    陷阱根 = 本機根 / "trap"
    (陷阱根 / "skills" / "alpha").mkdir(parents=True)
    (陷阱根 / "skills" / "alpha" / "SKILL.md").write_text("SECRET")
    monkeypatch.chdir(陷阱根)
    monkeypatch.setenv("HOME", str(陷阱根))
    for 目前, 目錄們, _檔案們 in os.walk(本機根 / "published" / "bundle1"):
        os.chmod(目前, 0o700)
        for 名稱 in 目錄們:
            os.chmod(Path(目前) / 名稱, 0o700)
    shutil.rmtree(本機根 / "published" / "bundle1")
    提供 = 提供者({"v1": 定位})
    _固定失敗(lambda: _載入(本機根, 提供, 定位))
    assert 提供.呼叫 == ["v1"]


def test_普通與控制流程callback皆清除敏感traceback_locals(本機根: Path) -> None:
    """普通錯誤固定化；控制流程保持 identity，兩者停止 frame 都清空 locals。"""
    _, 定位 = _發布(本機根)
    for 原錯誤 in (RuntimeError("ordinary"), KeyboardInterrupt("stop")):
        保存 = {}
        class 敵對:
            def 取得技能套件定位(self, _版本):
                secret = bytearray(b"TOP-SECRET")
                try:
                    raise 原錯誤
                except BaseException as 錯誤:
                    保存["tb"] = 錯誤.__traceback__
                    raise
        載入器 = 已發布技能套件載入器(本機根 / "published", 敵對())
        if isinstance(原錯誤, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt) as 資訊:
                _載入(本機根, 敵對(), 定位)
            assert 資訊.value is 原錯誤 and 資訊.value.args == ("stop",)
        else:
            _固定失敗(lambda: 載入器.載入技能套件快照(
                "v1", 定位.bundle_hash, 定位.manifest_reference,
                "endpoint_version_snapshot"))
        框架 = list(traceback.walk_tb(保存["tb"]))[-1][0]
        assert "secret" not in 框架.f_locals
