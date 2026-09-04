"""PostgreSQL 固定視窗 endpoint/credential 與來源驗證失敗節流。"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from 繁中代理.PostgreSQL連線 import 交易連線
from .限流 import (固定視窗秒數, 最大限流計數, 限流決策, 限流計數錯誤,
                  計算固定視窗, 驗證限流上限)
from .來源節流 import 來源驗證失敗節流決策, _正規輸入


def _增加(連線: Any, 表格: str, 欄位: tuple[str, ...], 值: tuple[object, ...], 計數欄: str) -> int:
    # 最後兩欄是可變計數與更新時間；唯一鍵只由 scope identity 與 window 組成。
    衝突 = ",".join(欄位[:-2])
    位置 = ("%s",) * (len(欄位) - 3) + ("to_timestamp(%s)", "%s", "to_timestamp(%s)")
    SQL = (f"INSERT INTO {表格}({','.join(欄位)}) VALUES ({','.join(位置)}) "
           f"ON CONFLICT ({衝突}) DO UPDATE SET {計數欄}={表格}.{計數欄}+1,"
           "updated_at=EXCLUDED.updated_at "
           f"WHERE {表格}.{計數欄}<9223372036854775807 RETURNING {計數欄}")
    列 = 連線.execute(SQL, 值).fetchone()
    if 列 is None: raise ValueError
    列 = _正規列(列, (計數欄,))
    if type(列[0]) is not int or not 1 <= 列[0] <= 最大限流計數:
        raise ValueError
    return 列[0]


def _正規列(列: object, 欄名: tuple[str, ...]) -> tuple[object, ...]:
    if isinstance(列, Mapping):
        if set(列) != set(欄名): raise ValueError
        return tuple(列[名稱] for 名稱 in 欄名)
    if type(列) is tuple and len(列) == len(欄名): return 列
    raise ValueError


def 增加PostgreSQL雙層計數並判定(連線: Any, 端點識別碼: str, 憑證識別碼: str,
                             端點上限: int, 憑證上限: int, 時間戳記: int | float) -> 限流決策:
    """在 caller transaction 內鎖定式 UPSERT 兩個 scope 後判定。"""
    try:
        if any(type(v) is not str or not v.strip() for v in (端點識別碼, 憑證識別碼)): raise ValueError
        端點上限 = 驗證限流上限(端點上限); 憑證上限 = 驗證限流上限(憑證上限)
        視窗 = 計算固定視窗(時間戳記)
        端點計數 = _增加(連線, "rate_limit_counters",
            ("scope_type","scope_id","window_start","request_count","updated_at"),
            ("endpoint",端點識別碼,視窗.開始秒,1,時間戳記), "request_count")
        憑證計數 = _增加(連線, "rate_limit_counters",
            ("scope_type","scope_id","window_start","request_count","updated_at"),
            ("credential",憑證識別碼,視窗.開始秒,1,時間戳記), "request_count")
        if 端點計數 <= 端點上限 and 憑證計數 <= 憑證上限:
            return 限流決策(True,端點計數,憑證計數,None,None,端點上限,憑證上限)
        範圍 = "endpoint" if 端點計數 > 端點上限 else "credential"
        重試 = max(1,min(固定視窗秒數,math.ceil(視窗.結束秒-float(時間戳記))))
        return 限流決策(False,端點計數,憑證計數,範圍,重試,端點上限,憑證上限)
    except (KeyboardInterrupt,SystemExit,GeneratorExit): raise
    except BaseException: raise 限流計數錯誤("限流計數失敗") from None


class PostgreSQL限流儲存庫:
    __slots__=("_設定",)
    def __init__(self, 凍結設定: object) -> None: self._設定=凍結設定

    def 增加雙層計數並判定(self, 端點識別碼: str, 憑證識別碼: str,
                         端點上限: int, 憑證上限: int, 時間戳記: int|float) -> 限流決策:
        with 交易連線(self._設定) as 連線:
            return 增加PostgreSQL雙層計數並判定(連線,端點識別碼,憑證識別碼,
                                          端點上限,憑證上限,時間戳記)

    def 記錄來源驗證失敗(self, 用戶端IP: str, 端點slug: str, 時間戳記: int|float,
                      *, 上限: int=10) -> 來源驗證失敗節流決策:
        """以 client_ip+endpoint_slug 的固定六十秒視窗原子節流。"""
        try:
            用戶端IP,端點slug,時間戳記,上限,_,開始,結束 = _正規輸入(
                用戶端IP,端點slug,時間戳記,上限,固定視窗秒數)
            with 交易連線(self._設定) as 連線:
                計數=_增加(連線,"auth_failure_rate_counters",
                    ("client_ip","endpoint_slug","window_start","failure_count","updated_at"),
                    (用戶端IP,端點slug,開始,1,時間戳記),"failure_count")
            已節流=計數>上限
            重試=max(1,min(固定視窗秒數,math.ceil(結束-float(時間戳記)))) if 已節流 else None
            return 來源驗證失敗節流決策(計數,上限,開始,結束,已節流,重試)
        except (KeyboardInterrupt,SystemExit,GeneratorExit): raise
        except BaseException: raise 限流計數錯誤("來源驗證失敗節流計數失敗") from None
