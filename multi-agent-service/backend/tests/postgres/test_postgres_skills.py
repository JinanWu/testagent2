from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, cast

import pytest
from psycopg.types.json import Jsonb

from 繁中代理.PostgreSQL技能庫 import PostgreSQL技能庫
import 繁中代理.PostgreSQL技能庫 as 技能模組


T0 = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 2, 4, 5, tzinfo=timezone.utc)
T3 = datetime(2026, 1, 2, 6, 7, tzinfo=timezone.utc)
T4 = datetime(2026, 1, 2, 7, 8, tzinfo=timezone.utc)


def _0001欄位清冊() -> dict[str, set[str]]:
    backend = Path(__file__).resolve().parents[2]
    候選 = (
        backend / "繁中代理/postgres_migrations/versions/0001_full_product_schema.py",
    )
    路徑 = next((路徑 for 路徑 in 候選 if 路徑.is_file()), None)
    assert 路徑 is not None, "找不到 PostgreSQL 0001 schema authority"
    語法樹 = ast.parse(路徑.read_text(encoding="utf-8"), filename=str(路徑))
    DDL清單 = next(
        ast.literal_eval(節點.value)
        for 節點 in 語法樹.body
        if isinstance(節點, ast.Assign)
        and any(isinstance(目標, ast.Name) and 目標.id == "DDL" for 目標 in 節點.targets)
    )
    清冊: dict[str, set[str]] = {}
    for DDL in DDL清單:
        符合 = re.match(r"CREATE TABLE (\w+) \((.*)\)\Z", DDL, re.S)
        if not 符合:
            continue
        表格, 主體 = 符合.groups()
        欄位 = {
            match.group(1)
            for match in re.finditer(r"(?:\A|,)\s*(\w+)\s+", 主體)
            if match.group(1).upper() not in {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"}
        }
        清冊[表格] = 欄位
    return 清冊


def _assert_SQL符合0001(SQL清單: list[str]) -> None:
    清冊 = _0001欄位清冊()
    SQL關鍵字 = {
        "SELECT", "FROM", "JOIN", "LEFT", "ON", "WHERE", "AND", "OR", "AS", "ORDER", "BY",
        "GROUP", "LIMIT", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "CONFLICT",
        "DO", "NOTHING", "EXCLUDED", "NULL", "TRUE", "FALSE", "CURRENT_TIMESTAMP", "COUNT", "MAX",
        "COALESCE", "ASC", "DESC",
    }
    for SQL in SQL清單:
        表格清單 = re.findall(r"\b(?:FROM|JOIN|INTO)\s+(\w+)", SQL, re.I)
        開頭更新 = re.match(r"UPDATE\s+(\w+)", SQL, re.I)
        if 開頭更新:
            表格清單.append(開頭更新.group(1))
        assert 表格清單, SQL
        assert set(表格清單) <= 清冊.keys(), SQL
        無字串SQL = re.sub(r"'(?:''|[^'])*'", "", SQL).replace("%s", "")
        別名 = set(re.findall(r"\bAS\s+(\w+)", 無字串SQL, re.I))
        別名.update(re.findall(r"\b(?:FROM|JOIN)\s+\w+\s+(?:AS\s+)?(\w+)", 無字串SQL, re.I))
        合法欄位 = set().union(*(清冊[表格] for 表格 in 表格清單))
        token = set(re.findall(r"\b[A-Za-z_]\w*\b", 無字串SQL))
        schema外識別字 = token - SQL關鍵字 - {值.upper() for 值 in SQL關鍵字}
        schema外識別字 -= set(表格清單) | 別名 | 合法欄位
        assert not schema外識別字, (SQL, schema外識別字)
        for 表格, 欄位文字 in re.findall(
            r"INSERT\s+INTO\s+(\w+)\s*\((.*?)\)\s*VALUES", SQL, re.I | re.S
        ):
            欄位 = {值.strip() for 值 in 欄位文字.split(",")}
            assert 欄位 <= 清冊[表格], (SQL, 欄位 - 清冊[表格])
        for 別名, 欄位 in re.findall(r"\b([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)", SQL):
            if 別名.upper() == "EXCLUDED":
                assert any(欄位 in 清冊[表格] for 表格 in 表格清單), (SQL, 欄位)
        for 表格 in set(表格清單):
            if SQL.upper().startswith("UPDATE "):
                設定段 = re.search(r"\bSET\s+(.*?)\s+WHERE\b", SQL, re.I | re.S)
                assert 設定段
                更新欄位 = set(re.findall(r"\b(\w+)\s*=", 設定段.group(1)))
                assert 更新欄位 <= 清冊[表格], (SQL, 更新欄位 - 清冊[表格])


class 假游標:
    """刻意匹配 psycopg Cursor 常用呼叫簽章，不以 **kwargs 掩護錯誤。"""

    def __init__(self, 連線):
        self.連線 = 連線
        self.結果 = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None, *, prepare=None, binary=None):
        self.連線.呼叫.append(("execute", " ".join(query.split()), params))
        if query.lstrip().upper().startswith("SELECT"):
            self.結果 = self.連線.查詢結果.pop(0) if self.連線.查詢結果 else []
        return self

    def executemany(self, query, params_seq, *, returning=False):
        參數 = list(params_seq)
        self.連線.呼叫.append(("executemany", " ".join(query.split()), 參數))
        return self

    def fetchone(self):
        return self.結果[0] if self.結果 else None

    def fetchall(self):
        return list(self.結果)


class 假連線:
    def __init__(self):
        self.呼叫 = []
        self.查詢結果 = []

    def cursor(self, name="", *, binary=False, row_factory=None, scrollable=None, withhold=False):
        assert name == ""
        return 假游標(self)


@pytest.fixture
def 庫與連線(monkeypatch):
    連線 = 假連線()

    @contextmanager
    def 假交易連線(設定):
        assert 設定 is 凍結設定
        yield 連線

    凍結設定 = object()
    monkeypatch.setattr(技能模組, "交易連線", 假交易連線)
    return PostgreSQL技能庫(cast(Any, 凍結設定)), 連線


def test_技能CRUD與狀態釘選皆帶owner條件(庫與連線):
    庫, 連線 = 庫與連線
    庫.建立技能("s1", "alpha", "全文", "cat", user_id="u1", 建立時間=T0)
    建立參數 = 連線.呼叫[0][2]
    assert 建立參數[:4] == ("u1", "s1", "alpha", "全文")
    assert isinstance(建立參數[4], Jsonb)
    assert 建立參數[4].obj == {"category": "cat"}
    assert len(建立參數[5]) == 64
    assert 建立參數[6:] == (T0, T0)
    assert 連線.呼叫[1][2] == ("u1", "s1", T0)

    連線.查詢結果.append([("s1", "u1", "alpha", "cat", "全文", "t0", "t0")])
    assert 庫.讀取技能內容("alpha", user_id="u1")["content"] == "全文"
    assert 連線.呼叫[-1][2] == ("u1", "alpha")
    assert "user_id = %s" in 連線.呼叫[-1][1]

    庫.更新技能內容("s1", "新全文", T1, user_id="u1")
    庫.設定狀態("s1", "archived", user_id="u1")
    庫.設定pin("s1", True, user_id="u1")
    assert 連線.呼叫[-3][2][0] == "新全文"
    assert len(連線.呼叫[-3][2][1]) == 64
    assert 連線.呼叫[-3][2][2:] == (T1, "u1", "s1")
    assert 連線.呼叫[-2][2] == ("archived", "u1", "s1")
    assert 連線.呼叫[-1][2] == (True, "u1", "s1")

    庫.刪除技能("s1", user_id="u1")
    assert 連線.呼叫[-1][2] == ("u1", "s1")
    assert 連線.呼叫[-1][1].startswith("DELETE FROM user_skills")

    呼叫數 = len(連線.呼叫)
    庫.設定狀態("s1", "stale", user_id="u1")
    assert len(連線.呼叫) == 呼叫數 + 1
    assert 連線.呼叫[-1][2] == ("stale", "u1", "s1")


def test_使用量快照事件追加與資料庫彙總(庫與連線):
    庫, 連線 = 庫與連線
    記錄 = {
        "user_id": "u1",
        "use_count": 3,
        "last_used_at": T3,
        "state": "active",
        "pinned": True,
        "created_at": T0,
    }
    庫.覆寫使用量列("s1", 記錄, user_id="u1")
    assert "ON CONFLICT (user_id, skill_id) DO UPDATE" in 連線.呼叫[-1][1]
    assert 連線.呼叫[-1][2] == ("u1", "s1", 3, T3, "active", True, T0)

    連線.查詢結果.append([("s1", "u1", 3, T3, "active", True, T0)])
    assert 庫.讀取全部使用量("u1") == {"s1": 記錄}
    assert 連線.呼叫[-1][2] == ("u1",)

    assert 庫.記錄多筆事件(["s1", "s2"], "u1", T4) == 2
    種類, SQL, 參數 = 連線.呼叫[-1]
    assert 種類 == "executemany"
    assert SQL.startswith("INSERT INTO skill_usage_events")
    assert 參數 == [("u1", "s1", T4), ("u1", "s2", T4)]

    連線.查詢結果.append([("s1", "u1", 2, T4)])
    assert 庫.彙總事件("u1") == [
        {"skill_id": "s1", "user_id": "u1", "use_count": 2, "last_used_at": T4}
    ]
    assert "COUNT(*)" in 連線.呼叫[-1][1]
    assert "GROUP BY user_id, skill_id" in 連線.呼叫[-1][1]
    assert 連線.呼叫[-1][2] == ("u1",)


def test_列技能永遠owner隔離且預設排除封存(庫與連線):
    庫, 連線 = 庫與連線
    連線.查詢結果.append([])
    assert 庫.列出技能身分(user_id="u2", 限定使用者=False) == []
    SQL = 連線.呼叫[-1][1]
    assert "s.user_id = %s" in SQL
    assert "<> 'archived'" in SQL
    assert 連線.呼叫[-1][2] == ("u2",)


def test_所有公開讀取拒絕缺少owner(庫與連線):
    庫, _ = 庫與連線
    with pytest.raises(ValueError, match="user_id"):
        庫.讀取技能內容("alpha")
    with pytest.raises(ValueError, match="user_id"):
        庫.讀取全部使用量()
    with pytest.raises(ValueError, match="user_id"):
        庫.讀取所有事件()


def test_timestamptz只傳aware_datetime且ISO舊API仍相容(庫與連線):
    庫, 連線 = 庫與連線
    庫.建立技能("s1", "alpha", "全文", user_id="u1", 建立時間="2026-01-02T03:04:00Z")
    assert 連線.呼叫[0][2][6].tzinfo is not None
    with pytest.raises(ValueError, match="aware datetime"):
        庫.更新技能內容("s1", "x", datetime(2026, 1, 1), user_id="u1")
    with pytest.raises(ValueError, match="aware datetime"):
        庫.覆寫使用量列("s1", {"created_at": T0, "last_used_at": datetime(2026, 1, 1)}, "u1")
    呼叫數 = len(連線.呼叫)
    庫.覆寫使用量列("s1", {"state": "stale"}, "u1")
    assert len(連線.呼叫) == 呼叫數 + 1
    assert 連線.呼叫[-1][2][4] == "stale"


def test_所有執行SQL表格與寫入欄位皆存在0001(庫與連線):
    庫, 連線 = 庫與連線
    庫.建立技能("s1", "alpha", "全文", user_id="u1", 建立時間=T0)
    連線.查詢結果.extend([[], [], [], []])
    庫.讀取技能內容("alpha", "u1")
    庫.列出技能身分("u1")
    庫.讀取全部使用量("u1")
    庫.讀取使用量列("s1", "u1")
    庫.覆寫使用量列("s1", {"created_at": T0}, "u1")
    庫.設定狀態("s1", "active", "u1")
    庫.設定pin("s1", True, "u1")
    庫.記錄多筆事件(["s1"], "u1", T1)
    庫.讀取所有事件("u1")
    庫.彙總事件("u1")
    庫.刪除使用量列("s1", "u1")
    庫.刪除技能("s1", "u1")
    _assert_SQL符合0001([SQL for _, SQL, _ in 連線.呼叫])
