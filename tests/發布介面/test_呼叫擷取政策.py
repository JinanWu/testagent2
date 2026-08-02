from dataclasses import FrozenInstanceError
import pytest

from 繁中代理.發布介面.呼叫.擷取政策 import 呼叫擷取命令, 擷取階段


def test_擷取命令DTO不可變且欄位精確():
    命令 = 呼叫擷取命令(
        階段=擷取階段.AUTHENTICATED, metadata_role="user",
        input_json='{"x":1}', metadata_json='{"role":"user"}',
        metadata_size_bytes=15, metadata_sha256="a" * 64,
    )
    assert 命令.階段 is 擷取階段.AUTHENTICATED
    assert 命令.input_json == '{"x":1}'
    with pytest.raises(FrozenInstanceError):
        命令.input_json = '{}'
