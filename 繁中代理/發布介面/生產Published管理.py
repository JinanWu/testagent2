"""A3-02 per-app Production Planner Resource 與 fail-closed Lazy Draft Proxy。

用途：在 startup 使用既有 Published 工具發布庫組裝權威來源、能力轉接器、
Fake／Gemini Planner、唯一草稿 Aggregate 與伺服器端規劃服務。
參數：公開設定只保存 explicit factories、handler release 與最長二十四小時 TTL。
返回值：建立可由 lifespan 擁有及關閉的 Planner 資源。
例外：設定或 lifecycle 違約固定拒絕；factory 與控制流程例外保留原型別。
副作用：模組匯入與物件建構零 I/O；只有 startup factory 會建立權威來源。
"""
from __future__ import annotations

import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock
from typing import Callable

from starlette.concurrency import run_in_threadpool

from 繁中代理.使用者 import 使用者庫

from .執行期.工具發布庫 import 工具發布庫
from .規劃.擁有者能力 import 使用者權威來源, 擁有者能力轉接器
from .規劃.規劃器供應商 import Gemini規劃器, 決定性假規劃器
from .規劃.規劃器契約 import 規劃器轉接器
from .規劃.規劃器服務 import 伺服器端草稿規劃服務
from .規劃.綱要 import 規劃服務, 規劃草稿
from .規劃.發布管理 import 發布管理協調器
from .路由.規劃發布 import 發布確認, 端點發布結果, 管理操作錯誤

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_組裝錯誤 = "草稿規劃生產組裝無效"
_服務不可用 = "草稿規劃服務不可用"
_發布服務不可用 = "發布管理服務不可用"
_設定錯誤 = "Planner生產設定無效"
_本機Path型別 = type(Path())
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class 草稿規劃服務不可用(RuntimeError):
    """表示 Lazy Draft Proxy 尚未安裝、正在 drain 或已經關閉。

    參數：沿用 ``RuntimeError`` 的標準建構參數。
    返回值：不適用。
    例外：由 proxy 在 authority window 外建立並拋出。
    副作用：建構本身無外部副作用。
    """


@dataclass(frozen=True, slots=True)
class Planner生產設定:
    """保存 Production Planner startup 的 explicit composition dependencies。

    參數：handler release、權威來源工廠、Fake／Gemini planner 工廠及草稿 TTL。
    返回值：不可變且可由 app builder 安全保存的設定。
    例外：格式、callback 或超過二十四小時的 TTL 固定拋 ``ValueError``。
    副作用：只做純記憶體驗證，不呼叫 callback 或讀取 DB／FS／Provider。
    """

    處理器發布: str
    使用者權威來源工廠: Callable[[Path], 使用者權威來源]
    規劃器工廠: Callable[[], 規劃器轉接器]
    草稿存續秒數: float = 86_400.0

    def __post_init__(self) -> None:
        """驗證 exact release、callbacks 與 ephemeral TTL 上限。

        參數：無；讀取目前資料類別欄位。
        返回值：None。
        例外：任一欄位違約時固定拋 ``ValueError``。
        副作用：無 I/O，也不執行兩個注入工廠。
        """
        發布 = object.__getattribute__(self, "處理器發布")
        來源工廠 = object.__getattribute__(self, "使用者權威來源工廠")
        規劃器工廠 = object.__getattribute__(self, "規劃器工廠")
        秒數 = object.__getattribute__(self, "草稿存續秒數")
        if (
            type(發布) is not str
            or _識別格式.fullmatch(發布) is None
            or not callable(來源工廠)
            or not callable(規劃器工廠)
            or type(秒數) not in (int, float)
            or 秒數 <= 0
            or 秒數 > 86_400
            or (type(秒數) is float and not math.isfinite(秒數))
        ):
            raise ValueError(_設定錯誤) from None


class 延遲草稿規劃服務:
    """固定 per-app identity 並以 active leases 控制 Draft Service authority。

    參數：建構不接受依賴，真實服務只能在 startup 透過 ``安裝`` 提供。
    返回值：``建立草稿`` 委派真實伺服器端規劃服務的結果。
    例外：未安裝固定 fail closed；double install／wrong clear 固定 ``ValueError``。
    副作用：在條件鎖內追蹤服務 identity、drain 狀態與 active lease 數。
    """

    def __init__(self) -> None:
        """建立未安裝的 per-app Planner slot。

        參數：無。
        返回值：None。
        例外：只有 Python 鎖配置錯誤可能原樣傳出。
        副作用：只配置記憶體鎖與空 slot，不執行外部 I/O。
        """
        self._條件 = Condition(RLock())
        self._服務: 伺服器端草稿規劃服務 | None = None
        self._進行中 = 0
        self._正在停止 = False

    def 安裝(self, 服務: 伺服器端草稿規劃服務) -> None:
        """在 startup exact-once 安裝真實 Draft Service。

        參數：``服務`` 為完整且 exact 的伺服器端草稿規劃服務。
        返回值：None。
        例外：型別不符、已有服務或殘留 lease 時固定拋 ``ValueError``。
        副作用：原子啟用後續 request lease。
        """
        if type(服務) is not 伺服器端草稿規劃服務:
            raise ValueError(_組裝錯誤) from None
        with self._條件:
            if self._服務 is not None or self._進行中:
                raise ValueError(_組裝錯誤) from None
            self._正在停止 = False
            self._服務 = 服務

    def 清除(self, 服務: 伺服器端草稿規劃服務) -> None:
        """只接受目前 identity，先撤銷 authority 再等待 active leases 歸零。

        參數：``服務`` 必須是本次 startup 安裝的 exact 服務。
        返回值：None。
        例外：wrong clear 或重複 clear 固定拋 ``ValueError``。
        副作用：立即拒絕新租借，並可能阻塞至既有同步委派結束。
        """
        self._撤銷並等待(服務, 允許已撤銷=False)

    def _撤銷並等待(
        self, 服務: 伺服器端草稿規劃服務, *, 允許已撤銷: bool,
    ) -> None:
        """執行不可繞過的 slot 撤銷，供公開 clear 與 failure cleanup 共用。

        參數：``服務`` 是本次 identity；``允許已撤銷`` 只供資源失敗清理重入。
        返回值：None。
        例外：公開模式遇到 wrong clear 固定 ``ValueError``；等待錯誤原樣傳出。
        副作用：鎖內先清 slot 並標記 draining，再等待 active leases 歸零。
        """
        with self._條件:
            if self._服務 is not 服務:
                if 允許已撤銷 and self._服務 is None and self._正在停止:
                    while self._進行中:
                        self._條件.wait()
                    return
                raise ValueError(_組裝錯誤) from None
            self._服務 = None
            self._正在停止 = True
            while self._進行中:
                self._條件.wait()

    @contextmanager
    def _租借服務(self):
        """在完整同步委派期間持有一個服務 lease。

        參數：無。
        返回值：context manager 讓出本次 exact 服務。
        例外：authority window 外固定拋 ``草稿規劃服務不可用``；服務例外原樣傳出。
        副作用：鎖內增減 active lease，歸零時喚醒 shutdown waiter。
        """
        with self._條件:
            服務 = self._服務
            if 服務 is None or self._正在停止:
                raise 草稿規劃服務不可用(_服務不可用) from None
            self._進行中 += 1
        try:
            yield 服務
        finally:
            with self._條件:
                self._進行中 -= 1
                if self._進行中 == 0:
                    self._條件.notify_all()

    def 建立草稿(
        self,
        擁有者識別碼: str,
        原始需求: str,
        選擇技能: tuple[str, ...],
        回應模式: str,
        *,
        現在: float,
    ) -> 規劃草稿:
        """以單一 active lease 委派完整伺服器端草稿規劃流程。

        參數：擁有者、需求、已選技能、回應模式與現在時間原樣傳給真實服務。
        返回值：真實服務保存後回傳的 ``規劃草稿``。
        例外：服務不可用固定失敗；權限、Planner 與 Aggregate 例外原樣傳出。
        副作用：租借一次 proxy slot，可能查 authority、呼叫 provider 並保存記憶體草稿。
        """
        with self._租借服務() as 服務:
            return 服務.建立草稿(
                擁有者識別碼, 原始需求, 選擇技能, 回應模式, 現在=現在,
            )


class 延遲發布管理服務:
    """以固定 per-app identity 管理 Endpoint Create authority 與 request leases。

    參數：建構不接受相依；真實 ``發布管理協調器`` 只能於 startup exact-once 安裝。
    返回值：``原子發布`` 在 authority window 內委派 exact 協調器結果。
    例外：未啟動、啟動失敗、draining 或關閉後固定 ``RuntimeError``；安裝違約為 ``ValueError``。
    副作用：只在條件鎖內保存一個協調器 slot、停止狀態與 active lease 數量。
    """

    def __init__(self) -> None:
        """建立只有空 slot 的 lifecycle-safe Create proxy。

        參數：無。
        返回值：None。
        例外：只有 Python 鎖配置錯誤可能原樣傳出。
        副作用：只配置記憶體鎖與空 slot，不讀取 DB／FS／key 或呼叫 factory。
        """
        self._條件 = Condition(RLock())
        self._服務: 發布管理協調器 | None = None
        self._進行中 = 0
        self._正在停止 = False

    def 安裝(self, 服務: 發布管理協調器) -> None:
        """在完整 management composition 成功後 exact-once 安裝 Create authority。

        參數：``服務`` 是本次 startup 建立的 exact ``發布管理協調器``。
        返回值：None。
        例外：型別不符、已有服務或殘留 lease 時固定拋 ``ValueError``。
        副作用：原子開啟後續 request lease；不執行任何發布操作。
        """
        if type(服務) is not 發布管理協調器:
            raise ValueError(_組裝錯誤) from None
        with self._條件:
            if self._服務 is not None or self._進行中:
                raise ValueError(_組裝錯誤) from None
            self._正在停止 = False
            self._服務 = 服務

    def 清除(self, 服務: 發布管理協調器) -> None:
        """先撤銷 Create authority，再等待本次服務的 active leases 歸零。

        參數：``服務`` 必須是目前 exact startup identity。
        返回值：None。
        例外：wrong clear 或重複 clear 固定拋 ``ValueError``。
        副作用：立即拒絕新租借，並可能阻塞直到既有同步發布委派完成。
        """
        self._撤銷並等待(服務, 允許已撤銷=False)

    def _撤銷並等待(self, 服務: 發布管理協調器, *, 允許已撤銷: bool) -> None:
        """執行可重入 failure cleanup 使用的不可繞過 slot 撤銷。

        參數：``服務`` 是本次 identity；``允許已撤銷`` 只供 startup／shutdown 失敗清理。
        返回值：None。
        例外：公開模式的 wrong clear 固定 ``ValueError``；等待錯誤原樣傳出。
        副作用：鎖內先清空 slot 並標記 draining，再等待 active leases 歸零。
        """
        with self._條件:
            if self._服務 is not 服務:
                if 允許已撤銷 and self._服務 is None and self._正在停止:
                    while self._進行中:
                        self._條件.wait()
                    return
                raise ValueError(_組裝錯誤) from None
            self._服務 = None
            self._正在停止 = True
            while self._進行中:
                self._條件.wait()

    @contextmanager
    def _租借服務(self):
        """在完整同步 Endpoint Create 委派期間持有一個 authority lease。

        參數：無。
        返回值：context manager 讓出本次 exact 協調器。
        例外：authority window 外固定拋 ``RuntimeError``；協調器例外原樣傳出。
        副作用：鎖內增減 active lease，歸零時喚醒 shutdown waiter。
        """
        with self._條件:
            服務 = self._服務
            if 服務 is None or self._正在停止:
                raise RuntimeError(_發布服務不可用) from None
            self._進行中 += 1
        try:
            yield 服務
        finally:
            with self._條件:
                self._進行中 -= 1
                if self._進行中 == 0:
                    self._條件.notify_all()

    def 原子發布(
        self, *, 擁有者使用者識別碼: str, 確認: 發布確認,
    ) -> 端點發布結果 | 管理操作錯誤:
        """以單一 active lease 委派完整 Endpoint Create 協調流程。

        參數：權威擁有者識別碼及由安全路由重建的發布確認原樣傳給真實協調器。
        返回值：協調器的端點建立收據或固定管理操作錯誤。
        例外：服務不可用固定失敗；協調器控制流程例外原樣傳出。
        副作用：租借一次 proxy slot，可能發布套件、加密憑證並提交 SQLite 圖形。
        """
        with self._租借服務() as 服務:
            return 服務.原子發布(
                擁有者使用者識別碼=擁有者使用者識別碼, 確認=確認,
            )


class 生產Planner資源:
    """擁有已安裝 Draft Service 與其全部 startup authority references。

    參數：由成功組裝傳入 proxy、服務、唯一 Aggregate、轉接器、來源與共用工具庫。
    返回值：提供 async ``關閉``、同一 Aggregate 與同一工具發布庫的 identity access。
    例外：關閉時 proxy drain 或使用者連線清理例外在 references 清除後傳出。
    副作用：shutdown 先撤銷 proxy/drain，再關閉自有使用者庫並釋放所有強參照。
    """

    def __init__(
        self,
        代理: 延遲草稿規劃服務,
        服務: 伺服器端草稿規劃服務,
        草稿Aggregate: 規劃服務,
        權限轉接器: 擁有者能力轉接器,
        使用者來源: 使用者權威來源,
        工具發布庫物件: 工具發布庫,
    ) -> None:
        """保存同一次成功 startup 建立或借用的 authority references。

        參數：六個物件皆屬同一 app 與 startup composition。
        返回值：None。
        例外：只有 Python 鎖配置錯誤可能原樣傳出。
        副作用：配置 exact-once 關閉鎖，不執行額外 I/O。
        """
        self._代理 = 代理
        self._服務 = 服務
        self._規劃服務 = 草稿Aggregate
        self._權限轉接器 = 權限轉接器
        self._使用者來源 = 使用者來源
        self._工具發布庫 = 工具發布庫物件
        self._關閉條件 = Condition(RLock())
        self._關閉狀態 = "尚未開始"
        self._關閉錯誤: BaseException | None = None

    def 取得規劃服務(self) -> 規劃服務 | None:
        """取得本 app 唯一且仍由 lifespan 擁有的 Draft Aggregate。

        參數：無。
        返回值：啟動期間回傳 exact ``規劃服務``；關閉後回傳 ``None``。
        例外：無預期例外。
        副作用：無。
        """
        return self._規劃服務

    def 取得工具發布庫(self) -> 工具發布庫 | None:
        """取得 Planner 借用的 exact Published 工具發布庫 identity。

        參數：無。
        返回值：啟動期間回傳共用發布庫；關閉後回傳 ``None``。
        例外：無預期例外。
        副作用：無；不建立或安裝第二份 registry。
        """
        return self._工具發布庫

    def 取得擁有者解析器(self) -> 擁有者能力轉接器 | None:
        """取得 Planner 使用且仍由 lifespan 擁有的 exact Owner Resolver。

        參數：無。
        返回值：啟動期間回傳 exact ``擁有者能力轉接器``；關閉後回傳 ``None``。
        例外：無預期例外。
        副作用：無；不查詢權威來源，也不建立第二份 resolver。
        """
        return self._權限轉接器

    def _清除同步(self) -> None:
        """協調 concurrent callers 並 exact-once 完成撤銷、drain 與 reference cleanup。

        參數：無。
        返回值：None。
        例外：所有 caller 在同一次關閉完成後取得相同的 exact 第一錯誤物件。
        副作用：唯一 owner 執行清理；並行 caller 等待完成，不會提前成功返回。
        """
        with self._關閉條件:
            if self._關閉狀態 == "已完成":
                if self._關閉錯誤 is not None:
                    raise self._關閉錯誤
                return
            if self._關閉狀態 == "進行中":
                while self._關閉狀態 != "已完成":
                    self._關閉條件.wait()
                if self._關閉錯誤 is not None:
                    raise self._關閉錯誤
                return
            self._關閉狀態 = "進行中"
        關閉錯誤: BaseException | None = None
        try:
            self._執行清除同步()
        except BaseException as 錯誤:
            關閉錯誤 = 錯誤
        finally:
            with self._關閉條件:
                self._關閉錯誤 = 關閉錯誤
                self._關閉狀態 = "已完成"
                self._關閉條件.notify_all()
        if 關閉錯誤 is not None:
            raise 關閉錯誤

    def _執行清除同步(self) -> None:
        """由唯一 shutdown owner 執行實際 proxy drain 與 authority cleanup。

        參數：無。
        返回值：None。
        例外：控制流程優先於普通錯誤，並保留 exact identity 與 args。
        副作用：撤銷 proxy、等待 active leases、關閉自有來源並清空強參照。
        """
        服務, 來源 = self._服務, self._使用者來源
        第一控制流程: BaseException | None = None
        第一普通錯誤: BaseException | None = None
        try:
            if 服務 is not None:
                self._代理.清除(服務)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                第一控制流程 = 錯誤
            else:
                第一普通錯誤 = 錯誤
            try:
                if type(服務) is 伺服器端草稿規劃服務:
                    self._代理._撤銷並等待(服務, 允許已撤銷=True)
            except BaseException as 撤銷錯誤:
                if isinstance(撤銷錯誤, _控制流程例外) and 第一控制流程 is None:
                    第一控制流程 = 撤銷錯誤
                elif 第一普通錯誤 is None:
                    第一普通錯誤 = 撤銷錯誤
        finally:
            self._服務 = None
            self._規劃服務 = None
            self._權限轉接器 = None
            self._使用者來源 = None
            self._工具發布庫 = None
        try:
            _關閉自有使用者來源(來源)
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程例外):
                if 第一控制流程 is None:
                    第一控制流程 = 錯誤
            elif 第一普通錯誤 is None:
                第一普通錯誤 = 錯誤
        if 第一控制流程 is not None:
            raise 第一控制流程
        if 第一普通錯誤 is not None:
            raise 第一普通錯誤

    async def 關閉(self) -> None:
        """在線程池執行可能阻塞的 exact-once proxy drain 與 authority cleanup。

        參數：無。
        返回值：None。
        例外：同步清除的第一個例外原樣傳出。
        副作用：撤銷 route authority、等待 lease 並清除本資源擁有參照。
        """
        await run_in_threadpool(self._清除同步)


def _關閉自有使用者來源(來源: object | None) -> None:
    """只關閉本組裝明確建立的 exact ``使用者庫`` SQLite 連線。

    參數：``來源`` 為可能的權威來源。
    返回值：None。
    例外：SQLite close 失敗原樣傳出。
    副作用：exact 使用者庫會關閉連線；注入的其他來源只釋放呼叫端參照。
    """
    if type(來源) is 使用者庫:
        來源.連線.close()


def 建立生產Planner資源(
    設定: Planner生產設定,
    Web資料庫路徑: Path,
    工具發布庫物件: 工具發布庫,
    代理: 延遲草稿規劃服務,
) -> 生產Planner資源:
    """以既有 Published registry 組裝並最後安裝唯一 Production Draft Service。

    參數：explicit Planner 設定、Web DB 路徑、已安裝工具的共用發布庫與 per-app proxy。
    返回值：已安裝且擁有唯一 Aggregate／authority references 的 ``生產Planner資源``。
    例外：組裝契約違約固定 ``ValueError``；factory 與控制流程例外保留原型別。
    副作用：依序建立權威來源、能力轉接器、Planner、Aggregate 與服務；最後才安裝 proxy。
    """
    if (
        type(設定) is not Planner生產設定
        or type(Web資料庫路徑) is not _本機Path型別
        or not Web資料庫路徑.is_absolute()
        or type(工具發布庫物件) is not 工具發布庫
        or type(代理) is not 延遲草稿規劃服務
    ):
        raise ValueError(_組裝錯誤) from None
    使用者來源 = 權限轉接器 = 規劃器 = 草稿Aggregate = 服務 = None
    已安裝 = False
    try:
        if 工具發布庫物件.取得發布(設定.處理器發布) is None:
            raise ValueError(_組裝錯誤) from None
        使用者來源 = 設定.使用者權威來源工廠(Web資料庫路徑)
        查詢 = getattr(使用者來源, "建立使用者上下文", None)
        if not callable(查詢):
            raise ValueError(_組裝錯誤) from None
        權限轉接器 = 擁有者能力轉接器(使用者來源, 工具發布庫物件, 設定.處理器發布)
        規劃器 = 設定.規劃器工廠()
        if type(規劃器) not in (決定性假規劃器, Gemini規劃器):
            raise ValueError(_組裝錯誤) from None
        草稿Aggregate = 規劃服務(存續秒數=設定.草稿存續秒數)
        服務 = 伺服器端草稿規劃服務(權限轉接器, 規劃器, 草稿服務=草稿Aggregate)
        代理.安裝(服務)
        已安裝 = True
        return 生產Planner資源(
            代理, 服務, 草稿Aggregate, 權限轉接器, 使用者來源, 工具發布庫物件,
        )
    except BaseException as 啟動錯誤:
        清理控制流程: BaseException | None = None
        if 已安裝 and 服務 is not None:
            try:
                代理.清除(服務)
            except _控制流程例外 as 控制流程:
                清理控制流程 = 控制流程
            except BaseException:
                pass
        try:
            _關閉自有使用者來源(使用者來源)
        except _控制流程例外 as 控制流程:
            if 清理控制流程 is None:
                清理控制流程 = 控制流程
        except BaseException:
            pass
        使用者來源 = 權限轉接器 = 規劃器 = 草稿Aggregate = 服務 = None
        if isinstance(啟動錯誤, _控制流程例外):
            raise
        if 清理控制流程 is not None:
            raise 清理控制流程
        raise


__all__ = (
    "Planner生產設定",
    "延遲草稿規劃服務",
    "延遲發布管理服務",
    "草稿規劃服務不可用",
    "生產Planner資源",
    "建立生產Planner資源",
)
