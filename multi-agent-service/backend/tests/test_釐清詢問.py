"""測試 clarify 釐清詢問工具。"""

import json

import pytest

from 繁中代理.工具集.釐清詢問 import (
    一次性詢問策略,
    建立釐清處理器,
    詢問未回應,
    回呼失敗原因,
    無互動通道原因,
    缺少問題原因,
    逾時原因,
    略過原因,
)


def 建立記錄用回呼(回答: str):
    """建立一個會記下收到什麼問題、並回傳固定答案的回呼。"""
    收到: dict = {}

    def 回呼(問題, 選項清單):
        收到["問題"] = 問題
        收到["選項清單"] = 選項清單
        return 回答

    return 回呼, 收到


def test_沒有互動通道時立即回指引不空等():
    """確認未注入回呼時直接回「沒人可問」指引，且標示未詢問。"""
    結果 = 建立釐清處理器()({"question": "要用哪個方案？"})

    assert 結果["asked"] is False
    assert 結果["answered"] is False
    assert 結果["reason"] == 無互動通道原因
    assert "no user available" in 結果["answer"]
    assert 結果["choice_index"] is None


def test_沒有互動通道時指引會帶上選項():
    """確認有 choices 時指引會列出選項，讓模型知道從哪幾個裡挑。"""
    結果 = 建立釐清處理器()({"question": "選哪個？", "choices": ["方案甲", "方案乙"]})

    assert "方案甲" in 結果["answer"]
    assert "方案乙" in 結果["answer"]


def test_使用者選了選項會回報命中位置():
    """確認回答命中選項時附上 choice_index。"""
    回呼, 收到 = 建立記錄用回呼("方案乙")

    結果 = 建立釐清處理器(回呼)({"question": "選哪個？", "choices": ["方案甲", "方案乙"]})

    assert 收到["問題"] == "選哪個？"
    assert 收到["選項清單"] == ["方案甲", "方案乙"]
    assert 結果["answered"] is True
    assert 結果["answer"] == "方案乙"
    assert 結果["choice_index"] == 1


def test_自由作答時沒有選項位置():
    """確認回答不在選項內時 choice_index 為 None，仍視為已回答。"""
    回呼, _ = 建立記錄用回呼("兩個都不要，改用方案丙")

    結果 = 建立釐清處理器(回呼)({"question": "選哪個？", "choices": ["方案甲", "方案乙"]})

    assert 結果["answered"] is True
    assert 結果["answer"] == "兩個都不要，改用方案丙"
    assert 結果["choice_index"] is None


def test_開放式問答不傳選項給平台():
    """確認省略 choices 時回呼收到 None，代表開放式問答。"""
    回呼, 收到 = 建立記錄用回呼("我想先看效能數據")

    結果 = 建立釐清處理器(回呼)({"question": "你怎麼看？"})

    assert 收到["選項清單"] is None
    assert 結果["answer"] == "我想先看效能數據"


def test_逾時回指引且標示已詢問():
    """確認平台逾時時模型收到「自行判斷」指引，且知道問題確實問出去過。"""
    def 逾時回呼(問題, 選項清單):
        raise 詢問未回應(逾時原因)

    結果 = 建立釐清處理器(逾時回呼)({"question": "要繼續嗎？"})

    assert 結果["asked"] is True
    assert 結果["answered"] is False
    assert 結果["reason"] == 逾時原因
    assert "did not provide a response within the time limit" in 結果["answer"]


def test_使用者略過回指引():
    """確認使用者按 Ctrl+C 略過時模型收到略過指引而非錯誤。"""
    def 略過回呼(問題, 選項清單):
        raise 詢問未回應(略過原因)

    結果 = 建立釐清處理器(略過回呼)({"question": "要繼續嗎？"})

    assert 結果["reason"] == 略過原因
    assert "skipped" in 結果["answer"]


def test_缺少問題回可讀指引而非丟錯():
    """確認漏給 question 時模型看得到原因。

    丟錯的話 `工具結果` 契約層會換成固定的「工具執行失敗」，模型不知道自己漏了
    什麼，也就無從補救。
    """
    回呼, 收到 = 建立記錄用回呼("不該被呼叫")

    結果 = 建立釐清處理器(回呼)({"question": "   "})

    assert 結果["reason"] == 缺少問題原因
    assert 結果["asked"] is False
    assert "non-empty `question`" in 結果["answer"]
    assert 收到 == {}, "沒有問題就不該去打擾使用者"


def test_詢問策略爆掉時回可讀指引():
    """確認平台實作出包時模型收到說明，而不是不透明的工具失敗。"""
    def 會爆的回呼(問題, 選項清單):
        raise RuntimeError("終端機壞了")

    結果 = 建立釐清處理器(會爆的回呼)({"question": "要繼續嗎？"})

    assert 結果["reason"] == 回呼失敗原因
    assert 結果["answered"] is False
    assert "could not be delivered" in 結果["answer"]


def test_一次性策略等同沒有互動通道():
    """確認明確注入的 oneshot 策略與「沒人可問」行為一致。"""
    結果 = 建立釐清處理器(一次性詢問策略)({"question": "選哪個？", "choices": ["甲", "乙"]})

    assert 結果["reason"] == 無互動通道原因
    assert 結果["asked"] is False
    assert "甲" in 結果["answer"]


def test_回傳會帶回問題與選項():
    """確認結果原樣帶回 question 與 choices，壓縮後仍看得出這個答案在回什麼。"""
    回呼, _ = 建立記錄用回呼("方案甲")

    結果 = 建立釐清處理器(回呼)({"question": "選哪個？", "choices": ["方案甲", "方案乙"]})

    assert 結果["question"] == "選哪個？"
    assert 結果["choices_offered"] == ["方案甲", "方案乙"]


def test_選項超過四個會截斷並警告():
    """確認超出 schema 上限的選項被截斷，並讓模型看到警告。"""
    回呼, 收到 = 建立記錄用回呼("甲")

    結果 = 建立釐清處理器(回呼)({
        "question": "選哪個？",
        "choices": ["甲", "乙", "丙", "丁", "戊"],
    })

    assert 收到["選項清單"] == ["甲", "乙", "丙", "丁"]
    assert any("最多 4 個" in 警告 for 警告 in 結果["warnings"])


def test_選項格式錯誤時退回開放式問答():
    """確認 choices 不是陣列時修正成開放式問答而非整次失敗。"""
    回呼, 收到 = 建立記錄用回呼("隨便")

    結果 = 建立釐清處理器(回呼)({"question": "選哪個？", "choices": "甲或乙"})

    assert 收到["選項清單"] is None
    assert 結果["answered"] is True
    assert 結果["warnings"]


def test_空白選項會被濾掉():
    """確認空字串選項不會出現在使用者面前。"""
    回呼, 收到 = 建立記錄用回呼("甲")

    建立釐清處理器(回呼)({"question": "選哪個？", "choices": ["甲", "", "  ", "乙"]})

    assert 收到["選項清單"] == ["甲", "乙"]


def test_登錄器會把注入的回呼接到clarify():
    """確認 clarify 經由工具登錄器呼叫時，用的是平台注入的策略。"""
    from 繁中代理.工具註冊 import 建立預設工具登錄器

    回呼, 收到 = 建立記錄用回呼("方案甲")
    登錄器 = 建立預設工具登錄器(".", None, 回呼)

    輸出 = json.loads(登錄器.呼叫工具("clarify", {"question": "選哪個？", "choices": ["方案甲"]}))

    assert 收到["問題"] == "選哪個？"
    assert 輸出["success"] is True
    assert 輸出["result"]["answer"] == "方案甲"


def test_登錄器未注入回呼時clarify仍可用():
    """確認一次性執行（沒有回呼）時 clarify 不會變成工具失敗。"""
    from 繁中代理.工具註冊 import 建立預設工具登錄器

    登錄器 = 建立預設工具登錄器(".", None)

    輸出 = json.loads(登錄器.呼叫工具("clarify", {"question": "選哪個？"}))

    assert 輸出["success"] is True
    assert 輸出["result"]["reason"] == 無互動通道原因


def 建立CLI詢問(monkeypatch, 輸入序列):
    """取得 互動CLI 的詢問策略，並把 input 換成預先排好的輸入。

    詢問使用者 不依賴任何實例狀態，故以未初始化的物件取用，避免為了測一段互動
    邏輯而開 DB、建 runtime。
    """
    from 繁中代理 import cli

    待輸入 = list(輸入序列)
    monkeypatch.setattr(cli, "input", lambda 提示="": 待輸入.pop(0), raising=False)
    return cli.互動CLI.詢問使用者.__get__(object.__new__(cli.互動CLI))


def test_CLI輸入編號會換成選項文字(monkeypatch):
    """確認使用者打 2 時回傳的是選項文字而非數字。"""
    詢問 = 建立CLI詢問(monkeypatch, ["2"])

    assert 詢問("選哪個？", ["方案甲", "方案乙"]) == "方案乙"


def test_CLI選其他會改問自由作答(monkeypatch):
    """確認選了「其他」不會把編號本身當答案，而是再問一次。"""
    詢問 = 建立CLI詢問(monkeypatch, ["3", "我要方案丙"])

    assert 詢問("選哪個？", ["方案甲", "方案乙"]) == "我要方案丙"


def test_CLI可直接打字不選編號(monkeypatch):
    """確認選項模式下仍可直接自由作答。"""
    詢問 = 建立CLI詢問(monkeypatch, ["都不好，再想想"])

    assert 詢問("選哪個？", ["方案甲", "方案乙"]) == "都不好，再想想"


def test_CLI空白輸入會再問一次(monkeypatch):
    """確認直接按 Enter 不會被當成空答案送出去。"""
    詢問 = 建立CLI詢問(monkeypatch, ["", "   ", "方案甲"])

    assert 詢問("選哪個？", ["方案甲"]) == "方案甲"


def test_CLI按Ctrl_C視為略過而非中斷REPL(monkeypatch):
    """確認略過會轉成 詢問未回應，不讓 KeyboardInterrupt 打掛整個 REPL。"""
    from 繁中代理 import cli

    def 中斷(提示=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "input", 中斷, raising=False)
    詢問 = cli.互動CLI.詢問使用者.__get__(object.__new__(cli.互動CLI))

    with pytest.raises(詢問未回應) as 例外:
        詢問("要繼續嗎？")

    assert 例外.value.原因 == 略過原因


def test_模型拿到答案後在同一個turn繼續(tmp_path):
    """確認 clarify 不會中斷 tool loop：問完就地拿到答案，同一次呼叫給出最終回答。

    這是 clarify 與「把問題當最終回答結案」最關鍵的差別，用完整 runtime 驗證。
    """
    from 繁中代理.代理執行階段 import 代理執行階段
    from 繁中代理.工作階段庫 import 工作階段庫
    from 繁中代理.模型供應商 import 模型回應

    class 會發問的供應商:
        """先呼叫 clarify，收到答案後直接產出最終回答。"""

        def __init__(self):
            self.看到的答案 = None

        def 產生回應(self, 訊息清單, 工具清單):
            """第一次回 clarify tool call，第二次回最終答案。"""
            if 訊息清單 and 訊息清單[-1].get("role") == "tool":
                self.看到的答案 = json.loads(訊息清單[-1]["content"])["result"]["answer"]
                return 模型回應(文字=f"好，就用{self.看到的答案}。", 完成原因="stop")
            return 模型回應(
                文字="",
                工具呼叫清單=[{
                    "id": "call_clarify",
                    "type": "function",
                    "function": {
                        "name": "clarify",
                        "arguments": json.dumps({"question": "用哪個方案？", "choices": ["方案甲", "方案乙"]}, ensure_ascii=False),
                    },
                }],
                完成原因="tool_calls",
            )

    供應商 = 會發問的供應商()
    runtime = 代理執行階段(
        工作階段庫(tmp_path / "sessions.sqlite3"),
        供應商,
        模型名稱="fake",
        供應商名稱="fake",
        工作目錄=str(tmp_path),
        詢問回呼=lambda 問題, 選項清單: 選項清單[1],
    )

    結果 = runtime.執行使用者訊息("幫我實作")

    assert 供應商.看到的答案 == "方案乙"
    assert 結果.最終回答 == "好，就用方案乙。"
    assert 結果.模型呼叫次數 == 2
    assert 結果.工具呼叫次數 == 1
