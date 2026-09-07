"""CP4 Owner capability adapter：權威投影與發布重驗。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.嚴格JSON import 建立正規JSON
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.規劃.擁有者能力 import 擁有者能力轉接器, 擁有者能力錯誤
from 繁中代理.發布介面.規劃.權限協調 import 能力摘要


class _使用者庫:
    def __init__(self, 上下文):
        self.上下文, self.呼叫 = 上下文, []

    def 建立使用者上下文(self, user_id=None):
        self.呼叫.append(user_id)
        return self.上下文


def _技能(根: Path, 名稱: str, 本文: str = "# Alpha\n") -> Path:
    目錄 = 根 / 名稱
    目錄.mkdir(parents=True)
    (目錄 / "SKILL.md").write_text(
        f"---\nname: {名稱}\ndescription: {名稱} summary\n---\n{本文}", encoding="utf-8",
    )
    return 目錄


def _工具庫(*, release="release-1"):
    庫 = 工具發布庫()
    for 發布, 修訂, 名稱 in ((release, "rev-a", "alpha-tool"), ("release-other", "rev-z", "other-tool")):
        工具 = 工具定義(名稱, 名稱 + " description", {"type": "object", "properties": {}}, lambda _: 名稱)
        庫.登錄發布(工具發布描述(發布, (工具發布註冊(修訂, 工具),)))
    return 庫


def _上下文(根, **覆寫):
    資料 = dict(
        user_id="owner-1", roles=["member"], enabled_tools={"alpha-tool"},
        enabled_skills={"alpha"}, skill_roots=[根], is_admin=True, disabled=False,
    )
    資料.update(覆寫)
    return 使用者上下文(**資料)


def _轉接器(根, **覆寫):
    使用者庫 = _使用者庫(_上下文(根, **覆寫))
    return 擁有者能力轉接器(使用者庫, _工具庫(), "release-1"), 使用者庫


def test_authoritative_disabled_roles_enabled_sets_skill_roots與exact_release(tmp_path):
    _技能(tmp_path, "alpha")
    _技能(tmp_path, "beta")
    轉接器, 使用者庫 = _轉接器(tmp_path, roles=["admin"], is_admin=False)
    快照 = 轉接器.查詢規劃權限("owner-1")
    assert 使用者庫.呼叫 == ["owner-1"]
    assert [項.名稱 for 項 in 快照.技能] == ["alpha"]
    assert [(項.名稱, 項.釘選修訂) for 項 in 快照.工具] == [("alpha-tool", "rev-a")]
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)
    assert 轉接器.解析發布能力("owner-1", 摘要).具有管理權限 is True

    使用者庫.上下文 = _上下文(tmp_path, disabled=True)
    with pytest.raises(擁有者能力錯誤, match="^擁有者發布能力不可用$"):
        轉接器.查詢規劃權限("owner-1")

    空轉接器, _ = _轉接器(tmp_path, enabled_tools=set(), enabled_skills=set())
    空快照 = 空轉接器.查詢規劃權限("owner-1")
    assert 空快照.技能 == 空快照.工具 == ()


def test_owner能力接受production核准工具修訂中的at分隔符(tmp_path):
    _技能(tmp_path, "alpha")
    使用者庫 = _使用者庫(_上下文(tmp_path))
    庫 = 工具發布庫()
    工具 = 工具定義(
        "alpha-tool", "alpha-tool description",
        {"type": "object", "properties": {}}, lambda _: "ok",
    )
    庫.登錄發布(工具發布描述(
        "release-1", (工具發布註冊("alpha-tool@bundle-v1", 工具),),
    ))
    轉接器 = 擁有者能力轉接器(使用者庫, 庫, "release-1")
    快照 = 轉接器.查詢規劃權限("owner-1")
    assert [(項目.名稱, 項目.釘選修訂) for 項目 in 快照.工具] == [
        ("alpha-tool", "alpha-tool@bundle-v1"),
    ]
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)
    assert 轉接器.解析發布能力("owner-1", 摘要).權限快照.工具 == 快照.工具


def test_owner能力保留工具release有序authority(tmp_path):
    _技能(tmp_path, "alpha")
    上下文 = _上下文(
        tmp_path, enabled_tools={"skills_list", "skill_view"},
    )
    使用者庫 = _使用者庫(上下文)
    庫 = 工具發布庫()
    工具們 = tuple(
        工具發布註冊(
            f"{名稱}@bundle-v1",
            工具定義(
                名稱, 名稱 + " description",
                {"type": "object", "properties": {}}, lambda _: "ok",
            ),
        )
        for 名稱 in ("skills_list", "skill_view")
    )
    庫.登錄發布(工具發布描述("release-1", 工具們))
    轉接器 = 擁有者能力轉接器(使用者庫, 庫, "release-1")
    快照 = 轉接器.查詢規劃權限("owner-1")
    assert [項目.名稱 for 項目 in 快照.工具] == ["skills_list", "skill_view"]
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)
    發布能力 = 轉接器.解析發布能力("owner-1", 摘要)
    assert set(發布能力.工具結構快照) == {"skills_list", "skill_view"}
    assert 發布能力.權限快照.工具 == 快照.工具

    反向摘要 = 能力摘要(快照.權限修訂, 快照.技能, tuple(reversed(快照.工具)))
    with pytest.raises(擁有者能力錯誤, match="^擁有者發布能力不可用$"):
        轉接器.解析發布能力("owner-1", 反向摘要)


def test_one_shot_Published能力以exact_pin排除互動工具(tmp_path):
    _技能(tmp_path, "alpha")
    使用者庫 = _使用者庫(_上下文(
        tmp_path, enabled_tools={"clarify", "alpha-tool"},
    ))
    庫 = 工具發布庫()
    工具們 = (
        工具發布註冊("clarify@published-v1", 工具定義(
            "clarify", "interactive", {"type": "object"}, lambda _: "no channel",
        )),
        工具發布註冊("rev-a", 工具定義(
            "alpha-tool", "safe", {"type": "object"}, lambda _: "ok",
        )),
    )
    庫.登錄發布(工具發布描述("release-1", 工具們))
    轉接器 = 擁有者能力轉接器(使用者庫, 庫, "release-1")
    快照 = 轉接器.查詢規劃權限("owner-1")
    assert [(項.名稱, 項.釘選修訂) for 項 in 快照.工具] == [("alpha-tool", "rev-a")]
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)
    發布能力 = 轉接器.解析發布能力("owner-1", 摘要)
    assert tuple(發布能力.工具結構快照) == ("alpha-tool",)
    assert [項.名稱 for 項 in 發布能力.權限快照.工具] == ["alpha-tool"]


def test_one_shot禁止工具必須exact_revision且政策格式fail_closed(tmp_path):
    _技能(tmp_path, "alpha")
    使用者庫 = _使用者庫(_上下文(tmp_path, enabled_tools={"clarify"}))
    庫 = 工具發布庫()
    庫.登錄發布(工具發布描述("release-1", (工具發布註冊(
        "clarify@published-v2", 工具定義("clarify", "safe-v2", {"type": "object"}, lambda _: "ok"),
    ),)))
    轉接器 = 擁有者能力轉接器(使用者庫, 庫, "release-1")
    assert [(項.名稱, 項.釘選修訂) for 項 in 轉接器.查詢規劃權限("owner-1").工具] == [
        ("clarify", "clarify@published-v2"),
    ]
    with pytest.raises(擁有者能力錯誤):
        擁有者能力轉接器(
            使用者庫, 庫, "release-1",
            cast(Any, {("clarify", "clarify@published-v1")}),
        )


def test_permission_revision使用完整canonical_authority(tmp_path):
    內容 = "---\nname: alpha\ndescription: alpha summary\n---\n# Alpha\n"
    _技能(tmp_path, "alpha", "# Alpha\n")
    轉接器, _ = _轉接器(tmp_path, roles=["member", "admin"])
    快照 = 轉接器.查詢規劃權限("owner-1")
    雜湊 = hashlib.sha256(內容.encode()).hexdigest()
    根狀態 = tmp_path.stat()
    投影 = {
        "owner": "owner-1", "roles": ["admin", "member"], "skill_roots": [{
            "path": str(tmp_path.absolute()), "device": 根狀態.st_dev,
            "inode": 根狀態.st_ino, "mode": 根狀態.st_mode,
            "hash": hashlib.sha256(repr((("alpha/SKILL.md", "alpha", 雜湊),)).encode("utf-8")).hexdigest(),
        }],
        "handler_release": "release-1",
        "skills": [{"name": "alpha", "summary": "alpha summary", "content_sha256_reference": 雜湊}],
        "tools": [{"name": "alpha-tool", "revision": "rev-a", "description": "alpha-tool description",
                   "parameters": {"properties": {}, "type": "object"}}],
    }
    assert 快照.權限修訂 == "perm-" + hashlib.sha256(建立正規JSON(投影).encode()).hexdigest()


def test_snapshot與發布解析結果皆detached且每次重查(tmp_path):
    _技能(tmp_path, "alpha")
    轉接器, 使用者庫 = _轉接器(tmp_path, roles=["member"])
    快照 = 轉接器.查詢規劃權限("owner-1")
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)
    結果 = 轉接器.解析發布能力("owner-1", 摘要)
    object.__setattr__(快照.技能[0], "名稱", "forged")
    表 = 結果.建立技能表()
    表["alpha"] = Path("/forged")
    assert 轉接器.查詢規劃權限("owner-1").技能[0].名稱 == "alpha"
    assert 結果.建立技能表()["alpha"] == tmp_path / "alpha"
    assert 使用者庫.呼叫 == ["owner-1", "owner-1", "owner-1"]


def test_publish_recheck拒絕permission_content_tool漂移與撤銷(tmp_path):
    技能目錄 = _技能(tmp_path, "alpha")
    轉接器, 使用者庫 = _轉接器(tmp_path)
    快照 = 轉接器.查詢規劃權限("owner-1")
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)
    assert tuple(轉接器.解析發布能力("owner-1", 摘要).建立技能表()) == ("alpha",)

    使用者庫.上下文.roles.append("admin")
    with pytest.raises(擁有者能力錯誤):
        轉接器.解析發布能力("owner-1", 摘要)
    使用者庫.上下文.roles.pop()
    (技能目錄 / "SKILL.md").write_text("---\nname: alpha\ndescription: changed\n---\n", encoding="utf-8")
    with pytest.raises(擁有者能力錯誤):
        轉接器.解析發布能力("owner-1", 摘要)

    另一個, _ = _轉接器(tmp_path)
    新快照 = 另一個.查詢規劃權限("owner-1")
    新摘要 = 能力摘要(新快照.權限修訂, 新快照.技能, 新快照.工具)
    另一個._工具發布庫.移除發布("release-1")
    with pytest.raises(擁有者能力錯誤):
        另一個.解析發布能力("owner-1", 新摘要)


def test_missing_exact_release不使用current_latest或其他發布(tmp_path):
    _技能(tmp_path, "alpha")
    使用者庫 = _使用者庫(_上下文(tmp_path))
    庫 = _工具庫(release="current")
    轉接器 = 擁有者能力轉接器(使用者庫, 庫, "missing")
    with pytest.raises(擁有者能力錯誤):
        轉接器.查詢規劃權限("owner-1")


def test_發布解析只投影草稿exact_selected_tools與skills(tmp_path):
    """owner 另有授權時，未被草稿選取的工具與技能不得進入發布能力。"""
    _技能(tmp_path, "alpha")
    _技能(tmp_path, "beta")
    使用者庫 = _使用者庫(_上下文(
        tmp_path, enabled_tools={"alpha-tool", "beta-tool"}, enabled_skills={"alpha", "beta"},
    ))
    庫 = 工具發布庫()
    工具們 = tuple(
        工具發布註冊(
            f"rev-{名稱[0]}",
            工具定義(名稱, 名稱 + " description", {"type": "object", "properties": {}}, lambda _: "ok"),
        )
        for 名稱 in ("alpha-tool", "beta-tool")
    )
    庫.登錄發布(工具發布描述("release-1", 工具們))
    轉接器 = 擁有者能力轉接器(使用者庫, 庫, "release-1")
    完整 = 轉接器.查詢規劃權限("owner-1")
    摘要 = 能力摘要(完整.權限修訂, (完整.技能[0],), (完整.工具[0],))
    結果 = 轉接器.解析發布能力("owner-1", 摘要)
    assert tuple(結果.建立技能表()) == ("alpha",)
    assert tuple(結果.工具結構快照) == ("alpha-tool",)
    assert [項目.名稱 for 項目 in 結果.權限快照.技能] == ["alpha"]
    assert [項目.名稱 for 項目 in 結果.權限快照.工具] == ["alpha-tool"]
