from __future__ import annotations

import httpx
import pytest

import setup_auth
from src import token_store
from src.db import connect, init_db
from src.http import make_client

TOKEN_PAYLOAD = {
    "access_token": "AT",
    "expires_in": 5184000,
    "refresh_token": "RT",
    "refresh_token_expires_in": 31536000,
}


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LINKEDIN_ORG_URNS", "urn:li:organization:1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    db_path = str(tmp_path / "auth.db")
    monkeypatch.setenv("DB_PATH", db_path)
    return db_path


def _stored_state(db_path: str) -> str | None:
    conn = connect(db_path)
    try:
        init_db(conn)
        row = conn.execute("SELECT state FROM oauth_state WHERE id = 1").fetchone()
        return row["state"] if row else None
    finally:
        conn.close()


def _mock_token_endpoint(monkeypatch):
    def handler(request):
        assert "accessToken" in str(request.url)
        return httpx.Response(200, json=TOKEN_PAYLOAD)

    monkeypatch.setattr(
        setup_auth, "make_client", lambda: make_client(httpx.MockTransport(handler))
    )


def test_url_command_prints_link_and_stores_state(auth_env, capsys):
    assert setup_auth.main(
        ["url", "--redirect-uri", "http://localhost:8080/cb", "--scope", "rw_organization_admin"]
    ) == 0
    out = capsys.readouterr().out
    state = _stored_state(auth_env)
    assert state is not None
    assert "client_id=cid" in out
    assert f"state={state}" in out
    assert "rw_organization_admin" in out


def test_exchange_with_redirect_response_verifies_state(auth_env, capsys, monkeypatch):
    setup_auth.main(["url", "--redirect-uri", "http://localhost:8080/cb", "--scope", "s"])
    state = _stored_state(auth_env)
    _mock_token_endpoint(monkeypatch)

    code = setup_auth.main(
        ["exchange", "--redirect-response",
         f"http://localhost:8080/cb?code=AUTHCODE&state={state}"]
    )
    assert code == 0
    assert "Токены сохранены" in capsys.readouterr().out

    conn = connect(auth_env)
    try:
        init_db(conn)
        token = token_store.load_token(conn)
    finally:
        conn.close()
    assert token is not None and token.access_token == "AT"


def test_exchange_rejects_wrong_state(auth_env, monkeypatch):
    setup_auth.main(["url", "--redirect-uri", "http://localhost:8080/cb", "--scope", "s"])
    _mock_token_endpoint(monkeypatch)
    with pytest.raises(SystemExit, match="state не совпадает"):
        setup_auth.main(
            ["exchange", "--redirect-response",
             "http://localhost:8080/cb?code=AUTHCODE&state=FORGED"]
        )


def test_exchange_requires_prior_url_step(auth_env):
    with pytest.raises(SystemExit, match="Нет сохранённого state"):
        setup_auth.main(
            ["exchange", "--code", "AUTHCODE", "--state", "x",
             "--redirect-uri", "http://localhost:8080/cb"]
        )


def test_exchange_explicit_flags_with_matching_state(auth_env, monkeypatch):
    setup_auth.main(["url", "--redirect-uri", "http://localhost:8080/cb", "--scope", "s"])
    state = _stored_state(auth_env)
    _mock_token_endpoint(monkeypatch)
    assert setup_auth.main(
        ["exchange", "--code", "AUTHCODE", "--state", state,
         "--redirect-uri", "http://localhost:8080/cb"]
    ) == 0


def test_exchange_requires_code_or_response(auth_env):
    with pytest.raises(SystemExit, match="redirect-response"):
        setup_auth.main(["exchange", "--state", "x"])


def test_exchange_preserves_own_query_in_redirect_uri(auth_env, monkeypatch):
    """?app=bot в зарегистрированном redirect_uri не теряется при обмене."""
    setup_auth.main(["url", "--redirect-uri", "http://localhost:8080/cb?app=bot", "--scope", "s"])
    state = _stored_state(auth_env)
    sent: dict[str, str] = {}

    def handler(request):
        sent["body"] = request.content.decode()
        return httpx.Response(200, json=TOKEN_PAYLOAD)

    monkeypatch.setattr(
        setup_auth, "make_client", lambda: make_client(httpx.MockTransport(handler))
    )
    setup_auth.main(
        ["exchange", "--redirect-response",
         f"http://localhost:8080/cb?app=bot&code=AUTHCODE&state={state}"]
    )
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fcb%3Fapp%3Dbot" in sent["body"]


def test_exchange_rejects_response_without_code(auth_env):
    with pytest.raises(SystemExit, match="нет параметра code"):
        setup_auth.main(
            ["exchange", "--redirect-response", "http://localhost:8080/cb?state=x"]
        )


def test_state_is_single_use(auth_env, monkeypatch):
    setup_auth.main(["url", "--redirect-uri", "http://localhost:8080/cb", "--scope", "s"])
    state = _stored_state(auth_env)
    _mock_token_endpoint(monkeypatch)
    setup_auth.main(
        ["exchange", "--redirect-response",
         f"http://localhost:8080/cb?code=AUTHCODE&state={state}"]
    )
    # повторный exchange с тем же state — отказ (state одноразовый)
    with pytest.raises(SystemExit, match="Нет сохранённого state"):
        setup_auth.main(
            ["exchange", "--redirect-response",
             f"http://localhost:8080/cb?code=AUTHCODE&state={state}"]
        )
