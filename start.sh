#!/usr/bin/env bash
# Запуск склада зеркал на macOS и Linux.
# Повторный запуск безопасен: уже сделанное не переделывается.
set -e
cd "$(dirname "$0")"

echo "========================================"
echo "  СКЛАД ЗЕРКАЛ - запуск"
echo "========================================"
echo

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "[ОШИБКА] Python не найден. Установите Python 3.11 или новее."
  exit 1
fi

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "[1/3] Создаю рабочее окружение. Это делается один раз..."
  "$PYTHON" -m venv .venv
fi

if ! "$PY" -c "import fastapi, sqlalchemy, reportlab, openpyxl" >/dev/null 2>&1; then
  echo "[2/3] Устанавливаю библиотеки. Первый раз это занимает пару минут..."
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
fi

echo "[3/3] Готовлю базу..."
echo
"$PY" -m app.cli setup

echo
echo "========================================"
echo "  Сайт открывается: http://localhost:8000"
echo
echo "  Вход: Админов Антон, PIN 1234"
echo
echo "  Остановить - Ctrl+C"
echo "========================================"
echo

( sleep 3; (command -v xdg-open >/dev/null && xdg-open http://localhost:8000) \
  || (command -v open >/dev/null && open http://localhost:8000) || true ) >/dev/null 2>&1 &

exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
