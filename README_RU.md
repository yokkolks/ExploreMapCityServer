# ExploreMap City Server — Render ready

Готовый FastAPI-сервер для ExploreMap. Он определяет город по GPS, получает районы из Overture Maps через DuckDB и возвращает GeoJSON. Результаты городов кэшируются локально на экземпляре сервера.

## Быстрый запуск локально

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Проверка: `http://127.0.0.1:8000/health`

## Деплой на Render

В репозитории уже есть `render.yaml`, поэтому основные настройки подготовлены. Render использует Python, устанавливает зависимости из `requirements.txt` и запускает Uvicorn на `$PORT`. Регион настроен на Frankfurt.

Можно создать Web Service через Render Dashboard и выбрать GitHub-репозиторий. Если Render предложит использовать Blueprint из `render.yaml`, согласись.

После деплоя проверь:

- `/health` — должен вернуть `ok: true` и `db_ready: true`
- `/docs` — Swagger API

## Важно

`.venv`, `__pycache__`, `city_cache` и локальные DuckDB-файлы специально не входят в Git/архив как рабочие данные. Render сам создаёт Python-окружение и приложение создаёт необходимые файлы при запуске.

На бесплатном Render экземпляр может засыпать после периода без запросов, поэтому первый запрос после простоя может быть медленнее. Локальный `city_cache` также не следует считать постоянным хранилищем.

## API

`GET /api/v1/city-districts?latitude=...&longitude=...`

Возвращает GeoJSON районов текущего города.
