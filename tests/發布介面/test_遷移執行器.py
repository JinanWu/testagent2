"""發布介面遷移 SQL splitter 與 authorizer primitive 測試。"""

import concurrent.futures
import sqlite3
import threading

import pytest

from 繁中代理.發布介面.遷移執行器 import 拆分遷移SQL, 執行遷移, 遷移SQL錯誤, 遷移執行錯誤, 遷移授權狀態, 遷移項目, 驗證遷移SQL


秘密標記 = "唯一SQL_SECRET_MARKER_不可外洩"
PRODUCTION_MODULE = "繁中代理.發布介面.遷移執行器"


def _錯誤不含SQL標記(錯誤):
    assert 秘密標記 not in str(錯誤)
    assert 秘密標記 not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None


def _production_traceback_locals不含SQL標記(錯誤):
    traceback = 錯誤.__traceback__
    checked = False
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == PRODUCTION_MODULE:
            checked = True
            for 名稱, 值 in frame.f_locals.items():
                assert 秘密標記 not in repr(值), 名稱
        traceback = traceback.tb_next
    assert checked


def _assert_sanitized_exception(錯誤):
    _錯誤不含SQL標記(錯誤)
    _production_traceback_locals不含SQL標記(錯誤)


def _查詢(db, sql):
    with sqlite3.connect(db) as 連線:
        return 連線.execute(sql).fetchall()


def test_拆分遷移SQL切分基本多陳述並strip():
    assert 拆分遷移SQL("  CREATE TABLE a(id INTEGER); \n INSERT INTO a VALUES(1);  ") == (
        "CREATE TABLE a(id INTEGER);",
        "INSERT INTO a VALUES(1);",
    )


def test_拆分遷移SQL保留字串與註解內分號不誤切():
    腳本 = """
    INSERT INTO log VALUES('a;b', "c;d");
    -- comment ; stays with next statement
    INSERT INTO log VALUES(2 /* block ; comment */);
    """

    assert 拆分遷移SQL(腳本) == (
        """INSERT INTO log VALUES('a;b', "c;d");""",
        "-- comment ; stays with next statement\n    INSERT INTO log VALUES(2 /* block ; comment */);",
    )


def test_拆分遷移SQL不切開trigger_body內分號():
    腳本 = """
    CREATE TRIGGER trg AFTER INSERT ON a
    BEGIN
      INSERT INTO b VALUES(new.id);
      INSERT INTO c VALUES('x;y');
    END;
    CREATE INDEX idx_a_id ON a(id);
    """

    assert 拆分遷移SQL(腳本) == (
        "CREATE TRIGGER trg AFTER INSERT ON a\n    BEGIN\n      INSERT INTO b VALUES(new.id);\n      INSERT INTO c VALUES('x;y');\n    END;",
        "CREATE INDEX idx_a_id ON a(id);",
    )


def test_拆分遷移SQL允許尾端line與block_comment且空腳本回空tuple():
    assert 拆分遷移SQL("CREATE TABLE a(id); -- trailing ; comment\n/* tail ; */") == ("CREATE TABLE a(id);",)
    assert 拆分遷移SQL(" \n -- only ; comment\n /* block ; only */ \n") == ()


@pytest.mark.parametrize("腳本", ["SELECT 1", "CREATE TABLE a(id); /* unclosed"])
def test_拆分遷移SQL拒絕未完成尾端SQL_未關閉comment_非字串(腳本):
    with pytest.raises(遷移SQL錯誤):
        拆分遷移SQL(腳本)


def test_拆分遷移SQL非字串錯誤固定():
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        拆分遷移SQL(b"SELECT 1;")

    assert str(錯誤.value) == "遷移 SQL 不符合契約"


@pytest.mark.parametrize(
    "腳本",
    [
        f"SELECT 1 /* {秘密標記}",
        f"SELECT '{秘密標記}'",
        {"sql": 秘密標記},
    ],
)
def test_拆分遷移SQL錯誤traceback_locals不保留raw腳本(腳本):
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        拆分遷移SQL(腳本)

    _assert_sanitized_exception(錯誤.value)


@pytest.mark.parametrize("陳述", ["CREATE TABLE ;", "SELECT FROM;", "SELECT 'unterminated;"])
def test_驗證遷移SQL拒絕parser語法與不完整輸入(陳述):
    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL([陳述])


@pytest.mark.parametrize("陳述", ["SELECT * FROM later_table;", "SELECT missing_column FROM later_table;", "INSERT INTO later_table VALUES (?);"])
def test_驗證遷移SQL允許語意依賴稍後建立(陳述):
    驗證遷移SQL([陳述])


@pytest.mark.parametrize("陳述", ["BEGIN;", "END;", "COMMIT;", "ROLLBACK;", "SAVEPOINT s;", "RELEASE s;"])
def test_驗證遷移SQL拒絕交易與savepoint_opcode(陳述):
    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL([陳述])


@pytest.mark.parametrize("陳述", ["ATTACH 'x.db' AS x;", "DETACH x;", "PRAGMA foreign_keys = ON;"])
def test_驗證遷移SQL拒絕attach_detach_pragma(陳述):
    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL([陳述])


def test_驗證遷移SQL不因字串或註解中的禁用詞拒絕():
    驗證遷移SQL(
        [
            "SELECT 'BEGIN COMMIT SAVEPOINT PRAGMA ATTACH DETACH';",
            "-- ROLLBACK;\nSELECT 1;",
        ]
    )


def test_遷移授權狀態只在真opcode拒絕且記錄類型():
    狀態 = 遷移授權狀態()

    驗證遷移SQL(["SELECT 'PRAGMA';"])
    assert 狀態.拒絕類型 is None

    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL(["PRAGMA user_version;"], 授權狀態=狀態)

    assert 狀態.拒絕類型 == "PRAGMA"


def test_驗證遷移SQL重用授權狀態不被前次拒絕污染():
    狀態 = 遷移授權狀態()

    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL(["PRAGMA user_version;"], 授權狀態=狀態)

    驗證遷移SQL(["SELECT * FROM later_table;"], 授權狀態=狀態)
    assert 狀態.拒絕類型 is None


@pytest.mark.parametrize(
    "陳述",
    [
        f"SELECT FROM {秘密標記};",
        f"PRAGMA {秘密標記};",
    ],
)
def test_驗證遷移SQL錯誤不含整段SQL且錯誤路徑可重複使用(陳述):
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        驗證遷移SQL([陳述])

    _assert_sanitized_exception(錯誤.value)
    驗證遷移SQL(["SELECT 1;"])


def test_驗證遷移SQL非字串陳述traceback_locals不保留raw_sql():
    陳述 = {"sql": 秘密標記}
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        驗證遷移SQL([陳述])

    _assert_sanitized_exception(錯誤.value)
    驗證遷移SQL(["SELECT 1;"])


def test_執行遷移依版本排序_ledger_fields_回傳_applied_且同名重跑先skip(tmp_path):
    db = tmp_path / "app.db"
    清單 = [
        遷移項目(2, "second", "INSERT INTO t VALUES(2);"),
        遷移項目(1, "first", "CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES(1);"),
    ]

    assert 執行遷移(db, 清單) == (1, 2)
    assert _查詢(db, "SELECT id FROM t ORDER BY id") == [(1,), (2,)]
    ledger = _查詢(db, "SELECT version,name,typeof(applied_at) FROM published_api_schema_migrations ORDER BY version")
    assert ledger == [(1, "first", "real"), (2, "second", "real")]
    assert 執行遷移(db, [遷移項目(1, "first", f"CREATE TABLE broken_{秘密標記}(")]) == ()


@pytest.mark.parametrize(
    "清單",
    [
        None,
        {"sql": 秘密標記},
        f"not a list {秘密標記}",
        秘密標記.encode(),
        [None],
        [{"sql": 秘密標記}],
        [object()],
        [遷移項目(1, "a", "CREATE TABLE a(id);"), 遷移項目(1, "b", "CREATE TABLE b(id);")],
        [遷移項目(0, "a", "CREATE TABLE a(id);")],
        [遷移項目(True, "a", "CREATE TABLE a(id);")],
        [遷移項目(1, "  ", "CREATE TABLE a(id);")],
        [遷移項目(1, "a", {"sql": 秘密標記})],
    ],
)
def test_執行遷移拒絕不合法輸入且不套用任何schema(tmp_path, 清單):
    db = tmp_path / "bad.db"

    with pytest.raises(遷移執行錯誤) as 錯誤:
        執行遷移(db, 清單)

    assert str(錯誤.value) == "遷移項目不符合契約"
    _assert_sanitized_exception(錯誤.value)
    assert not db.exists() or _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'") == []


def test_執行遷移一般sqlite錯誤整筆rollback且保留原錯誤型別(tmp_path):
    db = tmp_path / "rollback.db"
    sql = f"CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO missing VALUES(1); /* {秘密標記} */"

    with pytest.raises(sqlite3.OperationalError) as 錯誤:
        執行遷移(db, [遷移項目(1, "bad", sql)])

    _assert_sanitized_exception(錯誤.value)
    assert _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'") == []


@pytest.mark.parametrize(
    "sql",
    [
        f"CREATE TABLE broken_{秘密標記}(;",
        f"SELECT 1; /* {秘密標記}",
    ],
)
def test_執行遷移_parser與splitter錯誤traceback_locals不保留raw_sql(tmp_path, sql):
    db = tmp_path / "parser.db"

    with pytest.raises(遷移SQL錯誤) as 錯誤:
        執行遷移(db, [遷移項目(1, "bad", sql)])

    _assert_sanitized_exception(錯誤.value)


@pytest.mark.parametrize("陳述", ["COMMIT;", "END;", "ROLLBACK;", "BEGIN;", "SAVEPOINT s;", "RELEASE s;"])
def test_執行遷移交易控制由authorizer固定錯誤且無schema_ledger殘留(tmp_path, 陳述):
    db = tmp_path / "forbidden.db"

    with pytest.raises(遷移執行錯誤) as 錯誤:
        執行遷移(db, [遷移項目(1, "bad", f"CREATE TABLE a(id); {陳述} SELECT '{秘密標記}';")])

    assert str(錯誤.value) == "遷移 SQL 包含禁止操作"
    _assert_sanitized_exception(錯誤.value)
    assert _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'") == []


def test_執行遷移外鍵違反rollback且成功後新連線foreign_key_check_empty(tmp_path):
    db = tmp_path / "fk.db"
    建表 = "CREATE TABLE p(id INTEGER PRIMARY KEY); CREATE TABLE c(pid INTEGER REFERENCES p(id));"
    assert 執行遷移(db, [遷移項目(1, "schema", 建表)]) == (1,)

    with pytest.raises(sqlite3.IntegrityError):
        執行遷移(db, [遷移項目(2, "bad_fk", "INSERT INTO c VALUES(9);")])

    assert _查詢(db, "SELECT * FROM c") == []
    assert _查詢(db, "SELECT version FROM published_api_schema_migrations ORDER BY version") == [(1,)]
    assert 執行遷移(db, [遷移項目(2, "ok_fk", "INSERT INTO p VALUES(9); INSERT INTO c VALUES(9);")]) == (2,)
    assert _查詢(db, "PRAGMA foreign_key_check") == []


def test_執行遷移同版本不同名稱rollback且診斷不含SQL(tmp_path):
    db = tmp_path / "rename.db"
    執行遷移(db, [遷移項目(1, "old", "CREATE TABLE t(id);")])

    with pytest.raises(遷移執行錯誤) as 錯誤:
        執行遷移(db, [遷移項目(1, "new", f"CREATE TABLE leak_{秘密標記}(id);")])

    assert "1" in str(錯誤.value) and "old" in str(錯誤.value) and "new" in str(錯誤.value)
    _assert_sanitized_exception(錯誤.value)
    assert _查詢(db, "SELECT name FROM sqlite_master WHERE type='table' AND name='t'") == [("t",)]


def test_執行遷移同版本多thread只套用一次且不hang(tmp_path):
    db = tmp_path / "race.db"
    barrier = threading.Barrier(6)
    項目 = 遷移項目(1, "race", "CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES(1);")

    def run_one():
        barrier.wait(timeout=5)
        return 執行遷移(db, [項目])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run_one) for _ in range(6)]
        results = [f.result(timeout=10) for f in futures]

    assert sorted(results) == [(), (), (), (), (), (1,)]
    assert _查詢(db, "SELECT COUNT(*) FROM t") == [(1,)]
    assert _查詢(db, "SELECT COUNT(*) FROM published_api_schema_migrations WHERE version=1") == [(1,)]


def test_執行遷移authorizer_cleanup後續正常migration可成功(tmp_path):
    db = tmp_path / "cleanup.db"
    with pytest.raises(遷移執行錯誤):
        執行遷移(db, [遷移項目(1, "bad", "SAVEPOINT s;")])

    assert 執行遷移(db, [遷移項目(1, "ok", "CREATE TABLE ok(id);")]) == (1,)
