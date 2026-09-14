FROM python:3.12-slim

# Не под root: в /data лежат OAuth-токены (см. src/db.py — файл БД ужимается до 0600)
RUN useradd --uid 10001 --create-home appuser

WORKDIR /app

# Сначала зависимости — кэш слоёв не инвалидируется при правках кода
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY src ./src
COPY main.py setup_auth.py ./

# Небуферизованный stdout → логи сразу видны в `docker logs`
ENV PYTHONUNBUFFERED=1

USER appuser

# Жив ли цикл: heartbeat в bot_health не старше 2 интервалов опроса
HEALTHCHECK --interval=5m --timeout=15s --start-period=2m \
    CMD ["python", "-m", "src.healthcheck"]

CMD ["python", "main.py"]
