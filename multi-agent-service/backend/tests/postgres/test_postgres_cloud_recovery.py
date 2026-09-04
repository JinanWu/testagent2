from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from 繁中代理.發布介面.技能套件.CloudStorage權威 import CloudStorage技能套件權威


class Blob:
    def __init__(self, bucket, name, generation=None):
        self.bucket, self.name, self.generation = bucket, name, generation
        self.metadata = {"kind": "bundle"}
        self.time_created = datetime.fromtimestamp(1, timezone.utc)

    def upload_from_string(self, data, **kw):
        if self.name in self.bucket.objects:
            raise RuntimeError("PreconditionFailed")
        self.generation = 11
        self.bucket.objects[self.name] = (11, data if isinstance(data, bytes) else data.encode())
        if self.bucket.raise_after_write:
            raise OSError("ack lost")

    def reload(self, **kw):
        self.generation = self.bucket.objects[self.name][0]

    def download_as_bytes(self, **kw):
        generation, data = self.bucket.objects[self.name]
        if kw["if_generation_match"] != generation:
            raise RuntimeError("PreconditionFailed")
        self.generation = generation
        return data

    def delete(self, **kw):
        if self.bucket.objects[self.name][0] != kw["if_generation_match"]:
            raise RuntimeError("PreconditionFailed")
        del self.bucket.objects[self.name]


class Client:
    def __init__(self, bucket): self.bucket = bucket
    def list_blobs(self, bucket, **kw):
        for name, (generation, data) in self.bucket.objects.items():
            blob = Blob(self.bucket, name, generation)
            yield blob


class Bucket:
    name = "strict"
    def __init__(self):
        self.objects = {}
        self.raise_after_write = False
        self.client = Client(self)
    def blob(self, name, generation=None): return Blob(self, name, generation)


def publish(bucket, bundle="bundle-1"):
    source = Path("/private/tmp/c1-recovery-skill")
    source.mkdir(exist_ok=True)
    (source / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    return CloudStorage技能套件權威(bucket).發布(
        套件識別碼=bundle, 端點識別碼="endpoint-1", 端點版本識別碼="version-1",
        版本號碼=1, 建立時間=1, 建立者識別碼="owner-1", 技能表={"skill": source},
    )


def test_listing_is_bounded_detached_projection_and_ack_loss_requires_exact_readback():
    bucket = Bucket()
    bucket.raise_after_write = True
    receipt = publish(bucket)
    authority = CloudStorage技能套件權威(bucket)
    projection = authority.有界列出()[0]
    assert projection.object_key == receipt.object_key
    assert projection.generation == receipt.generation == 11
    assert projection.metadata == {"kind": "bundle"}


def test_precondition_conflict_is_not_ack_success():
    bucket = Bucket()
    publish(bucket)
    authority = CloudStorage技能套件權威(bucket)
    try:
        publish(bucket)
    except Exception as error:
        assert "耐久性未知" in str(error)
    else:
        raise AssertionError("conflicting immutable upload was accepted")


class ReceiptRepo:
    def __init__(self, state): self.state, self.calls = state, []
    def 新鮮查詢(self, *, version_id): return self.state
    def 收據相符(self, row, receipt): return True
    def 補齊收據(self, receipt, version_id, now): self.calls.append((receipt, version_id, now))


def test_recovery_reconciles_only_explicit_existing_version():
    bucket = Bucket()
    publish(bucket)
    authority = CloudStorage技能套件權威(bucket)
    repo = ReceiptRepo({"version_exists": True, "receipt": None})
    result = authority.雲端復原(收據儲存庫=repo, 現在=100)
    assert result == (("bundles/v1/bundle-1/manifest.json", "reconciled"),)
    assert repo.calls and repo.calls[0][1] == "version-1"
