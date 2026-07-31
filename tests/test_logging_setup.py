from __future__ import annotations

import logging
import sys

from src import logging_setup
from src.config import load_config
from src.logging_setup import SecretRedactingFilter, configure_logging, register_secrets


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 0, message, None, None)


def test_filter_redacts_known_secrets():
    filt = SecretRedactingFilter(["SECRET", "CID"])
    record = _record("url=https://x/botSECRET/send id=CID")
    assert filt.filter(record) is True
    assert "SECRET" not in record.getMessage()
    assert "CID" not in record.getMessage()
    assert "***" in record.getMessage()


def test_filter_ignores_empty_secrets():
    filt = SecretRedactingFilter(["", None])  # type: ignore[list-item]
    record = _record("nothing to hide")
    assert filt.filter(record) is True
    assert record.getMessage() == "nothing to hide"


def test_configure_logging_caps_httpx_logger(valid_env):
    configure_logging(load_config(valid_env))
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_filter_scrubs_traceback_text():
    """Секрет в сообщении исключения не должен утекать через log.exception."""
    filt = SecretRedactingFilter(["SUPER_SECRET"])
    try:
        raise RuntimeError("boom https://x/botSUPER_SECRET/send")
    except RuntimeError:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 0, "cycle.unexpected_error", None, sys.exc_info()
        )
    assert filt.filter(record) is True
    formatted = logging.Formatter().format(record)  # рендерит и трейсбек
    assert "SUPER_SECRET" not in formatted
    assert "***" in formatted


def test_register_secrets_extends_live_filter(valid_env):
    """Ротирующиеся токены из SQLite добавляются в редакцию на лету."""
    configure_logging(load_config(valid_env))
    register_secrets("RUNTIME_TOKEN_123", "")  # пустые игнорируются
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 0, "token=RUNTIME_TOKEN_123", None, None
    )
    handler = logging.getLogger().handlers[0]
    for filt in handler.filters:
        filt.filter(record)
    assert "RUNTIME_TOKEN_123" not in record.getMessage()


def test_register_secrets_noop_before_configure(monkeypatch):
    monkeypatch.setattr(logging_setup, "_redactor", None)
    register_secrets("whatever")  # не должно падать
