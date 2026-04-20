#!/bin/sh
# Dispatches one image to two Railway services based on SERVICE_TYPE.
#   SERVICE_TYPE=api → uvicorn on $PORT (FastAPI + orchestrator daemon)
#   SERVICE_TYPE=bot (default) → python-telegram-bot long polling
set -e

case "${SERVICE_TYPE:-bot}" in
  api)
    exec uvicorn webapp.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  bot)
    exec python -m telegram_bot.run_bot
    ;;
  *)
    echo "Unknown SERVICE_TYPE=$SERVICE_TYPE (expected 'api' or 'bot')" >&2
    exit 1
    ;;
esac
