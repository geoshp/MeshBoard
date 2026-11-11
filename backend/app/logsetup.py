"""Логирование с маскировкой секретов.

* Значения паролей/ключей регистрируются в RedactFilter и заменяются на *** в любых записях.
* Логгер библиотеки meshcore никогда не опускается ниже INFO: на DEBUG она пишет сырые кадры,
  в которых есть пароль логина в репитер.
* Тексты личных сообщений в лог не пишутся (только служебная информация).
"""

from __future__ import annotations

import logging
import sys

_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 3:
        _SECRETS.add(value)
        # пароль в hex-виде (так библиотека печатает кадры)
        _SECRETS.add(value.encode("utf-8").hex())


def redact(text: str) -> str:
    for s in sorted(_SECRETS, key=len, reverse=True):
        if s in text:
            text = text.replace(s, "***")
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _SECRETS:
            try:
                msg = record.getMessage()
            except Exception:  # noqa: BLE001
                return True
            red = redact(msg)
            if red != msg:
                record.msg = red
                record.args = None
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    mc = logging.getLogger("meshcore")
    mc.setLevel(max(logging.INFO, getattr(logging, level, logging.INFO)))
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
