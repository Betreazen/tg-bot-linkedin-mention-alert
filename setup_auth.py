"""Разовая первичная авторизация LinkedIn (SPEC §8.1). Запускается ВРУЧНУЮ.

Двухшаговый Authorization Code flow с защитой CSRF state (RFC 6749 §10.12):

    1. python setup_auth.py url --redirect-uri <URI> --scope "<SCOPES>"
       → печатает ссылку авторизации (state сохраняется в SQLite), открыть в браузере
         под админом страницы и подтвердить доступ.
    2. python setup_auth.py exchange --redirect-response "<ПОЛНЫЙ URL из адресной строки>"
       → сверяет state, меняет code на пару токенов и пишет их в SQLite.

ВАЖНО: перед exchange остановите работающий бот (docker compose down) — иначе
параллельный рефреш может конфликтовать с записью свежих токенов.
"""

from __future__ import annotations

import argparse
import hmac
import secrets
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

from src import oauth, token_store
from src.config import Config, load_config
from src.db import connect, init_db
from src.http import make_client
from src.logging_setup import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    config = load_config()
    configure_logging(config)
    args = _parse_args(argv)

    conn = connect(config.db_path)
    try:
        init_db(conn)
        if args.command == "url":
            return _cmd_url(conn, config, args)
        return _cmd_exchange(conn, config, args)
    finally:
        conn.close()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Первичная авторизация LinkedIn")
    sub = parser.add_subparsers(dest="command", required=True)

    url = sub.add_parser("url", help="сгенерировать ссылку авторизации (шаг 1)")
    url.add_argument("--redirect-uri", required=True, help="redirect_uri приложения")
    url.add_argument(
        "--scope", required=True,
        help="scope из Developer Portal приложения (продукт Community Management API)",
    )

    exchange = sub.add_parser("exchange", help="обменять code на токены (шаг 2)")
    exchange.add_argument(
        "--redirect-response",
        help="ПОЛНЫЙ URL из адресной строки после redirect (содержит code и state)",
    )
    exchange.add_argument("--code", help="authorization code (если не задан --redirect-response)")
    exchange.add_argument("--state", help="state из redirect (если не задан --redirect-response)")
    exchange.add_argument("--redirect-uri", help="redirect_uri (если не задан --redirect-response)")
    return parser.parse_args(argv)


def _cmd_url(conn: sqlite3.Connection, config: Config, args: argparse.Namespace) -> int:
    state = secrets.token_urlsafe(24)
    token_store.save_auth_state(conn, state, datetime.now(UTC))
    url = oauth.build_authorization_url(
        client_id=config.linkedin_client_id,
        redirect_uri=args.redirect_uri,
        scope=args.scope,
        state=state,
    )
    print("Откройте в браузере (под админом страницы):")
    print(f"  {url}")
    print("После подтверждения скопируйте ПОЛНЫЙ URL из адресной строки и выполните:")
    print('  python setup_auth.py exchange --redirect-response "<URL>"')
    return 0


def _cmd_exchange(conn: sqlite3.Connection, config: Config, args: argparse.Namespace) -> int:
    code, state, redirect_uri = _extract_exchange_params(args)
    stored = token_store.pop_auth_state(conn)
    if stored is None:
        raise SystemExit(
            "Нет сохранённого state: сначала выполните `setup_auth.py url ...` (шаг 1)."
        )
    if state is None or not hmac.compare_digest(stored, state):
        raise SystemExit(
            "state не совпадает — код из чужого/устаревшего запроса не принимается. "
            "Повторите шаги 1–2."
        )
    with make_client() as client:
        token = oauth.exchange_code(
            client,
            code=code,
            redirect_uri=redirect_uri,
            client_id=config.linkedin_client_id,
            client_secret=config.linkedin_client_secret,
        )
    token_store.save_token(conn, token)
    print("Токены сохранены.")
    print("  access истекает: ", token.access_token_expires_at.isoformat())
    print("  refresh истекает:", token.refresh_token_expires_at.isoformat())
    return 0


def _extract_exchange_params(args: argparse.Namespace) -> tuple[str, str | None, str]:
    """(code, state, redirect_uri) из --redirect-response либо из явных флагов."""
    if args.redirect_response:
        parts = urlsplit(args.redirect_response)
        query = parse_qs(parts.query)
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [None])[0]
        if not code:
            raise SystemExit("в --redirect-response нет параметра code")
        # redirect_uri для обмена = исходный URI приложения: без code/state, добавленных
        # LinkedIn, но С собственным query, если он был зарегистрирован (?app=... и т.п.)
        own_query = urlencode(
            [(k, v) for k, vs in query.items() if k not in ("code", "state") for v in vs]
        )
        redirect_uri = urlunsplit((parts.scheme, parts.netloc, parts.path, own_query, ""))
        return code, state, redirect_uri
    if not args.code or not args.redirect_uri:
        raise SystemExit("нужен либо --redirect-response, либо --code и --redirect-uri")
    return args.code, args.state, args.redirect_uri


if __name__ == "__main__":
    raise SystemExit(main())
