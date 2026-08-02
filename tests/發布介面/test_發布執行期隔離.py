import hashlib
import pytest

from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行錯誤, 計算技能套件雜湊,
)


def test_bundle_DTO與canonical_hash可完整呼叫():
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        技能套件檔案(path="../SKILL.md", sha256="a" * 64, content=b"x")
    內容 = b"ok"
    檔案 = 技能套件檔案(
        path="SKILL.md", sha256=hashlib.sha256(內容).hexdigest(), content=內容,
    )
    摘要 = 計算技能套件雜湊((檔案,))
    快照 = 技能套件快照(
        endpoint_version_id="ver-1", skill_bundle_hash=摘要,
        manifest_digest=摘要, files=(檔案,),
    )
    assert 快照.files == (檔案,) and len(摘要) == 64
