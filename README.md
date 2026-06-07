# YouTube Trends 📊

Ежедневный дашборд для отслеживания **динамики и трендов по каждой карточке
видео** в разрезе авторов и тем, плюс **карта связей идей** между видео в стиле
Obsidian.

Проект каждый день обходит папку `youtube-daily` в приватном репозитории
**MyCrabs**, находит файлы `daily_package` (в форматах `.json` и `.md`),
копирует их к себе, разбирает, накапливает историю метрик и строит:

- 📈 **дашборд** с динамикой просмотров / лайков / комментариев по каждому видео,
  агрегатами по авторам и темам и таймлайном;
- 🕸️ **граф связей идей** — узлы-видео соединяются по смысловой близости
  (семантические эмбеддинги) и по общим тегам.

---

## Как это работает

```
MyCrabs/youtube-daily/*daily_package*.{json,md}
        │  (1) fetch_packages.py — клонирует MyCrabs по PAT и копирует пакеты
        ▼
   data/raw/                        ← архив исходных пакетов в этом репо
        │  (2) process.py — парсинг .md + .json, слияние, накопление истории
        ▼
   docs/data/history.json           ← сырые временные ряды по каждому видео
   docs/data/dashboard.json         ← карточки, авторы, темы, таймлайн, тренды
   docs/data/graph.json             ← узлы и рёбра карты связей идей
        │  (3) docs/index.html — статический дашборд (GitHub Pages)
        ▼
   📊 Дашборд + 🕸️ Карта связей
```

### Форматы пакетов
Поддерживаются `.json`, `.jsonl`, `.md`/`.markdown` и `.txt`. Одно и то же видео
может быть описано и в JSON (метрики), и в Markdown-заметке (идеи, конспект) —
парсер **объединяет** их по `(video_id, дата)`, беря лучшее из обоих форматов.
Распознаётся широкий набор имён полей (`channel`/`author`, `views`/`viewCount`,
`ideas`/`key_points`/`тезисы` и т.д.) — см. `scripts/lib/normalize.py`.

### Связи идей (семантика)
`scripts/lib/embed.py` строит рёбра между похожими видео. Бэкенды (по убыванию
качества, с автоматическим выбором):

1. **sentence-transformers** (`paraphrase-multilingual-MiniLM-L12-v2`) —
   мультиязычные семантические эмбеддинги (основной режим в CI);
2. **scikit-learn TF-IDF** — лёгкий лексический бэкенд;
3. **встроенный TF-IDF на чистом Python** — работает вообще без зависимостей.

Порог похожести подбирается под бэкенд (косинус у плотных и разреженных
векторов в разных шкалах). Управление через переменные окружения:
`EMBED_BACKEND=auto|st|tfidf`, `EMBED_MODEL=<hf-модель>`.

---

## Запуск локально

```bash
pip install -r requirements.txt          # опционально: без этого работает fallback

# (опционально) подтянуть свежие пакеты из приватного MyCrabs
export MYCRABS_TOKEN=ghp_xxx              # PAT с доступом на чтение MyCrabs
python scripts/fetch_packages.py --dest data/raw

# собрать датасеты дашборда и графа
python scripts/process.py --raw data/raw --out docs/data

# посмотреть дашборд
python -m http.server -d docs 8000       # http://localhost:8000
```

В репозитории уже лежат демо-пакеты в `data/raw/` (6 видео, 3 автора, 3 темы,
два дня замеров), поэтому дашборд открывается сразу. Когда пойдут реальные
данные — удалите демо-файлы `data/raw/2026-06-0*` и `docs/data/history.json`,
чтобы история началась с чистого листа.

---

## Настройка автоматизации (GitHub Actions)

Воркфлоу `.github/workflows/daily.yml` запускается ежедневно (05:30 UTC) и
вручную (`workflow_dispatch`). Что нужно один раз настроить:

1. **Секрет `MYCRABS_TOKEN`** — Settings → Secrets and variables → Actions →
   *New repository secret*. Это Personal Access Token с правом `repo` (или
   fine-grained read-only на репозиторий MyCrabs).
2. **GitHub Pages** — Settings → Pages → Source: *GitHub Actions*.
3. (опционально) переменные репозитория: `MYCRABS_REPO` (по умолчанию
   `bugoll/MyCrabs`), `SOURCE_DIR` (`youtube-daily`), `EMBED_BACKEND` (`auto`).

После этого каждый день: пакеты копируются в `data/raw/`, пересобираются
`docs/data/*.json`, изменения коммитятся, дашборд публикуется на Pages.

---

## Рабочий процесс (локально ↔ GitHub)

Разработка ведётся в локальной папке `youtube_trends`, синхронизированной с
GitHub через git: новые файлы создаются локально и пушатся в репозиторий.

```bash
git clone https://github.com/bugoll/youtube_trends.git
cd youtube_trends
# ...правки...
git add -A && git commit -m "..." && git push
```

---

## Структура

```
.github/workflows/daily.yml   ежедневный пайплайн + публикация Pages
scripts/
  fetch_packages.py           клонирование MyCrabs и копирование пакетов
  process.py                  парсинг → история → dashboard.json + graph.json
  lib/normalize.py            нормализация .json/.md в единую схему
  lib/embed.py                семантические/TF-IDF связи идей
docs/                         статический дашборд (корень GitHub Pages)
  index.html, styles.css, app.js, graph.js
  data/                       сгенерированные датасеты
data/raw/                     архив исходных пакетов daily_package
```
