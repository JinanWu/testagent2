"""A08-1 fixed Published skills release 與 immutable bundle authority 測試。"""

from __future__ import annotations

import hashlib
import json

import pytest

from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行快照, 發布執行請求,
    建立發布執行器,
)
from 繁中代理.發布介面.執行期.服務帳戶 import ServiceAccountContext
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照, 模型設定快照

def _快照(*檔案: tuple[str, bytes]) -> 技能套件快照:
    """建立已通過摘要驗證的 request-local immutable bundle。"""
    項目 = tuple(
        技能套件檔案(path=路徑, sha256=hashlib.sha256(內容).hexdigest(), content=內容)
        for 路徑, 內容 in sorted(檔案)
    )
    from 繁中代理.發布介面.執行期.執行器 import 計算技能套件雜湊

    清單 = b'{"bundle":"verified"}'
    return 技能套件快照(
        endpoint_version_id="ver-1",
        skill_bundle_hash=計算技能套件雜湊(項目),
        manifest_digest=hashlib.sha256(清單).hexdigest(),
        清單原始資料=清單,
        files=項目,
    )

def test_production技能工具factory建立request_local_closures且限制路徑():
    """A08-SKILL-01：factory 直接捕捉 verified bundle，不需 context/global authority。"""
    from 繁中代理.發布介面.生產技能工具 import (
        建立技能套件釘選工具登錄器, 建立技能工具發布描述,
    )
    from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫

    描述 = 建立技能工具發布描述()
    工具庫 = 工具發布庫()
    工具庫.登錄發布(描述)
    發布版 = 工具庫.取得發布("testagent2-published-skills-v1")
    assert 發布版 is not None
    描述 = 發布版.描述
    assert 描述.handler_release == "testagent2-published-skills-v1"
    assert [(項.tool.名稱, 項.revision) for 項 in 描述.tools] == [
        ("skills_list", "skills_list@bundle-v1"),
        ("skill_view", "skill_view@bundle-v1"),
    ]
    快照 = _快照(
        ("alpha/SKILL.md", b"alpha body"),
        ("alpha/references/guide.md", b"guide"),
        ("alpha/templates/example.txt", b"template"),
        ("alpha/scripts/run.py", b"forbidden"),
        ("alpha/assets/logo.txt", b"forbidden"),
    )
    登錄器 = 建立技能套件釘選工具登錄器(
        發布版, 發布版.工具快照,
        tuple((檔案.path, 檔案.content) for 檔案 in 快照.files),
    )
    import json
    def 呼叫(名稱, 參數):
        結果 = json.loads(登錄器.呼叫工具(名稱, 參數))
        assert 結果["success"] is True
        return 結果["result"]
    assert 呼叫("skills_list", {}) == {"skills": [{"name": "alpha"}]}
    assert 呼叫("skills_list", {"category": "anything"}) == {"skills": []}
    assert 呼叫("skill_view", {"name": "alpha"})["content"] == "alpha body"
    assert 呼叫("skill_view", {"name": "alpha", "file_path": "references/guide.md"})["content"] == "guide"
    for 路徑 in ("scripts/run.py", "assets/logo.txt", "../SKILL.md", "/etc/passwd"):
        assert json.loads(登錄器.呼叫工具(
            "skill_view", {"name": "alpha", "file_path": 路徑},
        )) == {"success": False, "error": "發布工具不可用"}


class _工具迴圈模型:
    """依 genuine transcript 依序要求 list/view，最後回傳實際 tool content。"""

    def __init__(self, *, 非法路徑: bool = False):
        self.非法路徑, self.calls = 非法路徑, []

    def 產生發布回應(self, **參數):
        import copy
        import json
        self.calls.append(copy.deepcopy(參數))
        訊息 = 參數["messages"]
        工具訊息 = [項 for 項 in 訊息 if 項["role"] == "tool"]
        if not 工具訊息:
            名稱, arguments = "skills_list", "{}"
        elif len(工具訊息) == 1:
            名稱 = "skill_view"
            arguments = json.dumps({
                "name": "alpha",
                "file_path": "../SKILL.md" if self.非法路徑 else "SKILL.md",
            })
        else:
            內容 = json.loads(工具訊息[-1]["content"])["result"]["content"]
            return 模型回應快照(內容, "stop", {}, [])
        呼叫 = {"id": f"call-{len(工具訊息)}", "type": "function",
                "function": {"name": 名稱, "arguments": arguments}}
        return 模型回應快照("", "tool_calls", {}, [呼叫])


class _固定Authority:
    def __init__(self, 快照, 上下文, 套件):
        self.快照, self.上下文, self.套件 = 快照, 上下文, 套件

    def 取得發布執行快照(self, _版本):
        return self.快照

    def 載入服務帳戶上下文(self, _帳戶, _版本, _來源):
        return self.上下文

    def 載入技能套件快照(self, *_參數):
        return self.套件


def _建立技能執行器(套件, 模型):
    from 繁中代理.發布介面.生產技能工具 import 建立技能工具發布描述
    from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫
    工具庫 = 工具發布庫()
    發布版 = 工具庫.登錄發布(建立技能工具發布描述())
    工具快照 = 發布版.工具快照
    版本 = 發布執行快照(
        endpoint_id="ep-1", version_id="ver-1", service_account_id="sa-1",
        system_prompt="pinned", permission_snapshot_digest="a" * 64,
        skill_bundle_hash=套件.skill_bundle_hash,
        tool_handler_release="testagent2-published-skills-v1", tool_snapshot=工具快照,
        model_config=模型設定快照("fake", "model", 0, 100, 2, False, 1),
        response_schema=None, manifest_reference="bundle-1/manifest.json",
    )
    上下文 = ServiceAccountContext(
        service_account_id="sa-1", endpoint_version_id="ver-1",
        permission_snapshot_digest="a" * 64,
        allowed_tools=("skills_list", "skill_view"), skill_bundle_hash=套件.skill_bundle_hash,
        tool_handler_release="testagent2-published-skills-v1",
    )
    authority = _固定Authority(版本, 上下文, 套件)
    執行器 = 建立發布執行器(
        endpoint_version_id="ver-1", service_account_id="sa-1",
        發布快照提供者=authority, 服務帳戶載入器=authority, 技能套件載入器=authority,
        工具修訂提供者=發布版, 模型供應商註冊表={"fake": 模型},
    )
    return 執行器, authority


def test_真executor_tool_loop依bundle建立closure且同一executor重試不漂移():
    """A08-SKILL-02：A/B request-local registry 不串線，retry 不讀 mutable loader。"""
    套件甲 = _快照(("alpha/SKILL.md", b"bundle A"))
    套件乙 = _快照(("alpha/SKILL.md", b"bundle B"))
    模型甲, 模型乙 = _工具迴圈模型(), _工具迴圈模型()
    執行器甲, authority甲 = _建立技能執行器(套件甲, 模型甲)
    執行器乙, _ = _建立技能執行器(套件乙, 模型乙)

    assert 執行器甲.執行單次(發布執行請求({"attempt": 1})).text == "bundle A"
    authority甲.套件 = 套件乙
    assert 執行器甲.執行單次(發布執行請求({"attempt": 2})).text == "bundle A"
    assert 執行器乙.執行單次(發布執行請求({"attempt": 1})).text == "bundle B"
    for 模型 in (模型甲, 模型乙):
        assert [項["name"] for 項 in 模型.calls[-1]["messages"] if 項["role"] == "tool"] == [
            "skills_list", "skill_view",
        ]


def test_真executor非法技能路徑固定映射為工具失敗():
    """A08-SKILL-03：非法 file_path fail closed，model/tool mapping 不偽裝成功。"""
    from 繁中代理.發布介面.執行期.執行器 import 發布工具執行錯誤
    模型 = _工具迴圈模型(非法路徑=True)
    執行器, _ = _建立技能執行器(_快照(("alpha/SKILL.md", b"alpha")), 模型)
    with pytest.raises(發布工具執行錯誤, match="^發布工具執行失敗$"):
        執行器.執行單次(發布執行請求({"bad": True}))
    assert len(模型.calls) == 2


def test_revision可用at但工具名稱與release仍維持舊字元集():
    """A08-REV-01：只 revision validator 擴充 @，不可放寬其他 identifier。"""
    from 繁中代理.工具 import 工具定義
    from 繁中代理.發布介面.執行期.工具版本庫 import 工具版本庫, 工具快照錯誤
    from 繁中代理.發布介面.執行期.工具發布庫 import (
        工具發布描述, 工具發布註冊, 工具發布錯誤,
    )
    工具 = 工具定義("skills_list", "list", {"type": "object"}, lambda _: {})
    assert 工具版本庫().登錄修訂("skills_list@bundle-v1", 工具).revision.endswith("@bundle-v1")
    with pytest.raises(工具快照錯誤):
        工具版本庫().登錄修訂("rev-1", 工具定義("skills@list", "bad", {}, lambda _: {}))
    with pytest.raises(工具發布錯誤):
        工具發布描述("release@bad", (工具發布註冊("rev-1", 工具),))
