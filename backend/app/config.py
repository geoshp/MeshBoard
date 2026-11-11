"""Конфигурация из переменных окружения.

Любую секретную переменную можно задать через файл: VAR_FILE=/run/secrets/... (Docker secrets).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    """Значение переменной; если задан NAME_FILE — читается из файла."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"не удалось прочитать {name}_FILE: {e.strerror}") from e
    value = os.environ.get(name)
    if value is None:
        return default
    return value


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = _env(name)
    if v is None or v.strip() == "":
        return default
    try:
        return int(v)
    except ValueError as e:
        raise ConfigError(f"{name} должно быть целым числом") from e


def _list(name: str) -> list[str]:
    v = _env(name) or ""
    return [x.strip().lower() for x in re.split(r"[,\s]+", v) if x.strip()]


def parse_repeater_passwords(text: str) -> dict[str, str]:
    """Формат: по одной записи в строке `ключ=пароль`, где ключ — префикс публичного ключа (hex)
    или имя репитера. Пустые строки и строки с # игнорируются. Разделитель — первый '='."""
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, pwd = line.split("=", 1)
        key = key.strip()
        if key:
            result[key.lower()] = pwd.strip()
    return result


@dataclass
class Settings:
    # подключение
    connection: str = "tcp"  # serial | tcp
    serial_port: str = "/dev/ttyUSB0"
    serial_baudrate: int = 115200
    tcp_host: str = "127.0.0.1"
    tcp_port: int = 5000
    mock: bool = False
    mock_speed: float = 1.0  # множитель частоты событий эмулятора

    # http
    host: str = "127.0.0.1"
    port: int = 8080
    password: str = ""
    secret_key: str = ""
    session_hours: int = 24 * 7
    cookie_secure: bool = False

    # данные
    data_dir: str = "./data"
    retention_messages_days: int = 90
    retention_metrics_days: int = 30

    # опрос
    self_poll_sec: int = 60
    repeater_poll_min: int = 15
    own_repeaters: list[str] = field(default_factory=list)
    repeater_passwords: dict[str, str] = field(default_factory=dict)
    repeater_default_password: str | None = None

    # карта
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "&copy; OpenStreetMap contributors"

    log_level: str = "INFO"
    static_dir: str = ""

    @property
    def db_path(self) -> str:
        return str(Path(self.data_dir) / "meshboard.db")

    def password_for_repeater(self, public_key: str, name: str) -> str | None:
        """Ищет пароль: по самому длинному совпавшему префиксу ключа, затем по имени, затем общий."""
        pk = public_key.lower()
        best: tuple[int, str] | None = None
        for key, pwd in self.repeater_passwords.items():
            if re.fullmatch(r"[0-9a-f]{2,64}", key) and pk.startswith(key):
                if best is None or len(key) > best[0]:
                    best = (len(key), pwd)
        if best:
            return best[1]
        if name and name.lower() in self.repeater_passwords:
            return self.repeater_passwords[name.lower()]
        return self.repeater_default_password


def load_settings() -> Settings:
    s = Settings()
    s.mock = _bool("MESHCORE_MOCK")
    s.connection = (_env("MESHCORE_CONNECTION", "tcp") or "tcp").strip().lower()
    if s.connection not in ("serial", "tcp"):
        raise ConfigError("MESHCORE_CONNECTION должно быть serial или tcp")
    s.serial_port = _env("MESHCORE_SERIAL_PORT", s.serial_port) or s.serial_port
    s.serial_baudrate = _int("MESHCORE_SERIAL_BAUDRATE", s.serial_baudrate)
    s.tcp_host = _env("MESHCORE_TCP_HOST", s.tcp_host) or s.tcp_host
    s.tcp_port = _int("MESHCORE_TCP_PORT", s.tcp_port)
    try:
        s.mock_speed = float(_env("MESHCORE_MOCK_SPEED", "1") or "1")
    except ValueError as e:
        raise ConfigError("MESHCORE_MOCK_SPEED должно быть числом") from e

    s.host = _env("MESHBOARD_HOST", s.host) or s.host
    s.port = _int("MESHBOARD_PORT", s.port)
    s.password = _env("MESHBOARD_PASSWORD", "") or ""
    s.secret_key = _env("MESHBOARD_SECRET_KEY", "") or ""
    s.session_hours = _int("MESHBOARD_SESSION_HOURS", s.session_hours)
    s.cookie_secure = _bool("MESHBOARD_COOKIE_SECURE")

    s.data_dir = _env("MESHBOARD_DATA_DIR", s.data_dir) or s.data_dir
    s.retention_messages_days = _int("MESHBOARD_RETENTION_MESSAGES_DAYS", s.retention_messages_days)
    s.retention_metrics_days = _int("MESHBOARD_RETENTION_METRICS_DAYS", s.retention_metrics_days)

    s.self_poll_sec = max(10, _int("MESHBOARD_SELF_POLL_SEC", s.self_poll_sec))
    s.repeater_poll_min = max(1, _int("MESHBOARD_REPEATER_POLL_MIN", s.repeater_poll_min))
    s.own_repeaters = _list("MESHBOARD_OWN_REPEATERS")
    s.repeater_passwords = parse_repeater_passwords(_env("MESHBOARD_REPEATER_PASSWORDS", "") or "")
    s.repeater_default_password = _env("MESHBOARD_REPEATER_DEFAULT_PASSWORD")

    s.tile_url = _env("MESHBOARD_TILE_URL", s.tile_url) or s.tile_url
    s.tile_attribution = _env("MESHBOARD_TILE_ATTRIBUTION", s.tile_attribution) or s.tile_attribution
    s.log_level = (_env("MESHBOARD_LOG_LEVEL", "INFO") or "INFO").upper()
    s.static_dir = _env("MESHBOARD_STATIC_DIR", "") or ""

    if not s.password:
        raise ConfigError(
            "MESHBOARD_PASSWORD (или MESHBOARD_PASSWORD_FILE) не задан: авторизация обязательна"
        )
    if len(s.password) < 8:
        raise ConfigError("MESHBOARD_PASSWORD должен быть не короче 8 символов")
    return s
