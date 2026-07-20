"""CP4 Owner capability adapter：檔案系統界限與控制流程清理。"""
from __future__ import annotations

import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.規劃.擁有者能力 import 擁有者能力轉接器, 擁有者能力錯誤
from 繁中代理.發布介面.規劃.權限協調 import 能力摘要
from 繁中代理.發布介面.安全技能目錄 import (
    建立錨定安全技能目錄, 技能目錄限制, 技能目錄錯誤,
)


def _寫技能(根: Path, 本文="# Alpha\n"):
    目錄 = 根 / "alpha"
    目錄.mkdir(parents=True)
    (目錄 / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: alpha summary\n---\n" + 本文, encoding="utf-8",
    )


def _工具庫():
    工具 = 工具定義("alpha-tool", "safe", {"type": "object"}, lambda _: None)
    庫 = 工具發布庫()
    庫.登錄發布(工具發布描述("release-1", (工具發布註冊("rev-a", 工具),)))
    return 庫


class _來源:
    def __init__(self, 根):
        self.上下文 = 使用者上下文(
            user_id="owner-1", roles=["member"], enabled_tools={"alpha-tool"},
            enabled_skills={"alpha"}, skill_roots=[根], disabled=False,
        )

    def 建立使用者上下文(self, user_id=None):
        return self.上下文


def _建立(根):
    return 擁有者能力轉接器(_來源(根), _工具庫(), "release-1")


def test_oversized與symlink技能皆不進入authority_snapshot(tmp_path):
    過大根 = tmp_path / "large"
    _寫技能(過大根, "x" * (256 * 1024))
    assert _建立(過大根).查詢規劃權限("owner-1").技能 == ()

    連結根 = tmp_path / "linked"
    外部 = tmp_path / "outside.md"
    外部.write_text("---\nname: alpha\ndescription: escaped\n---\n", encoding="utf-8")
    (連結根 / "alpha").mkdir(parents=True)
    os.symlink(外部, 連結根 / "alpha" / "SKILL.md")
    assert _建立(連結根).查詢規劃權限("owner-1").技能 == ()


def test_publish_recheck拒絕root_path被symlink替換(tmp_path):
    公開根 = tmp_path / "skills"
    _寫技能(公開根)
    轉接器 = _建立(公開根)
    快照 = 轉接器.查詢規劃權限("owner-1")
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)

    舊根 = tmp_path / "old-skills"
    公開根.rename(舊根)
    攻擊根 = tmp_path / "attacker"
    _寫技能(攻擊根)
    os.symlink(攻擊根, 公開根)
    with pytest.raises(擁有者能力錯誤):
        轉接器.解析發布能力("owner-1", 摘要)


def test_configured_symlink_root在resolve前拒絕(tmp_path):
    真根 = tmp_path / "real"
    _寫技能(真根)
    連結根 = tmp_path / "configured-link"
    os.symlink(真根, 連結根)
    with pytest.raises(擁有者能力錯誤):
        _建立(連結根).查詢規劃權限("owner-1")


def test_configured_root任何中間component為symlink皆拒絕(tmp_path):
    真父 = tmp_path / "real-parent"
    真根 = 真父 / "skills"
    _寫技能(真根)
    連結父 = tmp_path / "linked-parent"
    os.symlink(真父, 連結父)

    with pytest.raises(擁有者能力錯誤):
        _建立(連結父 / "skills").查詢規劃權限("owner-1")


def test_dotdot僅lexical正規化且不要求被消去component存在(tmp_path):
    根 = tmp_path / "skills"
    _寫技能(根)
    正規 = _建立(根).查詢規劃權限("owner-1")
    別名 = tmp_path / "does-not-exist" / ".." / "skills"

    assert _建立(別名).查詢規劃權限("owner-1").權限修訂 == 正規.權限修訂


def test_same_inode_root_alias重複明確拒絕(tmp_path):
    根 = tmp_path / "skills"
    _寫技能(根)
    來源 = _來源(根)
    來源.上下文.skill_roots = [根, 根 / "does-not-exist" / ".."]
    轉接器 = 擁有者能力轉接器(來源, _工具庫(), "release-1")

    with pytest.raises(擁有者能力錯誤):
        轉接器.查詢規劃權限("owner-1")


def test_publish_recheck拒絕同path同content的root_inode替換(tmp_path):
    根 = tmp_path / "skills"
    _寫技能(根)
    轉接器 = _建立(根)
    快照 = 轉接器.查詢規劃權限("owner-1")
    摘要 = 能力摘要(快照.權限修訂, 快照.技能, 快照.工具)

    原inode = 根.stat().st_ino
    根.rename(tmp_path / "old-skills")
    _寫技能(根)
    assert 根.stat().st_ino != 原inode
    with pytest.raises(擁有者能力錯誤):
        轉接器.解析發布能力("owner-1", 摘要)


def test_root_projection依canonical_path排序且不保留mutable清單(tmp_path):
    空根, 技能根 = tmp_path / "z-empty", tmp_path / "a-skills"
    空根.mkdir()
    _寫技能(技能根)
    roots = [空根, 技能根]
    上下文 = 使用者上下文(
        user_id="owner-1", roles=["member"], enabled_tools={"alpha-tool"},
        enabled_skills={"alpha"}, skill_roots=roots, disabled=False,
    )
    來源一 = _來源(技能根)
    來源一.上下文 = 上下文
    一 = 擁有者能力轉接器(來源一, _工具庫(), "release-1")
    二上下文 = 使用者上下文(
        user_id="owner-1", roles=["member"], enabled_tools={"alpha-tool"},
        enabled_skills={"alpha"}, skill_roots=list(reversed(roots)), disabled=False,
    )
    來源二 = _來源(技能根)
    來源二.上下文 = 二上下文
    二 = 擁有者能力轉接器(來源二, _工具庫(), "release-1")
    快照 = 一.查詢規劃權限("owner-1")
    roots.clear()
    assert 快照.權限修訂 == 二.查詢規劃權限("owner-1").權限修訂
    assert [項.名稱 for 項 in 快照.技能] == ["alpha"]


@pytest.mark.parametrize("競態", ["metadata", "content"])
def test_safe_catalog拒絕read後metadata或content_hash競態(tmp_path, monkeypatch, 競態):
    import 繁中代理.發布介面.安全技能目錄 as 目錄模組

    根 = tmp_path / "skills"
    _寫技能(根)
    if 競態 == "metadata":
        原fstat = os.fstat
        regular次數 = 0

        def 競態fstat(fd):
            nonlocal regular次數
            狀態 = 原fstat(fd)
            if stat.S_ISREG(狀態.st_mode):
                regular次數 += 1
                if regular次數 == 2:
                    return SimpleNamespace(
                        st_dev=狀態.st_dev, st_ino=狀態.st_ino, st_mode=狀態.st_mode,
                        st_size=狀態.st_size, st_mtime_ns=狀態.st_mtime_ns + 1,
                    )
            return 狀態

        monkeypatch.setattr(目錄模組.os, "fstat", 競態fstat)
    else:
        原read, 原lseek = os.read, os.lseek
        驗證中 = False

        def 追蹤lseek(fd, offset, whence):
            nonlocal 驗證中
            驗證中 = True
            return 原lseek(fd, offset, whence)

        def 競態read(fd, size):
            內容 = 原read(fd, size)
            if 驗證中 and 內容:
                return bytes([內容[0] ^ 1]) + 內容[1:]
            return 內容

        monkeypatch.setattr(目錄模組.os, "lseek", 追蹤lseek)
        monkeypatch.setattr(目錄模組.os, "read", 競態read)
    assert _建立(根).查詢規劃權限("owner-1").技能 == ()


def _可達標記(值, 標記, 已見):
    if 值 is None or id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is bytes:
        return 標記.encode() in 值
    if type(值) is dict:
        return any(_可達標記(項, 標記, 已見) for 對 in dict.items(值) for 項 in 對)
    if type(值) in (tuple, list, set, frozenset):
        return any(_可達標記(項, 標記, 已見) for 項 in 值)
    字典 = getattr(值, "__dict__", None)
    if type(字典) is dict:
        return _可達標記(字典, 標記, 已見)
    return False
