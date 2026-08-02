import hashlib
import pytest

import 繁中代理.發布介面.執行期.執行器 as 執行器模組
from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行快照, 發布執行請求,
    發布執行錯誤, 計算技能套件雜湊,
)
from 繁中代理.發布介面.執行期.模型契約 import 模型設定快照


def test_請求保存canonical_fresh_JSON():
    請求 = 發布執行請求({"nested": [1]})
    第一份 = 請求.input
    第一份["nested"].append(2)
    assert 請求.input == {"nested": [1]}
    assert object.__getattribute__(請求, "_input_json") == '{"nested":[1]}'


def test_技能套件拒絕非正規路徑並驗證雜湊():
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        技能套件檔案(path="../SKILL.md", sha256="a" * 64, content=b"x")
    內容 = b"ok"
    檔案 = 技能套件檔案(path="SKILL.md", sha256=hashlib.sha256(內容).hexdigest(), content=內容)
    assert len(計算技能套件雜湊((檔案,))) == 64


def test_schema預檢接受本機ref拒絕外部ref():
    assert 執行器模組._綱要只含本機參照({"$defs": {"x": {"type": "string"}}, "$ref": "#/$defs/x"})
    assert not 執行器模組._綱要只含本機參照({"$ref": "https://example.invalid/schema"})
    綱要 = '{"properties":{"x":{"type":"integer"}},"required":["x"],"type":"object"}'
    assert 執行器模組._預檢回應綱要(綱要) is None
    assert 執行器模組._模型輸出符合綱要('{"x":1}', 綱要)
    assert not 執行器模組._模型輸出符合綱要('{"x":"bad"}', 綱要)
