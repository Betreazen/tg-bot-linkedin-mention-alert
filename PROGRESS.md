# Прогресс-лог: LinkedIn Mentions Alert Bot

Живой журнал статуса разработки. Обновляется по мере закрытия фаз [PLAN.md](PLAN.md).

**Легенда статусов:** ⬜ не начато · 🟡 в работе · ✅ готово · ⛔ заблокировано

---

## Статус фаз

| Фаза | Описание | Статус | Примечание |
|------|----------|--------|------------|
| — | Спека, план, память, прогресс-лог | ✅ | Создано 2026-07-24 |
| 0 | Валидация LinkedIn API (spike) | ✅ | Live read-only 2026-07-31: форма/scope/timeRange/ссылка/дедуп подтверждены |
| 1 | Каркас проекта, конфиг, БД | ✅ | 24 теста, покрытие src 100% (2026-07-24) |
| 2 | Авторизация и Token Manager | ✅ | OAuth+HTTP+Token Manager, ротация refresh, 52 теста, cov 100% (2026-07-24) |
| 3 | LinkedIn Client | ✅ | Restli-запрос mentions на моках; форма ответа — mock-допущение до Ф0 |
| 4 | Dedup + Notifier | ✅ | Дедуп + Telegram, скраб токена (2026-07-24) |
| 5 | Scheduler + Health | ✅ | Цикл, курсор, служебные/проактивные алерты, 429-исключение |
| 6 | Docker и запуск | ✅ | Dockerfile/compose (валидны), README, smoke-импорт |
| 7 | Тесты, покрытие, ревью | ✅ | 98 тестов, cov 100%; ревью security+python, CRITICAL/HIGH закрыты |

**Текущая веха:** M4 достигнута — код v1 готов и покрыт тестами на моках. Осталась Фаза 0
(валидация живого API формы `organizationalEntityNotifications`) — на стороне заказчика.

---

## Журнал

### 2026-07-24
- Изучено ТЗ «Telegram-бот мониторинга упоминаний LinkedIn».
- Активирован скилл **spec-pilot**; интервью не проводилось — ТЗ уже содержит 11
  зафиксированных решений.
- Созданы: `SPEC.md`, `PLAN.md`, `PROGRESS.md`.
- Заведены файлы памяти проекта (см. `~/.claude/projects/.../memory/`).
- Разделы 12 (вне скоупа) и 13 (Roadmap v2) исходного ТЗ в v1 не прорабатываются
  (решение заказчика).
- Заказчик утвердил спеку → **Фаза 1 выполнена**: каркас репозитория, `src/config.py`
  (загрузка+валидация, fail fast), `src/db.py` (схема SQLite + `poll_cursor`, идемпотентный
  `init_db`), `src/models.py` (`TokenSet`, `Notification`). Настроены `pyproject.toml`,
  `.gitignore`, `.env.example`, `requirements*.txt`, venv `.venv` (Python 3.14.4).
- Тесты: **24 passed**, покрытие `src` **100%** (`pytest --cov`).
- **Фаза 2 выполнена**: изучена проверенная LinkedIn-интеграция в `smm-dashboard` и
  портирована основа — `src/http.py` (таксономия ошибок transient/permanent, вырезание
  секретов из query), `src/oauth.py` (обмен code + refresh, **ротация refresh_token**),
  `src/token_store.py`, `src/token_manager.py` (рефреш по skew), `setup_auth.py`.
  Исправлена версия API: `202506` → `202606` (202506 sunset).
- Тесты: **52 passed**, покрытие `src` **100%**.
- Открытые вопросы 3–5 частично сняты переиспользованием (см. таблицу ниже).
- **Фазы 3–6 выполнены**: `src/linkedin_client.py` (Restli-запрос mentions, ключ дедупа),
  `src/dedup.py`, `src/notifier.py` (форматы + Telegram + скраб токена), `src/scheduler.py`
  (цикл, курсор, служебные/проактивные алерты), `src/cursor.py`, `src/health.py`, `main.py`,
  `Dockerfile`, `docker-compose.yml` (валиден), `.dockerignore`, `README.md`.
- **Фаза 7 (ревью)**: прогнаны `security-reviewer` и `python-reviewer`. Закрыты:
  - CRITICAL — утечка Telegram-токена в логи (httpx INFO писал URL с токеном в обход
    скраба) → `src/logging_setup.py`: понижение httpx до WARNING + фильтр-редактор секретов;
  - CRITICAL — токен утекал через `raise ... from exc` (нескрабленный `__cause__`) → `from None`;
  - HIGH — незащищённые отправки алертов роняли цикл → `_safe_send` (try/except);
  - HIGH — таксономия transient/permanent → введён `RateLimitError` (429 не считается в порог);
  - HIGH — троттл проактивного алерта ставился до отправки → теперь после успеха;
  - MEDIUM/LOW — валидация `LOG_LEVEL`, текст для истёкшего refresh, тип `Mapping` в oauth,
    `round` в epoch-мс.
- Итог: **98 тестов, покрытие `src` 100%**. Известные принятые ограничения:
  троттл проактивного алерта в памяти (после рестарта возможно 1 повтор/сутки);
  `httpx.Client` создаётся на вызов (ок при текущем масштабе).
- **Дальше:** Фаза 0 на живых токенах — сверить форму ответа mentions и при необходимости
  поправить `linkedin_client._parse_element` / ключ дедупа.

---

### 2026-07-31
- Получены боевые ключи (LinkedIn client/secret/access/refresh, Telegram bot/chat, org URN).
- **Фаза 0 (live, read-only) пройдена** по реальной странице компании:
  - member — APPROVED ADMINISTRATOR; scope `rw_organization_admin` работает; access-токен живой.
  - `GET organizationalEntityNotifications?actions=List(SHARE_MENTION)` → 200, 10 реальных
    упоминаний; поля точно как в доке (`notificationId` — отрицательный long, `lastModifiedAt`,
    `sourcePost`=activity, `generatedActivity`=share/ugcPost).
  - `timeRange=(start:..,end:..)` (Restli-tuple) → 200 (наша кодировка верна); плоская форма → 400.
  - Разовый `500 GATEWAY_INTERNAL_ERROR` оказался транзиентным → наш `TransientError`/ретрай.
  - Telegram `getChat` → OK (группа «Linkedin mention chat», chat_id рабочий).
  - Наш парсер распарсил все 10 (числовой id → строка, время из `lastModifiedAt`).
- Правки по итогам: ссылка теперь из `sourcePost` (activity-permalink), `generatedActivity` —
  фолбэк. Тесты: **100 passed, cov 100%**.
- **End-to-end (live) пройден:** `.env` записан, токены засижены в `./data/bot.db`; ссылка
  подтверждена кликом (`sourcePost`/activity открывает пост с упоминанием). Тестовое
  сообщение доставлено в группу «Linkedin mention chat». Один живой цикл: access+refresh
  **ротированы и сохранены** (access→2026-09-29, refresh→2027-07-30), 0 упоминаний за окно
  ~20 мин (без флуда), `cycle.ok`, 0 сбоев. `temp_*.txt` удалены (токены устарели).
- **Осталось:** боевой деплой `docker compose up -d --build` (persistent, по подтверждению).

### 2026-07-31 (позже): жёсткое код-ревью всего проекта + фиксы

Прогнано полное ревью 4 параллельными агентами (python-reviewer, security-reviewer,
code-reviewer, silent-failure-hunter). Вердикт до фиксов: **Block** (1 CRITICAL, 5 HIGH).
Все находки (включая MEDIUM/LOW) исправлены:

- **C1 (CRITICAL)** — исключения вне `BotError` (в т.ч. все ошибки SQLite) обходили
  счётчик сбоев и Telegram-алертинг → `src/db.py:storage_errors()` (sqlite3 → новый
  `StorageError`), широкие `except Exception` в scheduler, best-effort алерт последнего
  рубежа в `main.run_loop`. Ротированный refresh-токен при сбое записи: retry + CAS.
- **H1** — один кривой элемент ответа LinkedIn навсегда блокировал алерты org (raise
  посреди парсинга + прикованный курсор) → пер-элементный skip с WARNING.
- **H2** — SPEC §10 «401/403 → refresh» не был реализован → `AuthError` в `http.py`,
  `TokenManager.force_refresh()`, один реактивный refresh за цикл + повтор запроса.
- **H3** — алерт строго `== порога` (один раз за простой; терялся при недоступном
  Telegram) → `>=` + суточный повтор, метка `last_service_alert_at` ставится только после
  успешной доставки (неудача повторяется), троттлинг refresh-предупреждений персистится
  в БД (переживает рестарт).
- **H4** — затяжной 429 был невидим («ни успех, ни сбой» без потолка) → отдельный
  счётчик `consecutive_rate_limited` + алерт по тому же порогу.
- **H5** — root-контейнер + bind mount + права 644 на `bot.db` с токенами → `USER appuser`
  (UID 10001) в Dockerfile, `chmod 0600`/`mkdir 0700` в `db.py`.
- **MEDIUM**: пагинация mentions (`start`/`count`, PAGE_SIZE=50, предохранитель 20 стр.);
  OAuth CSRF `state` (двухшаговый `setup_auth.py url`/`exchange`, одноразовый state в БД,
  `hmac.compare_digest`); редакция секретов в ТРЕЙСБЕКАХ (`exc_text`) + динамическая
  регистрация access/refresh-токенов (`register_secrets`); CAS-защита от гонки
  `setup_auth` vs бот (`TokenConflictError`, свежая ручная пара побеждает);
  Docker `HEALTHCHECK` (`src/healthcheck.py`, heartbeat `last_cycle_at` ≤ 2 интервалов);
  ретраи Telegram + `retry_after` (SPEC §8.3); mypy чистый (нарровинг в oauth, int() в
  health); зависимости запинены точно; README-команды для Windows исправлены (PowerShell).
- **LOW**: graceful SIGTERM/SIGINT (`threading.Event`); ретеншен дедупа 90 дней (`prune`);
  ротация docker-логов (3×10 МБ); валидации config (пустые сегменты URN, месяц в
  `LINKEDIN_API_VERSION`); логи всех молчаливых фолбэков (TTL токенов, sha1-ключ,
  event_time); убраны мёртвые `params` и `PRAGMA foreign_keys`; `load_config` разбит
  (< 50 строк); Telegram 401 больше не триггерит refresh LinkedIn-токена.
- Схема БД расширена идемпотентными миграциями (`ALTER TABLE bot_health ...`,
  `oauth_state`) — существующая боевая `./data/bot.db` накатывается без потерь.
- Тестовые слепые зоны закрыты: оконная математика `_since` (первый цикл и после
  курсора), граница 15-мин skew, инъекции не-BotError, `main.py`/`setup_auth.py`/
  `healthcheck.py` в покрытии.
- Контрольное ревью фиксов (code-reviewer): вердикт «fixes are sound», 0 CRITICAL/HIGH.
  Два дозакрытых замечания: троттлинг служебных алертов теперь по виду сбоя
  (`last_service_alert_kind`: смена failure↔rate_limit алертится сразу); деривация
  `redirect_uri` из `--redirect-response` сохраняет собственный query приложения.
- Итог: **163 теста, покрытие 98% (src+main+setup_auth), mypy — 0 ошибок**.

### 2026-07-31 (боевой прогон и деплой)

- **`docker compose up -d --build` выполнен**: образ собран на python:3.12-slim (код
  впервые прогнан на боевом интерпретаторе — оговорка «3.14 vs 3.12» снята), контейнер
  под `appuser` (uid 10001), `restart: unless-stopped`.
- **Живая валидация**: два цикла к LinkedIn API — `cycle.ok`; второй тик точно по
  таймеру (+15:00). Миграция боевой `./data/bot.db` накатилась без потерь (все новые
  колонки + `oauth_state`), курсор/health записаны. Новых упоминаний в окне не было —
  в Telegram ничего не отправлялось (корректно).
- **Прогон поймал баг**: healthcheck через второе соединение SQLite нестабилен на bind
  mount Docker Desktop (gRPC-FUSE, `SQLITE_CANTOPEN`). Исправлено: heartbeat-файл
  (`HEARTBEAT_PATH`, по умолчанию `/tmp/bot-heartbeat`) пишется планировщиком каждый
  цикл, `src/healthcheck.py` читает только его mtime — к БД не ходит вовсе.
  4 плановые health-проверки подряд exit=0, статус `healthy` устойчив.
- Итог после прогона: **165 тестов, mypy — 0 ошибок, бот задеплоен и работает**.
- Остаток: реальный размер страницы notifications API сверить, когда придёт всплеск
  упоминаний (>50 за окно); пока пагинация проверена только моками.

## Открытые вопросы / блокеры

| # | Вопрос | Кто снимает | Статус |
|---|--------|-------------|--------|
| 1 | Форма ответа `organizationalEntityNotifications`, поле URN | Фаза 0 | ✅ закрыт (2026-07-31) |
| 2 | Стабильный `notificationId` | Фаза 0 | ✅ есть (числовой long) |
| 3 | Кодировка `timeRange` для finder-а `criteria` | Фаза 0 | ✅ tuple `(start:..,end:..)` |
| 4 | Актуальная `LinkedIn-Version` и scope-ы | Фаза 0 | ✅ `202606` + `rw_organization_admin` |
| 5 | Рабочий URN для ссылки `/feed/update/` | Живой клик | ✅ `sourcePost` подтверждён кликом (2026-07-31) |
| 6 | Живой refresh + ротация refresh-токена | Первый цикл | ✅ прогнан: оба токена ротированы и сохранены |
