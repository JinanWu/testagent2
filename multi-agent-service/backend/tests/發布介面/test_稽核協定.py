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


class HostileBaseException(BaseException):
    """測試ordinary BaseException必須固定化。"""


class ChildKeyboardInterrupt(KeyboardInterrupt):
    """控制流程subclass測試。"""


class ChildSystemExit(SystemExit):
    """控制流程subclass測試。"""


class ChildGeneratorExit(GeneratorExit):
    """控制流程subclass測試。"""


class RaisingAuditSink:
    """在lookup或call階段拋出指定例外物件。"""

    def __init__(self, stage, error):
        self.stage = stage
        self.error = error

    @property
    def append_audit_event(self):
        if self.stage == "lookup":
            raise self.error
        return self._call

    def _call(self, _event):
        raise self.error


class FakeAuditSink:
    """測試用sink，可模擬lookup、call與receipt結果。"""

    def __init__(self, receipt=None, *, lookup_raises=False, call_raises=False):
        """保存receipt、失敗模式與呼叫紀錄。"""
        self.receipt = receipt
        self.calls = []
        self.lookups = 0
        self.lookup_raises = lookup_raises
        self.call_raises = call_raises

    @property
    def append_audit_event(self):
        """回傳append callable，或在attribute lookup階段拋錯。"""
        self.lookups += 1
        if self.lookup_raises:
            raise RuntimeError(SINK_MARKER_SECRET)
        return self._append_audit_event

    def _append_audit_event(self, event, /):
        """記錄event並回傳receipt，或在call階段拋錯。"""
        if self.call_raises:
            raise RuntimeError(SINK_MARKER_SECRET)
        self.calls.append(event)
        return self.receipt


class SideEffectSpy:
    """測試用不可信nested物件，任何dereference或to_json都算違約。"""

    def __init__(self):
        """初始化side effect計數。"""
        self.hits = 0

    def __getattr__(self, name):
        """任何attribute dereference都記錄並拋出。"""
        self.hits += 1
        raise RuntimeError(SINK_MARKER_SECRET)

    def to_json(self):
        """任何to_json呼叫都記錄並拋出。"""
        self.hits += 1
        raise RuntimeError(SINK_MARKER_SECRET)


class FakeReceiptSubclass(AuditAppendReceipt):
    """測試用receipt subclass，驗證helper只接受exact type。"""


class FakeReprMarker:
    """測試用不可信receipt-like物件，repr含敏感marker。"""

    def __repr__(self):
        """回傳敏感marker，驗證錯誤路徑會清除raw receipt。"""
        return SINK_MARKER_SECRET


def _assert_error_sanitized(error, marker, *, 保留參數=False):
    """確認公開錯誤、例外鏈與發布介面 production frame locals 不含 marker。"""
    if not 保留參數:
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


def _forged_actor(actor_type, actor_id):
    """繞過constructor建立exact actor，驗證helper會深層重新正規化。"""
    actor = object.__new__(發布領域模型.AuditActorRef)
    object.__setattr__(actor, "actor_type", actor_type)
    object.__setattr__(actor, "actor_id", actor_id)
    return actor


def _forged_resource(resource_type, resource_id):
    """繞過constructor建立exact resource，驗證helper會深層重新正規化。"""
    resource = object.__new__(發布領域模型.AuditResourceRef)
    object.__setattr__(resource, "resource_type", resource_type)
    object.__setattr__(resource, "resource_id", resource_id)
    return resource


def _forged_metadata(data):
    """繞過constructor建立exact metadata，驗證helper會深層重新正規化。"""
    metadata = object.__new__(發布領域模型.AuditMetadata)
    object.__setattr__(metadata, "_資料", data)
    return metadata


def _forged_event(**overrides):
    """繞過constructor建立exact event，驗證helper在sink lookup前重新正規化。"""
    valid_event = _make_event()
    event = object.__new__(發布領域模型.AuditEvent)
    for field in fields(發布領域模型.AuditEvent):
        object.__setattr__(
            event,
            field.name,
            overrides.get(field.name, getattr(valid_event, field.name)),
        )
    return event


def _assert_sink_failure(sink, event, marker=None):
    """確認 helper 固定失敗關閉，且錯誤與 traceback 不保留 marker。"""
    with pytest.raises(AuditSinkError) as error:
        附加稽核事件或失敗關閉(sink, event)

    assert type(error.value) is AuditSinkError
    assert error.value.args == ("稽核事件無法確認提交",)
    if marker is not None:
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
    """成功 append 必須用 canonical event 呼叫並回傳 canonical receipt。"""
    event = _make_event()
    raw_receipt = _make_receipt(sequence=9)
    sink = FakeAuditSink(raw_receipt)

    receipt = 附加稽核事件或失敗關閉(sink, event)

    assert len(sink.calls) == 1
    canonical_event = sink.calls[0]
    assert type(canonical_event) is 發布領域模型.AuditEvent
    assert canonical_event is not event
    assert canonical_event.to_json() == event.to_json()
    assert canonical_event.actor is not event.actor
    assert canonical_event.resource is not event.resource
    assert canonical_event.metadata is not event.metadata
    assert receipt == raw_receipt
    assert receipt is not raw_receipt
    assert type(receipt) is AuditAppendReceipt
    assert receipt.event_id == canonical_event.event_id


def test_append_audit_event_rejects_forged_exact_event_before_sink_lookup():
    """forged exact event 或 nested DTO 不合法時，不可 lookup/call sink。"""
    forged_secret_event = _forged_event(event_id=SINK_MARKER_SECRET)
    forged_actor_event = _forged_event(
        actor=_forged_actor("user", SINK_MARKER_SECRET),
    )
    forged_resource_event = _forged_event(
        resource=_forged_resource("audit.event", SINK_MARKER_SECRET),
    )
    forged_string_metadata_event = _forged_event(
        metadata=_forged_metadata({"safe_key": "raw secret"}),
    )
    forged_raw_metadata_event = _forged_event(
        metadata=_forged_metadata({"raw_payload": 1}),
    )
    secret_event_sink = FakeAuditSink(_make_receipt(), lookup_raises=True)
    actor_event_sink = FakeAuditSink(_make_receipt())
    resource_event_sink = FakeAuditSink(_make_receipt())
    string_metadata_event_sink = FakeAuditSink(_make_receipt())
    raw_metadata_event_sink = FakeAuditSink(_make_receipt())

    _assert_sink_failure(secret_event_sink, forged_secret_event)
    _assert_sink_failure(actor_event_sink, forged_actor_event)
    _assert_sink_failure(resource_event_sink, forged_resource_event)
    _assert_sink_failure(string_metadata_event_sink, forged_string_metadata_event)
    _assert_sink_failure(raw_metadata_event_sink, forged_raw_metadata_event)

    assert secret_event_sink.calls == []
    assert actor_event_sink.calls == []
    assert resource_event_sink.calls == []
    assert string_metadata_event_sink.calls == []
    assert raw_metadata_event_sink.calls == []
    assert secret_event_sink.lookups == 0
    assert actor_event_sink.lookups == 0
    assert resource_event_sink.lookups == 0
    assert string_metadata_event_sink.lookups == 0
    assert raw_metadata_event_sink.lookups == 0


@pytest.mark.parametrize("field", ["actor", "resource", "metadata"])
def test_append_audit_event_rejects_nonexact_nested_without_dereference_or_sink(field):
    """nested欄位非exact時，只可type guard fail，不可dereference或lookup sink。"""
    spy = SideEffectSpy()
    event = _forged_event(**{field: spy})
    sink = FakeAuditSink(_make_receipt(), lookup_raises=True)

    _assert_sink_failure(sink, event)

    assert spy.hits == 0
    assert sink.lookups == 0
    assert sink.calls == []


def test_append_audit_event_failures_close_with_fixed_error():
    """lookup、call、bad receipt 與不一致提交都必須固定失敗關閉。"""
    event = _make_event()
    event_subclass_type = type(
        "FakeEventSubclass",
        (發布領域模型.AuditEvent,),
        {},
    )
    event_subclass = object.__new__(event_subclass_type)
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
    assert event_subclass_sink.lookups == 0


@pytest.mark.parametrize("stage", ["lookup", "call"])
def test_audit_sink_custom_base_exception固定化且marker不留production_frame(stage):
    """非控制流程BaseException必須變成固定AuditSinkError。"""
    marker = f"{SINK_MARKER_SECRET}_{stage}_base"
    original = HostileBaseException(marker)
    error = _assert_sink_failure(RaisingAuditSink(stage, original), _make_event(), marker)
    assert type(error) is AuditSinkError


@pytest.mark.parametrize("stage", ["lookup", "call"])
@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit, ChildKeyboardInterrupt, ChildSystemExit, ChildGeneratorExit],
)
def test_audit_sink控制流程保留exact_identity且清除production_frame(stage, error_type):
    """K/I/S/G及其既有型別在清理敏感locals後原樣重拋。"""
    marker = f"{SINK_MARKER_SECRET}_{stage}_{error_type.__name__}"
    original = error_type(marker)
    with pytest.raises(error_type) as captured:
        附加稽核事件或失敗關閉(RaisingAuditSink(stage, original), _make_event())  # type: ignore[arg-type]
    assert captured.value is original
    assert captured.value.args == (marker,)
    _assert_error_sanitized(captured.value, marker, 保留參數=True)


def test_audit_metadata_instance_shadowed_serializer不會執行():
    """exact DTO仍須使用trusted class dispatch重建。"""
    metadata = 發布領域模型.AuditMetadata({"count": 1})
    calls = []
    object.__setattr__(metadata, "to_json", lambda: calls.append(True))
    event = _make_event(metadata=metadata)
    sink = FakeAuditSink(_make_receipt())

    receipt = 附加稽核事件或失敗關閉(sink, event)  # type: ignore[arg-type]

    assert receipt.committed is True
    assert calls == []
    assert sink.calls[0].metadata.to_json() == {"count": 1}
