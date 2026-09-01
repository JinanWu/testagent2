"""CP3 Chat、sessions、skills 的 strict HTTP 成功回應模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _嚴格模型(BaseModel):
    """拒絕額外欄位並允許繁中Python名稱對應固定HTTP aliases。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class 聊天回覆模型(_嚴格模型):
    """只公開assistant角色與有界回覆內容。"""

    角色: Literal["assistant"] = Field(alias="role")
    內容: str = Field(alias="content", max_length=65_536)


class 聊天成功回應(_嚴格模型):
    """Chat成功時公開logical session ID與assistant回覆。"""

    工作階段識別碼: str = Field(alias="session_id", min_length=1, max_length=128)
    回覆: 聊天回覆模型 = Field(alias="reply")


class 工作階段列表項目模型(_嚴格模型):
    """工作階段列表單項的固定公開欄位。"""

    識別碼: str = Field(alias="id", min_length=1, max_length=128)
    標題: str = Field(alias="title", max_length=512)
    更新時間: float = Field(alias="updated_at")
    訊息數量: int = Field(alias="message_count", ge=0)


class 工作階段列表回應(_嚴格模型):
    """最多回傳五十個登入使用者可見的logical roots。"""

    工作階段清單: list[工作階段列表項目模型] = Field(alias="sessions", max_length=50)


class 工作階段模型(_嚴格模型):
    """工作階段詳情中的固定公開metadata。"""

    識別碼: str = Field(alias="id", min_length=1, max_length=128)
    標題: str = Field(alias="title", max_length=512)
    更新時間: float = Field(alias="updated_at")


class 工作階段訊息模型(_嚴格模型):
    """只允許user或assistant純文字訊息。"""

    角色: Literal["user", "assistant"] = Field(alias="role")
    內容: str = Field(alias="content", max_length=65_536)


class 工作階段詳情回應(_嚴格模型):
    """公開root metadata與有界安全文字transcript。"""

    工作階段: 工作階段模型 = Field(alias="session")
    訊息清單: list[工作階段訊息模型] = Field(alias="messages", max_length=10_000)


class 技能項目模型(_嚴格模型):
    """技能列表的固定公開metadata，不包含來源路徑。"""

    識別碼: str = Field(alias="id", min_length=1, max_length=128)
    名稱: str = Field(alias="name", min_length=1, max_length=128)
    分類: str = Field(alias="category", min_length=1, max_length=256)
    描述: str = Field(alias="description", max_length=1_024)


class 技能列表回應(_嚴格模型):
    """公開有界且已授權的技能metadata清單。"""

    技能清單: list[技能項目模型] = Field(alias="skills", max_length=1_000)


class 技能詳情回應(技能項目模型):
    """公開單一授權技能metadata與安全讀取內容。"""

    內容: str = Field(alias="content", max_length=262_144)
