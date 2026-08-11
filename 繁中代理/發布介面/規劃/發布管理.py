"""協調權威草稿、技能套件、加密憑證與發布資料庫交易。

參數：公開服務建構時接收共享草稿 aggregate、owner resolver 與真實發布 primitives。
回傳：初始發布成功只回傳一次明文金鑰；固定失敗回傳管理錯誤分類。
例外：控制流程例外維持物件身分傳出；一般相依失敗固定關閉。
副作用：可查權威、發布不可變套件、提交 SQLite 圖形或隔離孤兒套件。
"""
from __future__ import annotations

import base64
import math
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

from ..憑證.加密 import AESGCM憑證封套
from ..技能套件.協調器 import 技能套件協調器
from ..技能套件.發布器 import (
    套件發布收據, 套件耐久性未知, 已驗證技能套件清單, 技能套件發布器,
)
from ..路由.規劃發布 import (
    發布確認, 端點發布結果 as 路由端點發布結果, 管理操作錯誤, 版本建立結果,
)
from .擁有者能力 import 已解析發布能力, 擁有者能力轉接器, 擁有者能力錯誤
from .版本服務 import (
    SQLite版本配置服務, 下一版本準備, 版本存取錯誤, 版本配置提交判定,
)
from .端點發布 import (
    SQLite端點發布服務, 已準備初始憑證, 已準備發布識別, 發布版本快照,
    端點發布輸入錯誤,
)
from .綱要 import 發布值確認, 草稿存取錯誤, 規劃服務, 規劃草稿
from .權限協調 import 授權選擇錯誤, 能力摘要, 權限協調器

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_版本配置鍵 = frozenset({
    "original_requirement_text", "system_prompt", "model_config_snapshot",
    "retry_policy", "input_schema", "response_schema",
})


class 發布管理協調器:
    """完成初始發布的單一 production 協調邊界。

    參數：建構依賴都由 production composition root 明確注入；版本 primitive 可先供未來方法使用。
    回傳：``原子發布`` 回傳路由端點發布結果或固定管理操作錯誤。
    例外：控制流程例外原樣傳出；建構與一般執行失敗不揭露相依細節。
    副作用：發布時依固定順序存取草稿、權威、密碼學、檔案系統與 SQLite。
    """

    def __init__(
        self, *, 草稿服務: 規劃服務, 擁有者解析器: 擁有者能力轉接器,
        套件發布器物件: 技能套件發布器, 套件協調器物件: 技能套件協調器,
        端點發布服務: SQLite端點發布服務, 憑證封套: AESGCM憑證封套,
        版本配置服務: SQLite版本配置服務 | None = None,
        模型設定: dict[str, Any] | None = None, 重試政策: dict[str, Any] | None = None,
        憑證存續秒數: float = 31_536_000, 時鐘: Callable[[], float] = __import__("time").time,
        識別碼產生器: Callable[[str], str] | None = None,
        隨機位元組: Callable[[int], bytes] = os.urandom,
    ) -> None:
        """保存已明確注入的協調依賴並驗證純量設定。

        參數：接收共享服務、P04／P05、AES-GCM、時間、識別與熵來源。
        回傳：無。
        例外：型別、callback 或存續時間不符時拋出 ``ValueError``。
        副作用：只保存參照，不查權威、不產生識別、不建立金鑰或開啟資源。
        """
        if (
            type(草稿服務) is not 規劃服務 or type(擁有者解析器) is not 擁有者能力轉接器
            or type(套件發布器物件) is not 技能套件發布器
            or type(套件協調器物件) is not 技能套件協調器
            or type(端點發布服務) is not SQLite端點發布服務
            or type(憑證封套) is not AESGCM憑證封套
            or (版本配置服務 is not None and type(版本配置服務) is not SQLite版本配置服務)
            or type(憑證存續秒數) not in (int, float) or not math.isfinite(憑證存續秒數)
            or 憑證存續秒數 <= 0 or not callable(時鐘) or not callable(隨機位元組)
            or (識別碼產生器 is not None and not callable(識別碼產生器))
            or type(模型設定 if 模型設定 is not None else {}) is not dict
            or type(重試政策 if 重試政策 is not None else {}) is not dict
        ):
            raise ValueError("發布管理設定無效") from None
        self._草稿服務 = 草稿服務
        self._擁有者解析器 = 擁有者解析器
        self._套件發布器 = 套件發布器物件
        self._套件協調器 = 套件協調器物件
        self._端點發布服務 = 端點發布服務
        self._憑證封套 = 憑證封套
        self._版本配置服務 = 版本配置服務
        self._模型設定 = dict(模型設定 or {"model": "published-default", "temperature": 0})
        self._重試政策 = dict(重試政策 or {"max_attempts": 1})
        self._憑證存續秒數 = float(憑證存續秒數)
        self._時鐘 = 時鐘
        self._識別碼產生器 = 識別碼產生器 or (lambda 前綴: f"{前綴}-{uuid.uuid4().hex}")
        self._隨機位元組 = 隨機位元組

    def 原子發布(
        self, *, 擁有者使用者識別碼: str, 確認: 發布確認,
    ) -> 路由端點發布結果 | 管理操作錯誤:
        """依固定順序建立 v1 圖形，成功時只揭露一次初始 API 金鑰。

        參數：擁有者識別碼來自正規工作階段；確認只作為草稿識別、短名與顯示值確認。
        回傳：提交成功回路由收據；任何一般拒絕回固定 ``管理操作錯誤``。
        例外：控制流程例外在盡力隔離已發布套件後維持原物件傳出。
        副作用：可確認共享草稿、產生一次金鑰、發布套件、提交 P04 或標記孤兒。
        """
        收據: 套件發布收據 | None = None
        新金鑰 = None
        明文: str | None = None
        熵 = bytearray()
        明文緩衝 = bytearray()
        try:
            if type(確認) is not 發布確認 or type(擁有者使用者識別碼) is not str:
                return 管理操作錯誤("invalid")
            現在 = self._讀取時間()
            草稿 = self._草稿服務.讀取草稿(
                擁有者使用者識別碼, 確認.草稿識別碼, 現在=現在,
            )
            if type(草稿) is not 規劃草稿 or type(草稿.能力摘要) is not 能力摘要:
                return 管理操作錯誤("draft_not_found")
            摘要 = 草稿.能力摘要
            能力 = self._擁有者解析器.解析發布能力(
                擁有者使用者識別碼, 摘要,
            )
            綱要 = 草稿.綱要
            if not self._確認顯示值(確認.配置, 綱要):
                return 管理操作錯誤("invalid")
            self._草稿服務.確認發布值(
                擁有者使用者識別碼, 確認.草稿識別碼, slug=確認.短名,
                response_schema=綱要["response_schema"], docs=綱要["human_docs"],
                endpoint_limit=綱要["rate_limit"]["endpoint_per_minute"],
                credential_limit=綱要["rate_limit"]["credential_per_minute"], 現在=現在,
            )
            草稿 = self._草稿服務.讀取已確認草稿(
                擁有者使用者識別碼, 確認.草稿識別碼, 現在=現在,
            )
            if type(草稿.發布確認) is not 發布值確認:
                return 管理操作錯誤("draft_not_found")
            發布值 = 草稿.發布確認
            識別 = self._配置識別(現在)
            熵.extend(self._取得熵())
            明文緩衝.extend(b"pk_" + base64.urlsafe_b64encode(熵).rstrip(b"="))
            新金鑰 = self._憑證封套.加密(
                明文緩衝.decode("ascii"), 識別.endpoint_id, 識別.credential_id,
            )
            明文 = 新金鑰.api_key
            憑證 = 已準備初始憑證(
                "初始憑證", "呼叫已發布端點", 新金鑰.envelope.key_version,
                新金鑰.envelope.nonce, 新金鑰.envelope.ciphertext, 新金鑰.key_hash,
                新金鑰.key_prefix, 新金鑰.key_last4, 現在 + self._憑證存續秒數, [],
                發布值.credential_limit, 擁有者使用者識別碼,
            )
            self._建立快照(草稿, 能力, 識別, None)
            收據 = self._套件發布器.發布(
                套件識別碼=識別.套件識別碼, 端點識別碼=識別.endpoint_id,
                端點版本識別碼=識別.version_id, 版本號碼=1, 建立時間=現在,
                建立者識別碼=擁有者使用者識別碼, 技能表=能力.建立技能表(),
            )
            self._驗證發布清單(收據, 草稿, 能力)
            快照 = self._建立快照(草稿, 能力, 識別, 收據)
            圖形 = self._端點發布服務.發布已準備圖形(
                擁有者使用者識別碼, 草稿, 快照, 憑證, 識別, 收據,
                請求識別碼=None,
                寫入前權威確認=lambda: self._擁有者解析器.解析發布能力(
                    擁有者使用者識別碼, 摘要,
                ),
            )
            新金鑰 = None
            成功結果 = 路由端點發布結果(
                圖形.endpoint_id, 圖形.version_id, 圖形.version_number, 圖形.status, 明文,
            )
            明文 = None
            return 成功結果
        except 套件耐久性未知 as 主要:
            收據 = 主要.收據
            新金鑰 = 明文 = None
            self._清空敏感緩衝(熵, 明文緩衝)
            self._清除秘密框架(主要)
            self._盡力標記孤兒(收據)
            return 管理操作錯誤("internal")
        except _控制流程 as 主要:
            新金鑰 = 明文 = None
            self._清空敏感緩衝(熵, 明文緩衝)
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
            raise
        except 草稿存取錯誤 as 主要:
            新金鑰 = 明文 = None
            self._清空敏感緩衝(熵, 明文緩衝)
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
            return 管理操作錯誤("draft_not_found")
        except BaseException as 主要:
            新金鑰 = 明文 = None
            self._清空敏感緩衝(熵, 明文緩衝)
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
            return 管理操作錯誤("internal")
        finally:
            for 索引 in range(len(熵)):
                熵[索引] = 0
            for 索引 in range(len(明文緩衝)):
                明文緩衝[索引] = 0
            明文 = None
            新金鑰 = None

    def 原子建立並切換版本(
        self, *, 擁有者使用者識別碼: str, 端點識別碼: str, 配置: dict[str, Any],
    ) -> 版本建立結果 | 管理操作錯誤:
        """以 fresh owner authority 發布下一版套件並由 P05 原子切換 current。

        參數：擁有者來自 canonical session；端點來自受限路徑；配置只可覆寫版本內容欄位。
        回傳：全部提交成功回 ``版本建立結果``；一般拒絕只回固定 ``管理操作錯誤``。
        例外：三種控制流程例外保持同一物件傳出；只有資料庫尚未耐久提交時隔離套件。
        副作用：唯讀準備 current、重查權威、發布新套件、重驗清單、執行 P05 或隔離孤兒。
        """
        收據: 套件發布收據 | None = None
        準備: 下一版本準備 | None = None
        版本識別碼: str | None = None
        稽核識別碼: str | None = None
        現在: float | None = None
        try:
            if (
                self._版本配置服務 is None or type(擁有者使用者識別碼) is not str
                or type(端點識別碼) is not str or type(配置) is not dict
                or set(配置) != _版本配置鍵
            ):
                return 管理操作錯誤("invalid")
            準備 = self._版本配置服務.準備下一版本(
                擁有者使用者識別碼, 端點識別碼,
            )
            摘要 = 權限協調器(self._擁有者解析器).建立能力摘要(
                擁有者使用者識別碼,
                tuple(準備.current_snapshot.allowed_skills),
                tuple(準備.current_snapshot.allowed_tools),
            )
            能力 = self._擁有者解析器.解析發布能力(擁有者使用者識別碼, 摘要)
            現在 = self._讀取時間()
            版本識別碼 = self._識別碼產生器("version")
            套件識別碼 = self._識別碼產生器("bundle")
            稽核識別碼 = self._識別碼產生器("audit")
            if len({版本識別碼, 套件識別碼, 稽核識別碼, 端點識別碼}) != 4:
                raise ValueError("版本識別碰撞")
            self._建立下一版快照(準備, 配置, 摘要, 能力, 套件識別碼, None)
            收據 = self._套件發布器.發布(
                套件識別碼=套件識別碼, 端點識別碼=端點識別碼,
                端點版本識別碼=版本識別碼, 版本號碼=準備.next_version_number,
                建立時間=現在, 建立者識別碼=擁有者使用者識別碼,
                技能表=能力.建立技能表(),
            )
            投影 = self._驗證下一版發布清單(
                收據, 端點識別碼, 版本識別碼, 準備.next_version_number, 摘要, 能力,
            )
            快照 = self._建立下一版快照(
                準備, 配置, 摘要, 能力, 套件識別碼, 收據,
            )
            結果 = self._版本配置服務.配置並啟用(
                執行者使用者識別碼=擁有者使用者識別碼, 執行者類型="user",
                端點識別碼=端點識別碼, 已準備快照=快照,
                已準備版本識別碼=版本識別碼, 已準備時間=現在,
                套件收據=收據, 稽核識別碼=稽核識別碼, 請求識別碼=None,
                套件驗證器=lambda manifest, version_id, endpoint_id: self._投影符合準備(
                    manifest, 投影, version_id, endpoint_id,
                ),
            )
            return 版本建立結果(
                結果.endpoint_id, 結果.version_id, 結果.version_number,
                結果.version_id, 結果.schema_changed,
            )
        except 套件耐久性未知 as 主要:
            收據 = 主要.收據
            self._清除秘密框架(主要)
            self._盡力標記孤兒(收據)
            return 管理操作錯誤("internal")
        except _控制流程 as 主要:
            self._清除秘密框架(主要)
            if (
                收據 is not None and type(準備) is 下一版本準備
                and type(版本識別碼) is str and type(稽核識別碼) is str
                and type(現在) is float
            ):
                self._依提交判定處理控制流程套件(
                    擁有者使用者識別碼=擁有者使用者識別碼,
                    端點識別碼=端點識別碼, 版本識別碼=版本識別碼,
                    版本號碼=準備.next_version_number, 收據=收據,
                    稽核識別碼=稽核識別碼, 建立時間=現在,
                )
            raise
        except 版本存取錯誤 as 主要:
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
            return 管理操作錯誤("forbidden")
        except (擁有者能力錯誤, 授權選擇錯誤) as 主要:
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
            return 管理操作錯誤("forbidden")
        except 端點發布輸入錯誤 as 主要:
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
                return 管理操作錯誤("internal")
            return 管理操作錯誤("invalid")
        except BaseException as 主要:
            self._清除秘密框架(主要)
            if 收據 is not None:
                self._盡力標記孤兒(收據)
            return 管理操作錯誤("internal")

    def _建立下一版快照(
        self, 準備: 下一版本準備, 配置: dict[str, Any], 摘要: 能力摘要,
        能力: 已解析發布能力, 套件識別碼: str, 收據: 套件發布收據 | None,
    ) -> 發布版本快照:
        """由 current、受限配置、fresh authority 與 exact bundle locator 建立快照。

        參數：準備資料來自 P05 唯讀權威；配置只含六個可變內容欄位；摘要、能力與
        套件資料皆屬本次 request。
        回傳：發布前回傳使用假摘要的預檢快照；發布後回傳 exact receipt locator 快照。
        例外：配置鍵、JSON 值、權威關係或 DTO 無效時拋出固定值錯誤。
        副作用：只配置脫離 JSON 樹，不讀取路徑、內容或資料庫。
        """
        if type(配置) is not dict or set(配置) != _版本配置鍵:
            raise ValueError("版本配置無效") from None
        假摘要 = "0" * 64
        清單 = {
            "permission_revision": 摘要.權限修訂,
            "skills": [{
                "name": 項目.名稱, "content_sha256_reference": 項目.內容sha256參照,
            } for 項目 in 摘要.技能],
            "bundle_id": 套件識別碼,
            "manifest_reference": f"{套件識別碼}/manifest.json" if 收據 is None else 收據.清單參照,
            "manifest_digest": 假摘要 if 收據 is None else 收據.清單摘要,
            "sha256": 假摘要 if 收據 is None else 收據.套件雜湊,
        }
        return 發布版本快照(
            original_requirement_text=配置["original_requirement_text"],
            system_prompt=配置["system_prompt"],
            allowed_skills=[項目.名稱 for 項目 in 摘要.技能],
            allowed_tools=[項目.名稱 for 項目 in 摘要.工具],
            tool_schema_snapshot=能力.工具結構快照,
            tool_runtime_revision=能力.工具執行修訂,
            model_config_snapshot=配置["model_config_snapshot"],
            retry_policy=配置["retry_policy"], skill_bundle_manifest=清單,
            input_schema=配置["input_schema"], response_schema=配置["response_schema"],
            created_by_user_id=準備.owner_user_id,
        )

    def _驗證下一版發布清單(
        self, 收據: 套件發布收據, 端點識別碼: str, 版本識別碼: str,
        版本號碼: int, 摘要: 能力摘要, 能力: 已解析發布能力,
    ) -> 已驗證技能套件清單:
        """descriptor-safe 重驗 v2 清單並比對 identity、digest 與 fresh skill hashes。

        參數：收據與預配身分限定候選；摘要與能力限定當下 owner skill pins。
        回傳：完全相符的不可變 ``已驗證技能套件清單`` 投影。
        例外：任一收據、身分、摘要或技能 hash 不符時拋出 ``ValueError``。
        副作用：呼叫套件協調器透過安全描述元重驗已發布套件。
        """
        投影 = self._套件協調器.讀取已驗證清單(收據)
        預期技能 = tuple(
            (釘選.名稱, 釘選.內容sha256參照)
            for 釘選 in 摘要.技能
        )
        能力技能 = tuple((來源.名稱, 來源.內容sha256) for 來源 in 能力.技能來源)
        實際技能 = tuple((來源.name, 來源.source_hash) for 來源 in 投影.source_skills)
        if (
            投影.bundle_id != 收據.套件識別碼 or 投影.endpoint_id != 端點識別碼
            or 投影.endpoint_version_id != 版本識別碼 or 投影.version_number != 版本號碼
            or 投影.manifest_digest != 收據.清單摘要 or 投影.bundle_hash != 收據.套件雜湊
            or 投影.total_bytes != 收據.總位元組數
            or 預期技能 != 能力技能 or 實際技能 != 預期技能
        ):
            raise ValueError("發布技能清單不符") from None
        return 投影

    @staticmethod
    def _投影符合準備(
        候選: 已驗證技能套件清單, 預期: 已驗證技能套件清單,
        版本識別碼: str, 端點識別碼: str,
    ) -> bool:
        """在 P05 descriptor authority 內比對預先重驗的 exact detached projection。

        參數：候選由 P05 同一描述元重驗；預期由協調器第一次重驗；另帶 prepared 身分。
        回傳：型別、完整投影及版本與端點身分全數 exact 相符時為真。
        例外：無預期例外；敵意型別以短路比較關閉為假。
        副作用：只比較不可變純量與元組，不讀取路徑或技能內容。
        """
        return (
            type(候選) is 已驗證技能套件清單
            and type(預期) is 已驗證技能套件清單 and 候選 == 預期
            and 候選.endpoint_version_id == 版本識別碼
            and 候選.endpoint_id == 端點識別碼
        )

    def _驗證發布清單(
        self, 收據: 套件發布收據, 草稿: 規劃草稿, 能力: 已解析發布能力,
    ) -> None:
        """重驗 canonical manifest 並精確比對草稿釘選與本次 resolved skills。

        參數：收據、草稿與能力皆來自本 request 的權威流程。
        回傳：完全相符時回傳 ``None``。
        例外：清單、名稱或來源摘要不符時拋出 ``ValueError``。
        副作用：透過套件協調器描述元安全讀取並驗證 active bundle。
        """
        投影 = self._套件協調器.讀取已驗證清單(收據)
        摘要 = 草稿.能力摘要
        if type(摘要) is not 能力摘要 or len(摘要.技能) != len(能力.技能來源):
            raise ValueError("發布技能清單不符") from None
        預期: list[tuple[str, str]] = []
        for 釘選, 來源 in zip(摘要.技能, 能力.技能來源, strict=True):
            if 釘選.名稱 != 來源.名稱 or 釘選.內容sha256參照 != 來源.內容sha256:
                raise ValueError("發布技能清單不符") from None
            預期.append((釘選.名稱, 釘選.內容sha256參照))
        實際 = tuple((項目.name, 項目.source_hash) for 項目 in 投影.source_skills)
        if 實際 != tuple(預期):
            raise ValueError("發布技能清單不符") from None

    @staticmethod
    def _清除秘密框架(錯誤: BaseException) -> None:
        """清空失敗框架與 exception graph，避免 locals 保留明文別名。

        參數：錯誤是目前主要 ordinary 或控制流程失敗。
        回傳：無。例外：框架或 exception graph 清理失敗一律抑制。
        副作用：清除 traceback 中可清除 frame 的區域變數參照，並清除
        ``__cause__``、``__context__``、``__suppress_context__``；不改變
        例外 identity、型別與 ``args``。
        """
        try:
            錯誤.__cause__ = None
            錯誤.__context__ = None
            錯誤.__suppress_context__ = True
        except BaseException:
            pass
        try:
            traceback.clear_frames(錯誤.__traceback__)
        except BaseException:
            pass

    @staticmethod
    def _清空敏感緩衝(*緩衝區: bytearray) -> None:
        """在清理 traceback 前先把本次金鑰材料的 mutable buffers 歸零。

        參數：``緩衝區`` 是本方法擁有的 entropy 與明文 bytearray。
        回傳：無。例外：目前實作不預期拋出例外。
        副作用：原地將每個 bytearray 的所有元素改為零；不處理 immutable bytes。
        """
        for 緩衝 in 緩衝區:
            for 索引 in range(len(緩衝)):
                緩衝[索引] = 0

    def _建立快照(
        self, 草稿: 規劃草稿, 能力: 已解析發布能力, 識別: 已準備發布識別,
        收據: 套件發布收據 | None,
    ) -> 發布版本快照:
        """只由權威草稿、exact release 與套件收據建立版本快照。

        參數：草稿、能力與識別已由本次流程產生；收據空值只供發布前完整預檢。
        回傳：新的 ``發布版本快照``。
        例外：任何欄位或關係不符時由 P04 DTO 固定拒絕。
        副作用：只配置脫離 JSON 樹，不讀取客戶端配置或外部資源。
        """
        摘要 = 草稿.能力摘要
        發布值 = 草稿.發布確認
        if type(摘要) is not 能力摘要 or type(發布值) is not 發布值確認:
            raise ValueError("發布管理草稿無效") from None
        假摘要 = "0" * 64
        清單 = {
            "permission_revision": 摘要.權限修訂,
            "skills": [{
                "name": 項目.名稱, "content_sha256_reference": 項目.內容sha256參照,
            } for 項目 in 摘要.技能],
            "bundle_id": 識別.套件識別碼,
            "manifest_reference": f"{識別.套件識別碼}/manifest.json" if 收據 is None else 收據.清單參照,
            "manifest_digest": 假摘要 if 收據 is None else 收據.清單摘要,
            "sha256": 假摘要 if 收據 is None else 收據.套件雜湊,
        }
        綱要 = 草稿.綱要
        return 發布版本快照(
            草稿.原始需求, 綱要["system_prompt"],
            [項目.名稱 for 項目 in 摘要.技能],
            [項目.名稱 for 項目 in 摘要.工具],
            能力.工具結構快照, 能力.工具執行修訂, self._模型設定,
            self._重試政策, 清單, 綱要["input_schema"],
            發布值.response_schema, 草稿.擁有者識別碼,
        )

    def _配置識別(self, 現在: float) -> 已準備發布識別:
        """在任何套件或資料庫寫入前一次預配完整圖形識別。

        參數：現在是本 request 唯一建立時間。
        回傳：包含六個互異識別的 ``已準備發布識別``。
        例外：工廠或 DTO 失敗原樣交由公開邊界固定映射。
        副作用：依序呼叫六次注入的識別碼工廠。
        """
        值 = [self._識別碼產生器(前綴) for 前綴 in (
            "endpoint", "version", "credential", "account", "bundle", "audit",
        )]
        return 已準備發布識別(
            endpoint_id=值[0], version_id=值[1], credential_id=值[2],
            service_account_id=值[3], 套件識別碼=值[4], 稽核識別碼=值[5],
            created_at=現在,
        )

    def _讀取時間(self) -> float:
        """讀取並驗證本 request 唯一時間。

        參數：無。
        回傳：有限非負浮點時間。
        例外：時鐘輸出無效時拋出 ``ValueError``。
        副作用：恰呼叫一次注入時鐘。
        """
        值 = self._時鐘()
        if type(值) not in (int, float) or not math.isfinite(值) or 值 < 0:
            raise ValueError("發布管理時間無效") from None
        return float(值)

    def _取得熵(self) -> bytes:
        """取得一次且恰為三十二位元組的金鑰熵。

        參數：無。
        回傳：exact bytes 熵。
        例外：來源型別或長度不符時拋出 ``ValueError``。
        副作用：恰呼叫一次注入熵來源。
        """
        值 = self._隨機位元組(32)
        if type(值) is not bytes or len(值) != 32:
            raise ValueError("發布管理熵無效") from None
        return 值

    @staticmethod
    def _確認顯示值(配置: object, 綱要: object) -> bool:
        """只把客戶端配置當作伺服器草稿顯示值的相等確認。

        參數：配置來自路由；綱要來自共享草稿服務。
        回傳：配置非空、鍵受限且每個值等於權威綱要時為真。
        例外：一般形狀錯誤關閉為假；控制流程原樣傳出。
        副作用：只比較 bounded 路由 JSON，不把客戶端值帶入任何持久化快照。
        """
        try:
            if type(配置) is not dict or not 配置 or type(綱要) is not dict:
                return False
            對照 = {
                "system_prompt": 綱要["system_prompt"], "input_schema": 綱要["input_schema"],
                "response_schema": 綱要["response_schema"], "human_docs": 綱要["human_docs"],
                "rate_limit": 綱要["rate_limit"],
            }
            return set(配置) == set(對照) and all(
                配置[鍵] == 對照[鍵] for 鍵 in 對照
            )
        except _控制流程:
            raise
        except BaseException:
            return False

    def _依提交判定處理控制流程套件(
        self, *, 擁有者使用者識別碼: str, 端點識別碼: str,
        版本識別碼: str, 版本號碼: int, 收據: 套件發布收據,
        稽核識別碼: str, 建立時間: float,
    ) -> None:
        """控制流程後只在 SQLite 明確證明未提交時隔離候選套件。

        參數：權威擁有者、端點、預配版本與版號、發布收據、稽核識別及建立時間
        共同描述本次唯一候選。
        回傳：完成唯讀判定與必要隔離後回傳 ``None``。
        例外：判定、隔離與次要控制流程全部抑制，呼叫端可保持主要控制流程物件。
        副作用：唯讀查詢 Published SQLite；只有判定 ``未提交`` 時移動候選至孤兒區。
        """
        try:
            if self._版本配置服務 is None:
                return
            判定 = self._版本配置服務.判定版本配置提交結果(
                執行者使用者識別碼=擁有者使用者識別碼, 執行者類型="user",
                端點識別碼=端點識別碼, 版本識別碼=版本識別碼,
                版本號碼=版本號碼, 套件收據=收據,
                稽核識別碼=稽核識別碼, 建立時間=建立時間,
            )
            if 判定 is 版本配置提交判定.未提交:
                self._盡力標記孤兒(收據)
        except BaseException as 次要錯誤:
            self._清除秘密框架(次要錯誤)
            pass

    def _盡力標記孤兒(self, 收據: 套件發布收據) -> None:
        """隔離已耐久套件而永不覆蓋目前主要失敗。

        參數：收據是本次發布器成功回傳的 authoritative receipt。
        回傳：無。
        例外：所有普通與控制流程清理錯誤都被抑制，主要失敗保持優先。
        副作用：可呼叫套件協調器把 active bundle 移至孤兒隔離區。
        """
        try:
            self._套件協調器.標記孤兒(收據)
        except BaseException as 清理錯誤:
            self._清除秘密框架(清理錯誤)
            pass


__all__ = ["發布管理協調器"]
