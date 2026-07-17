"""稽核 append receipt 公開契約測試。"""

from dataclasses import FrozenInstanceError
from dataclasses import fields
import traceback
from typing import get_type_hints

import pytest

import 繁中代理.發布介面 as 發布介面套件
from 繁中代理.發布介面 import AuditAppendReceipt
from 繁中代理.發布介面 import AuditEventSink
from 繁中代理.發布介面 import AuditReceiptError
from 繁中代理.發布介面 import AuditSinkError
from 繁中代理.發布介面 import 附加稽核事件或失敗關閉
from 繁中代理.發布介面 import 領域模型 as 發布領域模型


RECEIPT_MARKER_SECRET = "pk_unique_marker_audit_receipt"
SINK_MARKER_SECRET = "pk_unique_marker_audit_sink"


class EvilStr(str):
    """測試用 str subclass，避免 exact str 檢查被放寬。"""


class EvilInt(int):
    """測試用 int subclass，避免 exact int 檢查被放寬。"""


class FakeAuditSink:
    """測試用sink，可模擬lookup、call與receipt結果。"""

    def __init__(self, receipt=None, *, lookup_raises=False, call_raises=False):
        """保存receipt、失敗模式與呼叫紀錄。"""
        self.receipt = receipt
        self.calls = []
        self.lookup_raises = lookup_raises
        self.call_raises = call_raises

    @property
    def append_audit_event(self):
        """回傳append callable，或在attribute lookup階段拋錯。"""
        if self.lookup_raises:
            raise RuntimeError(SINK_MARKER_SECRET)
        return self._append_audit_event

    def _append_audit_event(self, event, /):
        """記錄event並回傳receipt，或在call階段拋錯。"""
        if self.call_raises:
            raise RuntimeError(SINK_MARKER_SECRET)
        self.calls.append(event)
        return self.receipt


class FakeReceiptSubclass(AuditAppendReceipt):
    """測試用receipt subclass，驗證helper只接受exact type。"""


class FakeReprMarker:
    """測試用不可信receipt-like物件，repr含敏感marker。"""

    def __repr__(self):
        """回傳敏感marker，驗證錯誤路徑會清除raw receipt。"""
        return SINK_MARKER_SECRET


def _assert_error_sanitized(error, marker):
    """確認公開錯誤、例外鏈與發布介面 production frame locals 不含 marker。"""
    assert marker not in str(error)
    assert marker not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    for frame, _ in traceback.walk_tb(error.__traceback__):
        if frame.f_globals.get("__name__", "").startswith("繁中代理.發布介面"):
            assert marker not in repr(frame.f_locals)


def _make_receipt(**overrides):
    """建立合法 receipt，讓各測試只覆寫被測欄位。"""
    params = {
        "event_id": "evt_1",
        "committed": True,
        "sequence": 1,
    } | overrides
    return AuditAppendReceipt(**params)


def _make_event(**overrides):
    """建立合法 AuditEvent，讓測試只覆寫被測欄位。"""
    params = {
        "event_id": "evt_1",
        "occurred_at": 1,
        "action": "append",
        "outcome": "success",
        "actor": 發布領域模型.AuditActorRef("system", None),
        "resource": 發布領域模型.AuditResourceRef("audit.event", "res_1"),
        "metadata": 發布領域模型.AuditMetadata(),
    } | overrides
    return 發布領域模型.AuditEvent(**params)


def _forged_receipt(event_id, sequence):
    """繞過constructor建立exact receipt，驗證helper會重新正規化。"""
    receipt = object.__new__(AuditAppendReceipt)
    object.__setattr__(receipt, "event_id", event_id)
    object.__setattr__(receipt, "committed", True)
    object.__setattr__(receipt, "sequence", sequence)
    return receipt


def _assert_sink_failure(sink, event, marker=SINK_MARKER_SECRET):
    """確認 helper 固定失敗關閉，且錯誤與 traceback 不保留 marker。"""
    with pytest.raises(AuditSinkError) as error:
        附加稽核事件或失敗關閉(sink, event)

    assert type(error.value) is AuditSinkError
    assert error.value.args == ("稽核事件無法確認提交",)
    _assert_error_sanitized(error.value, marker)
    return error.value


def test_audit_append_receipt_exports_keep_identity():
    """AuditAppendReceipt、AuditReceiptError 與 AuditEventSink 必須從 root 穩定匯出。"""
    assert "AuditAppendReceipt" in 發布介面套件.__all__
    assert "AuditReceiptError" in 發布介面套件.__all__
    assert "AuditEventSink" in 發布介面套件.__all__
    assert "AuditSinkError" in 發布介面套件.__all__
    assert "附加稽核事件或失敗關閉" in 發布介面套件.__all__
    assert 發布介面套件.AuditAppendReceipt is AuditAppendReceipt
    assert 發布介面套件.AuditReceiptError is AuditReceiptError
    assert 發布介面套件.AuditEventSink is AuditEventSink
    assert 發布介面套件.AuditSinkError is AuditSinkError
    assert (
        發布介面套件.附加稽核事件或失敗關閉
        is 附加稽核事件或失敗關閉
    )
    assert AuditAppendReceipt is 發布領域模型.AuditAppendReceipt
    assert AuditReceiptError is 發布領域模型.AuditReceiptError
    assert AuditEventSink is 發布介面套件.AuditEventSink


def test_audit_event_sink_protocol_is_not_runtime_checkable():
    """AuditEventSink 不可 runtime isinstance 檢查，避免結構檢查成公開保證。"""
    with pytest.raises(TypeError):
        isinstance(object(), AuditEventSink)


def test_audit_append_receipt_valid_committed_json_order_types_and_new_dict():
    """committed=True receipt 必須輸出固定鍵序、concrete types 與新 dict。"""
    receipt = _make_receipt(sequence=7)
    output = receipt.to_json()

    assert list(output) == ["event_id", "committed", "sequence"]
    assert output == {"event_id": "evt_1", "committed": True, "sequence": 7}
    assert type(output) is dict
    assert output is not receipt.to_json()
    assert type(receipt.event_id) is str
    assert type(receipt.committed) is bool
    assert type(receipt.sequence) is int


def test_audit_append_receipt_valid_failed_json_order_types_and_new_dict():
    """committed=False receipt 必須固定 sequence=None 且回傳 ordinary new dict。"""
    receipt = _make_receipt(committed=False, sequence=None)
    output = receipt.to_json()

    assert list(output) == ["event_id", "committed", "sequence"]
    assert output == {"event_id": "evt_1", "committed": False, "sequence": None}
    assert type(output) is dict
    assert output is not receipt.to_json()
    assert type(receipt.event_id) is str
    assert type(receipt.committed) is bool
    assert receipt.sequence is None


@pytest.mark.parametrize("sequence", [1, 2**63 - 1])
def test_audit_append_receipt_accepts_committed_sequence_boundaries(sequence):
    """sequence 邊界 1 與 2**63-1 必須被接受。"""
    assert _make_receipt(sequence=sequence).sequence == sequence


@pytest.mark.parametrize(
    "event_id",
    [
        EvilStr("evt_1"),
        "evt.pk_live_123",
        "evt.sk-prod-123",
        "evt.BearerToken",
        "a" * 64,
        "/Users/example/event",
        r"C:\Users\event",
        "~/event",
        RECEIPT_MARKER_SECRET,
    ],
)
def test_audit_append_receipt_rejects_event_id_secret_hash_path_and_subclass(event_id):
    """event_id 重用安全 identifier，拒絕 subclass、secret、digest 與 path。"""
    with pytest.raises(AuditReceiptError):
        _make_receipt(event_id=event_id)


@pytest.mark.parametrize("committed", [1, 0, "true", None])
def test_audit_append_receipt_rejects_committed_nonbool(committed):
    """committed 欄位只接受 exact bool，不接受 int、字串或 None。"""
    with pytest.raises(AuditReceiptError):
        _make_receipt(committed=committed)


@pytest.mark.parametrize("sequence", [0, -1, 2**63, True, False, EvilInt(1)])
def test_audit_append_receipt_rejects_bad_committed_sequence_values(sequence):
    """committed=True 時 sequence 必須是 1..2**63-1 的 exact int。"""
    with pytest.raises(AuditReceiptError):
        _make_receipt(sequence=sequence)


@pytest.mark.parametrize(
    ("committed", "sequence"),
    [
        (False, 1),
        (False, 0),
        (False, EvilInt(1)),
        (True, None),
    ],
)
def test_audit_append_receipt_rejects_state_sequence_mismatch(committed, sequence):
    """committed 狀態與 sequence nullability 不一致時必須 fail closed。"""
    with pytest.raises(AuditReceiptError):
        _make_receipt(committed=committed, sequence=sequence)


def test_audit_append_receipt_is_frozen_and_declares_public_types():
    """AuditAppendReceipt 為 frozen dataclass，且宣告欄位型別固定。"""
    receipt = _make_receipt()
    hints = get_type_hints(AuditAppendReceipt)

    assert {field.name: hints[field.name] for field in fields(AuditAppendReceipt)} == {
        "event_id": str,
        "committed": bool,
        "sequence": int | None,
    }
    with pytest.raises(FrozenInstanceError):
        receipt.sequence = 2


def test_audit_append_receipt_error_sanitizes_all_package_frames_and_exception_chain():
    """receipt 錯誤不可在發布介面 production traceback frames 留下 raw marker。"""
    with pytest.raises(AuditReceiptError) as error:
        _make_receipt(event_id=RECEIPT_MARKER_SECRET)

    _assert_error_sanitized(error.value, RECEIPT_MARKER_SECRET)


def test_append_audit_event_success_returns_canonical_receipt_for_same_event():
    """成功 append 必須用同一 event 呼叫並回傳 canonical receipt。"""
    event = _make_event()
    raw_receipt = _make_receipt(sequence=9)
    sink = FakeAuditSink(raw_receipt)

    receipt = 附加稽核事件或失敗關閉(sink, event)

    assert sink.calls == [event]
    assert receipt == raw_receipt
    assert receipt is not raw_receipt
    assert type(receipt) is AuditAppendReceipt


def test_append_audit_event_failures_close_with_fixed_error():
    """lookup、call、bad receipt 與不一致提交都必須固定失敗關閉。"""
    event = _make_event()
    event_subclass = object.__new__(type("FakeEventSubclass", (發布領域模型.AuditEvent,), {}))
    for field in fields(發布領域模型.AuditEvent):
        object.__setattr__(event_subclass, field.name, getattr(event, field.name))
    event_subclass_sink = FakeAuditSink(_make_receipt())
    failures = [
        (FakeAuditSink(None), event),
        (object(), event),
        (FakeAuditSink(lookup_raises=True), event),
        (FakeAuditSink(call_raises=True), event),
        (FakeAuditSink(object()), event),
        (FakeAuditSink(FakeReprMarker()), event),
        (FakeAuditSink(FakeReceiptSubclass("evt_1", True, 1)), event),
        (FakeAuditSink(_make_receipt(committed=False, sequence=None)), event),
        (FakeAuditSink(_make_receipt(event_id="evt_2")), event),
        (event_subclass_sink, event_subclass),
        (FakeAuditSink(_forged_receipt(SINK_MARKER_SECRET, 1)), event),
        (FakeAuditSink(_forged_receipt("evt_1", EvilInt(1))), event),
    ]

    errors = [_assert_sink_failure(sink, failed_event) for sink, failed_event in failures]

    assert len({id(error) for error in errors}) == len(errors)
    assert {error.args for error in errors} == {("稽核事件無法確認提交",)}
    assert event_subclass_sink.calls == []
