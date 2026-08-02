"""鎖定技能套件孤兒隔離與啟動協調的安全、交易及額度契約。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys

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


def test_開安全絕對目錄close釋放後失敗不重關舊fd且不洩漏next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ownership transfer 後 close 已釋放舊 fd 時，外層不得再 close stale fd。"""
    目標 = tmp_path / "parent" / "target"
    目標.mkdir(parents=True)
    原開啟 = 協調模組.os.open
    原關閉 = 協調模組._系統關閉
    已開啟: list[int] = []
    關閉紀錄: list[int] = []
    重用哨兵: list[int] = []

    def 記錄開啟(*參數, **關鍵字):
        描述元 = 原開啟(*參數, **關鍵字)
        已開啟.append(描述元)
        return 描述元

    def 首次關閉後失敗(描述元: int):
        關閉紀錄.append(描述元)
        原關閉(描述元)
        if len(關閉紀錄) == 1:
            # 真正重用剛釋放的 fd；若外層誤關 stale old，會關掉此 sentinel。
            哨兵 = 原開啟(os.devnull, os.O_RDONLY)
            assert 哨兵 == 描述元
            重用哨兵.append(哨兵)
            raise OSError("close-after-release")

    monkeypatch.setattr(協調模組.os, "open", 記錄開啟)
    monkeypatch.setattr(協調模組, "_系統關閉", 首次關閉後失敗)
    try:
        with pytest.raises(OSError, match="close-after-release"):
            協調模組._開安全絕對目錄(目標)

        assert len(已開啟) == 2
        assert 關閉紀錄 == 已開啟
        assert len(重用哨兵) == 1
        os.fstat(重用哨兵[0])
        with pytest.raises(OSError):
            os.fstat(已開啟[1])
    finally:
        for 描述元 in 重用哨兵:
            原關閉(描述元)


def test_開安全絕對目錄舊close成功後下一opcode控制仍清理next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """old close 回傳後、下一 opcode 前注入控制，outer owned slot 必須已持有 next。"""
    目標 = tmp_path / "parent" / "target"
    目標.mkdir(parents=True)
    原開啟 = 協調模組.os.open
    原關閉 = 協調模組._系統關閉
    原控制 = KeyboardInterrupt("after-old-close-before-next-opcode", 23)
    已開啟: list[int] = []
    關閉紀錄: list[int] = []
    舊關閉已回傳 = False

    def 記錄開啟(*參數, **關鍵字):
        描述元 = 原開啟(*參數, **關鍵字)
        已開啟.append(描述元)
        return 描述元

    def 記錄關閉(描述元: int):
        nonlocal 舊關閉已回傳
        關閉紀錄.append(描述元)
        原關閉(描述元)
        if len(關閉紀錄) == 1:
            舊關閉已回傳 = True

    def opcode追蹤(框架, 事件, 參數):
        if 框架.f_code is 協調模組._開安全絕對目錄.__code__:
            框架.f_trace_opcodes = True
            if 事件 == "opcode" and 舊關閉已回傳:
                raise 原控制
        return opcode追蹤

    monkeypatch.setattr(協調模組.os, "open", 記錄開啟)
    monkeypatch.setattr(協調模組, "_系統關閉", 記錄關閉)
    sys.settrace(opcode追蹤)
    try:
        with pytest.raises(KeyboardInterrupt) as 捕捉:
            協調模組._開安全絕對目錄(目標)
    finally:
        sys.settrace(None)

    assert 捕捉.value is 原控制 and 捕捉.value.args == ("after-old-close-before-next-opcode", 23)
    assert len(已開啟) == 2
    assert 關閉紀錄 == 已開啟
    for 描述元 in 已開啟:
        with pytest.raises(OSError):
            os.fstat(描述元)


def test_開安全絕對目錄close釋放前失敗不猜測重試並保留控制物件(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """close 是否釋放不明時不得重試 stale fd；next cleanup 不得遮蔽控制例外。"""
    目標 = tmp_path / "parent" / "target"
    目標.mkdir(parents=True)
    原開啟 = 協調模組.os.open
    原關閉 = 協調模組._系統關閉
    原控制 = KeyboardInterrupt("close-before-release", 17)
    已開啟: list[int] = []
    關閉紀錄: list[int] = []

    def 記錄開啟(*參數, **關鍵字):
        描述元 = 原開啟(*參數, **關鍵字)
        已開啟.append(描述元)
        return 描述元

    def 首次關閉前控制失敗(描述元: int):
        關閉紀錄.append(描述元)
        if len(關閉紀錄) == 1:
            raise 原控制
        原關閉(描述元)
        raise OSError("next-cleanup-after-release")

    monkeypatch.setattr(協調模組.os, "open", 記錄開啟)
    monkeypatch.setattr(協調模組, "_系統關閉", 首次關閉前控制失敗)
    try:
        with pytest.raises(KeyboardInterrupt) as 捕捉:
            協調模組._開安全絕對目錄(目標)

        assert 捕捉.value is 原控制 and 捕捉.value.args == ("close-before-release", 17)
        assert len(已開啟) == 2
        assert 關閉紀錄 == 已開啟
        assert len(關閉紀錄) == len(set(關閉紀錄))
        os.fstat(已開啟[0])
        with pytest.raises(OSError):
            os.fstat(已開啟[1])
    finally:
        if 已開啟:
            try:
                原關閉(已開啟[0])
            except OSError:
                pass


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


def test_重驗後manifest替換不改變釘選DB決策(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """協調 DB lookup 與 insert 只可使用 descriptor 驗證時取得的 immutable projection。"""
    根, _ = _發布(tmp_path)
    _, 連線 = _資料庫(tmp_path)
    原重驗 = 協調模組._重驗套件
    已替換 = False

    def 重驗後替換(*參數, **關鍵字):
        nonlocal 已替換
        結果 = 原重驗(*參數, **關鍵字)
        if not 已替換 and 結果.收據.套件識別碼 == "bundle-1":
            清單路徑 = 根 / "bundle-1" / "manifest.json"
            os.chmod(清單路徑, 0o644)
            清單路徑.write_bytes(b"{}")
            已替換 = True
        return 結果

    monkeypatch.setattr(協調模組, "_重驗套件", 重驗後替換)
    結果 = 技能套件協調器(根, 孤兒保留秒數=60).啟動協調(2.0, 連線)

    assert 結果.已補收據 == ("bundle-1",)
    assert 連線.execute("SELECT version_id,bundle_id FROM published_skill_bundles").fetchone() == (
        "ver-1", "bundle-1",
    )
    連線.close()


def test_啟動完整讀取共享4MiB接受精確邊界(tmp_path: Path) -> None:
    """manifest 與內容完整讀取總和恰好等於公開 authority 時仍應成功。"""
    來源 = tmp_path / "source-boundary"
    來源.mkdir()
    for 索引 in range(3):
        (來源 / f"part-{索引}.bin").write_bytes(bytes([索引]) * (1024 * 1024))
    (來源 / "SKILL.md").write_bytes(b"s" * ((1024 * 1024) - 4096))
    發布器 = 技能套件發布器(tmp_path / "bundles")
    初次 = 發布器.發布(
        套件識別碼="bundle-1", 端點識別碼="ep-1", 端點版本識別碼="ver-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
    )
    清單大小 = (初次.路徑 / "manifest.json").stat().st_size
    for 目前, 目錄列, 檔案列 in os.walk(初次.路徑, topdown=False):
        for 名稱 in 檔案列:
            os.chmod(Path(目前) / 名稱, 0o644)
        for 名稱 in 目錄列:
            os.chmod(Path(目前) / 名稱, 0o755)
        os.chmod(目前, 0o755)
    shutil.rmtree(初次.路徑)
    (來源 / "SKILL.md").write_bytes(b"s" * ((1024 * 1024) - 清單大小))
    收據 = 發布器.發布(
        套件識別碼="bundle-1", 端點識別碼="ep-1", 端點版本識別碼="ver-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
    )
    assert 收據.總位元組數 + (收據.路徑 / "manifest.json").stat().st_size == 技能套件最大總位元組數
    _, 連線 = _資料庫(tmp_path)

    結果 = 技能套件協調器(tmp_path / "bundles", 孤兒保留秒數=60).啟動協調(2.0, 連線)

    assert 結果.已補收據 == ("bundle-1",)
    連線.close()


def test_隔離失敗回復收據並保留呼叫端既有交易(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """receipt write 後隔離失敗須 rollback to/release 自有 savepoint，不回滾外層交易。"""
    根, _ = _發布(tmp_path, "bundle-a", "ver-1")
    _發布(tmp_path, "bundle-b", "missing-1")
    _, 連線 = _資料庫(tmp_path)
    連線.execute("UPDATE published_endpoints SET updated_at=9 WHERE id='ep-1'")
    monkeypatch.setattr(技能套件協調器, "_隔離已重驗套件", lambda *_: (_ for _ in ()).throw(OSError()))

    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=1).啟動協調(2.0, 連線)

    assert 連線.in_transaction
    assert 連線.execute("SELECT updated_at FROM published_endpoints WHERE id='ep-1'").fetchone() == (9.0,)
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.rollback()
    連線.close()


def test_retention失敗回復收據及自建交易狀態(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """到期孤兒刪除失敗時，新增收據與 coordinator 自建外層交易都必須回復。"""
    根, _ = _發布(tmp_path, "bundle-a", "ver-1")
    _, 孤兒收據 = _發布(tmp_path, "bundle-b", "missing-1")
    協調器 = 技能套件協調器(根, 孤兒保留秒數=1, 時鐘=lambda: 1.0)
    協調器.標記孤兒(孤兒收據)
    _, 連線 = _資料庫(tmp_path)
    monkeypatch.setattr(技能套件協調器, "_刪除已重驗孤兒", lambda *_: (_ for _ in ()).throw(OSError()))

    with pytest.raises(技能套件協調錯誤):
        協調器.啟動協調(10.0, 連線)

    assert not 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_隔離close失敗回復收據及自建交易狀態(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔離完成後 descriptor close 的普通失敗仍須回復本輪 receipt writes。"""
    根, _ = _發布(tmp_path, "bundle-a", "ver-1")
    _發布(tmp_path, "bundle-b", "missing-1")
    _, 連線 = _資料庫(tmp_path)
    原改名 = 協調模組._不可覆寫改名at
    原關閉 = 協調模組._系統關閉
    狀態 = {"改名": False, "已失敗": False}

    def 改名後啟用(*參數):
        原改名(*參數)
        狀態["改名"] = True

    def 關閉後失敗(描述元: int):
        原關閉(描述元)
        if 狀態["改名"] and not 狀態["已失敗"]:
            狀態["已失敗"] = True
            raise OSError("close")

    monkeypatch.setattr(協調模組, "_不可覆寫改名at", 改名後啟用)
    monkeypatch.setattr(協調模組, "_系統關閉", 關閉後失敗)
    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=1).啟動協調(2.0, 連線)
    assert 狀態["已失敗"] and not 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_control與close失敗保留原identity及args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mutation control failure 展開時的 close failure 不得替換原物件或參數。"""
    根, _ = _發布(tmp_path, "bundle-a", "ver-1")
    _發布(tmp_path, "bundle-b", "missing-1")
    _, 連線 = _資料庫(tmp_path)
    原關閉 = 協調模組._系統關閉
    原控制 = KeyboardInterrupt("原控制", 7)
    狀態 = {"控制": False, "已失敗": False}

    def 控制改名(*_):
        狀態["控制"] = True
        raise 原控制

    def 關閉後失敗(描述元: int):
        原關閉(描述元)
        if 狀態["控制"] and not 狀態["已失敗"]:
            狀態["已失敗"] = True
            raise OSError("close")

    monkeypatch.setattr(協調模組, "_不可覆寫改名at", 控制改名)
    monkeypatch.setattr(協調模組, "_系統關閉", 關閉後失敗)
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        技能套件協調器(根, 孤兒保留秒數=1).啟動協調(2.0, 連線)
    assert 捕捉.value is 原控制 and 捕捉.value.args == ("原控制", 7)
    assert 狀態["已失敗"] and not 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def _發布大量孤兒(tmp_path: Path, *, 額外檔案數: int = 128) -> tuple[Path, 技能套件協調器]:
    """建立會令舊版 preflight 加 deletion 重複列舉超過 256 項的到期孤兒。"""
    來源 = tmp_path / "source-many"
    來源.mkdir()
    (來源 / "SKILL.md").write_text("# safe")
    for 索引 in range(額外檔案數):
        (來源 / f"item-{索引:03}.txt").write_text(str(索引))
    根 = tmp_path / "bundles"
    收據 = 技能套件發布器(根).發布(
        套件識別碼="bundle-many", 端點識別碼="ep-1", 端點版本識別碼="missing-many",
        版本號碼=2, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
    )
    協調器 = 技能套件協調器(根, 孤兒保留秒數=0, 時鐘=lambda: 1.0)
    協調器.標記孤兒(收據)
    return 根, 協調器


def test_到期刪除依完整投影且不再次消耗共享預算(tmp_path: Path) -> None:
    """129 個技能檔只在 preflight 消耗 authority，mutation 依投影重驗後可完整刪除。"""
    根, 協調器 = _發布大量孤兒(tmp_path)
    _, 連線 = _資料庫(tmp_path)

    結果 = 協調器.啟動協調(10.0, 連線)

    assert 結果.已刪除 == ("bundle-many",)
    assert not (根 / ".orphaned" / "bundle-many").exists()
    連線.close()


def test_129項投影不一致在任何unlink前拒絕且before等於after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最後一檔 metadata 在 preflight 後改變時，exact revalidation 不得先部分刪除前項。"""
    根, 協調器 = _發布大量孤兒(tmp_path)
    _, 連線 = _資料庫(tmp_path)
    原預檢 = 協調器._預檢啟動
    套件根 = 根 / ".orphaned" / "bundle-many"
    變更後快照: list[tuple[str, int, int, int]] = []

    def 快照() -> list[tuple[str, int, int, int]]:
        """以路徑、種類模式、大小與 inode 比較整棵測試樹。"""
        return sorted(
            (
                路徑.relative_to(套件根).as_posix(),
                stat.S_IFMT(路徑.lstat().st_mode) | stat.S_IMODE(路徑.lstat().st_mode),
                路徑.lstat().st_size,
                路徑.lstat().st_ino,
            )
            for 路徑 in 套件根.rglob("*")
        )

    def 預檢後改變最後項目():
        """保留 preflight projection 後只改變排序最後一檔的模式。"""
        結果 = 原預檢()
        目標 = 套件根 / "demo" / "item-127.txt"
        os.chmod(目標, 0o644)
        變更後快照.extend(快照())
        return 結果

    unlink呼叫: list[str] = []
    原unlink = 協調模組.os.unlink

    def 記錄unlink(名稱, *參數, **關鍵字):
        """記錄任何不應發生的 unlink 後仍呼叫真實 primitive。"""
        unlink呼叫.append(os.fspath(名稱))
        return 原unlink(名稱, *參數, **關鍵字)

    monkeypatch.setattr(協調器, "_預檢啟動", 預檢後改變最後項目)
    monkeypatch.setattr(協調模組.os, "unlink", 記錄unlink)
    with pytest.raises(技能套件協調錯誤):
        協調器.啟動協調(10.0, 連線)

    assert unlink呼叫 == []
    assert 快照() == 變更後快照
    assert not 連線.in_transaction
    連線.close()


def test_uuid失敗會回滾自建BEGIN並恢復原交易狀態(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEGIN 後 savepoint 名稱產生失敗仍在 setup 保護流程內。"""
    根, _ = _發布(tmp_path)
    _, 連線 = _資料庫(tmp_path)
    monkeypatch.setattr(協調模組.uuid, "uuid4", lambda: (_ for _ in ()).throw(OSError("uuid")))

    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=60).啟動協調(2.0, 連線)

    assert not 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_authorizer拒絕SAVEPOINT會回滾自建BEGIN(tmp_path: Path) -> None:
    """SAVEPOINT 未建立時不得呼叫 rollback-to，且 coordinator 自建交易必須回滾。"""
    根, _ = _發布(tmp_path)
    _, 連線 = _資料庫(tmp_path)

    def 授權(動作, *_):
        """只拒絕 SQLite SAVEPOINT opcode。"""
        return sqlite3.SQLITE_DENY if 動作 == sqlite3.SQLITE_SAVEPOINT else sqlite3.SQLITE_OK

    連線.set_authorizer(授權)
    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=60).啟動協調(2.0, 連線)
    連線.set_authorizer(None)

    assert not 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.close()


def test_nested呼叫端交易遇SAVEPOINT拒絕不回滾caller也不留協調狀態(tmp_path: Path) -> None:
    """caller 已有交易而 SAVEPOINT 未建立時，只傳出失敗並完整保留 caller state。"""
    根, _ = _發布(tmp_path)
    _, 連線 = _資料庫(tmp_path)
    連線.execute("UPDATE published_endpoints SET updated_at=17 WHERE id='ep-1'")

    def 授權(動作, *_):
        """拒絕 savepoint setup，允許 caller 既有交易中的查詢。"""
        return sqlite3.SQLITE_DENY if 動作 == sqlite3.SQLITE_SAVEPOINT else sqlite3.SQLITE_OK

    連線.set_authorizer(授權)
    with pytest.raises(技能套件協調錯誤):
        技能套件協調器(根, 孤兒保留秒數=60).啟動協調(2.0, 連線)
    連線.set_authorizer(None)

    assert 連線.in_transaction
    assert 連線.execute("SELECT updated_at FROM published_endpoints WHERE id='ep-1'").fetchone() == (17.0,)
    assert 連線.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (0,)
    連線.rollback()
    連線.close()


def test_R4六個public與helper文件皆具四章() -> None:
    """R4 觸及的六個公開／helper callable 都明載參數、回傳、例外與副作用。"""
    物件列 = (
        協調模組._安全刪樹,
        技能套件協調器._預檢啟動,
        技能套件協調器._隔離已重驗套件,
        技能套件協調器._刪除已重驗孤兒,
        技能套件協調器.標記孤兒,
        技能套件協調器.啟動協調,
    )
    for 物件 in 物件列:
        文件 = 物件.__doc__ or ""
        assert all(章 in 文件 for 章 in ("參數：", "回傳：", "例外：", "副作用：")), 物件.__name__
