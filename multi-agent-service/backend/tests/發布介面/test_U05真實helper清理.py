"""RUN U05：真實 canonical JSON 與 bundle hash helper frame 清理。"""

import hashlib
import sys

import pytest

import 繁中代理.發布介面.執行期.執行器 as 執行器模組
from 繁中代理.發布介面.執行期.模型契約 import 模型設定快照
from 繁中代理.發布介面.執行期.執行器 import (
    技能套件檔案, 發布執行快照, 發布執行請求, 發布執行錯誤,
    計算技能套件雜湊,
)

標記 = "U05_REAL_HELPER_SECRET"
控制型別 = [KeyboardInterrupt, SystemExit, GeneratorExit]
控制型別 += [type(f"控制子類{索引}", (型別,), {}) for 索引, 型別 in enumerate(控制型別)]


def _含標記(值, 已看):
    if id(值) in 已看:
        return False
    已看.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is bytes:
        return 標記.encode() in 值
    if isinstance(值, BaseException):
        return _含標記(值.args, 已看)
    if type(值) is dict:
        for 鍵, 子值 in 值.items():
            if _含標記(鍵, 已看) or _含標記(子值, 已看):
                return True
        return False
    if type(值) in (tuple, list, set, frozenset):
        for 子值 in 值:
            if _含標記(子值, 已看):
                return True
        return False
    try:
        欄位們 = object.__getattribute__(值, "__dict__")
    except (AttributeError, TypeError):
        欄位們 = None
    if 欄位們 is not None and _含標記(欄位們, 已看):
        return True
    for 類別 in type(值).__mro__:
        插槽們 = 類別.__dict__.get("__slots__", ())
        if type(插槽們) is str:
            插槽們 = (插槽們,)
        for 插槽 in 插槽們:
            try:
                if _含標記(object.__getattribute__(值, 插槽), 已看):
                    return True
            except (AttributeError, TypeError):
                pass
    return False


def _執行器框架皆乾淨(錯誤, 必含):
    名稱們 = []
    追蹤 = 錯誤.__traceback__
    while 追蹤 is not None:
        框架 = 追蹤.tb_frame
        if 框架.f_code.co_filename.endswith("執行器.py"):
            名稱們.append(框架.f_code.co_name)
            for 值 in tuple(框架.f_locals.values()):
                assert not _含標記(值, set()), 框架.f_code.co_name
        追蹤 = 追蹤.tb_next
    assert 必含 <= set(名稱們)


def _檔案():
    內容 = 標記.encode()
    return (技能套件檔案(
        path=f"{標記}.md", sha256=hashlib.sha256(內容).hexdigest(), content=內容,
    ),)


def _結構快照():
    return 發布執行快照(
        endpoint_id="endpoint-1", version_id="version-1", service_account_id="sa-1",
        system_prompt="prompt", permission_snapshot_digest="a" * 64,
        skill_bundle_hash="b" * 64, tool_handler_release="release-1", tool_snapshot=(),
        model_config=模型設定快照("fake", "model", 0, 10, 5, True, 1),
        response_schema={"marker": 標記}, manifest_reference="bundle-1/manifest.json",
    )


@pytest.mark.parametrize("例外型別", 控制型別)
def test_json_dumps控制在真實建立正規JSON框架逐層清理(monkeypatch, 例外型別):
    原函式 = 執行器模組.json.dumps
    呼叫數 = 0
    中斷 = 例外型別(標記)

    def 受控函式(*參數, **命名參數):
        nonlocal 呼叫數
        呼叫數 += 1
        if sys._getframe(1).f_code.co_name == "_建立正規JSON":
            raise 中斷
        return 原函式(*參數, **命名參數)

    monkeypatch.setattr(執行器模組.json, "dumps", 受控函式)
    with pytest.raises(例外型別) as 錯誤:
        發布執行請求({"marker": 標記})
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,) and 呼叫數 >= 2
    _執行器框架皆乾淨(錯誤.value, {"_建立正規JSON", "__init__"})


@pytest.mark.parametrize("例外型別", 控制型別)
def test_json_dumps控制在真實套件雜湊框架清理(monkeypatch, 例外型別):
    中斷 = 例外型別(標記)
    monkeypatch.setattr(執行器模組.json, "dumps", lambda *_參數, **_命名: (_ for _ in ()).throw(中斷))
    with pytest.raises(例外型別) as 錯誤:
        計算技能套件雜湊(_檔案())
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,)
    _執行器框架皆乾淨(錯誤.value, {"計算技能套件雜湊"})


@pytest.mark.parametrize("入口", ["input", "schema"])
@pytest.mark.parametrize("例外型別", 控制型別)
def test_json_loads控制在真實解析與公開property框架清理(monkeypatch, 入口, 例外型別):
    目標 = 發布執行請求({"marker": 標記}) if 入口 == "input" else _結構快照()
    中斷 = 例外型別(標記)

    def 受控函式(原文, **_命名):
        assert 標記 in 原文
        raise 中斷

    monkeypatch.setattr(執行器模組.json, "loads", 受控函式)
    with pytest.raises(例外型別) as 錯誤:
        _ = 目標.input if 入口 == "input" else 目標.response_schema
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,)
    _執行器框架皆乾淨(錯誤.value, {"_解析正規JSON", 入口 if 入口 == "input" else "response_schema"})


@pytest.mark.parametrize("入口", ["建立", "解析"])
@pytest.mark.parametrize("例外型別", 控制型別)
def test_複製JSON控制在兩個真實canonical_helper框架清理(monkeypatch, 入口, 例外型別):
    請求 = 發布執行請求({"marker": 標記})
    原函式 = 執行器模組.複製JSON
    呼叫數 = 0
    中斷 = 例外型別(標記)

    def 受控函式(*參數):
        nonlocal 呼叫數
        呼叫數 += 1
        if 入口 == "解析" or 呼叫數 == 2:
            raise 中斷
        return 原函式(*參數)

    monkeypatch.setattr(執行器模組, "複製JSON", 受控函式)
    with pytest.raises(例外型別) as 錯誤:
        if 入口 == "建立":
            發布執行請求({"marker": 標記})
        else:
            _ = 請求.input
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,)
    必含 = "_建立正規JSON" if 入口 == "建立" else "_解析正規JSON"
    _執行器框架皆乾淨(錯誤.value, {必含})


def test_canonical套件雜湊控制仍清除檔案projection(monkeypatch):
    """確認 BUNDLE canonical helper 的控制例外維持 identity 且清除 projection。

    參數：``monkeypatch`` 替換執行器捕捉的 canonical bundle helper。回傳：無。
    例外：測試只接受注入的 ``KeyboardInterrupt``。副作用：測試期間暫時替換 helper。
    """
    呼叫數 = 0
    中斷 = KeyboardInterrupt(標記)

    def 受控函式(項目們):
        """檢查 projection 不攜帶原始內容後注入控制例外。

        參數：``項目們`` 是 ordered file 三元組來源。回傳：不適用。
        例外：固定拋出測試控制例外。副作用：遞增區域呼叫計數。
        """
        nonlocal 呼叫數
        呼叫數 += 1
        assert len(項目們) == 1
        assert set(項目們[0]) == {"path", "size_bytes", "sha256"}
        assert "content" not in 項目們[0]
        raise 中斷

    monkeypatch.setattr(執行器模組, "計算清單套件雜湊", 受控函式)
    with pytest.raises(KeyboardInterrupt) as 錯誤:
        計算技能套件雜湊(_檔案())
    assert 錯誤.value is 中斷 and 呼叫數 == 1
    _執行器框架皆乾淨(錯誤.value, {"計算技能套件雜湊"})


@pytest.mark.parametrize("入口", ["request", "input", "hash"])
def test_普通nested_dependency失敗在公開邊界固定無鏈(monkeypatch, 入口):
    if 入口 == "request":
        原函式 = 執行器模組.json.dumps
        呼叫數 = 0
        def 失敗函式(*參數, **命名參數):
            nonlocal 呼叫數
            呼叫數 += 1
            if sys._getframe(1).f_code.co_name == "_建立正規JSON":
                raise RuntimeError(標記)
            return 原函式(*參數, **命名參數)
        monkeypatch.setattr(執行器模組.json, "dumps", 失敗函式)
        呼叫 = lambda: 發布執行請求({"marker": 標記})
    elif 入口 == "input":
        請求 = 發布執行請求({"marker": 標記})
        monkeypatch.setattr(執行器模組.json, "loads", lambda *_參數, **_命名: (_ for _ in ()).throw(RuntimeError(標記)))
        呼叫 = lambda: 請求.input
    else:
        monkeypatch.setattr(執行器模組.json, "dumps", lambda *_參數, **_命名: (_ for _ in ()).throw(RuntimeError(標記)))
        呼叫 = lambda: 計算技能套件雜湊(_檔案())
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        呼叫()
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
