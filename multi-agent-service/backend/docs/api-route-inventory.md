# API route inventory

前端 Nginx 會完整代理兩個前綴，因此以下所有 API 都會被轉送到後端：

| Prefix | Routes |
| --- | --- |
| `/api/auth` | `POST /login`、`GET /me`、`GET /session`、`POST /logout` |
| `/api/chat` | `POST /api/chat` |
| `/api/sessions` | `GET /api/sessions`、`GET /api/sessions/{session_id}` |
| `/api/skills` | `GET /api/skills`、`GET /api/skills/{skill_id}` |
| `/api/published-endpoints` | list/detail、draft、publish、version、credential、docs、owner metrics/diagnostics |
| `/api/admin` | invocation list/detail、redaction |
| `/v1/endpoints` | `POST /{slug}/invoke`、`GET /{slug}/docs` |

非瀏覽器 API 仍保留在 backend 直接處理：`GET /healthz` 與 `GET /openapi.json`。

代理設定位於 `frontend/nginx.conf`。新增任何 API 時，必須維持其路徑在
`/api/*` 或 `/v1/*`；若引入新頂層 prefix，必須同步增加前端 Nginx location
與本文件。
