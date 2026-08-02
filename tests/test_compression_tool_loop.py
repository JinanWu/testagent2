"""測試 context compression 與 tool-call loop。"""

import json
from pathlib import Path
from typing import Any

專案根目錄 = Path(__file__).resolve().parents[1]

from 繁中代理.上下文壓縮器 import 上下文壓縮器, 粗估訊息Token數, 是否摘要訊息
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.模型供應商 import 假模型供應商, 模型回應


def test_舊代理執行階段不會自動啟用發布路徑():
    """未呼叫新 factory 時，舊 runtime 的公開執行契約完全不變。"""
    assert not hasattr(代理執行階段, "執行發布輸入")


def test_context_compression_threshold_floor():
    """確認 threshold 是 max(context_length * ratio, minimum floor)。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=10000, 觸發比例=0.5)
    assert 壓縮器.門檻Token數 == 8192
    assert 壓縮器.是否需要壓縮(5000) is False
    assert 壓縮器.是否需要壓縮(8192) is True


def test_context_compression_trigger():
    """確認超過門檻後保留頭尾並插入 reference-only 摘要。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 觸發比例=0.5, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": "開頭"}]
    for i in range(30):
        訊息清單.append({"role": "assistant", "content": "中段" + str(i) + "x" * 1200})
    訊息清單.append({"role": "user", "content": "最新問題"})
    結果 = 壓縮器.壓縮訊息(訊息清單, 系統提示詞="s" * 1000)
    assert 結果.是否已壓縮 is True
    assert 結果.訊息清單[0]["content"] == "開頭"
    assert any(是否摘要訊息(訊息) for 訊息 in 結果.訊息清單)
    assert "REFERENCE ONLY" in "\n".join(str(訊息.get("content", "")) for 訊息 in 結果.訊息清單)
    assert 結果.訊息清單[-1]["content"] == "最新問題"
    assert 結果.壓縮後Token數 < 結果.壓縮前Token數


def test_provider_usage_drives_compression_even_when_rough_estimate_low():
    """確認 post-response usage 使用 provider prompt token，不再只看 rough estimate。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=10000, 觸發比例=0.5, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": f"訊息 {i}"} for i in range(12)]
    assert 粗估訊息Token數(訊息清單) < 壓縮器.門檻Token數
    結果 = 壓縮器.壓縮訊息(訊息清單, provider提示Token數=9000)
    assert 結果.是否已壓縮 is True


def _是否有相鄰同角色(訊息清單: list[dict[str, Any]]) -> bool:
    """檢查 user/assistant 是否連續兩則同 role（tool 訊息不計入）。

    參數：
        訊息清單: 測試用輸入或 fixture；用於建立此測試案例需要的資料或暫存路徑。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    上一角色: str | None = None
    for 訊息 in 訊息清單:
        角色 = 訊息.get("role")
        if 角色 not in {"user", "assistant"}:
            continue
        if 上一角色 == 角色:
            return True
        上一角色 = 角色
    return False


def test_summary_role_defaults_to_assistant_between_users():
    """user → summary → user 時，summary 應選 assistant 維持交替。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器()
    摘要 = 壓縮器.建立摘要訊息(
        [{"role": "user", "content": "開頭"}],
        [{"role": "user", "content": "尾端"}],
        "summary text",
    )
    assert 摘要["role"] == "assistant"
    assert 摘要["_compressed_summary"] is True


def test_summary_role_becomes_user_when_sandwiched_by_assistant():
    """assistant → summary → assistant 時，summary 應選 user 避免相鄰 assistant。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器()
    摘要 = 壓縮器.建立摘要訊息(
        [{"role": "assistant", "content": "前段"}],
        [{"role": "assistant", "content": "尾段"}],
        "summary text",
    )
    assert 摘要["role"] == "user"


def test_summary_role_becomes_user_when_head_is_assistant_and_no_tail():
    """開頭以 assistant 結尾且無尾端時，summary 應選 user。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器()
    摘要 = 壓縮器.建立摘要訊息(
        [{"role": "assistant", "content": "前段"}],
        [],
        "summary text",
    )
    assert 摘要["role"] == "user"


def test_merge_summary_into_tail_when_both_assistant():
    """summary 與 tail 同為 assistant 時，應 merge 到 tail message。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器()
    訊息清單 = [
        {"role": "user", "content": "head"},
        {"role": "assistant", "content": "summary body", "_compressed_summary": True},
        {"role": "assistant", "content": "tail reply"},
    ]
    結果 = 壓縮器.合併相鄰同角色摘要(訊息清單)
    assert len(結果) == 2
    assert 結果[-1]["role"] == "assistant"
    assert 結果[-1].get("_compressed_summary") is not True
    assert 結果[-1]["_contains_compressed_summary"] is True
    assert "[Most recent preserved message]" in 結果[-1]["content"]
    assert "summary body" in 結果[-1]["content"]
    assert "tail reply" in 結果[-1]["content"]


def test_merge_adjacent_summaries_with_same_role():
    """連續兩則同 role 的 summary 應合併為一則。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器()
    訊息清單 = [
        {"role": "user", "content": "head"},
        {"role": "assistant", "content": "first summary", "_compressed_summary": True},
        {"role": "assistant", "content": "second summary", "_compressed_summary": True},
        {"role": "user", "content": "tail"},
    ]
    結果 = 壓縮器.合併相鄰同角色摘要(訊息清單)
    assert len(結果) == 3
    摘要訊息 = 結果[1]
    assert 摘要訊息["_compressed_summary"] is True
    assert "first summary" in 摘要訊息["content"]
    assert "second summary" in 摘要訊息["content"]


def test_compression_maintains_user_assistant_alternation():
    """壓縮後 user/assistant 不應連續同 role；summary 應動態選 role 或 merge 到 tail。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": "開頭"}]
    for i in range(30):
        訊息清單.append({"role": "assistant", "content": f"中段{i}" + "x" * 1200})
    訊息清單.append({"role": "user", "content": "最新問題"})
    結果 = 壓縮器.壓縮訊息(訊息清單, 強制=True)
    assert 結果.是否已壓縮 is True
    assert not _是否有相鄰同角色(結果.訊息清單)
    assert any(是否摘要訊息(訊息) for 訊息 in 結果.訊息清單)
    assert 結果.訊息清單[0]["role"] == "user"
    assert 結果.訊息清單[-1]["content"] == "最新問題"


def test_tool_result_pruning_and_tool_pair_sanitize():
    """確認舊大型 tool result 會被修剪，orphan tool result 會被移除。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [
        {"role": "user", "content": "開始"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_old", "type": "function", "function": {"name": "terminal", "arguments": '{"command": "npm test"}'}}]},
        {"role": "tool", "tool_call_id": "call_old", "name": "terminal", "content": "x" * 5000},
        {"role": "tool", "tool_call_id": "orphan", "name": "read_file", "content": "孤兒結果"},
        *({"role": "user", "content": "中段" + str(i) + "y" * 1000} for i in range(20)),
        {"role": "user", "content": "最新"},
    ]
    結果 = 壓縮器.壓縮訊息(訊息清單, 強制=True)
    文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 結果.訊息清單)
    assert "[terminal]" in 文字
    assert all(訊息.get("tool_call_id") != "orphan" for 訊息 in 結果.訊息清單)


def test_tool_pair_sanitize_drops_interrupted_sequence():
    """tool_call 與 tool result 中間插入其他訊息時，應移除整組 broken pair。

    參數：
        無。此測試自行建立需要的壓縮器與訊息清單。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器()
    訊息清單 = [
        {"role": "user", "content": "開始"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}]},
        {"role": "user", "content": "使用者介入"},
        {"role": "tool", "tool_call_id": "call1", "name": "terminal", "content": "結果"},
    ]
    結果 = 壓縮器.清理工具配對(訊息清單)
    assert len(結果) == 2
    assert 結果[0]["content"] == "開始"
    assert 結果[1]["content"] == "使用者介入"
    assert all(訊息.get("role") != "tool" for 訊息 in 結果)
    assert all(not 訊息.get("tool_calls") for 訊息 in 結果)


def test_tool_result_dedupe_keeps_newest_copy():
    """相同內容的 tool result 應 dedupe 較舊版本，保留最新完整版。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=3)
    相同內容 = "line\n" * 300
    訊息清單 = [
        {"role": "user", "content": "開始"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_old", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "call_old", "name": "read_file", "content": 相同內容},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_new", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "call_new", "name": "read_file", "content": 相同內容},
        {"role": "user", "content": "最新"},
    ]
    修剪後 = 壓縮器.修剪舊工具結果(訊息清單)
    工具內容 = [訊息["content"] for 訊息 in 修剪後 if 訊息.get("role") == "tool"]
    assert 工具內容.count(相同內容) == 1
    assert any("Duplicate tool output" in str(內容) for 內容 in 工具內容)


def test_read_file_pruning_uses_informative_summary():
    """舊 read_file 結果應變成含 path 的一行摘要。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [
        {"role": "user", "content": "開始"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "config.py", "offset": 10}'}}]},
        {"role": "tool", "tool_call_id": "call1", "name": "read_file", "content": "x" * 3000},
        *({"role": "user", "content": f"填充 {i}" + "y" * 1200} for i in range(20)),
        {"role": "user", "content": "尾端"},
    ]
    修剪後 = 壓縮器.修剪舊工具結果(訊息清單)
    舊結果 = 修剪後[2]["content"]
    assert "[read_file] read config.py from line 10" in 舊結果


def test_tool_call_arguments_truncated_as_valid_json():
    """過長 write_file arguments 應 JSON-safe 截斷，而非硬切 raw string。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    大內容 = "a" * 800
    參數 = json.dumps({"path": "big.txt", "content": 大內容}, ensure_ascii=False)
    訊息清單 = [
        {"role": "user", "content": "開始"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "write_file", "arguments": 參數}}]},
        {"role": "tool", "tool_call_id": "call1", "name": "write_file", "content": "ok"},
        *({"role": "user", "content": f"填充 {i}" + "z" * 1200} for i in range(20)),
        {"role": "user", "content": "尾端"},
    ]
    修剪後 = 壓縮器.修剪舊工具結果(訊息清單)
    新參數 = 修剪後[1]["tool_calls"][0]["function"]["arguments"]
    解析 = json.loads(新參數)
    assert 解析["path"] == "big.txt"
    assert len(解析["content"]) < len(大內容)
    assert 解析["content"].endswith("...[truncated]")


def test_iterative_summary_update_uses_existing_summary():
    """確認再次壓縮會納入既有 summary，而非把 prefix 重複堆疊。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2)
    訊息清單 = [{"role": "user", "content": "開頭"}] + [{"role": "assistant", "content": "第一輪" + "x" * 1000} for _ in range(20)] + [{"role": "user", "content": "尾端"}]
    第一次 = 壓縮器.壓縮訊息(訊息清單, 強制=True)
    第二次訊息 = [*第一次.訊息清單, *({"role": "assistant", "content": "第二輪" + "y" * 1000} for _ in range(16)), {"role": "user", "content": "新問題"}]
    第二次 = 壓縮器.壓縮訊息(第二次訊息, 強制=True)
    摘要文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 第二次.訊息清單 if 是否摘要訊息(訊息))
    assert 摘要文字.count("[CONTEXT COMPACTION") == 1
    assert "Previous summary retained" in 摘要文字 or "第一輪" in 摘要文字


def test_auxiliary_llm_summary_used_when_injected():
    """確認注入 摘要函式 時走 LLM 主路徑，而非 deterministic fallback。

    參數：
        無。此測試自行建立需要的壓縮器、訊息清單與假 provider。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    呼叫次數 = {"n": 0}

    def 摘要函式(摘要輸入: str, 目標Token: int) -> str:
        """回傳測試用 auxiliary summary 文字。

        參數：
            摘要輸入: 壓縮器產生的 summary prompt，測試會確認其中包含必要提示詞。
            目標Token: 壓縮器傳入的 summary 目標長度；此測試只確認函式被呼叫。

        返回值：
            str：符合 structured summary 章節格式的假摘要內容，用於驗證壓縮器會
            使用注入的 LLM 摘要路徑。
        """
        呼叫次數["n"] += 1
        assert "summarization agent" in 摘要輸入
        assert "Historical Task Snapshot" in 摘要輸入
        return (
            "## Historical Task Snapshot\n"
            "- User asked to refactor auth module\n"
            "## Historical In-Progress State\n"
            "- reviewing middleware.py\n"
            "## Historical Pending User Asks\n"
            "- None.\n"
            "## Historical Remaining Work\n"
            "- finish JWT migration\n"
            "## Relevant Files\n"
            "- middleware.py\n"
            "## Resolved Questions\n"
            "- None.\n"
            "## Key Decisions\n"
            "- adopt JWT\n"
            "## Blocked\n"
            "- None."
        )

    壓縮器 = 上下文壓縮器(上下文長度=8192, 保留開頭數=1, 保留尾端數=2, 摘要函式=摘要函式)
    訊息清單 = [{"role": "user", "content": "開頭"}] + [{"role": "assistant", "content": "中段" + str(i) + "x" * 1200} for i in range(20)] + [{"role": "user", "content": "最新"}]
    結果 = 壓縮器.壓縮訊息(訊息清單, 強制=True)
    摘要文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 結果.訊息清單 if 是否摘要訊息(訊息))
    assert 呼叫次數["n"] == 1
    assert "refactor auth module" in 摘要文字
    assert "Previous summary retained" not in 摘要文字


class 動態README假模型供應商:
    """以目前 checkout 的 README 路徑觸發 read_file 的測試 provider。"""

    def 產生回應(self, 訊息清單, 工具清單):
        """第一次要求 read_file；收到 tool result 後回傳最終回答。"""
        if 訊息清單 and 訊息清單[-1].get("role") == "tool":
            return 模型回應(文字=f"已完成工具 roundtrip；工具結果摘要：{str(訊息清單[-1].get('content', ''))[:200]}")
        return 模型回應(
            工具呼叫清單=[{
                "id": "call_readme",
                "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": str(專案根目錄 / "README.md"), "limit": 20}, ensure_ascii=False)},
            }],
            完成原因="tool_calls",
        )


def test_tool_call_loop_roundtrip(tmp_path):
    """確認 fake model 會觸發真實 read_file 工具並回到模型產生最終回答。

    參數：
        tmp_path: 測試用輸入或 fixture；用於建立此測試案例需要的資料或暫存路徑。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    runtime = 代理執行階段(
        庫,
        動態README假模型供應商(),
        模型名稱="fake",
        供應商名稱="fake",
        工作目錄=str(專案根目錄),
        啟用壓縮摘要=False,
    )
    結果 = runtime.執行使用者訊息("請讀取 README 並回答", 工作階段識別碼="tool-loop")
    assert 結果.工具呼叫次數 == 1
    assert 結果.模型呼叫次數 == 2
    assert "工具 roundtrip" in 結果.最終回答
    assert any(訊息.get("role") == "tool" and "# testagent2" in 訊息.get("content", "") for 訊息 in 結果.訊息清單)


class 壓縮摘要假模型供應商:
    """區分 compression summary 與主模型呼叫。"""

    def __init__(self) -> None:
        """初始化假 provider 的呼叫計數器。

        參數：
            無。此假物件不需要外部設定。

        返回值：
            None。初始化後會建立 `壓縮呼叫次數` 與 `主模型呼叫次數` 兩個計數欄位。
        """
        self.壓縮呼叫次數 = 0
        self.主模型呼叫次數 = 0

    def 產生回應(self, 訊息清單, 工具清單):
        """依 prompt 類型回傳 compression summary 或主模型回答。

        參數：
            訊息清單: runtime 傳入的 request messages；若內容包含 summarization
                agent prompt，視為 compression summary 呼叫。
            工具清單: provider tool schemas；此假 provider 不使用，但保留相同介面。

        返回值：
            模型回應：summary 呼叫時回傳 structured summary；主模型呼叫時回傳
            `完成` 並帶高 prompt_token_count 以觸發 post-response compression。
        """
        文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 訊息清單)
        if "summarization agent" in 文字:
            self.壓縮呼叫次數 += 1
            return 模型回應(
                文字=(
                    "## Historical Task Snapshot\n- captured by auxiliary llm\n"
                    "## Historical In-Progress State\n- active\n"
                    "## Historical Pending User Asks\n- None.\n"
                    "## Historical Remaining Work\n- stale work\n"
                    "## Relevant Files\n- README.md\n"
                    "## Resolved Questions\n- None.\n"
                    "## Key Decisions\n- use sqlite\n"
                    "## Blocked\n- None."
                )
            )
        self.主模型呼叫次數 += 1
        return 模型回應(文字="完成", 使用量={"prompt_token_count": 9000})


def test_runtime_wires_auxiliary_compression_summary(tmp_path):
    """確認 runtime 預設會用 auxiliary LLM 產生 structured summary。

    參數：
        tmp_path: 測試用輸入或 fixture；用於建立此測試案例需要的資料或暫存路徑。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    舊id = 庫.建立或讀取工作階段("aux-s")
    歷史 = [{"role": "user", "content": f"歷史 {i}"} for i in range(36)]
    庫.寫入訊息清單(舊id, 歷史)
    供應商 = 壓縮摘要假模型供應商()
    runtime = 代理執行階段(
        庫,
        供應商,
        模型名稱="fake",
        供應商名稱="fake",
        工作目錄=str(專案根目錄),
        上下文長度=10000,
        模型模式="fake",
        啟用壓縮摘要=True,
    )
    結果 = runtime.執行使用者訊息("最新", 工作階段識別碼=舊id)
    assert 結果.是否已壓縮 is True
    assert 供應商.壓縮呼叫次數 >= 1
    摘要文字 = "\n".join(str(訊息.get("content", "")) for 訊息 in 結果.訊息清單 if 是否摘要訊息(訊息))
    assert "captured by auxiliary llm" in 摘要文字


class 使用量假模型供應商:
    """回傳高 provider usage 以觸發 post-response compression split。"""

    def 產生回應(self, 訊息清單, 工具清單):
        """回傳含 prompt_token_count 的最終回答。

        參數：
            訊息清單: 測試用輸入或 fixture；用於建立此測試案例需要的資料或暫存路徑。
            工具清單: 測試用輸入或 fixture；用於建立此測試案例需要的資料或暫存路徑。

        返回值：
            None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
        """
        return 模型回應(文字="完成", 使用量={"prompt_token_count": 9000})


def test_runtime_provider_usage_creates_compression_split(tmp_path):
    """確認 runtime 使用 provider usage 觸發壓縮並建立新 session。

    參數：
        tmp_path: 測試用輸入或 fixture；用於建立此測試案例需要的資料或暫存路徑。

    返回值：
        None。此測試透過 assert 驗證預期行為；失敗時由 pytest 回報 assertion error。
    """
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    舊id = 庫.建立或讀取工作階段("usage-s")
    歷史 = [{"role": "user", "content": f"歷史 {i}"} for i in range(36)]
    庫.寫入訊息清單(舊id, 歷史)
    runtime = 代理執行階段(
        庫,
        使用量假模型供應商(),
        模型名稱="fake",
        供應商名稱="fake",
        工作目錄=str(專案根目錄),
        上下文長度=10000,
        啟用壓縮摘要=False,
    )
    結果 = runtime.執行使用者訊息("最新", 工作階段識別碼=舊id)
    assert 結果.是否已壓縮 is True
    assert 結果.工作階段識別碼 != 舊id
    assert 庫.讀取工作階段(舊id)["end_reason"] == "compression"
    目前 = 庫.讀取工作階段(結果.工作階段識別碼)
    父鏈 = []
    while 目前 and 目前.get("parent_session_id"):
        父鏈.append(目前["parent_session_id"])
        目前 = 庫.讀取工作階段(目前["parent_session_id"])
    assert 舊id in 父鏈
