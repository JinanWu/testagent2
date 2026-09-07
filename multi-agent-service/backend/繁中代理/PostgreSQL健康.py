from __future__ import annotations

from collections.abc import Mapping
from typing import Any

HEAD_REVISION = "0001_full_product_schema"


class PostgreSQL未就緒(RuntimeError):
    """連線正常性或 schema revision 不符合產品要求。"""


def _第一欄(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    if isinstance(row, (tuple, list)):
        return row[0] if row else None
    return row


def 檢查PostgreSQL就緒(connection: Any, 預期版本: str = HEAD_REVISION) -> None:
    """使用既有 psycopg 連線檢查 SQL 可用性及 Alembic ledger 是否恰為 head。

    只執行唯讀查詢；runtime 不會建立、變更或修補 schema。
    """
    if type(預期版本) is not str or not 預期版本:
        raise PostgreSQL未就緒("PostgreSQL 預期 schema revision 無效")
    try:
        one = _第一欄(connection.execute("SELECT 1").fetchone())
        revision_rows = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise PostgreSQL未就緒("PostgreSQL 連線或 migration ledger 不可用") from None
    if one != 1:
        raise PostgreSQL未就緒("PostgreSQL SELECT 1 檢查失敗")
    if len(revision_rows) != 1 or _第一欄(revision_rows[0]) != 預期版本:
        raise PostgreSQL未就緒("PostgreSQL schema revision 尚未到達 Alembic head")
