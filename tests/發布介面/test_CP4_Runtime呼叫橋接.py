"""CP4 Published Runtime single-attempt bridge 與 pinned-tool loop。"""
from dataclasses import dataclass
import hashlib
import json
from types import SimpleNamespace

import pytest

import 繁中代理.發布介面.呼叫.編排器 as INV模組
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.呼叫.生產橋接 import 驗證釘選輸出結構
from 繁中代理.發布介面.呼叫.編排器 import (
    執行嘗試紀錄收據, 執行嘗試請求, 外部呼叫編排器,
)
from 繁中代理.發布介面.領域模型 import EndpointRef, InvocationRef
from 繁中代理.發布介面.執行期.工具發布庫 import (
    工具發布庫, 工具發布描述, 工具發布註冊,
)
from 繁中代理.發布介面.執行期.工具結果 import 工具設定錯誤, 工具逾時
from 繁中代理.發布介面.執行期.工具版本庫 import (
    工具快照項目, 建立版本釘選工具登錄器, 計算工具修訂摘要,
)
from 繁中代理.發布介面.執行期.模型契約 import 供應商逾時, 模型回應快照, 模型設定快照
from 繁中代理.發布介面.執行期.服務帳戶 import ServiceAccountContext
from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行快照, 計算技能套件雜湊,
)
from 繁中代理.發布介面.執行期.呼叫橋接 import 建立發布執行嘗試橋接


@dataclass(frozen=True, slots=True)
class _Pin:
    endpoint_id: str
    service_account_id: str
    version_id: str
    version_number: int

    def 取得版本快照(self):
        return SimpleNamespace(response_schema={
            "type": "object", "properties": {"answer": {"type": "string"}},
            "required": ["answer"], "additionalProperties": False,
        })


class _Repo:
    def __init__(self, snapshot, context):
        self.snapshot, self.context, self.calls = snapshot, context, []

    def 取得發布執行快照(self, version):
        self.calls.append(("snapshot", version))
        return self.snapshot

    def 載入服務帳戶上下文(self, account, version, source):
        self.calls.append(("account", account, version, source))
        return self.context


class _BundleLoader:
    def __init__(self, bundle):
        self.bundle, self.calls = bundle, []

    def 載入技能套件快照(self, *args):
        self.calls.append(args)
        return self.bundle


class _Releases:
    def __init__(self, repository):
        self.repository, self.calls = repository, []

    def 取得發布(self, release):
        self.calls.append(release)
        return self.repository.取得發布(release)


class _Model:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def 產生發布回應(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _materials(*, structured=False, schema=None, handler=None, provider="fake", release="rel-old"):
    handler = handler or (lambda args: {"release": "old", "value": args["value"]})
    parameters = {
        "type": "object", "properties": {"value": {"type": "integer"}},
        "required": ["value"], "additionalProperties": False,
    }
    tool = 工具定義("lookup", "pinned lookup", parameters, handler)
    releases = 工具發布庫()
    releases.登錄發布(工具發布描述(release, (工具發布註冊("rev-1", tool),)))
    digest = 計算工具修訂摘要(
        name="lookup", revision="rev-1", description="pinned lookup", parameters=parameters,
    )
    item = 工具快照項目("lookup", "rev-1", digest)
    content = b"runtime"
    file = 技能套件檔案(path="SKILL.md", sha256=hashlib.sha256(content).hexdigest(), content=content)
    bundle_hash = 計算技能套件雜湊((file,))
    bundle = 技能套件快照(
        endpoint_version_id="ver-1", skill_bundle_hash=bundle_hash,
        manifest_digest=hashlib.sha256(b"{}").hexdigest(), 清單原始資料=b"{}", files=(file,),
    )
    snapshot = 發布執行快照(
        endpoint_id="ep-1", version_id="ver-1", service_account_id="sa-1",
        system_prompt="pinned", permission_snapshot_digest="a" * 64,
        skill_bundle_hash=bundle_hash, tool_handler_release=release, tool_snapshot=(item,),
        model_config=模型設定快照(provider, "model", 0, 100, 2, structured, 1),
        response_schema=schema, manifest_reference="bundle-1/manifest.json",
    )
    context = ServiceAccountContext(
        service_account_id="sa-1", endpoint_version_id="ver-1",
        permission_snapshot_digest="a" * 64, allowed_tools=("lookup",),
        skill_bundle_hash=bundle_hash, tool_handler_release=release,
    )
    return snapshot, context, bundle, releases


def _bridge(model, materials):
    snapshot, context, bundle, release_repo = materials
    repo, loader, releases = _Repo(snapshot, context), _BundleLoader(bundle), _Releases(release_repo)
    bridge = 建立發布執行嘗試橋接(
        發布快照儲存庫=repo, 技能套件載入器=loader, 工具發布庫=releases,
        模型供應商註冊表={"fake": model},
    )
    request = 執行嘗試請求(_Pin("ep-1", "sa-1", "ver-1", 1), {"q": 1}, {"role": "system"}, 1)
    return bridge, request, repo, loader, releases


def _inv_orchestrator(bridge):
    orchestrator = 外部呼叫編排器(
        object(), object(), object(), 解析未找到型別=LookupError, 釘選型別=_Pin,
        驗證型別=object, 驗證狀態型別=object, 階段型別=object,
        準備擷取=lambda *args: object(), 寫入擷取=lambda *args, **kwargs: "inv-1",
        限流決策型別=object, 提交雙層計數=lambda *args: None,
        驗證輸入=lambda data: True, 執行嘗試=bridge, 驗證輸出=驗證釘選輸出結構,
        記錄執行嘗試=lambda invocation, request, result, valid: 執行嘗試紀錄收據(
            invocation.id, request.attempt, True, request.attempt,
        ),
    )
    pin = _Pin("ep-1", "sa-1", "ver-1", 1)
    def start(
        短名: str, 請求識別: str, 提供的API金鑰: str, 輸入資料: object,
        中繼資料: object | None, 驗證時間: int | float,
    ) -> INV模組.外部呼叫入口:
        del 短名, 請求識別, 提供的API金鑰, 輸入資料, 中繼資料, 驗證時間
        return INV模組.外部呼叫入口(
            EndpointRef("ep-1", "demo", 1), InvocationRef("inv-1", "req-1"),
            pin, None, None, INV模組._正規呼叫快照("{}", None),
        )

    orchestrator.開始 = start
    return orchestrator


def test_single_attempt結構不符不在bridge內重試():
    schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
    model = _Model([模型回應快照('{"wrong":true}', "stop", {"total_tokens": 7}, [])])
    bridge, request, repo, _, releases = _bridge(model, _materials(structured=True, schema=schema))

    result = bridge(request)

    assert result.kind == "success" and result.data == {"wrong": True}
    assert result.usage.total_tokens == 7 and len(model.calls) == 1
    assert repo.calls[0] == ("snapshot", "ver-1") and releases.calls == ["rel-old"]
    assert model.calls[0]["messages"][1]["role"] == "user"
    assert request.metadata == {"role": "system"} and request.metadata not in model.calls[0]["messages"]


def test_single_attempt_malformed結構文字仍交給INV判定():
    schema = {"type": "object", "required": ["answer"]}
    model = _Model([模型回應快照("not-json", "stop", {"total_tokens": 4}, [])])
    bridge, request, *_ = _bridge(model, _materials(structured=True, schema=schema))

    result = bridge(request)

    assert result.kind == "success" and result.data == "not-json"
    assert result.usage is not None and result.usage.total_tokens == 4
    assert len(model.calls) == 1


def test_valid_structured_JSON投影為exact_builtins():
    schema = {"type": "object"}
    text = '{"answer":[true,1,1.5,null,{"x":"y"}]}'
    model = _Model([模型回應快照(text, "stop", {}, [])])
    bridge, request, *_ = _bridge(model, _materials(structured=True, schema=schema))

    data = bridge(request).data

    assert type(data) is dict and type(data["answer"]) is list
    assert [type(value) for value in data["answer"]] == [bool, int, float, type(None), dict]
    assert type(data["answer"][-1]["x"]) is str


@pytest.mark.parametrize("texts,expected", [
    (("not-json", '{"answer":"ok"}'), "success"),
    (("not-json", "still-not-json"), "model_output_schema_invalid"),
])
def test_INV對malformed結構輸出恰執行兩次(texts, expected):
    model = _Model([模型回應快照(text, "stop", {}, []) for text in texts])
    bridge, *_ = _bridge(model, _materials(structured=True, schema={"type": "object"}))

    result = _inv_orchestrator(bridge).執行("demo", "req-1", "key", {}, None, 1)

    assert len(model.calls) == 2
    if expected == "success":
        assert result.status_code == 200 and result.envelope.data == {"answer": "ok"}
    else:
        assert result.to_json()["envelope"]["error"]["code"] == expected


def test_tool_loop只執行snapshot_release且附加結果再呼叫模型():
    called = []
    def old(args):
        called.append(("old", args.copy()))
        return {"release": "old"}
    materials = _materials(handler=old)
    # 同名新 release 存在，但 exact lookup 不得落到它。
    materials[3].登錄發布(工具發布描述(
        "rel-new", (工具發布註冊("rev-2", 工具定義(
            "lookup", "new", {"type": "object"}, lambda args: called.append(("new", args)),
        )),),
    ))
    call = {"id": "call-1", "type": "function",
            "function": {"name": "lookup", "arguments": '{"value":3}'}}
    model = _Model([
        模型回應快照("", "tool_calls", {"total": 1}, [call]),
        模型回應快照("done", "stop", {"total": 2}, []),
    ])
    bridge, request, _, _, releases = _bridge(model, materials)

    result = bridge(request)

    assert result.kind == "success" and result.data == "done"
    assert called == [("old", {"value": 3})] and releases.calls == ["rel-old"]
    assert len(model.calls) == 2
    assert model.calls[1]["messages"][-1]["role"] == "tool"
    assert json.loads(model.calls[1]["messages"][-1]["content"])["success"] is True
