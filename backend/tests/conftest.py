import time

import pytest

from app.config import Settings


def make_settings(tmp_path, **kw) -> Settings:
    s = Settings()
    s.mock = True
    s.mock_speed = 20.0
    s.mock_autonomous = False
    s.mock_history = False
    s.password = "test-password-123"
    s.secret_key = "test-secret"
    s.data_dir = str(tmp_path)
    s.self_poll_sec = 3600
    s.static_dir = str(tmp_path / "nostatic")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def wait_until(fn, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    raise AssertionError("condition not met in time")
