from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from 繁中代理.發布介面.技能套件.CloudStorage權威 import (
    CloudStorage技能套件權威,
    解析CloudStorage清單參照,
)


class _Blob:
    def __init__(self, store, name, generation=None):
        self.store, self.name, self.generation = store, name, generation

    def upload_from_string(self, data, **kwargs):
        assert kwargs["if_generation_match"] == 0
        if self.name in self.store:
            raise RuntimeError("PreconditionFailed")
        self.generation = 7
        self.store[self.name] = (self.generation, data.encode() if isinstance(data, str) else data)

    def download_as_bytes(self, **kwargs):
        generation, data = self.store[self.name]
        assert kwargs["if_generation_match"] == generation == self.generation
        return data

    def reload(self, **kwargs):
        self.generation = self.store[self.name][0]

    def delete(self, **kwargs):
        generation, _ = self.store[self.name]
        assert kwargs["if_generation_match"] == generation
        del self.store[self.name]


class _Bucket:
    name = "bundle-test"

    def __init__(self):
        self.store = {}

    def blob(self, name, generation=None):
        return _Blob(self.store, name, generation)


def test_publish_readback_is_generation_pinned_and_hashes_content():
    source = Path(tempfile.mkdtemp(prefix="c1-bundle-", dir="/private/tmp")) / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text("# test skill\n", encoding="utf-8")
    (source / "skill.py").write_text("return 1\n", encoding="utf-8")
    authority = CloudStorage技能套件權威(_Bucket())
    receipt = authority.發布(
        套件識別碼="bundle-1", 端點識別碼="endpoint-1", 端點版本識別碼="version-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"skill": source},
    )
    key, generation = 解析CloudStorage清單參照(receipt.清單參照)
    assert key == receipt.object_key and generation == receipt.generation == 7
    raw = authority.直接讀回(receipt)
    assert hashlib.sha256(raw).hexdigest() == receipt.清單摘要
    assert authority.載入技能套件快照(
        "version-1", receipt.套件雜湊, receipt.清單參照, "endpoint_version_snapshot",
    ).endpoint_version_id == "version-1"
