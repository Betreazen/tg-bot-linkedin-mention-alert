from __future__ import annotations

import pytest

from src.config import Config, ConfigError, load_config


def test_valid_env_applies_defaults(valid_env):
    cfg = load_config(valid_env)
    assert isinstance(cfg, Config)
    assert cfg.linkedin_org_urns == ("urn:li:organization:123",)
    assert cfg.poll_interval_minutes == 15
    assert cfg.poll_overlap_minutes == 5
    assert cfg.failure_alert_threshold == 3
    assert cfg.refresh_token_warning_days == 14
    assert cfg.linkedin_api_version == "202606"
    assert cfg.db_path == "/data/bot.db"
    assert cfg.timezone == "Europe/Kyiv"
    assert cfg.log_level == "INFO"


def test_multiple_org_urns_parsed_and_trimmed(valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = " urn:li:organization:1 , urn:li:organization:2 "
    cfg = load_config(valid_env)
    assert cfg.linkedin_org_urns == ("urn:li:organization:1", "urn:li:organization:2")


_REQUIRED_KEYS = (
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_ORG_URNS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


@pytest.mark.parametrize("missing", _REQUIRED_KEYS)
def test_missing_required_raises_with_key(valid_env, missing):
    valid_env.pop(missing, None)
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert missing in str(exc.value)


def test_blank_required_raises(valid_env):
    valid_env["TELEGRAM_CHAT_ID"] = "   "
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "TELEGRAM_CHAT_ID" in str(exc.value)


def test_invalid_numeric_raises(valid_env):
    valid_env["POLL_INTERVAL_MINUTES"] = "abc"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "POLL_INTERVAL_MINUTES" in str(exc.value)


def test_zero_overlap_allowed_but_zero_interval_rejected(valid_env):
    valid_env["POLL_OVERLAP_MINUTES"] = "0"
    assert load_config(valid_env).poll_overlap_minutes == 0

    valid_env["POLL_INTERVAL_MINUTES"] = "0"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "POLL_INTERVAL_MINUTES" in str(exc.value)


def test_negative_int_rejected(valid_env):
    valid_env["FAILURE_ALERT_THRESHOLD"] = "-1"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "FAILURE_ALERT_THRESHOLD" in str(exc.value)


def test_invalid_org_urn_prefix_rejected(valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = "urn:li:person:999"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "urn:li:organization:" in str(exc.value)


def test_invalid_timezone_rejected(valid_env):
    valid_env["TIMEZONE"] = "Nowhere/Novoid"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "TIMEZONE" in str(exc.value)


@pytest.mark.parametrize("bad", ["2025", "202600", "202699", "abc123"])
def test_invalid_api_version_rejected(valid_env, bad):
    valid_env["LINKEDIN_API_VERSION"] = bad
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "LINKEDIN_API_VERSION" in str(exc.value)


def test_empty_org_urn_segment_rejected(valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = "urn:li:organization:1,,urn:li:organization:2"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "пустой элемент" in str(exc.value)


def test_trailing_comma_in_org_urns_rejected(valid_env):
    valid_env["LINKEDIN_ORG_URNS"] = "urn:li:organization:1,"
    with pytest.raises(ConfigError):
        load_config(valid_env)


def test_heartbeat_path_default_and_override(valid_env):
    assert load_config(valid_env).heartbeat_path == valid_env["HEARTBEAT_PATH"]
    valid_env.pop("HEARTBEAT_PATH")
    assert load_config(valid_env).heartbeat_path == "/tmp/bot-heartbeat"


def test_log_level_uppercased(valid_env):
    valid_env["LOG_LEVEL"] = "debug"
    assert load_config(valid_env).log_level == "DEBUG"


def test_invalid_log_level_rejected(valid_env):
    valid_env["LOG_LEVEL"] = "FOOBAR"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    assert "LOG_LEVEL" in str(exc.value)


def test_multiple_errors_collected(valid_env):
    valid_env.pop("LINKEDIN_CLIENT_ID")
    valid_env["POLL_INTERVAL_MINUTES"] = "-3"
    with pytest.raises(ConfigError) as exc:
        load_config(valid_env)
    msg = str(exc.value)
    assert "LINKEDIN_CLIENT_ID" in msg
    assert "POLL_INTERVAL_MINUTES" in msg
