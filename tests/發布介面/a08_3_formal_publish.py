"""A08-3 formal publication graph through lifespan-owned production management."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.生產技能工具 import 安裝生產技能工具, 技能工具發布名稱
from 繁中代理.發布介面.規劃.規劃器供應商 import Gemini規劃器

from 繁中代理.發布介面.路由.規劃發布 import 發布確認, 管理操作錯誤
from 繁中代理.發布介面.設定 import 生產設定


def _正規(值: object) -> str:
    return json.dumps(值, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class _固定Planner產生器:
    def 產生JSON(self, *, 系統指令: str, 使用者內容: str) -> str:
        del 系統指令
        輸入 = json.loads(使用者內容)
        版本 = "V2" if "V2" in 輸入["original_requirement_text"] else "V1"
        return _正規({
            "endpoint_name": "Stable API", "suggested_slug": "stable",
            "behavior_summary": 輸入["original_requirement_text"],
            "selected_skills": ["stable"],
            "recommended_tools": ["skill_view", "skills_list"],
            "tool_capabilities": {"skill_view": "view", "skills_list": "list"},
            "system_prompt": f"SYSTEM-{版本}", "input_schema": {"type": "object"},
            "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                                "required": ["answer"], "additionalProperties": False},
            "human_docs": "stable", "rate_limit": {"endpoint_per_minute": 100, "credential_per_minute": 100},
            "warnings": [],
        })


def _模型設定(版本: int) -> dict[str, object]:
    return {"provider": "gemini-adc", "model": f"gemini-pinned-v{版本}", "temperature": 0.0,
            "max_tokens": 100, "timeout_seconds": 3.0, "structured_output": True,
            "schema_retry_count": 1}


def _管理應用(web: Path, db: Path, bundles: Path):
    bundles.mkdir(parents=True, exist_ok=True)
    web設定 = 生產設定(web, ("http://localhost:5173",), "gemini-adc", "gemini-pinned-v1",
                      "test-project", "global", Cookie安全=False, 工作階段有效秒數=60)
    published = Published生產設定(
        db, bundles, 安裝生產技能工具, lambda: {"gemini-adc": object()},
        Planner設定=Planner生產設定(
            技能工具發布名稱, lambda 路徑: 使用者庫(路徑),
            lambda: Gemini規劃器(_固定Planner產生器()), 3600.0),
        憑證封套工廠=lambda: AESGCM憑證封套({1: b"A" * 32}, 1),
    )
    return 建立CP4ASGI應用程式(web設定, published)


def _配置(版本: int) -> dict[str, Any]:
    return {
        "original_requirement_text": f"REQ-V{版本}", "system_prompt": f"SYSTEM-V{版本}",
        "model_config_snapshot": _模型設定(版本), "retry_policy": {"max_attempts": 2},
        "input_schema": {"type": "object"},
        "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                            "required": ["answer"], "additionalProperties": False},
    }


def 建立正式v1(
    *, web: Path, db: Path, bundles: Path, skill_root: Path,
    skill_body: str = "BUNDLE-V1",
) -> dict[str, str]:
    skill = skill_root / "stable"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: stable\ndescription: A08 stable skill\n---\n\n" + skill_body,
        encoding="utf-8",
    )
    users = 使用者庫(web)
    try:
        owner = users.建立使用者(
            "stable-owner", "[REDACTED]", roles=["user"],
            enabled_tools=["skills_list", "skill_view"], enabled_skills=["stable"],
            skill_roots=[str(skill_root)], allowed_workdirs=[str(skill_root.parent)],
        )
        owner_id = owner["id"]
    finally:
        users.連線.close()
    app = _管理應用(web, db, bundles)
    with TestClient(app) as _client:
        resource = app.state.發布介面資源[-1]
        planner = resource.取得Planner資源()._服務
        draft = planner.建立草稿(owner_id, "REQ-V1", ("stable",), "structured", 現在=time.time())
        preview = draft.綱要
        result = resource.取得發布管理服務().原子發布(
            擁有者使用者識別碼=owner_id,
            確認=發布確認(draft.草稿識別碼, "stable", {
                "system_prompt": preview["system_prompt"], "input_schema": preview["input_schema"],
                "response_schema": preview["response_schema"], "human_docs": preview["human_docs"],
                "rate_limit": preview["rate_limit"],
            }),
        )
        assert not isinstance(result, 管理操作錯誤)
        return {"owner": owner_id, "endpoint": result.端點識別碼,
                "v1": result.版本識別碼, "key": result.初始API金鑰}


def 正式切換v2(*, web: Path, db: Path, bundles: Path, skill_root: Path,
             owner: str, endpoint: str) -> str:
    (skill_root / "stable" / "SKILL.md").write_text(
        "---\nname: stable\ndescription: A08 stable skill\n---\n\nBUNDLE-V2", encoding="utf-8")
    app = _管理應用(web, db, bundles)
    with TestClient(app) as _client:
        result = app.state.發布介面資源[-1].取得發布管理服務().原子建立並切換版本(
            擁有者使用者識別碼=owner, 端點識別碼=endpoint, 配置=_配置(2))
        assert not isinstance(result, 管理操作錯誤)
        assert result.版本編號 == 2 and result.目前版本識別碼 == result.版本識別碼
        return result.版本識別碼
