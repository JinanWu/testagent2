"""LOG L04 invocation 級精確計量持久化測試。"""

import json
import sqlite3
import threading

import pytest

import 繁中代理.發布介面.呼叫.儲存庫 as 儲存庫模組
from 繁中代理.發布介面.呼叫.儲存庫 import (
    SQLite呼叫儲存庫,
    呼叫儲存錯誤,
    呼叫計量,
    合併呼叫計量,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


class _中斷成本驗證:
    """在 DTO 固定 slot 驗證層注入 BaseException。"""

    def __init__(self, 錯誤):
        self.錯誤 = 錯誤

    def fullmatch(self, _值):
        raise self.錯誤


def _包含標記(值, 標記, 已看=None):
    """只遞迴檢查安全內建容器與 DTO slots。"""
    if type(值) is str:
        return 標記 in 值
    if type(值) not in (dict, list, tuple, set, 呼叫計量):
        return False
    if 已看 is None:
        已看 = set()
    if id(值) in 已看:
        return False
    已看.add(id(值))
    項目們 = 值.values() if type(值) is dict else 值
    if type(值) is 呼叫計量:
        項目們 = (值.input_tokens, 值.output_tokens, 值.estimated_cost_usd, 值.pricing_version)
    for 項目 in 項目們:
        if _包含標記(項目, 標記, 已看):
            return True
    return False


def _建立資料庫(tmp_path):
    """建立一個可呼叫端點；model config 刻意不含任何 rate schema。"""
    路徑 = tmp_path / "metering.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep','owner','svc','meter','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver-1','ep',1,'需求','提示','[]','[]','{}','rev','{\"opaque\":true}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver-1' WHERE id='ep'")
    return 路徑


def _建立執行中呼叫(路徑, invocation_id="inv-meter"):
    """建立並開始一個呼叫。"""
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 12, 識別碼工廠=lambda: invocation_id)
    儲存庫.建立已解析呼叫("ep", "ver-1", f"req-{invocation_id}", {})
    儲存庫.標記執行中(invocation_id)
    return 儲存庫


def _讀取計量(路徑, invocation_id="inv-meter"):
    """讀回單一 invocation 的終態計量。"""
    with sqlite3.connect(路徑) as 連線:
        return 連線.execute(
            "SELECT status,usage_json,latency_ms,pricing_version,completed_at FROM endpoint_invocations WHERE id=?",
            (invocation_id,),
        ).fetchone()


def _讀取完整呼叫(路徑, invocation_id="inv-meter"):
    """讀回整列，供拒絕路徑驗證交易沒有部分改寫。"""
    with sqlite3.connect(路徑) as 連線:
        return 連線.execute(
            "SELECT * FROM endpoint_invocations WHERE id=?", (invocation_id,),
        ).fetchone()


def test_runtime計量原樣持久化且完全不讀版本或模型設定(tmp_path):
    """DTO 捕捉後建立任意 config、再漂移 current，仍保存原 pricing version。"""
    usage = 呼叫計量(3, 4, "0.00001375", "price-v1")
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE published_endpoints SET current_version_id=NULL WHERE id='ep'")

    儲存庫.完成呼叫("inv-meter", "succeeded", output={"ok": True}, usage=usage, latency_ms=17)

    狀態, usage_json, 延遲, 定價版本, 完成時間 = _讀取計量(路徑)
    assert (狀態, 延遲, 定價版本, 完成時間) == ("succeeded", 17, "price-v1", 12)
    assert usage_json == (
        '{"estimated_cost_usd":"0.00001375","input_tokens":3,'
        '"output_tokens":4,"total_tokens":7}'
    )


def test_retries只合併成一列且重複結案不重複計量(tmp_path):
    """token/cost 先合併，終態 CAS 阻止第二次寫入。"""
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)
    usage = 合併呼叫計量(
        呼叫計量(2, 1, "0.1", "price-v1"),
        呼叫計量(5, 3, "0.02", "price-v1"),
    )
    儲存庫.完成呼叫("inv-meter", "failed", error={"code": "model"}, usage=usage, latency_ms=9)
    第一次 = _讀取計量(路徑)

    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-meter", "failed", error={}, usage=usage, latency_ms=10)

    assert _讀取計量(路徑) == 第一次
    assert json.loads(第一次[1]) == {
        "estimated_cost_usd": "0.12", "input_tokens": 7,
        "output_tokens": 4, "total_tokens": 11,
    }
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT count(*) FROM endpoint_invocations").fetchone() == (1,)
        assert 連線.execute(
            "SELECT count(*),sum(status='failed') FROM endpoint_invocations WHERE status IN ('succeeded','failed')"
        ).fetchone() == (1, 1)


@pytest.mark.parametrize(
    "usage",
    [
        呼叫計量(True, 0, "0", "price-v1"),
        呼叫計量(-1, 0, "0", "price-v1"),
        呼叫計量(2**63, 0, "0", "price-v1"),
        呼叫計量(2**63 - 1, 1, "0", "price-v1"),
        呼叫計量(1, 1, "NaN", "price-v1"),
        呼叫計量(1, 1, "Infinity", "price-v1"),
        呼叫計量(1, 1, "01", "price-v1"),
        呼叫計量(1, 1, "1.0", "price-v1"),
        呼叫計量(1, 1, "1." + "0" * 28 + "1", "price-v1"),
        呼叫計量(1, 1, "1", "bad version"),
    ],
)
def test_畸形偽造或超界DTO結案完整回滾(tmp_path, usage):
    """repository 重新驗證 slots、canonical decimal、範圍與識別碼。"""
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)

    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-meter", "succeeded", output={}, usage=usage, latency_ms=1)

    assert _讀取計量(路徑) == ("running", None, None, None, None)


def test_竄改凍結DTO與不同定價retry皆固定拒絕(tmp_path):
    """frozen/slotted 物件仍不受盲信，跨 pricing version 不得合併。"""
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)
    usage = 呼叫計量(1, 1, "0.1", "price-v1")
    object.__setattr__(usage, "estimated_cost_usd", float("nan"))
    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-meter", "succeeded", output={}, usage=usage)
    with pytest.raises(呼叫儲存錯誤, match="呼叫計量合併失敗"):
        合併呼叫計量(呼叫計量(1, 0, "0.1", "v1"), 呼叫計量(1, 0, "0.2", "v2"))
    assert _讀取計量(路徑) == ("running", None, None, None, None)


def test_自訂BaseException固定拒絕且完整回滾(tmp_path, monkeypatch):
    """非終止流程 BaseException 不得逸出 repository boundary。"""
    class 自訂錯誤(BaseException):
        pass

    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)
    monkeypatch.setattr(儲存庫模組, "_成本格式", _中斷成本驗證(自訂錯誤("secret")))
    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-meter", "succeeded", output={}, usage=呼叫計量(1, 1, "1", "v1"))
    assert _讀取計量(路徑) == ("running", None, None, None, None)


def test_legacy任意JSON仍相容且pricing_version為空(tmp_path):
    """既有 arbitrary usage 路徑不被誤當作可信 pricing persistence。"""
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)
    儲存庫.完成呼叫("inv-meter", "succeeded", output={}, usage={"legacy": 1}, latency_ms=2)
    assert _讀取計量(路徑)[1:4] == ('{"legacy":1}', 2, None)


@pytest.mark.parametrize("畸形定價版本", ["stale-price", 7, sqlite3.Binary(b"stale")])
@pytest.mark.parametrize("轉換", ["開始", "legacy結案", "計量結案"])
def test_preterminal定價版本非空在任何副作用前固定拒絕且整列不變(
    tmp_path, monkeypatch, 畸形定價版本, 轉換,
):
    """動態型別無論為何，pricing_version 非空都破壞精確 pre-terminal invariant。"""
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 12, 識別碼工廠=lambda: "inv-meter")
    儲存庫.建立已解析呼叫("ep", "ver-1", "req-preterminal", {})
    if 轉換 != "開始":
        儲存庫.標記執行中("inv-meter")
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "UPDATE endpoint_invocations SET pricing_version=? WHERE id='inv-meter'",
            (畸形定價版本,),
        )
    修改前 = _讀取完整呼叫(路徑)
    次數 = {"時鐘": 0, "可信複製": 0, "正規化": 0, "DTO": 0}

    def 計數(名稱):
        def 呼叫(*_args, **_kwargs):
            次數[名稱] += 1
            raise AssertionError(名稱)
        return 呼叫

    儲存庫._時鐘 = 計數("時鐘")
    monkeypatch.setattr(儲存庫, "_建立可信JSON樹", 計數("可信複製"))
    monkeypatch.setattr(儲存庫模組, "建立正規JSON", 計數("正規化"))
    monkeypatch.setattr(儲存庫模組, "_重建呼叫計量", 計數("DTO"))

    with pytest.raises(呼叫儲存錯誤):
        if 轉換 == "開始":
            儲存庫.標記執行中("inv-meter")
        else:
            usage = ({"legacy": 1} if 轉換 == "legacy結案"
                     else 呼叫計量(1, 2, "0.1", "price-v1"))
            儲存庫.完成呼叫("inv-meter", "succeeded", output={"ok": True}, usage=usage)

    assert 次數 == {"時鐘": 0, "可信複製": 0, "正規化": 0, "DTO": 0}
    assert _讀取完整呼叫(路徑) == 修改前


def test_兩個真實結案寫入者只有一個計量勝者(tmp_path):
    """BEGIN IMMEDIATE 與終態 CAS 讓兩條連線只能保存一份計量。"""
    路徑 = _建立資料庫(tmp_path)
    _建立執行中呼叫(路徑)
    閘門 = threading.Barrier(2)
    結果 = []

    def 完成(tokens):
        try:
            閘門.wait(timeout=10)
            SQLite呼叫儲存庫(路徑, 時鐘=lambda: 20).完成呼叫(
                "inv-meter", "succeeded", output={"tokens": tokens},
                usage=呼叫計量(tokens, 0, str(tokens), f"price-v{tokens}"), latency_ms=tokens,
            )
            結果.append(None)
        except BaseException as 錯誤:
            結果.append(錯誤)

    執行緒們 = [threading.Thread(target=完成, args=(tokens,)) for tokens in (1, 2)]
    for 執行緒 in 執行緒們:
        執行緒.start()
    for 執行緒 in 執行緒們:
        執行緒.join(timeout=10)
    assert not any(執行緒.is_alive() for 執行緒 in 執行緒們)
    assert sum(項目 is None for 項目 in 結果) == 1
    assert sum(type(項目) is 呼叫儲存錯誤 for 項目 in 結果) == 1
    assert _讀取計量(路徑)[3] in {"price-v1", "price-v2"}


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流程原樣傳播且所有production框架清除DTO(tmp_path, monkeypatch, 錯誤類型):
    """terminal control flow 保留 identity/args，且每個 production frame 都清除 marker。"""
    標記 = "secret-pricing-marker"
    路徑 = _建立資料庫(tmp_path)
    儲存庫 = _建立執行中呼叫(路徑)
    錯誤 = 錯誤類型(標記)
    monkeypatch.setattr(儲存庫模組, "_成本格式", _中斷成本驗證(錯誤))
    with pytest.raises(錯誤類型) as 資訊:
        儲存庫.完成呼叫(
            "inv-meter", "succeeded", output={}, usage=呼叫計量(1, 1, "0.1", 標記)
        )
    assert 資訊.value is 錯誤 and 資訊.value.args == (標記,)
    框架 = [項目.frame for 項目 in 資訊.traceback if str(項目.frame.code.path) == 儲存庫模組.__file__]
    assert {"完成呼叫", "_更新狀態", "_重建呼叫計量"} <= {項目.code.name for 項目 in 框架}
    for 框架項 in 框架:
        assert not _包含標記(框架項.f_locals, 標記)
