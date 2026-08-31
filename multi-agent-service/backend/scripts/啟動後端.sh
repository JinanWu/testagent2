#!/usr/bin/env bash
# 本機開發啟動後端。設定來自 repo 外的 ~/.config/testagent2/dev.env。
# 註：shell 變數名沿用 ASCII —— bash/zsh 的識別碼都不支援多位元組字元。
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
env_file="${TESTAGENT2_DEV_ENV:-$HOME/.config/testagent2/dev.env}"
[ -f "$env_file" ] || { echo "找不到設定檔：$env_file" >&2; exit 1; }

# 後端有嚴格白名單：先清掉 shell 裡所有 TESTAGENT2_*/AIAGENT_*，
# 避免非核准變數（例如 .env 的 AIAGENT_MODEL）讓啟動固定失敗。
for name in ${!TESTAGENT2_@} ${!AIAGENT_@}; do
  unset "$name"
done

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

cd "$root"
exec env -u PYTHONPATH -u VIRTUAL_ENV -u PYTHONHOME PYTHONNOUSERSITE=1 \
  .venv/bin/python -m uvicorn asgi:建立應用程式 --factory \
  --host 127.0.0.1 --port "${PORT:-8000}"
