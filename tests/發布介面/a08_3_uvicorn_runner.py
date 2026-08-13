"""A08-3 process-isolated drain and inherited-socket Uvicorn runner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import threading

專案根 = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(專案根))

from fastapi.testclient import TestClient
from 繁中代理.模型供應商 import GeminiADC供應商
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照


def _版本回應(參數):
    prompt = 參數["messages"][0]["content"]
    version = "v2" if "SYSTEM-V2" in prompt else "v1"
    return version, 模型回應快照(
        json.dumps({"answer": version}, separators=(",", ":")),
        "stop", {"total_tokens": 1}, [],
    )


def _不連網(self, **參數):
    del self
    return _版本回應(參數)[1]


GeminiADC供應商.產生發布回應 = _不連網

import asgi  # noqa: E402
import uvicorn  # noqa: E402
from a08_3_formal_publish import 正式切換v2  # noqa: E402


def _呼叫(client, payload):
    return client.post(
        "/v1/endpoints/stable/invoke",
        headers={"Authorization": f"Bearer {os.environ['A08_3_API_KEY']}"},
        json={"input": payload},
    )


def _執行drain() -> None:
    """在可被parent kill的process內證明非零lease shutdown drain。"""
    已進入, 可返回, shutdown完成 = threading.Event(), threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def 阻塞模型(self, **參數):
        del self
        version, response = _版本回應(參數)
        users = json.dumps([x.get("content") for x in 參數["messages"] if x.get("role") == "user"])
        if "restart-drain" in users:
            已進入.set()
            if not 可返回.wait(10):
                raise RuntimeError("drain gate timeout")
        return response

    GeminiADC供應商.產生發布回應 = 阻塞模型
    client = TestClient(asgi.建立應用程式(), raise_server_exceptions=False)
    client.__enter__()
    first = _呼叫(client, {"before": True}).json()
    v2 = 正式切換v2(
        web=Path(os.environ["TESTAGENT2_DB_PATH"]),
        db=Path(os.environ["TESTAGENT2_PUBLISHED_DB_PATH"]),
        bundles=Path(os.environ["TESTAGENT2_PUBLISHED_BUNDLE_ROOT"]),
        skill_root=Path(os.environ["A08_3_SKILL_ROOT"]),
        owner=os.environ["A08_3_OWNER"], endpoint=os.environ["A08_3_ENDPOINT"],
    )
    result: dict[str, object] = {}

    def request() -> None:
        try:
            result["response"] = _呼叫(client, {"restart-drain": True}).json()
        except BaseException as error:
            errors.append(error)

    def shutdown() -> None:
        try:
            client.__exit__(None, None, None)
        except BaseException as error:
            errors.append(error)
        finally:
            shutdown完成.set()

    request_thread = threading.Thread(target=request)
    shutdown_thread = threading.Thread(target=shutdown)
    request_thread.start()
    if not 已進入.wait(10):
        raise AssertionError("request lease not entered")
    shutdown_thread.start()
    premature = shutdown完成.wait(.1)
    可返回.set()
    request_thread.join(10)
    shutdown_thread.join(10)
    if request_thread.is_alive() or shutdown_thread.is_alive() or errors:
        raise AssertionError(f"drain failed: {errors!r}")
    print(json.dumps({
        "pid": os.getpid(), "premature": premature, "v2": v2,
        "first": first, "drained": result["response"],
    }, separators=(",", ":")), flush=True)


def _執行server() -> None:
    監聽 = socket.socket(fileno=int(os.environ["A08_3_SOCKET_FD"]))
    設定 = uvicorn.Config(asgi.建立應用程式(), log_level="warning")
    uvicorn.Server(設定).run(sockets=[監聽])


if __name__ == "__main__":
    if os.environ.get("A08_3_MODE") == "drain":
        _執行drain()
    else:
        _執行server()
