#!/bin/bash
# Ежедневное обновление YouTube Trends:
#   1. подтягивает новые daily_package из MyCrabs
#   2. пересобирает дашборд и граф
#   3. коммитит и пушит изменения
#
# Требует: .env в корне проекта со строкой MYCRABS_TOKEN=ghp_...

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$PROJECT_DIR/logs/daily_refresh.log"
mkdir -p "$PROJECT_DIR/logs"

exec >> "$LOG" 2>&1
echo ""
echo "=========================================="
echo "$(date '+%Y-%m-%d %H:%M:%S')  START"
echo "=========================================="

cd "$PROJECT_DIR"

# Загрузить токен из .env
if [ -f ".env" ]; then
  export "$(grep -v '^#' .env | xargs)"
else
  echo "ERROR: .env не найден — создайте файл по образцу .env.example"
  exit 1
fi

if [ -z "${MYCRABS_TOKEN:-}" ]; then
  echo "ERROR: MYCRABS_TOKEN не задан в .env"
  exit 1
fi

# Определить python
PYTHON="${PYTHON:-python3}"

echo "--- fetch ---"
$PYTHON scripts/fetch_packages.py --dest data/raw

echo "--- process ---"
$PYTHON scripts/process.py --raw data/raw --out docs/data

echo "--- refresh metrics ---"
# Забирает актуальные просмотры/лайки/комментарии и статус каждого видео с YouTube.
# Данные публичные — авторизация не нужна.
$PYTHON scripts/refresh_metrics.py --out docs/data
# Пересобираем дашборд с обновлёнными данными.
$PYTHON scripts/process.py --raw data/raw --out docs/data

echo "--- git ---"
git add data/raw docs/data
if git diff --cached --quiet; then
  echo "Нет новых данных — пуш не нужен."
else
  git commit -m "data: daily refresh $(date '+%Y-%m-%d')"
  git push
  echo "Запушено."
fi

echo "$(date '+%Y-%m-%d %H:%M:%S')  DONE"
