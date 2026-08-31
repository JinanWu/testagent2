# syntax=docker/dockerfile:1.7
#
# testagent2 後端服務 image：uvicorn 只供應 canonical API。
#
# 設計原則：
#   - 依使用情境決定設定來源；GCP ADC 一律由執行環境提供
#     （本機用 compose 掛載 ~/.config/gcloud，雲端靠 service account 的 metadata server）。
#   - 依賴層與原始碼層分離，改程式碼不會觸發重裝依賴。
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# 依賴層：只帶 pyproject 與空套件目錄，讓後續改原始碼不必重裝依賴。
# playwright 目前沒有宣告在 pyproject，這裡對齊 .venv 的 1.62.0 明確 pin。
COPY pyproject.toml constraints-a08.txt ./
RUN mkdir -p 繁中代理 \
 && touch 繁中代理/__init__.py \
 && pip install -c constraints-a08.txt -e . \
 && pip install -c constraints-a08.txt playwright==1.62.0

# web_render 工具用的 Chromium。--with-deps 補齊 Debian 缺的系統函式庫；
# 裝在 /ms-playwright 而非 /root/.cache，非 root 執行時才讀得到。
RUN playwright install --with-deps chromium \
 && chmod -R a+rX /ms-playwright

# 原始碼層：覆蓋掉上面的空套件目錄
COPY 繁中代理/ ./繁中代理/
COPY assets/ ./assets/
COPY asgi.py ./

COPY docker-seed/ ./docker-seed/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint

RUN useradd --create-home --uid 10001 app \
 && chmod 755 /usr/local/bin/docker-entrypoint \
 && mkdir -p /tmp/agent-service/bundles \
 && chown -R app:app /app /tmp/agent-service
USER app

# 容器內的固定路徑 authority；compose 與雲端部署都沿用這組值。
# 四個路徑互不為別名，符合 解析Canonical環境設定 的契約。
ENV TESTAGENT2_DB_PATH=/tmp/agent-service/web.sqlite3 \
    TESTAGENT2_PUBLISHED_DB_PATH=/tmp/agent-service/published.sqlite3 \
    TESTAGENT2_PUBLISHED_BUNDLE_ROOT=/tmp/agent-service/bundles \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/healthz\",timeout=4)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint"]

# bind 0.0.0.0 讓容器外連得進來；讀 PORT 讓 Cloud Run 之類的平台能指定埠號。
CMD ["sh", "-c", "exec python -m uvicorn asgi:建立應用程式 --factory --host 0.0.0.0 --port ${PORT:-8000}"]
