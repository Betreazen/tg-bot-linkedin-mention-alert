# LinkedIn Mentions Alert Bot

Независимый бот: через официальный LinkedIn Community Management API забирает **все**
упоминания компании (`SHARE_MENTION`) и шлёт единый поток алертов со ссылками в один
Telegram-чат. Polling раз в 15 минут, состояние — в SQLite на Docker volume.

Подробности: [SPEC.md](SPEC.md) · план: [PLAN.md](PLAN.md) · статус: [PROGRESS.md](PROGRESS.md).

## Стек

Python 3.12+, `httpx`, `sqlite3` (stdlib), `python-dotenv`, `tzdata`. Docker + compose.

## Конфигурация

```bash
cp .env.example .env      # заполнить значения; .env в git НЕ коммитится
```

Ключевые переменные (полный список — в `.env.example` и SPEC §7):

| Переменная | Назначение |
|---|---|
| `LINKEDIN_CLIENT_ID/SECRET` | приложение LinkedIn |
| `LINKEDIN_ORG_URNS` | org URN (список через запятую) |
| `LINKEDIN_API_VERSION` | заголовок `LinkedIn-Version` (по умолчанию `202606`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | куда слать алерты |
| `POLL_INTERVAL_MINUTES` | интервал опроса (15) |
| `FAILURE_ALERT_THRESHOLD` | порог подряд идущих сбоев для служебного алерта (3) |
| `REFRESH_TOKEN_WARNING_DAYS` | за сколько дней предупредить об истечении refresh (14) |
| `DB_PATH` | путь к SQLite (в контейнере `/data/bot.db`) |

## Настройка Telegram

1. Создать бота у [@BotFather](https://t.me/BotFather) (`/newbot`) — полученный токен
   записать в `TELEGRAM_BOT_TOKEN`.
2. Добавить бота в чат/группу, куда должны приходить алерты, и отправить туда любое
   сообщение.
3. Узнать `chat_id`:

   ```bash
   curl "https://api.telegram.org/bot<ТОКЕН>/getUpdates"
   ```

   В ответе найти `"chat":{"id":...}` (у групп id отрицательный, например `-100123456789`)
   и записать в `TELEGRAM_CHAT_ID`.

## Разовая авторизация LinkedIn (вручную, вне контейнера)

1. Создать приложение в LinkedIn Developer Portal, запросить **Community Management API**
   и программные refresh tokens (SPEC §8.1). Выписать scope продукта из портала.
2. **Остановить бот, если он запущен** (`docker compose down`) — параллельная запись
   токенов конфликтует с рефрешем.
3. Подготовить окружение и сгенерировать ссылку авторизации (state защищает от подмены
   кода — CSRF):

   PowerShell (Windows):
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   $env:DB_PATH = "./data/bot.db"
   .venv\Scripts\python setup_auth.py url --redirect-uri <URI> --scope "<SCOPE из портала>"
   ```

   bash (Linux/macOS):
   ```bash
   python -m venv .venv && .venv/bin/pip install -r requirements.txt
   DB_PATH=./data/bot.db .venv/bin/python setup_auth.py url --redirect-uri <URI> --scope "<SCOPE>"
   ```

4. Открыть напечатанную ссылку в браузере под админом страницы, подтвердить доступ,
   скопировать **полный URL** из адресной строки после redirect и обменять код:

   ```powershell
   .venv\Scripts\python setup_auth.py exchange --redirect-response "<ПОЛНЫЙ URL>"
   ```

   Токены запишутся в ту же БД, что монтируется в контейнер. Дальше бот сам поддерживает
   токены (включая реактивный refresh по 401/403). Раз в ~365 дней (истечение
   `refresh_token`) повторить шаги 2–4; бот заранее предупредит в Telegram за
   `REFRESH_TOKEN_WARNING_DAYS` и продолжит напоминать раз в сутки.

## Запуск

```bash
docker compose up -d --build      # поднять бота
docker compose logs -f            # логи (stdout, с ротацией 3×10 МБ)
docker ps                         # колонка STATUS показывает (healthy|unhealthy)
docker compose down               # остановить (SIGTERM обрабатывается аккуратно)
```

Контейнер работает **не под root** (UID 10001). На Linux-хосте выдать каталогу данных
права перед первым запуском: `mkdir -p data && sudo chown -R 10001 data`
(на Docker Desktop под Windows/macOS не требуется).

БД (`./data/bot.db`, права 0600) хранит токены, дедуп упоминаний (ретеншен 90 дней) и
курсор опроса — состояние переживает рестарт. `HEALTHCHECK` следит, что цикл опроса
живой, по mtime heartbeat-файла (`/tmp/bot-heartbeat`, пишется каждый цикл): файл вместо
второго соединения к SQLite, потому что на bind mount Docker Desktop (Windows/macOS)
параллельные соединения SQLite нестабильны.

## Надёжность и алертинг

- Сбой любого рода (сеть, LinkedIn, Telegram, SQLite, неожиданное исключение) попадает
  в счётчик подряд идущих сбоев; на пороге `FAILURE_ALERT_THRESHOLD` уходит служебный
  алерт, и пока сбой продолжается — повторяется раз в сутки.
- Неудачная доставка алерта повторяется в следующем цикле (не теряется).
- Затяжная серия 429 от LinkedIn считается отдельно и тоже алертится по порогу.
- 401/403 → автоматический реактивный refresh токена + повтор запроса (SPEC §10).
- Один кривой элемент в ответе LinkedIn пропускается с WARNING, не блокируя остальные.
- Ответы длиннее одной страницы дочитываются пагинацией (`start`/`count`).

## Разработка и тесты

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest --cov --cov-report=term-missing
.venv\Scripts\python -m mypy src main.py setup_auth.py
```

Внешние вызовы (LinkedIn, Telegram) замоканы через `httpx.MockTransport` — тесты не ходят
в сеть. Покрытие включает `main.py` и `setup_auth.py`.

## Статус

Логика v1 готова и укреплена по итогам код-ревью (см. PROGRESS.md, 2026-07-31):
160 тестов, покрытие 98%, mypy чистый. Финальная валидация пагинации и формы ответа
`organizationalEntityNotifications` — на живых токенах.
