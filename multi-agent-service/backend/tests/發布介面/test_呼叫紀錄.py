"""發布介面呼叫建立與結案儲存庫測試。"""

import inspect
import os
import sqlite3

import pytest

import 繁中代理.發布介面.呼叫.儲存庫 as 儲存庫模組
from 繁中代理.發布介面.呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫儲存錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

秘密 = "sk-不可寫入資料庫-唯一標記"


def _建立端點資料庫(tmp_path):
    """建立已遷移且有一個slug命中端點及版本的資料庫。"""
    路徑 = tmp_path / "invocations.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep','owner','svc','hit','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def _讀取呼叫(路徑):
    """回傳唯一呼叫資料列的dict快照。"""
    with sqlite3.connect(路徑) as 連線:
        連線.row_factory = sqlite3.Row
        資料列 = 連線.execute("SELECT * FROM endpoint_invocations").fetchone()
        return dict(資料列) if 資料列 is not None else None


def test_slug命中先建立pending且工作階段可為空(tmp_path):
    """已解析slug在處理前保存完整input，nullable session不建立偽連結。"""
    路徑 = _建立端點資料庫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 10, 識別碼工廠=lambda: "inv-1")

    識別碼 = 儲存庫.建立已解析呼叫("ep", "ver", "req-1", {"z": [1], "question": "安全內容"}, metadata={"z": 2, "a": 1})

    資料列 = _讀取呼叫(路徑)
    assert 識別碼 == "inv-1"
    assert (資料列["status"], 資料列["session_id"], 資料列["credential_id"]) == ("pending", None, None)
    assert (資料列["input_json"], 資料列["metadata_json"]) == ('{"question":"安全內容","z":[1]}', '{"a":1,"z":2}')


def test_允許明確狀態轉換並保存成功輸出(tmp_path):
    """pending只可開始，running可一次結案為succeeded並保存完整output。"""
    路徑 = _建立端點資料庫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 12, 識別碼工廠=lambda: "inv-2")
    儲存庫.建立已解析呼叫("ep", "ver", "req-2", "輸入", session_id=None)

    儲存庫.標記執行中("inv-2")
    儲存庫.完成呼叫("inv-2", "succeeded", output={"answer": "完整輸出"}, usage={"tokens": 3}, latency_ms=2)

    資料列 = _讀取呼叫(路徑)
    assert (資料列["status"], 資料列["output_json"], 資料列["error_json"]) == (
        "succeeded", '{"answer":"完整輸出"}', None,
    )
    assert (資料列["completed_at"], 資料列["latency_ms"]) == (12, 2)


def test_拒絕非法轉換且結案明確不冪等(tmp_path):
    """pending不可直接成功，完成後重送也固定拒絕而非覆寫紀錄。"""
    路徑 = _建立端點資料庫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 15, 識別碼工廠=lambda: "inv-3")
    儲存庫.建立已解析呼叫("ep", "ver", "req-3", {})
    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-3", "succeeded", output={})
    儲存庫.完成呼叫("inv-3", "invalid_api_key", error={"code": "invalid_api_key"})
    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-3", "invalid_api_key", error={"code": "changed"})
    assert _讀取呼叫(路徑)["error_json"] == '{"code":"invalid_api_key"}'


def test_秘密不進資料列_repr_錯誤且失敗交易回滾(tmp_path):
    """API key不屬於介面或欄位；資料庫錯誤固定化且duplicate交易不留半成品。"""
    路徑 = _建立端點資料庫(tmp_path)
    assert "api_key" not in inspect.signature(SQLite呼叫儲存庫.建立已解析呼叫).parameters
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 20, 識別碼工廠=iter(("inv-safe", "inv-other")).__next__)
    儲存庫.建立已解析呼叫("ep", "ver", "req-safe", {"payload": "safe"})
    with pytest.raises(呼叫儲存錯誤) as 錯誤:
        儲存庫.建立已解析呼叫("ep", "ver", "req-safe", {"payload": 秘密})
    assert 秘密 not in str(錯誤.value) + repr(錯誤.value) + repr(儲存庫) + repr([項目.frame.f_locals for 項目 in 錯誤.traceback])
    assert 秘密 not in repr(_讀取呼叫(路徑))
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT count(*) FROM endpoint_invocations").fetchone() == (1,)
        assert all("key" not in row[1].lower() for row in 連線.execute("PRAGMA table_info(endpoint_invocations)"))


def test_讀到畸形SQLite狀態時fail_closed(tmp_path):
    """SQLite動態型別繞過CHECK後，狀態讀取仍不可誤判為合法轉換。"""
    路徑 = _建立端點資料庫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 30, 識別碼工廠=lambda: "inv-bad")
    儲存庫.建立已解析呼叫("ep", "ver", "req-bad", {})
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET status=7 WHERE id='inv-bad'")
    with pytest.raises(呼叫儲存錯誤, match="呼叫狀態更新失敗"):
        儲存庫.標記執行中("inv-bad")


def _建立另一端點與憑證(路徑):
    """建立第二組端點、版本與憑證，供複合外鍵隔離測試使用。"""
    with sqlite3.connect(路徑) as 連線:
        連線.create_function("published_ip_allowlist_valid", 1, lambda _值: 1)
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES ('svc-2',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep-2','owner','svc-2','other','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver-2','ep-2',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver-2' WHERE id='ep-2'")
        連線.execute(
            "INSERT INTO endpoint_credentials("
            "id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,"
            "expires_at,last_used_at,created_at,updated_at,revoked_at,ip_allowlist_json,rate_limit_requests,"
            "created_by_user_id,revision) VALUES ("
            "'cred-2','ep-2','名稱','用途',1,zeroblob(12),zeroblob(62),"
            "'0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',"
            "'prefix','1234',99,NULL,0,0,NULL,'[]',1,'owner',0)"
        )


@pytest.mark.parametrize(
    ("endpoint_version_id", "credential_id"),
    [("ver-2", None), ("ver", "cred-2")],
)
def test_複合端點外鍵不符時建立交易完整回滾(tmp_path, endpoint_version_id, credential_id):
    """版本或憑證屬於另一端點時，各自命中複合外鍵且不留下呼叫。"""
    路徑 = _建立端點資料庫(tmp_path)
    _建立另一端點與憑證(路徑)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 40, 識別碼工廠=lambda: "inv-fk")

    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        儲存庫.建立已解析呼叫(
            "ep", endpoint_version_id, "req-fk", {}, credential_id=credential_id,
        )

    assert _讀取呼叫(路徑) is None


@pytest.mark.parametrize(
    ("status", "先執行中"),
    [("failed", False), ("failed", True), ("rate_limited", False)],
)
def test_保存允許的失敗與限流結案(tmp_path, status, 先執行中):
    """failed接受pending/running，rate_limited接受pending，並只保存error。"""
    路徑 = _建立端點資料庫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 41, 識別碼工廠=lambda: "inv-negative")
    儲存庫.建立已解析呼叫("ep", "ver", "req-negative", {})
    if 先執行中:
        儲存庫.標記執行中("inv-negative")

    儲存庫.完成呼叫("inv-negative", status, error={"code": status}, usage={"tokens": 1})

    資料列 = _讀取呼叫(路徑)
    assert (資料列["status"], 資料列["output_json"]) == (status, None)
    assert 資料列["error_json"] == f'{{"code":"{status}"}}'


class _計數字典(dict):
    """記錄正規JSON是否曾讀取items。"""

    def __init__(self, *args, **kwargs):
        """初始化字典內容與零次讀取計數。"""
        super().__init__(*args, **kwargs)
        self.讀取次數 = 0

    def items(self):
        """記錄動態 items 呼叫後委派內建實作。"""
        self.讀取次數 += 1
        return super().items()


class _計數時鐘:
    """記錄時鐘呼叫次數並回傳固定時間。"""

    def __init__(self, 值=50):
        """保存固定時間並初始化零次呼叫計數。"""
        self.值 = 值
        self.呼叫次數 = 0

    def __call__(self):
        """增加呼叫計數並回傳固定時間。"""
        self.呼叫次數 += 1
        return self.值


def test_非法與不存在狀態在時鐘及payload副作用前拒絕(tmp_path):
    """非法pending成功及不存在id都先在交易授權，敵意payload與時鐘保持零次。"""
    路徑 = _建立端點資料庫(tmp_path)
    建立時鐘 = _計數時鐘()
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=建立時鐘, 識別碼工廠=lambda: "inv-order")
    儲存庫.建立已解析呼叫("ep", "ver", "req-order", {})
    建立時鐘.呼叫次數 = 0

    for invocation_id in ("inv-order", "missing"):
        payload = _計數字典(answer="不可讀")
        with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
            儲存庫.完成呼叫(invocation_id, "succeeded", output=payload)
        assert (建立時鐘.呼叫次數, payload.讀取次數) == (0, 0)


def test_畸形未結案欄位在任何結案副作用前拒絕(tmp_path):
    """pending若已帶output即非合法完成前狀態，交易不可讀payload或時鐘。"""
    路徑 = _建立端點資料庫(tmp_path)
    時鐘 = _計數時鐘()
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=時鐘, 識別碼工廠=lambda: "inv-malformed")
    儲存庫.建立已解析呼叫("ep", "ver", "req-malformed", {})
    時鐘.呼叫次數 = 0
    with sqlite3.connect(路徑) as 連線:
        連線.execute("UPDATE endpoint_invocations SET output_json='{}' WHERE id='inv-malformed'")
    payload = _計數字典(code="不可讀")

    with pytest.raises(呼叫儲存錯誤, match="呼叫結案失敗"):
        儲存庫.完成呼叫("inv-malformed", "failed", error=payload)

    assert (時鐘.呼叫次數, payload.讀取次數) == (0, 0)


@pytest.mark.parametrize("情境", ["missing", "symlink", "empty", "malformed"])
def test_runtime只開啟既有非空一般SQLite檔(tmp_path, 情境):
    """缺檔、symlink、空檔及非SQLite內容都固定拒絕且不建立或追隨。"""
    路徑 = tmp_path / "runtime.sqlite3"
    if 情境 == "symlink":
        目標 = _建立端點資料庫(tmp_path)
        路徑 = tmp_path / "link.sqlite3"
        os.symlink(目標, 路徑)
    elif 情境 == "empty":
        路徑.touch()
    elif 情境 == "malformed":
        路徑.write_bytes(b"not sqlite")
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-db")

    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        儲存庫.建立已解析呼叫("ep", "ver", "req-db", {})

    if 情境 == "missing":
        assert not 路徑.exists()


def test_runtime拒絕缺少必要schema或ledger的資料庫(tmp_path):
    """一般SQLite檔若沒有精確呼叫表與前三版ledger，runtime不得自行遷移。"""
    路徑 = tmp_path / "wrong-schema.sqlite3"
    with sqlite3.connect(路徑) as 連線:
        連線.execute("CREATE TABLE unrelated(id TEXT)")
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-schema")

    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        儲存庫.建立已解析呼叫("ep", "ver", "req-schema", {})


class _敵意字串(str):
    """若 production 呼叫敵意字串方法便留下可觀測次數。"""

    呼叫次數 = 0

    def __str__(self):
        """記錄不應發生的字串轉換。"""
        type(self).呼叫次數 += 1
        return super().__str__()


class _敵意串列(list):
    """若 production 呼叫敵意串列覆寫方法便留下可觀測次數。"""

    呼叫次數 = 0

    def __iter__(self):
        """記錄不應發生的動態迭代。"""
        type(self).呼叫次數 += 1
        return super().__iter__()


@pytest.mark.parametrize("敵意值", [_敵意字串(秘密), _敵意串列([秘密]), _計數字典(secret=秘密)])
def test_巢狀非精確JSON值在共用正規器與敵意方法前拒絕(tmp_path, monkeypatch, 敵意值):
    """精確dict/list內的任一子類先拒絕，不呼叫其方法或共用正規器。"""
    路徑 = _建立端點資料庫(tmp_path)
    正規器次數 = 0

    def 假正規器(_值):
        """記錄拒絕前是否錯誤進入共用正規器。"""
        nonlocal 正規器次數
        正規器次數 += 1
        return "{}"

    monkeypatch.setattr(儲存庫模組, "建立正規JSON", 假正規器)
    if hasattr(敵意值, "讀取次數"):
        敵意值.讀取次數 = 0
    else:
        type(敵意值).呼叫次數 = 0
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-hostile")

    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        儲存庫.建立已解析呼叫("ep", "ver", "req-hostile", {"outer": [{"value": 敵意值}]})

    方法次數 = getattr(敵意值, "讀取次數", getattr(type(敵意值), "呼叫次數", None))
    assert (正規器次數, 方法次數, _讀取呼叫(路徑)) == (0, 0, None)


class _假結果:
    """提供交易假連線所需的最小 SQLite cursor result。"""

    def __init__(self, 資料列=None, rowcount=1):
        """保存可回傳資料列與受影響列數。"""
        self._資料列 = 資料列
        self.rowcount = rowcount

    def fetchone(self):
        """回傳設定的單一資料列。"""
        return self._資料列


class _交易假連線:
    """記錄交易離開、關閉與可選SQL失敗。"""

    def __init__(self, *, 寫入失敗=False, 關閉錯誤=None):
        """設定寫入或關閉失敗情境並初始化觀測值。"""
        self.寫入失敗 = 寫入失敗
        self.關閉錯誤 = 關閉錯誤
        self.離開例外 = []
        self.關閉次數 = 0
        self.提交次數 = 0
        self.回滾次數 = 0
        self.in_transaction = False

    def __enter__(self):
        """模擬 SQLite connection context 進入。"""
        return self

    def __exit__(self, 類型, _值, _追蹤):
        """記錄交易 context 收到的例外類型。"""
        self.離開例外.append(類型)

    def execute(self, SQL, _參數=None):
        """模擬狀態查詢與可選寫入失敗。"""
        if SQL.startswith("BEGIN"):
            self.in_transaction = True
            return _假結果()
        if SQL.startswith("SELECT id,endpoint_id,endpoint_version_id"):
            return _假結果(None)
        if SQL.startswith("SELECT status"):
            return _假結果(("pending", None, None, None, None, None, None))
        if self.寫入失敗 and (SQL.startswith("INSERT") or SQL.startswith("UPDATE")):
            raise sqlite3.OperationalError("不可外洩的資料庫細節")
        return _假結果()

    def commit(self):
        """記錄明確提交並離開交易。"""
        self.提交次數 += 1
        self.in_transaction = False

    def rollback(self):
        """記錄明確回滾並離開交易。"""
        self.回滾次數 += 1
        self.in_transaction = False

    def close(self):
        """記錄關閉並依設定丟出關閉錯誤。"""
        self.關閉次數 += 1
        if self.關閉錯誤 is not None:
            raise self.關閉錯誤


@pytest.mark.parametrize(("動作", "寫入失敗"), [("建立", False), ("建立", True), ("更新", False), ("更新", True)])
def test_建立與更新成功失敗皆交易離開後精確關閉一次(monkeypatch, 動作, 寫入失敗):
    """明確交易或既有context都只關閉一次；一般DB失敗先回滾再固定化。"""
    假連線 = _交易假連線(寫入失敗=寫入失敗)
    儲存庫 = SQLite呼叫儲存庫("unused", 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-close")
    monkeypatch.setattr(儲存庫, "_開啟連線", lambda: 假連線)

    if 動作 == "建立":
        呼叫 = lambda: 儲存庫.建立已解析呼叫("ep", "ver", "req-close", {})
    else:
        呼叫 = lambda: 儲存庫._更新狀態("inv-close", {"pending"}, "running")
    if 寫入失敗:
        with pytest.raises(呼叫儲存錯誤):
            呼叫()
        if 動作 == "建立":
            assert 假連線.提交次數 == 0 and 假連線.回滾次數 == 1
        else:
            assert 假連線.離開例外 == [sqlite3.OperationalError]
    else:
        呼叫()
        if 動作 == "建立":
            assert 假連線.提交次數 == 1 and 假連線.回滾次數 == 0
        else:
            assert 假連線.離開例外 == [None]
    assert 假連線.關閉次數 == 1


class _自訂基礎錯誤(BaseException):
    """非控制流程BaseException，邊界必須固定化。"""


def _斷言固定新錯誤(呼叫, 訊息):
    """同一失敗每次建立無鏈結的新公開錯誤。"""
    錯誤們 = []
    for _ in range(2):
        with pytest.raises(呼叫儲存錯誤, match=訊息) as 資訊:
            呼叫()
        assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None
        錯誤們.append(資訊.value)
    assert 錯誤們[0] is not 錯誤們[1]


def _內建樹包含標記(值, 標記, 已見):
    """只用精確內建操作遞迴掃描標記，不觸發敵意物件方法。"""
    值型別 = type(值)
    if 值型別 is str:
        return 標記 in 值
    if 值型別 is bytes:
        return 標記.encode() in 值
    if 值型別 not in (dict, list, tuple, set, frozenset):
        return False
    識別 = id(值)
    if 識別 in 已見:
        return False
    已見.add(識別)
    if 值型別 is dict:
        for 鍵, 項目 in dict.items(值):
            if _內建樹包含標記(鍵, 標記, 已見) or _內建樹包含標記(項目, 標記, 已見):
                return True
        return False
    迭代器 = {
        list: list.__iter__,
        tuple: tuple.__iter__,
        set: set.__iter__,
        frozenset: frozenset.__iter__,
    }[值型別]
    return any(_內建樹包含標記(項目, 標記, 已見) for 項目 in 迭代器(值))


@pytest.mark.parametrize("邊界", ["建立", "完成", "更新", "開啟"])
def test_自訂BaseException跨所有公開與內部邊界固定化(tmp_path, monkeypatch, 邊界):
    """非控制流程基礎錯誤不外洩，且依邊界產生fresh fixed error。"""
    儲存庫 = SQLite呼叫儲存庫(tmp_path / "missing", 識別碼工廠=lambda: "inv-base")
    if 邊界 == "建立":
        monkeypatch.setattr(儲存庫, "_識別碼工廠", lambda: (_ for _ in ()).throw(_自訂基礎錯誤(秘密)))
        呼叫, 訊息 = lambda: 儲存庫.建立已解析呼叫("ep", "ver", "req", {}), "呼叫建立失敗"
    elif 邊界 == "完成":
        monkeypatch.setattr(儲存庫, "_更新狀態", lambda *_a, **_k: (_ for _ in ()).throw(_自訂基礎錯誤(秘密)))
        呼叫, 訊息 = lambda: 儲存庫.完成呼叫("inv", "failed", error={}), "呼叫結案失敗"
    elif 邊界 == "更新":
        monkeypatch.setattr(儲存庫, "_開啟連線", lambda: (_ for _ in ()).throw(_自訂基礎錯誤(秘密)))
        呼叫, 訊息 = lambda: 儲存庫._更新狀態("inv", {"pending"}, "running"), "呼叫狀態更新失敗"
    else:
        monkeypatch.setattr(儲存庫模組.os, "lstat", lambda _p: (_ for _ in ()).throw(_自訂基礎錯誤(秘密)))
        呼叫, 訊息 = 儲存庫._開啟連線, "呼叫資料庫開啟失敗"
    _斷言固定新錯誤(呼叫, 訊息)


def _斷言控制流程與production清理(呼叫, 錯誤, 必要框架):
    """控制流程保持物件及args，所有儲存庫production frame移除秘密local。"""
    with pytest.raises(type(錯誤)) as 資訊:
        呼叫()
    assert 資訊.value is 錯誤 and 資訊.value.args == (秘密,)
    框架 = [項目.frame for 項目 in 資訊.traceback if str(項目.frame.code.path) == 儲存庫模組.__file__]
    assert 必要框架 <= {框架項.code.name for 框架項 in 框架}
    assert all(not _內建樹包含標記(框架項.f_locals, 秘密, set()) for 框架項 in 框架)


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("邊界", ["建立", "完成", "更新", "開啟"])
def test_三種控制流程跨邊界原樣傳播且production框架清理(tmp_path, monkeypatch, 錯誤類型, 邊界):
    """建立、完成、更新、開啟皆不吞終止流程，且各自清空敏感locals。"""
    錯誤 = 錯誤類型(秘密)
    儲存庫 = SQLite呼叫儲存庫(tmp_path / "missing", 識別碼工廠=lambda: "inv-flow")
    if 邊界 == "建立":
        假連線 = _交易假連線()
        monkeypatch.setattr(儲存庫, "_開啟連線", lambda: 假連線)
        monkeypatch.setattr(儲存庫, "_識別碼工廠", lambda: (_ for _ in ()).throw(錯誤))
        呼叫, 框架 = lambda: 儲存庫.建立已解析呼叫("ep", "ver", "req", {}), {"建立已解析呼叫"}
    elif 邊界 == "完成":
        monkeypatch.setattr(儲存庫, "_更新狀態", lambda *_a, **_k: (_ for _ in ()).throw(錯誤))
        呼叫, 框架 = lambda: 儲存庫.完成呼叫("inv", "failed", error={}), {"完成呼叫"}
    elif 邊界 == "更新":
        monkeypatch.setattr(儲存庫, "_開啟連線", lambda: (_ for _ in ()).throw(錯誤))
        呼叫, 框架 = lambda: 儲存庫._更新狀態("inv", {"pending"}, "running"), {"_更新狀態"}
    else:
        monkeypatch.setattr(儲存庫模組.os, "lstat", lambda _p: (_ for _ in ()).throw(錯誤))
        呼叫, 框架 = 儲存庫._開啟連線, {"_開啟連線"}
    _斷言控制流程與production清理(呼叫, 錯誤, 框架)


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_開啟失敗時close控制流程覆蓋外錯並保持身份(tmp_path, monkeypatch, 錯誤類型):
    """FK readback失敗清理若遇終止流程，傳播內層close物件而非外層ValueError。"""
    路徑 = tmp_path / "nonempty.sqlite3"
    路徑.write_bytes(b"x")
    錯誤 = 錯誤類型(秘密)
    假連線 = _交易假連線(關閉錯誤=錯誤)
    參數 = []
    def 假連接(*args, **kwargs):
        """記錄 SQLite connect 參數並回傳假連線。"""
        參數.append((args, kwargs))
        return 假連線

    monkeypatch.setattr(儲存庫模組.sqlite3, "connect", 假連接)
    儲存庫 = SQLite呼叫儲存庫(路徑)
    _斷言控制流程與production清理(儲存庫._開啟連線, 錯誤, {"_開啟連線"})
    assert 假連線.關閉次數 == 1
    assert 參數[0][0][0].endswith("?mode=rw") and 參數[0][1] == {"uri": True, "isolation_level": None}


def test_開啟連線拒絕TOCTOU替換並關閉已開資源(tmp_path, monkeypatch):
    """第二次lstat若inode改變，schema probe前fail closed並關閉一次。"""
    路徑 = tmp_path / "replace.sqlite3"
    路徑.write_bytes(b"x")
    原狀態 = os.lstat(路徑)
    替換狀態 = type("替換狀態", (), dict(st_mode=原狀態.st_mode, st_size=1,
                                     st_dev=原狀態.st_dev, st_ino=原狀態.st_ino + 1))()
    狀態們 = iter((原狀態, 替換狀態))
    假連線 = _交易假連線()
    monkeypatch.setattr(儲存庫模組.os, "lstat", lambda _p: next(狀態們))
    monkeypatch.setattr(儲存庫模組.sqlite3, "connect", lambda *_a, **_k: 假連線)
    with pytest.raises(呼叫儲存錯誤, match="呼叫資料庫開啟失敗"):
        SQLite呼叫儲存庫(路徑)._開啟連線()
    assert 假連線.關閉次數 == 1


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("動作", ["建立", "更新"])
def test_已回傳連線close控制流程原樣傳播(monkeypatch, 錯誤類型, 動作):
    """closing的close失敗不雙關閉，且建立/更新邊界保留終止流程身份。"""
    錯誤 = 錯誤類型(秘密)
    假連線 = _交易假連線(關閉錯誤=錯誤)
    儲存庫 = SQLite呼叫儲存庫("unused", 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-close-flow")
    monkeypatch.setattr(儲存庫, "_開啟連線", lambda: 假連線)
    if 動作 == "建立":
        呼叫, 框架 = lambda: 儲存庫.建立已解析呼叫("ep", "ver", "req", {}), {"建立已解析呼叫"}
    else:
        呼叫, 框架 = lambda: 儲存庫._更新狀態("inv", {"pending"}, "running"), {"_更新狀態"}
    _斷言控制流程與production清理(呼叫, 錯誤, 框架)
    assert 假連線.關閉次數 == 1


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_結案正規化中斷清空payload與canonical_locals(monkeypatch, 錯誤類型):
    """正規器中斷時完成與更新框架皆不保留payload或已建canonical字串。"""
    錯誤 = 錯誤類型(秘密)
    假連線 = _交易假連線()
    儲存庫 = SQLite呼叫儲存庫("unused", 時鐘=lambda: 1)
    monkeypatch.setattr(儲存庫, "_開啟連線", lambda: 假連線)
    monkeypatch.setattr(儲存庫模組, "建立正規JSON", lambda _值: (_ for _ in ()).throw(錯誤))
    呼叫 = lambda: 儲存庫.完成呼叫("inv", "failed", error={"secret": 秘密})
    _斷言控制流程與production清理(呼叫, 錯誤, {"完成呼叫", "_更新狀態"})
    assert 假連線.關閉次數 == 1


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_開啟連線外層控制流程在一般close後仍原樣傳播(tmp_path, monkeypatch, 錯誤類型):
    """schema probe 終止流程經一般 close 後仍保持原物件。"""
    路徑 = tmp_path / "outer-flow.sqlite3"
    路徑.write_bytes(b"x")
    錯誤 = 錯誤類型(秘密)
    假連線 = _交易假連線()
    假連線.execute = lambda *_a, **_k: (_ for _ in ()).throw(錯誤)
    monkeypatch.setattr(儲存庫模組.sqlite3, "connect", lambda *_a, **_k: 假連線)
    _斷言控制流程與production清理(SQLite呼叫儲存庫(路徑)._開啟連線, 錯誤, {"_開啟連線"})
    assert 假連線.關閉次數 == 1


def test_開啟失敗的普通close錯誤仍固定拒絕(tmp_path, monkeypatch):
    """一般 close 錯誤不覆蓋固定公開開啟錯誤。"""
    路徑 = tmp_path / "close-fail.sqlite3"
    路徑.write_bytes(b"x")
    假連線 = _交易假連線(關閉錯誤=RuntimeError(秘密))
    monkeypatch.setattr(儲存庫模組.sqlite3, "connect", lambda *_a, **_k: 假連線)
    _斷言固定新錯誤(SQLite呼叫儲存庫(路徑)._開啟連線, "呼叫資料庫開啟失敗")
    assert 假連線.關閉次數 == 2
