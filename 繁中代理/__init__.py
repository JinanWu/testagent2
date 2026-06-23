"""testagent2 的 Hermes-style Agent 套件。

此套件示範一個可執行的 CLI 型 AI Agent MVP。程式內部保留
OpenAI-compatible message canonical shape，只有在 provider adapter 邊界才轉換成
Gemini Vertex AI 所需格式。專案刻意使用繁體中文撰寫專案自有類別、函數、變數、註解與文檔，以支援易讀性實驗。
"""

__all__ = ["代理執行階段", "提示詞組裝器", "工作階段庫"]
