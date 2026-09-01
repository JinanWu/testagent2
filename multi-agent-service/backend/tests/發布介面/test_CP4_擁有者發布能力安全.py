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


@pytest.mark.parametrize("控制", [KeyboardInterrupt("K"), SystemExit("S"), GeneratorExit("G")])
def test_control_flow保留identity_args並清除鏈與production_locals(tmp_path, 控制):
    標記 = "OWNER-CONTROL-SECRET"

    class _失敗來源:
        def __init__(self):
            self.敏感 = 標記

        def 建立使用者上下文(self, user_id=None):
            try:
                raise RuntimeError(標記)
            except RuntimeError:
                raise 控制

    轉接器 = 擁有者能力轉接器(_失敗來源(), _工具庫(), "release-1")
    原args = 控制.args
    控制.__traceback__ = None
    with pytest.raises(type(控制)) as 捕捉:
        轉接器.查詢規劃權限("owner-1")
    assert 捕捉.value is 控制 and 控制.args == 原args
    assert 控制.__cause__ is 控制.__context__ is None and 控制.__suppress_context__ is True
    追蹤 = 控制.__traceback__
    while 追蹤 is not None:
        框 = 追蹤.tb_frame
        if 框.f_code.co_filename.endswith("擁有者能力.py"):
            assert all(not _可達標記(值, 標記, set()) for 值 in tuple(框.f_locals.values()))
        追蹤 = 追蹤.tb_next


def test_cleanup失敗不可覆蓋原control_identity與args(tmp_path, monkeypatch):
    import 繁中代理.發布介面.規劃.擁有者能力 as 能力模組

    原控制 = SystemExit("ORIGINAL")

    class _控制來源:
        def 建立使用者上下文(self, user_id=None):
            raise 原控制

    def cleanup控制(_):
        raise KeyboardInterrupt("CLEANUP")

    monkeypatch.setattr(能力模組.traceback, "clear_frames", cleanup控制)
    轉接器 = 擁有者能力轉接器(_控制來源(), _工具庫(), "release-1")
    原args = 原控制.args
    with pytest.raises(SystemExit) as 捕捉:
        轉接器.查詢規劃權限("owner-1")
    assert 捕捉.value is 原控制 and 捕捉.value.args is 原args


def test_aggregate雙讀預算邊界含兩次加一probe且超額前不讀(tmp_path, monkeypatch):
    根 = tmp_path / "skills"
    _寫技能(根)
    大小 = (根 / "alpha" / "SKILL.md").stat().st_size
    限制 = 技能目錄限制(256 * 1024, 10, 2 * (大小 + 1), 20)
    assert [項.名稱 for 項 in 建立錨定安全技能目錄((根,), None, 上限=限制).技能] == ["alpha"]

    import 繁中代理.發布介面.安全技能目錄 as 目錄模組
    原read = os.read
    讀取次數 = 0

    def 計數read(fd, count):
        nonlocal 讀取次數
        讀取次數 += 1
        return 原read(fd, count)

    monkeypatch.setattr(目錄模組.os, "read", 計數read)
    with pytest.raises(技能目錄錯誤):
        建立錨定安全技能目錄(
            (根,), None, 上限=技能目錄限制(256 * 1024, 10, 2 * (大小 + 1) - 1, 20),
        )
    assert 讀取次數 == 0


def test_root_swap供應evil再restore仍只讀anchored_root(tmp_path, monkeypatch):
    import 繁中代理.發布介面.安全技能目錄 as 目錄模組

    根 = tmp_path / "skills"
    _寫技能(根, "ORIGINAL")
    原open = os.open
    已競態 = False

    def 競態open(path, flags, *args, **kwargs):
        nonlocal 已競態
        if path == "SKILL.md" and kwargs.get("dir_fd") is not None and not 已競態:
            已競態 = True
            舊根 = tmp_path / "old"
            根.rename(舊根)
            _寫技能(根, "EVIL")
            fd = 原open(path, flags, *args, **kwargs)
            for 子 in (根 / "alpha").iterdir():
                子.unlink()
            (根 / "alpha").rmdir()
            根.rmdir()
            舊根.rename(根)
            return fd
        return 原open(path, flags, *args, **kwargs)

    monkeypatch.setattr(目錄模組.os, "open", 競態open)
    結果 = 建立錨定安全技能目錄((根,), None)
    assert 已競態 and "ORIGINAL" in 結果.技能[0].內容 and "EVIL" not in 結果.技能[0].內容


def test_root_open前lstat後被換成symlink時拒絕且不供應evil(tmp_path, monkeypatch):
    import 繁中代理.發布介面.安全技能目錄 as 目錄模組

    根, evil = tmp_path / "skills", tmp_path / "evil"
    _寫技能(根, "ORIGINAL")
    _寫技能(evil, "EVIL")
    原open = os.open
    已替換 = False

    def 競態open(path, flags, *args, **kwargs):
        nonlocal 已替換
        if path == 根.name and kwargs.get("dir_fd") is not None and not 已替換:
            已替換 = True
            根.rename(tmp_path / "old")
            根.symlink_to(evil, target_is_directory=True)
        return 原open(path, flags, *args, **kwargs)

    monkeypatch.setattr(目錄模組.os, "open", 競態open)
    with pytest.raises((技能目錄錯誤, OSError)):
        建立錨定安全技能目錄((根,), None)
    assert 已替換


def test_anchored_descriptor_stack在子掃描失敗後不洩漏任何fd(tmp_path, monkeypatch):
    import 繁中代理.發布介面.安全技能目錄 as 目錄模組

    根 = tmp_path / "skills"
    _寫技能(根)
    原open, 原scandir = os.open, os.scandir
    已開啟: list[int] = []
    掃描次數 = 0

    def 追蹤open(*args, **kwargs):
        fd = 原open(*args, **kwargs)
        已開啟.append(fd)
        return fd

    def 失敗scandir(fd):
        nonlocal 掃描次數
        掃描次數 += 1
        if 掃描次數 == 2:
            raise RuntimeError("child scan failed")
        return 原scandir(fd)

    monkeypatch.setattr(目錄模組.os, "open", 追蹤open)
    monkeypatch.setattr(目錄模組.os, "scandir", 失敗scandir)
    with pytest.raises(RuntimeError, match="child scan failed"):
        建立錨定安全技能目錄((根,), None)
    for fd in 已開啟:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert 已開啟


def test_primary期間close在釋放前失敗只嘗試一次且不猜測retry(tmp_path, monkeypatch):
    import 繁中代理.發布介面.安全技能目錄 as 目錄模組

    根 = tmp_path / "skills"
    _寫技能(根)
    原控制 = SystemExit("PRIMARY", "ARGS")
    原close = os.close
    目標描述器 = None
    關閉嘗試: list[int] = []
    哨兵描述器 = os.open("/dev/null", os.O_RDONLY)

    def 失敗讀取(描述器, _大小):
        nonlocal 目標描述器
        目標描述器 = 描述器
        raise 原控制

    def 釋放前失敗(描述器):
        關閉嘗試.append(描述器)
        if 描述器 == 目標描述器:
            raise KeyboardInterrupt("close-before-release")
        return 原close(描述器)

    monkeypatch.setattr(目錄模組.os, "read", 失敗讀取)
    monkeypatch.setattr(目錄模組.os, "close", 釋放前失敗)
    原參數 = 原控制.args
    try:
        with pytest.raises(SystemExit) as 捕捉:
            建立錨定安全技能目錄((根,), None)

        assert 捕捉.value is 原控制 and 捕捉.value.args is 原參數
        assert 目標描述器 is not None
        assert 關閉嘗試.count(目標描述器) == 1
        assert len(關閉嘗試) == len(set(關閉嘗試))
        os.fstat(哨兵描述器)
        os.fstat(目標描述器)
    finally:
        monkeypatch.setattr(目錄模組.os, "close", 原close)
        for 描述器 in (哨兵描述器, 目標描述器):
            if 描述器 is not None:
                try:
                    原close(描述器)
                except OSError:
                    pass


def test_primary期間close釋放後同inode同fd重用仍不會誤關哨兵(tmp_path, monkeypatch):
    import 繁中代理.發布介面.安全技能目錄 as 目錄模組

    根 = tmp_path / "skills"
    _寫技能(根)
    技能路徑 = 根 / "alpha" / "SKILL.md"
    原控制 = GeneratorExit("PRIMARY", "ARGS")
    原close = os.close
    目標描述器 = None
    哨兵描述器 = None
    關閉嘗試: list[int] = []

    def 失敗讀取(描述器, _大小):
        nonlocal 目標描述器
        目標描述器 = 描述器
        raise 原控制

    def 釋放後失敗(描述器):
        nonlocal 哨兵描述器
        關閉嘗試.append(描述器)
        原close(描述器)
        if 描述器 == 目標描述器:
            哨兵描述器 = os.open(技能路徑, os.O_RDONLY)
            assert 哨兵描述器 == 描述器
            raise KeyboardInterrupt("close-after-release")

    monkeypatch.setattr(目錄模組.os, "read", 失敗讀取)
    monkeypatch.setattr(目錄模組.os, "close", 釋放後失敗)
    原參數 = 原控制.args
    try:
        with pytest.raises(GeneratorExit) as 捕捉:
            建立錨定安全技能目錄((根,), None)

        assert 捕捉.value is 原控制 and 捕捉.value.args is 原參數
        assert 目標描述器 is not None
        assert 哨兵描述器 is not None and 哨兵描述器 == 目標描述器
        assert 關閉嘗試.count(目標描述器) == 1
        哨兵狀態 = os.fstat(哨兵描述器)
        路徑狀態 = 技能路徑.stat()
        assert (哨兵狀態.st_dev, 哨兵狀態.st_ino) == (路徑狀態.st_dev, 路徑狀態.st_ino)
    finally:
        monkeypatch.setattr(目錄模組.os, "close", 原close)
        if 哨兵描述器 is not None:
            原close(哨兵描述器)
