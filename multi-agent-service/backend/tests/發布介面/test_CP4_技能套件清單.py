"""驗證技能套件清單、來源掃描與重驗的安全契約。"""

import hashlib
import json
import os

import pytest

import 繁中代理.發布介面.技能套件.清單 as 清單模組
from 繁中代理.發布介面.技能套件.安全複製 import (
    技能套件安全錯誤,
    掃描技能,
    重驗檔案,
    限制,
)
from 繁中代理.發布介面.技能套件 import 載入器 as 載入器模組
from 繁中代理.發布介面.技能套件.清單 import (
    建立清單, 是合法技能套件定位參照, 是合法技能套件清單參照, 正規JSON, 計算套件雜湊,
)
from 繁中代理.發布介面.執行期.模型契約 import 模型設定快照
from 繁中代理.發布介面.執行期.執行器 import 發布執行快照, 發布執行錯誤


def test_正規JSON與套件雜湊依UTF8路徑排序():
    """確認正規 JSON 格式與內容雜湊不受輸入順序影響。

    參數：無。回傳：無；契約以斷言表示。例外：契約不符時由 pytest 回報。
    副作用：只配置記憶體資料，不讀寫檔案。
    """
    項目列 = [
        {"path": "資料/z.txt", "size_bytes": 1, "sha256": "b" * 64},
        {"path": "a.txt", "size_bytes": 2, "sha256": "a" * 64},
    ]
    預期 = hashlib.sha256(
        正規JSON([["a.txt", 2, "a" * 64], ["資料/z.txt", 1, "b" * 64]])
    ).hexdigest()
    assert 計算套件雜湊(項目列) == 預期
    assert 正規JSON({"b": 1, "a": "繁中"}) == b'{"a":"\xe7\xb9\x81\xe4\xb8\xad","b":1}'


@pytest.mark.parametrize("參照,合法", [
    ("bundle-1/manifest.json", True),
    ("../bundle-1/manifest.json", False),
    ("bundle-1/../manifest.json", False),
    ("bundle-1/./manifest.json", False),
    ("/bundle-1/manifest.json", False),
    ("bundle-1\\manifest.json", False),
    ("bundle-1//manifest.json", False),
    ("bundle-1/manifest.json/extra", False),
    ("bundle-1/MANIFEST.json", False),
    (1, False),
])
def test_manifest_reference共享authority三處同判定(參照, 合法):
    """共享 validator、loader seam 與 runtime DTO 對 canonical/traversal 完全一致。"""
    assert 是合法技能套件清單參照(參照) is 合法
    assert 載入器模組._是清單參照(參照) is 合法
    參數 = dict(
        endpoint_id="ep-1", version_id="ver-1", service_account_id="sa-1",
        system_prompt="prompt", permission_snapshot_digest="a" * 64,
        skill_bundle_hash="b" * 64, tool_handler_release="release-1", tool_snapshot=(),
        model_config=模型設定快照("fake", "model", 0, 10, 5, False, 1),
        response_schema=None, manifest_reference=參照,
    )
    if 合法:
        assert 發布執行快照(**參數).manifest_reference == 參照
    else:
        with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
            發布執行快照(**參數)


def test_定位參照只接受同bundle的本機或generation_pinned_GCS() -> None:
    assert 是合法技能套件定位參照("bundle-1/manifest.json", "bundle-1") is True
    assert 是合法技能套件定位參照(
        "bundles/v1/bundle-1/manifest.json#generation=7", "bundle-1",
    ) is True
    assert 是合法技能套件定位參照(
        "bundles/v1/other/manifest.json#generation=7", "bundle-1",
    ) is False
    assert 是合法技能套件定位參照(
        "bundles/v1/bundle-1/manifest.json#generation=0", "bundle-1",
    ) is False
    assert 是合法技能套件定位參照(
        "bundles/v1/bundle-1/manifest.json", "bundle-1",
    ) is False


def test_manifest_reference共享authority保留控制流程identity(monkeypatch):
    """共享 lexical authority 不把控制流程例外誤關閉成一般 False。"""
    錯誤 = KeyboardInterrupt("停止")

    def 中斷(*_):
        raise 錯誤

    monkeypatch.setattr(清單模組.unicodedata, "normalize", 中斷)
    try:
        with pytest.raises(KeyboardInterrupt) as 捕捉:
            是合法技能套件清單參照("bundle-1/manifest.json")
    finally:
        monkeypatch.undo()
    assert 捕捉.value is 錯誤


def test_掃描固定排除但保留未列入規則的隱藏檔(tmp_path):
    """確認固定垃圾檔被排除，而安全隱藏檔仍會納入。

    參數：``tmp_path`` 是 pytest 隔離暫存目錄。回傳：無。例外：契約不符時由 pytest 回報。
    副作用：在暫存目錄建立技能樹並由掃描器讀取。
    """
    根目錄 = tmp_path / "skill"
    根目錄.mkdir()
    (根目錄 / "SKILL.md").write_text("# 技能", encoding="utf-8")
    (根目錄 / ".env.example").write_text("SAFE=1", encoding="utf-8")
    (根目錄 / ".DS_Store").write_bytes(b"x")
    (根目錄 / "x.pyc").write_bytes(b"x")
    (根目錄 / "node_modules").mkdir()
    (根目錄 / "node_modules" / "x").write_bytes(b"x")
    掃描 = 掃描技能("demo", 根目錄)
    assert [檔案.相對路徑 for 檔案 in 掃描.檔案] == [".env.example", "SKILL.md"]
    assert {項目.相對路徑 for 項目 in 掃描.排除} == {".DS_Store", "node_modules", "x.pyc"}


def test_缺少技能說明非目錄與特殊檔案皆關閉失敗(tmp_path):
    """確認三種不安全來源都以固定安全錯誤拒絕。

    參數：``tmp_path`` 是 pytest 隔離暫存目錄。回傳：無。例外：只允許預期安全錯誤。
    副作用：在暫存目錄建立目錄、一般檔案與命名管線。
    """
    根目錄 = tmp_path / "skill"
    根目錄.mkdir()
    with pytest.raises(技能套件安全錯誤):
        掃描技能("demo", 根目錄)
    一般檔案 = tmp_path / "file"
    一般檔案.write_text("x")
    with pytest.raises(技能套件安全錯誤):
        掃描技能("demo", 一般檔案)
    (根目錄 / "SKILL.md").write_text("x")
    os.mkfifo(根目錄 / "pipe")
    with pytest.raises(技能套件安全錯誤):
        掃描技能("demo", 根目錄)


def test_符號連結不複製且逃出來源時拒絕(tmp_path):
    """確認內部連結只記錄排除，外部連結則拒絕整個來源。

    參數：``tmp_path`` 是 pytest 隔離暫存目錄。回傳：無。例外：只允許預期安全錯誤。
    副作用：在暫存目錄建立檔案及兩個符號連結。
    """
    根目錄 = tmp_path / "skill"
    根目錄.mkdir()
    (根目錄 / "SKILL.md").write_text("x")
    (根目錄 / "target").write_text("safe")
    os.symlink("target", 根目錄 / "inside")
    掃描 = 掃描技能("demo", 根目錄)
    assert "inside" in {項目.相對路徑 for 項目 in 掃描.排除}
    外部檔案 = tmp_path / "outside"
    外部檔案.write_text("secret")
    os.symlink(外部檔案, 根目錄 / "escape")
    with pytest.raises(技能套件安全錯誤):
        掃描技能("demo", 根目錄)


@pytest.mark.parametrize(
    "上限參數",
    [
        {"最大檔案數": 1},
        {"最大總位元組數": 2},
        {"最大檔案位元組數": 1},
        {"最大深度": 0},
        {"最大路徑位元組數": 4},
    ],
)
def test_所有資源上限都關閉失敗(tmp_path, 上限參數):
    """確認每一種資源上限都能獨立拒絕超限來源。

    參數：``tmp_path`` 是 pytest 暫存目錄；``上限參數`` 是單一限制覆寫。
    回傳：無。例外：只允許預期安全錯誤。副作用：建立並掃描暫存技能樹。
    """
    根目錄 = tmp_path / "skill"
    根目錄.mkdir()
    (根目錄 / "SKILL.md").write_text("123")
    (根目錄 / "other").write_text("456")
    if "最大深度" in 上限參數:
        (根目錄 / "nested").mkdir()
        (根目錄 / "nested" / "x").write_text("x")
    with pytest.raises(技能套件安全錯誤):
        掃描技能("demo", 根目錄, 上限=限制(**上限參數))


def test_重驗拒絕掃描後遭替換的檔案(tmp_path):
    """確認重驗會以釘選身分偵測掃描後的同路徑替換。

    參數：``tmp_path`` 是 pytest 隔離暫存目錄。回傳：無。例外：只允許預期安全錯誤。
    副作用：建立技能、掃描後替換檔案，再由重驗器讀取。
    """
    根目錄 = tmp_path / "skill"
    根目錄.mkdir()
    (根目錄 / "SKILL.md").write_text("abc")
    掃描 = 掃描技能("demo", 根目錄)
    檔案 = 掃描.檔案[0]
    (根目錄 / "SKILL.md").unlink()
    (根目錄 / "SKILL.md").write_text("xyz")
    with pytest.raises(技能套件安全錯誤):
        重驗檔案(掃描, 檔案)


def test_清單完整欄位與摘要語意分離(tmp_path):
    """確認第一版線上欄位完整，且清單摘要不同於內容集合摘要。

    參數：``tmp_path`` 是 pytest 隔離暫存目錄。回傳：無。例外：契約不符時由 pytest 回報。
    副作用：建立並掃描暫存技能目錄，不寫入發布目標。
    """
    根目錄 = tmp_path / "skill"
    根目錄.mkdir()
    (根目錄 / "SKILL.md").write_text("abc")
    掃描 = 掃描技能("demo", 根目錄)
    清單, 原始資料, 摘要 = 建立清單(
        套件識別碼="bundle-1",
        端點識別碼="endpoint-1",
        端點版本識別碼="version-1",
        版本號碼=1,
        建立時間=1.0,
        建立者識別碼="owner-1",
        掃描列=(掃描,),
    )
    assert set(清單) == {
        "manifest_version", "bundle_id", "endpoint_id", "endpoint_version_id",
        "version_number", "created_at", "created_by_user_id", "source_skills",
        "copied_files", "copied_file_hashes", "excluded_files", "warnings",
        "total_bytes", "bundle_hash",
    }
    assert 摘要 == hashlib.sha256(原始資料).hexdigest()
    assert 摘要 != 清單["bundle_hash"]
    assert json.loads(原始資料)["manifest_version"] == 1
