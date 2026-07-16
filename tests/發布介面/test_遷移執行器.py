"""發布介面遷移 SQL splitter 與 authorizer primitive 測試。"""

import pytest

from 繁中代理.發布介面.遷移執行器 import 拆分遷移SQL, 遷移SQL錯誤, 遷移授權狀態, 驗證遷移SQL


秘密標記 = "唯一SQL_SECRET_MARKER_不可外洩"


def _錯誤不含SQL標記(錯誤):
    assert 秘密標記 not in str(錯誤)
    assert 秘密標記 not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None


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


def test_驗證遷移SQL錯誤不含整段SQL且錯誤路徑可重複使用():
    陳述 = f"SELECT FROM {秘密標記};"

    with pytest.raises(遷移SQL錯誤) as 錯誤:
        驗證遷移SQL([陳述])

    陳述 = None
    _錯誤不含SQL標記(錯誤.value)
    驗證遷移SQL(["SELECT 1;"])
