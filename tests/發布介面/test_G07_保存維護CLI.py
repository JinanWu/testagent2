"""GOV G07 明確 dry-run／execute 保存維護 CLI 契約。"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.維護 import 執行主程式
from 繁中代理.發布介面.治理.保存期限 import 保存候選計畫, 保存清除結果

專案根目錄 = Path(__file__).resolve().parents[2]
模組 = "繁中代理.發布介面.維護"


def _秒(年: int, 月: int = 1, 日: int = 1) -> float:
    """建立固定 UTC epoch 秒。"""
    return datetime(年, 月, 日, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def 資料庫(tmp_path: Path) -> Path:
    """建立含一筆到期完整相依與一筆未到期根的資料庫。"""
    路徑 = (tmp_path / "PRIVATE_DATABASE_G07.sqlite").resolve()
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('sa',0,NULL)")
        連線.execute("INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES('ep','owner','sa','slug','active',0,0)")
        連線.execute("INSERT INTO published_endpoint_versions VALUES('ver','ep',1,'r','s','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)")
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
        for 識別碼, 建立 in (("PRIVATE_ELIGIBLE_G07", _秒(2020)), ("fresh", _秒(2024))):
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) VALUES(?,?,?,?,?,'{}',?)",
                (識別碼, "ep", "ver", "req-" + 識別碼, "succeeded", 建立),
            )
        識別碼 = "PRIVATE_ELIGIBLE_G07"
        連線.execute("INSERT INTO run_events VALUES(?,?,?,?,?,?)", ("run", 識別碼, 1, "event", '{"PRIVATE_PAYLOAD_G07":1}', 0))
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,run_event_id,sequence_number,tool_name,arguments_json,outcome,result_json,created_at) VALUES('tool',?,'run',1,'tool','{}','success','{}',0)",
            (識別碼,),
        )
        連線.execute(
            "INSERT INTO audit_events VALUES('audit','event',0,'retention.test','success','system',NULL,'invocation',?,NULL,'ep',?,'{}',0)",
            (識別碼, 識別碼),
        )
        連線.execute(
            "INSERT INTO endpoint_redactions VALUES('red',?,'run_event','run','',?,'privacy','system',NULL,'audit',1,0)",
            (識別碼, "a" * 64),
        )
    return 路徑


def _執行(*參數: str) -> subprocess.CompletedProcess[str]:
    """以真實 module entrypoint 執行隔離子程序。"""
    return subprocess.run(
        [sys.executable, "-m", 模組, *參數], cwd=專案根目錄,
        text=True, capture_output=True, check=False,
    )


def test_頂層與子命令help可探索且成功() -> None:
    """固定文件要求的 module invocation 與 retention flags。"""
    頂層 = _執行("--help")
    子層 = _執行("retention", "--help")
    assert 頂層.returncode == 子層.returncode == 0
    assert "retention" in 頂層.stdout
    assert all(旗標 in 子層.stdout for 旗標 in
               ("--database", "--now-epoch", "--batch-limit", "--dry-run", "--execute"))
    assert not 頂層.stderr and not 子層.stderr


def test_dry_run只讀且輸出固定聚合JSON(資料庫: Path) -> None:
    """真實 G05 規劃不改寫檔案，亦不揭露識別碼、路徑或 payload。"""
    執行前 = 資料庫.read_bytes()
    結果 = _執行("retention", "--database", str(資料庫), "--now-epoch", str(_秒(2025)),
             "--batch-limit", "1", "--dry-run")
    assert 結果.returncode == 0 and not 結果.stderr
    assert json.loads(結果.stdout) == {
        "mode": "dry-run", "candidate_count": 1, "run_event_count": 1,
        "tool_call_count": 1, "redaction_count": 1, "audit_event_count": 1,
        "earliest_deadline": _秒(2025), "latest_deadline": _秒(2025),
    }
    assert 結果.stdout.count("\n") == 1
    assert 資料庫.read_bytes() == 執行前
    assert all(秘密 not in 結果.stdout for 秘密 in
               (str(資料庫), "PRIVATE_ELIGIBLE_G07", "PRIVATE_PAYLOAD_G07"))


def test_execute明確清除一次且第二次全零(資料庫: Path) -> None:
    """真實 G06 每次只呼叫一批，第二次執行保持冪等。"""
    參數 = ("retention", "--database", str(資料庫), "--now-epoch", str(_秒(2025)),
          "--batch-limit", "1", "--execute")
    第一次 = _執行(*參數)
    第二次 = _執行(*參數)
    assert 第一次.returncode == 第二次.returncode == 0
    assert json.loads(第一次.stdout) == {
        "mode": "execute", "invocation_count": 1, "run_event_count": 1,
        "tool_call_count": 1, "redaction_count": 1, "audit_event_count": 1,
    }
    assert json.loads(第二次.stdout) == {
        "mode": "execute", "invocation_count": 0, "run_event_count": 0,
        "tool_call_count": 0, "redaction_count": 0, "audit_event_count": 0,
    }
    with closing(sqlite3.connect(資料庫)) as 連線:
        assert 連線.execute("SELECT id FROM endpoint_invocations").fetchall() == [("fresh",)]


@pytest.mark.parametrize("額外", [
    (), ("--dry-run", "--execute"), ("--now-epoch", "nan", "--dry-run"),
    ("--now-epoch", "-1", "--dry-run"), ("--now-epoch", "1e3", "--dry-run"),
    ("--batch-limit", "0", "--dry-run"), ("--batch-limit", "1001", "--dry-run"),
])
def test_缺少或衝突模式與非法數值皆為argparse二(資料庫: Path, 額外: tuple[str, ...]) -> None:
    """所有前置 CLI 契約錯誤由 argparse 以 2 拒絕。"""
    基本 = ["retention", "--database", str(資料庫)]
    if "--now-epoch" not in 額外:
        基本 += ["--now-epoch", "0"]
    結果 = _執行(*基本, *額外)
    assert 結果.returncode == 2 and not 結果.stdout


def test_相對路徑與缺少資料庫分屬argparse二及固定私密失敗一(tmp_path: Path) -> None:
    """無效 shape 不執行；domain/db 失敗不洩漏路徑或 traceback。"""
    相對 = _執行("retention", "--database", "PRIVATE_RELATIVE_G07", "--now-epoch", "0", "--dry-run")
    絕對路徑 = str((tmp_path / "PRIVATE_MISSING_G07.sqlite").resolve())
    缺少 = _執行("retention", "--database", 絕對路徑, "--now-epoch", "0", "--dry-run")
    assert 相對.returncode == 2 and not 相對.stdout
    assert 缺少.returncode == 1 and not 缺少.stdout
    assert 缺少.stderr == "retention maintenance failed\n"
    assert 絕對路徑 not in 缺少.stderr and "Traceback" not in 缺少.stderr


def test_注入工廠每種模式只呼叫對應服務一次(資料庫: Path, capsys) -> None:
    """dry-run 不觸及清除服務，execute 不觸及規劃器且各自只呼叫一次。"""
    次數 = {"plan_factory": 0, "plan": 0, "purge_factory": 0, "purge": 0}
    class 假規劃器:
        """記錄一次規劃呼叫。"""
        def 規劃(self, _現在, *, 候選上限):
            """回傳一筆固定 G05 計畫。"""
            次數["plan"] += 1
            return (保存候選計畫("secret", 1.0, (), (), (), (), 0, 0, 0, 0, (), ()),)
    class 假清除服務:
        """記錄一次清除呼叫。"""
        def 清除(self, _現在, *, 批次上限):
            """回傳固定 G06 計數。"""
            次數["purge"] += 1
            return 保存清除結果(1, 0, 0, 0, 0)
    def 規劃工廠(_路徑):
        """建立記錄規劃器。"""
        次數["plan_factory"] += 1
        return 假規劃器()
    def 清除工廠(_路徑):
        """建立記錄清除服務。"""
        次數["purge_factory"] += 1
        return 假清除服務()
    基本 = ["retention", "--database", str(資料庫), "--now-epoch", "0"]
    assert 執行主程式([*基本, "--dry-run"], 規劃器工廠=規劃工廠, 清除服務工廠=清除工廠) == 0
    assert 執行主程式([*基本, "--execute"], 規劃器工廠=規劃工廠, 清除服務工廠=清除工廠) == 0
    capsys.readouterr()
    assert 次數 == {"plan_factory": 1, "plan": 1, "purge_factory": 1, "purge": 1}


class 自訂Base(BaseException):
    """驗證 ordinary custom Base 固定化。"""


def test_注入工廠ordinary固定化而KISG保持exact(資料庫: Path, capsys) -> None:
    """embedding 工廠失敗遵循固定錯誤與 Python 控制流程政策。"""
    參數 = ["retention", "--database", str(資料庫), "--now-epoch", "0", "--dry-run"]
    assert 執行主程式(參數, 規劃器工廠=lambda _路徑: (_ for _ in ()).throw(自訂Base("PRIVATE_G07"))) == 1
    捕捉輸出 = capsys.readouterr()
    assert not 捕捉輸出.out and 捕捉輸出.err == "retention maintenance failed\n"
    原始 = KeyboardInterrupt("CONTROL_G07")
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        執行主程式(參數, 規劃器工廠=lambda _路徑: (_ for _ in ()).throw(原始))
    assert 捕捉.value is 原始
