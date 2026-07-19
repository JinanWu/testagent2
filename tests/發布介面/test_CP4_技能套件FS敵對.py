"""驗證技能套件來源路徑與整體資源額度的敵對檔案系統契約。"""

import os
from pathlib import Path

import pytest

from 繁中代理.發布介面.技能套件.安全複製 import 技能套件安全錯誤, 掃描技能, 重驗檔案
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器, 套件發布錯誤


def _建立大型來源(根目錄: Path, 名稱: str) -> Path:
    """建立可單獨通過、合併後超過套件總額度的來源。

    參數：``根目錄`` 是測試隔離目錄；``名稱`` 是來源子目錄名稱。
    回傳：新來源路徑。例外：檔案系統錯誤原樣傳出。
    副作用：建立一個目錄及三個一般檔案。
    """
    來源 = 根目錄 / 名稱
    來源.mkdir()
    (來源 / "SKILL.md").write_bytes(b"x")
    (來源 / "a.bin").write_bytes(b"a" * (1024 * 1024))
    (來源 / "b.bin").write_bytes(b"b" * (1024 * 1024))
    return 來源


def test_整個套件共享檔案數與位元組額度且先於耐久寫入(tmp_path: Path) -> None:
    """確認多技能合計超限會在建立發布根之前失敗。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受固定發布錯誤。副作用：建立來源並嘗試發布。
    """
    第一來源 = _建立大型來源(tmp_path, "one")
    第二來源 = _建立大型來源(tmp_path, "two")
    發布根 = tmp_path / "bundles"
    發布器 = 技能套件發布器(發布根)
    with pytest.raises(套件發布錯誤):
        發布器.發布(
            套件識別碼="bundle", 端點識別碼="endpoint", 端點版本識別碼="version",
            版本號碼=1, 建立時間=1.0, 建立者識別碼="owner",
            技能表={"one": 第一來源, "two": 第二來源},
        )
    assert not 發布根.exists()


def test_來源路徑任何父層符號連結一律拒絕(tmp_path: Path) -> None:
    """確認最終根目錄不是連結也不能掩蓋父層連結。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受固定安全錯誤。副作用：建立來源與父層符號連結。
    """
    真實父層 = tmp_path / "real"
    來源 = 真實父層 / "skill"
    來源.mkdir(parents=True)
    (來源 / "SKILL.md").write_text("safe")
    別名 = tmp_path / "alias"
    os.symlink(真實父層, 別名)
    with pytest.raises(技能套件安全錯誤):
        掃描技能("demo", 別名 / "skill")


def test_重驗拒絕根目錄替換與原檔案硬連結重播(tmp_path: Path) -> None:
    """確認保存的根身分能拒絕以原 inode 偽裝的新目錄。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受固定安全錯誤。副作用：掃描後替換來源根並建立硬連結。
    """
    來源 = tmp_path / "skill"
    來源.mkdir()
    原檔案 = 來源 / "SKILL.md"
    原檔案.write_text("safe")
    掃描 = 掃描技能("demo", 來源)
    舊來源 = tmp_path / "old"
    來源.rename(舊來源)
    來源.mkdir()
    os.link(舊來源 / "SKILL.md", 來源 / "SKILL.md")
    with pytest.raises(技能套件安全錯誤):
        重驗檔案(掃描, 掃描.檔案[0])


def test_重驗拒絕中間目錄替換與原檔案硬連結重播(tmp_path: Path) -> None:
    """確認保存的每層目錄身分能拒絕中間目錄替換。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受固定安全錯誤。副作用：掃描後替換子目錄並建立硬連結。
    """
    來源 = tmp_path / "skill"
    子目錄 = 來源 / "nested"
    子目錄.mkdir(parents=True)
    (來源 / "SKILL.md").write_text("root")
    原檔案 = 子目錄 / "x.txt"
    原檔案.write_text("safe")
    掃描 = 掃描技能("demo", 來源)
    舊子目錄 = 來源 / "old"
    子目錄.rename(舊子目錄)
    子目錄.mkdir()
    os.link(舊子目錄 / "x.txt", 子目錄 / "x.txt")
    目標 = next(檔案 for 檔案 in 掃描.檔案 if 檔案.相對路徑 == "nested/x.txt")
    with pytest.raises(技能套件安全錯誤):
        重驗檔案(掃描, 目標)
