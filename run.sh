#!/usr/bin/env sh
# MarginGuard: запуск одной командой (macOS / Linux). Нужен Python 3.11+.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "Создаю виртуальное окружение…"
  $PY -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
PORT=${PORT:-8000}
echo "Откройте http://localhost:$PORT  (с телефона в той же Wi-Fi: http://<IP-компьютера>:$PORT)"
exec python -m uvicorn app.web.main:app --host 0.0.0.0 --port "$PORT"
