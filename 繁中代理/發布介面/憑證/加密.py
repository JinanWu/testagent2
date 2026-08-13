"""端點API key產生、識別摘要與AES-256-GCM封套。"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class 憑證加密錯誤(ValueError):
    """固定、不含secret或crypto internals的credential錯誤。"""


@dataclass(frozen=True, slots=True)
class AESGCM密文:
    """可持久化的versioned AES-GCM envelope。"""

    key_version: int
    nonce: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class 新APIKey:
    """僅create/reveal邊界短暫持有明文；repr永不顯示敏感欄位。"""

    api_key: str = field(repr=False)
    key_hash: str
    key_prefix: str
    key_last4: str
    envelope: AESGCM密文 = field(repr=False)


class AESGCM憑證封套:
    """使用DB外multi-key keyring加密endpoint-bound API keys。

    描述：使用DB外multi-key keyring加密endpoint-bound API keys。
    參數：建構資料由類別欄位或建構器簽章明確提供，不讀取隱含輸入。
    返回值：可供呼叫端使用的``AESGCM憑證封套``類型或實例。
    """

    def __init__(
        self,
        keys: Mapping[int, bytes],
        active_version: int,
        *,
        隨機位元組: Callable[[int], bytes] = os.urandom,
    ) -> None:
        """複製並驗證AES-256 keyring；key material不進repr或DB。

        描述：複製並驗證AES-256 keyring；key material不進repr或DB。
        參數：``keys``、``active_version``、``隨機位元組``。
        返回值：依函式型別標註或既有協定回傳結果。
        """
        copied: dict[int, bytes] = {}
        加密器: dict[int, AESGCM] = {}
        key: bytes | None = None
        try:
            if type(keys) is not dict or type(active_version) is not int or active_version <= 0:
                raise ValueError
            for version, key in keys.items():
                if type(version) is not int or version <= 0 or type(key) is not bytes or len(key) != 32:
                    raise ValueError
                copied[version] = key
            if active_version not in copied or not callable(隨機位元組):
                raise ValueError
            加密器.update((version, AESGCM(金鑰材料)) for version, 金鑰材料 in copied.items())
        except Exception:
            key = None
            copied.clear()
            加密器.clear()
            del keys, copied
            raise 憑證加密錯誤("憑證加密失敗") from None
        copied.clear()
        self._加密器 = 加密器
        self._active_version = active_version
        self._隨機位元組 = 隨機位元組

    def 產生並加密(self, endpoint_id: str, credential_id: str) -> 新APIKey:
        """使用OS CSPRNG產生固定32-byte entropy token並加密。"""
        隨機資料 = 憑證明文 = result = None
        是否失敗 = False
        try:
            隨機資料 = self._取得隨機位元組(32)
            憑證明文 = "pk_" + base64.urlsafe_b64encode(隨機資料).rstrip(b"=").decode("ascii")
            result = self.加密(憑證明文, endpoint_id, credential_id)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            隨機資料 = 憑證明文 = result = None
            del self, endpoint_id, credential_id
            raise
        except BaseException:
            是否失敗 = True
        隨機資料 = 憑證明文 = None
        del self, endpoint_id, credential_id
        if 是否失敗 or result is None:
            raise 憑證加密錯誤("憑證加密失敗") from None
        return result

    def 加密(self, api_key: str, endpoint_id: str, credential_id: str) -> 新APIKey:
        """以endpoint/credential/version AAD加密合法平台API key。

        描述：以endpoint/credential/version AAD加密合法平台API key。
        參數：``api_key``、``endpoint_id``、``credential_id``。
        返回值：依函式型別標註或既有協定回傳結果。
        """
        憑證明文 = api_key
        del api_key
        result: 新APIKey | None = None
        金鑰版本 = 單次隨機值 = 加密內容 = None
        是否失敗 = False
        try:
            self._驗證APIKey(憑證明文)
            金鑰版本 = self._active_version
            單次隨機值 = self._取得隨機位元組(12)
            加密內容 = self._加密器[金鑰版本].encrypt(
                單次隨機值,
                憑證明文.encode("ascii"),
                self._AAD(endpoint_id, credential_id, 金鑰版本),
            )
            result = 新APIKey(
                憑證明文,
                hashlib.sha256(憑證明文.encode("ascii")).hexdigest(),
                憑證明文[:12],
                憑證明文[-4:],
                AESGCM密文(金鑰版本, 單次隨機值, 加密內容),
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            result = 金鑰版本 = 單次隨機值 = 加密內容 = 憑證明文 = None
            del endpoint_id, credential_id, self
            raise
        except BaseException:
            是否失敗 = True
        金鑰版本 = 單次隨機值 = 加密內容 = 憑證明文 = None
        del endpoint_id, credential_id, self
        if 是否失敗 or result is None:
            raise 憑證加密錯誤("憑證加密失敗") from None
        return result

    def 解密(self, envelope: AESGCM密文, endpoint_id: str, credential_id: str) -> str:
        """依envelope version與相同AAD解密；tamper/wrong binding一律固定拒絕。

        描述：依envelope version與相同AAD解密；tamper/wrong binding一律固定拒絕。
        參數：``envelope``、``endpoint_id``、``credential_id``。
        返回值：依函式型別標註或既有協定回傳結果。
        """
        if (
            type(envelope) is not AESGCM密文
            or type(envelope.key_version) is not int
            or type(envelope.nonce) is not bytes
            or len(envelope.nonce) != 12
            or type(envelope.ciphertext) is not bytes
            or len(envelope.ciphertext) != 62
            or envelope.key_version not in self._加密器
        ):
            raise 憑證加密錯誤("憑證解密失敗") from None
        plaintext: bytes | None = None
        api_key: str | None = None
        try:
            plaintext = self._加密器[envelope.key_version].decrypt(
                envelope.nonce,
                envelope.ciphertext,
                self._AAD(endpoint_id, credential_id, envelope.key_version),
            )
            api_key = plaintext.decode("ascii")
            self._驗證APIKey(api_key)
        except (InvalidTag, UnicodeDecodeError, ValueError, 憑證加密錯誤):
            plaintext = None
            api_key = None
            raise 憑證加密錯誤("憑證解密失敗") from None
        if api_key is None:
            raise 憑證加密錯誤("憑證解密失敗") from None
        return api_key

    def _取得隨機位元組(self, length: int) -> bytes:
        """要求entropy source回傳exact bytes length。"""
        value = None
        是否失敗 = False
        try:
            value = self._隨機位元組(length)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            value = None
            del self, length
            raise
        except BaseException:
            是否失敗 = True
        if type(value) is not bytes or len(value) != length:
            value = None
            是否失敗 = True
        del self, length
        if 是否失敗:
            raise 憑證加密錯誤("憑證加密失敗") from None
        return value

    @staticmethod
    def _AAD(endpoint_id: str, credential_id: str, version: int) -> bytes:
        """以length-prefix避免identity串接歧義。"""
        for identity in (endpoint_id, credential_id):
            if type(identity) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", identity) is None:
                raise 憑證加密錯誤("憑證加密失敗") from None
        endpoint = endpoint_id.encode("ascii")
        credential = credential_id.encode("ascii")
        header = b"testagent2.endpoint-credential.v1\x00" + version.to_bytes(4, "big")
        return header + len(endpoint).to_bytes(2, "big") + endpoint + len(credential).to_bytes(2, "big") + credential

    @staticmethod
    def _驗證APIKey(api_key: str) -> None:
        """只接受本平台32-byte entropy base64url token。"""
        if not APIKey格式有效(api_key):
            del api_key
            raise 憑證加密錯誤("憑證加密失敗") from None


def APIKey格式有效(api_key) -> bool:
    """驗證exact32-byte、canonical unpadded base64url平台token。"""
    if type(api_key) is not str or re.fullmatch(r"pk_[A-Za-z0-9_-]{43}", api_key) is None:
        return False
    encoded = api_key[3:]
    try:
        decoded = base64.b64decode(encoded + "=", altchars=b"-_", validate=True)
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    except (ValueError, UnicodeError):
        return False
    return len(decoded) == 32 and canonical == encoded
