"""從明確信任的代理鏈解析發布呼叫的權威來源 IP。"""

import ipaddress
from typing import cast

最大信任代理網段數 = 32
最大網段文字位元組數 = 64
最大位址文字位元組數 = 64
最大轉送標頭位元組數 = 2_048
最大轉送跳數 = 16

_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_映射位址網段 = ipaddress.ip_network("::ffff:0:0/96")


class 來源位址錯誤(ValueError):
    """表示來源位址或受信任代理提供的轉送鏈無法安全解析。"""


def _清除控制流程上下文(控制流程: BaseException) -> None:
    """不觸發敵對覆寫方法，移除清理期間建立的例外關聯。"""
    BaseException.__setattr__(控制流程, "__cause__", None)
    BaseException.__setattr__(控制流程, "__context__", None)
    BaseException.__setattr__(控制流程, "__suppress_context__", True)


def _解析位址文字(文字: object):
    """解析精確內建 ASCII 字串，並明確拒絕 scoped／IPv4-mapped IPv6。"""
    位址 = None
    失敗 = False
    try:
        if type(文字) is not str:
            raise ValueError
        if not 1 <= len(文字) <= 最大位址文字位元組數 or not 文字.isascii():
            raise ValueError
        if "%" in 文字:
            raise ValueError
        位址 = ipaddress.ip_address(文字)
        if type(位址) is ipaddress.IPv6Address and 位址.ipv4_mapped is not None:
            raise ValueError
    except _控制流程例外 as 控制流程:
        _清除控制流程上下文(控制流程)
        文字 = 位址 = 控制流程 = None
        raise
    except BaseException:
        失敗 = True
    文字 = None
    if 失敗 or type(位址) not in (ipaddress.IPv4Address, ipaddress.IPv6Address):
        位址 = None
        raise 來源位址錯誤("來源位址解析失敗") from None
    return 位址


def _編譯信任網段(網段文字: object) -> tuple[object, ...]:
    """以 stdlib ipaddress 編譯有數量及逐項位元組上限的明確 CIDR。"""
    網段 = None
    結果: list[object] | None = None
    當前文字 = None
    失敗 = False
    try:
        if type(網段文字) is not tuple or len(網段文字) > 最大信任代理網段數:
            raise ValueError
        結果 = []
        for 當前文字 in 網段文字:
            if type(當前文字) is not str:
                raise ValueError
            if not 1 <= len(當前文字) <= 最大網段文字位元組數 or not 當前文字.isascii():
                raise ValueError
            網段 = ipaddress.ip_network(當前文字, strict=True)
            if 網段.version == 6 and 網段.overlaps(_映射位址網段):
                raise ValueError
            結果.append(網段)
    except _控制流程例外 as 控制流程:
        _清除控制流程上下文(控制流程)
        網段文字 = 網段 = 結果 = 當前文字 = 控制流程 = None
        raise
    except BaseException:
        失敗 = True
    網段文字 = 網段 = 當前文字 = None
    if 失敗 or type(結果) is not list:
        結果 = None
        raise 來源位址錯誤("來源位址解析失敗") from None
    return tuple(結果)


def 解析來源位址(對端位址: object, X轉送來源標頭: object, 信任代理網段: object) -> str:
    """依唯一 X-Forwarded-For occurrence 與右至左信任鏈選出來源。

    ``X轉送來源標頭`` 是 transport adapter 篩出的原始 header value tuple；
    非受信任 peer 完全忽略它。受信任 peer 必須恰有一個 ASCII value，且
    只允許逗號與 OWS（SP／HTAB）分隔的裸 IP literal。若整條鏈都受信任，
    以最左側 header 位址為來源。IPv4-mapped IPv6 一律拒絕而不跨 family。
    """
    網段 = 對端 = 標頭值 = 欄位 = 跳點 = 當前 = None
    鏈: list[object] | None = None
    失敗 = False
    try:
        網段 = _編譯信任網段(信任代理網段)
        對端 = _解析位址文字(對端位址)
        if not any(對端 in 項 for 項 in 網段 if 對端.version == 項.version):
            結果 = 對端.compressed
        else:
            if type(X轉送來源標頭) is not tuple or len(X轉送來源標頭) != 1:
                raise ValueError
            標頭值 = X轉送來源標頭[0]
            if type(標頭值) is not bytes or not 1 <= len(標頭值) <= 最大轉送標頭位元組數:
                raise ValueError
            if 標頭值.count(b",") >= 最大轉送跳數:
                raise ValueError
            欄位 = 標頭值.split(b",")
            if not 1 <= len(欄位) <= 最大轉送跳數:
                raise ValueError
            鏈 = []
            for 當前 in 欄位:
                if type(當前) is not bytes:
                    raise ValueError
                當前 = 當前.strip(b" \t")
                if not 當前 or not 當前.isascii():
                    raise ValueError
                跳點 = _解析位址文字(當前.decode("ascii"))
                鏈.append(跳點)
            鏈.append(對端)
            結果 = 鏈[0].compressed
            for 跳點 in reversed(鏈):
                if not any(跳點 in 項 for 項 in 網段 if 跳點.version == 項.version):
                    結果 = 跳點.compressed
                    break
        if type(結果) is not str:
            raise ValueError
    except _控制流程例外 as 控制流程:
        _清除控制流程上下文(控制流程)
        對端位址 = X轉送來源標頭 = 信任代理網段 = None
        網段 = 對端 = 標頭值 = 欄位 = 跳點 = 當前 = 鏈 = 結果 = 控制流程 = None
        raise
    except BaseException:
        失敗 = True
    對端位址 = X轉送來源標頭 = 信任代理網段 = None
    網段 = 對端 = 標頭值 = 欄位 = 跳點 = 當前 = 鏈 = None
    if 失敗:
        結果 = None
        raise 來源位址錯誤("來源位址解析失敗") from None
    return cast(str, 結果)
