"""A6-03：凍結 v1／v2 per-version 技能套件契約。

本檔把 Acceptance #6「每個 Endpoint Version 都有自己的不可變 Bundle」寫成不可偷改的
契約。v1 與 v2 路徑均已實作，相關案例皆應為 GREEN；v2 透過正式版本配置服務
建立新 Bundle、Version 與 Receipt，並原子切換 current pointer。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.技能套件.協調器 import 技能套件協調器
from 繁中代理.發布介面.技能套件.發布器 import (
    技能套件發布器, 套件發布錯誤,
)
from 繁中代理.發布介面.技能套件.載入器 import (
    已發布技能套件載入器, 技能套件定位,
)
from 繁中代理.發布介面.規劃.擁有者能力 import 擁有者能力轉接器
from 繁中代理.發布介面.規劃.發布管理 import 發布管理協調器
from 繁中代理.發布介面.規劃.版本服務 import SQLite版本配置服務
from 繁中代理.發布介面.規劃.端點發布 import SQLite端點發布服務
from 繁中代理.發布介面.規劃.權限協調 import 權限協調器
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.路由.規劃發布 import (
    發布確認, 端點發布結果, 版本建立結果, 管理操作錯誤,
)


def _綱要() -> dict:
    """建立 Planner 已驗證後會保存的 authority outline。"""
    return {
        "endpoint_name": "Alpha", "suggested_slug": "alpha-api",
        "behavior_summary": "精確回答", "selected_skills": ["alpha"],
        "recommended_tools": ["alpha-tool"], "tool_capabilities": {"alpha-tool": "查詢"},
        "system_prompt": "只根據技能回答", "input_schema": None,
        "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                            "required": ["answer"], "additionalProperties": False},
        "human_docs": "呼叫後回傳答案。",
        "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
        "warnings": [],
    }


def _確認(草稿識別碼: str, *, 短名: str = "alpha-api") -> 發布確認:
    """建立只確認顯示值的 route DTO。"""
    綱要 = _綱要()
    return 發布確認(草稿識別碼, 短名, {
        "system_prompt": 綱要["system_prompt"], "response_schema": 綱要["response_schema"],
        "rate_limit": 綱要["rate_limit"],
    })


_技能正文 = "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n原始內容\n"


def _建立環境(tmp_path: Path) -> dict:
    """以真實 owner、tool release、遷移 DB、AES 與 bundle 目錄組裝 production 服務。"""
    技能根 = tmp_path / "skills"
    技能 = 技能根 / "alpha"
    技能.mkdir(parents=True)
    (技能 / "SKILL.md").write_text(_技能正文, encoding="utf-8")
    使用者庫物件 = 使用者庫(tmp_path / "web.sqlite3")
    使用者 = 使用者庫物件.建立使用者(
        "owner", roles=["admin"], enabled_tools=["alpha-tool"], enabled_skills=["alpha"],
        skill_roots=[str(技能根)],
    )
    擁有者 = str(使用者["id"])
    工具庫 = 工具發布庫()
    工具庫.登錄發布(工具發布描述("release-1", (工具發布註冊("revision-1", 工具定義(
        "alpha-tool", "Alpha tool", {"type": "object", "properties": {}}, lambda _: "ok",
    )),)))
    解析器 = 擁有者能力轉接器(使用者庫物件, 工具庫, "release-1")
    草稿服務 = 規劃服務(存續秒數=1000, 識別碼產生器=lambda: "draft-1")
    草稿服務.建立授權草稿(
        權限協調器(解析器), 擁有者, "建立 Alpha API", _綱要(),
        ("alpha",), ("alpha-tool",), 現在=10.0,
    )
    資料庫 = tmp_path / "published.sqlite3"
    初始化發布介面資料庫(資料庫)
    套件根 = tmp_path / "bundles"
    封套 = AESGCM憑證封套({1: b"K" * 32}, 1, 隨機位元組=lambda 長度: b"N" * 長度)
    次數: dict[str, int] = {}
    鎖 = threading.Lock()

    def 識別碼(前綴: str) -> str:
        """為每種 graph identity 提供原子序號。"""
        with 鎖:
            次數[前綴] = 次數.get(前綴, 0) + 1
            return f"{前綴}-{次數[前綴]}"

    def 未使用識別() -> str:
        """prepared P04 路徑不得呼叫 legacy 識別工廠。"""
        return "unused"

    端點服務 = SQLite端點發布服務(
        資料庫, 未使用識別, 未使用識別, 未使用識別, 未使用識別, lambda: 20.0,
    )
    版本服務 = SQLite版本配置服務(資料庫, 未使用識別, lambda: 20.0)
    協調器 = 技能套件協調器(套件根, 孤兒保留秒數=3600, 時鐘=lambda: 20.0)
    服務 = 發布管理協調器(
        草稿服務=草稿服務, 擁有者解析器=解析器,
        套件發布器物件=技能套件發布器(套件根),
        套件協調器物件=協調器,
        端點發布服務=端點服務, 版本配置服務=版本服務,
        憑證封套=封套, 時鐘=lambda: 20.0,
        識別碼產生器=識別碼, 隨機位元組=lambda 長度: bytes(range(長度)),
    )
    return {
        "服務": 服務, "資料庫": 資料庫, "套件根": 套件根, "技能": 技能,
        "使用者庫": 使用者庫物件, "擁有者": 擁有者, "草稿服務": 草稿服務,
    }


def _發布v1(環境: dict) -> 端點發布結果:
    """走 production v1 路徑完成初次發布。"""
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert type(結果) is 端點發布結果, f"v1 初次發布應成功，實得 {結果!r}"
    return 結果


def _第二版配置() -> dict:
    """建立 v2 應套用的新配置，只改行為文字不改身分。"""
    綱要 = _綱要()
    return {
        "original_requirement_text": "建立 Alpha API 第二版",
        "system_prompt": "只根據技能回答，並附出處",
        "model_config_snapshot": {"model": "published-v2", "temperature": 0},
        "retry_policy": {"max_attempts": 2},
        "input_schema": 綱要["input_schema"],
        "response_schema": 綱要["response_schema"],
    }


def _建立第二版(環境: dict, 結果: 端點發布結果):
    """呼叫正式 v2 協調入口建立下一版並切換 current pointer。"""
    return 環境["服務"].原子建立並切換版本(
        擁有者使用者識別碼=環境["擁有者"], 端點識別碼=結果.端點識別碼,
        配置=_第二版配置(),
    )


def _收據列(資料庫: Path) -> list[tuple]:
    """讀取所有套件收據，依發布時間排序。"""
    連線 = sqlite3.connect(資料庫)
    try:
        return 連線.execute(
            "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,"
            "total_bytes,state FROM published_skill_bundles ORDER BY published_at,bundle_id"
        ).fetchall()
    finally:
        連線.close()


def _樹指紋(根: Path) -> list[tuple[str, int, str, int]]:
    """建立套件樹的完整 bytes 與模式指紋，用來證明舊 Bundle 未被改動。"""
    項目: list[tuple[str, int, str, int]] = []
    for 目前, 目錄們, 檔案們 in os.walk(根):
        目錄們.sort()
        for 名稱 in sorted(檔案們):
            路徑 = Path(目前) / 名稱
            資料 = 路徑.read_bytes()
            項目.append((
                str(路徑.relative_to(根)), len(資料),
                hashlib.sha256(資料).hexdigest(), 路徑.stat().st_mode & 0o777,
            ))
    return 項目


def _技能來源(根: Path, 名稱: str, 內容: bytes) -> Path:
    """建立可直接交給發布器的最小技能來源目錄。"""
    技能 = 根 / f"source-{名稱}"
    技能.mkdir(parents=True, exist_ok=True)
    (技能 / "SKILL.md").write_bytes(內容)
    return 技能


# ---------------------------------------------------------------------------
# v1：初次發布必須把 Bundle 收據精確綁定到該 Version
# ---------------------------------------------------------------------------


def test_v1套件收據與版本識別碼精確綁定(tmp_path):
    """v1 的 Bundle／Manifest／Receipt 必須三者一致且綁到 version-1。"""
    環境 = _建立環境(tmp_path)
    結果 = _發布v1(環境)

    收據列 = _收據列(環境["資料庫"])
    assert len(收據列) == 1, "v1 只能有一筆套件收據"
    套件識別碼, 版本識別碼, 清單參照, 清單摘要, 套件雜湊, 總位元組數, 狀態 = 收據列[0]

    assert 版本識別碼 == 結果.版本識別碼
    assert 清單參照 == f"{套件識別碼}/manifest.json"
    assert 狀態 == "published"

    清單路徑 = 環境["套件根"] / 套件識別碼 / "manifest.json"
    原始資料 = 清單路徑.read_bytes()
    清單 = json.loads(原始資料)
    assert 清單["endpoint_version_id"] == 結果.版本識別碼
    assert 清單["endpoint_id"] == 結果.端點識別碼
    assert 清單["version_number"] == 1
    assert 清單["bundle_id"] == 套件識別碼
    assert 清單["bundle_hash"] == 套件雜湊
    assert 清單["total_bytes"] == 總位元組數
    assert hashlib.sha256(原始資料).hexdigest() == 清單摘要


# ---------------------------------------------------------------------------
# v2：目前 RED，原因必須是 原子建立並切換版本() 尚未實作
# ---------------------------------------------------------------------------


def test_v2產生不同套件識別碼(tmp_path):
    """v2 必須建立自己的 Bundle，不得共用或覆寫 v1 的 Bundle。"""
    環境 = _建立環境(tmp_path)
    v1結果 = _發布v1(環境)
    v1套件識別碼 = _收據列(環境["資料庫"])[0][0]

    v2結果 = _建立第二版(環境, v1結果)

    assert type(v2結果) is not 管理操作錯誤, (
        f"v2 orchestration 尚未實作：原子建立並切換版本() 回 {v2結果!r}"
    )
    assert type(v2結果) is 版本建立結果
    assert v2結果.版本識別碼 != v1結果.版本識別碼
    assert v2結果.目前版本識別碼 == v2結果.版本識別碼

    收據列 = _收據列(環境["資料庫"])
    assert len(收據列) == 2, "v1 與 v2 必須各自持有一筆套件收據"
    套件識別碼們 = {列[0] for 列 in 收據列}
    版本識別碼們 = {列[1] for 列 in 收據列}
    assert len(套件識別碼們) == 2, "v2 必須使用新的 bundle_id"
    assert 版本識別碼們 == {v1結果.版本識別碼, v2結果.版本識別碼}
    assert v1套件識別碼 in 套件識別碼們


def test_v2清單版本號碼為2(tmp_path):
    """v2 Manifest 的 version_number 必須是 2 且綁到 v2 的 version id。"""
    環境 = _建立環境(tmp_path)
    v1結果 = _發布v1(環境)

    v2結果 = _建立第二版(環境, v1結果)

    assert type(v2結果) is not 管理操作錯誤, (
        f"v2 orchestration 尚未實作：原子建立並切換版本() 回 {v2結果!r}"
    )
    assert v2結果.版本編號 == 2
    v2收據 = [列 for 列 in _收據列(環境["資料庫"]) if 列[1] == v2結果.版本識別碼]
    assert len(v2收據) == 1
    清單 = json.loads((環境["套件根"] / v2收據[0][0] / "manifest.json").read_bytes())
    assert 清單["version_number"] == 2
    assert 清單["endpoint_version_id"] == v2結果.版本識別碼


def test_v2發布後v1套件樹與清單摘要完全不變(tmp_path):
    """發布 v2 不得改動 v1 的任何 byte、模式、清單摘要或收據。"""
    環境 = _建立環境(tmp_path)
    v1結果 = _發布v1(環境)
    v1收據 = _收據列(環境["資料庫"])[0]
    v1套件根 = 環境["套件根"] / v1收據[0]
    發布前指紋 = _樹指紋(v1套件根)

    v2結果 = _建立第二版(環境, v1結果)

    assert type(v2結果) is not 管理操作錯誤, (
        f"v2 orchestration 尚未實作：原子建立並切換版本() 回 {v2結果!r}"
    )
    assert _樹指紋(v1套件根) == 發布前指紋, "v1 Bundle Tree 必須逐 byte 不變"
    v2後v1收據 = [列 for 列 in _收據列(環境["資料庫"]) if 列[1] == v1結果.版本識別碼]
    assert v2後v1收據 == [v1收據], "v1 收據不得被改寫"


# ---------------------------------------------------------------------------
# Runtime：Live Skill 改動不得讓已發布版本漂移
# ---------------------------------------------------------------------------


def test_LiveSkill變更不影響v1執行期載入(tmp_path):
    """發布後修改來源 SKILL.md，v1 Loader 仍必須回傳發布當下的內容。"""
    環境 = _建立環境(tmp_path)
    結果 = _發布v1(環境)
    套件識別碼, 版本識別碼, 清單參照, 清單摘要, 套件雜湊, 總位元組數, _狀態 = _收據列(
        環境["資料庫"]
    )[0]

    (環境["技能"] / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n被竄改的內容\n",
        encoding="utf-8",
    )

    定位 = 技能套件定位(
        version_id=版本識別碼, bundle_id=套件識別碼, manifest_reference=清單參照,
        manifest_digest=清單摘要, bundle_hash=套件雜湊, total_bytes=總位元組數,
    )

    class 提供者:
        """只回應 exact version 的最小 authoritative provider。"""
        def 取得技能套件定位(self, endpoint_version_id: str):
            assert endpoint_version_id == 版本識別碼
            return 定位

    快照 = 已發布技能套件載入器(環境["套件根"], 提供者()).載入技能套件快照(
        版本識別碼, 套件雜湊, 清單參照, "endpoint_version_snapshot",
    )
    技能檔案 = [檔案 for 檔案 in 快照.files if 檔案.path.endswith("SKILL.md")]
    assert 技能檔案, "快照必須包含 SKILL.md"
    for 檔案 in 技能檔案:
        assert 檔案.content == _技能正文.encode(), "v1 Runtime 不得讀到 Live Skill 的新內容"
        assert "被竄改".encode() not in 檔案.content


# ---------------------------------------------------------------------------
# Fail closed：草稿後權限或內容漂移必須拒絕發布
# ---------------------------------------------------------------------------


def test_草稿後撤權發布固定失敗關閉(tmp_path):
    """草稿建立後撤掉技能授權，發布必須關閉且不得留下套件收據。"""
    環境 = _建立環境(tmp_path)
    環境["使用者庫"].設定權限欄位("owner", "enabled_skills_json", ["other"])

    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )

    assert type(結果) is 管理操作錯誤, f"撤權後必須 fail closed，實得 {結果!r}"
    assert _收據列(環境["資料庫"]) == [], "失敗發布不得留下套件收據"


def test_草稿後SKILLmd漂移發布固定拒絕(tmp_path):
    """草稿釘選 SKILL.md 雜湊後改動來源，發布必須拒絕。"""
    環境 = _建立環境(tmp_path)
    (環境["技能"] / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n草稿後被改\n",
        encoding="utf-8",
    )

    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )

    assert type(結果) is 管理操作錯誤, f"SKILL.md 漂移後必須拒絕，實得 {結果!r}"
    assert _收據列(環境["資料庫"]) == [], "拒絕的發布不得留下套件收據"


# ---------------------------------------------------------------------------
# Bundle ID 冪等與碰撞矩陣
# ---------------------------------------------------------------------------


def test_同套件識別碼同身分同雜湊冪等(tmp_path):
    """相同 ID、相同身分與相同內容重複發布必須回傳等價收據且不重建。"""
    發布器 = 技能套件發布器(tmp_path / "published")
    技能表 = {"alpha": _技能來源(tmp_path, "alpha", b"alpha prompt")}
    參數 = dict(
        套件識別碼="bundle-1", 端點識別碼="endpoint-1", 端點版本識別碼="version-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner", 技能表=技能表,
    )

    第一次 = 發布器.發布(**參數)
    指紋 = _樹指紋(第一次.路徑)
    第二次 = 發布器.發布(**參數)

    assert 第二次.套件識別碼 == 第一次.套件識別碼
    assert 第二次.清單摘要 == 第一次.清單摘要
    assert 第二次.套件雜湊 == 第一次.套件雜湊
    assert 第二次.總位元組數 == 第一次.總位元組數
    assert _樹指紋(第一次.路徑) == 指紋, "冪等重發不得改動既有 Bundle"
    assert [項目.name for 項目 in (tmp_path / "published").iterdir()] == ["bundle-1"], (
        "冪等重發不得留下 .stage-* 殘留"
    )


@pytest.mark.parametrize("漂移", ["版本號碼", "端點版本識別碼", "內容"])
def test_同套件識別碼不同版本或雜湊固定拒絕(tmp_path, 漂移: str):
    """相同 Bundle ID 但身分或內容不同時必須拒絕，且不得覆寫既有 Bundle。"""
    發布器 = 技能套件發布器(tmp_path / "published")
    參數 = dict(
        套件識別碼="bundle-1", 端點識別碼="endpoint-1", 端點版本識別碼="version-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner",
        技能表={"alpha": _技能來源(tmp_path, "alpha", b"alpha prompt")},
    )
    第一次 = 發布器.發布(**參數)
    指紋 = _樹指紋(第一次.路徑)

    衝突 = dict(參數)
    if 漂移 == "版本號碼":
        衝突["版本號碼"] = 2
    elif 漂移 == "端點版本識別碼":
        衝突["端點版本識別碼"] = "version-2"
    else:
        衝突["技能表"] = {"alpha": _技能來源(tmp_path, "beta", b"beta prompt")}

    with pytest.raises(套件發布錯誤):
        發布器.發布(**衝突)

    assert _樹指紋(第一次.路徑) == 指紋, "碰撞不得改動既有不可變 Bundle"
    assert [項目.name for 項目 in (tmp_path / "published").iterdir()] == ["bundle-1"], (
        "碰撞不得留下 .stage-* 殘留"
    )
