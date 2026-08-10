"""驗證技能套件耐久發布、來源重驗、碰撞與失敗點契約。"""

import errno
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import cast

import pytest

import 繁中代理.發布介面.技能套件.發布器 as 發布器模組
from 繁中代理.發布介面.技能套件.發布器 import (
    技能套件發布器,
    套件發布錯誤,
    套件耐久性未知,
    已驗證技能套件清單,
    驗證已發布技能套件清單,
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


def _含敵意清單資料(值: object, 標記: str, 已看: set[int] | None = None) -> bool:
    """只走訪測試已知容器，判斷 traceback local 是否保留清單標記。

    參數：``值`` 是框架 local；``標記`` 是敵意來源片段；``已看`` 防止循環。
    回傳：值樹含標記文字或位元組時為真。例外：不執行任意物件 accessor。
    副作用：只配置物件身分集合。
    """
    已看 = set() if 已看 is None else 已看
    if id(值) in 已看:
        return False
    已看.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is bytes:
        return 標記.encode() in 值
    if type(值) in (tuple, list, set):
        容器 = cast(tuple[object, ...] | list[object] | set[object], 值)
        return any(_含敵意清單資料(項, 標記, 已看) for 項 in 容器)
    if type(值) is dict:
        對照 = cast(dict[object, object], 值)
        return any(
            _含敵意清單資料(鍵, 標記, 已看) or _含敵意清單資料(項, 標記, 已看)
            for 鍵, 項 in 對照.items()
        )
    if isinstance(值, BaseException):
        return _含敵意清單資料(值.args, 標記, 已看)
    return False


def _斷言發布器框架已清理(錯誤: BaseException, 標記: str) -> list[str]:
    """以實際 ``f_locals`` 證明所有發布器 traceback frame 不含敵意清單資料。

    參數：``錯誤`` 是公開驗證器傳出的例外；``標記`` 識別 bytes、文字與 metadata。
    回傳：被檢查的生產框架名稱。例外：殘留資料時由斷言回報。
    副作用：只讀取 traceback 與框架 locals。
    """
    框架 = []
    追蹤 = 錯誤.__traceback__
    while 追蹤 is not None:
        if 追蹤.tb_frame.f_code.co_filename.endswith("技能套件/發布器.py"):
            框架.append(追蹤.tb_frame.f_code.co_name)
            assert all(
                not _含敵意清單資料(值, 標記)
                for 值 in 追蹤.tb_frame.f_locals.values()
            )
        追蹤 = 追蹤.tb_next
    assert 框架
    return 框架


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


def test_C3_公開清單投影與發布收據及publisher驗證路徑一致(tmp_path: Path, monkeypatch) -> None:
    """確認 public projection immutable，且新發布直接重用同一 validator authority。

    參數：``tmp_path`` 建立隔離成果；``monkeypatch`` 記錄 publisher 驗證呼叫。
    回傳：無。例外：契約違反由 pytest 回報。副作用：發布兩個不可變測試套件。
    """
    來源 = _建立來源(tmp_path)
    發布器 = 技能套件發布器(tmp_path / "bundles")
    收據 = _發布(發布器, 來源)
    原始資料 = (收據.路徑 / "manifest.json").read_bytes()
    投影 = 驗證已發布技能套件清單(原始資料)
    assert type(投影) is 已驗證技能套件清單
    assert 投影.manifest_digest == 收據.清單摘要 == hashlib.sha256(原始資料).hexdigest()
    assert 投影.bundle_hash == 收據.套件雜湊 and 投影.total_bytes == 收據.總位元組數
    assert [(項.path, 項.size_bytes, 項.sha256) for 項 in 投影.copied_files] == [
        (項["path"], 項["size_bytes"], 項["sha256"])
        for 項 in json.loads(原始資料)["copied_files"]
    ]
    with pytest.raises(FrozenInstanceError):
        setattr(投影, "bundle_hash", "0" * 64)

    原驗證器 = 發布器模組.驗證已發布技能套件清單
    驗證輸入: list[bytes] = []

    def 記錄驗證(資料: bytes):
        """記錄 publisher 提供的 exact bytes 後委派 public validator。

        參數：``資料`` 是新清單 bytes。回傳：public immutable projection。
        例外：原驗證器例外原樣傳出。副作用：附加一次記憶體呼叫紀錄。
        """
        驗證輸入.append(資料)
        return 原驗證器(資料)

    monkeypatch.setattr(發布器模組, "驗證已發布技能套件清單", 記錄驗證)
    第二筆 = _發布(發布器, 來源, 套件識別碼="bundle-2")
    assert len(驗證輸入) == 1
    第二投影 = 原驗證器(驗證輸入[0])
    assert (第二投影.manifest_digest, 第二投影.bundle_hash, 第二投影.total_bytes) == (
        第二筆.清單摘要, 第二筆.套件雜湊, 第二筆.總位元組數,
    )


@pytest.mark.parametrize(
    "損毀",
    ["noncanonical", "duplicate-key", "nonfinite", "metadata", "type",
     "source-relation", "file-relation", "hash-relation"],
)
def test_C3_公開清單嚴格拒絕非正規重複鍵非有限值型別與關係(tmp_path: Path, 損毀: str) -> None:
    """確認 hostile manifest bytes 在 public boundary 一律以 ValueError 關閉失敗。

    參數：``tmp_path`` 建立一份合法基準；``損毀`` 選擇 bytes、metadata、型別或關係攻擊。
    回傳：無。例外：測試只接受 ``ValueError`` 家族。副作用：發布並讀取隔離基準清單。
    """
    收據 = _發布(技能套件發布器(tmp_path / "bundles"), _建立來源(tmp_path))
    原始資料 = (收據.路徑 / "manifest.json").read_bytes()
    清單 = json.loads(原始資料)
    if 損毀 == "noncanonical":
        惡意資料 = 原始資料 + b"\n"
    elif 損毀 == "duplicate-key":
        惡意資料 = b'{"bundle_id":"duplicate",' + 原始資料[1:]
    elif 損毀 == "nonfinite":
        惡意資料 = 原始資料.replace(b'"created_at":1.0', b'"created_at":NaN')
    else:
        if 損毀 == "metadata": 清單["created_at"] = -1
        elif 損毀 == "type": 清單["total_bytes"] = True
        elif 損毀 == "source-relation": 清單["source_skills"][0]["source_path"] = "relative"
        elif 損毀 == "file-relation": 清單["copied_files"][0]["path"] = "other/SKILL.md"
        else: 清單["bundle_hash"] = "0" * 64
        惡意資料 = 發布器模組.正規JSON(清單)
    with pytest.raises(ValueError):
        驗證已發布技能套件清單(惡意資料)


def test_C3_公開清單驗證器控制流程例外維持identity(tmp_path: Path, monkeypatch) -> None:
    """確認可注入 canonical helper 的控制流程例外不被改寫或包裝。

    參數：``tmp_path`` 提供合法清單；``monkeypatch`` 注入控制流程例外。
    回傳：無。例外：只接受同一 ``KeyboardInterrupt`` 實例。副作用：暫時替換 helper。
    """
    收據 = _發布(技能套件發布器(tmp_path / "bundles"), _建立來源(tmp_path))
    清單 = json.loads((收據.路徑 / "manifest.json").read_bytes())
    標記 = "C3_CONTROL_SOURCE_MARKER"
    清單["source_skills"][0]["source_path"] = f"/{標記}"
    原始資料 = 發布器模組.正規JSON(清單)
    中斷 = KeyboardInterrupt("C3_CONTROL")

    def 注入控制(_值: object) -> bytes:
        """在 canonical parity 階段拋出指定控制例外。

        參數：``_值`` 未使用。回傳：不適用。例外：固定拋出測試中斷。
        副作用：不存取外部資源。
        """
        raise 中斷

    monkeypatch.setattr(發布器模組, "正規JSON", 注入控制)
    with pytest.raises(KeyboardInterrupt) as 錯誤:
        驗證已發布技能套件清單(原始資料)
    assert 錯誤.value is 中斷 and 錯誤.value.args == ("C3_CONTROL",)
    _斷言發布器框架已清理(錯誤.value, 標記)


@pytest.mark.parametrize("攻擊", ["duplicate", "noncanonical"])
def test_C4_公開清單普通失敗固定無鏈且traceback清除敵意資料(
    tmp_path: Path, 攻擊: str,
) -> None:
    """以實際框架 locals 鎖定重複鍵與非正規失敗不保留清單資料。

    參數：``tmp_path`` 建立合法清單；``攻擊`` 選擇重複鍵或非正規 bytes。
    回傳：無。例外：公開邊界只接受無敏感鏈的 exact ``ValueError``。
    副作用：發布並讀取隔離清單，不修改生產成果。
    """
    收據 = _發布(技能套件發布器(tmp_path / "bundles"), _建立來源(tmp_path))
    清單 = json.loads((收據.路徑 / "manifest.json").read_bytes())
    標記 = "C4_ABSOLUTE_SOURCE_MARKER"
    清單["source_skills"][0]["source_path"] = f"/{標記}"
    原始資料 = 發布器模組.正規JSON(清單)
    惡意資料 = (
        b'{"bundle_id":"duplicate",' + 原始資料[1:]
        if 攻擊 == "duplicate" else 原始資料 + b"\n"
    )

    with pytest.raises(ValueError) as 錯誤:
        驗證已發布技能套件清單(惡意資料)
    assert type(錯誤.value) is ValueError and 錯誤.value.args == ()
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    _斷言發布器框架已清理(錯誤.value, 標記)


def test_C4_框架清理故障不覆蓋普通或控制流程原結果(tmp_path: Path, monkeypatch) -> None:
    """確認 cleanup 自身故障不取代固定 ValueError 或原控制流程實例。

    參數：``tmp_path`` 提供合法清單；``monkeypatch`` 注入清理與 canonical 故障。
    回傳：無。例外：只接受既定普通錯誤及同一控制流程例外。
    副作用：暫時替換兩個模組 helper。
    """
    收據 = _發布(技能套件發布器(tmp_path / "bundles"), _建立來源(tmp_path))
    原始資料 = (收據.路徑 / "manifest.json").read_bytes()
    monkeypatch.setattr(
        發布器模組, "_清除例外框架",
        lambda _錯誤: (_ for _ in ()).throw(RuntimeError("CLEANUP_FAILURE")),
    )
    with pytest.raises(ValueError) as 普通錯誤:
        驗證已發布技能套件清單(原始資料 + b"\n")
    assert 普通錯誤.value.__cause__ is None and 普通錯誤.value.__context__ is None

    中斷 = KeyboardInterrupt("ORIGINAL_CONTROL")
    monkeypatch.setattr(
        發布器模組, "正規JSON", lambda _值: (_ for _ in ()).throw(中斷),
    )
    with pytest.raises(KeyboardInterrupt) as 控制錯誤:
        驗證已發布技能套件清單(原始資料)
    assert 控制錯誤.value is 中斷 and 控制錯誤.value.args == ("ORIGINAL_CONTROL",)


def test_改名前暫存根保持可寫且最終目錄尚未出現(tmp_path: Path) -> None:
    """釘選封存只鎖內容，暫存根維持 0700 使原子改名可行且成果尚未可見。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：只接受固定發布錯誤。副作用：在改名失敗點觀察暫存樹與最終路徑。
    """
    根目錄 = tmp_path / "bundles"
    最終目錄 = 根目錄 / "bundle-1"
    觀察: dict[str, object] = {}

    def 失敗點(名稱: str) -> None:
        """在改名前記錄暫存樹狀態後中止發布。

        參數：``名稱`` 是線上失敗點。回傳：無。例外：命中改名時拋出 ``OSError``。
        副作用：只讀取暫存樹模式，不修改檔案系統。
        """
        if 名稱 != "rename":
            return
        暫存列 = list(根目錄.glob(".stage-*"))
        暫存根 = 暫存列[0]
        觀察["最終已存在"] = 最終目錄.exists()
        觀察["暫存根模式"] = stat.S_IMODE(暫存根.lstat().st_mode)
        觀察["技能目錄模式"] = stat.S_IMODE((暫存根 / "demo").lstat().st_mode)
        觀察["清單模式"] = stat.S_IMODE((暫存根 / "manifest.json").lstat().st_mode)
        raise OSError("rename")

    with pytest.raises(套件發布錯誤):
        _發布(技能套件發布器(根目錄, 失敗點=失敗點), _建立來源(tmp_path))
    assert 觀察["最終已存在"] is False
    assert 觀察["暫存根模式"] == 0o700
    assert 觀察["技能目錄模式"] == 0o555
    assert 觀察["清單模式"] == 0o444
    assert list(根目錄.glob(".stage-*")) == []
    assert not 最終目錄.exists()


def test_父目錄同步失敗仍保留完整且唯讀的最終套件(tmp_path: Path) -> None:
    """確認父同步未知時成果已封存為 0555 並保有可驗證內容。

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

    根目錄 = tmp_path / "bundles"
    with pytest.raises(套件耐久性未知) as 捕捉:
        _發布(技能套件發布器(根目錄, 失敗點=失敗點), _建立來源(tmp_path))
    成果 = 捕捉.value.收據.路徑
    assert stat.S_IMODE(成果.lstat().st_mode) == 0o555
    assert (成果 / "demo" / "nested" / "x.txt").read_text() == "content"
    assert json.loads((成果 / "manifest.json").read_bytes())["bundle_hash"] == 捕捉.value.收據.套件雜湊
    assert list(根目錄.glob(".stage-*")) == []


def test_安全清除移除已封存暫存樹且不跟隨符號連結(tmp_path: Path) -> None:
    """確認清理能刪除唯讀暫存樹，且只解除連結本身而不動連結目標。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：清理契約違反由 pytest 回報。副作用：建立含敵對連結的暫存樹後清理。
    """
    外部目錄 = tmp_path / "outside"
    外部目錄.mkdir()
    外部檔案 = 外部目錄 / "keep.txt"
    外部檔案.write_text("keep")
    os.chmod(外部檔案, 0o640)
    os.chmod(外部目錄, 0o750)
    暫存根 = tmp_path / ".stage-x"
    (暫存根 / "demo" / "nested").mkdir(parents=True)
    (暫存根 / "demo" / "SKILL.md").write_text("# demo")
    (暫存根 / "demo" / "nested" / "x.txt").write_text("content")
    os.symlink(外部目錄, 暫存根 / "demo" / "dir-link")
    os.symlink(外部檔案, 暫存根 / "demo" / "file-link")
    os.chmod(暫存根 / "demo" / "SKILL.md", 0o444)
    os.chmod(暫存根 / "demo" / "nested" / "x.txt", 0o444)
    os.chmod(暫存根 / "demo" / "nested", 0o555)
    os.chmod(暫存根 / "demo", 0o555)

    發布器模組._安全清除(暫存根)

    assert not 暫存根.exists()
    assert 外部目錄.is_dir()
    assert 外部檔案.read_text() == "keep"
    assert stat.S_IMODE(外部目錄.lstat().st_mode) == 0o750
    assert stat.S_IMODE(外部檔案.lstat().st_mode) == 0o640


def test_安全清除不刪除暫存根外項目且拒絕連結根(tmp_path: Path) -> None:
    """確認清理只作用於自有暫存根，連結指向的樹一律不被刪除。

    參數：``tmp_path`` 是 pytest 隔離目錄。回傳：無。
    例外：清理契約違反由 pytest 回報。副作用：以符號連結假冒暫存根後呼叫清理。
    """
    受害目錄 = tmp_path / "victim"
    (受害目錄 / "inner").mkdir(parents=True)
    (受害目錄 / "inner" / "data.txt").write_text("data")
    os.chmod(受害目錄, 0o750)
    鄰居 = tmp_path / "bundles" / "bundle-1"
    鄰居.mkdir(parents=True)
    (鄰居 / "manifest.json").write_text("{}")
    連結根 = tmp_path / "bundles" / ".stage-link"
    os.symlink(受害目錄, 連結根)

    發布器模組._安全清除(連結根)

    assert 連結根.is_symlink()
    assert (受害目錄 / "inner" / "data.txt").read_text() == "data"
    assert stat.S_IMODE(受害目錄.lstat().st_mode) == 0o750
    assert (鄰居 / "manifest.json").read_text() == "{}"
