# testagent2 frontend

這個資料夾是獨立部署的 React/Vite 前端。瀏覽器只連到本服務；Nginx 會把
`/api/*` 與 `/v1/*` 轉送到 `BACKEND_ORIGIN` 指定的後端。

## 本機開發

```bash
npm ci
npm run dev
```

Vite 開發伺服器會將 `/api/*` 代理到 `http://127.0.0.1:8000`。

## Production build

```bash
npm run typecheck
npm test
npm run build
docker build -t testagent2-web .
docker run --rm -p 8080:8080 \
  -e BACKEND_ORIGIN=http://host.docker.internal:8000 testagent2-web
```

前端程式不得設定或使用後端公開 URL；API request 一律維持相對路徑。

## Cloud Run

`cloud-run.yaml` 會部署獨立的 frontend service。部署前明確替換：

- `REPLACE_FRONTEND_SERVICE_ACCOUNT`：前端 Cloud Run service account。
- `REPLACE_BACKEND_HOSTNAME`：後端 Cloud Run HTTPS hostname，不含結尾 `/`。
- image URI：改成此次建置並推送的 immutable tag 或 digest。

後端的 `TESTAGENT2_WEB_ORIGINS` 必須包含正式前端 origin。`BACKEND_ORIGIN`
只由 Nginx runtime 讀取，不會進入 Vite bundle。
