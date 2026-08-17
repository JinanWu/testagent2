"""待辦清單工具（todo）。

功能：
    提供 `todo` 的 handler。這是模型給自己用的工作記憶：多步驟任務先列出步驟，
    每完成一步就更新狀態，避免長對話中途遺漏未完成項目。使用者不會看到這份清
    單，它只回給模型。

存放位置：
    `~/.testagent2/todos/<清單鍵>.json`，一個工作階段一個檔。刻意不進 session
    DB —— 清單是任務進行中的暫存狀態，不需要跟訊息一起備份，也不值得為它同時
    修改 SQLite 與 BigQuery 兩個工作階段庫。

清單鍵：
    使用 compression lineage 的 **root** session id，而非當下的 session id。
    runtime 在壓縮時會分裂出新 session（見 `代理執行階段.嘗試壓縮並分裂工作階
    段`），用當下 id 當鍵會讓清單在壓縮後憑空消失。因此同一條 lineage 共用一份
    清單；換成另一個 session 則是全新的空白清單。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

合法狀態 = ("pending", "in_progress", "completed", "cancelled")
進行中狀態 = "in_progress"
待處理狀態 = "pending"
最大項目數 = 50
最大識別碼長度 = 64
最大內容長度 = 500
無工作階段鍵 = "無工作階段"


def 待辦根目錄() -> Path:
    """取得待辦清單檔的根目錄。

    參數：無。
    返回值：Path；目錄可能尚未建立。
    """
    家目錄 = os.getenv("TESTAGENT2_HOME", "").strip()
    根 = Path(家目錄).expanduser() if 家目錄 else Path.home() / ".testagent2"
    return 根 / "todos"


def 解析清單鍵(參數: dict[str, Any]) -> str:
    """決定這次讀寫要用哪一份清單。

    以 lineage root session id 為鍵，讓清單能跨壓縮分裂存活。取不到工作階段庫
    時退回當下的 session id：清單仍可用，只是壓縮後會換一份。

    參數：
        參數: 工具呼叫參數，其中 `_current_user_id` 由登錄器注入。
    返回值：str，可安全用作檔名的清單鍵。
    """
    from ..工作階段上下文 import 讀取目前工作階段識別碼, 讀取目前工作階段資料庫路徑

    工作階段識別碼 = (讀取目前工作階段識別碼() or "").strip()
    if not 工作階段識別碼:
        return 無工作階段鍵
    使用者識別碼 = 參數.get("_current_user_id")
    try:
        from ..儲存 import 建立工作階段庫

        資料庫路徑 = 讀取目前工作階段資料庫路徑() or str(Path.home() / ".testagent2" / "sessions.sqlite3")
        庫 = 建立工作階段庫(資料庫路徑)
        庫.檢查工作階段存取(工作階段識別碼, user_id=使用者識別碼)
        譜系 = 庫.取得工作階段譜系(工作階段識別碼)
    except PermissionError:
        # 跨使用者存取一律拒絕，不退回較寬鬆的鍵。
        raise
    except Exception as 錯誤:
        logger.debug("待辦清單無法解析 lineage root，退回當下 session：%s", 錯誤)
        return 工作階段識別碼
    return (譜系[0] if 譜系 else 工作階段識別碼)


def 清單檔路徑(清單鍵: str) -> Path:
    """把清單鍵轉成實際檔案路徑。

    參數：
        清單鍵: 由 `解析清單鍵` 取得的鍵。
    返回值：Path；鍵含路徑分隔字元時丟出 ValueError。
    """
    安全鍵 = 清單鍵.strip()
    if not 安全鍵 or "/" in 安全鍵 or "\\" in 安全鍵 or 安全鍵.startswith("."):
        raise ValueError(f"不合法的待辦清單鍵：{清單鍵!r}")
    return 待辦根目錄() / f"{安全鍵}.json"


def 讀取清單(清單鍵: str) -> list[dict[str, Any]]:
    """讀取已保存的清單。

    參數：
        清單鍵: 清單識別鍵。
    返回值：項目清單；檔案不存在或內容毀損時回傳空清單。
    """
    路徑 = 清單檔路徑(清單鍵)
    if not 路徑.exists():
        return []
    try:
        資料 = json.loads(路徑.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as 錯誤:
        logger.warning("待辦清單檔毀損，視為空清單：%s（%s）", 路徑, 錯誤)
        return []
    if type(資料) is not list:
        return []
    清單: list[dict[str, Any]] = []
    for 原始項目 in 資料:
        if type(原始項目) is dict and 原始項目.get("status") in 合法狀態:
            清單.append({
                "id": str(原始項目.get("id") or ""),
                "content": str(原始項目.get("content") or ""),
                "status": str(原始項目["status"]),
            })
    return 清單


def 寫入清單(清單鍵: str, 清單: list[dict[str, Any]]) -> None:
    """以原子寫入保存清單。

    參數：
        清單鍵: 清單識別鍵。
        清單: 已驗證的項目清單。
    返回值：None。
    """
    路徑 = 清單檔路徑(清單鍵)
    路徑.parent.mkdir(parents=True, exist_ok=True)
    暫存路徑 = 路徑.with_suffix(".json.tmp")
    暫存路徑.write_text(json.dumps(清單, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(暫存路徑, 路徑)


def 取出識別碼(原始項目: dict[str, Any]) -> str:
    """取出並修剪項目 id。

    參數：
        原始項目: 模型提供的項目。
    返回值：str；缺少或過長時回傳空字串代表不可用。
    """
    識別碼 = str(原始項目.get("id") or "").strip()
    return 識別碼 if 0 < len(識別碼) <= 最大識別碼長度 else ""


def 正規化狀態(原始狀態: Any, 識別碼: str, 警告清單: list[str]) -> str:
    """把狀態正規化成合法值。

    參數：
        原始狀態: 模型提供的狀態。
        識別碼: 用於警告訊息的項目 id。
        警告清單: 就地累積的警告。
    返回值：合法狀態字串；不合法時退回 pending 並記警告。
    """
    狀態 = str(原始狀態 or "").strip().lower()
    if 狀態 in 合法狀態:
        return 狀態
    警告清單.append(
        f"項目「{識別碼}」的 status {原始狀態!r} 不合法，已當作 {待處理狀態} 處理"
        f"（合法值：{'、'.join(合法狀態)}）"
    )
    return 待處理狀態


def 正規化內容(原始內容: Any, 識別碼: str, 警告清單: list[str]) -> str:
    """把內容正規化並套用長度上限。

    參數：
        原始內容: 模型提供的內容。
        識別碼: 用於警告訊息的項目 id。
        警告清單: 就地累積的警告。
    返回值：修剪後的內容；過長時截斷並記警告。
    """
    內容 = str(原始內容 or "").strip()
    if len(內容) > 最大內容長度:
        警告清單.append(f"項目「{識別碼}」的 content 超過 {最大內容長度} 字元，已截斷")
        return 內容[:最大內容長度]
    return 內容


def 建立新項目(原始項目: dict[str, Any], 識別碼: str, 警告清單: list[str]) -> dict[str, Any]:
    """把模型提供的資料建成一筆完整項目。

    新項目必須有內容才有意義；缺少時補上佔位字串而非拒絕整批，理由見
    `合併或取代`。

    參數：
        原始項目: 模型提供的項目。
        識別碼: 已驗證的項目 id。
        警告清單: 就地累積的警告。
    返回值：完整項目 dict。
    """
    內容 = 正規化內容(原始項目.get("content"), 識別碼, 警告清單)
    if not 內容:
        警告清單.append(f"項目「{識別碼}」沒有 content，已填入佔位文字")
        內容 = "（未填寫內容）"
    return {
        "id": 識別碼,
        "content": 內容,
        "status": 正規化狀態(原始項目.get("status"), 識別碼, 警告清單),
    }


def 套用部分更新(既有項目: dict[str, Any], 原始項目: dict[str, Any], 警告清單: list[str]) -> dict[str, Any]:
    """merge 模式下只覆蓋模型實際提供的欄位。

    這是與 Hermes 對齊的行為：模型回報進度時可以只送 `{id, status}`，不必重打
    content，省 token 也少一個打錯字的機會。

    參數：
        既有項目: 目前保存的項目。
        原始項目: 模型提供的部分欄位。
        警告清單: 就地累積的警告。
    返回值：更新後的項目 dict。
    """
    更新後 = dict(既有項目)
    識別碼 = 更新後["id"]
    if str(原始項目.get("content") or "").strip():
        更新後["content"] = 正規化內容(原始項目.get("content"), 識別碼, 警告清單)
    if 原始項目.get("status") is not None:
        更新後["status"] = 正規化狀態(原始項目.get("status"), 識別碼, 警告清單)
    return 更新後


def 合併或取代(
    既有清單: list[dict[str, Any]],
    原始清單: Any,
    是否合併: bool,
    警告清單: list[str],
) -> list[dict[str, Any]]:
    """把模型送來的項目套用到清單上。

    驗證採「修正並回報」而非「拒絕」：`工具結果` 契約層會把 handler 的錯誤訊息
    換成固定文字，直接拒絕的話模型只會收到不帶原因的失敗、無從修正。因此僅對
    結構性錯誤（不是陣列、項目不是物件、缺可用 id、超出數量上限）才拋錯，其餘
    一律修正後寫進 warnings 讓模型看得到。

    參數：
        既有清單: 目前保存的清單。
        原始清單: 工具參數中的 todos。
        是否合併: True 依 id 更新，False 整份取代。
        警告清單: 就地累積的警告。
    返回值：套用後的清單。
    """
    if type(原始清單) is not list:
        raise ValueError("todos 必須是陣列")
    if len(原始清單) > 最大項目數:
        raise ValueError(f"todos 一次最多 {最大項目數} 筆")

    基底 = [dict(項目) for 項目 in 既有清單] if 是否合併 else []
    位置表 = {項目["id"]: 位置 for 位置, 項目 in enumerate(基底)}
    for 原始項目 in 原始清單:
        if type(原始項目) is not dict:
            raise ValueError("todos 的每個項目都必須是物件")
        識別碼 = 取出識別碼(原始項目)
        if not 識別碼:
            raise ValueError(f"每個項目都需要非空、不超過 {最大識別碼長度} 字元的 id")
        位置 = 位置表.get(識別碼)
        if 位置 is None:
            位置表[識別碼] = len(基底)
            基底.append(建立新項目(原始項目, 識別碼, 警告清單))
        elif 是否合併:
            基底[位置] = 套用部分更新(基底[位置], 原始項目, 警告清單)
        else:
            # 同一批出現重複 id：後者覆蓋前者並保留原位置，比整批拒絕實用。
            警告清單.append(f"同一次寫入出現重複 id「{識別碼}」，以最後一筆為準")
            基底[位置] = 建立新項目(原始項目, 識別碼, 警告清單)
    return 基底


def 收斂進行中項目(清單: list[dict[str, Any]]) -> list[str]:
    """確保最多只有一個 in_progress，多餘的降回 pending。

    schema 規定同時只能有一個項目進行中。這裡選擇自動收斂而非直接拒絕：工具結
    果契約層會把 handler 的錯誤字串換成固定訊息，拒絕的話模型只會看到不帶原因
    的失敗；改成修正後回報，模型能從回傳的清單與 warnings 看出實際發生什麼事。

    參數：
        清單: 待寫入的清單（就地修改）。
    返回值：警告訊息清單；未違反時為空。
    """
    進行中位置 = [位置 for 位置, 項目 in enumerate(清單) if 項目["status"] == 進行中狀態]
    if len(進行中位置) <= 1:
        return []
    保留位置 = 進行中位置[-1]
    被降級 = []
    for 位置 in 進行中位置[:-1]:
        清單[位置]["status"] = 待處理狀態
        被降級.append(清單[位置]["content"])
    return [
        f"同時只能有一個項目 in_progress，已保留「{清單[保留位置]['content']}」，"
        f"其餘 {len(被降級)} 筆改回 pending：{'、'.join(被降級)}"
    ]


def 建立回傳(清單: list[dict[str, Any]], 警告清單: list[str]) -> dict[str, Any]:
    """組出回給模型的結果。

    參數：
        清單: 目前完整清單。
        警告清單: 本次自動修正的說明。
    返回值：dict，含 todos、total_count、statistics，必要時含 warnings。
    """
    統計 = {狀態: 0 for 狀態 in 合法狀態}
    for 項目 in 清單:
        統計[項目["status"]] += 1
    結果: dict[str, Any] = {
        "todos": 清單,
        "total_count": len(清單),
        "statistics": 統計,
    }
    if 警告清單:
        結果["warnings"] = 警告清單
    return 結果


def 待辦清單(參數: dict[str, Any]) -> dict[str, Any]:
    """執行 `todo`：讀取或寫入目前工作階段的待辦清單。

    參數：
        參數: 工具呼叫參數。省略 todos 表示只讀；merge=True 時依 id 更新，
            否則整份取代。
    返回值：dict，永遠包含目前完整清單。
    """
    清單鍵 = 解析清單鍵(參數)
    既有清單 = 讀取清單(清單鍵)
    原始清單 = 參數.get("todos")
    if 原始清單 is None:
        return 建立回傳(既有清單, [])
    警告清單: list[str] = []
    清單 = 合併或取代(既有清單, 原始清單, bool(參數.get("merge")), 警告清單)
    if len(清單) > 最大項目數:
        raise ValueError(f"合併後超過 {最大項目數} 筆上限")
    警告清單.extend(收斂進行中項目(清單))
    寫入清單(清單鍵, 清單)
    return 建立回傳(清單, 警告清單)
