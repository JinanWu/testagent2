"""驗證技能套件耐久發布、來源重驗、碰撞與失敗點契約。"""

import errno
import json
import os
from pathlib import Path
import stat

import pytest

import 繁中代理.發布介面.技能套件.發布器 as 發布器模組
from 繁中代理.發布介面.技能套件.發布器 import (
    技能套件發布器,
    套件發布錯誤,
    套件耐久性未知,
)


def _建立來源(暫存路徑: Path) -> Path:
    """建立含必要說明與巢狀檔案的隔離技能來源。"""
    根目錄 = 暫存路徑 / "source"
    根目錄.mkdir()
    (根目錄 / "SKILL.md").write_text("# demo")
    (根目錄 / "nested").mkdir()
    (根目錄 / "nested" / "x.txt").write_text("content")
    return 根目錄


def _發布(發布器: 技能套件發布器, 來源: Path, *, 套件識別碼: str = "bundle-1"):
    """以固定端點與版本資料發布單一測試技能。"""
    return 發布器.發布(
        套件識別碼=套件識別碼,
        端點識別碼="endpoint-1",
        端點版本識別碼="version-1",
        版本號碼=1,
        建立時間=1.0,
        建立者識別碼="owner-1",
        技能表={"demo": 來源},
    )


def test_耐久發布與相同雜湊冪等(tmp_path):
    """相同來源重送應回同一收據且清單對應內容雜湊。"""
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    第一筆 = _發布(發布器, 來源)
    第二筆 = _發布(發布器, 來源)
    assert 第二筆 == 第一筆
    assert 第一筆.路徑.name == "bundle-1"
    assert (第一筆.路徑 / "demo" / "nested" / "x.txt").read_text() == "content"
    原始清單 = (第一筆.路徑 / "manifest.json").read_bytes()
    assert json.loads(原始清單)["bundle_hash"] == 第一筆.套件雜湊


def test_不同雜湊碰撞拒絕且不覆寫(tmp_path):
    """相同識別碼的不同內容不得改寫既有清單。"""
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    收據 = _發布(發布器, 來源)
    原始清單 = (收據.路徑 / "manifest.json").read_bytes()
    (來源 / "nested" / "x.txt").write_text("changed")
    with pytest.raises(套件發布錯誤):
        _發布(發布器, 來源)
    assert (收據.路徑 / "manifest.json").read_bytes() == 原始清單


def test_來源重驗失敗會清除暫存目錄(tmp_path, monkeypatch):
    """掃描後替換來源檔時，發布需拒絕且不留下暫存目錄。"""
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    原始掃描 = 發布器._掃描

    def 掃描後替換(技能表):
        掃描列 = 原始掃描(技能表)
        目標 = 來源 / "nested" / "x.txt"
        目標.unlink()
        目標.write_text("replacement")
        return 掃描列

    monkeypatch.setattr(發布器, "_掃描", 掃描後替換)
    with pytest.raises(套件發布錯誤):
        _發布(發布器, 來源)
    assert not (tmp_path / "bundles" / "bundle-1").exists()
    assert list((tmp_path / "bundles").glob(".stage-*")) == []


@pytest.mark.parametrize("失敗步驟", ["file_fsync", "manifest_fsync", "stage_fsync", "rename", "parent_fsync"])
def test_耐久性失敗點不留下暫存目錄(tmp_path, 失敗步驟):
    """每個耐久步驟失敗都清除暫存；改名後失敗不得假裝回滾。"""
    來源 = _建立來源(tmp_path)

    def 失敗點(名稱):
        if 名稱 == 失敗步驟:
            raise OSError(失敗步驟)

    發布器 = 技能套件發布器(tmp_path / "bundles", 失敗點=失敗點)
    with pytest.raises(套件發布錯誤):
        _發布(發布器, 來源)
    assert list((tmp_path / "bundles").glob(".stage-*")) == []
    if 失敗步驟 != "parent_fsync":
        assert not (tmp_path / "bundles" / "bundle-1").exists()
    else:
        assert (tmp_path / "bundles" / "bundle-1").exists()


def test_相同內容雜湊忽略建立中繼資料但維持端點版本身分(tmp_path: Path) -> None:
    """確認 created_at 可不同，而端點或版本身分不同仍碰撞。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：身分不一致只接受固定碰撞錯誤。副作用：發布並重讀同一最終目錄。
    """
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    第一筆 = _發布(發布器, 來源)
    第二筆 = 發布器.發布(
        套件識別碼="bundle-1", 端點識別碼="endpoint-1", 端點版本識別碼="version-1",
        版本號碼=1, 建立時間=2.0, 建立者識別碼="another-owner", 技能表={"demo": 來源},
    )
    assert 第二筆 == 第一筆
    with pytest.raises(套件發布錯誤):
        發布器.發布(
            套件識別碼="bundle-1", 端點識別碼="endpoint-2", 端點版本識別碼="version-1",
            版本號碼=1, 建立時間=2.0, 建立者識別碼="owner-1", 技能表={"demo": 來源},
        )


@pytest.mark.parametrize("竄改種類", ["content", "extra", "symlink"])
def test_既有最終目錄重驗內容種類與額外項目(tmp_path: Path, 竄改種類: str) -> None:
    """確認同雜湊重送前會拒絕內容竄改、額外檔與符號連結。

    參數：``tmp_path`` 是隔離目錄；``竄改種類`` 選擇敵對變更。回傳：無。
    例外：只接受固定碰撞錯誤。副作用：發布後暫時放寬權限並竄改成果。
    """
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    收據 = _發布(發布器, 來源)
    os.chmod(收據.路徑, 0o755)
    技能目錄 = 收據.路徑 / "demo"
    os.chmod(技能目錄, 0o755)
    目標 = 技能目錄 / "SKILL.md"
    if 竄改種類 == "content":
        os.chmod(目標, 0o644)
        目標.write_text("EVIL")
    elif 竄改種類 == "extra":
        (技能目錄 / "extra").write_text("EVIL")
    else:
        os.chmod(目標, 0o644)
        目標.unlink()
        os.symlink("nested/x.txt", 目標)
    with pytest.raises(套件發布錯誤):
        _發布(發布器, 來源)


def test_最終檔案與目錄皆為不可變模式(tmp_path: Path) -> None:
    """確認發布成果所有一般檔為 0444 且所有目錄為 0555。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：契約違反由 pytest 回報。副作用：發布並查詢成果模式。
    """
    收據 = _發布(技能套件發布器(tmp_path / "bundles"), _建立來源(tmp_path))
    for 根, 目錄列, 檔案列 in os.walk(收據.路徑):
        assert stat.S_IMODE(Path(根).stat().st_mode) == 0o555
        for 名稱 in 目錄列:
            assert stat.S_IMODE((Path(根) / 名稱).stat().st_mode) == 0o555
        for 名稱 in 檔案列:
            assert stat.S_IMODE((Path(根) / 名稱).stat().st_mode) == 0o444


def test_競爭者建立空最終目錄不得被原子改名覆寫(tmp_path: Path) -> None:
    """確認改名前出現的空碰撞目錄會保留且發布失敗。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受固定發布錯誤。副作用：在 rename 失敗點建立競爭目錄。
    """
    來源 = _建立來源(tmp_path)
    最終目錄 = tmp_path / "bundles" / "bundle-1"

    def 建立競爭者(名稱: str) -> None:
        """在改名前建立空碰撞目錄。

        參數：``名稱`` 是線上失敗點名稱。回傳：無。例外：系統錯誤原樣傳出。
        副作用：命中 rename 時建立最終目錄。
        """
        if 名稱 == "rename":
            最終目錄.mkdir()

    with pytest.raises(套件發布錯誤):
        _發布(技能套件發布器(tmp_path / "bundles", 失敗點=建立競爭者), 來源)
    assert 最終目錄.is_dir()
    assert list(最終目錄.iterdir()) == []


def test_平台不支援不可覆寫改名時關閉失敗(tmp_path: Path, monkeypatch) -> None:
    """確認缺少原子 no-replace primitive 時不降級為一般 rename。

    參數：``tmp_path`` 是隔離目錄；``monkeypatch`` 注入平台故障。回傳：無。
    例外：只接受固定發布錯誤。副作用：暫時替換模組原子改名函式。
    """
    def 不支援(_來源: Path, _目標: Path) -> None:
        """模擬平台不支援原子不可覆寫改名。

        參數：來源與目標均未使用。回傳：無。例外：固定拋出 ``OSError``。
        副作用：不修改檔案系統。
        """
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr(發布器模組, "_不可覆寫改名", 不支援)
    with pytest.raises(套件發布錯誤):
        _發布(技能套件發布器(tmp_path / "bundles"), _建立來源(tmp_path))
    assert not (tmp_path / "bundles" / "bundle-1").exists()


def test_父目錄同步失敗攜帶可辨識收據(tmp_path: Path) -> None:
    """確認 rename 後 fsync 失敗以專用例外攜帶 authoritative receipt。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受 ``套件耐久性未知``。副作用：發布並在父目錄同步前注入失敗。
    """
    def 失敗點(名稱: str) -> None:
        """只在父目錄同步步驟注入系統錯誤。

        參數：``名稱`` 是線上失敗點。回傳：無。例外：命中時拋出 ``OSError``。
        副作用：不修改檔案系統。
        """
        if 名稱 == "parent_fsync":
            raise OSError("parent_fsync")

    with pytest.raises(套件耐久性未知) as 捕捉:
        _發布(技能套件發布器(tmp_path / "bundles", 失敗點=失敗點), _建立來源(tmp_path))
    assert 捕捉.value.收據.路徑.exists()
    assert 捕捉.value.收據.套件識別碼 == "bundle-1"


@pytest.mark.parametrize(
    ("欄位", "惡意值"),
    [
        ("manifest_version", True), ("bundle_id", []), ("endpoint_id", "../endpoint"),
        ("endpoint_version_id", ""), ("version_number", True),
        ("created_at", {"not": "a timestamp"}),
        ("created_by_user_id", []), ("source_skills", "not-a-list"),
        ("excluded_files", {"not": "a-list"}), ("warnings", "not-a-list"),
        ("total_bytes", True), ("bundle_hash", "bad"),
    ],
)
def test_既有正規清單所有頂層型別與界限損毀固定拒絕(
    tmp_path: Path, 欄位: str, 惡意值: object
) -> None:
    """重現 R2：canonical manifest 的每類 hostile metadata 都不能取得 authoritative receipt。

    參數：隔離目錄及參數化欄位和值描述損毀。回傳：無。
    例外：重送只接受 ``套件發布錯誤``。副作用：發布後改寫既有正規清單。
    """
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    收據 = _發布(發布器, 來源)
    清單路徑 = 收據.路徑 / "manifest.json"
    清單 = json.loads(清單路徑.read_bytes())
    清單[欄位] = 惡意值
    os.chmod(清單路徑, 0o644)
    清單路徑.write_bytes(發布器模組.正規JSON(清單))
    os.chmod(清單路徑, 0o444)
    with pytest.raises(套件發布錯誤):
        _發布(發布器, 來源)


def test_新清單在耐久寫入前套用完整結構重驗(tmp_path: Path) -> None:
    """新產生的負建立時間 manifest 也須在建立發布根前關閉失敗。

    參數：``tmp_path`` 是隔離目錄。回傳：無。例外：只接受固定發布錯誤。
    副作用：掃描來源，但不得建立發布根或寫入套件。
    """
    來源 = _建立來源(tmp_path)
    發布根 = tmp_path / "bundles"
    with pytest.raises(套件發布錯誤):
        技能套件發布器(發布根).發布(
            套件識別碼="bundle-1", 端點識別碼="endpoint-1",
            端點版本識別碼="version-1", 版本號碼=1, 建立時間=-1.0,
            建立者識別碼="owner-1", 技能表={"demo": 來源},
        )
    assert not 發布根.exists()


@pytest.mark.parametrize(
    "損毀",
    ["source-extra-key", "source-hash-relation", "source-name-relation", "copied-unsorted",
     "excluded-reason", "excluded-path-relation", "warning-entry", "hash-table-relation"],
)
def test_既有正規清單巢狀結構與欄間關係損毀固定拒絕(tmp_path: Path, 損毀: str) -> None:
    """重現 R2：來源、複製、排除、警告與摘要關係均須從 canonical bytes 重驗。

    參數：``tmp_path`` 隔離成果；``損毀`` 選擇實際 hostile 關係。回傳：無。
    例外：重送只接受固定發布錯誤。副作用：發布、改寫清單並再次讀取。
    """
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    收據 = _發布(發布器, 來源)
    清單路徑 = 收據.路徑 / "manifest.json"
    清單 = json.loads(清單路徑.read_bytes())
    if 損毀 == "source-extra-key": 清單["source_skills"][0]["extra"] = 1
    elif 損毀 == "source-hash-relation": 清單["source_skills"][0]["source_hash"] = "0" * 64
    elif 損毀 == "source-name-relation": 清單["source_skills"][0]["name"] = "other"
    elif 損毀 == "copied-unsorted": 清單["copied_files"].reverse()
    elif 損毀 == "excluded-reason":
        清單["excluded_files"] = [{"path": "demo/cache.tmp", "reason": "invented"}]
    elif 損毀 == "excluded-path-relation":
        清單["excluded_files"] = [{"path": "other/cache.tmp", "reason": "fixed_excluded_file"}]
    elif 損毀 == "warning-entry": 清單["warnings"] = ["invented"]
    else: 清單["copied_file_hashes"] = {}
    os.chmod(清單路徑, 0o644)
    清單路徑.write_bytes(發布器模組.正規JSON(清單))
    os.chmod(清單路徑, 0o444)
    with pytest.raises(套件發布錯誤):
        _發布(發布器, 來源)
