"""測試 todo 待辦清單工具。"""

import json

import pytest

from 繁中代理.工具集 import 待辦清單 as 模組
from 繁中代理.工具集.待辦清單 import 待辦清單

# autouse fixture 會把 解析清單鍵 換成固定值，這裡先留住真本尊供專門測它的案例使用。
真實解析清單鍵 = 模組.解析清單鍵


@pytest.fixture(autouse=True)
def 隔離待辦目錄(tmp_path, monkeypatch):
    """把待辦檔導向暫存目錄，並預設清單鍵，避免碰到真實家目錄。"""
    monkeypatch.setattr(模組, "待辦根目錄", lambda: tmp_path / "todos")
    monkeypatch.setattr(模組, "解析清單鍵", lambda 參數: "測試工作階段")
    return tmp_path


def 項目(識別碼, 內容, 狀態):
    """組出一筆待辦項目。"""
    return {"id": 識別碼, "content": 內容, "status": 狀態}


def test_未提供todos時只讀不寫():
    """確認空參數回傳目前清單，且不建立檔案。"""
    結果 = 待辦清單({})

    assert 結果["todos"] == []
    assert 結果["total_count"] == 0
    assert 結果["statistics"]["pending"] == 0
    assert not 模組.清單檔路徑("測試工作階段").exists()


def test_寫入後可讀回並帶統計():
    """確認寫入的清單能原樣讀回，並附各狀態計數。"""
    待辦清單({"todos": [
        項目("1", "查 schema", "completed"),
        項目("2", "寫 handler", "in_progress"),
        項目("3", "補測試", "pending"),
    ]})

    結果 = 待辦清單({})

    assert [項.get("content") for 項 in 結果["todos"]] == ["查 schema", "寫 handler", "補測試"]
    assert 結果["statistics"] == {
        "pending": 1, "in_progress": 1, "completed": 1, "cancelled": 0,
    }


def test_預設整份取代():
    """確認未給 merge 時舊清單被完全換掉。"""
    待辦清單({"todos": [項目("1", "舊計畫甲", "pending"), 項目("2", "舊計畫乙", "pending")]})

    結果 = 待辦清單({"todos": [項目("9", "全新計畫", "in_progress")]})

    assert [項["id"] for 項 in 結果["todos"]] == ["9"]


def test_merge只更新有列到的項目並保留順序():
    """確認 merge=true 依 id 更新、未列到的不動、新的接在尾端。"""
    待辦清單({"todos": [
        項目("1", "第一步", "in_progress"),
        項目("2", "第二步", "pending"),
        項目("3", "第三步", "pending"),
    ]})

    結果 = 待辦清單({"merge": True, "todos": [
        項目("1", "第一步", "completed"),
        項目("2", "第二步", "in_progress"),
        項目("4", "臨時追加", "pending"),
    ]})

    assert [(項["id"], 項["status"]) for 項 in 結果["todos"]] == [
        ("1", "completed"), ("2", "in_progress"), ("3", "pending"), ("4", "pending"),
    ]


def test_多個進行中會自動收斂並回報警告():
    """確認保留最後一個 in_progress，其餘降回 pending 並告知模型。"""
    結果 = 待辦清單({"todos": [
        項目("1", "任務甲", "in_progress"),
        項目("2", "任務乙", "in_progress"),
        項目("3", "任務丙", "in_progress"),
    ]})

    狀態表 = {項["id"]: 項["status"] for 項 in 結果["todos"]}
    assert 狀態表 == {"1": "pending", "2": "pending", "3": "in_progress"}
    assert len(結果["warnings"]) == 1
    assert "任務丙" in 結果["warnings"][0]
    assert "任務甲" in 結果["warnings"][0]


def test_單一進行中不產生警告():
    """確認合法輸入不會夾帶多餘警告。"""
    結果 = 待辦清單({"todos": [項目("1", "唯一任務", "in_progress")]})

    assert "warnings" not in 結果


def test_收斂結果會被保存():
    """確認自動降級後寫入磁碟，不是只改回傳值。"""
    待辦清單({"todos": [項目("1", "甲", "in_progress"), 項目("2", "乙", "in_progress")]})

    再讀 = 待辦清單({})

    assert {項["id"]: 項["status"] for 項 in 再讀["todos"]} == {"1": "pending", "2": "in_progress"}


@pytest.mark.parametrize("壞輸入", [
    "不是陣列",
    [["不是物件"]],
    [{"id": "", "content": "沒有id", "status": "pending"}],
    [{"content": "完全沒給 id", "status": "pending"}],
])
def test_結構性錯誤仍然拒絕(壞輸入):
    """確認無法修正的輸入（非陣列、非物件、缺可用 id）直接拒絕。"""
    with pytest.raises(ValueError):
        待辦清單({"todos": 壞輸入})
    assert 待辦清單({})["todos"] == []


def test_可修正的欄位問題以警告回報而非拒絕():
    """契約層會吃掉錯誤訊息，因此可修正的問題改成修正＋warnings 讓模型看得到。"""
    結果 = 待辦清單({"todos": [
        {"id": "1", "content": "", "status": "pending"},
        {"id": "2", "content": "狀態拼錯", "status": "done"},
        {"id": "3", "content": "完全沒給 status"},
    ]})

    狀態表 = {項["id"]: 項["status"] for 項 in 結果["todos"]}
    assert 狀態表 == {"1": "pending", "2": "pending", "3": "pending"}
    assert 結果["todos"][0]["content"] == "（未填寫內容）"
    警告文字 = " ".join(結果["warnings"])
    assert "沒有 content" in 警告文字
    assert "不合法" in 警告文字


def test_同批id重複以最後一筆為準():
    """確認重複 id 收斂而非拒絕整批，並告知模型。"""
    結果 = 待辦清單({"todos": [項目("1", "先寫的", "pending"), 項目("1", "後蓋掉", "completed")]})

    assert [(項["id"], 項["content"]) for 項 in 結果["todos"]] == [("1", "後蓋掉")]
    assert "重複 id" in " ".join(結果["warnings"])


def test_超過項目數上限遭拒():
    """確認單次寫入與合併後都受上限保護。"""
    過多 = [項目(str(序號), f"任務{序號}", "pending") for 序號 in range(模組.最大項目數 + 1)]
    with pytest.raises(ValueError):
        待辦清單({"todos": 過多})

    待辦清單({"todos": [項目(str(序號), f"任務{序號}", "pending") for 序號 in range(模組.最大項目數)]})
    with pytest.raises(ValueError):
        待辦清單({"merge": True, "todos": [項目("額外", "壓過上限", "pending")]})


def test_內容過長會截斷並警告():
    """確認過長內容被截到上限，而不是整批失敗。"""
    結果 = 待辦清單({"todos": [項目("1", "字" * (模組.最大內容長度 + 50), "pending")]})

    assert len(結果["todos"][0]["content"]) == 模組.最大內容長度
    assert "已截斷" in " ".join(結果["warnings"])


def test_merge只送status即可更新不必重打content():
    """對齊 Hermes：回報進度時只需 {id, status}，content 保持原樣。"""
    待辦清單({"todos": [
        項目("1", "很長的原始任務描述，不應該被迫重打", "in_progress"),
        項目("2", "第二步", "pending"),
    ]})

    結果 = 待辦清單({"merge": True, "todos": [{"id": "1", "status": "completed"}]})

    第一筆 = 結果["todos"][0]
    assert 第一筆["status"] == "completed"
    assert 第一筆["content"] == "很長的原始任務描述，不應該被迫重打"
    assert "warnings" not in 結果, "合法的部分更新不該產生警告"


def test_merge只送content可單獨改描述():
    """確認只給 content 時狀態不變。"""
    待辦清單({"todos": [項目("1", "原描述", "in_progress")]})

    結果 = 待辦清單({"merge": True, "todos": [{"id": "1", "content": "修正後的描述"}]})

    assert 結果["todos"][0] == {"id": "1", "content": "修正後的描述", "status": "in_progress"}


def test_merge遇到新id仍需完整建立():
    """確認 merge 模式下沒見過的 id 會被當成新項目建立。"""
    待辦清單({"todos": [項目("1", "既有", "in_progress")]})

    結果 = 待辦清單({"merge": True, "todos": [{"id": "9", "content": "新增的"}]})

    assert [項["id"] for 項 in 結果["todos"]] == ["1", "9"]
    assert 結果["todos"][1]["status"] == "pending"


def test_清單檔毀損時視為空清單(隔離待辦目錄):
    """確認壞掉的 JSON 不會讓工具整個炸掉。"""
    路徑 = 模組.清單檔路徑("測試工作階段")
    路徑.parent.mkdir(parents=True, exist_ok=True)
    路徑.write_text("{壞掉的 json", encoding="utf-8")

    assert 待辦清單({})["todos"] == []


def test_清單鍵不接受路徑分隔字元():
    """確認清單鍵無法逃出待辦目錄。"""
    for 壞鍵 in ["../逃脫", "a/b", "a\\b", ".隱藏"]:
        with pytest.raises(ValueError):
            模組.清單檔路徑(壞鍵)


def test_經由工具登錄器可正常呼叫(monkeypatch, tmp_path):
    """確認 todo 已登錄，且結果能通過工具結果契約層。"""
    from 繁中代理.工具註冊 import 建立預設工具登錄器

    登錄器 = 建立預設工具登錄器()
    assert 登錄器.工具表["todo"].處理函數.__name__ == "待辦清單"

    結果 = json.loads(登錄器.呼叫工具("todo", {"todos": [項目("1", "端到端", "in_progress")]}))

    assert 結果["success"] is True
    assert 結果["result"]["todos"][0]["content"] == "端到端"


def test_解析清單鍵用譜系根讓清單跨壓縮分裂存活(tmp_path, monkeypatch):
    """核心設計：壓縮分裂出新 session 後，仍指向同一份清單。"""
    from 繁中代理.工作階段上下文 import (
        設定目前工作階段識別碼, 設定目前工作階段資料庫路徑,
    )
    from 繁中代理.工作階段庫 import 工作階段庫

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    資料庫路徑 = tmp_path / "sessions.sqlite3"
    庫 = 工作階段庫(資料庫路徑)
    根識別碼 = 庫.建立或讀取工作階段(None, source="cli", user_id="u1", model="fake")
    庫.寫入訊息清單(根識別碼, [{"role": "user", "content": "原始對話"}])
    子識別碼 = 庫.建立壓縮後工作階段(根識別碼, [{"role": "user", "content": "壓縮後"}], "系統提示")
    assert 子識別碼 != 根識別碼

    設定目前工作階段資料庫路徑(str(資料庫路徑))
    try:
        設定目前工作階段識別碼(根識別碼)
        分裂前鍵 = 真實解析清單鍵({"_current_user_id": "u1"})
        設定目前工作階段識別碼(子識別碼)
        分裂後鍵 = 真實解析清單鍵({"_current_user_id": "u1"})
    finally:
        設定目前工作階段識別碼(None)
        設定目前工作階段資料庫路徑(None)

    assert 分裂前鍵 == 根識別碼
    assert 分裂後鍵 == 根識別碼, "壓縮分裂後應仍指向同一份清單"


def test_解析清單鍵拒絕跨使用者存取(tmp_path, monkeypatch):
    """確認別的使用者拿不到這份清單，不會退回較寬鬆的鍵。"""
    from 繁中代理.工作階段上下文 import (
        設定目前工作階段識別碼, 設定目前工作階段資料庫路徑,
    )
    from 繁中代理.工作階段庫 import 工作階段庫

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    資料庫路徑 = tmp_path / "sessions.sqlite3"
    庫 = 工作階段庫(資料庫路徑)
    識別碼 = 庫.建立或讀取工作階段(None, source="cli", user_id="owner", model="fake")

    設定目前工作階段資料庫路徑(str(資料庫路徑))
    設定目前工作階段識別碼(識別碼)
    try:
        with pytest.raises(PermissionError):
            真實解析清單鍵({"_current_user_id": "intruder"})
    finally:
        設定目前工作階段識別碼(None)
        設定目前工作階段資料庫路徑(None)


def test_沒有工作階段時仍可使用(monkeypatch):
    """確認 runtime 外呼叫（如手動測試）不會直接爆掉。"""
    from 繁中代理.工作階段上下文 import 設定目前工作階段識別碼

    設定目前工作階段識別碼(None)
    assert 真實解析清單鍵({}) == 模組.無工作階段鍵
