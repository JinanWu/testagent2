"""Published Runtime 的版本快照、immutable 技能套件與 snapshot-only 執行器。"""

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

from .工具版本庫 import 工具快照項目
from .模型契約 import 模型設定快照, 複製JSON, 重建設定

_識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_雜湊 = re.compile(r"[0-9a-f]{64}")
_固定錯誤 = "發布執行期不可用"
_唯一來源 = "endpoint_version_snapshot"
_最大檔案數 = 256
_最大套件位元組 = 4_000_000
_最大提示位元組 = 1_000_000
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_綱要修正訊息 = "輸出未符合回應綱要；請只回傳符合綱要的 JSON。"

class 發布執行錯誤(RuntimeError):
    """版本、套件或組裝邊界失敗時不洩漏內容的固定錯誤。"""

class 結構化輸出錯誤(發布執行錯誤):
    """兩次模型輸出皆不符合版本釘選綱要。"""

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
    """一個 canonical POSIX path 與 immutable bytes 的 hash-addressed 項目。"""

    path: str
    sha256: str
    content: bytes

    def __init__(self, *, path: str, sha256: str, content: bytes) -> None:
        """驗證 exact scalar；內容與摘要不符一律固定拒絕。"""
        try:
            if type(self) is not 技能套件檔案 or not _是套件路徑(path):
                raise ValueError
            if type(sha256) is not str or _雜湊.fullmatch(sha256) is None:
                raise ValueError
            if type(content) is not bytes or len(content) > _最大套件位元組:
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
    """loader 回傳的 ordered、text/binary 完整 immutable bundle。"""

    endpoint_version_id: str
    skill_bundle_hash: str
    manifest_digest: str
    files: tuple[技能套件檔案, ...]

    def __init__(
        self, *, endpoint_version_id: str, skill_bundle_hash: str,
        manifest_digest: str, files: tuple[技能套件檔案, ...],
    ) -> None:
        """重建每個檔案並驗證排序、資源上限與 canonical manifest digest。"""
        重建檔案 = None
        try:
            if not _是識別碼(endpoint_version_id) or type(files) is not tuple:
                raise ValueError
            重建檔案 = _重建套件檔案(files)
            計算值 = 計算技能套件雜湊(重建檔案)
            if type(skill_bundle_hash) is not str or type(manifest_digest) is not str:
                raise ValueError
            if not hmac.compare_digest(計算值, skill_bundle_hash):
                raise ValueError
            if not hmac.compare_digest(計算值, manifest_digest):
                raise ValueError
            object.__setattr__(self, "endpoint_version_id", endpoint_version_id)
            object.__setattr__(self, "skill_bundle_hash", skill_bundle_hash)
            object.__setattr__(self, "manifest_digest", manifest_digest)
            object.__setattr__(self, "files", 重建檔案)
        except _控制流程:
            self = endpoint_version_id = skill_bundle_hash = manifest_digest = files = 重建檔案 = None
            raise
        except BaseException:
            self = endpoint_version_id = skill_bundle_hash = manifest_digest = files = 重建檔案 = None
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
            for 值 in (endpoint_id, version_id, service_account_id, tool_handler_release, manifest_reference):
                if not _是識別碼(值):
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

    def __init__(self, input: Any) -> None:
        """立即 bounded detach caller JSON，拒絕 subclass、循環與非有限數。"""
        結果 = None
        失敗 = False
        try:
            結果 = 複製JSON(input, 500_000)
            object.__setattr__(self, "_input_json", _建立正規JSON(結果))
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
    """重建 canonical ordered manifest 後計算整體 SHA-256。"""
    重建檔案 = 項目們 = 項 = 路徑 = 摘要 = 內容 = 清單 = 原文 = 原始位元 = 結果 = None
    失敗 = False
    try:
        重建檔案 = _重建套件檔案(files)
        項目們 = []
        for 項 in 重建檔案:
            路徑 = object.__getattribute__(項, "path")
            摘要 = object.__getattribute__(項, "sha256")
            內容 = object.__getattribute__(項, "content")
            項目們.append({"path": 路徑, "sha256": 摘要, "size": len(內容)})
        清單 = {"version": 1, "files": 項目們}
        原文 = json.dumps(清單, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
        原始位元 = 原文.encode("utf-8")
        結果 = hashlib.sha256(原始位元).hexdigest()
        return 結果
    except _控制流程:
        files = 重建檔案 = 項目們 = 項 = 路徑 = 摘要 = 內容 = 清單 = 原文 = 原始位元 = 結果 = None
        raise
    except BaseException:
        files = 重建檔案 = 項目們 = 項 = 路徑 = 摘要 = 內容 = 清單 = 原文 = 原始位元 = 結果 = None
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
    """exact tuple 全量重建，拒絕重複、失序與超限。"""
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
            if 總量 > _最大套件位元組 or 重建.path in 已看:
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
