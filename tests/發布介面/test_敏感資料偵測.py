import pytest
from 繁中代理.發布介面.呼叫.敏感偵測 import 敏感偵測錯誤, 敏感命中, 偵測敏感資料


def test_偵測器回傳位置而不回傳原文():
    hits = 偵測敏感資料({"email": "user@example.com", "card": "4111 1111 1111 1111"})
    assert ("email", "/email") in [(h.類型代碼, h.JSON路徑) for h in hits]
    assert ("payment_card_candidate", "/card") in [(h.類型代碼, h.JSON路徑) for h in hits]
    assert all("user@example.com" not in repr(h) for h in hits)


def test_偵測器拒絕cycle與非有限值():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料({"x": cycle})
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料({"x": float("nan")})
