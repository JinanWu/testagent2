"""Cloud Storage immutable bundle authority.

The database stores only the receipt locator.  One bundle is one create-only
object and every read is pinned to the generation recorded in that receipt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from pathlib import Path
from typing import Any, Mapping

from .安全複製 import 掃描技能, 重驗檔案
from .清單 import 建立清單, 正規JSON
from .發布器 import 驗證已發布技能套件清單, 已驗證技能套件清單
from ..執行期.執行器 import 技能套件快照, 技能套件檔案

_KEY = re.compile(r"bundles/v1/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/manifest\.json\Z")
_REF = re.compile(r"bundles/v1/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/manifest\.json#generation=([1-9][0-9]*)\Z")
_CAP = 6 * 1024 * 1024


class CloudStorage套件發布錯誤(RuntimeError):
    """Fixed public error for storage, validation, collision, and uncertainty."""


@dataclass(frozen=True, slots=True)
class CloudStorage套件發布收據:
    套件識別碼: str
    清單參照: str
    清單摘要: str
    套件雜湊: str
    總位元組數: int
    bucket: str
    object_key: str
    generation: int

    @property
    def manifest_digest(self) -> str: return self.清單摘要
    @property
    def bundle_hash(self) -> str: return self.套件雜湊


@dataclass(frozen=True, slots=True)
class CloudStorage物件投影:
    """Detached listing projection; it never exposes a live Blob object."""
    object_key: str
    generation: int
    time_created: Any
    metadata: Mapping[str, str]

    @property
    def name(self) -> str: return self.object_key


def 解析CloudStorage清單參照(value: str) -> tuple[str, int]:
    m = _REF.fullmatch(value) if type(value) is str else None
    if not m or f"bundles/v1/{m.group(1)}/manifest.json#generation={int(m.group(2))}" != value:
        raise CloudStorage套件發布錯誤("技能套件載入失敗。")
    return f"bundles/v1/{m.group(1)}/manifest.json", int(m.group(2))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: Any) -> bytes:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]*", value) or len(value) % 4 == 1:
        raise ValueError
    raw = value.encode("ascii") + b"=" * ((4 - len(value) % 4) % 4)
    result = base64.urlsafe_b64decode(raw)
    if _b64(result) != value: raise ValueError
    return result


def _error() -> None:
    raise CloudStorage套件發布錯誤("技能套件發布失敗") from None


class CloudStorage技能套件權威:
    """Publish and load bundles through a supplied google-cloud-storage bucket."""
    __slots__ = ("_bucket", "_bucket_name", "_timeout")

    def __init__(self, bucket: Any, *, bucket_name: str | None = None, timeout: float = 30.0) -> None:
        if bucket is None or type(timeout) not in (int, float) or timeout <= 0:
            raise ValueError("Cloud Storage authority 無效")
        self._bucket = bucket
        self._bucket_name = bucket_name or str(bucket.name)
        if not self._bucket_name: raise ValueError("Cloud Storage bucket 無效")
        self._timeout = float(timeout)

    def _receipt(self, manifest: dict[str, Any], raw: bytes, generation: Any) -> CloudStorage套件發布收據:
        if type(generation) is not int or generation <= 0: _error()
        def field(name: str) -> Any:
            return manifest[name] if isinstance(manifest, Mapping) else getattr(manifest, name)
        bundle = field("bundle_id")
        key = f"bundles/v1/{bundle}/manifest.json"
        return CloudStorage套件發布收據(bundle, f"{key}#generation={generation}", hashlib.sha256(raw).hexdigest(), field("bundle_hash"), field("total_bytes"), self._bucket_name, key, generation)

    def _decode(self, raw: bytes, expected: CloudStorage套件發布收據 | None = None) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
        if type(raw) is not bytes or len(raw) > _CAP: _error()
        try:
            pairs = lambda items: {k: v for k, v in items} if len({k for k, _ in items}) == len(items) else (_ for _ in ()).throw(ValueError())
            env = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if raw != 正規JSON(env): raise ValueError
            if type(env) is not dict or set(env) != {"storage_manifest_version", "manifest", "files"} or env["storage_manifest_version"] != 1:
                raise ValueError
            inner = 正規JSON(env["manifest"])
            manifest = 驗證已發布技能套件清單(inner)
            files: dict[str, bytes] = {}
            for row in env["files"]:
                if type(row) is not dict or set(row) != {"path", "content_base64url"} or row["path"] in files:
                    raise ValueError
                files[row["path"]] = _unb64(row["content_base64url"])
            expected_paths = {x.path for x in manifest.copied_files}
            if set(files) != expected_paths or any(hashlib.sha256(files[x]).hexdigest() != next(y.sha256 for y in manifest.copied_files if y.path == x) or len(files[x]) != next(y.size_bytes for y in manifest.copied_files if y.path == x) for x in files):
                raise ValueError
            if expected and (manifest.bundle_id != expected.套件識別碼 or manifest.manifest_digest != expected.清單摘要 or manifest.bundle_hash != expected.套件雜湊 or manifest.total_bytes != expected.總位元組數):
                raise ValueError
            return manifest, inner, files
        except CloudStorage套件發布錯誤: raise
        except BaseException: _error()

    def _read(self, receipt: CloudStorage套件發布收據) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
        try:
            blob = self._bucket.blob(receipt.object_key, generation=receipt.generation)
            raw = blob.download_as_bytes(if_generation_match=receipt.generation, checksum="crc32c", timeout=self._timeout)
            return self._decode(raw, receipt)
        except CloudStorage套件發布錯誤: raise
        except BaseException: _error()

    def 發布(self, *, 套件識別碼: str, 端點識別碼: str, 端點版本識別碼: str, 版本號碼: int, 建立時間: float, 建立者識別碼: str, 技能表: Mapping[str, str | Path]) -> CloudStorage套件發布收據:
        try:
            scans = tuple(掃描技能(name, path) for name, path in sorted(技能表.items(), key=lambda x: x[0].encode("utf-8")))
            manifest, inner, _ = 建立清單(套件識別碼=套件識別碼, 端點識別碼=端點識別碼, 端點版本識別碼=端點版本識別碼, 版本號碼=版本號碼, 建立時間=建立時間, 建立者識別碼=建立者識別碼, 掃描列=scans)
            contents = []
            for scan in scans:
                for item in scan.檔案:
                    contents.append({"path": f"{scan.名稱}/{item.相對路徑}", "content_base64url": _b64(重驗檔案(scan, item))})
            contents.sort(key=lambda x: x["path"].encode("utf-8"))
            envelope = 正規JSON({"storage_manifest_version": 1, "manifest": manifest, "files": contents})
            key = f"bundles/v1/{套件識別碼}/manifest.json"
            blob = self._bucket.blob(key)
            try:
                blob.upload_from_string(envelope, content_type="application/json", if_generation_match=0, checksum="crc32c", timeout=self._timeout)
                generation = blob.generation
            except BaseException as upload_error:
                # ACK loss is reconciled by exact key; no delete and no fabricated generation.
                try:
                    existing = self._bucket.blob(key)
                    existing.reload(timeout=self._timeout)
                    generation = existing.generation
                    if type(generation) is not int or generation <= 0:
                        raise ValueError
                    observed = existing.download_as_bytes(
                        if_generation_match=generation, checksum="crc32c", timeout=self._timeout,
                    )
                    if observed != envelope:
                        raise ValueError
                except BaseException:
                    if type(upload_error).__name__ in {"NotFound", "NotFoundError"} or getattr(upload_error, "code", None) == 404:
                        raise CloudStorage套件發布錯誤("技能套件尚未提交") from None
                    raise CloudStorage套件發布錯誤("技能套件耐久性未知") from None
                if type(upload_error).__name__ in {"PreconditionFailed", "PreconditionFailedError"} or "PreconditionFailed" in str(upload_error):
                    raise CloudStorage套件發布錯誤("技能套件耐久性未知") from None
            receipt = self._receipt(manifest, inner, generation)
            self._read(receipt)
            return receipt
        except CloudStorage套件發布錯誤: raise
        except BaseException: _error()

    def 直接讀回(self, receipt: CloudStorage套件發布收據) -> bytes:
        _, raw, _ = self._read(receipt)
        return raw

    def 有界列出(self, *, 上限: int = 256) -> tuple[CloudStorage物件投影, ...]:
        """Return a detached, bounded key/generation projection."""
        if type(上限) is not int or not 1 <= 上限 <= 256: raise ValueError
        try:
            result = []
            for blob in self._bucket.client.list_blobs(self._bucket_name, prefix="bundles/v1/", max_results=上限, page_size=上限):
                generation = blob.generation
                if type(generation) is not int or generation <= 0: _error()
                metadata = getattr(blob, "metadata", None)
                if metadata is None: metadata = {}
                if type(metadata) is not dict or any(type(k) is not str or type(v) is not str for k, v in metadata.items()):
                    _error()
                result.append(CloudStorage物件投影(
                    blob.name, generation, getattr(blob, "time_created", None),
                    MappingProxyType(dict(metadata)),
                ))
                if len(result) == 上限: break
            return tuple(result)
        except CloudStorage套件發布錯誤: raise
        except BaseException: _error()

    def generation_CAS刪除(self, *, object_key: str, generation: int) -> None:
        """Delete only the observed immutable generation."""
        if not _KEY.fullmatch(object_key) or type(generation) is not int or generation <= 0: _error()
        try:
            self._bucket.blob(object_key, generation=generation).delete(if_generation_match=generation, timeout=self._timeout)
        except BaseException:
            _error()

    def 下載驗證投影(self, projection: CloudStorage物件投影) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
        """Download exactly one listed generation and reject any generation drift."""
        if type(projection) is not CloudStorage物件投影 or not _KEY.fullmatch(projection.object_key):
            _error()
        try:
            blob = self._bucket.blob(projection.object_key, generation=projection.generation)
            raw = blob.download_as_bytes(
                if_generation_match=projection.generation, checksum="crc32c", timeout=self._timeout,
            )
            if blob.generation != projection.generation:
                _error()
            return self._decode(raw)
        except CloudStorage套件發布錯誤: raise
        except BaseException: _error()

    def 雲端復原(self, *, 收據儲存庫: Any, 現在: float, 保存秒數: float = 0) -> tuple[Any, ...]:
        """Reconcile listed objects against fresh PG state; uncertainty retains objects."""
        if type(現在) not in (int, float) or type(保存秒數) not in (int, float) or 現在 < 0 or 保存秒數 < 0:
            raise ValueError("Cloud recovery input invalid")
        outcomes = []
        for projection in self.有界列出():
            try:
                manifest, inner, _ = self.下載驗證投影(projection)
                expected_key = f"bundles/v1/{manifest.bundle_id}/manifest.json"
                if expected_key != projection.object_key:
                    raise ValueError
                receipt = self._receipt(manifest, inner, projection.generation)
                state = 收據儲存庫.新鮮查詢(version_id=manifest.endpoint_version_id)
                version_exists = state.get("version_exists") is True if isinstance(state, dict) else False
                db_receipt = state.get("receipt") if isinstance(state, dict) else None
                if db_receipt is not None:
                    db_version = db_receipt.get("version_id") if isinstance(db_receipt, dict) else db_receipt[1]
                    if db_version != manifest.endpoint_version_id or not 收據儲存庫.收據相符(db_receipt, receipt):
                        raise ValueError
                    outcomes.append((projection.object_key, "retained")); continue
                if version_exists:
                    收據儲存庫.補齊收據(receipt, manifest.endpoint_version_id, 現在)
                    outcomes.append((projection.object_key, "reconciled")); continue
                created = projection.time_created
                age = None
                if isinstance(created, datetime): age = 現在 - created.timestamp()
                elif type(created) in (int, float): age = 現在 - created
                if age is not None and age > 保存秒數:
                    self.generation_CAS刪除(object_key=projection.object_key, generation=projection.generation)
                    outcomes.append((projection.object_key, "deleted"))
                else:
                    outcomes.append((projection.object_key, "recovery-required"))
            except BaseException:
                outcomes.append((projection.object_key, "recovery-required"))
        return tuple(outcomes)

    recovery = 雲端復原
    list_bounded = 有界列出
    download_pinned = 下載驗證投影
    delete_generation = generation_CAS刪除

    def 載入技能套件快照(self, endpoint_version_id: str, skill_bundle_hash: str, manifest_reference: str, source: str) -> 技能套件快照:
        try:
            if source != "endpoint_version_snapshot": raise ValueError
            key, generation = 解析CloudStorage清單參照(manifest_reference)
            bundle = key.split("/")[2]
            blob = self._bucket.blob(key, generation=generation)
            raw = blob.download_as_bytes(if_generation_match=generation, checksum="crc32c", timeout=self._timeout)
            manifest, raw, files = self._decode(raw)
            if manifest.endpoint_version_id != endpoint_version_id or manifest.bundle_hash != skill_bundle_hash:
                raise ValueError
            return 技能套件快照(endpoint_version_id=endpoint_version_id, skill_bundle_hash=skill_bundle_hash, manifest_digest=hashlib.sha256(raw).hexdigest(), 清單原始資料=raw, files=tuple(技能套件檔案(path=p, sha256=next(x.sha256 for x in manifest.copied_files if x.path == p), content=data) for p, data in sorted(files.items(), key=lambda x: x[0].encode("utf-8"))))
        except CloudStorage套件發布錯誤: raise
        except BaseException: raise CloudStorage套件發布錯誤("技能套件載入失敗。") from None


class CloudStorage技能套件協調器:
    """Controller-facing adapter; all bytes and validation remain in the authority."""
    __slots__ = ("_authority",)

    def __init__(self, authority: CloudStorage技能套件權威) -> None:
        if type(authority) is not CloudStorage技能套件權威:
            raise ValueError("Cloud Storage coordinator 無效") from None
        self._authority = authority

    def 讀取已驗證清單(self, receipt: CloudStorage套件發布收據) -> 已驗證技能套件清單:
        manifest, _, _ = self._authority._read(receipt)
        return manifest

    def 標記孤兒(self, receipt: CloudStorage套件發布收據) -> None:
        # Immutable objects are never renamed or moved; reconciliation owns deletion.
        return None

    def 協調(self, **_: Any) -> tuple[()]:
        return ()


CloudStorageBundleAuthority = CloudStorage技能套件權威
CloudStorageBundleReceipt = CloudStorage套件發布收據
