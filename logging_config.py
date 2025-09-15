# -*- coding: utf-8 -*-
import os
import logging
import logging.config
from pathlib import Path

LOG_DIR_DEFAULT = "/opt/Ebot/logs"

def _ensure_dir(path: str) -> None:
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def setup_logging(log_dir: str | None = None, level: str = "INFO") -> None:
    """
    Инициализирует единый логгер с ротацией по размеру.
    - info.log: уровень INFO и выше
    - error.log: уровень ERROR и выше
    """
    d = log_dir or os.getenv("EBOT_LOG_DIR", LOG_DIR_DEFAULT)
    _ensure_dir(d)

    info_path = os.path.join(d, "info.log")
    err_path = os.path.join(d, "error.log")

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "info_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "INFO",
                "formatter": "standard",
                "filename": info_path,
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "standard",
                "filename": err_path,
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
            },
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "standard",
            },
        },
        "root": {
            "level": level,
            "handlers": ["info_file", "error_file", "console"],
        },
    }
    try:
        logging.config.dictConfig(config)
    except Exception:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))


