"""统一日志。"""
from __future__ import annotations

import logging
import sys

_ROOT = "free_proxy"
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def setup_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger(_ROOT)
    if root.handlers:
        return root

    level = level.upper()
    if level not in _LEVELS:
        level = "INFO"
    root.setLevel(level)
    root.propagate = False

    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    )
    root.addHandler(h)
    return root


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(_ROOT + ("." + name if name else ""))