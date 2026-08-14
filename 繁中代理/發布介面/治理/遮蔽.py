"""GOV G04 SQLite 不可逆 payload 遮蔽與墓碑帳本。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from typing import Any

from ..領域模型 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef
from .稽核 import _建立canonical列, _清理控制鏈, _清理連線操作, _重拋控制
from .稽核資料庫 import _開啟既有資料庫, _驗證目前路徑, _驗證schema
from .稽核結構 import _LEDGER

_固定錯誤 = "呼叫資料無法遮蔽"
_最大JSON位元組 = 1_048_576
_目標 = {
    "invocation_input": ("endpoint_invocations", "input_json", False),
    "metadata": ("endpoint_invocations", "metadata_json", False),
    "output": ("endpoint_invocations", "output_json", False),
    "error": ("endpoint_invocations", "error_json", False),
    "run_event": ("run_events", "payload_json", True),
    "tool_arguments": ("endpoint_tool_calls", "arguments_json", True),
    "tool_result": ("endpoint_tool_calls", "result_json", True),
    "tool_error": ("endpoint_tool_calls", "error_json", True),
}
_秘密格式 = re.compile(r"(?i)(?:bearer|(?:sk|pk)[_-])|\b[0-9a-f]{64}\b")
_必要觸發器 = frozenset({
    "endpoint_redactions_require_tombstone", "endpoint_redactions_target_before_insert",
    "endpoint_redactions_no_update", "endpoint_redactions_no_delete",
    "redacted_invocation_payload_no_update", "redacted_run_event_no_update",
    "redacted_tool_call_no_update", "redacted_run_event_no_delete",
    "redacted_tool_call_no_delete",
})
_遮蔽物件摘要 = (
    ("table", "endpoint_redactions", "ead15701fb385c3c2652214c8bab6a3214ad056a10ede486b76a376092b91eec"),
    ("trigger", "endpoint_redactions_no_delete", "510b4591ca13baf4c281784ad75e275ba89de10ba0e2f10d4db597a6a72145d5"),
    ("trigger", "endpoint_redactions_no_update", "e8fab9d58eec253205d44dc7d9c2ffb4282004c5fa5f678d66ee8d3f956ba4c0"),
    ("trigger", "endpoint_redactions_require_tombstone", "3291bfa7c97745815307300e13ffa1abbae39898a571dcb113b630d9b4292174"),
    ("trigger", "endpoint_redactions_target_before_insert", "e886992ff4eb4ec6d190962ed935ea4b564b7608081efe92370a29a5cc20949d"),
    ("trigger", "redacted_invocation_payload_no_update", "13db663832200be54f42de56e2f4b676dc71283d3d55b24b4cefec8b9c006b99"),
    ("trigger", "redacted_run_event_no_delete", "ea8c587b4c4873f84e7649ba3ab418649ef65d92d739683f257fb8ace909992d"),
    ("trigger", "redacted_run_event_no_update", "36493fb5742d49740e2a8e6d9a7939e7b05e46b0831897bf89b41d852ad02181"),
    ("trigger", "redacted_tool_call_no_delete", "532779cd5095eb3ea00ee7812b6fb5dc6a8f2fdcdbb1b9012c91f2950803c440"),
    ("trigger", "redacted_tool_call_no_update", "61497db9ab276bdd977c08e87ce975d2a9d75f2c6360088d658c1d30caa2febb"),
)


class 不可逆遮蔽錯誤(RuntimeError):
    """遮蔽請求無法安全且原子提交時的固定公開錯誤。"""


class SQLite不可逆遮蔽服務:
    """原子提交 canonical audit、payload 墓碑與 append-only 遮蔽帳本。"""

    __slots__ = ("_path",)

    def __init__(self, 資料庫路徑: str) -> None:
        """驗證並保存既有SQLite資料庫路徑。"""
        if type(資料庫路徑) is not str or not 資料庫路徑 or 資料庫路徑.startswith("~"):
            raise 不可逆遮蔽錯誤(_固定錯誤) from None
        self._path = 資料庫路徑

    def 遮蔽(
        self, 管理員授權: bool, 遮蔽識別碼: str, 稽核事件識別碼: str,
        操作者識別碼: str, 請求識別碼: str, 呼叫識別碼: str,
        目標類型: str, 目標列識別碼: str, JSON路徑: str, 原因: str,
        發生時間: int | float, /,
    ) -> dict[str, Any]:
        """僅 exact admin 可遮蔽；空路徑代表整個 JSON，非空採 RFC 6901。"""
        連線 = 游標 = payload = 原始文字 = 新文字 = 摘要 = 結果 = 既有 = None
        事件 = 稽核列 = 表格 = 欄位 = 範圍 = 參數 = 墓碑 = None
        已開始 = 已提交 = 一般失敗 = False
        主要控制 = None
        回滾控制盒: list[BaseException] = []
        關閉控制盒: list[BaseException] = []
        捕捉路徑 = self._path
        try:
            _驗證請求(管理員授權, 遮蔽識別碼, 稽核事件識別碼, 操作者識別碼,
                      請求識別碼, 呼叫識別碼, 目標類型, 目標列識別碼,
                      JSON路徑, 原因, 發生時間)
            連線 = _開啟既有資料庫(捕捉路徑)
            連線.execute("BEGIN IMMEDIATE")
            已開始 = True
            _驗證目前路徑(連線, 捕捉路徑)
            捕捉路徑 = None
            _驗證schema(連線)
            _驗證遮蔽schema(連線)
            既有 = 連線.execute(
                "SELECT r.id,r.json_path,r.original_sha256,r.reason,r.actor_id,r.audit_event_id,"
                "r.is_tombstone,r.redacted_at,r.actor_type,r.invocation_id,a.event_id,"
                "a.occurred_at,a.action,a.outcome,a.actor_type,a.actor_id,a.resource_type,"
                "a.resource_id,a.request_id,a.endpoint_id,a.invocation_id,a.metadata_json,"
                "a.created_at,i.endpoint_id FROM endpoint_redactions r JOIN audit_events a "
                "ON a.id=r.audit_event_id JOIN endpoint_invocations i ON i.id=r.invocation_id "
                "WHERE r.target_type=? AND r.target_row_id=? AND r.json_path=?",
                (目標類型, 目標列識別碼, JSON路徑),
            ).fetchall()
            if 既有:
                if len(既有) != 1 or not _相同重試(
                    既有[0], 遮蔽識別碼, 稽核事件識別碼, 原因, 操作者識別碼,
                    請求識別碼, 呼叫識別碼, 發生時間,
                ):
                    raise ValueError
                表格, 欄位, 子列 = _目標[目標類型]
                範圍 = "id=? AND invocation_id=?" if 子列 else "id=?"
                參數 = (目標列識別碼, 呼叫識別碼) if 子列 else (呼叫識別碼,)
                payload, 原始文字 = _讀取payload(連線, 表格, 欄位, 範圍, 參數)
                _確認墓碑(payload, JSON路徑, 遮蔽識別碼, 發生時間)
                結果 = _結果(既有[0], 目標類型, 目標列識別碼)
                連線.commit()
            else:
                表格, 欄位, 子列 = _目標[目標類型]
                if not 子列 and 目標列識別碼 != 呼叫識別碼:
                    raise ValueError
                範圍 = "id=? AND invocation_id=?" if 子列 else "id=?"
                參數 = (目標列識別碼, 呼叫識別碼) if 子列 else (呼叫識別碼,)
                payload, 原始文字 = _讀取payload(連線, 表格, 欄位, 範圍, 參數)
                端點列 = 連線.execute(
                    "SELECT endpoint_id FROM endpoint_invocations WHERE id=?", (呼叫識別碼,)
                ).fetchall()
                if len(端點列) != 1 or not _安全識別碼(端點列[0][0]):
                    raise ValueError
                墓碑 = {"$tombstone": {"redaction_id": 遮蔽識別碼, "redacted_at": float(發生時間)}}
                if JSON路徑 == "":
                    摘要來源 = _建立正規JSON(payload).encode("utf-8")
                    payload = 墓碑
                else:
                    容器, 鍵 = _尋找JSON位置(payload, JSON路徑)
                    舊值 = 容器[鍵]
                    摘要來源 = _建立正規JSON(舊值).encode("utf-8")
                    容器[鍵] = 墓碑
                摘要 = hashlib.sha256(摘要來源).hexdigest()
                新文字 = _建立正規JSON(payload)
                事件 = AuditEvent(
                    event_id=稽核事件識別碼, occurred_at=發生時間,
                    action="audit.payload.redact", outcome="success",
                    actor=AuditActorRef("user", 操作者識別碼),
                    resource=AuditResourceRef("endpoint.redaction", 遮蔽識別碼),
                    request_id=請求識別碼, endpoint_id=端點列[0][0],
                    invocation_id=呼叫識別碼, metadata=AuditMetadata({"is_tombstone": True}),
                )
                稽核列 = _建立canonical列(事件) + (float(發生時間),)
                游標 = 連線.execute(
                    "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,"
                    "actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,"
                    "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", 稽核列,
                )
                稽核序號 = 游標.lastrowid
                游標.close(); 游標 = None
                if type(稽核序號) is not int or 稽核序號 < 1 or 連線.execute(
                    "SELECT event_id FROM audit_events WHERE rowid=?", (稽核序號,)
                ).fetchall() != [(稽核事件識別碼,)]:
                    raise sqlite3.DatabaseError
                更新 = 連線.execute(f"UPDATE {表格} SET {欄位}=? WHERE {範圍}", (新文字, *參數))
                if 更新.rowcount != 1:
                    raise sqlite3.DatabaseError
                連線.execute(
                    "INSERT INTO endpoint_redactions(id,invocation_id,target_type,target_row_id,"
                    "json_path,original_sha256,reason,actor_type,actor_id,audit_event_id,"
                    "is_tombstone,redacted_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)",
                    (遮蔽識別碼, 呼叫識別碼, 目標類型, 目標列識別碼, JSON路徑,
                     摘要, 原因.strip(), "admin", 操作者識別碼, 稽核事件識別碼,
                     float(發生時間)),
                )
                結果 = _結果((遮蔽識別碼, JSON路徑, 摘要, 原因.strip(),
                             操作者識別碼, 稽核事件識別碼, 1, float(發生時間)),
                            目標類型, 目標列識別碼)
                連線.commit()
            已提交 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
            _清理控制鏈(捕捉控制)
            主要控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            一般失敗 = True
        if 連線 is not None and 已開始 and not 已提交:
            回滾控制盒 = _清理連線操作(連線, "rollback")
        if 連線 is not None:
            關閉控制盒 = _清理連線操作(連線, "close")
        self = 連線 = 游標 = payload = 原始文字 = 新文字 = 摘要 = 既有 = None
        事件 = 稽核列 = 表格 = 欄位 = 範圍 = 參數 = 墓碑 = 捕捉路徑 = None
        管理員授權 = 遮蔽識別碼 = 稽核事件識別碼 = 操作者識別碼 = 請求識別碼 = None
        呼叫識別碼 = 目標類型 = 目標列識別碼 = JSON路徑 = 原因 = 發生時間 = None
        if 主要控制 is not None:
            回滾控制盒.clear(); 關閉控制盒.clear()
            控制盒 = [主要控制]; 主要控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 回滾控制盒:
            關閉控制盒.clear(); 結果 = None
            _重拋控制(回滾控制盒.pop())
        if 關閉控制盒:
            結果 = None
            _重拋控制(關閉控制盒.pop())
        if 一般失敗 or not 已提交 or type(結果) is not dict:
            結果 = None
            raise 不可逆遮蔽錯誤(_固定錯誤) from None
        return 結果


setattr(SQLite不可逆遮蔽服務, "redact", SQLite不可逆遮蔽服務.遮蔽)


def _驗證請求(*值: Any) -> None:
    """驗證exact管理授權、識別、路徑、原因與時間。"""
    授權, *文字, 時間 = 值
    if type(授權) is not bool or 授權 is not True or type(時間) not in (int, float):
        raise ValueError
    if not math.isfinite(時間) or 時間 < 0 or 時間 > 253402300799:
        raise ValueError
    if not all(type(項) is str for 項 in 文字):
        raise ValueError
    遮蔽ID, 事件ID, 操作者, 請求ID, 呼叫ID, 類型, 列ID, 路徑, 原因 = 文字
    if not all(_安全識別碼(項) for 項 in
               (遮蔽ID, 事件ID, 操作者, 請求ID, 呼叫ID, 列ID)):
        raise ValueError
    驗證遮蔽公開欄位(類型, 路徑, 原因)


def _安全識別碼(值: Any) -> bool:
    """判斷值是否為有界且無空白的exact識別字串。"""
    return type(值) is str and 0 < len(值) <= 128 and not any(字元.isspace() for 字元 in 值)


def _解析路徑(路徑: str) -> tuple[str, ...]:
    """解析有界RFC 6901路徑並拒絕非法跳脫。"""
    if 路徑 == "":
        return ()
    if type(路徑) is not str or not 路徑.startswith("/") or len(路徑) > 4096:
        raise ValueError
    結果 = []
    for 片段 in 路徑[1:].split("/"):
        if len(結果) >= 16 or len(片段) > 256 or re.search(r"~(?![01])", 片段):
            raise ValueError
        結果.append(片段.replace("~1", "/").replace("~0", "~"))
    return tuple(結果)


def 驗證遮蔽公開欄位(目標類型: object, JSON路徑: object, 原因: object, /) -> None:
    """驗證可公開遮蔽紀錄沿用不可逆遮蔽的目標、RFC 6901路徑與安全原因界線。"""
    if 目標類型 not in _目標 or type(JSON路徑) is not str or type(原因) is not str:
        raise ValueError
    if len(原因) > 256 or not 原因.strip() or _秘密格式.search(原因):
        raise ValueError
    _解析路徑(JSON路徑)


def _尋找JSON位置(payload: Any, 路徑: str) -> tuple[Any, Any]:
    """在exact JSON tree定位待遮蔽值的容器與鍵。"""
    片段列 = _解析路徑(路徑)
    現在 = payload
    for 片段 in 片段列[:-1]:
        現在 = _下一層(現在, 片段)
    if not 片段列:
        raise ValueError
    鍵 = _索引(現在, 片段列[-1])
    _ = 現在[鍵]
    return 現在, 鍵


def _下一層(現在: Any, 片段: str) -> Any:
    """以已驗證片段讀取下一層exact JSON節點。"""
    return 現在[_索引(現在, 片段)]


def _索引(現在: Any, 片段: str) -> Any:
    """把路徑片段轉為exact dict鍵或有界list索引。"""
    if type(現在) is dict:
        return 片段
    if type(現在) is list and 片段.isascii() and 片段.isdigit() and (片段 == "0" or not 片段.startswith("0")):
        索引 = int(片段)
        if 索引 < len(現在):
            return 索引
    raise ValueError


def _解析JSON(文字: str) -> Any:
    """解析並驗證有界、無重複鍵且finite的JSON。"""
    值 = json.loads(文字, parse_constant=_拒絕JSON常數, object_pairs_hook=_無重複物件)
    預算 = [0]
    if not _合法JSON(值, 0, 預算):
        raise ValueError
    return 值


def _拒絕JSON常數(_值: str) -> None:
    """拒絕JSON非有限常數。"""
    raise ValueError


def _無重複物件(項目: list[tuple[str, Any]]) -> dict[str, Any]:
    """建立無重複exact字串鍵的JSON物件。"""
    結果: dict[str, Any] = {}
    for 鍵, 值 in 項目:
        if 鍵 in 結果:
            raise ValueError
        結果[鍵] = 值
    return 結果


def _合法JSON(值: Any, 深度: int, 預算: list[int]) -> bool:
    """以共享節點與深度預算驗證exact JSON tree。"""
    預算[0] += 1
    if 預算[0] > 4096 or 深度 > 16:
        return False
    if 值 is None or type(值) in (bool, int, str):
        return True
    if type(值) is float:
        return math.isfinite(值)
    if type(值) is list:
        return all(_合法JSON(項, 深度 + 1, 預算) for 項 in 值)
    if type(值) is dict:
        return all(type(鍵) is str and _合法JSON(項, 深度 + 1, 預算) for 鍵, 項 in 值.items())
    return False


def _建立正規JSON(值: Any) -> str:
    """以固定排序與分隔符建立canonical JSON文字。"""
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _讀取payload(連線: sqlite3.Connection, 表格: str, 欄位: str,
               範圍: str, 參數: tuple[str, ...]) -> tuple[Any, str]:
    """先在SQLite內檢查型別與位元組長度，再實體化payload。"""
    中繼列 = 連線.execute(
        f"SELECT typeof({欄位}),length(CAST({欄位} AS BLOB)) FROM {表格} WHERE {範圍}", 參數,
    ).fetchall()
    if (len(中繼列) != 1 or 中繼列[0][0] != "text" or type(中繼列[0][1]) is not int
            or not 0 <= 中繼列[0][1] <= _最大JSON位元組):
        raise ValueError
    列 = 連線.execute(f"SELECT {欄位} FROM {表格} WHERE {範圍}", 參數).fetchall()
    if len(列) != 1 or type(列[0][0]) is not str:
        raise ValueError
    return _解析JSON(列[0][0]), 列[0][0]


def _確認墓碑(payload: Any, 路徑: str, 遮蔽ID: str, 時間: int | float) -> None:
    """確認既有payload仍保存指定不可逆墓碑。"""
    值 = payload if 路徑 == "" else _尋找JSON位置(payload, 路徑)[0][
        _尋找JSON位置(payload, 路徑)[1]
    ]
    if 值 != {"$tombstone": {"redaction_id": 遮蔽ID, "redacted_at": float(時間)}}:
        raise ValueError


def _相同重試(列: tuple[Any, ...], 遮蔽ID: str, 事件ID: str, 原因: str,
          操作者: str, 請求ID: str, 呼叫ID: str, 時間: int | float) -> bool:
    """確認所有ledger與audit語意完全匹配同一冪等請求。"""
    return (type(列) is tuple and len(列) == 24 and 列[0] == 遮蔽ID
            and 列[3] == 原因.strip() and 列[4] == 操作者 and 列[5] == 事件ID
            and 列[6] == 1 and 列[7] == float(時間) and 列[8:11] == ("admin", 呼叫ID, 事件ID)
            and 列[11:19] == (float(時間), "audit.payload.redact", "success", "user",
                               操作者, "endpoint.redaction", 遮蔽ID, 請求ID)
            and 列[19:24] == (列[23], 呼叫ID, '{"is_tombstone":true}', float(時間), 列[23])
            and type(列[2]) is str and len(列[2]) == 64)


def _結果(列: tuple[Any, ...], 類型: str, 列ID: str) -> dict[str, Any]:
    """由可信ledger tuple建立fresh受控墓碑結果。"""
    return {"redaction_id": 列[0], "target_type": 類型, "target_row_id": 列ID,
            "json_path": 列[1], "original_sha256": 列[2], "reason": 列[3],
            "actor_id": 列[4], "audit_event_id": 列[5], "is_tombstone": True,
            "redacted_at": 列[7]}


def _驗證遮蔽schema(連線: sqlite3.Connection) -> None:
    """在同一寫交易snapshot驗證完整遮蔽schema語意。"""
    if tuple(連線.execute("SELECT version,name FROM published_api_schema_migrations ORDER BY version")) != _LEDGER:
        raise ValueError
    欄位 = tuple(連線.execute("PRAGMA table_info(endpoint_redactions)"))
    外鍵 = tuple(連線.execute("PRAGMA foreign_key_list(endpoint_redactions)"))
    索引 = tuple(sorted((列[1], 列[2], 列[3], 列[4], tuple(
        項[2] for 項 in 連線.execute(f'PRAGMA index_info("{列[1]}")')
    )) for 列 in 連線.execute("PRAGMA index_list(endpoint_redactions)")))
    物件 = tuple((類型, 名稱, hashlib.sha256(SQL.encode()).hexdigest())
               for 類型, 名稱, SQL in 連線.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND "
        "(tbl_name='endpoint_redactions' OR name IN "
        "('redacted_invocation_payload_no_update','redacted_run_event_no_update',"
        "'redacted_tool_call_no_update','redacted_run_event_no_delete',"
        "'redacted_tool_call_no_delete')) AND type IN ('table','trigger') ORDER BY type,name"
    ))
    if (欄位 != ((0,"id","TEXT",0,None,1),(1,"invocation_id","TEXT",1,None,0),
        (2,"target_type","TEXT",1,None,0),(3,"target_row_id","TEXT",1,None,0),
        (4,"json_path","TEXT",1,"''",0),(5,"original_sha256","TEXT",1,None,0),
        (6,"reason","TEXT",1,None,0),(7,"actor_type","TEXT",1,None,0),
        (8,"actor_id","TEXT",0,None,0),(9,"audit_event_id","TEXT",1,None,0),
        (10,"is_tombstone","INTEGER",1,None,0),(11,"redacted_at","REAL",1,None,0))
        or 外鍵 != ((0,0,"audit_events","audit_event_id","id","NO ACTION","RESTRICT","NONE"),
                       (1,0,"endpoint_invocations","invocation_id","id","NO ACTION","RESTRICT","NONE"))
        or 索引 != (("idx_endpoint_redactions_audit",0,"c",0,("audit_event_id",)),
                    ("idx_endpoint_redactions_invocation_time",0,"c",0,("invocation_id","redacted_at")),
                    ("idx_endpoint_redactions_retention_invocation_id",0,"c",0,("invocation_id","id")),
                    ("sqlite_autoindex_endpoint_redactions_1",1,"pk",0,("id",)),
                    ("sqlite_autoindex_endpoint_redactions_2",1,"u",0,("target_type","target_row_id","json_path")))
        or 物件 != _遮蔽物件摘要):
        raise ValueError
