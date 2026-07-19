"""發布介面 FastAPI 應用程式的固定設定。"""

發布介面標題 = "繁中代理發布介面"
"""OpenAPI 的固定應用程式標題。"""

發布介面版本 = "0.1.0"
"""OpenAPI 的固定應用程式版本。"""

允許路由前綴 = (
    "/api/published-endpoints",
    "/api/admin",
    "/api/chat",
    "/api/auth",
    "/v1/endpoints",
)
"""後續 invoke、管理與認證能力可使用的 exact router prefixes。"""

路由設定錯誤訊息 = "發布介面路由設定無效"
"""composition inventory 不合法時的固定錯誤。"""

啟動錯誤訊息 = "發布介面啟動失敗"
"""一般 startup 失敗的固定錯誤。"""

關閉錯誤訊息 = "發布介面關閉失敗"
"""一般 shutdown 失敗的固定錯誤。"""
