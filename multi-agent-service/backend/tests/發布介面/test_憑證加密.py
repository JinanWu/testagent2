import base64
import hashlib
from dataclasses import replace

import pytest

from 繁中代理.發布介面.憑證.加密 import AESGCM密文, AESGCM憑證封套, 憑證加密錯誤


def test_產生固定entropy_api_key並roundtrip且repr不含明文():
    calls = []

    def random_bytes(length):
        calls.append(length)
        return bytes([length]) * length

    vault = AESGCM憑證封套({1: b"k" * 32}, 1, 隨機位元組=random_bytes)
    created = vault.產生並加密("endpoint-1", "credential-1")
    assert calls == [32, 12]
    assert len(created.api_key) == 46 and created.api_key.startswith("pk_")
    assert created.key_hash == hashlib.sha256(created.api_key.encode("ascii")).hexdigest()
    assert created.key_prefix == created.api_key[:12]
    assert created.key_last4 == created.api_key[-4:]
    assert created.api_key.encode() not in created.envelope.ciphertext
    assert created.api_key not in repr(created)
    assert created.api_key not in repr(created.envelope)
    assert vault.解密(created.envelope, "endpoint-1", "credential-1") == created.api_key


@pytest.mark.parametrize("binding", [("endpoint-2", "credential-1"), ("endpoint-1", "credential-2")])
def test_AAD_binding錯誤固定拒絕(binding):
    vault = AESGCM憑證封套({1: b"a" * 32}, 1)
    created = vault.產生並加密("endpoint-1", "credential-1")
    with pytest.raises(憑證加密錯誤, match="憑證解密失敗"):
        vault.解密(created.envelope, *binding)


def test_tamper_wrong_key與未知版本固定拒絕():
    vault = AESGCM憑證封套({1: b"a" * 32}, 1)
    created = vault.產生並加密("endpoint", "credential")
    tampered = replace(
        created.envelope,
        ciphertext=created.envelope.ciphertext[:-1] + bytes([created.envelope.ciphertext[-1] ^ 1]),
    )
    for reader, envelope in (
        (vault, tampered),
        (AESGCM憑證封套({1: b"b" * 32}, 1), created.envelope),
        (vault, replace(created.envelope, key_version=2)),
    ):
        with pytest.raises(憑證加密錯誤, match="憑證解密失敗"):
            reader.解密(envelope, "endpoint", "credential")


def test_multi_key可讀舊版且新資料使用active_version():
    old = AESGCM憑證封套({1: b"1" * 32}, 1).產生並加密("endpoint", "old")
    rotated = AESGCM憑證封套({1: b"1" * 32, 2: b"2" * 32}, 2)
    assert rotated.解密(old.envelope, "endpoint", "old") == old.api_key
    new = rotated.產生並加密("endpoint", "new")
    assert new.envelope.key_version == 2


@pytest.mark.parametrize(
    "factory",
    [lambda _n: b"short", lambda _n: "not-bytes", lambda _n: (_ for _ in ()).throw(RuntimeError("raw"))],
)
def test_entropy_source非exact_bytes固定拒絕(factory):
    vault = AESGCM憑證封套({1: b"k" * 32}, 1, 隨機位元組=factory)
    with pytest.raises(憑證加密錯誤, match="憑證加密失敗"):
        vault.產生並加密("endpoint", "credential")


def test_identity失敗traceback不保留generated_api_key():
    vault = AESGCM憑證封套({1: b"k" * 32}, 1, 隨機位元組=lambda n: b"z" * n)
    generated = "pk_" + base64.urlsafe_b64encode(b"z" * 32).rstrip(b"=").decode("ascii")
    with pytest.raises(憑證加密錯誤) as error:
        vault.產生並加密("../bad", "credential")
    traceback = error.value.__traceback__
    while traceback is not None:
        if "/憑證/加密.py" in traceback.tb_frame.f_code.co_filename:
            assert generated not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_keyring與envelope_shape_fail_closed():
    for keys, active in (({}, 1), ({1: b"short"}, 1), ({1: b"k" * 32}, 2)):
        with pytest.raises(憑證加密錯誤):
            AESGCM憑證封套(keys, active)
    vault = AESGCM憑證封套({1: b"k" * 32}, 1)
    with pytest.raises(憑證加密錯誤, match="憑證解密失敗"):
        vault.解密(AESGCM密文(1, b"short", b"ciphertext"), "endpoint", "credential")


def test_keyring失敗traceback不保留master_key():
    master_key = b"K" * 32
    with pytest.raises(憑證加密錯誤) as error:
        AESGCM憑證封套({1: master_key}, 2)
    traceback = error.value.__traceback__
    while traceback is not None:
        if "/憑證/加密.py" in traceback.tb_frame.f_code.co_filename:
            assert repr(master_key) not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_public_encrypt拒絕非canonical_base64url_token():
    vault = AESGCM憑證封套({1: b"k" * 32}, 1)
    canonical = vault.產生並加密("endpoint", "generated").api_key
    noncanonical = canonical[:-1] + "B"
    with pytest.raises(憑證加密錯誤, match="憑證加密失敗"):
        vault.加密(noncanonical, "endpoint", "credential")
