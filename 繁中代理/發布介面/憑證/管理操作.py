"""不持有加密封套的憑證清單與撤銷操作。"""

import json
import math
import sqlite3

from ..憑證管理契約 import (
    找不到端點憑證錯誤, 憑證列表結果, 憑證摘要, 憑證撤銷收據,
    憑證管理操作錯誤, 憑證管理狀態,
)
from ..領域模型 import WebOwnerPrincipal
from .服務 import SQLite憑證撤銷服務, 憑證撤銷找不到錯誤

_閒置秒數 = 15_552_000
_控制例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _重拋控制(盒: list[BaseException]) -> None:
    """從單元素盒重拋控制例外並清除helper參照。"""
    try:
        raise 盒.pop()
    except _控制例外:
        del 盒
        raise


def _清除控制鏈(錯誤: BaseException) -> None:
    """保留exact控制例外與args但移除舊trace鏈。"""
    BaseException.__setattr__(錯誤, "__traceback__", None)
    BaseException.__setattr__(錯誤, "__cause__", None)
    BaseException.__setattr__(錯誤, "__context__", None)
    BaseException.__setattr__(錯誤, "__suppress_context__", True)


def 撤銷管理憑證(資料庫, 時鐘, 端點識別碼, 憑證識別碼, 擁有者識別碼, 是否管理者, 請求識別碼):
    """不持有管理service或封套的撤銷boundary。"""
    來源 = 結果 = 委派 = None
    是否找不到 = 是否失敗 = False
    控制盒: list[BaseException] = []
    try:
        委派 = SQLite憑證撤銷服務(資料庫, **{"clock": 時鐘})
        來源 = 委派.撤銷(
            端點識別碼, 憑證識別碼, WebOwnerPrincipal(擁有者識別碼),
            請求識別碼, **{"actor_is_admin": 是否管理者},
        )
        結果 = 憑證撤銷收據(來源.credential_id, 來源.revoked_at, 來源.already_revoked)
    except 憑證撤銷找不到錯誤:
        是否找不到 = True
    except _控制例外 as 錯誤:
        _清除控制鏈(錯誤)
        控制盒.append(錯誤)
    except BaseException:
        是否失敗 = True
    finally:
        來源 = 委派 = 資料庫 = 時鐘 = None
        端點識別碼 = 憑證識別碼 = 擁有者識別碼 = 請求識別碼 = None
        是否管理者 = None
    if 控制盒:
        結果 = None
        _重拋控制(控制盒)
    if 是否找不到:
        結果 = None
        raise 找不到端點憑證錯誤("找不到端點或憑證") from None
    if 是否失敗 or 結果 is None:
        結果 = None
        raise 憑證管理操作錯誤("憑證管理失敗") from None
    return 結果


def 列出管理憑證(資料庫, 時鐘, 端點識別碼, 擁有者識別碼):
    """不持有管理service或封套的清單boundary。"""
    資料列清單 = 結果 = 連線 = 游標 = 項目 = 現在時間 = None
    是否找不到 = 是否失敗 = False
    主要控制盒: list[BaseException] = []
    清理控制盒: list[BaseException] = []
    try:
        if type(端點識別碼) is not str or type(擁有者識別碼) is not str:
            raise ValueError
        現在時間 = float(時鐘())
        if not math.isfinite(現在時間) or 現在時間 < 0:
            raise ValueError
        資料庫URI = 資料庫.resolve().as_uri() + "?mode=ro"
        連線 = sqlite3.connect(資料庫URI, timeout=30, isolation_level=None, **{"uri": True})
        連線.execute("BEGIN")
        游標 = 連線.execute(
            "SELECT e.id,c.id,c.name,c.purpose,c.key_prefix,c.key_last4,"
            "c.expires_at,c.last_used_at,c.created_at,c.revoked_at,"
            "c.ip_allowlist_json,c.rate_limit_requests FROM published_endpoints AS e "
            "LEFT JOIN endpoint_credentials AS c ON c.endpoint_id=e.id "
            "WHERE e.id=? AND e.owner_user_id=? ORDER BY c.created_at DESC,c.id ASC",
            (端點識別碼, 擁有者識別碼),
        )
        資料列清單 = 游標.fetchmany(10_001)
        游標.close()
        游標 = None
        if not 資料列清單:
            是否找不到 = True
        elif len(資料列清單) > 10_000:
            raise ValueError
        elif len(資料列清單) == 1 and 資料列清單[0][1] is None:
            if 資料列清單[0] != (端點識別碼,) + (None,) * 11:
                raise ValueError
            結果 = 憑證列表結果(())
        else:
            項目 = []
            for 索引 in range(len(資料列清單)):
                項目.append(_重建摘要(資料列清單[索引], 現在時間, 端點識別碼))
            結果 = 憑證列表結果(tuple(項目))
        連線.execute("COMMIT")
    except _控制例外 as 錯誤:
        _清除控制鏈(錯誤)
        主要控制盒.append(錯誤)
    except BaseException:
        是否失敗 = True
    finally:
        資料列清單 = 項目 = 現在時間 = 資料庫URI = None
        if 游標 is not None:
            try:
                游標.close()
            except _控制例外 as 錯誤:
                _清除控制鏈(錯誤)
                清理控制盒.append(錯誤)
            except BaseException:
                是否失敗 = True
        if 連線 is not None:
            try:
                if 連線.in_transaction:
                    連線.execute("ROLLBACK")
            except _控制例外 as 錯誤:
                _清除控制鏈(錯誤)
                清理控制盒.append(錯誤)
            except BaseException:
                是否失敗 = True
            try:
                連線.close()
            except _控制例外 as 錯誤:
                _清除控制鏈(錯誤)
                if not 清理控制盒:
                    清理控制盒.append(錯誤)
            except BaseException:
                是否失敗 = True
        游標 = 連線 = 資料庫 = 時鐘 = None
        端點識別碼 = 擁有者識別碼 = None
    if 主要控制盒:
        結果 = None
        清理控制盒.clear()
        _重拋控制(主要控制盒)
    if 清理控制盒:
        結果 = None
        _重拋控制(清理控制盒)
    if 是否找不到 and not 是否失敗:
        raise 找不到端點憑證錯誤("找不到端點或憑證") from None
    if 是否失敗 or 結果 is None:
        結果 = None
        raise 憑證管理操作錯誤("憑證管理失敗") from None
    return 結果


def _重建摘要(資料列: object, 現在時間: float, 端點識別碼: str) -> 憑證摘要:
    """驗證固定資料列並重建安全摘要。"""
    結果 = 解析值 = None
    try:
        if type(資料列) is not tuple or len(資料列) != 12 or 資料列[0] != 端點識別碼:
            raise ValueError
        憑證識別碼, 名稱, 用途, 前綴, 末四碼 = 資料列[1:6]
        到期, 最後使用, 建立, 撤銷, 允許清單JSON, 速率 = 資料列[6:]
        for 值 in (到期, 建立):
            if type(值) not in (int, float) or not math.isfinite(float(值)):
                raise ValueError
        if 建立 > 現在時間 or 到期 <= 建立:
            raise ValueError
        for 值 in (最後使用, 撤銷):
            if 值 is not None and (type(值) not in (int, float) or not math.isfinite(float(值)) or 值 < 建立 or 值 > 現在時間):
                raise ValueError
        if type(允許清單JSON) is not str or len(允許清單JSON.encode("utf-8")) > 65_536:
            raise ValueError
        解析值 = json.loads(允許清單JSON)
        if type(解析值) is not list or any(type(項目值) is not str for 項目值 in 解析值):
            raise ValueError
        if json.dumps(解析值, ensure_ascii=True, separators=(",", ":")) != 允許清單JSON:
            raise ValueError
        狀態 = 憑證管理狀態.已撤銷 if 撤銷 is not None else (
            憑證管理狀態.已過期 if 現在時間 >= 到期 else (
                憑證管理狀態.閒置 if 現在時間 >= (最後使用 if 最後使用 is not None else 建立) + _閒置秒數
                else 憑證管理狀態.有效
            )
        )
        結果 = 憑證摘要(
            憑證識別碼, 名稱, 用途, 前綴, 末四碼, 狀態, float(到期),
            None if 最後使用 is None else float(最後使用), float(建立),
            None if 撤銷 is None else float(撤銷), tuple(解析值), 速率,
        )
        return 結果
    finally:
        資料列 = 解析值 = 現在時間 = 端點識別碼 = None
        憑證識別碼 = 名稱 = 用途 = 前綴 = 末四碼 = None
        到期 = 最後使用 = 建立 = 撤銷 = 允許清單JSON = 速率 = 狀態 = 值 = None
        結果 = None
