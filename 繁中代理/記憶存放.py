"""Hermes-like 內建記憶存放：MEMORY.md / USER.md、§ 分隔與 frozen snapshot。"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .提示詞組裝器 import 掃描提示注入內容

記憶分隔符 = "\n§\n"
@dataclass
class 記憶存放:
    """管理 profile-scoped 記憶檔。參數：Hermes home 與字數限制。返回值：記憶存放物件。"""
    hermes家目錄: Path
    記憶字數限制: int = 2200
    使用者字數限制: int = 1375
    記憶項目: list[str] = field(default_factory=list)
    使用者項目: list[str] = field(default_factory=list)
    快照: dict[str, str] = field(default_factory=lambda: {"memory": "", "user": ""})

    def 載入(self) -> None:
        """讀取 MEMORY.md / USER.md 並固定 prompt snapshot。參數：無。返回值：None。"""
        self.記憶項目 = self._讀檔("memory")
        self.使用者項目 = self._讀檔("user")
        self.快照 = {目標: self._格式化區塊(目標, self._清理快照(self._項目(目標), 檔名)) for 目標, 檔名 in [("memory", "MEMORY.md"), ("user", "USER.md")]}
    def 新增(self, 目標: str, 內容: str) -> dict[str, Any]:
        """新增記憶項目。參數：目標與內容。返回值：操作結果。"""
        內容 = 內容.strip()
        錯誤 = self._驗證內容(內容)
        if 錯誤:
            return 錯誤
        項目 = self._項目(目標)
        if 內容 in 項目:
            return self._成功(目標, "Entry already exists")
        檢查 = self._檢查容量(目標, [*項目, 內容])
        if 檢查:
            return 檢查
        項目.append(內容); self._寫檔(目標)
        return self._成功(目標, "Entry added")
    def 取代(self, 目標: str, 舊文字: str, 新內容: str) -> dict[str, Any]:
        """以 substring 找到唯一項目並取代。參數：目標、舊文字、新內容。返回值：操作結果。"""
        return self._更新(目標, 舊文字, 新內容.strip())
    def 移除(self, 目標: str, 舊文字: str) -> dict[str, Any]:
        """以 substring 找到唯一項目並移除。參數：目標與舊文字。返回值：操作結果。"""
        return self._更新(目標, 舊文字, None)

    def 格式化給系統提示(self, 目標: str) -> str:
        """回傳 frozen snapshot，不讀取 live 狀態。參數：目標。返回值：snapshot 文字。"""
        return self.快照.get(目標, "")

    def _更新(self, 目標: str, 舊文字: str, 新內容: str | None) -> dict[str, Any]:
        符合 = [i for i, 值 in enumerate(self._項目(目標)) if 舊文字.strip() and 舊文字 in 值]
        if len(符合) != 1:
            return {"success": False, "error": "找不到唯一符合項目", "matches": len(符合)}
        項目 = self._項目(目標)
        if 新內容 is None:
            項目.pop(符合[0])
        else:
            錯誤 = self._驗證內容(新內容)
            if 錯誤:
                return 錯誤
            測試項目 = [*項目]; 測試項目[符合[0]] = 新內容
            檢查 = self._檢查容量(目標, 測試項目)
            if 檢查:
                return 檢查
            項目[符合[0]] = 新內容
        self._寫檔(目標)
        return self._成功(目標, "Entry removed" if 新內容 is None else "Entry replaced")

    def _驗證內容(self, 內容: str) -> dict[str, Any] | None:
        if not 內容:
            return {"success": False, "error": "content 不可為空"}
        掃描結果 = 掃描提示注入內容(內容, "memory")
        return None if 掃描結果 == 內容 else {"success": False, "error": 掃描結果}

    def _路徑(self, 目標: str) -> Path:
        return self.hermes家目錄 / "memories" / ("USER.md" if 目標 == "user" else "MEMORY.md")
    def _項目(self, 目標: str) -> list[str]:
        return self.使用者項目 if 目標 == "user" else self.記憶項目
    def _限制(self, 目標: str) -> int:
        return self.使用者字數限制 if 目標 == "user" else self.記憶字數限制
    def _讀檔(self, 目標: str) -> list[str]:
        路徑 = self._路徑(目標)
        if not 路徑.exists():
            return []
        return list(dict.fromkeys(項.strip() for 項 in 路徑.read_text(encoding="utf-8").split(記憶分隔符) if 項.strip()))
    def _寫檔(self, 目標: str) -> None:
        路徑 = self._路徑(目標); 路徑.parent.mkdir(parents=True, exist_ok=True)
        路徑.write_text(記憶分隔符.join(self._項目(目標)), encoding="utf-8")
    def _檢查容量(self, 目標: str, 項目: list[str]) -> dict[str, Any] | None:
        總長 = len(記憶分隔符.join(項目)); 限制 = self._限制(目標)
        return None if 總長 <= 限制 else {"success": False, "error": f"Memory at {總長}/{限制} chars", "current_entries": self._項目(目標)}
    def _清理快照(self, 項目: list[str], 檔名: str) -> list[str]:
        return [值 if 掃描提示注入內容(值, 檔名) == 值 else f"[BLOCKED: {檔名} entry contained threat pattern.]" for 值 in 項目]
    def _格式化區塊(self, 目標: str, 項目: list[str]) -> str:
        if not 項目:
            return ""
        內容 = 記憶分隔符.join(項目); 標題 = "USER PROFILE (who the user is)" if 目標 == "user" else "MEMORY (your personal notes)"
        return f"{'═' * 46}\n{標題} [{len(內容)}/{self._限制(目標)} chars]\n{'═' * 46}\n{內容}"
    def _成功(self, 目標: str, 訊息: str) -> dict[str, Any]:
        return {"success": True, "target": 目標, "entries": self._項目(目標), "message": 訊息}
