"""鎖定技能套件孤兒隔離與啟動協調的安全、交易及額度契約。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import stat

import pytest

from 繁中代理.發布介面.技能套件.協調器 import 技能套件協調器, 技能套件協調錯誤
from 繁中代理.發布介面.技能套件 import 協調器 as 協調模組
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.安全複製 import 技能套件最大總位元組數
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _發布(tmp_path: Path, 名稱: str = "bundle-1", 版本: str = "ver-1"):
    """建立真實不可變套件並回傳發布根、來源與收據。"""
    來源 = tmp_path / f"source-{名稱}"
    來源.mkdir(exist_ok=True)
    (來源 / "SKILL.md").write_text("# safe")
    根 = tmp_path / "bundles"
    收據 = 技能套件發布器(根).發布(
        套件識別碼=名稱, 端點識別碼="ep-1", 端點版本識別碼=版本,
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
    )
    return 根, 收據


def _資料庫(tmp_path: Path, *, 有版本: bool = True) -> tuple[Path, sqlite3.Connection]:
    """建立第十二版資料庫及可選版本列。"""
    路徑 = tmp_path / "db.sqlite3"
    初始化發布介面資料庫(路徑)
    連線 = sqlite3.connect(路徑)
    連線.execute("PRAGMA foreign_keys=ON")
    連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
    連線.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
        "current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) "
        "VALUES('ep-1','owner-1','sa-1','slug','active',NULL,1,1,60,60)"
    )
    if 有版本:
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES("
            "'ver-1','ep-1',1,'r','p','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner-1',1)"
        )
    連線.commit()
    return 路徑, 連線


def test_標記孤兒完整重驗後原子移入固定隔離區(tmp_path: Path) -> None:
    """合法 authoritative receipt 才能移動，且目的地不可覆寫。"""
    根, 收據 = _發布(tmp_path)
    協調器 = 技能套件協調器(根, 孤兒保留秒數=60, 時鐘=lambda: 10.0)
    assert 協調器.標記孤兒(收據) == 根 / ".orphaned" / "bundle-1"
    assert not 收據.路徑.exists()
    assert (根 / ".orphaned" / "bundle-1" / "manifest.json").is_file()

    _, 第二 = _發布(tmp_path, "bundle-1")
    with pytest.raises(技能套件協調錯誤, match="^技能套件協調錯誤$"):
        協調器.標記孤兒(第二)
    assert 第二.路徑.exists()


@pytest.mark.parametrize("攻擊", ["receipt-path", "symlink", "hardlink"])
def test_隔離拒絕收據路徑替換符號連結與硬連結(tmp_path: Path, 攻擊: str) -> None:
    """隔離前 exact receipt、樹種類及單連結 inode 必須維持一致。"""
    根, 收據 = _發布(tmp_path)
    if 攻擊 == "receipt-path":
        object.__setattr__(收據, "路徑", tmp_path / "elsewhere" / "bundle-1")
    else:
        目標 = 收據.路徑 / "demo" / "SKILL.md"
        os.chmod(收據.路徑 / "demo", 0o755)
        os.chmod(目標, 0o644)
        if 攻擊 == "symlink":
            目標.unlink()
            os.symlink("../manifest.json", 目標)
        else:
            os.link(目標, tmp_path / "alias")
    with pytest.raises(技能套件協調錯誤, match="^技能套件協調錯誤$"):
        技能套件協調器(根, 孤兒保留秒數=60).標記孤兒(收據)
    assert 收據.路徑.exists() if 攻擊 != "receipt-path" else (根 / "bundle-1").exists()


def test_啟動協調補寫reconciled但交易仍由呼叫端所有(tmp_path: Path) -> None:
    """有版本無收據時完整重驗後新增 reconciled，且協調器不提交。"""
    根, _ = _發布(tmp_path)
    路徑, 連線 = _資料庫(tmp_path)
    結果 = 技能套件協調器(根, 孤兒保留秒數=60).啟動協調(2.0, 連線)
    assert 結果.已補收據 == ("bundle-1",)
    assert 連線.execute(
        "SELECT state,published_at,reconciled_at FROM published_skill_bundles"
    ).fetchone() == ("reconciled", 2.0, 2.0)
    連線.rollback()
    連線.close()
    with sqlite3.connect(路徑) as 驗證:
        assert 驗證.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)


def test_啟動協調無版本隔離並於明確保存期後安全刪除(tmp_path: Path) -> None:
    """缺版本的合法 active bundle 先隔離，只有下一輪超過 retention 才刪除。"""
    根, _ = _發布(tmp_path)
    _, 連線 = _資料庫(tmp_path, 有版本=False)
    協調器 = 技能套件協調器(根, 孤兒保留秒數=5, 時鐘=lambda: 10.0)
    第一輪 = 協調器.啟動協調(10.0, 連線)
    assert 第一輪.已隔離 == ("bundle-1",) and 第一輪.已刪除 == ()
    assert (根 / ".orphaned" / "bundle-1").exists()
    assert 協調器.啟動協調(14.0, 連線).已刪除 == ()
    assert 協調器.啟動協調(16.0, 連線).已刪除 == ("bundle-1",)
    assert not (根 / ".orphaned" / "bundle-1").exists()
    連線.close()


def test_啟動掃描共享256項額度且普通錯誤固定(tmp_path: Path) -> None:
    """active authority 不得無界列舉，超限不做部分協調。"""
    根 = tmp_path / "bundles"
    根.mkdir()
    for 索引 in range(257):
        (根 / f"bundle-{索引}").mkdir()
    _, 連線 = _資料庫(tmp_path)
    with pytest.raises(技能套件協調錯誤, match="^技能套件協調錯誤$") as 錯誤:
        技能套件協調器(根, 孤兒保留秒數=1).啟動協調(2.0, 連線)
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_啟動掃描第257名稱立刻關閉iterator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """>256 名稱不得先 list/sort；第 257 個 yield 後必須立刻關閉 descriptor iterator。"""
    根 = tmp_path / "bundles"
    根.mkdir()
    for 索引 in range(300):
        (根 / f"bundle-{索引}").mkdir()
    _, 連線 = _資料庫(tmp_path)
    原掃描 = os.scandir
    追蹤: list[dict[str, int | bool]] = []

    class 計數迭代器:
        def __init__(self, 路徑):
            self._內層 = 原掃描(路徑)
            self.狀態: dict[str, int | bool] = {"yielded": 0, "closed": False}
            追蹤.append(self.狀態)

        def __iter__(self):
            return self

        def __next__(self):
            項目 = next(self._內層)
            self.狀態["yielded"] = int(self.狀態["yielded"]) + 1
            return 項目

        def close(self):
            self.狀態["closed"] = True
            self._內層.close()

    monkeypatch.setattr(協調模組.os, "scandir", 計數迭代器)
    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=1).啟動協調(2.0, 連線)
    assert 追蹤 == [{"yielded": 257, "closed": True}]
    連線.close()


def test_巢狀套件樹與根共享第257項額度(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """根、套件根及巢狀技能目錄必須消耗同一個 256-entry authority。"""
    來源 = tmp_path / "source-large-tree"
    來源.mkdir()
    (來源 / "SKILL.md").write_text("# safe")
    for 索引 in range(253):
        (來源 / f"item-{索引:03}.txt").write_text("")
    根 = tmp_path / "bundles"
    技能套件發布器(根).發布(
        套件識別碼="bundle-1", 端點識別碼="ep-1", 端點版本識別碼="ver-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
    )
    _, 連線 = _資料庫(tmp_path)
    原掃描 = os.scandir
    已產出 = 0
    已關閉 = 0

    class 計數迭代器:
        def __init__(self, 路徑):
            self._內層 = 原掃描(路徑)
        def __iter__(self):
            return self
        def __next__(self):
            nonlocal 已產出
            項目 = next(self._內層)
            已產出 += 1
            return 項目
        def close(self):
            nonlocal 已關閉
            已關閉 += 1
            self._內層.close()

    monkeypatch.setattr(協調模組.os, "scandir", 計數迭代器)
    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=1).啟動協調(2.0, 連線)
    assert 已產出 == 257 and 已關閉 == 3
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_啟動掃描active與orphan共同消耗256項額度(tmp_path: Path) -> None:
    """active 與 orphan 不得各自取得 256 項額度，超限前不得移動或刪除。"""
    根 = tmp_path / "bundles"
    孤兒根 = 根 / ".orphaned"
    孤兒根.mkdir(parents=True, mode=0o700)
    for 索引 in range(128):
        (根 / f"active-{索引}").mkdir()
    for 索引 in range(129):
        (孤兒根 / f"orphan-{索引}").mkdir()
    _, 連線 = _資料庫(tmp_path)

    with pytest.raises(技能套件協調錯誤, match="^技能套件協調錯誤$"):
        技能套件協調器(根, 孤兒保留秒數=0).啟動協調(100.0, 連線)

    assert len(list(根.glob("active-*"))) == 128
    assert len(list(孤兒根.glob("orphan-*"))) == 129
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_啟動協調兩套件完整讀取共享4MiB且超限零部分異動(tmp_path: Path) -> None:
    """兩個各自合法但合計超過公開 authority 的套件必須在任何 DB／FS mutation 前拒絕。"""
    每套件內容 = (技能套件最大總位元組數 // 2) + 1
    for 索引 in range(2):
        來源 = tmp_path / f"source-{索引}"
        來源.mkdir()
        (來源 / "SKILL.md").write_bytes(b"x" * (1024 * 1024))
        (來源 / "extra.bin").write_bytes(b"y" * (1024 * 1024))
        (來源 / "tail.bin").write_bytes(b"z" * (每套件內容 - 2 * 1024 * 1024))
        技能套件發布器(tmp_path / "bundles").發布(
            套件識別碼=f"bundle-{索引}", 端點識別碼="ep-1",
            端點版本識別碼=f"missing-{索引}", 版本號碼=索引 + 1,
            建立時間=1.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
        )
    _, 連線 = _資料庫(tmp_path)

    with pytest.raises(技能套件協調錯誤, match="^技能套件協調錯誤$"):
        技能套件協調器(tmp_path / "bundles", 孤兒保留秒數=0).啟動協調(100.0, 連線)

    assert sorted(路徑.name for 路徑 in (tmp_path / "bundles").glob("bundle-*")) == ["bundle-0", "bundle-1"]
    assert not (tmp_path / "bundles" / ".orphaned").exists()
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()
