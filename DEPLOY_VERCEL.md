# Деплой BankHub на Vercel

Проект разворачивается как два Vercel Project из одного репозитория:

1. `bankhub-api` — FastAPI из корня репозитория;
2. `bankhub-web` — Next.js из каталога `web`.

Так 1С получает отдельный стабильный HTTPS-адрес API, а браузер работает через
same-origin proxy Next.js и не требует CORS.

## 1. PostgreSQL

В Vercel Marketplace подключите PostgreSQL-провайдера, например Neon или Supabase,
к проекту `bankhub-api`. В переменную `DATABASE_URL` должен попасть pooled connection
string с обязательным TLS. Локальный `DATABASE_PATH` на Vercel не используется.

Таблица и индексы создаются автоматически при первом старте FastAPI. Содержимое
локального файла `data/bankhub.sqlite3` автоматически не переносится.

## 2. Backend `bankhub-api`

Создайте Vercel Project со следующими настройками:

- Root Directory: корень репозитория (`.`);
- entrypoint: `server.py` — определяется автоматически;
- Framework Preset: определяется Vercel как FastAPI/Python;
- Python: версия из `.python-version`;
- Production Environment Variables:

```env
DATABASE_URL=postgresql://...pooled...
APP_API_KEY=длинный_случайный_секрет
MAX_PDF_MB=4
OPENAI_API_KEY=необязательно
OPENAI_MODEL=gpt-4o
OPENAI_TIMEOUT_SECONDS=45
```

Не используйте `change-me` в production. После деплоя проверьте:

```text
https://bankhub-api.vercel.app/health
https://bankhub-api.vercel.app/docs
```

`/health` должен вернуть `database: "postgresql"` и `max_pdf_mb: 4`.

## 3. Frontend `bankhub-web`

Создайте второй Vercel Project из того же репозитория:

- Root Directory: `web`;
- Framework Preset: Next.js;
- Environment Variable:

```env
BACKEND_URL=https://bankhub-api.vercel.app
```

Сначала разверните API, затем frontend: адрес API нужен Next.js во время сборки.
После изменения `BACKEND_URL` обязательно выполните новый deployment.

## 4. Подключение 1С

В расширении 1С используйте HTTPS-адрес backend-проекта и тот же `APP_API_KEY`:

```bsl
BankHubClient.ЗагрузитьПодтвержденныеОперации(
    "https://bankhub-api.vercel.app",
    "длинный_случайный_секрет");
```

1С подключается к API исходящим HTTPS-запросом. Публиковать саму базу 1С в интернет
для BankHub не требуется.

## Ограничение PDF

Vercel Function ограничивает тело запроса 4,5 МБ, включая multipart-обвязку, поэтому
приложение разрешает PDF до 4 МБ. Файл обрабатывается в памяти и не сохраняется.
Для более крупных выписок следующим этапом нужна прямая загрузка в Blob Storage.

## Проверка после деплоя

1. Откройте frontend и сохраните `APP_API_KEY` в интерфейсе.
2. Загрузите небольшой PDF и подтвердите одну операцию.
3. Проверьте API `GET /api/v1/transactions/export?status=ready` с заголовком
   `X-API-Key`.
4. Запустите импорт в тестовой базе 1С.
5. Убедитесь, что создан непроведенный банковский документ, а операция получила
   статус `exported`.
