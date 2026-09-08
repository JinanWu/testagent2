"""terminal 走 Cloud Run Sandboxes 的邊界測試。

不呼叫真正的沙箱（地端沒有 `/usr/local/gcp/bin/sandbox`），只驗證：
    1. 開關預設關閉，既有行為完全不變。
    2. 開啟時組出正確的 `sandbox do ... -- bash -c` argv。
    3. 沙箱不可用時 fail closed，不會退回主機裸跑。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from 繁中代理.基本工具 import 執行終端指令
from 繁中代理.沙箱執行 import (
    工作階段狀態檔,
    在雲端執行,
    建立沙箱指令,
    沙箱不可用,
    沙箱可執行檔,
    沙箱是否允許連線,
    沙箱是否啟用,
)


@pytest.fixture(autouse=True)
def _隔離環境(monkeypatch):
    """把開關釘在關閉狀態，避免讀到開發者 .env。"""
    monkeypatch.setattr("繁中代理.沙箱執行.載入本機環境檔", lambda: None)
    monkeypatch.delenv("TERMINAL_SANDBOX", raising=False)
    monkeypatch.delenv("TERMINAL_SANDBOX_EGRESS", raising=False)
    monkeypatch.delenv("TERMINAL_SANDBOX_STATE_DIR", raising=False)
    # K_SERVICE 只有 Cloud Run 會注入；預設當成地端
    monkeypatch.delenv("K_SERVICE", raising=False)


@pytest.fixture()
def 假裝有沙箱(monkeypatch):
    """讓 沙箱是否可執行 回 True，不需要真的放一個檔案。"""
    monkeypatch.setattr("繁中代理.沙箱執行.沙箱是否可執行", lambda: True)


def test_地端未設定時直接執行():
    """確認地端沒設環境變數時維持既有行為——開發與 CI 不該被影響。"""
    assert 在雲端執行() is False
    assert 沙箱是否啟用() is False
    assert 沙箱是否允許連線() is False


def test_雲端未設定時仍要求沙箱(monkeypatch):
    """正式環境漏設 TERMINAL_SANDBOX 不得退化成主機裸跑。

    這是整個模組最重要的一條：fail open 的話，忘記設定就等於完全沒有隔離，
    而且沒有任何跡象看得出來。
    """
    monkeypatch.setenv("K_SERVICE", "agent-service")
    assert 在雲端執行() is True
    assert 沙箱是否啟用() is True


def test_明確關閉是唯一逃生口(monkeypatch):
    """確認雲端要繞過沙箱必須是刻意設定，不能是漏設。"""
    monkeypatch.setenv("K_SERVICE", "agent-service")
    for 值 in ("off", "0", "false", "no"):
        monkeypatch.setenv("TERMINAL_SANDBOX", 值)
        assert 沙箱是否啟用() is False


def test_雲端漏設時terminal直接報錯(monkeypatch):
    """端到端確認：雲端沒有沙箱可執行檔時，terminal 失敗而不是裸跑。"""
    import subprocess as _sp

    monkeypatch.setenv("K_SERVICE", "agent-service")
    monkeypatch.setattr("繁中代理.沙箱執行.沙箱是否可執行", lambda: False)

    def _不該被呼叫(*參數, **命名參數):
        raise AssertionError("沙箱不可用時絕不能退回主機執行")

    monkeypatch.setattr(_sp, "run", _不該被呼叫)
    with pytest.raises(沙箱不可用):
        執行終端指令({"command": "echo hi", "_runtime_workdir": "/tmp"})


@pytest.mark.parametrize("值,預期", [
    ("on", True), ("1", True), ("true", True), ("TRUE", True), ("yes", True),
    ("off", False), ("0", False), ("false", False), ("no", False),
    # 空字串與打錯字都算「未設定」，在地端 fixture 下退回直接執行
    ("", False), ("隨便", False),
])
def test_開關值解析(monkeypatch, 值, 預期):
    """確認肯定值開啟、否定值關閉、無法辨識的值退回依環境判斷。"""
    monkeypatch.setenv("TERMINAL_SANDBOX", 值)
    assert 沙箱是否啟用() is 預期


def test_建立沙箱指令的基本形狀(假裝有沙箱):
    """確認組出來的是 sandbox do --write -- bash -c '<指令>'。"""
    argv = 建立沙箱指令("echo hi", 工作目錄=None, 允許連線=False)
    assert argv == [
        str(沙箱可執行檔), "do", "--write", "--", "/usr/bin/bash", "-c", "echo hi",
    ]
    # 沒有 --allow-egress：對外連線預設擋住
    assert "--allow-egress" not in argv


def test_允許連線才加egress旗標(假裝有沙箱):
    """確認網路是明確 opt-in，不會因為忘了設就默默放行。"""
    assert "--allow-egress" in 建立沙箱指令("curl x", 允許連線=True)
    assert "--allow-egress" not in 建立沙箱指令("curl x", 允許連線=False)


def test_工作目錄在沙箱內切換且有跳脫(假裝有沙箱):
    """確認 cd 是在沙箱內做的，且含空白或引號的路徑不會被拆開或注入。"""
    argv = 建立沙箱指令("ls", 工作目錄="/tmp/有 空白的'目錄")
    內層 = argv[-1]
    assert 內層.startswith("cd ")
    assert 內層.endswith(" && ls")
    # shlex.quote 後整段路徑必須是單一參數，不能讓引號逃出來
    import shlex
    assert shlex.split(內層.removesuffix(" && ls"))[1] == "/tmp/有 空白的'目錄"


def test_沒有沙箱可執行檔時fail_closed(monkeypatch):
    """確認沙箱不可用是明確錯誤，而不是默默退回主機裸跑。

    這是本模組最重要的一條：靜默降級會讓人以為指令被隔離了，實際沒有。
    """
    monkeypatch.setattr("繁中代理.沙箱執行.沙箱是否可執行", lambda: False)
    with pytest.raises(沙箱不可用):
        建立沙箱指令("echo hi")


def test_開關關閉時terminal維持直接執行(monkeypatch):
    """確認預設路徑上 subprocess 仍以 shell=True 與 cwd 呼叫，行為零變動。"""
    捕捉: dict = {}

    def _假執行(args, **命名參數):
        捕捉["args"] = args
        捕捉.update(命名參數)
        return subprocess.CompletedProcess(args, 0, stdout="ok")

    monkeypatch.setattr(subprocess, "run", _假執行)
    結果 = 執行終端指令({"command": "echo hi", "_runtime_workdir": "/tmp"})

    assert 結果 == {"output": "ok", "exit_code": 0}
    assert 捕捉["args"] == "echo hi"
    assert 捕捉["shell"] is True
    # 解析工具路徑 會 resolve，macOS 上 /tmp 是 /private/tmp 的 symlink
    assert 捕捉["cwd"] == str(Path("/tmp").resolve())


def test_開關開啟時terminal改走沙箱argv(monkeypatch, 假裝有沙箱):
    """確認開啟後 subprocess 收到的是 argv 清單，且不得再帶 shell=True。"""
    monkeypatch.setenv("TERMINAL_SANDBOX", "on")
    捕捉: dict = {}

    def _假執行(args, **命名參數):
        捕捉["args"] = args
        捕捉.update(命名參數)
        return subprocess.CompletedProcess(args, 0, stdout="ok")

    monkeypatch.setattr(subprocess, "run", _假執行)
    執行終端指令({"command": "echo hi", "_runtime_workdir": "/tmp"})

    assert isinstance(捕捉["args"], list)
    assert 捕捉["args"][:3] == [str(沙箱可執行檔), "do", "--write"]
    # shell=True 加在 argv 清單上會變成只執行第一個元素，必須確定沒帶
    assert "shell" not in 捕捉
    assert "cwd" not in 捕捉


def test_同一工作階段共用同一個狀態檔(tmp_path, monkeypatch, 假裝有沙箱):
    """確認同 session 的兩次呼叫指向同一個 tar，檔案狀態才能延續。

    這是「上一輪 mkdir、下一輪寫檔案」能成立的前提——sandbox do 本身用完即刪。
    """
    monkeypatch.setenv("TERMINAL_SANDBOX_STATE_DIR", str(tmp_path))

    第一次 = 建立沙箱指令("mkdir 工作區", 工作階段識別碼="session-A")
    第二次 = 建立沙箱指令("echo hi > 工作區/a.txt", 工作階段識別碼="session-A")
    別的階段 = 建立沙箱指令("ls", 工作階段識別碼="session-B")

    def _取狀態檔(argv):
        return next(項目 for 項目 in argv if 項目.startswith("--sync-tar="))

    assert _取狀態檔(第一次) == _取狀態檔(第二次)
    assert _取狀態檔(第一次) != _取狀態檔(別的階段)


def test_沒有工作階段時不做狀態延續(tmp_path, monkeypatch, 假裝有沙箱):
    """確認沒有 session id 時不會硬湊一個共用檔——那會讓不同對話互相汙染。"""
    monkeypatch.setenv("TERMINAL_SANDBOX_STATE_DIR", str(tmp_path))
    argv = 建立沙箱指令("ls", 工作階段識別碼=None)
    assert not any(項目.startswith("--sync-tar=") for 項目 in argv)


def test_狀態檔名不受session_id字元影響(tmp_path, monkeypatch):
    """確認惡意 session id 無法造成路徑穿越——檔名一律是雜湊。"""
    monkeypatch.setenv("TERMINAL_SANDBOX_STATE_DIR", str(tmp_path))
    路徑 = 工作階段狀態檔("../../etc/passwd")
    assert 路徑 is not None
    assert 路徑.parent == tmp_path
    assert 路徑.name.endswith(".tar")
    assert ".." not in 路徑.name
