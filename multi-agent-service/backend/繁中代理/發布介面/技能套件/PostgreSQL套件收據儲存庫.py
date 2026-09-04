"""PostgreSQL-only bundle receipt boundary (bytes remain in Cloud Storage)."""
from __future__ import annotations
from typing import Any
from ..PostgreSQL工作單元 import PostgreSQL工作單元

class PostgreSQL套件收據儲存庫:
    __slots__ = ("_工作單元",)
    def __init__(self, 設定: Any) -> None: self._工作單元 = PostgreSQL工作單元(設定)
    def 依版本查詢(self, version_id: str) -> Any:
        with self._工作單元.交易() as conn:
            return conn.execute("SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at FROM published_skill_bundles WHERE version_id=%s", (version_id,)).fetchone()
    def 新增(self, receipt: Any, version_id: str, published_at: Any, *, state: str = "published") -> None:
        with self._工作單元.交易() as conn:
            conn.execute("INSERT INTO published_skill_bundles(bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (receipt.套件識別碼, version_id, receipt.清單參照, receipt.清單摘要, receipt.套件雜湊, receipt.總位元組數, state, published_at))

    def 新鮮查詢(self, *, version_id: str) -> dict[str, Any]:
        """One fresh transaction returns explicit version and receipt existence."""
        if type(version_id) is not str or not version_id:
            raise ValueError("version id invalid")
        with self._工作單元.交易() as conn:
            version = conn.execute(
                "SELECT id FROM published_endpoint_versions WHERE id=%s", (version_id,)
            ).fetchone()
            receipt = conn.execute(
                "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at "
                "FROM published_skill_bundles WHERE version_id=%s", (version_id,)
            ).fetchone()
            return {"version_exists": version is not None, "receipt": receipt}

    def 收據相符(self, row: Any, receipt: Any) -> bool:
        """Compare only detached authoritative identity fields, never row truthiness."""
        try:
            values = tuple(row.get(k) for k in (
                "bundle_id", "version_id", "manifest_reference", "manifest_digest",
                "bundle_hash", "total_bytes",
            )) if isinstance(row, dict) else tuple(row[:6])
            return values == (
                receipt.套件識別碼, receipt.端點版本識別碼 if hasattr(receipt, "端點版本識別碼") else values[1],
                receipt.清單參照, receipt.清單摘要, receipt.套件雜湊, receipt.總位元組數,
            )
        except BaseException:
            return False

    def 補齊收據(self, receipt: Any, version_id: str, reconciled_at: Any) -> None:
        """Insert reconciled state only after the same transaction proves version exists."""
        if type(version_id) is not str or not version_id:
            raise ValueError("version id invalid")
        with self._工作單元.交易() as conn:
            version = conn.execute(
                "SELECT id FROM published_endpoint_versions WHERE id=%s FOR SHARE", (version_id,)
            ).fetchone()
            existing = conn.execute(
                "SELECT bundle_id,version_id FROM published_skill_bundles WHERE version_id=%s FOR SHARE", (version_id,)
            ).fetchone()
            if version is None or existing is not None:
                raise RuntimeError("receipt reconciliation precondition failed")
            conn.execute(
                "INSERT INTO published_skill_bundles(bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,'reconciled',%s,%s)",
                (receipt.套件識別碼, version_id, receipt.清單參照, receipt.清單摘要,
                 receipt.套件雜湊, receipt.總位元組數, reconciled_at, reconciled_at),
            )

    reconcile_receipt = 補齊收據
    fresh_query = 新鮮查詢
