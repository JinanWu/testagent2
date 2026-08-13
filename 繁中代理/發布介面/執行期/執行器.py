"""建立只使用已發布快照的版本隔離執行期。

參數：不適用；模組公開快照資料型別、驗證函式與執行器工廠。
回傳：不適用；各公開函式依自身契約回傳不可變快照、摘要或執行器。
例外：匯入依賴缺失時傳出標準匯入例外；執行期錯誤由各公開邊界固定化。
副作用：匯入只建立型別、常數、鎖與弱參照狀態表，不載入版本或呼叫模型。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import unicodedata
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ..技能套件.清單 import (
    是合法技能套件清單參照,
    計算套件雜湊 as 計算清單套件雜湊,
)
from ..技能套件.安全複製 import 技能套件最大總位元組數
from .工具結果 import 工具設定錯誤, 工具逾時
from .工具版本庫 import 工具快照項目
from .模型契約 import 模型設定快照, 複製JSON, 重建設定

_識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_雜湊 = re.compile(r"[0-9a-f]{64}")
_固定錯誤 = "發布執行期不可用"
_唯一來源 = "endpoint_version_snapshot"
_最大檔案數 = 256
_最大提示位元組 = 1_000_000
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_綱要修正訊息 = "輸出未符合回應綱要；請只回傳符合綱要的 JSON。"
_最大工具回合 = 8
_最大工具呼叫 = 16
_最大工具結果位元組 = 262_144

class 發布執行錯誤(RuntimeError):
    """版本、套件或組裝邊界失敗時不洩漏內容的固定錯誤。"""

class 結構化輸出錯誤(發布執行錯誤):
    """兩次模型輸出皆不符合版本釘選綱要。"""

class 發布工具執行錯誤(發布執行錯誤):
    """釘選工具呼叫無效、失敗或超過有界執行額度。"""

class 發布執行快照提供者(Protocol):
    """只依 exact endpoint version id 取得 immutable 快照。"""

    def 取得發布執行快照(self, endpoint_version_id: str) -> object:
        """不得查詢 current、latest 或 live endpoint。"""

class 技能套件載入器(Protocol):
    """只依版本、雜湊與 manifest reference 讀取發布快照。"""

    def 載入技能套件快照(
        self, endpoint_version_id: str, skill_bundle_hash: str,
        manifest_reference: str, source: str,
    ) -> object:
        """不得接受 Path、cwd、home 或任意本機根目錄。"""

@dataclass(frozen=True, slots=True, repr=False, init=False)
class 技能套件檔案:
    """一個 canonical POSIX path 與 immutable bytes 的 hash-addressed 項目。

    欄位：``path``、``sha256`` 與 ``content`` 保存已驗證路徑、摘要與內容。
    回傳：建立不可變檔案 DTO。例外：任一欄位或摘要不符時拋出固定執行錯誤。
    副作用：只複製內容，不讀寫檔案系統。
    """

    path: str
    sha256: str
    content: bytes

    def __init__(self, *, path: str, sha256: str, content: bytes) -> None:
        """驗證並保存 exact 檔案 scalar。

        參數：三個 keyword-only 參數分別是路徑、SHA-256 與內容。回傳：無。
        例外：路徑、型別、共享套件額度或摘要不符時拋出固定 ``發布執行錯誤``。
        副作用：配置內容的 immutable 複本，不存取外部資源。
        """
        try:
            if type(self) is not 技能套件檔案 or not _是套件路徑(path):
                raise ValueError
            if type(sha256) is not str or _雜湊.fullmatch(sha256) is None:
                raise ValueError
            if type(content) is not bytes or len(content) > 技能套件最大總位元組數:
                raise ValueError
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), sha256):
                raise ValueError
            object.__setattr__(self, "path", path)
            object.__setattr__(self, "sha256", sha256)
            object.__setattr__(self, "content", bytes(content))
        except _控制流程:
            self = path = sha256 = content = None
            raise
        except BaseException:
            self = path = sha256 = content = None
            raise 發布執行錯誤(_固定錯誤) from None

@dataclass(frozen=True, slots=True, repr=False, init=False)
class 技能套件快照:
    """保存 loader 已驗證的兩種摘要與完整 immutable bundle。

    欄位：``endpoint_version_id`` 釘選端點版本；``skill_bundle_hash`` 驗證 ordered
    檔案三元組；``manifest_digest`` 識別 loader 已驗證的 canonical manifest bytes；
    ``清單原始資料`` 保存對應的 canonical bytes；``files`` 保存依 UTF-8 路徑排序的
    完整內容。回傳：不適用。
    例外：欄位由建構器驗證，失敗時拋出固定 ``發布執行錯誤``。
    副作用：只保存重新建立的不可變值，不存取檔案系統。
    """

    endpoint_version_id: str
    skill_bundle_hash: str
    manifest_digest: str
    清單原始資料: bytes
    files: tuple[技能套件檔案, ...]

    def __init__(
        self, *, endpoint_version_id: str, skill_bundle_hash: str,
        manifest_digest: str, 清單原始資料: bytes,
        files: tuple[技能套件檔案, ...],
    ) -> None:
        """重建檔案並分別驗證 bundle identity 與 manifest digest 格式。

        參數：端點版本、兩種摘要與 manifest bytes 來自已釘選 loader；``files`` 是
        完整 ordered 檔案。
        回傳：無。例外：型別、排序、額度、內容摘要、bundle hash 或 manifest digest
        不合契約時拋出固定 ``發布執行錯誤``。副作用：只配置 immutable 複本。
        """
        重建檔案 = None
        try:
            if not _是識別碼(endpoint_version_id) or type(files) is not tuple:
                raise ValueError
            重建檔案 = _重建套件檔案(files)
            計算值 = 計算技能套件雜湊(重建檔案)
            if (not _是雜湊(skill_bundle_hash) or not _是雜湊(manifest_digest)
                    or type(清單原始資料) is not bytes
                    or len(清單原始資料) > 技能套件最大總位元組數
                    or not hmac.compare_digest(
                        hashlib.sha256(清單原始資料).hexdigest(), manifest_digest,
                    )):
                raise ValueError
            if not hmac.compare_digest(計算值, skill_bundle_hash):
                raise ValueError
            object.__setattr__(self, "endpoint_version_id", endpoint_version_id)
            object.__setattr__(self, "skill_bundle_hash", skill_bundle_hash)
            object.__setattr__(self, "manifest_digest", manifest_digest)
            object.__setattr__(self, "清單原始資料", bytes(清單原始資料))
            object.__setattr__(self, "files", 重建檔案)
        except _控制流程:
            self = None
            endpoint_version_id = skill_bundle_hash = manifest_digest = ""
            清單原始資料 = b""
            files = ()
            重建檔案 = None
            raise
        except BaseException:
            self = None
            endpoint_version_id = skill_bundle_hash = manifest_digest = ""
            清單原始資料 = b""
            files = ()
            重建檔案 = None
            raise 發布執行錯誤(_固定錯誤) from None

@dataclass(frozen=True, slots=True, repr=False, init=False)
class 發布執行快照:
    """單一 endpoint version 的完整 prompt/tool/model pin。"""

    endpoint_id: str
    version_id: str
    service_account_id: str
    system_prompt: str
    permission_snapshot_digest: str
    skill_bundle_hash: str
    tool_handler_release: str
    tool_snapshot: tuple[工具快照項目, ...]
    model_config: 模型設定快照
    _response_schema_json: str | None
    manifest_reference: str

    @property
    def response_schema(self) -> dict[str, Any] | None:
        """每次由 private canonical JSON 產生 fresh schema tree。"""
        原文 = 結果 = None
        失敗 = False
        try:
            原文 = object.__getattribute__(self, "_response_schema_json")
            if 原文 is None:
                return None
            結果 = _解析正規JSON(原文, 500_000)
            if type(結果) is not dict:
                raise ValueError
            return 結果
        except _控制流程:
            self = 原文 = 結果 = None
            raise
        except BaseException:
            self = 原文 = 結果 = None
            失敗 = True
        if 失敗:
            raise 發布執行錯誤(_固定錯誤) from None
        raise AssertionError

    def __init__(
        self, *, endpoint_id: str, version_id: str, service_account_id: str,
        system_prompt: str, permission_snapshot_digest: str,
        skill_bundle_hash: str, tool_handler_release: str,
        tool_snapshot: tuple[工具快照項目, ...], model_config: 模型設定快照,
        response_schema: dict[str, Any] | None, manifest_reference: str,
    ) -> None:
        """完整重建所有 nested DTO；不保留 provider/caller mutable identity。"""
        工具 = 設定 = 結構 = None
        try:
            for 值 in (endpoint_id, version_id, service_account_id, tool_handler_release):
                if not _是識別碼(值):
                    raise ValueError
            if not 是合法技能套件清單參照(manifest_reference):
                raise ValueError
            if type(system_prompt) is not str or not system_prompt.strip() or len(system_prompt.encode()) > 500_000:
                raise ValueError
            if not _是雜湊(permission_snapshot_digest) or not _是雜湊(skill_bundle_hash):
                raise ValueError
            工具 = _重建工具快照(tool_snapshot)
            設定 = 重建設定(model_config)
            結構 = None if response_schema is None else 複製JSON(response_schema, 500_000)
            if 結構 is not None and type(結構) is not dict:
                raise ValueError
            if 設定.structured_output != (結構 is not None):
                raise ValueError
            值們 = (endpoint_id, version_id, service_account_id, system_prompt,
                    permission_snapshot_digest, skill_bundle_hash, tool_handler_release,
                    工具, 設定, None if 結構 is None else _建立正規JSON(結構),
                    manifest_reference)
            for 名稱, 值 in zip(self.__dataclass_fields__, 值們, strict=True):
                object.__setattr__(self, 名稱, 值)
        except _控制流程:
            self = endpoint_id = version_id = service_account_id = system_prompt = None
            permission_snapshot_digest = skill_bundle_hash = tool_handler_release = None
            tool_snapshot = model_config = response_schema = manifest_reference = 工具 = 設定 = 結構 = None
            raise
        except BaseException:
            self = endpoint_id = version_id = service_account_id = system_prompt = None
            permission_snapshot_digest = skill_bundle_hash = tool_handler_release = None
            tool_snapshot = model_config = response_schema = manifest_reference = 工具 = 設定 = 結構 = None
            raise 發布執行錯誤(_固定錯誤) from None

@dataclass(frozen=True, slots=True, repr=False, init=False)
class 發布執行請求:
    """只包含一份 detached JSON input；不存在 system/tool role 注入入口。"""

    _input_json: str
    _歷史JSON: str

    @property
    def input(self) -> Any:
        """每次回傳 fresh exact-builtins JSON tree。"""
        原文 = 結果 = None
        失敗 = False
        try:
            原文 = object.__getattribute__(self, "_input_json")
            結果 = _解析正規JSON(原文, 500_000)
            return 結果
        except _控制流程:
            self = 原文 = 結果 = None
            raise
        except BaseException:
            self = 原文 = 結果 = None
            失敗 = True
        if 失敗:
            raise 發布執行錯誤(_固定錯誤) from None
        raise AssertionError

    def __init__(self, input: Any, 歷史: tuple[object, ...] = ()) -> None:
        """立即 bounded detach caller JSON，拒絕 subclass、循環與非有限數。

        參數：current input 與有序完整 successful history pairs。
        返回值：無；保存 canonical input/history JSON 快照。
        """
        結果 = None
        失敗 = False
        try:
            結果 = 複製JSON(input, 500_000)
            object.__setattr__(self, "_input_json", _建立正規JSON(結果))
            歷史值 = []
            for 對話組 in 歷史:
                歷史值.extend((
                    object.__getattribute__(對話組, "user_message"),
                    object.__getattribute__(對話組, "assistant_message"),
                ))
            object.__setattr__(self, "_歷史JSON", _建立正規JSON(歷史值))
            return
        except _控制流程:
            self = input = 結果 = None
            raise
        except BaseException:
            self = input = 結果 = None
            失敗 = True
        if 失敗:
            raise 發布執行錯誤(_固定錯誤) from None
        raise AssertionError

def 計算技能套件雜湊(files: tuple[技能套件檔案, ...]) -> str:
    """依 BUNDLE canonical ordered file 三元組計算內容摘要。

    參數：``files`` 是依 UTF-8 路徑排序的完整 immutable 檔案 tuple。
    回傳：對 ``[path, size_bytes, sha256]`` ordered projection 計算的小寫 SHA-256。
    例外：檔案型別、順序、內容或額度不合契約時拋出固定 ``發布執行錯誤``；
    控制流程例外原樣傳出。副作用：只配置短暫 projection，不修改輸入。
    """
    重建檔案 = 項目們 = 項 = 路徑 = 摘要 = 內容 = 結果 = None
    失敗 = False
    try:
        重建檔案 = _重建套件檔案(files)
        項目們 = []
        for 項 in 重建檔案:
            路徑 = object.__getattribute__(項, "path")
            摘要 = object.__getattribute__(項, "sha256")
            內容 = object.__getattribute__(項, "content")
            項目們.append({"path": 路徑, "size_bytes": len(內容), "sha256": 摘要})
        結果 = 計算清單套件雜湊(項目們)
        return 結果
    except _控制流程:
        files = ()
        重建檔案 = 項目們 = 項 = 路徑 = 摘要 = 內容 = 結果 = None
        raise
    except BaseException:
        files = ()
        重建檔案 = 項目們 = 項 = 路徑 = 摘要 = 內容 = 結果 = None
        失敗 = True
    if 失敗:
        raise 發布執行錯誤(_固定錯誤) from None
    raise AssertionError


def _建立正規JSON(值: Any) -> str:
    """先 detach module-owned exact JSON tree，再編碼唯一 canonical identity。"""
    脫離 = 結果 = None
    try:
        脫離 = 複製JSON(值, 500_000)
        結果 = json.dumps(
            脫離, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        return 結果
    except _控制流程:
        值 = 脫離 = 結果 = None
        raise
    except BaseException:
        值 = 脫離 = 結果 = None
        raise


def _拒絕正規JSON常數(值: str) -> object:
    """拒絕 JSON 非有限常數且不在 hook traceback 保留 token。"""
    try:
        raise ValueError
    except BaseException:
        值 = None
        raise


def _建立正規JSON物件(項目們: list[tuple[str, Any]]) -> dict[str, Any]:
    """以 cleanup-aware explicit loop 拒絕 duplicate object keys。"""
    結果 = 鍵 = 值 = None
    try:
        結果 = {}
        for 鍵, 值 in 項目們:
            if 鍵 in 結果:
                raise ValueError
            結果[鍵] = 值
        return 結果
    except BaseException:
        項目們 = 結果 = 鍵 = 值 = None
        raise


def _解析正規JSON(原文: object, 上限: int) -> Any:
    """嚴格拒絕 duplicate/nonfinite/noncanonical 後回傳 fresh exact tree。"""
    來源 = 已解析 = 重建 = 重播 = None
    try:
        if type(原文) is not str or len(原文.encode("utf-8")) > 上限:
            raise ValueError
        來源 = 原文
        已解析 = json.loads(
            來源, parse_constant=_拒絕正規JSON常數,
            object_pairs_hook=_建立正規JSON物件,
        )
        重建 = 複製JSON(已解析, 上限)
        重播 = _建立正規JSON(重建)
        if 重播 != 來源:
            raise ValueError
        return 重建
    except _控制流程:
        原文 = 上限 = 來源 = 已解析 = 重建 = 重播 = None
        raise
    except BaseException:
        原文 = 上限 = 來源 = 已解析 = 重建 = 重播 = None
        raise


def _解析模型JSON(原文: object, 上限: int) -> Any:
    """接受任意合法排版，但拒絕 duplicate、nonfinite 與超限 JSON。"""
    來源 = 已解析 = 重建 = None
    try:
        if type(原文) is not str or len(原文.encode("utf-8")) > 上限:
            raise ValueError
        來源 = 原文
        已解析 = json.loads(
            來源, parse_constant=_拒絕正規JSON常數,
            object_pairs_hook=_建立正規JSON物件,
        )
        重建 = 複製JSON(已解析, 上限)
        return 重建
    except BaseException:
        原文 = 上限 = 來源 = 已解析 = 重建 = None
        raise


def _綱要只含本機參照(綱要: object) -> bool:
    """只允許同一份 schema resource 內以 # 起始的靜態或動態參照。"""
    待看 = [綱要]
    目前 = 鍵 = 值 = None
    try:
        while 待看:
            目前 = 待看.pop()
            if type(目前) is dict:
                for 鍵, 值 in dict.items(目前):
                    if 鍵 in ("$ref", "$dynamicRef"):
                        if type(值) is not str or not 值.startswith("#"):
                            return False
                    if type(值) in (dict, list):
                        待看.append(值)
            elif type(目前) is list:
                for 值 in 目前:
                    if type(值) in (dict, list):
                        待看.append(值)
        return True
    finally:
        綱要 = 待看 = 目前 = 鍵 = 值 = None


def _建立綱要驗證器(綱要: dict[str, Any]) -> Draft202012Validator:
    """建立不啟用 format checker 的 fresh Draft 2020-12 validator。"""
    結果 = None
    try:
        結果 = Draft202012Validator(綱要)
        return 結果
    except BaseException:
        綱要 = 結果 = None
        raise


def _預檢回應綱要(綱要原文: str | None) -> None:
    """在 SA/bundle/tool/model callback 前檢查 meta-schema 與遠端參照。"""
    綱要 = 驗證器 = None
    try:
        if 綱要原文 is None:
            return
        綱要 = _解析正規JSON(綱要原文, 500_000)
        if type(綱要) is not dict or not _綱要只含本機參照(綱要):
            raise ValueError
        Draft202012Validator.check_schema(綱要)
        驗證器 = _建立綱要驗證器(綱要)
    except BaseException:
        綱要原文 = 綱要 = 驗證器 = None
        raise


def _模型輸出符合綱要(回應文字: str, 綱要原文: str) -> bool:
    """以 invocation-local schema/tree 驗證；只有輸出無效可觸發 retry。"""
    綱要 = 驗證器 = 已解析 = 驗證錯誤 = None
    try:
        綱要 = _解析正規JSON(綱要原文, 500_000)
        驗證器 = _建立綱要驗證器(綱要)
        try:
            已解析 = _解析模型JSON(回應文字, 500_000)
        except _控制流程:
            raise
        except (ValueError, RecursionError, UnicodeError):
            return False
        try:
            驗證器.validate(已解析)
        except _控制流程:
            raise
        except ValidationError as 驗證錯誤:
            if type(驗證錯誤) is not ValidationError:
                raise
            return False
        return True
    finally:
        回應文字 = 綱要原文 = 綱要 = 驗證器 = 已解析 = 驗證錯誤 = None


def _重建套件檔案(不可信檔案: object) -> tuple[技能套件檔案, ...]:
    """以共享 4 MiB 額度全量重建 exact 檔案 tuple。

    參數：``不可信檔案`` 是待驗證的 exact tuple。回傳：排序與內容皆重新驗證的
    immutable ``技能套件檔案`` tuple。例外：型別、身分、重複、失序、摘要或總量
    不合契約時傳出驗證例外。副作用：只配置內容複本與短暫索引，不存取外部資源。
    """
    結果 = 描述 = 已看 = None
    try:
        if type(不可信檔案) is not tuple or not 1 <= len(不可信檔案) <= _最大檔案數:
            raise ValueError
        描述 = []
        for 項 in 不可信檔案:
            if type(項) is not 技能套件檔案:
                raise ValueError
            描述.append((項, object.__getattribute__(項, "path"),
                         object.__getattribute__(項, "sha256"),
                         object.__getattribute__(項, "content")))
        結果 = []
        已看 = set()
        前一路徑 = None
        總量 = 0
        for 原項, 路徑, 摘要, 內容 in 描述:
            重建 = 技能套件檔案(path=路徑, sha256=摘要, content=內容)
            總量 += len(重建.content)
            編碼路徑 = 重建.path.encode("utf-8")
            if 總量 > 技能套件最大總位元組數 or 重建.path in 已看:
                raise ValueError
            if 前一路徑 is not None and 前一路徑 >= 編碼路徑:
                raise ValueError
            已看.add(重建.path)
            前一路徑 = 編碼路徑
            結果.append(重建)
        if len(不可信檔案) != len(描述):
            raise ValueError
        for 索引 in range(len(描述)):
            原項, 路徑, 摘要, 內容 = 描述[索引]
            if 不可信檔案[索引] is not 原項:
                raise ValueError
            if (object.__getattribute__(原項, "path") is not 路徑
                    or object.__getattribute__(原項, "sha256") is not 摘要
                    or object.__getattribute__(原項, "content") is not 內容):
                raise ValueError
        return tuple(結果)
    except BaseException:
        不可信檔案 = 結果 = 描述 = 已看 = 原項 = 路徑 = 摘要 = 內容 = 重建 = None
        raise

def _重建工具快照(值: object) -> tuple[工具快照項目, ...]:
    """完整捕捉並重建 U02 exact tool snapshot tuple。"""
    結果 = 描述 = 已看 = None
    try:
        if type(值) is not tuple:
            raise ValueError
        描述 = []
        for 項 in 值:
            if type(項) is not 工具快照項目:
                raise ValueError
            描述.append((項, object.__getattribute__(項, "name"),
                         object.__getattribute__(項, "revision"),
                         object.__getattribute__(項, "digest")))
        結果 = []
        已看 = set()
        for 原項, 名稱, 修訂, 摘要 in 描述:
            if 名稱 in 已看:
                raise ValueError
            已看.add(名稱)
            結果.append(工具快照項目(name=名稱, revision=修訂, digest=摘要))
        if len(值) != len(描述):
            raise ValueError
        for 索引 in range(len(描述)):
            原項, 名稱, 修訂, 摘要 = 描述[索引]
            if 值[索引] is not 原項:
                raise ValueError
            if (object.__getattribute__(原項, "name") is not 名稱
                    or object.__getattribute__(原項, "revision") is not 修訂
                    or object.__getattribute__(原項, "digest") is not 摘要):
                raise ValueError
        return tuple(結果)
    except BaseException:
        值 = 結果 = 描述 = 已看 = 原項 = 名稱 = 修訂 = 摘要 = None
        raise

def _是套件路徑(值: object) -> bool:
    """只接受 bounded NFC relative POSIX path，拒絕 path traversal 概念。"""
    if type(值) is not str or not 值 or len(值.encode()) > 512 or "\\" in 值:
        return False
    if unicodedata.normalize("NFC", 值) != 值 or 值.startswith("/"):
        return False
    部分 = 值.split("/")
    if len(部分) > 16:
        return False
    for 段 in 部分:
        if not 段 or 段 in (".", ".."):
            return False
        for 字 in 段:
            編碼 = ord(字)
            if 編碼 < 32 or 0xD800 <= 編碼 <= 0xDFFF:
                return False
    return True

def _是識別碼(值: object) -> bool:
    """判斷 exact bounded identifier。"""
    return type(值) is str and _識別碼.fullmatch(值) is not None

def _是雜湊(值: object) -> bool:
    """判斷 exact lowercase SHA-256。"""
    return type(值) is str and _雜湊.fullmatch(值) is not None


# 執行器狀態刻意外置；instance 沒有 prompt、owner、memory 或 provider data slots。
import threading
import weakref

from .工具版本庫 import 建立版本釘選工具登錄器
from .服務帳戶 import 載入服務帳戶上下文或失敗關閉
from .模型契約 import 模型轉接請求, 模型逾時錯誤
from .模型轉接器 import 建立模型轉接器


def _建立執行模型請求(狀態: tuple[object, ...], 輸入原文: str, 修正: bool) -> 模型轉接請求:
    """每一回都從 sealed canonical state 重建 fresh messages、tools 與 schema。"""
    輸入 = 訊息 = 工具 = 結構 = 結果 = None
    try:
        輸入 = _解析正規JSON(輸入原文, 500_000)
        訊息 = [
            {"role": "system", "content": 狀態[1]},
            {"role": "user", "content": 輸入原文, "metadata": {"input_json": 輸入}},
        ]
        if 修正:
            訊息.append({"role": "user", "content": _綱要修正訊息})
        工具 = 狀態[2].列出工具結構()  # type: ignore[union-attr]
        結構 = None if 狀態[4] is None else _解析正規JSON(狀態[4], 500_000)
        結果 = 模型轉接請求(訊息, 工具, 結構)
        return 結果
    except BaseException:
        狀態 = 輸入原文 = 修正 = 輸入 = 訊息 = 工具 = 結構 = 結果 = None
        raise


def _建立含歷史初始訊息(狀態: tuple[object, ...], 輸入原文: str,
                 歷史原文: str) -> list[dict[str, Any]]:
    """固定 Published system prompt，接 bounded successful history，再接 current input。

    參數：sealed executor state、canonical current input 與 canonical history JSON。
    返回值：system → history → current user 的 fresh model messages。
    """
    歷史 = _解析正規JSON(歷史原文, 500_000)
    if type(歷史) is not list or any(type(訊息) is not dict for 訊息 in 歷史):
        raise ValueError
    訊息 = [{"role": "system", "content": 狀態[1]}]
    訊息.extend(歷史)
    輸入 = _解析正規JSON(輸入原文, 500_000)
    訊息.append({"role": "user", "content": 輸入原文, "metadata": {"input_json": 輸入}})
    return 訊息


def _建立對話模型請求(狀態: tuple[object, ...], 訊息: list[dict[str, Any]]) -> 模型轉接請求:
    """由 invocation-local transcript 與建立點釘選工具／schema 建立 fresh request。

    參數：sealed state 與已驗證 transcript。回傳：detached 模型請求。
    例外：任一資料失真時傳出驗證例外。副作用：只讀 captured registry。
    """
    工具 = 狀態[2].列出工具結構()  # type: ignore[union-attr]
    結構 = None if 狀態[4] is None else _解析正規JSON(狀態[4], 500_000)
    return 模型轉接請求(訊息, 工具, 結構)


def _工具綱要索引(登錄器: object) -> dict[str, dict[str, Any]]:
    """捕捉目前 executor 所封存 release 的 exact 名稱與 parameters schema。

    參數：版本釘選工具登錄器。回傳：fresh name→schema 索引。
    例外：重名或工具外形失真時拋出驗證例外。副作用：不查詢其他 release。
    """
    結果: dict[str, dict[str, Any]] = {}
    for 項 in 登錄器.列出工具結構():  # type: ignore[union-attr]
        if type(項) is not dict or frozenset(項) != frozenset(("type", "function")):
            raise ValueError
        函數 = dict.__getitem__(項, "function")
        if dict.__getitem__(項, "type") != "function" or type(函數) is not dict:
            raise ValueError
        if frozenset(函數) != frozenset(("name", "description", "parameters")):
            raise ValueError
        名稱, 綱要 = dict.__getitem__(函數, "name"), dict.__getitem__(函數, "parameters")
        if not _是識別碼(名稱) or type(綱要) is not dict or 名稱 in 結果:
            raise ValueError
        Draft202012Validator.check_schema(綱要)
        結果[名稱] = 綱要
    return 結果


def _附加工具回合(訊息: list[dict[str, Any]], 回應: object,
                   登錄器: object, 呼叫們: list[dict[str, Any]]) -> None:
    """嚴格驗證並執行一回 captured-release calls，再附加 assistant/tool messages。

    參數：local transcript、模型回應、釘選登錄器及 detached calls。回傳：無。
    例外：name/id/arguments/schema、handler 或結果額度不符時固定工具失敗。
    副作用：每個完整預檢成功的 call 恰好執行一次；結果只加入 local transcript。
    """
    try:
        綱要們 = _工具綱要索引(登錄器)
        已看: set[str] = set()
        描述 = []
        for 呼叫 in 呼叫們:
            if type(呼叫) is not dict or frozenset(呼叫) != frozenset(("id", "type", "function")):
                raise ValueError
            識別, 類型, 函數 = (dict.__getitem__(呼叫, 鍵) for 鍵 in ("id", "type", "function"))
            if not _是識別碼(識別) or 識別 in 已看 or 類型 != "function" or type(函數) is not dict:
                raise ValueError
            if frozenset(函數) != frozenset(("name", "arguments")):
                raise ValueError
            名稱, 參數原文 = (dict.__getitem__(函數, 鍵) for 鍵 in ("name", "arguments"))
            if not _是識別碼(名稱) or 名稱 not in 綱要們 or type(參數原文) is not str:
                raise ValueError
            參數 = _解析模型JSON(參數原文, 32_768)
            if type(參數) is not dict:
                raise ValueError
            _建立綱要驗證器(綱要們[名稱]).validate(參數)
            已看.add(識別)
            描述.append((識別, 名稱, 參數))
        安全呼叫 = 複製JSON(呼叫們, 1_000_000)
        訊息.append({"role": "assistant", "content": object.__getattribute__(回應, "text"),
                   "tool_calls": 安全呼叫,
                   "finish_reason": object.__getattribute__(回應, "finish_reason")})
        結果總量 = 0
        for 識別, 名稱, 參數 in 描述:
            工具結果 = 登錄器.呼叫工具(名稱, 參數)  # type: ignore[union-attr]
            if type(工具結果) is not str:
                raise ValueError
            結果總量 += len(工具結果.encode("utf-8"))
            if 結果總量 > _最大工具結果位元組:
                raise ValueError
            結果物件 = _解析模型JSON(工具結果, _最大工具結果位元組)
            if type(結果物件) is not dict or dict.get(結果物件, "success") is not True:
                raise ValueError
            訊息.append({"role": "tool", "tool_call_id": 識別,
                       "name": 名稱, "content": 工具結果})
    except _控制流程:
        raise
    except BaseException as 錯誤:
        if type(錯誤) is 工具逾時 or type(錯誤) is 工具設定錯誤:
            raise
        raise 發布工具執行錯誤("發布工具執行失敗") from None


class 發布執行器:
    """只可由 factory 建立、且只執行建立點已釘選狀態的 runtime。"""

    __slots__ = ()

    def __new__(cls, *參數: object, **命名參數: object) -> 發布執行器:
        """拒絕 caller 直接建構或注入 trusted slots。"""
        cls = 參數 = 命名參數 = None
        raise 發布執行錯誤(_固定錯誤) from None

    def 執行(self, 請求: 發布執行請求):
        """非結構化呼叫一次；結構化輸出無效時恰好重試一次並只回傳最終回應。"""
        狀態 = 輸入原值 = 模型請求 = 回應 = 回應文字 = None
        失敗 = 兩次無效 = False
        try:
            if type(self) is not _發布執行器實作 or type(請求) is not 發布執行請求:
                raise ValueError
            with _執行器狀態鎖:
                狀態 = _執行器狀態.get(self)
            if type(狀態) is not tuple or len(狀態) != 5 or 狀態[0] is not _執行器封印:
                raise ValueError
            輸入原值 = object.__getattribute__(請求, "_input_json")
            模型請求 = _建立執行模型請求(狀態, 輸入原值, False)
            回應 = 狀態[3].產生回應(模型請求)
            if 狀態[4] is None:
                return 回應
            回應文字 = object.__getattribute__(回應, "text")
            if _模型輸出符合綱要(回應文字, 狀態[4]):
                return 回應
            回應 = 回應文字 = 模型請求 = None
            模型請求 = _建立執行模型請求(狀態, 輸入原值, True)
            回應 = 狀態[3].產生回應(模型請求)
            回應文字 = object.__getattribute__(回應, "text")
            if _模型輸出符合綱要(回應文字, 狀態[4]):
                return 回應
            兩次無效 = True
        except _控制流程:
            self = 請求 = 狀態 = 輸入原值 = 模型請求 = 回應 = 回應文字 = None
            raise
        except 發布執行錯誤:
            self = 請求 = 狀態 = 輸入原值 = 模型請求 = 回應 = 回應文字 = None
            raise
        except BaseException:
            self = 請求 = 狀態 = 輸入原值 = 模型請求 = 回應 = 回應文字 = None
            失敗 = True
        self = 請求 = 狀態 = 輸入原值 = 模型請求 = 回應 = 回應文字 = None
        if 兩次無效:
            raise 結構化輸出錯誤("模型輸出不符合回應綱要") from None
        if 失敗:
            raise 發布執行錯誤(_固定錯誤) from None
        raise AssertionError

    def 執行單次(self, 請求: 發布執行請求):
        """執行一個 INV attempt；允許有界工具 roundtrip，但不做 schema 修正重試。

        參數：``請求`` 是已脫離 JSON。回傳：沒有未執行 tool call 的最終模型回應。
        例外：工具外形、schema、handler 或額度失敗固定為 ``發布工具執行錯誤``；
        其他普通失敗固定化；控制流程 identity 保留。副作用：只呼叫 captured provider
        與 exact-release handler。
        """
        狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
        try:
            if type(self) is not _發布執行器實作 or type(請求) is not 發布執行請求:
                raise ValueError
            with _執行器狀態鎖:
                狀態 = _執行器狀態.get(self)
            if type(狀態) is not tuple or len(狀態) != 5 or 狀態[0] is not _執行器封印:
                raise ValueError
            輸入原文 = object.__getattribute__(請求, "_input_json")
            歷史原文 = object.__getattribute__(請求, "_歷史JSON")
            訊息 = _建立含歷史初始訊息(狀態, 輸入原文, 歷史原文)
            工具總數 = 0
            for 回合 in range(_最大工具回合 + 1):
                模型請求 = _建立對話模型請求(狀態, 訊息)
                回應 = 狀態[3].產生回應(模型請求)  # type: ignore[union-attr]
                呼叫們 = object.__getattribute__(回應, "tool_calls")
                if type(呼叫們) is not list:
                    raise ValueError
                if not 呼叫們:
                    return 回應
                if 回合 == _最大工具回合:
                    raise 發布工具執行錯誤("發布工具執行失敗")
                工具總數 += len(呼叫們)
                if not 1 <= len(呼叫們) <= _最大工具呼叫 or 工具總數 > _最大工具呼叫:
                    raise 發布工具執行錯誤("發布工具執行失敗")
                _附加工具回合(訊息, 回應, 狀態[2], 呼叫們)
        except _控制流程:
            self = 請求 = 狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
            raise
        except 模型逾時錯誤:
            self = 請求 = 狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
            raise
        except (工具逾時, 工具設定錯誤):
            狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
            del self, 請求
            raise
        except 發布工具執行錯誤:
            self = 請求 = 狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
            raise 發布工具執行錯誤("發布工具執行失敗") from None
        except 發布執行錯誤:
            self = 請求 = 狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
            raise
        except BaseException:
            self = 請求 = 狀態 = 輸入原文 = 訊息 = 模型請求 = 回應 = 呼叫們 = None
            raise 發布執行錯誤(_固定錯誤) from None


class _發布執行器實作(發布執行器):
    """module-private weak-referenceable implementation。"""

    __slots__ = ("__weakref__",)


class _方法代理:
    """只保存建立前已捕捉 callable 的短生命週期 adapter。"""

    __slots__ = ("__方法",)

    def __init__(self, 方法: object) -> None:
        object.__setattr__(self, "_方法代理__方法", 方法)

    def 取得發布執行快照(self, endpoint_version_id: str) -> object:
        """轉送 exact version lookup。"""
        try:
            return object.__getattribute__(self, "_方法代理__方法")(endpoint_version_id)  # type: ignore[operator]
        except _控制流程:
            self = endpoint_version_id = None
            raise

    def 載入服務帳戶上下文(self, service_account_id: str, endpoint_version_id: str, source: str) -> object:
        """轉送 exact service-account snapshot lookup。"""
        try:
            return object.__getattribute__(self, "_方法代理__方法")(service_account_id, endpoint_version_id, source)  # type: ignore[operator]
        except _控制流程:
            self = service_account_id = endpoint_version_id = source = None
            raise

    def 載入技能套件快照(self, endpoint_version_id: str, skill_bundle_hash: str,
                         清單參照: str, source: str) -> object:
        """轉送 exact immutable bundle lookup。"""
        try:
            return object.__getattribute__(self, "_方法代理__方法")(
                endpoint_version_id, skill_bundle_hash, 清單參照, source,
            )  # type: ignore[operator]
        except _控制流程:
            self = endpoint_version_id = skill_bundle_hash = 清單參照 = source = None
            raise

    def 取得工具修訂(self, 名稱: str, 修訂名稱: str) -> object:
        """轉送 exact revision lookup。"""
        try:
            return object.__getattribute__(self, "_方法代理__方法")(名稱, 修訂名稱)  # type: ignore[operator]
        except _控制流程:
            self = 名稱 = 修訂名稱 = None
            raise

    def 產生發布回應(self, **參數: Any) -> object:
        """轉送 factory 前捕捉的 model provider method。"""
        try:
            return object.__getattribute__(self, "_方法代理__方法")(**參數)  # type: ignore[operator]
        except _控制流程:
            self = 參數 = None
            raise


_執行器封印 = object()
_執行器狀態鎖 = threading.Lock()
_執行器狀態: weakref.WeakKeyDictionary[發布執行器, tuple[object, str, object, object, object]] = weakref.WeakKeyDictionary()


def 建立發布執行器(
    *, endpoint_version_id: str, service_account_id: str,
    發布快照提供者: object, 服務帳戶載入器: object, 技能套件載入器: object,
    工具修訂提供者: object, 模型供應商註冊表: dict[str, object],
) -> 發布執行器:
    """先捕捉所有 callback，再依 version→SA→bundle→tool→model 階段封存。"""
    版本方法 = 帳戶方法 = 套件方法 = 工具方法 = None
    模型描述 = 版本原值 = 版本 = 上下文 = 套件原值 = 套件 = None
    工具登錄器 = 模型轉接器 = 提示 = 執行器 = 結構 = None
    失敗 = False
    try:
        if not _是識別碼(endpoint_version_id) or not _是識別碼(service_account_id):
            raise ValueError
        if type(發布快照提供者) is 發布執行快照:
            版本原值 = 發布快照提供者
        else:
            版本方法 = getattr(發布快照提供者, "取得發布執行快照")
        帳戶方法 = getattr(服務帳戶載入器, "載入服務帳戶上下文")
        套件方法 = getattr(技能套件載入器, "載入技能套件快照")
        工具方法 = getattr(工具修訂提供者, "取得工具修訂")
        模型描述 = _捕捉模型註冊表(模型供應商註冊表)
        if 版本原值 is None:
            版本原值 = 版本方法(endpoint_version_id)  # type: ignore[operator]
        版本 = _重建發布快照(版本原值)
        if 版本.version_id != endpoint_version_id or 版本.service_account_id != service_account_id:
            raise ValueError
        結構 = object.__getattribute__(版本, "_response_schema_json")
        _預檢回應綱要(結構)
        上下文 = 載入服務帳戶上下文或失敗關閉(
            _方法代理(帳戶方法), service_account_id, endpoint_version_id,
        )
        _驗證交叉欄位(版本, 上下文)
        套件原值 = 套件方法(
            endpoint_version_id, 版本.skill_bundle_hash,
            版本.manifest_reference, _唯一來源,
        )  # type: ignore[operator]
        套件 = _重建技能套件(套件原值)
        if 套件.endpoint_version_id != endpoint_version_id or not hmac.compare_digest(
            套件.skill_bundle_hash, 版本.skill_bundle_hash,
        ):
            raise ValueError
        提示 = _建立提示(版本.system_prompt, 套件.files)
        if 版本.tool_handler_release == "testagent2-published-skills-v1":
            from ..生產技能工具 import 建立技能套件釘選工具登錄器
            工具登錄器 = 建立技能套件釘選工具登錄器(
                _方法代理(工具方法), 版本.tool_snapshot,
                tuple((檔案.path, bytes(檔案.content)) for 檔案 in 套件.files),
            )
        else:
            工具登錄器 = 建立版本釘選工具登錄器(
                _方法代理(工具方法), 版本.tool_snapshot,
            )
        模型轉接器 = 建立模型轉接器(dict(模型描述), 版本.model_config)
        執行器 = object.__new__(_發布執行器實作)
        with _執行器狀態鎖:
            _執行器狀態[執行器] = (_執行器封印, 提示, 工具登錄器, 模型轉接器, 結構)
        return 執行器
    except _控制流程:
        endpoint_version_id = service_account_id = 發布快照提供者 = 服務帳戶載入器 = None
        技能套件載入器 = 工具修訂提供者 = 模型供應商註冊表 = None
        版本方法 = 帳戶方法 = 套件方法 = 工具方法 = 模型描述 = None
        版本原值 = 版本 = 上下文 = 套件原值 = 套件 = 工具登錄器 = 模型轉接器 = 提示 = 執行器 = 結構 = None
        raise
    except BaseException:
        endpoint_version_id = service_account_id = 發布快照提供者 = 服務帳戶載入器 = None
        技能套件載入器 = 工具修訂提供者 = 模型供應商註冊表 = None
        版本方法 = 帳戶方法 = 套件方法 = 工具方法 = 模型描述 = None
        版本原值 = 版本 = 上下文 = 套件原值 = 套件 = 工具登錄器 = 模型轉接器 = 提示 = 執行器 = 結構 = None
        失敗 = True
    if 失敗:
        raise 發布執行錯誤(_固定錯誤) from None
    raise AssertionError


def _捕捉模型註冊表(註冊表: object) -> tuple[tuple[str, object], ...]:
    """在任何 provider callback 前捕捉 registry descriptor 與全部 model methods。"""
    描述 = 結果 = None
    try:
        if type(註冊表) is not dict:
            raise ValueError
        描述 = []
        for 名稱, 提供者 in dict.items(註冊表):
            if type(名稱) is not str:
                raise ValueError
            描述.append((名稱, 提供者))
        if len(描述) != len(註冊表):
            raise ValueError
        結果 = []
        for 名稱, 提供者 in 描述:
            方法 = getattr(提供者, "產生發布回應")
            if not callable(方法):
                raise ValueError
            結果.append((名稱, _方法代理(方法)))
        迭代器 = iter(dict.items(註冊表))
        for 名稱, 提供者 in 描述:
            現名, 現提供者 = next(迭代器)
            if 現名 != 名稱 or 現提供者 is not 提供者:
                raise ValueError
        try:
            next(迭代器)
            raise ValueError
        except StopIteration:
            return tuple(結果)
    except BaseException:
        註冊表 = 描述 = 結果 = 名稱 = 提供者 = 方法 = 迭代器 = 現名 = 現提供者 = None
        raise


def _重建發布快照(值: object) -> 發布執行快照:
    """從 exact untrusted DTO 捕捉全欄並重新執行 invariants。"""
    資料 = 結構 = 結果 = None
    try:
        if type(值) is not 發布執行快照:
            raise ValueError
        資料 = []
        for 名稱 in 發布執行快照.__dataclass_fields__:
            資料.append(object.__getattribute__(值, 名稱))
        結構 = None if 資料[9] is None else _解析正規JSON(資料[9], 500_000)
        結果 = 發布執行快照(
            endpoint_id=資料[0], version_id=資料[1], service_account_id=資料[2],
            system_prompt=資料[3], permission_snapshot_digest=資料[4],
            skill_bundle_hash=資料[5], tool_handler_release=資料[6],
            tool_snapshot=資料[7], model_config=資料[8], response_schema=結構,
            manifest_reference=資料[10],
        )
        return 結果
    except BaseException:
        值 = 資料 = 結構 = 結果 = 名稱 = None
        raise


def _重建技能套件(值: object) -> 技能套件快照:
    """從載入器的精確資料物件重建完整不可變技能套件。

    參數：``值`` 是不可信載入器回傳的 ``技能套件快照``。
    回傳：重新驗證並複製所有清單原始資料與檔案內容的新 ``技能套件快照``。
    例外：型別、欄位身分、摘要、順序或內容不符時傳出驗證例外。
    副作用：只配置不可變複本，不讀寫檔案系統、不呼叫載入器或模型。
    """
    資料 = 結果 = None
    try:
        if type(值) is not 技能套件快照:
            raise ValueError
        資料 = []
        for 名稱 in 技能套件快照.__dataclass_fields__:
            資料.append(object.__getattribute__(值, 名稱))
        結果 = 技能套件快照(
            endpoint_version_id=資料[0], skill_bundle_hash=資料[1],
            manifest_digest=資料[2], 清單原始資料=資料[3], files=資料[4],
        )
        for 索引, 名稱 in enumerate(技能套件快照.__dataclass_fields__):
            if object.__getattribute__(值, 名稱) is not 資料[索引]:
                raise ValueError
        return 結果
    except BaseException:
        值 = 資料 = 結果 = 名稱 = None
        raise


def _驗證交叉欄位(版本: 發布執行快照, 上下文: object) -> None:
    """在 bundle/tool/model callback 前比對所有 authority pins 與工具順序。"""
    工具名稱 = []
    try:
        for 項 in 版本.tool_snapshot:
            工具名稱.append(項.name)
        if (版本.service_account_id != 上下文.service_account_id
                or 版本.version_id != 上下文.endpoint_version_id
                or not hmac.compare_digest(版本.permission_snapshot_digest, 上下文.permission_snapshot_digest)
                or tuple(工具名稱) != 上下文.allowed_tools
                or not hmac.compare_digest(版本.skill_bundle_hash, 上下文.skill_bundle_hash)
                or 版本.tool_handler_release != 上下文.tool_handler_release):
            raise ValueError
    except BaseException:
        版本 = 上下文 = 工具名稱 = 項 = None
        raise


def _是提示檔案路徑(路徑: str) -> bool:
    """判斷 canonical bundle 路徑是否可插入模型提示。

    參數：``路徑`` 是已驗證的相對 POSIX 檔案路徑。回傳：根層舊契約或
    ``<skill-name>`` 下的 ``SKILL.md``、``references/**``、``templates/**``
    才為真。例外：不拋出例外。副作用：只配置短暫路徑元件。
    """
    部分 = 路徑.split("/")
    if 路徑 == "SKILL.md":
        return True
    if len(部分) >= 2 and 部分[0] in ("references", "templates"):
        return True
    if len(部分) == 2 and _是識別碼(部分[0]) and 部分[1] == "SKILL.md":
        return True
    return (
        len(部分) >= 3
        and _是識別碼(部分[0])
        and 部分[1] in ("references", "templates")
    )


def _建立提示(系統提示: str, 檔案們: tuple[技能套件檔案, ...]) -> str:
    """依 bundle 順序嵌入 allowlisted UTF-8 text。

    參數：``系統提示`` 是版本快照文字；``檔案們`` 是完整 hash-verified ordered
    bundle。回傳：系統提示及多技能說明、參考與模板合成文字。
    例外：提示超限或輸入狀態異常時傳出驗證例外；非 UTF-8 allowlisted 檔案略過。
    副作用：只配置提示；scripts、assets 與任意 nested 路徑不會進入提示。
    """
    區段 = 結果 = 文字 = None
    try:
        區段 = [系統提示]
        for 項 in 檔案們:
            if not _是提示檔案路徑(項.path):
                continue
            try:
                文字 = 項.content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            區段.append(f"## 技能套件：{項.path}\n{文字}")
        結果 = "\n\n".join(區段)
        if len(結果.encode()) > _最大提示位元組:
            raise ValueError
        return 結果
    except BaseException:
        系統提示 = 檔案們 = 區段 = 結果 = 文字 = 項 = None
        raise
