"""以有界線性 regex 偵測呼叫 JSON 中的基本敏感資料位置。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

_最大深度 = 8
_每物件最大鍵數 = 128
_最大字串位元組 = 4 * 1024
_最大總位元組 = 32 * 1024
_最大節點數 = 4096
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_敏感類型代碼 = frozenset({
    "email", "tw_national_id_format", "phone", "payment_card_candidate",
    "credential_assignment",
})

_EMAIL樣式 = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}"
    r"(?:\.[A-Za-z0-9\-]{1,63})+(?![A-Za-z0-9._%+\-])"
)
_台灣身分證格式樣式 = re.compile(r"(?<![A-Za-z0-9])[A-Z][12][0-9]{8}(?![A-Za-z0-9])")
_電話樣式 = re.compile(r"(?<![0-9])(?:\+886[- ]?|0)9[0-9]{2}[- ]?[0-9]{3}[- ]?[0-9]{3}(?![0-9])")
_卡號候選樣式 = re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])")
_憑證指定樣式 = re.compile(
    r"(?i)[\"']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_./+~=-]{8,256})"
)


class 敏感偵測錯誤(RuntimeError):
    """代表敏感資料偵測因輸入或執行邊界而固定拒絕。"""


@dataclass(frozen=True, slots=True)
class 敏感命中:
    """不含原值、片段或來源物件的不可變敏感位置。"""

    類型代碼: str
    JSON路徑: str
    開始: int
    結束: int

    def __post_init__(self) -> None:
        """拒絕偽造型別、非 canonical pointer 與越界位置。"""
        類型代碼 = JSON路徑 = 開始 = 結束 = None
        通過 = False
        try:
            if type(self) is 敏感命中:
                類型代碼 = object.__getattribute__(self, "類型代碼")
                JSON路徑 = object.__getattribute__(self, "JSON路徑")
                開始 = object.__getattribute__(self, "開始")
                結束 = object.__getattribute__(self, "結束")
                通過 = (type(類型代碼) is str and 類型代碼 in _敏感類型代碼
                      and type(JSON路徑) is str and _是canonical_JSON路徑(JSON路徑)
                      and type(開始) is int and type(結束) is int
                      and 0 <= 開始 < 結束 <= _最大字串位元組)
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            self = 類型代碼 = JSON路徑 = 開始 = 結束 = 錯誤 = None
            if 是控制流程:
                raise
        self = 類型代碼 = JSON路徑 = 開始 = 結束 = None
        if not 通過:
            raise 敏感偵測錯誤("敏感命中格式無效") from None


def _是canonical_JSON路徑(路徑) -> bool:
    """檢查有界 RFC 6901 JSON Pointer；每個波浪號必須是 ~0 或 ~1。"""
    位元組 = 字元 = None
    try:
        if type(路徑) is not str:
            return False
        位元組 = str.encode(路徑, "utf-8")
        if len(位元組) > _最大字串位元組 or (路徑 and not 路徑.startswith("/")):
            return False
        索引 = 0
        while 索引 < len(路徑):
            字元 = 路徑[索引]
            if 字元 == "~":
                if 索引 + 1 >= len(路徑) or 路徑[索引 + 1] not in "01":
                    return False
                索引 += 1
            索引 += 1
        return True
    except BaseException:
        路徑 = 位元組 = 字元 = 索引 = None
        raise


def 偵測敏感資料(輸入: object) -> tuple[敏感命中, ...]:
    """單次走訪精確內建 JSON 樹，回傳穩定排序且不重疊的位置。"""
    if type(輸入) not in (str, dict):
        輸入 = None
        raise 敏感偵測錯誤("敏感資料偵測失敗") from None
    命中們: list[敏感命中] | None = []
    預算: list[int] | None = [0, 0]
    目前容器: set[int] | None = set()
    結果 = None
    try:
        _走訪JSON(輸入, "", 0, 預算, 目前容器, 命中們)
        輸入 = None
        命中們.sort(key=_命中排序鍵)
        結果 = _移除重疊命中(命中們)
        命中們 = None
        return 結果
    except BaseException as 邊界錯誤:
        是控制流程 = type(邊界錯誤) in _控制流程例外
        輸入 = 命中們 = 預算 = 目前容器 = 結果 = None
        if 是控制流程:
            raise
    raise 敏感偵測錯誤("敏感資料偵測失敗") from None


def _走訪JSON(值, 路徑, 深度, 預算, 目前容器, 命中們) -> None:
    """檢查限制並以 module-owned 描述子防止遍歷期間的不一致突變。"""
    值型別 = 位元組 = 描述子們 = 鍵 = 項目 = 子路徑 = 容器識別 = None
    原鍵 = 目前鍵 = 目前項目 = 索引 = None
    try:
        預算[0] += 1
        if 預算[0] > _最大節點數 or 深度 > _最大深度:
            raise ValueError
        值型別 = type(值)
        if 值型別 is str:
            位元組 = str.encode(值, "utf-8")
            _增加位元組預算(預算, len(位元組))
            if len(位元組) > _最大字串位元組:
                raise ValueError
            _掃描字串(值, 路徑, 命中們)
            return
        if 值 is None:
            _增加位元組預算(預算, 4)
            return
        if 值型別 is bool:
            _增加位元組預算(預算, 4 if 值 else 5)
            return
        if 值型別 is int:
            _增加位元組預算(預算, len(str(值)))
            return
        if 值型別 is float:
            if not math.isfinite(值):
                raise ValueError
            _增加位元組預算(預算, len(repr(值)))
            return
        if 值型別 not in (dict, list):
            raise ValueError
        容器識別 = id(值)
        if 容器識別 in 目前容器:
            raise ValueError
        目前容器.add(容器識別)
        描述子們 = []
        if 值型別 is dict:
            if dict.__len__(值) > _每物件最大鍵數:
                raise ValueError
            for 鍵, 項目 in dict.items(值):
                if type(鍵) is not str:
                    raise ValueError
                位元組 = str.encode(鍵, "utf-8")
                if len(位元組) > _最大字串位元組:
                    raise ValueError
                _增加位元組預算(預算, len(位元組) + 4)
                描述子們.append((鍵, id(項目)))
            for 鍵, 項目識別 in 描述子們:
                項目 = dict.__getitem__(值, 鍵)
                if id(項目) != 項目識別:
                    raise ValueError
                子路徑 = 路徑 + "/" + str.replace(str.replace(鍵, "~", "~0"), "/", "~1")
                _走訪JSON(項目, 子路徑, 深度 + 1, 預算, 目前容器, 命中們)
                if id(dict.__getitem__(值, 鍵)) != 項目識別:
                    raise ValueError
            if dict.__len__(值) != len(描述子們):
                raise ValueError
            索引 = 0
            for 目前鍵, 目前項目 in dict.items(值):
                原鍵, 項目識別 = 描述子們[索引]
                if (type(目前鍵) is not str or 目前鍵 != 原鍵
                        or id(目前項目) != 項目識別
                        or id(dict.__getitem__(值, 原鍵)) != 項目識別):
                    raise ValueError
                索引 += 1
            if 索引 != len(描述子們):
                raise ValueError
        else:
            for 項目 in list.__iter__(值):
                描述子們.append(id(項目))
            for 索引, 項目識別 in enumerate(描述子們):
                if list.__len__(值) != len(描述子們):
                    raise ValueError
                項目 = list.__getitem__(值, 索引)
                if id(項目) != 項目識別:
                    raise ValueError
                子路徑 = 路徑 + "/" + str(索引)
                _走訪JSON(項目, 子路徑, 深度 + 1, 預算, 目前容器, 命中們)
                if id(list.__getitem__(值, 索引)) != 項目識別:
                    raise ValueError
            if list.__len__(值) != len(描述子們):
                raise ValueError
            索引 = 0
            while 索引 < len(描述子們):
                if id(list.__getitem__(值, 索引)) != 描述子們[索引]:
                    raise ValueError
                索引 += 1
        目前容器.remove(容器識別)
    except BaseException:
        if 容器識別 is not None:
            目前容器.discard(容器識別)
        值 = 路徑 = 預算 = 目前容器 = 命中們 = None
        值型別 = 位元組 = 描述子們 = 鍵 = 項目 = 子路徑 = 容器識別 = None
        原鍵 = 目前鍵 = 目前項目 = 索引 = 項目識別 = None
        raise


def _增加位元組預算(預算, 數量) -> None:
    """累加 detector-specific UTF-8／結構預算並固定拒絕超限。"""
    預算[1] += 數量
    if 預算[1] > _最大總位元組:
        raise ValueError


def _掃描字串(文字, 路徑, 命中們) -> None:
    """把字串候選立即縮減成只含位置的 DTO。"""
    候選們 = None
    try:
        候選們 = _找出字串命中(文字)
        for 類型代碼, 開始, 結束 in 候選們:
            命中們.append(敏感命中(類型代碼, 路徑, 開始, 結束))
    except BaseException:
        文字 = 路徑 = 命中們 = 候選們 = 類型代碼 = 開始 = 結束 = None
        raise


def _找出字串命中(文字) -> list[tuple[str, int, int]]:
    """執行固定保守樣式；卡號候選另以 Luhn 驗證。"""
    結果 = []
    比對 = None
    try:
        for 類型代碼, 樣式 in (
            ("email", _EMAIL樣式), ("tw_national_id_format", _台灣身分證格式樣式),
            ("phone", _電話樣式), ("payment_card_candidate", _卡號候選樣式),
        ):
            for 比對 in 樣式.finditer(文字):
                if 類型代碼 != "payment_card_candidate" or _通過Luhn(比對.group(0)):
                    結果.append((類型代碼, 比對.start(), 比對.end()))
        for 比對 in _憑證指定樣式.finditer(文字):
            結果.append(("credential_assignment", 比對.start(1), 比對.end(1)))
        return 結果
    except BaseException:
        文字 = 結果 = 比對 = 類型代碼 = 樣式 = None
        raise


def _通過Luhn(候選) -> bool:
    """只接受含十三至十九位數且通過 Luhn checksum 的候選。"""
    數字們 = []
    字元 = 索引 = 數字 = 總和 = 奇偶 = None
    try:
        for 字元 in 候選:
            if "0" <= 字元 <= "9":
                數字們.append(ord(字元) - 48)
        if len(數字們) < 13 or len(數字們) > 19:
            return False
        總和 = 0
        奇偶 = len(數字們) % 2
        for 索引, 數字 in enumerate(數字們):
            if 索引 % 2 == 奇偶:
                數字 *= 2
                if 數字 > 9:
                    數字 -= 9
            總和 += 數字
        return 總和 % 10 == 0
    except BaseException:
        候選 = 數字們 = 字元 = 索引 = 數字 = 總和 = 奇偶 = None
        raise


def _驗證敏感命中(命中) -> None:
    """消費前重新驗證可能經 object.__setattr__ 竄改的 DTO。"""
    類型代碼 = JSON路徑 = 開始 = 結束 = None
    try:
        if type(命中) is not 敏感命中:
            raise ValueError
        類型代碼 = object.__getattribute__(命中, "類型代碼")
        JSON路徑 = object.__getattribute__(命中, "JSON路徑")
        開始 = object.__getattribute__(命中, "開始")
        結束 = object.__getattribute__(命中, "結束")
        if not (type(類型代碼) is str and 類型代碼 in _敏感類型代碼
                and type(JSON路徑) is str and _是canonical_JSON路徑(JSON路徑)
                and type(開始) is int and type(結束) is int
                and 0 <= 開始 < 結束 <= _最大字串位元組):
            raise ValueError
    except BaseException:
        命中 = 類型代碼 = JSON路徑 = 開始 = 結束 = None
        raise


def _命中排序鍵(命中) -> tuple[str, int, int, str]:
    """依 JSON Pointer、起訖 code-point offset 與類型代碼排序。"""
    結果 = None
    try:
        _驗證敏感命中(命中)
        結果 = (object.__getattribute__(命中, "JSON路徑"),
              object.__getattribute__(命中, "開始"),
              object.__getattribute__(命中, "結束"),
              object.__getattribute__(命中, "類型代碼"))
        命中 = None
        return 結果
    except BaseException:
        命中 = 結果 = None
        raise


def _移除重疊命中(命中們) -> tuple[敏感命中, ...]:
    """同一路徑依排序保留最先區間，捨棄其後任何重疊候選。"""
    結果 = []
    命中 = 已保留 = 回傳值 = None
    try:
        for 命中 in 命中們:
            _驗證敏感命中(命中)
            重疊 = False
            for 已保留 in 結果:
                _驗證敏感命中(已保留)
                if (object.__getattribute__(已保留, "JSON路徑") == object.__getattribute__(命中, "JSON路徑")
                        and object.__getattribute__(命中, "開始") < object.__getattribute__(已保留, "結束")
                        and object.__getattribute__(已保留, "開始") < object.__getattribute__(命中, "結束")):
                    重疊 = True
                    break
            if not 重疊:
                結果.append(命中)
        回傳值 = tuple(結果)
        命中們 = 結果 = 命中 = 已保留 = None
        return 回傳值
    except BaseException:
        命中們 = 結果 = 命中 = 已保留 = 回傳值 = 重疊 = None
        raise
