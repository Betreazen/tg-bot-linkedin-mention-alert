# Deployment and operations

Clone https://github.com/Betreazen/tg-bot-linkedin-mention-alert.git and check out a reviewed commit.
Prepare the SQLite directory with `mkdir -p data && sudo chown 10001:10001 data && chmod 700 data`. Complete OAuth setup as described in README before starting polling.
Copy `.env.example` to `.env`, replace placeholders and restrict access (`chmod 600 .env`).
Never commit environment files, databases, OAuth tokens, service account keys or logs.
Keep production configuration and persistent data on the server.

## Build and run

```sh
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

Docker installs `requirements.lock` with hashes. `requirements.txt` lists direct
dependencies. When changing it, regenerate the lock and run the tests:

```sh
uv pip compile requirements.txt --python-platform x86_64-unknown-linux-gnu --python-version 3.11 --generate-hashes --no-header --no-annotate -o requirements.lock
```

Use the same Compose project name on every deployment to preserve volume names.
The bot limit is 400 MiB RAM and 800 MiB RAM plus swap. PostgreSQL/Redis have
separate limits. `MALLOC_ARENA_MAX=2` and the existing PostgreSQL cache settings
are retained. Swap must exist on the host; these limits do not force all bot pages
into swap. Do not change host memory policy as part of a bot deployment.

## Updates and rollback

Record the current Git commit and image ID, back up persistent data, and build
before replacing the running bot. Update one project at a time. Verify container
status, logs and memory after startup. Do not run two pollers with the same token.
For rollback, restore the previous commit and saved image, retaining the same
environment and volumes. Review database migrations before rolling code back.
Never run `docker compose down -v` against production.

## Tests

Install `requirements.lock` and `requirements-dev.txt` in an isolated environment.
Run `python -m pytest`. Tests use dummy credentials and mocked Telegram calls.
