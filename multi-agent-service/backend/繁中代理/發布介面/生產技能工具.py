"""固定 Published skills release 與 bundle-bound request-local 工具登錄器。

公開 factory 接受 exact installed release provider、版本工具快照與已重建 immutable
``技能套件快照``。它先驗證 installed metadata/revision/digest，再建立 handlers 直接捕捉
bundle bytes/index 的 fresh registry；不使用 global、ContextVar、thread-local、路徑或資料庫。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..工具 import 工具定義
from ..工具集.管理部_bigquery import 管理部文件搜尋
from ..工具集.釐清詢問 import 建立釐清處理器
from .執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from .執行期.工具版本庫 import (
    工具修訂提供者, 工具快照項目, 工具版本庫,
    建立版本釘選工具登錄器, 版本釘選工具登錄器,
)

技能工具發布名稱 = "testagent2-published-skills-v1"
技能清單修訂 = "skills_list@bundle-v1"
技能檢視修訂 = "skill_view@bundle-v1"
管理部搜尋修訂 = "administrative_search@published-v1"
釐清修訂 = "clarify@published-v1"
_名稱格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_結果最大位元組 = 65_536
_拒絕訊息 = "技能套件工具拒絕存取"
_清單說明 = "List skills contained in this endpoint version's verified immutable bundle."
_檢視說明 = "Read SKILL.md, references, or templates from this endpoint version's verified bundle."
_管理部搜尋說明 = (
    "Search administrative department documents in BigQuery. Defaults to hybrid retrieval: "
    "semantic search over documents.embedding plus keyword search over title, content, "
    "category, and headings. Images are returned only as supplementary context for matched documents."
)
_釐清說明 = (
    "Ask the user a question when you need clarification, feedback, or a decision before "
    "proceeding. In one-shot Published API calls this returns a no-interactive-channel "
    "guidance result so the model can make a reasonable assumption and continue."
)
_清單結構 = {
    "type": "object",
    "properties": {"category": {"type": "string"}},
    "additionalProperties": False,
}
_檢視結構 = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
        "file_path": {"type": "string", "minLength": 1, "maxLength": 512},
    },
    "required": ["name"],
    "additionalProperties": False,
}
_管理部搜尋結構 = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "The administrative question or exact text to search for."},
        "search_mode": {
            "type": "string",
            "enum": ["hybrid", "semantic", "keyword"],
            "description": (
                "Retrieval mode. Use hybrid by default. Use keyword only for explicit exact-field "
                "or exact-text searches. Use semantic only when explicitly requested or for debugging."
            ),
            "default": "hybrid",
        },
        "include_images": {
            "type": "boolean",
            "description": "Whether to fetch image metadata for matched documents.",
            "default": True,
        },
        "filters": {
            "type": "object",
            "description": "Optional exact filters applied to documents.",
            "properties": {
                "category": {"type": "string", "description": "Exact document category filter."},
                "version": {"type": "string", "description": "Exact document version filter."},
                "source_file": {"type": "string", "description": "Exact source file filter."},
            },
            "additionalProperties": False,
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of merged document results to return.",
            "minimum": 1,
            "maximum": 20,
            "default": 5,
        },
        "hybrid_weights": {
            "type": "object",
            "description": "Optional hybrid scoring weights. Do not set unless explicitly requested.",
            "properties": {
                "semantic": {"type": "number", "description": "Semantic retrieval weight.", "minimum": 0},
                "keyword": {"type": "number", "description": "Keyword retrieval weight.", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    "required": ["query"],
}
_釐清結構 = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "The question to present to the user."},
        "choices": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
            "description": "Up to 4 answer choices. Omit this parameter entirely to ask an open-ended question.",
        },
    },
    "required": ["question"],
}
_發布工具規格 = (
    ("skills_list", 技能清單修訂, _清單說明, _清單結構),
    ("skill_view", 技能檢視修訂, _檢視說明, _檢視結構),
    ("administrative_search", 管理部搜尋修訂, _管理部搜尋說明, _管理部搜尋結構),
    ("clarify", 釐清修訂, _釐清說明, _釐清結構),
)


def _拒絕(_參數: dict[str, object]) -> dict[str, object]:
    """Installed release handler 不持有 bundle；canonical executor 必須改建 closure registry。"""
    raise ValueError(_拒絕訊息) from None


def 建立技能工具發布描述() -> 工具發布描述:
    """建立 application-owned 固定 release metadata；不讀環境或 bundle root。"""
    return 工具發布描述(
        技能工具發布名稱,
        (
            工具發布註冊(技能清單修訂, 工具定義(
                "skills_list", _清單說明, _清單結構, _拒絕,
            )),
            工具發布註冊(技能檢視修訂, 工具定義(
                "skill_view", _檢視說明, _檢視結構, _拒絕,
            )),
            工具發布註冊(管理部搜尋修訂, 工具定義(
                "administrative_search", _管理部搜尋說明, _管理部搜尋結構, 管理部文件搜尋,
            )),
            工具發布註冊(釐清修訂, 工具定義(
                "clarify", _釐清說明, _釐清結構, 建立釐清處理器(),
            )),
        ),
    )


def _是允許路徑(相對路徑: str) -> bool:
    """只允許 SKILL.md、references/** 與 templates/** canonical 相對路徑。"""
    部分 = 相對路徑.split("/")
    return 部分 == ["SKILL.md"] or (
        len(部分) >= 2
        and 部分[0] in {"references", "templates"}
        and all(項 not in {"", ".", ".."} and "\\" not in 項 for 項 in 部分[1:])
    )


def _驗證有界輸出(結果: dict[str, object]) -> dict[str, object]:
    """以實際 JSON UTF-8 wire size 驗證完整結果，不只計算 content。"""
    if len(json.dumps(
        結果, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")) > _結果最大位元組:
        raise ValueError(_拒絕訊息) from None
    return 結果


def 建立技能套件釘選工具登錄器(
    已安裝發布提供者: 工具修訂提供者,
    版本工具快照: tuple[工具快照項目, ...],
    已驗證技能檔案: tuple[tuple[str, bytes], ...],
) -> 版本釘選工具登錄器:
    """驗證 exact installed pins，建立只捕捉本 request bundle bytes 的 fresh registry。

    參數依序是 exact release provider、version-owned ordered snapshot、executor 已重建快照。
    回傳 registry 的 handlers 只封閉 immutable ``bytes`` index。任何 metadata、revision、
    digest、schema、bundle 外形、UTF-8、路徑或輸出額度異常都 fail closed。
    """
    if type(已驗證技能檔案) is not tuple or type(版本工具快照) is not tuple:
        raise ValueError(_拒絕訊息) from None

    # 先以既有 resolver 驗證 provider 回傳的 metadata/revision/digest。
    已安裝 = 建立版本釘選工具登錄器(已安裝發布提供者, 版本工具快照)
    規格表 = {名稱: (修訂, 說明, 結構) for 名稱, 修訂, 說明, 結構 in _發布工具規格}
    順序 = [名稱 for 名稱, _修訂, _說明, _結構 in _發布工具規格]
    快照名稱 = [項.name for 項 in 版本工具快照]
    if (any(名稱 not in 規格表 for 名稱 in 快照名稱)
            or len(快照名稱) != len(set(快照名稱))
            or 快照名稱 != [名稱 for 名稱 in 順序 if 名稱 in set(快照名稱)]
            or any(項.revision != 規格表[項.name][0] for 項 in 版本工具快照)):
        raise ValueError(_拒絕訊息) from None
    預期結構 = []
    for 名稱 in 快照名稱:
        _修訂, 說明, 結構 = 規格表[名稱]
        預期結構.append({"type": "function", "function": {
            "name": 名稱, "description": 說明, "parameters": 結構,
        }})
    if 已安裝.列出工具結構() != 預期結構:
        raise ValueError(_拒絕訊息) from None

    索引: dict[str, bytes] = {}
    技能名稱: list[str] = []
    for 檔案 in 已驗證技能檔案:
        if type(檔案) is not tuple or len(檔案) != 2 or type(檔案[0]) is not str or type(檔案[1]) is not bytes:
            raise ValueError(_拒絕訊息) from None
        路徑, 內容位元 = 檔案
        if 路徑 in 索引:
            raise ValueError(_拒絕訊息) from None
        索引[路徑] = bytes(內容位元)
        部分 = 路徑.split("/")
        if len(部分) == 2 and 部分[1] == "SKILL.md" and _名稱格式.fullmatch(部分[0]):
            技能名稱.append(部分[0])
    if len(技能名稱) != len(set(技能名稱)):
        raise ValueError(_拒絕訊息) from None
    固定技能名稱 = tuple(技能名稱)

    def 列出技能(參數: dict[str, Any]) -> dict[str, object]:
        """列出本次 immutable bundle 的技能；分類查詢不向外部補讀。"""
        if type(參數) is not dict or set(參數) - {"category"}:
            raise ValueError(_拒絕訊息) from None
        分類 = 參數.get("category")
        if 分類 is not None and type(分類) is not str:
            raise ValueError(_拒絕訊息) from None
        # Bundle manifest 沒有可驗證 category authority；filter 不得查詢外部來源。
        名稱們 = () if 分類 is not None else 固定技能名稱
        結果: dict[str, object] = {"skills": [{"name": 名稱} for 名稱 in 名稱們]}
        return _驗證有界輸出(結果)

    def 檢視技能(參數: dict[str, Any]) -> dict[str, object]:
        """從本次 immutable bundle 讀取單一受允許且有界的 UTF-8 檔案。"""
        if type(參數) is not dict or not 1 <= len(參數) <= 2 or set(參數) - {"name", "file_path"}:
            raise ValueError(_拒絕訊息) from None
        名稱 = 參數.get("name")
        相對路徑 = 參數.get("file_path", "SKILL.md")
        if (type(名稱) is not str or _名稱格式.fullmatch(名稱) is None
                or type(相對路徑) is not str or not _是允許路徑(相對路徑)):
            raise ValueError(_拒絕訊息) from None
        內容位元 = 索引.get(f"{名稱}/{相對路徑}")
        if 內容位元 is None or len(內容位元) > _結果最大位元組:
            raise ValueError(_拒絕訊息) from None
        try:
            內容 = 內容位元.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ValueError(_拒絕訊息) from None
        結果: dict[str, object] = {
            "name": 名稱, "file_path": 相對路徑, "content": 內容,
        }
        return _驗證有界輸出(結果)

    # 用相同 revision metadata 重算摘要；傳入 version snapshot 使任何 drift 再次 fail closed。
    本次工具庫 = 工具版本庫()
    本次工具庫.登錄修訂(技能清單修訂, 工具定義(
        "skills_list", _清單說明, _清單結構, 列出技能,
    ))
    本次工具庫.登錄修訂(技能檢視修訂, 工具定義(
        "skill_view", _檢視說明, _檢視結構, 檢視技能,
    ))
    本次工具庫.登錄修訂(管理部搜尋修訂, 工具定義(
        "administrative_search", _管理部搜尋說明, _管理部搜尋結構, 管理部文件搜尋,
    ))
    本次工具庫.登錄修訂(釐清修訂, 工具定義(
        "clarify", _釐清說明, _釐清結構, 建立釐清處理器(),
    ))
    return 建立版本釘選工具登錄器(本次工具庫, 版本工具快照)


def 安裝生產技能工具(工具庫: object) -> None:
    """在 lifespan startup 將唯一固定 Published skills release 登錄一次。"""
    工具庫.登錄發布(建立技能工具發布描述())  # type: ignore[attr-defined]


__all__ = (
    "技能工具發布名稱", "技能清單修訂", "技能檢視修訂", "管理部搜尋修訂", "釐清修訂",
    "建立技能工具發布描述", "建立技能套件釘選工具登錄器", "安裝生產技能工具",
)
