from dataclasses import FrozenInstanceError
from inspect import Parameter, signature

import pytest

from 繁中代理.發布介面.憑證管理契約 import (
    一次性憑證建立收據,
    找不到端點憑證錯誤,
    憑證建立命令,
    憑證列表結果,
    憑證摘要,
    憑證撤銷收據,
    憑證管理服務,
    憑證管理操作錯誤,
    憑證管理狀態,
    憑證管理錯誤,
    端點生命週期衝突錯誤,
)


def _摘要(**覆寫值):
    欄位值 = dict(
        憑證識別碼="cred-1", 名稱="production", 用途="integration",
        金鑰前綴="pk_example", 金鑰末四碼="last", 狀態=憑證管理狀態.有效,
        到期時間=200.0, 最後使用時間=None, 建立時間=100.0, 撤銷時間=None,
        IP允許清單=("192.0.2.1",), 速率限制請求數=60,
    )
    return 憑證摘要(**(欄位值 | 覆寫值))


def test_狀態與錯誤分類為封閉精確契約():
    assert tuple(項目.value for 項目 in 憑證管理狀態) == (
        "active", "inactive", "expired", "revoked",
    )
    assert issubclass(找不到端點憑證錯誤, 憑證管理錯誤)
    assert issubclass(端點生命週期衝突錯誤, 憑證管理錯誤)
    assert issubclass(憑證管理操作錯誤, 憑證管理錯誤)


def test_摘要與列表frozen_slots且只含安全欄位():
    摘要 = _摘要()
    結果 = 憑證列表結果((摘要,))
    assert 結果.項目 == (摘要,)
    assert not hasattr(摘要, "__dict__") and not hasattr(結果, "__dict__")
    assert set(摘要.__slots__) == {
        "憑證識別碼", "名稱", "用途", "金鑰前綴", "金鑰末四碼", "狀態",
        "到期時間", "最後使用時間", "建立時間", "撤銷時間", "IP允許清單",
        "速率限制請求數",
    }
    for forbidden in ("api_key", "key_hash", "key_nonce", "key_ciphertext", "key_version", "revision"):
        assert not hasattr(摘要, forbidden) and forbidden not in repr(摘要)
    with pytest.raises(FrozenInstanceError):
        摘要.名稱 = "changed"


@pytest.mark.parametrize("欄位,值", [
    ("憑證識別碼", ""), ("名稱", " x"), ("用途", "Bearer secret"),
    ("金鑰前綴", ""), ("金鑰末四碼", "123"), ("狀態", "active"),
    ("到期時間", float("nan")), ("建立時間", -1),
    ("最後使用時間", 99.0), ("撤銷時間", 99.0),
    ("IP允許清單", ["192.0.2.1"]), ("速率限制請求數", 0),
])
def test_摘要拒絕不精確或不一致值(欄位, 值):
    with pytest.raises(ValueError):
        _摘要(**{欄位: 值})


def test_列表只接受精確摘要tuple():
    with pytest.raises(ValueError):
        憑證列表結果([_摘要()])
    with pytest.raises(ValueError):
        憑證列表結果((object(),))


def test_建立命令只接受canonical_exact_allowlist():
    命令 = 憑證建立命令("name", "purpose", 200.0, ("192.0.2.0/24",), 60)
    assert 命令.IP允許清單 == ("192.0.2.0/24",)
    assert not hasattr(命令, "__dict__")
    for 無效值 in (["192.0.2.0/24"], ("192.0.2.1/24",), ("192.0.2.1", "192.0.2.1")):
        with pytest.raises(ValueError):
            憑證建立命令("name", "purpose", 200.0, 無效值, 60)


def test_建立收據明文不進repr而撤銷收據只有安全欄位():
    收據 = 一次性憑證建立收據(
        "cred-1", "production", "integration", "pk_example", "last",
        憑證管理狀態.有效, 200.0, None, 100.0, None, ("192.0.2.1",), 60,
        "pk_once-only-secret",
    )
    assert 收據.初始金鑰 == "pk_once-only-secret"
    assert 收據.初始金鑰 not in repr(收據) and not hasattr(收據, "__dict__")
    撤銷收據 = 憑證撤銷收據("cred-1", 150.0, False)
    assert set(撤銷收據.__slots__) == {"憑證識別碼", "撤銷時間", "是否已撤銷"}
    with pytest.raises(ValueError):
        憑證撤銷收據("cred-1", float("inf"), False)


def test_管理協定公開三個單次服務呼叫簽章():
    預期參數 = {
        # 列出憑證 與 撤銷憑證 一樣收 是否管理者：管理者可讀取非自己端點的憑證摘要。
        "列出憑證": ("self", "端點識別碼", "擁有者使用者識別碼", "是否管理者"),
        "建立憑證": ("self", "端點識別碼", "擁有者使用者識別碼", "請求"),
        "撤銷憑證": ("self", "端點識別碼", "憑證識別碼", "擁有者使用者識別碼", "是否管理者", "請求識別碼"),
    }
    for 名稱, 參數名稱 in 預期參數.items():
        方法 = getattr(憑證管理服務, 名稱)
        參數 = tuple(signature(方法).parameters.values())
        assert tuple(項目.name for 項目 in 參數) == 參數名稱
        assert all(項目.kind is Parameter.KEYWORD_ONLY for 項目 in 參數[1:])
    註解 = 憑證管理服務.撤銷憑證.__annotations__
    assert 註解["請求識別碼"] == "str" and 註解["是否管理者"] == "bool"
