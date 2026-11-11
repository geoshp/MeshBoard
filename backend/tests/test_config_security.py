"""Конфигурация, авторизация, маскировка секретов в логах."""

import logging
import time

import pytest

from app import config
from app.auth import Auth
from app.logsetup import RedactFilter, redact, register_secret


def test_password_required(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("MESHBOARD_", "MESHCORE_")):
            monkeypatch.delenv(k, raising=False)
    with pytest.raises(config.ConfigError):
        config.load_settings()
    monkeypatch.setenv("MESHBOARD_PASSWORD", "short")
    with pytest.raises(config.ConfigError):
        config.load_settings()
    monkeypatch.setenv("MESHBOARD_PASSWORD", "long-enough-pass")
    s = config.load_settings()
    assert s.host == "127.0.0.1"  # по умолчанию только localhost


def test_password_file(monkeypatch, tmp_path):
    f = tmp_path / "pw"
    f.write_text("from-file-pass\n")
    monkeypatch.setenv("MESHBOARD_PASSWORD_FILE", str(f))
    monkeypatch.setenv("MESHBOARD_PASSWORD", "ignored-env-pass")
    assert config.load_settings().password == "from-file-pass"


def test_repeater_passwords():
    pw = config.parse_repeater_passwords("# c\nc3=one\nc329=two=with=eq\nRPT Юг = three\n\nbad line\n")
    s = config.Settings(repeater_passwords=pw, repeater_default_password=None)
    assert s.password_for_repeater("c3295e" + "0" * 58, "x") == "two=with=eq"  # длиннее префикс
    assert s.password_for_repeater("c3aa" + "0" * 60, "x") == "one"
    assert s.password_for_repeater("ff" * 32, "rpt юг") == "three"
    assert s.password_for_repeater("ff" * 32, "nobody") is None


def test_auth_tokens_and_rate_limit():
    a = Auth("secret-pass", "k", session_hours=1)
    assert a.check_password("secret-pass") and not a.check_password("nope")
    tok = a.issue()
    assert a.verify(tok)
    assert not a.verify(tok[:-2] + "xx")
    assert not Auth("secret-pass", "other-key").verify(tok)
    assert not a.verify(None) and not a.verify("garbage")
    expired = Auth("p", "k", session_hours=0)
    t = expired.issue()
    time.sleep(1.1)
    assert not expired.verify(t)
    for _ in range(10):
        a.register_failure("1.2.3.4")
    assert a.rate_limited("1.2.3.4") and not a.rate_limited("5.6.7.8")


def test_redaction_plain_and_hex():
    register_secret("Sup3rSecretPw")
    assert "Sup3rSecretPw" not in redact("login with Sup3rSecretPw ok")
    hexed = "Sup3rSecretPw".encode().hex()
    assert hexed not in redact(f"Sending raw data: 1a{hexed}")
    rec = logging.LogRecord("meshcore", logging.INFO, __file__, 1, "pkt %s", (f"1a{hexed}",), None)
    RedactFilter().filter(rec)
    assert hexed not in rec.getMessage()


def test_meshcore_logger_never_debug():
    from app.logsetup import setup_logging
    setup_logging("DEBUG")
    assert logging.getLogger("meshcore").getEffectiveLevel() >= logging.INFO
    setup_logging("INFO")
