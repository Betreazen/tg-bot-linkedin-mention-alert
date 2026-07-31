# Спецификация: LinkedIn Mentions Alert Bot

**Версия спеки:** 1.0
**Дата:** 2026-07-24
**Статус:** черновик на утверждение (перед началом кодирования)
**Источник:** ТЗ «Telegram-бот мониторинга упоминаний LinkedIn» от 24.07.2026

> Эта спека — единый источник правды для разработки v1. Она формализует ТЗ и уточняет
> технические детали. Разделы 12 (вне скоупа) и 13 (Roadmap v2) исходного ТЗ в v1 **не
> прорабатываются** по решению заказчика.

---

## 1. Цель

У страницы компании в LinkedIn несколько администраторов. Раздел «Действия →
Упоминания» персонализирован: разные админы видят разный набор упоминаний, поэтому часть
упоминаний остаётся без реакции команды.

**Решение:** независимый сервис, который через официальный LinkedIn Community Management
API забирает **все** упоминания компании (без привязки к сессии конкретного админа) и шлёт
единый поток алертов со ссылками в общий Telegram-чат.

---

## 2. Область v1

### Входит в v1
- Опрос (polling) LinkedIn по событиям `SHARE_MENTION` для одной или нескольких org URN
  из конфига.
- Дедупликация уже отправленных упоминаний (устойчива к рестарту контейнера).
- Отправка минимального алерта (ссылка + время) в один Telegram group chat.
- Автоматическое обновление `access_token` через `refresh_token`.
- Проактивные и служебные алерты о сбоях (истечение токена, серия неудачных опросов).
- Хранение состояния в SQLite на Docker volume.
- Разовый скрипт первичной авторизации (`setup_auth.py`).

### Границы v1 (сознательно НЕ делаем)
Обогащённые сообщения (автор/текст), webhook/realtime, мониторинг хэштегов, события
`COMMENT`/`LIKE`, интеграция с внешним дашбордом. Поддержка нескольких страниц — только на
уровне конфига (список URN), без параллелизации запросов.

---

## 3. Зафиксированные решения (из интервью)

| # | Решение |
|---|---|
| 1 | LinkedIn Community Management API — новое приложение, approval не блокер (есть опыт) |
| 2 | Полностью отдельный проект, не связан с существующим дашбордом |
| 3 | Нет публичного HTTPS → **polling**, не webhook |
| 4 | Хранение состояния — **SQLite** на Docker volume |
| 5 | Одна LinkedIn-страница сейчас; конфиг — **список** org URN на будущее |
| 6 | Алерты только по событию **`SHARE_MENTION`** (не COMMENT/LIKE) |
| 7 | Формат сообщения — **минимальный**: ссылка + время |
| 8 | Интервал polling — **15 минут** |
| 9 | Язык — **Python 3.12+** |
| 10 | Сбои бота (токен/API) — **алерт в тот же чат** |
| 11 | Получатель — **один** Telegram group `chat_id` |

---

## 4. Архитектура

Один процесс в Docker-контейнере, без внешнего крона/оркестратора. Внутренний цикл
`while True: run(); sleep(POLL_INTERVAL)`.

```
LinkedIn Community Mgmt API
  organizationalEntityNotifications ?actions=List(SHARE_MENTION)
        │  poll раз в 15 мин (Bearer access_token)
        ▼
┌─────────────────────────────────────┐
│ Docker-контейнер (main.py)          │
│   Scheduler (loop/sleep)            │
│        ▼                            │
│   Token Manager  ── refresh token   │
│        ▼                            │
│   LinkedIn Client (GET notifs)      │
│        ▼                            │
│   Dedup layer (SQLite) ◄── volume /data/bot.db
│        ▼                            │
│   Notifier (Telegram sendMessage)   │
│        ▼                            │
│   Health / Error handling           │
└─────────────────────────────────────┘
        ▼
  Telegram group chat (все админы)
```

### Компоненты
- **Scheduler** — простой цикл опроса, интервал `POLL_INTERVAL_MINUTES`.
- **Token Manager** — проверяет срок жизни `access_token` перед каждым циклом; истёк/скоро
  истекает → обновляет через `refresh_token`. Следит за сроком `refresh_token` и шлёт
  проактивный алерт за `REFRESH_TOKEN_WARNING_DAYS` до жёсткого отказа.
- **LinkedIn Client** — обёртка над `GET /rest/organizationalEntityNotifications`;
  отдельный запрос на каждый org URN из списка.
- **Dedup layer** — сверяет идентификатор уведомления с уже отправленными в SQLite.
- **Notifier** — форматирует и шлёт сообщения через Telegram Bot API `sendMessage`.
- **Health / Error handling** — счётчик подряд идущих сбоев; при превышении порога или
  невозможности обновить токен — служебный алерт в тот же чат.

---

## 5. Поток данных (по шагам)

1. Scheduler запускает цикл каждые `POLL_INTERVAL_MINUTES` (15 мин).
2. Token Manager проверяет `access_token_expires_at` в SQLite; при необходимости обновляет
   токен через `refresh_token` grant.
3. Для каждого `org_urn` из конфига — запрос к LinkedIn с фильтром `actions=SHARE_MENTION`
   и `timeRange`, покрывающим период с последнего успешного опроса + оверлап
   `POLL_OVERLAP_MINUTES` (по умолчанию +5 мин) на случай задержек LinkedIn.
4. Каждая полученная запись проверяется по ключу дедупликации в таблице `mentions_sent`.
5. Новые записи → формируется ссылка на пост
   `https://www.linkedin.com/feed/update/{activity_urn}/` → отправка в Telegram → запись в
   SQLite как отправленная (в одной транзакции: сначала фиксируем отправку, затем обновляем
   курсор времени).
6. При успешном цикле сбрасывается счётчик последовательных ошибок, обновляется
   `last_successful_poll`.
7. При ошибке (сеть, 401/403, 5xx) — инкремент счётчика; при достижении
   `FAILURE_ALERT_THRESHOLD` подряд неудач — служебное сообщение в чат.
8. Логирование — в stdout (доступно через `docker logs`).

---

## 6. Модель данных (SQLite)

```sql
CREATE TABLE oauth_token (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    access_token_expires_at TIMESTAMP NOT NULL,
    refresh_token_expires_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE mentions_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT UNIQUE NOT NULL,  -- ключ дедупа (см. §8.4)
    org_urn TEXT NOT NULL,
    source_post_urn TEXT NOT NULL,
    detected_at TIMESTAMP NOT NULL,
    sent_at TIMESTAMP NOT NULL
);

CREATE TABLE bot_health (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_successful_poll TIMESTAMP,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

-- Курсор опроса на каждый org (нижняя граница timeRange следующего цикла).
CREATE TABLE poll_cursor (
    org_urn TEXT PRIMARY KEY,
    last_polled_at TIMESTAMP NOT NULL
);
```

Вставка алерта — `INSERT OR IGNORE` по `notification_id` (защита от дублей между циклами и
при рестарте). Все временные метки хранятся в UTC; в человекочитаемый вид (`TIMEZONE`)
переводятся только при форматировании сообщения.

---

## 7. Конфигурация (`.env`)

```
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_ORG_URNS=urn:li:organization:XXXXX
# список через запятую — задел на несколько страниц:
# LINKEDIN_ORG_URNS=urn:li:organization:XXXXX,urn:li:organization:YYYYY
LINKEDIN_API_VERSION=202606          # заголовок LinkedIn-Version: YYYYMM (202506 sunset)

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

POLL_INTERVAL_MINUTES=15
POLL_OVERLAP_MINUTES=5
FAILURE_ALERT_THRESHOLD=3
REFRESH_TOKEN_WARNING_DAYS=14

DB_PATH=/data/bot.db
TIMEZONE=Europe/Kyiv
LOG_LEVEL=INFO
```

Валидация конфига при старте: обязательные секреты присутствуют, URN-ы валидного формата,
числовые параметры > 0. При отсутствии обязательного значения — падение с понятной ошибкой
(fail fast), без старта цикла.

---

## 8. Интеграции

### 8.1 LinkedIn: авторизация (разовая, вне контейнера)
1. Создать приложение в LinkedIn Developer Portal.
2. Запросить продукт **Community Management API**, заполнить бизнес-данные.
3. Убедиться, что приложению доступны **программные refresh tokens** (иначе ручная
   реавторизация каждые ~60 дней вместо раза в год).
4. Один раз вручную пройти Authorization Code flow под администратором страницы (redirect
   URI можно временно на `localhost`).
5. Полученные `access_token` + `refresh_token` записать в SQLite через `setup_auth.py`.
6. Раз в ~365 дней повторить шаг 4; бот заранее предупредит в Telegram.

**Сроки жизни токенов (проверить в Фазе 0):** `access_token` ≈ 60 дней,
`refresh_token` ≈ 365 дней.

### 8.2 LinkedIn: запрос уведомлений
```
GET https://api.linkedin.com/rest/organizationalEntityNotifications
    ?q=criteria
    &actions=List(SHARE_MENTION)
    &organizationalEntity={URL-encoded org URN}
    &timeRange=(start:{ms},end:{ms})
Headers:
    Authorization: Bearer {access_token}
    LinkedIn-Version: {LINKEDIN_API_VERSION}
    X-Restli-Protocol-Version: 2.0.0
```
Из каждого элемента ответа берётся URN активности (поле уровня `generatedActivity` /
`activity` — точное имя **валидируется в Фазе 0**) → ссылка на пост.

### 8.3 Telegram
`POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage` c `chat_id`,
`text`, `disable_web_page_preview=false`. Ретрай при сетевых ошибках; уважать `retry_after`
при 429.

### 8.4 Ключ дедупликации (риск)
ТЗ предполагает стабильный `notificationId`. **Если** API не возвращает стабильный
идентификатор уведомления, дедуп строится по составному ключу
`sha1(org_urn + '|' + activity_urn + '|' + action)`. Финальное решение — по итогам
Фазы 0; поле `notification_id` в схеме хранит выбранный ключ.

### 8.5 Ротация refresh_token (важно)

LinkedIn при обмене (и authorization code, и refresh grant) **может вернуть новый
`refresh_token`**. Его нужно сохранить немедленно — иначе доступ теряется до ручного
переконсента владельца. Token Manager при рефреше перезаписывает пару токенов целиком.
Подтверждено переиспользованием проверенной интеграции из проекта `smm-dashboard`
(`connectors/linkedin.py`, `connectors/oauth.py`): token endpoint
`https://www.linkedin.com/oauth/v2/accessToken`, форма `grant_type=refresh_token`,
`X-Restli-Protocol-Version: 2.0.0`, актуальная `LinkedIn-Version: 202606`.

---

## 9. Форматы сообщений в Telegram

**Обычный алерт:**
```
🔔 Новое упоминание {Company}
🔗 https://www.linkedin.com/feed/update/urn:li:activity:XXXXXXXXXXXX/
🕒 24.07.2026 15:42
```

**Служебный алерт о сбое:**
```
⚠️ LinkedIn Mentions Bot: проблема
Не удалось обновить токен / 3 неудачных опроса подряд.
Последний успешный опрос: 24.07.2026 14:12
Требуется проверка.
```

**Проактивный алерт об истечении refresh_token:**
```
⏳ LinkedIn Mentions Bot: скоро истекает refresh_token (осталось N дней).
Нужна ручная реавторизация (шаг Authorization Code flow).
```

---

## 10. Обработка ошибок и edge cases

- **429 Rate limit** — отдельный `RateLimitError`; НЕ засчитывается в
  `FAILURE_ALERT_THRESHOLD`, естественный backoff — сам 15-минутный интервал опроса.
- **401/403** — попытка refresh токена; если не удалось — служебный алерт.
- **5xx / сеть** — инкремент счётчика сбоев, ретрай на следующем цикле.
- **Истечение `refresh_token`** — проактивный алерт за `REFRESH_TOKEN_WARNING_DAYS`.
- **Заголовок `LinkedIn-Version`** — вынесен в конфиг, обновляется централизованно.
- **Дубли между циклами** — уникальность `notification_id` + `INSERT OR IGNORE`.
- **Нахлёст временных окон** — оверлап `timeRange` на `POLL_OVERLAP_MINUTES`.
- **Пустой ответ / нет новых упоминаний** — нормальный путь, цикл успешен.
- **Частичный сбой по одному URN** из нескольких — не роняет остальные; ошибка считается
  по URN, курсор этого URN не двигается.

---

## 11. Нефункциональные требования

- **Безопасность:** секреты только в `.env` (в `.gitignore`), токены — в SQLite на volume,
  `.env.example` без значений. Никаких секретов в логах: Telegram-токен лежит в пути URL,
  поэтому httpx понижен до WARNING + фильтр-редактор затирает известные секреты в любой
  строке лога (`src/logging_setup.py`); текст ошибок Telegram скрабится (`notifier._scrub`),
  query из URL вырезается (`http._describe`).
- **Надёжность:** `restart: unless-stopped`; состояние переживает рестарт; курсор и дедуп
  в БД.
- **Наблюдаемость:** структурированные логи в stdout, уровень через `LOG_LEVEL`.
- **Идемпотентность:** повторный запуск не порождает дублей алертов.
- **Код:** маленькие модули (200–400 строк), явная обработка ошибок, без мутаций общего
  состояния, тесты ≥ 80% покрытия.

---

## 12. Критерии готовности (Definition of Done)

1. `setup_auth.py` записывает токены в SQLite из результата Authorization Code flow.
2. Бот в контейнере опрашивает LinkedIn каждые 15 мин и шлёт корректные алерты в Telegram.
3. Дубли не отправляются (проверено между циклами и после рестарта контейнера).
4. `access_token` обновляется автоматически; при близком истечении `refresh_token` приходит
   проактивный алерт.
5. Серия из `FAILURE_ALERT_THRESHOLD` сбоев → служебный алерт.
6. Все секреты вне git; `.env.example` присутствует.
7. Тесты проходят, покрытие ≥ 80%; `docker compose up` поднимает сервис с volume.
8. README описывает разовую авторизацию и запуск.

---

## 13. Открытые вопросы

**Снято переиспользованием `smm-dashboard`** (проверенная на моках CM API интеграция):
- ✅ token endpoint и форма refresh grant; ротация `refresh_token` при обмене (§8.5).
- ✅ актуальная `LinkedIn-Version: 202606` (202506 sunset); заголовки Restli 2.0.
- ✅ конвенция сборки URL Restli 2.0: скобочные параметры не URL-кодируются, URN —
  кодируется (`quote(urn, safe='')`); epoch-миллисекунды для границ времени.

**Остаётся снять в Фазе 0 (нужны живые токены)** — эндпоинт `organizationalEntityNotifications`
в `smm-dashboard` не используется (там статистика), поэтому именно по упоминаниям:
- Точная форма ответа и имя поля с URN активности (`generatedActivity` / `activity`).
- Наличие стабильного `notificationId` (иначе — составной ключ, §8.4).
- Кодировка `actions=List(SHARE_MENTION)` и `timeRange` для finder-а `criteria`.
- Требуемые scope-ы для чтения уведомлений организации.
- Подтверждение доступности программных refresh tokens для приложения.
