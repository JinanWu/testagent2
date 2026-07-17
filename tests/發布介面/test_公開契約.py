"""發布介面共同公開契約測試。"""

from collections.abc import Mapping
import json
from dataclasses import FrozenInstanceError
from dataclasses import dataclass
import math
import traceback

import pytest

import 繁中代理.發布介面 as 發布介面套件
from 繁中代理.發布介面 import (
    AuditActorRef,
    AuditMetadata,
    AuditMetadataError,
    AuditReferenceError,
    AuditResourceRef,
    嚴格JSON錯誤,
    建立失敗信封,
    建立正規JSON,
    建立成功信封,
    解析嚴格JSON,
    計算正規JSON雜湊,
)
from 繁中代理.發布介面 import 領域模型 as 發布領域模型
from 繁中代理.發布介面.領域模型 import AuditMetadata as 領域AuditMetadata
from 繁中代理.發布介面.領域模型 import AuditMetadataError as 領域AuditMetadataError
from 繁中代理.發布介面.領域模型 import EndpointRef
from 繁中代理.發布介面.領域模型 import InvokeEnvelope
from 繁中代理.發布介面.領域模型 import InvocationRef
from 繁中代理.發布介面.領域模型 import PublishedError
from 繁中代理.發布介面.領域模型 import PublishedUsage
from 繁中代理.發布介面.領域模型 import PublishedWarning
from 繁中代理.發布介面.領域模型 import ServiceAccountSnapshotRef


解析錯誤唯一SECRET_MARKER = "唯一SECRET_MARKER_解析_不外洩"
深層錯誤唯一SECRET_MARKER = "唯一SECRET_MARKER_深層_不外洩"
信封錯誤唯一SECRET_MARKER = "唯一SECRET_MARKER_信封_不外洩"
稽核ITEMS錯誤唯一SECRET_MARKER = "唯一SECRET_MARKER_audit_items_不外洩"
稽核重複鍵唯一SECRET_MARKER = "唯一SECRET_MARKER_audit_duplicate_不外洩"
稽核異常PAIR唯一SECRET_MARKER = "唯一SECRET_MARKER_audit_pair_不外洩"
稽核參照唯一SECRET_MARKER = "pk_唯一SECRET_MARKER_audit_ref_不外洩"
稽核資源型別唯一SECRET_MARKER = "sk_unique_marker_audit_resource_type"
稽核資源識別唯一SECRET_MARKER = "pk_unique_marker_audit_resource_id"


@dataclass(frozen=True)
class EvilEndpointRef(EndpointRef):
    """測試用惡意端點參照，模擬 subclass 偷加公開欄位。"""

    secret: str = "endpoint-secret"


@dataclass(frozen=True)
class EvilInvocationRef(InvocationRef):
    """測試用惡意呼叫參照，模擬 subclass 偷加公開欄位。"""

    secret: str = "invocation-secret"


@dataclass(frozen=True)
class EvilPublishedUsage(PublishedUsage):
    """測試用惡意用量摘要，模擬 subclass 偷加公開欄位。"""

    secret: str = "usage-secret"


@dataclass(frozen=True)
class EvilPublishedWarning(PublishedWarning):
    """測試用惡意警告摘要，模擬 subclass 偷加公開欄位。"""

    secret: str = "warning-secret"


@dataclass(frozen=True)
class EvilPublishedError(PublishedError):
    """測試用惡意錯誤摘要，模擬 subclass 偷加公開欄位。"""

    secret: str = "error-secret"


class EvilItemsRaisesMapping(Mapping):
    """測試用惡意 mapping，items() 會丟出含 marker 的 runtime exception。"""

    def __init__(self, marker):
        self._marker = marker

    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __repr__(self):
        return f"EvilItemsRaisesMapping({self._marker!r})"

    def items(self):
        raise RuntimeError(self._marker)


class EvilDuplicateItemsMapping(Mapping):
    """測試用惡意 mapping，items() 回傳重複 exact key pair。"""

    def __init__(self, marker):
        self._marker = marker

    def __getitem__(self, key):
        if key == "duplicate":
            return False
        raise KeyError(key)

    def __iter__(self):
        return iter(("duplicate",))

    def __len__(self):
        return 1

    def __repr__(self):
        return f"EvilDuplicateItemsMapping({self._marker!r})"

    def items(self):
        return (("duplicate", True), ("duplicate", False))


class EvilMalformedItemsMapping(Mapping):
    """測試用惡意mapping，items()回傳無法解包且帶marker的pair。"""

    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def items(self):  # type: ignore[override]
        return ((稽核異常PAIR唯一SECRET_MARKER, 1, 2),)


def _錯誤狀態不含marker(錯誤, marker):
    assert marker not in str(錯誤)
    assert marker not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None
    for frame_summary in traceback.extract_tb(錯誤.__traceback__):
        assert marker not in repr(frame_summary)
    traceback物件 = 錯誤.__traceback__
    while traceback物件 is not None:
        for 區域值 in traceback物件.tb_frame.f_locals.values():
            assert marker not in repr(區域值)
        traceback物件 = traceback物件.tb_next


def _領域模型錯誤狀態不含marker(錯誤, marker):
    assert marker not in str(錯誤)
    assert marker not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None
    for frame, _ in traceback.walk_tb(錯誤.__traceback__):
        if frame.f_globals.get("__name__", "").startswith("繁中代理.發布介面"):
            assert marker not in repr(frame.f_locals)


def _契約模組錯誤狀態不含marker(錯誤, marker):
    assert marker not in str(錯誤)
    assert marker not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None
    for frame, _ in traceback.walk_tb(錯誤.__traceback__):
        if frame.f_globals.get("__name__") == "繁中代理.發布介面.契約":
            assert marker not in repr(frame.f_locals)


def test_package_root_exports_factory_constructors():
    """factory constructors 必須從 package root 成為公開 API。"""
    assert "建立成功信封" in 發布介面套件.__all__
    assert "建立失敗信封" in 發布介面套件.__all__
    assert 發布介面套件.建立成功信封 is 建立成功信封
    assert 發布介面套件.建立失敗信封 is 建立失敗信封


def test_package_root_exports_audit_metadata_contract():
    """AuditMetadata 必須從 package root 穩定匯出且保留類別 identity。"""
    assert "AuditMetadata" in 發布介面套件.__all__
    assert "AuditMetadataError" in 發布介面套件.__all__
    assert 發布介面套件.AuditMetadata is AuditMetadata
    assert 發布介面套件.AuditMetadataError is AuditMetadataError
    assert AuditMetadata is 領域AuditMetadata
    assert AuditMetadataError is 領域AuditMetadataError


def test_package_root_exports_audit_reference_contract():
    """稽核參照 DTO 必須從 package root 穩定匯出且保留類別 identity。"""
    for name in ("AuditActorRef", "AuditReferenceError", "AuditResourceRef"):
        assert name in 發布介面套件.__all__
    assert 發布介面套件.AuditActorRef is AuditActorRef
    assert 發布介面套件.AuditReferenceError is AuditReferenceError
    assert 發布介面套件.AuditResourceRef is AuditResourceRef
    assert AuditActorRef is 發布領域模型.AuditActorRef
    assert AuditReferenceError is 發布領域模型.AuditReferenceError
    assert AuditResourceRef is 發布領域模型.AuditResourceRef


def test_audit_actor_ref_valid_user_service_system_json_order_and_new_dict():
    """actor 參照接受合法 enum，輸出固定鍵序且每次回傳新 dict。"""
    user = AuditActorRef("user", "user_123")
    service = AuditActorRef("service_account", "svc:deploy.1")
    system = AuditActorRef("system", None)
    user_json = user.to_json()

    assert list(user_json) == ["actor_type", "actor_id"]
    assert user_json == {"actor_type": "user", "actor_id": "user_123"}
    assert service.to_json() == {
        "actor_type": "service_account",
        "actor_id": "svc:deploy.1",
    }
    assert system.to_json() == {"actor_type": "system", "actor_id": None}
    assert type(user_json) is dict
    assert user_json is not user.to_json()


@pytest.mark.parametrize(
    ("actor_type", "actor_id"),
    [
        ("admin", "user_1"),
        ("User", "user_1"),
        ("user", None),
        ("service_account", ""),
        ("system", "system_1"),
        (b"user", "user_1"),
    ],
)
def test_audit_actor_ref_rejects_enum_and_system_rule(actor_type, actor_id):
    """actor_type enum 與 system actor_id None 規則必須 fail closed。"""
    with pytest.raises(AuditReferenceError):
        AuditActorRef(actor_type, actor_id)


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "a" * 129,
        "_bad",
        "bad space",
        "pk_live_123",
        "svc.pk_live_123",
        "sk-prod-123",
        "user:sk-prod-123",
        "sk_live_123",
        "svc.sk_test_123",
        "BearerToken",
        "id:BearerToken",
        "a" * 64,
        "user:" + "a" * 64,
        "/Users/example/token",
        r"C:\Users\secret",
        "~/secret",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_audit_actor_ref_rejects_bad_identifiers_raw_prefixes_digest_path_and_marker(
    identifier,
):
    """actor_id 不可帶 raw secret、完整 digest、路徑、空白或 PEM marker。"""
    with pytest.raises(AuditReferenceError):
        AuditActorRef("user", identifier)


@pytest.mark.parametrize(
    "identifier",
    [
        "550e8400-e29b-41d4-a716-446655440000",
        "req_ab12",
        "A:1.b-2",
    ],
)
def test_audit_actor_ref_accepts_uuid_request_id_and_safe_ascii_identifier(identifier):
    """合法 UUID、短 request id 與安全 ASCII identifier 不可被誤拒。"""
    assert AuditActorRef("user", identifier).actor_id == identifier


class EvilStr(str):
    """測試用 str subclass，避免 exact str 檢查被放寬。"""


def test_audit_actor_ref_rejects_type_subclasses():
    """actor 參照欄位只接受 exact str，不接受 str subclass。"""
    for args in (
        (EvilStr("user"), "user_1"),
        ("user", EvilStr("user_1")),
    ):
        with pytest.raises(AuditReferenceError):
            AuditActorRef(*args)


def test_audit_actor_ref_is_frozen():
    """actor 參照本身為 frozen dataclass。"""

    actor = AuditActorRef("user", "user_1")

    with pytest.raises(FrozenInstanceError):
        actor.actor_id = "user_2"


def test_audit_reference_error_sanitizes_all_package_frames():
    """稽核參照錯誤不可在任何發布介面 frame locals 留下敏感 marker。"""
    with pytest.raises(AuditReferenceError) as 錯誤:
        AuditActorRef("user", 稽核參照唯一SECRET_MARKER)

    _領域模型錯誤狀態不含marker(錯誤.value, 稽核參照唯一SECRET_MARKER)


def test_audit_resource_ref_valid_endpoint_version_req_uuid_order_and_new_dict():
    """resource 參照接受小寫 dotted type 與安全 id，輸出固定鍵序與新 dict。"""
    request_ref = AuditResourceRef("endpoint.version", "req_ab12")
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    uuid_ref = AuditResourceRef("endpoint.version", uuid)
    輸出 = request_ref.to_json()

    assert list(輸出) == ["resource_type", "resource_id"]
    assert 輸出 == {"resource_type": "endpoint.version", "resource_id": "req_ab12"}
    assert uuid_ref.to_json() == {"resource_type": "endpoint.version", "resource_id": uuid}
    assert type(輸出) is dict
    assert 輸出 is not request_ref.to_json()


@pytest.mark.parametrize(
    "resource_type",
    [
        b"endpoint",
        "Endpoint.version",
        "endpoint-version",
        "a" * 65,
        "pk_live",
        "svc.sk_test",
        "bearer_token",
        "a" * 64,
    ],
)
def test_audit_resource_ref_rejects_bad_resource_type_exact_type_case_hyphen_length_and_secret(
    resource_type,
):
    """resource_type 只接受 exact str 小寫 code，且拒絕 secret/digest 特徵。"""
    with pytest.raises(AuditReferenceError):
        AuditResourceRef(resource_type, "req_1")


@pytest.mark.parametrize(
    "resource_id",
    [
        "svc.sk_test_123",
        "svc.pk_live_123",
        "id:BearerToken",
        "user:" + "a" * 64,
        "/Users/example/token",
        稽核資源識別唯一SECRET_MARKER,
    ],
)
def test_audit_resource_ref_rejects_bad_resource_id_using_shared_safe_identifier_validator(
    resource_id,
):
    """resource_id 重用稽核安全識別值規則，拒絕 secret、digest、path 與 marker。"""
    with pytest.raises(AuditReferenceError):
        AuditResourceRef("endpoint.version", resource_id)


def test_audit_resource_ref_rejects_str_subclasses_for_both_fields():
    """resource 參照欄位只接受 exact str，不接受 str subclass。"""
    for args in (
        (EvilStr("endpoint.version"), "req_1"),
        ("endpoint.version", EvilStr("req_1")),
    ):
        with pytest.raises(AuditReferenceError):
            AuditResourceRef(*args)


def test_audit_resource_ref_is_frozen():
    """resource 參照本身為 frozen dataclass。"""
    resource = AuditResourceRef("endpoint.version", "req_1")

    with pytest.raises(FrozenInstanceError):
        resource.resource_id = "req_2"


def test_audit_resource_type_error_sanitizes_all_package_frames():
    """resource_type 錯誤不可在任何發布介面 frame locals 留下敏感 marker。"""
    with pytest.raises(AuditReferenceError) as 錯誤:
        AuditResourceRef(稽核資源型別唯一SECRET_MARKER, "req_1")

    _領域模型錯誤狀態不含marker(錯誤.value, 稽核資源型別唯一SECRET_MARKER)


def test_audit_resource_id_error_sanitizes_all_package_frames():
    """resource_id 錯誤不可在任何發布介面 frame locals 留下敏感 marker。"""
    with pytest.raises(AuditReferenceError) as 錯誤:
        AuditResourceRef("endpoint.version", 稽核資源識別唯一SECRET_MARKER)

    _領域模型錯誤狀態不含marker(錯誤.value, 稽核資源識別唯一SECRET_MARKER)


def test_audit_metadata_empty_and_exact_output_order():
    """AuditMetadata 空值與輸入 insertion order 都必須穩定輸出。"""
    empty = AuditMetadata()
    metadata = AuditMetadata({"enabled": True, "count": 3, "ratio": 1.25, "missing": None})

    assert empty.to_json() == {}
    assert list(metadata.to_json()) == ["enabled", "count", "ratio", "missing"]
    assert metadata.to_json() == {"enabled": True, "count": 3, "ratio": 1.25, "missing": None}


@pytest.mark.parametrize(
    "value",
    [False, True, 0, -1, 2**63 - 1, -(2**63), 0.0, -1.5, 1.0, None],
)
def test_audit_metadata_accepts_exact_scalar_boundaries(value):
    """metadata value 只接受 bool、int、finite float 與 None 的 exact type。"""
    assert AuditMetadata({"value": value}).to_json() == {"value": value}


@pytest.mark.parametrize(
    "key",
    ["", "A", "_bad", "bad-", "1bad", "a" * 65],
)
def test_audit_metadata_rejects_bad_key_formats(key):
    """metadata key 必須符合白名單格式。"""
    with pytest.raises(AuditMetadataError):
        AuditMetadata({key: True})


@pytest.mark.parametrize(
    "key",
    [
        "master",
        "private",
        "filesystem",
        "master_version",
        "private_ref",
        "filesystem_root",
        "raw_value",
        "path",
        "hash",
        "sha256",
        "file_path",
        "content_hash",
        "path_id",
        "schema_path",
        "sha256_digest",
        "token_count",
    ],
)
def test_audit_metadata_rejects_sensitive_keys(key):
    """metadata key 不可含敏感片段。"""
    with pytest.raises(AuditMetadataError):
        AuditMetadata({key: True})


@pytest.mark.parametrize("key", ["a", "a1_b2", "endpoint_version", "schema_version"])
def test_audit_metadata_accepts_safe_key_formats(key):
    """合法格式且不含敏感片段的 metadata key 不可被誤拒。"""
    assert AuditMetadata({key: True}).to_json() == {key: True}


@pytest.mark.parametrize(
    "value",
    [
        "raw_api_key_唯一SECRET_MARKER",
        "cipher_唯一SECRET_MARKER",
        "a" * 64,
        "/Users/example/private.txt",
        "唯一SECRET_MARKER_audit_string",
    ],
)
def test_audit_metadata_rejects_string_values_and_sanitizes_production_frames(value):
    """字串 value 一律拒絕，且 production traceback locals 不保留原始敏感值。"""
    marker = value
    with pytest.raises(AuditMetadataError) as 錯誤:
        AuditMetadata({"allowed": value})

    value = None
    _領域模型錯誤狀態不含marker(錯誤.value, marker)


def test_audit_metadata_rejects_mapping_items_exception_without_exception_context():
    """惡意 Mapping.items exception 必須轉成固定錯誤且不保留原始例外鏈。"""
    metadata = EvilItemsRaisesMapping(稽核ITEMS錯誤唯一SECRET_MARKER)

    with pytest.raises(AuditMetadataError) as 錯誤:
        AuditMetadata(metadata)

    metadata = None
    _領域模型錯誤狀態不含marker(錯誤.value, 稽核ITEMS錯誤唯一SECRET_MARKER)


def test_audit_metadata_rejects_mapping_items_duplicate_exact_key_without_later_wins():
    """custom Mapping.items 回傳重複 exact key 必須 fail closed，不能 later-wins。"""
    metadata = EvilDuplicateItemsMapping(稽核重複鍵唯一SECRET_MARKER)

    with pytest.raises(AuditMetadataError) as 錯誤:
        AuditMetadata(metadata)

    metadata = None
    _領域模型錯誤狀態不含marker(錯誤.value, 稽核重複鍵唯一SECRET_MARKER)


def test_audit_metadata_rejects_malformed_mapping_items_with_sanitized_error():
    """custom Mapping.items回傳異常pair時也必須轉成無marker的固定錯誤。"""
    with pytest.raises(AuditMetadataError) as 錯誤:
        AuditMetadata(EvilMalformedItemsMapping())

    _領域模型錯誤狀態不含marker(錯誤.value, 稽核異常PAIR唯一SECRET_MARKER)


class EvilInt(int):
    """測試用 int subclass，避免被 exact type 檢查放行。"""


class EvilFloat(float):
    """測試用 float subclass，避免被 exact type 檢查放行。"""


@pytest.mark.parametrize(
    "value",
    [{"nested": True}, [1], b"secret", object(), math.nan, math.inf, -math.inf, EvilInt(1), EvilFloat(1.0)],
)
def test_audit_metadata_rejects_nested_bytes_nonfinite_and_numeric_subclasses(value):
    """metadata value 不接受巢狀容器、bytes、custom object、非有限 float 或 numeric subclass。"""
    with pytest.raises(AuditMetadataError):
        AuditMetadata({"allowed": value})


def test_audit_metadata_caller_mapping_mutation_does_not_affect_snapshot():
    """建構後會建立 defensive snapshot，呼叫端 mapping 後續 mutation 不影響實例。"""
    source = {"first": True, "second": 2}
    metadata = AuditMetadata(source)

    source["first"] = False
    source["third"] = 3

    assert metadata.to_json() == {"first": True, "second": 2}


def test_audit_metadata_to_json_returns_new_mutable_dict_without_affecting_instance():
    """to_json 每次回傳 ordinary new dict，修改輸出不影響 frozen 實例。"""
    metadata = AuditMetadata({"count": 1})
    output = metadata.to_json()

    output["count"] = 2

    assert type(output) is dict
    assert metadata.to_json() == {"count": 1}


def test_audit_metadata_is_frozen():
    """AuditMetadata 實例凍結，外部不可重新指定內部快照欄位。"""
    metadata = AuditMetadata({"enabled": True})

    with pytest.raises(FrozenInstanceError):
        metadata._資料 = {}


def test_解析嚴格JSON接受一般JSON值並保留陣列順序():
    """解析 strict JSON 後回傳標準 Python JSON value。"""
    資料 = 解析嚴格JSON('{"b":[2,1],"a":{"文字":"繁中","空":null}}')

    assert 資料 == {"b": [2, 1], "a": {"文字": "繁中", "空": None}}


@pytest.mark.parametrize(
    "原始文字",
    [
        '{"a":1,"a":2}',
        '{"outer":{"x":1,"x":2}}',
    ],
)
def test_解析嚴格JSON拒絕頂層與巢狀重複鍵(原始文字):
    """object key 在任何層級重複都不是公開契約允許的 JSON。"""
    with pytest.raises(嚴格JSON錯誤):
        解析嚴格JSON(原始文字)


@pytest.mark.parametrize("原始文字", ["NaN", "Infinity", "-Infinity", '{"x": NaN}'])
def test_解析嚴格JSON拒絕非有限數值(原始文字):
    """stdlib json 預設接受的 NaN/Infinity 必須被拒絕。"""
    with pytest.raises(嚴格JSON錯誤):
        解析嚴格JSON(原始文字)


def test_解析嚴格JSON拒絕語法錯誤且錯誤不含原始payload():
    """錯誤訊息不可回洩原始 payload。"""
    原始文字 = '{"secret":"不可出現在錯誤訊息",'

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        解析嚴格JSON(原始文字)

    assert 原始文字 not in str(錯誤.value)
    assert "不可出現在錯誤訊息" not in str(錯誤.value)


def test_解析嚴格JSON語法錯誤不保留payload於例外鏈與traceback_locals():
    """public error 物件、例外鏈與 traceback locals 都不可保留 raw payload。"""
    原始文字 = f'{{"secret":"{解析錯誤唯一SECRET_MARKER}",'

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        解析嚴格JSON(原始文字)

    原始文字 = None
    _錯誤狀態不含marker(錯誤.value, 解析錯誤唯一SECRET_MARKER)


def test_解析嚴格JSON只接受字串輸入():
    """bytes 等非 str 輸入必須明確拒絕。"""
    with pytest.raises(嚴格JSON錯誤):
        解析嚴格JSON(b'{"a":1}')


def test_建立正規JSON輸出穩定排序無多餘空白且保留Unicode():
    """正規 JSON 必須穩定、精簡，且不把 Unicode escape 成 ASCII。"""
    資料 = {"b": [2, 1], "a": {"文字": "繁中", "布林": True, "空": None}}

    assert 建立正規JSON(資料) == '{"a":{"布林":true,"文字":"繁中","空":null},"b":[2,1]}'


def test_建立正規JSON不修改輸入資料():
    """canonical 建立過程不可改變呼叫端傳入的 dict/list。"""
    資料 = {"b": [{"z": 1, "a": 2}], "a": [3, 2, 1]}
    原本 = {"b": [{"z": 1, "a": 2}], "a": [3, 2, 1]}

    建立正規JSON(資料)

    assert 資料 == 原本


@pytest.mark.parametrize(
    "資料",
    [
        {1: "非字串鍵"},
        {"tuple": (1, 2)},
        {"set": {1, 2}},
        {"bytes": b"abc"},
        {"object": object()},
        {"nan": math.nan},
        {"inf": math.inf},
        {"neg_inf": -math.inf},
    ],
)
def test_建立正規JSON拒絕非JSON值(資料):
    """只接受 JSON value 型別與有限 float。"""
    with pytest.raises(嚴格JSON錯誤):
        建立正規JSON(資料)


def test_建立正規JSON拒絕self_referential_list且不保留例外鏈():
    """cyclic list 必須轉成公開嚴格 JSON 錯誤而非 RecursionError。"""
    資料 = []
    資料.append(資料)

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立正規JSON(資料)

    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None


def test_建立正規JSON拒絕self_referential_dict且不保留例外鏈():
    """cyclic dict 必須轉成公開嚴格 JSON 錯誤而非 RecursionError。"""
    資料 = {}
    資料["self"] = 資料

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立正規JSON(資料)

    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None


def test_建立正規JSON過深nested_list轉公開錯誤且不洩漏marker():
    """實際 Python recursion overflow 必須轉成 sanitized public error。"""
    資料 = 深層錯誤唯一SECRET_MARKER
    for _ in range(2000):
        資料 = [資料]

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立正規JSON(資料)

    資料 = None
    _錯誤狀態不含marker(錯誤.value, 深層錯誤唯一SECRET_MARKER)


def test_建立正規JSON允許不同keys共享同一child_list且無cycle():
    """cycle detection 只追蹤目前路徑，不能把共享 child 當成 cycle。"""
    child = [1, {"ok": True}]
    資料 = {"a": child, "b": child}

    assert 建立正規JSON(資料) == '{"a":[1,{"ok":true}],"b":[1,{"ok":true}]}'


def test_建立正規JSON允許bool且不被int判斷誤傷():
    """bool 是 int subclass，但在 JSON 契約中是合法布林值。"""
    assert 建立正規JSON({"否": False, "是": True}) == '{"否":false,"是":true}'


def test_正規JSON雜湊忽略dict順序與來源空白():
    """同一 JSON object 的插入順序與 parse 來源空白不影響 digest。"""
    第一份 = {"b": 2, "a": {"y": 1, "x": [True, None]}}
    第二份 = 解析嚴格JSON(' { "a" : { "x" : [ true , null ] , "y" : 1 } , "b" : 2 } ')

    assert 計算正規JSON雜湊(第一份) == 計算正規JSON雜湊(第二份)


def test_正規JSON雜湊保留陣列順序差異():
    """array order 是語意的一部分，順序不同 digest 必須不同。"""
    assert 計算正規JSON雜湊([1, 2, 3]) != 計算正規JSON雜湊([3, 2, 1])


@pytest.mark.parametrize(
    "dto",
    [
        EndpointRef("ep_1", "hello", 3),
        InvocationRef("inv_1", "req_1"),
        PublishedUsage(7),
        PublishedWarning("notice", "metadata omitted"),
        PublishedError("endpoint_not_found", "not found"),
        ServiceAccountSnapshotRef("sa_1", "ver_1", "digest"),
    ],
)
def test_公開DTO全部凍結(dto):
    """公開 DTO 都是 frozen dataclass。"""
    欄位名稱 = next(iter(dto.to_json()))

    with pytest.raises(FrozenInstanceError):
        setattr(dto, 欄位名稱, "changed")


def test_endpoint_ref_to_json_exact_fields():
    """EndpointRef 輸出固定欄位與順序。"""
    輸出 = EndpointRef("ep_1", "hello", 3).to_json()

    assert list(輸出) == ["id", "slug", "version"]
    assert 輸出 == {"id": "ep_1", "slug": "hello", "version": 3}


def test_invocation_ref_to_json_exact_fields且session_nullable():
    """InvocationRef 輸出固定欄位，session_id 可為 None。"""
    無session輸出 = InvocationRef("inv_1", "req_1").to_json()
    有session輸出 = InvocationRef("inv_2", "req_2", "session_1").to_json()

    assert list(無session輸出) == ["id", "request_id", "session_id"]
    assert 無session輸出 == {"id": "inv_1", "request_id": "req_1", "session_id": None}
    assert 有session輸出 == {"id": "inv_2", "request_id": "req_2", "session_id": "session_1"}


def test_published_usage_to_json_exact_fields且tokens_nullable():
    """PublishedUsage 輸出固定欄位，total_tokens 可為 None。"""
    未知用量輸出 = PublishedUsage().to_json()
    已知用量輸出 = PublishedUsage(7).to_json()

    assert list(未知用量輸出) == ["total_tokens"]
    assert 未知用量輸出 == {"total_tokens": None}
    assert 已知用量輸出 == {"total_tokens": 7}


def test_published_warning_to_json_exact_fields():
    """PublishedWarning 輸出固定欄位與順序。"""
    輸出 = PublishedWarning("notice", "metadata omitted").to_json()

    assert list(輸出) == ["code", "message"]
    assert 輸出 == {"code": "notice", "message": "metadata omitted"}


def test_published_error_to_json_exact_fields():
    """PublishedError 輸出固定欄位與順序。"""
    輸出 = PublishedError("endpoint_not_found", "not found").to_json()

    assert list(輸出) == ["code", "message"]
    assert 輸出 == {"code": "endpoint_not_found", "message": "not found"}


def test_service_account_snapshot_ref_to_json_exact_fields且不帶runtime_context():
    """ServiceAccountSnapshotRef 只公開參照欄位，不暴露 runtime context。"""
    參考 = ServiceAccountSnapshotRef("sa_1", "ver_1", "digest")
    輸出 = 參考.to_json()
    禁止欄位 = {
        "owner",
        "memory",
        "session",
        "global_skill",
        "workdir",
        "provider_secret",
        "provider_secrets",
    }

    assert list(輸出) == [
        "service_account_id",
        "endpoint_version_id",
        "permission_snapshot_digest",
    ]
    assert 輸出 == {
        "service_account_id": "sa_1",
        "endpoint_version_id": "ver_1",
        "permission_snapshot_digest": "digest",
    }
    assert 禁止欄位.isdisjoint(輸出)
    for 欄位 in 禁止欄位:
        assert 欄位 not in repr(參考)


def test_to_json回傳新dict且修改輸出不影響DTO():
    """共用 DTO to_json 使用新 dict，避免呼叫端修改輸出影響 frozen 實例。"""
    參考 = EndpointRef("ep_1", "hello", 3)
    輸出 = 參考.to_json()

    輸出["slug"] = "changed"

    assert 參考.slug == "hello"
    assert 參考.to_json() == {"id": "ep_1", "slug": "hello", "version": 3}


def test_invoke_envelope_success_failure_exact_keys_and_json_dumpable():
    """成功與 endpoint_not_found 失敗信封固定鍵序、nullability 與 JSON 輸出。"""
    success = InvokeEnvelope(
        ok=True,
        endpoint=EndpointRef("ep_1", "hello", 3),
        invocation=InvocationRef("inv_1", "req_1"),
        data={"items": [1, None, True]},
        usage=PublishedUsage(7),
        warnings=[PublishedWarning("notice", "metadata omitted")],
    ).to_json()
    failure = InvokeEnvelope(
        ok=False,
        endpoint=None,
        invocation=None,
        error=PublishedError("endpoint_not_found", "not found"),
    ).to_json()

    assert list(success) == ["ok", "endpoint", "invocation", "data", "usage", "warnings", "error"]
    assert success == {
        "ok": True,
        "endpoint": {"id": "ep_1", "slug": "hello", "version": 3},
        "invocation": {"id": "inv_1", "request_id": "req_1", "session_id": None},
        "data": {"items": [1, None, True]},
        "usage": {"total_tokens": 7},
        "warnings": [{"code": "notice", "message": "metadata omitted"}],
        "error": None,
    }
    assert failure == {
        "ok": False,
        "endpoint": None,
        "invocation": None,
        "data": None,
        "usage": None,
        "warnings": [],
        "error": {"code": "endpoint_not_found", "message": "not found"},
    }
    assert json.loads(json.dumps([success, failure], ensure_ascii=False)) == [success, failure]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ok": True, "endpoint": None, "invocation": InvocationRef("i", "r"), "data": None},
        {"ok": True, "endpoint": EndpointRef("e", "s", 1), "invocation": InvocationRef("i", "r"), "error": PublishedError("x", "x")},
        {"ok": False, "endpoint": None, "invocation": None, "error": PublishedError("x", "x"), "data": {}},
        {"ok": 1, "endpoint": None, "invocation": None, "error": PublishedError("x", "x")},
        {"ok": True, "endpoint": "bad", "invocation": InvocationRef("i", "r"), "data": None},
        {"ok": True, "endpoint": EndpointRef("e", "s", 1), "invocation": "bad", "data": None},
        {"ok": False, "endpoint": None, "invocation": None, "error": "bad"},
    ],
)
def test_invoke_envelope_rejects_inconsistent_combinations_and_wrong_dto_types(kwargs):
    """信封狀態組合與 DTO 型別錯誤都必須拒絕。"""
    with pytest.raises(Exception):
        InvokeEnvelope(**kwargs)


@pytest.mark.parametrize(
    "欄位, kwargs",
    [
        (
            "endpoint",
            {
                "ok": True,
                "endpoint": EvilEndpointRef("e", "s", 1),
                "invocation": InvocationRef("i", "r"),
                "data": None,
            },
        ),
        (
            "invocation",
            {
                "ok": True,
                "endpoint": EndpointRef("e", "s", 1),
                "invocation": EvilInvocationRef("i", "r"),
                "data": None,
            },
        ),
        (
            "usage",
            {
                "ok": True,
                "endpoint": EndpointRef("e", "s", 1),
                "invocation": InvocationRef("i", "r"),
                "data": None,
                "usage": EvilPublishedUsage(7),
            },
        ),
        (
            "warnings",
            {
                "ok": True,
                "endpoint": EndpointRef("e", "s", 1),
                "invocation": InvocationRef("i", "r"),
                "data": None,
                "warnings": [EvilPublishedWarning("notice", "metadata omitted")],
            },
        ),
        (
            "error",
            {
                "ok": False,
                "endpoint": None,
                "invocation": None,
                "error": EvilPublishedError("endpoint_not_found", "not found"),
            },
        ),
    ],
)
def test_invoke_envelope_boundary_rejects_nested_dto_subclasses(欄位, kwargs):
    """信封邊界只收公開 DTO exact type，避免 subclass 額外欄位被 to_json 洩出。"""
    with pytest.raises(ValueError):
        InvokeEnvelope(**kwargs)


def test_invoke_envelope_data_uses_defensive_deep_immutable_snapshot():
    """data 建構時深層快照，呼叫端與 to_json 輸出 mutation 都不影響內部。"""
    data = {"items": [{"name": "old"}]}
    envelope = InvokeEnvelope(ok=True, endpoint=EndpointRef("e", "s", 1), invocation=InvocationRef("i", "r"), data=data)

    data["items"][0]["name"] = "new"
    envelope.to_json()["data"]["items"][0]["name"] = "changed"

    assert envelope.to_json()["data"] == {"items": [{"name": "old"}]}


@pytest.mark.parametrize("data", [{"tuple": (1, 2)}, {"set": {1, 2}}, {"nan": math.nan}])
def test_invoke_envelope_rejects_non_json_data(data):
    """信封 data 只接受合法 JSON value。"""
    with pytest.raises(嚴格JSON錯誤):
        InvokeEnvelope(ok=True, endpoint=EndpointRef("e", "s", 1), invocation=InvocationRef("i", "r"), data=data)


def test_invoke_envelope_error_clears_marker_from_all_production_frames():
    """非 JSON 與 invariant 錯誤都不可在領域模型 traceback locals 保留 marker。"""
    marker = 信封錯誤唯一SECRET_MARKER
    cases = [
        {"endpoint": EndpointRef("e", "s", 1), "data": {"bad": object(), "marker": marker}},
        {"endpoint": marker, "data": None},
        {"endpoint": EvilEndpointRef("e", "s", 1, secret=marker), "data": None},
    ]
    for kwargs in cases:
        with pytest.raises(Exception) as 錯誤:
            InvokeEnvelope(ok=True, invocation=InvocationRef("i", "r"), **kwargs)
        kwargs = None
        _領域模型錯誤狀態不含marker(錯誤.value, marker)


def test_invoke_envelope_warnings_are_tuple_default_and_type_guarded():
    """warnings 內部為 immutable tuple，預設無 mutable container，且只收 warning DTO。"""
    warning = PublishedWarning("notice", "metadata omitted")
    empty = InvokeEnvelope(ok=True, endpoint=EndpointRef("e", "s", 1), invocation=InvocationRef("i", "r"), data=None)
    warned = InvokeEnvelope(
        ok=True,
        endpoint=EndpointRef("e", "s", 1),
        invocation=InvocationRef("i", "r"),
        data=None,
        warnings=[warning],
    )

    assert (empty.warnings, warned.warnings) == ((), (warning,))
    with pytest.raises(Exception):
        InvokeEnvelope(ok=True, endpoint=EndpointRef("e", "s", 1), invocation=InvocationRef("i", "r"), data=None, warnings=["bad"])


def test_factory_建立成功信封_exact_output_and_warning_passthrough():
    """成功 factory 只建立 ok True，並交由領域模型處理 warning 與 snapshot。"""
    data = {"items": [{"name": "old"}]}
    envelope = 建立成功信封(
        EndpointRef("ep_1", "hello", 3),
        InvocationRef("inv_1", "req_1"),
        data,
        usage=PublishedUsage(7),
        warnings=[PublishedWarning("notice", "metadata omitted")],
    )
    data["items"][0]["name"] = "new"

    assert envelope.to_json() == {
        "ok": True,
        "endpoint": {"id": "ep_1", "slug": "hello", "version": 3},
        "invocation": {"id": "inv_1", "request_id": "req_1", "session_id": None},
        "data": {"items": [{"name": "old"}]},
        "usage": {"total_tokens": 7},
        "warnings": [{"code": "notice", "message": "metadata omitted"}],
        "error": None,
    }


def test_factory_建立失敗信封_endpoint_not_found_null_refs_only():
    """endpoint_not_found 依 R84 固定不公開 endpoint/invocation refs。"""
    envelope = 建立失敗信封(PublishedError("endpoint_not_found", "not found"))

    assert envelope.to_json() == {
        "ok": False,
        "endpoint": None,
        "invocation": None,
        "data": None,
        "usage": None,
        "warnings": [],
        "error": {"code": "endpoint_not_found", "message": "not found"},
    }


@pytest.mark.parametrize(
    "code",
    ["invalid_api_key", "input_schema_invalid", "model_output_schema_invalid", "tool_execution_failed"],
)
def test_factory_建立失敗信封_non_not_found_requires_and_outputs_refs(code):
    """非 endpoint_not_found 失敗依 R93 必須帶 exact endpoint/invocation refs。"""
    envelope = 建立失敗信封(
        PublishedError(code, "failed"),
        endpoint=EndpointRef("ep_1", "hello", 3),
        invocation=InvocationRef("inv_1", "req_1", "session_1"),
        warnings=[PublishedWarning("notice", "metadata omitted")],
    )

    assert envelope.to_json() == {
        "ok": False,
        "endpoint": {"id": "ep_1", "slug": "hello", "version": 3},
        "invocation": {"id": "inv_1", "request_id": "req_1", "session_id": "session_1"},
        "data": None,
        "usage": None,
        "warnings": [{"code": "notice", "message": "metadata omitted"}],
        "error": {"code": code, "message": "failed"},
    }


def test_factory_建立失敗信封_rejects_endpoint_not_found_with_refs():
    """endpoint_not_found 不可同時帶 refs，避免 R84 外洩解析資訊。"""
    with pytest.raises(ValueError):
        建立失敗信封(
            PublishedError("endpoint_not_found", "not found"),
            endpoint=EndpointRef("ep_1", "hello", 3),
            invocation=InvocationRef("inv_1", "req_1"),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"endpoint": EndpointRef("ep_1", "hello", 3)},
        {"invocation": InvocationRef("inv_1", "req_1")},
        {},
    ],
)
def test_factory_建立失敗信封_rejects_non_not_found_missing_refs(kwargs):
    """非 endpoint_not_found 失敗缺任一 ref 都不符合 R93。"""
    with pytest.raises(ValueError):
        建立失敗信封(PublishedError("invalid_api_key", "failed"), **kwargs)


@pytest.mark.parametrize(
    "呼叫",
    [
        lambda: 建立成功信封(EvilEndpointRef("e", "s", 1), InvocationRef("i", "r"), None),
        lambda: 建立成功信封(EndpointRef("e", "s", 1), EvilInvocationRef("i", "r"), None),
        lambda: 建立成功信封(EndpointRef("e", "s", 1), InvocationRef("i", "r"), None, usage=EvilPublishedUsage(7)),
        lambda: 建立失敗信封(EvilPublishedError("endpoint_not_found", "x")),
        lambda: 建立失敗信封(PublishedError("invalid_api_key", "x"), endpoint=EvilEndpointRef("e", "s", 1), invocation=InvocationRef("i", "r")),
        lambda: 建立失敗信封(PublishedError("invalid_api_key", "x"), endpoint=EndpointRef("e", "s", 1), invocation=EvilInvocationRef("i", "r")),
    ],
)
def test_factory_rejects_wrong_dto_subclasses_before_domain_fallback(呼叫):
    """factory 先拒絕 subclass DTO，domain exact-type guard 仍保留最後防線。"""
    with pytest.raises(ValueError):
        呼叫()


def test_factory_建立成功信封_nonjson_error_clears_contract_frame_locals():
    """成功 data validation 失敗時，契約模組 frame locals 不保留 marker。"""
    marker = "唯一SECRET_MARKER_factory_success_data"
    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立成功信封(
            EndpointRef("e", "s", 1),
            InvocationRef("i", "r"),
            {"bad": object(), "marker": marker},
        )

    _契約模組錯誤狀態不含marker(錯誤.value, marker)


def test_factory_建立失敗信封_error_subclass_clears_contract_frame_locals():
    """失敗 error exact-type guard 失敗時，契約模組 frame locals 不保留 marker。"""
    marker = "唯一SECRET_MARKER_factory_failure_error"
    with pytest.raises(ValueError) as 錯誤:
        建立失敗信封(EvilPublishedError("endpoint_not_found", "x", secret=marker))

    _契約模組錯誤狀態不含marker(錯誤.value, marker)
