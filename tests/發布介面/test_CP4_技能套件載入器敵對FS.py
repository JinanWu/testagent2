"""驗證 loader 對 manifest、檔案樹與描述元競態的敵對關閉契約。"""

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from 繁中代理.發布介面.技能套件.清單 import 正規JSON
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.載入器 import (
    已發布技能套件載入器, 技能套件定位, 技能套件載入錯誤,
)

載入模組 = importlib.import_module("繁中代理.發布介面.技能套件.載入器")


@pytest.fixture
def 本機根() -> Path:
    """使用 /Users/wujinan 下的非 symlink 暫存根並強制清除唯讀樹。"""
    根 = Path(tempfile.mkdtemp(prefix="loader-hostile-", dir="/Users/wujinan"))
    yield 根
    for 目前, 目錄們, _ in os.walk(根, followlinks=False):
        try:
            os.chmod(目前, 0o700)
        except OSError:
            pass
        for 名稱 in 目錄們:
            try:
                os.chmod(Path(目前) / 名稱, 0o700, follow_symlinks=False)
            except OSError:
                pass
    shutil.rmtree(根, ignore_errors=True)


class 提供者:
    def __init__(self, 定位: object):
        self.定位 = 定位
        self.次數 = 0

    def 取得技能套件定位(self, endpoint_version_id: str) -> object:
        self.次數 += 1
        return self.定位


def _建立(根: Path):
    技能 = 根 / "source"
    (技能 / "nested").mkdir(parents=True)
    (技能 / "SKILL.md").write_bytes(b"prompt")
    (技能 / "nested" / "data.txt").write_bytes(b"payload")
    收據 = 技能套件發布器(根 / "published").發布(
        套件識別碼="bundle", 端點識別碼="endpoint", 端點版本識別碼="v1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner", 技能表={"alpha": 技能},
    )
    定位 = 技能套件定位(
        version_id="v1", bundle_id="bundle", manifest_reference=收據.清單參照,
        manifest_digest=收據.清單摘要, bundle_hash=收據.套件雜湊,
        total_bytes=收據.總位元組數,
    )
    return 定位, 根 / "published" / "bundle"


def _載入(根: Path, 定位: 技能套件定位):
    提供 = 提供者(定位)
    載入器 = 已發布技能套件載入器(根 / "published", 提供)
    with pytest.raises(技能套件載入錯誤) as 資訊:
        載入器.載入技能套件快照(
            "v1", 定位.bundle_hash, 定位.manifest_reference,
            "endpoint_version_snapshot")
    assert str(資訊.value) == "技能套件載入失敗。"
    assert 資訊.value.__cause__ is None and 資訊.value.__suppress_context__
    assert 提供.次數 == 1


def _改清單(定位: 技能套件定位, 套件根: Path, 修改) -> 技能套件定位:
    清單路徑 = 套件根 / "manifest.json"
    清單 = json.loads(清單路徑.read_bytes())
    修改(清單)
    原文 = 正規JSON(清單)
    os.chmod(清單路徑, 0o644)
    清單路徑.write_bytes(原文)
    os.chmod(清單路徑, 0o444)
    return 技能套件定位(
        version_id=定位.version_id, bundle_id=定位.bundle_id,
        manifest_reference=定位.manifest_reference,
        manifest_digest=hashlib.sha256(原文).hexdigest(), bundle_hash=定位.bundle_hash,
        total_bytes=定位.total_bytes,
    )


@pytest.mark.parametrize("欄位,惡意", [
    ("bundle_id", "other"), ("endpoint_version_id", "v2"),
    ("bundle_hash", "0" * 64), ("total_bytes", 1),
])
def test_manifest_bundle_version_hash_total逐欄不一致皆拒絕(
    本機根: Path, 欄位: str, 惡意: object,
) -> None:
    """即使重新釘選 manifest digest，manifest 與 locator 關係仍須 exact。"""
    定位, 套件根 = _建立(本機根)
    新定位 = _改清單(定位, 套件根, lambda 清單: 清單.__setitem__(欄位, 惡意))
    _載入(本機根, 新定位)


@pytest.mark.parametrize("惡意路徑", [
    "../escape", "/absolute", "alpha\\SKILL.md", "alpha/./SKILL.md",
    "alpha/e\u0301.txt", "alpha//SKILL.md",
])
def test_manifest拒絕traversal_absolute_backslash_NFC與非canonical路徑(
    本機根: Path, 惡意路徑: str,
) -> None:
    """hostile copied path 不得進入任何 filesystem materialization。"""
    定位, 套件根 = _建立(本機根)
    新定位 = _改清單(
        定位, 套件根,
        lambda 清單: 清單["copied_files"][0].__setitem__("path", 惡意路徑),
    )
    _載入(本機根, 新定位)


@pytest.mark.parametrize("種類", ["duplicate", "order"])
def test_manifest拒絕duplicate_JSON_key與非UTF8排序(本機根: Path, 種類: str) -> None:
    """重複鍵及 copied_files 順序漂移均關閉失敗。"""
    定位, 套件根 = _建立(本機根)
    清單路徑 = 套件根 / "manifest.json"
    if 種類 == "duplicate":
        原文 = 清單路徑.read_bytes().replace(b'{"bundle_hash":', b'{"bundle_hash":"x","bundle_hash":', 1)
        os.chmod(清單路徑, 0o644); 清單路徑.write_bytes(原文); os.chmod(清單路徑, 0o444)
        新定位 = 技能套件定位(
            version_id="v1", bundle_id="bundle", manifest_reference="bundle/manifest.json",
            manifest_digest=hashlib.sha256(原文).hexdigest(), bundle_hash=定位.bundle_hash,
            total_bytes=定位.total_bytes)
    else:
        新定位 = _改清單(定位, 套件根, lambda 清單: 清單["copied_files"].reverse())
    _載入(本機根, 新定位)


@pytest.mark.parametrize("種類", [
    "root_symlink", "bundle_symlink", "intermediate_symlink", "file_symlink",
])
def test_發布根套件中間目錄與檔案symlink皆拒絕(本機根: Path, 種類: str) -> None:
    """任一層 no-follow 邊界不得接受符號連結。"""
    定位, 套件根 = _建立(本機根)
    發布根 = 本機根 / "published"
    if 種類 == "root_symlink":
        發布根.rename(本機根 / "real-published"); os.symlink(本機根 / "real-published", 發布根)
    elif 種類 == "bundle_symlink":
        os.chmod(發布根, 0o755)
        套件根.rename(發布根 / "real-bundle"); os.symlink("real-bundle", 套件根)
    else:
        目標 = 套件根 / "alpha" / ("nested" if 種類 == "intermediate_symlink" else "SKILL.md")
        os.chmod(套件根 / "alpha", 0o755)
        目標.rename(目標.with_name("real")); os.symlink("real", 目標)
    _載入(本機根, 定位)


@pytest.mark.parametrize("種類", [
    "fifo", "extra_file", "extra_dir", "missing", "root_mode", "dir_mode", "file_mode",
])
def test_完整樹拒絕special_extra_missing與模式漂移(本機根: Path, 種類: str) -> None:
    """final enumeration 必須恰等於清單且目錄 0555、檔案 0444。"""
    定位, 套件根 = _建立(本機根)
    os.chmod(套件根, 0o755)
    if 種類 == "fifo": os.mkfifo(套件根 / "pipe")
    elif 種類 == "extra_file": (套件根 / "extra").write_bytes(b"x")
    elif 種類 == "extra_dir": (套件根 / "extra").mkdir()
    elif 種類 == "missing":
        os.chmod(套件根 / "alpha", 0o755); (套件根 / "alpha" / "SKILL.md").unlink()
    elif 種類 == "dir_mode": os.chmod(套件根 / "alpha", 0o755)
    elif 種類 == "file_mode": os.chmod(套件根 / "manifest.json", 0o644)
    if 種類 != "root_mode": os.chmod(套件根, 0o555)
    _載入(本機根, 定位)


def test_檔案內容雜湊與讀取前後mtime競態皆拒絕(本機根: Path, monkeypatch) -> None:
    """同大小竄改由 hash 擋下；讀取中 mtime 漂移由 before/after identity 擋下。"""
    定位, 套件根 = _建立(本機根)
    目標 = 套件根 / "alpha" / "SKILL.md"
    os.chmod(目標, 0o644); 目標.write_bytes(b"tamper"); os.chmod(目標, 0o444)
    _載入(本機根, 定位)
    定位, 套件根 = _建立(本機根 / "race")
    目標 = 套件根 / "alpha" / "SKILL.md"
    原讀取 = 載入模組.os.read
    已改 = False
    def 讀取(fd: int, 數量: int) -> bytes:
        nonlocal 已改
        資料 = 原讀取(fd, 數量)
        if not 已改 and os.fstat(fd).st_ino == 目標.stat().st_ino:
            已改 = True; os.utime(目標, ns=(目標.stat().st_atime_ns, 目標.stat().st_mtime_ns + 1_000_000))
        return 資料
    monkeypatch.setattr(載入模組.os, "read", 讀取)
    _載入(本機根 / "race", 定位)


@pytest.mark.parametrize("種類", ["content", "manifest"])
def test_大小額度在materialization前拒絕(本機根: Path, monkeypatch, 種類: str) -> None:
    """content exact size 或 manifest 總額度超限時不得對該 inode 呼叫 read。"""
    定位, 套件根 = _建立(本機根)
    目標 = 套件根 / ("alpha/SKILL.md" if 種類 == "content" else "manifest.json")
    os.chmod(目標, 0o644)
    with 目標.open("wb") as 串流:
        串流.truncate(5 * 1024 * 1024)
    os.chmod(目標, 0o444)
    inode = 目標.stat().st_ino
    原讀取 = 載入模組.os.read
    讀取目標 = []
    def 讀取(fd: int, 數量: int) -> bytes:
        if os.fstat(fd).st_ino == inode: 讀取目標.append(True)
        return 原讀取(fd, 數量)
    monkeypatch.setattr(載入模組.os, "read", 讀取)
    _載入(本機根, 定位)
    assert 讀取目標 == []


def test_檔案visible與open間inode替換拒絕(本機根: Path, monkeypatch) -> None:
    """檔案 stat 後 open 前即使同大小替換，也不得接受新 inode。"""
    定位, 套件根 = _建立(本機根)
    目標 = 套件根 / "alpha" / "SKILL.md"
    os.chmod(目標.parent, 0o755)
    原開啟 = 載入模組.os.open
    已換 = False
    def 開啟(路徑, flags, mode=0o777, *, dir_fd=None):
        nonlocal 已換
        if 路徑 == "SKILL.md" and not 已換:
            已換 = True; 目標.rename(目標.with_name("old")); 目標.write_bytes(b"prompt"); os.chmod(目標, 0o444)
        return 原開啟(路徑, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(載入模組.os, "open", 開啟)
    _載入(本機根, 定位)


def test_發布根ABA替換在visible與open之間拒絕(本機根: Path, monkeypatch) -> None:
    """path A→B→A 時已開啟 B descriptor 不得冒充 visible A。"""
    定位, _ = _建立(本機根)
    原開啟 = 載入模組.os.open
    已換 = False
    def 開啟(路徑, flags, mode=0o777, *, dir_fd=None):
        nonlocal 已換
        if 路徑 == "published" and not 已換:
            已換 = True
            原根 = 本機根 / "published"; 舊根 = 本機根 / "old"
            原根.rename(舊根); 原根.mkdir()
            fd = 原開啟(路徑, flags, mode, dir_fd=dir_fd)
            原根.rmdir(); 舊根.rename(原根)
            return fd
        return 原開啟(路徑, flags, mode, dir_fd=dir_fd)
    monkeypatch.setattr(載入模組.os, "open", 開啟)
    _載入(本機根, 定位)
