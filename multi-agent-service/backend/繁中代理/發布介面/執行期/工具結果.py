"""Published Runtime canonical 單層工具執行結果。"""

from __future__ import annotations

from enum import Enum
import json
import math
from typing import Any, Callable


class 工具錯誤代碼(str, Enum):
    """R92 terminal tool outcome taxonomy。"""

    執行失敗 = "tool_execution_failed"
    逾時 = "tool_timeout"
    端點設定錯誤 = "endpoint_misconfigured"


class 工具逾時(Exception):
    """只有此精確 signal 代表工具 timeout。"""


class 工具設定錯誤(Exception):
    """只有此精確 signal 代表 endpoint/platform 設定錯誤。"""


class 可恢復工具錯誤(Exception):
    """代表本次失敗可在後續編排中作為 warning 繼續。"""


控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
執行失敗訊息 = "工具執行失敗"
逾時訊息 = "工具執行逾時"
設定錯誤訊息 = "工具端點設定錯誤"
權限錯誤訊息 = "使用者無權使用工具或資源"
路徑權限錯誤訊息 = "使用者無權使用工具或資源；工具路徑超出允許範圍"
未知工具訊息 = "未知工具"
允許錯誤訊息 = frozenset((
    執行失敗訊息, 逾時訊息, 設定錯誤訊息, 權限錯誤訊息,
    路徑權限錯誤訊息, 未知工具訊息,
))
錯誤預設訊息 = {
    工具錯誤代碼.執行失敗: 執行失敗訊息,
    工具錯誤代碼.逾時: 逾時訊息,
    工具錯誤代碼.端點設定錯誤: 設定錯誤訊息,
}


class 工具執行結果:
    """不可變、可重建且 success/error 互斥的 canonical DTO。"""

    __slots__ = ("success", "_result_json", "error_code", "error_message", "recoverable", "permission_denied")

    def __init__(
        self, success: bool, result: Any = None,
        error_code: 工具錯誤代碼 | None = None, error_message: str | None = None,
        recoverable: bool = False, permission_denied: bool = False,
    ) -> None:
        """驗證互斥狀態，成功結果只以 immutable canonical JSON 文字封存。"""
        空值: Any = None
        失敗 = False
        成功, 結果, 代碼, 訊息 = success, result, error_code, error_message
        可恢復, 權限, 內部文字 = recoverable, permission_denied, None
        try:
            if type(成功) is not bool or type(可恢復) is not bool or type(權限) is not bool:
                raise ValueError
            if 成功:
                if 代碼 is not None or 訊息 is not None or 可恢復 or 權限:
                    raise ValueError
                內部文字 = _正規化結果文字(_複製JSON(結果))
            else:
                if 結果 is not None or type(代碼) is not 工具錯誤代碼:
                    raise ValueError
                if type(訊息) is not str or 訊息 not in 允許錯誤訊息:
                    raise ValueError
            object.__setattr__(self, "success", 成功)
            object.__setattr__(self, "_result_json", 內部文字)
            object.__setattr__(self, "error_code", 代碼)
            object.__setattr__(self, "error_message", 訊息)
            object.__setattr__(self, "recoverable", 可恢復)
            object.__setattr__(self, "permission_denied", 權限)
        except BaseException as 錯誤:
            類別 = type(錯誤)
            self = success = result = error_code = error_message = recoverable = permission_denied = 空值
            成功 = 結果 = 代碼 = 訊息 = 可恢復 = 權限 = 內部文字 = 空值
            if isinstance(錯誤, 控制流程):
                類別 = 錯誤 = 空值
                raise
            失敗 = True
        if 失敗:
            self = success = result = error_code = error_message = recoverable = permission_denied = 空值
            成功 = 結果 = 代碼 = 訊息 = 可恢復 = 權限 = 內部文字 = 空值
            raise ValueError("工具結果狀態無效")

    def __setattr__(self, 名稱: str, 值: Any) -> None:
        """拒絕一般欄位變更；偽造 slot 仍由重建邊界 fail closed。"""
        raise AttributeError("工具結果不可變")

    @property
    def result(self) -> Any:
        """成功時每次 strict decode immutable slot，回傳 fresh JSON tree。"""
        空值: Any = None
        文字 = None
        try:
            文字 = object.__getattribute__(self, "_result_json")
            if 文字 is None:
                return None
            結果 = _驗證內部結果(文字)
            self = 文字 = 空值
            return 結果
        except BaseException as 錯誤:
            類別 = type(錯誤)
            self = 文字 = 結果 = 空值
            if isinstance(錯誤, 控制流程):
                類別 = 錯誤 = 空值
                raise
            return None

    def __repr__(self) -> str:
        """不顯示 result 或可偽造欄位內容。"""
        空值: Any = None
        try:
            成功 = object.__getattribute__(self, "success") is True
        except BaseException as 錯誤:
            類別 = type(錯誤)
            self = 空值
            if isinstance(錯誤, 控制流程):
                類別 = 錯誤 = 空值
                raise
            成功 = False
        return f"工具執行結果(success={成功})"

    def 轉成JSON物件(self) -> dict[str, Any]:
        """完整重建 DTO；偽造狀態固定成 endpoint_misconfigured。"""
        空值: Any = None
        結果 = 物件 = None
        try:
            結果 = _重建結果(self)
            self = 空值
            if object.__getattribute__(結果, "success"):
                物件 = {"success": True, "result": object.__getattribute__(結果, "result")}
            else:
                物件 = {
                    "success": False,
                    "error": object.__getattribute__(結果, "error_message"),
                    "code": object.__getattribute__(結果, "error_code").value,
                    "recoverable": object.__getattribute__(結果, "recoverable"),
                }
                if object.__getattribute__(結果, "permission_denied"):
                    物件["permission_denied"] = True
            結果 = 空值
            return 物件
        except BaseException as 錯誤:
            類別 = type(錯誤)
            self = 結果 = 物件 = 空值
            if isinstance(錯誤, 控制流程):
                類別 = 錯誤 = 空值
                raise
            return {
                "success": False, "error": 設定錯誤訊息,
                "code": 工具錯誤代碼.端點設定錯誤.value, "recoverable": False,
            }

    def 轉成正規JSON(self) -> str:
        """回傳 deterministic UTF-8 canonical JSON；序列化失敗固定關閉。"""
        空值: Any = None
        物件 = 結果 = None
        try:
            物件 = self.轉成JSON物件()
            self = 空值
            結果 = json.dumps(
                物件, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
            物件 = 空值
            return 結果
        except BaseException as 錯誤:
            類別 = type(錯誤)
            self = 物件 = 結果 = 空值
            if isinstance(錯誤, 控制流程):
                類別 = 錯誤 = 空值
                raise
            return '{"code":"endpoint_misconfigured","error":"工具端點設定錯誤","recoverable":false,"success":false}'


def 建立固定工具失敗(
    代碼: 工具錯誤代碼, *, permission_denied: bool = False,
    error_message: str | None = None,
) -> 工具執行結果:
    """供 registry 建立僅含 module allowlist 訊息的安全失敗。"""
    結果 = None
    try:
        結果 = _失敗(代碼, False, permission_denied, error_message)
        代碼 = permission_denied = error_message = None
        return 結果
    except BaseException as 錯誤:
        類別 = type(錯誤)
        代碼 = permission_denied = error_message = 結果 = None
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        raise


def 呼叫工具處理函數(handler: Callable[[dict[str, Any]], Any], args: dict[str, Any]) -> 工具執行結果:
    """以 detached 參數恰呼叫 handler 一次，並固定分類所有失敗。"""
    已捕捉處理函數: Any = handler
    已捕捉參數: Any = args
    獨立參數 = None
    回傳值 = None
    try:
        if not callable(已捕捉處理函數) or type(已捕捉參數) is not dict:
            raise ValueError
        獨立參數 = _複製JSON(已捕捉參數)
    except BaseException as 錯誤:
        類別 = type(錯誤)
        已捕捉處理函數 = 已捕捉參數 = 獨立參數 = None
        del handler, args
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        return _失敗(工具錯誤代碼.端點設定錯誤)
    已捕捉參數 = None
    del handler, args
    try:
        回傳值 = 已捕捉處理函數(獨立參數)
        已捕捉處理函數 = 獨立參數 = None
        結果 = _正規化回傳(回傳值)
        回傳值 = None
        return 結果
    except BaseException as 錯誤:
        類別 = type(錯誤)
        已捕捉處理函數 = 獨立參數 = 回傳值 = 結果 = None
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        if 類別 is 工具逾時:
            return _失敗(工具錯誤代碼.逾時)
        if 類別 is 工具設定錯誤:
            return _失敗(工具錯誤代碼.端點設定錯誤)
        if 類別 is 可恢復工具錯誤:
            return _失敗(工具錯誤代碼.執行失敗, True)
        if 類別 is PermissionError:
            return _失敗(工具錯誤代碼.執行失敗, False, True, 路徑權限錯誤訊息)
        return _失敗(工具錯誤代碼.執行失敗)


def _正規化回傳(值: Any) -> 工具執行結果:
    """將 handler 回傳值正規化為單層 canonical 工具結果。"""
    狀態 = payload = 權限 = 可恢復 = 結果 = None
    try:
        if type(值) is dict and "success" in 值:
            狀態 = dict.__getitem__(值, "success")
            if type(狀態) is not bool:
                return _失敗(工具錯誤代碼.端點設定錯誤)
            if 狀態:
                if "error" in 值:
                    return _失敗(工具錯誤代碼.端點設定錯誤)
                if "result" in 值:
                    if len(值) != 2:
                        return _失敗(工具錯誤代碼.端點設定錯誤)
                    payload = dict.__getitem__(值, "result")
                else:
                    payload = dict(值)
                    del payload["success"]
                結果 = _成功或設定錯誤(payload)
                值 = 狀態 = payload = 權限 = 可恢復 = None
                return 結果
            if "result" in 值 or type(值.get("error")) is not str or not 值["error"]:
                return _失敗(工具錯誤代碼.端點設定錯誤)
            權限 = 值.get("permission_denied") is True and type(值.get("permission_denied")) is bool
            可恢復 = 值.get("recoverable") is True and type(值.get("recoverable")) is bool
            return _失敗(
                工具錯誤代碼.執行失敗, 可恢復, 權限,
                權限錯誤訊息 if 權限 else None,
            )
        結果 = _成功或設定錯誤(值)
        值 = 狀態 = payload = 權限 = 可恢復 = None
        return 結果
    except BaseException as 錯誤:
        類別 = type(錯誤)
        值 = 狀態 = payload = 權限 = 可恢復 = 結果 = None
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        return _失敗(工具錯誤代碼.端點設定錯誤)


def _成功或設定錯誤(值: Any) -> 工具執行結果:
    """封存成功值；不可複製或含巢狀 outcome 時固定回傳設定錯誤。"""
    副本 = 結果 = None
    try:
        副本 = _複製JSON(值)
        if _含巢狀Outcome(副本):
            raise ValueError
        結果 = 工具執行結果(True, 副本)
        值 = 副本 = None
        return 結果
    except BaseException as 錯誤:
        類別 = type(錯誤)
        值 = 副本 = 結果 = None
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        return _失敗(工具錯誤代碼.端點設定錯誤)


def _失敗(代碼: 工具錯誤代碼, 可恢復: bool = False, 權限: bool = False, 訊息: str | None = None) -> 工具執行結果:
    """依固定 taxonomy 與訊息 allowlist 建立 canonical 失敗結果。"""
    結果 = None
    try:
        if type(代碼) is not 工具錯誤代碼 or type(可恢復) is not bool or type(權限) is not bool:
            代碼, 可恢復, 權限, 訊息 = 工具錯誤代碼.端點設定錯誤, False, False, None
        if 訊息 not in 允許錯誤訊息:
            訊息 = 錯誤預設訊息[代碼]
        結果 = 工具執行結果(False, None, 代碼, 訊息, 可恢復, 權限)
        代碼 = 可恢復 = 權限 = 訊息 = None
        return 結果
    except BaseException as 錯誤:
        類別 = type(錯誤)
        代碼 = 可恢復 = 權限 = 訊息 = 結果 = None
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        raise


def _重建結果(值: 工具執行結果) -> 工具執行結果:
    """從所有 slot 驗證並重建 fresh canonical 工具結果。"""
    成功 = 文字 = payload = 代碼 = 訊息 = 可恢復 = 權限 = 結果 = None
    try:
        if type(值) is not 工具執行結果:
            raise ValueError
        成功 = object.__getattribute__(值, "success")
        文字 = object.__getattribute__(值, "_result_json")
        代碼 = object.__getattribute__(值, "error_code")
        訊息 = object.__getattribute__(值, "error_message")
        可恢復 = object.__getattribute__(值, "recoverable")
        權限 = object.__getattribute__(值, "permission_denied")
        if 成功 is True:
            payload = _驗證內部結果(文字)
        elif 文字 is not None:
            raise ValueError
        結果 = 工具執行結果(成功, payload, 代碼, 訊息, 可恢復, 權限)
        值 = 成功 = 文字 = payload = 代碼 = 訊息 = 可恢復 = 權限 = None
        return 結果
    except BaseException as 錯誤:
        類別 = type(錯誤)
        值 = 成功 = 文字 = payload = 代碼 = 訊息 = 可恢復 = 權限 = 結果 = None
        if isinstance(錯誤, 控制流程):
            類別 = 錯誤 = None
            raise
        raise ValueError("工具結果狀態無效") from None


def _正規化結果文字(值: Any) -> str:
    """將 module-owned strict JSON tree 轉成唯一 canonical 文字。"""
    try:
        return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except BaseException:
        值 = None
        raise


def _拒絕非有限(值: str) -> Any:
    """拒絕 JSON decoder 的非有限數值常數。"""
    raise ValueError


def _建立唯一字典(配對: list[tuple[str, Any]]) -> dict[str, Any]:
    """由 decoder pairs 建立 exact dict，重複鍵一律拒絕。"""
    結果: dict[str, Any] = {}
    for 鍵, 值 in 配對:
        if type(鍵) is not str or 鍵 in 結果:
            raise ValueError
        結果[鍵] = 值
    return 結果


def _嚴格解碼結果(文字: str) -> Any:
    """拒絕 duplicate keys 與 non-finite constants。"""
    try:
        return json.loads(文字, object_pairs_hook=_建立唯一字典, parse_constant=_拒絕非有限)
    except BaseException:
        文字 = None
        raise


def _驗證內部結果(文字: Any) -> Any:
    """strict decode、bounds copy 並驗證內部文字恰為 canonical。"""
    payload = 副本 = 正規文字 = None
    try:
        if type(文字) is not str or len(文字) > 1_000_000:
            raise ValueError
        payload = _嚴格解碼結果(文字)
        副本 = _複製JSON(payload)
        正規文字 = _正規化結果文字(副本)
        if 正規文字 != 文字:
            raise ValueError
        文字 = payload = 正規文字 = None
        return 副本
    except BaseException:
        文字 = payload = 副本 = 正規文字 = None
        raise


def _複製JSON(值: Any) -> Any:
    """在單次 bounded traversal 中建立 exact-builtins JSON snapshot。"""
    狀態: Any = [0, 0, set(), []]
    結果 = 描述 = 來源 = 身分 = 項目 = 鍵 = 子值 = None
    try:
        結果 = _複製JSON節點(值, 0, 狀態)
        for 描述 in 狀態[3]:
            種類, 來源, 預期 = 描述
            身分 = []
            if 種類 == "list":
                長度 = list.__len__(來源)
                for 索引 in range(長度):
                    項目 = list.__getitem__(來源, 索引)
                    身分.append(id(項目))
                    項目 = None
            else:
                長度 = dict.__len__(來源)
                for 鍵, 子值 in dict.items(來源):
                    if type(鍵) is not str:
                        raise ValueError
                    身分.append((id(鍵), id(子值)))
                    鍵 = 子值 = None
            if tuple(身分) != 預期:
                raise ValueError
            來源 = 身分 = 描述 = None
        值 = 狀態 = 描述 = 來源 = 身分 = 項目 = 鍵 = 子值 = None
        return 結果
    except BaseException:
        值 = 狀態 = 結果 = 描述 = 來源 = 身分 = 項目 = 鍵 = 子值 = None
        raise


def _複製JSON節點(來源: Any, 深度: int, 狀態: Any) -> Any:
    """以 bounded traversal 遞迴建立單一 exact-builtins JSON 節點。"""
    結果 = 描述 = 身分 = 項目 = 鍵 = 子值 = 字元 = 複製鍵 = None
    try:
        狀態[0] += 1
        if 狀態[0] > 10_000 or 深度 > 64:
            raise ValueError
        大小 = 0
        if 來源 is None:
            大小 = 4
        elif type(來源) is bool:
            大小 = 4 if 來源 else 5
        elif type(來源) is int:
            if int.bit_length(來源) > 12_000:
                raise ValueError
            大小 = len(str(來源))
        elif type(來源) is float:
            if not math.isfinite(來源):
                raise ValueError
            大小 = len(repr(來源))
        elif type(來源) is str:
            if len(來源) > 1_000_000:
                raise ValueError
            大小 = 2
            for 字元 in 來源:
                編碼 = ord(字元)
                if 編碼 < 32:
                    大小 += 2 if 字元 in "\b\t\n\f\r" else 6
                elif 字元 == '"' or 字元 == "\\":
                    大小 += 2
                elif 編碼 < 128:
                    大小 += 1
                elif 編碼 < 2048:
                    大小 += 2
                elif 0xD800 <= 編碼 <= 0xDFFF:
                    raise ValueError
                elif 編碼 < 65536:
                    大小 += 3
                else:
                    大小 += 4
                if 大小 > 1_000_000:
                    raise ValueError
            字元 = None
        elif type(來源) is list:
            識別 = id(來源)
            if 識別 in 狀態[2]:
                raise ValueError
            長度 = list.__len__(來源)
            if 長度 > 10_000:
                raise ValueError
            身分 = []
            for 索引 in range(長度):
                項目 = list.__getitem__(來源, 索引)
                身分.append(id(項目))
                項目 = None
            描述 = ("list", 來源, tuple(身分))
            狀態[3].append(描述)
            狀態[1] += 2 + max(0, 長度 - 1)
            if 狀態[1] > 1_000_000:
                raise ValueError
            狀態[2].add(識別)
            結果 = []
            for 索引 in range(長度):
                項目 = list.__getitem__(來源, 索引)
                結果.append(_複製JSON節點(項目, 深度 + 1, 狀態))
                項目 = None
            狀態[2].remove(識別)
            return 結果
        elif type(來源) is dict:
            識別 = id(來源)
            if 識別 in 狀態[2]:
                raise ValueError
            長度 = dict.__len__(來源)
            if 長度 > 10_000:
                raise ValueError
            身分 = []
            for 鍵, 子值 in dict.items(來源):
                if type(鍵) is not str:
                    raise ValueError
                身分.append((id(鍵), id(子值)))
                鍵 = 子值 = None
            描述 = ("dict", 來源, tuple(身分))
            狀態[3].append(描述)
            狀態[1] += 2 + max(0, 長度 - 1) + 長度
            if 狀態[1] > 1_000_000:
                raise ValueError
            狀態[2].add(識別)
            結果 = {}
            for 鍵, 子值 in dict.items(來源):
                if type(鍵) is not str:
                    raise ValueError
                複製鍵 = _複製JSON節點(鍵, 深度 + 1, 狀態)
                結果[複製鍵] = _複製JSON節點(子值, 深度 + 1, 狀態)
                鍵 = 子值 = 複製鍵 = None
            狀態[2].remove(識別)
            return 結果
        else:
            raise ValueError
        狀態[1] += 大小
        if 狀態[1] > 1_000_000:
            raise ValueError
        return 來源
    except BaseException:
        來源 = 狀態 = 結果 = 描述 = 身分 = 項目 = 鍵 = 子值 = 字元 = 複製鍵 = None
        raise


def _含巢狀Outcome(值: Any) -> bool:
    """只掃描 module-owned bounded copy，不建立 generator frame。"""
    待查: Any = [值]
    目前 = 子值 = None
    已看: Any = set()
    try:
        while 待查:
            目前 = 待查.pop()
            識別 = id(目前)
            if 識別 in 已看:
                continue
            已看.add(識別)
            if len(已看) > 10_000:
                raise ValueError
            if type(目前) is dict:
                if type(dict.get(目前, "success")) is bool and ("result" in 目前 or "error" in 目前):
                    return True
                for 子值 in dict.values(目前):
                    待查.append(子值)
                    子值 = None
            elif type(目前) is list:
                for 子值 in 目前:
                    待查.append(子值)
                    子值 = None
        return False
    except BaseException:
        值 = 待查 = 目前 = 子值 = 已看 = None
        raise
