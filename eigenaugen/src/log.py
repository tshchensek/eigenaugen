"""Leveled log lines on stderr, formatted like scripts/lib/common.sh."""

import sys
from enum import StrEnum

from eigenaugen.src.text import sanitize


class Level(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


COLORS = {
    Level.INFO: "\033[0;32m",
    Level.WARN: "\033[1;33m",
    Level.ERROR: "\033[0;31m",
}
RESET = "\033[0m"
# Width of the widest label plus one space, so messages line up.
LABEL_WIDTH = len(f"[{Level.ERROR}]") + 1


def _emit(level: Level, message: str) -> None:
    label = f"[{level}]"
    pad = " " * (LABEL_WIDTH - len(label))
    if sys.stderr.isatty():
        label = f"{COLORS[level]}{label}{RESET}"
    print(f"{label}{pad}{sanitize(message)}", file=sys.stderr, flush=True)


def info(message: str) -> None:
    _emit(Level.INFO, message)


def warn(message: str) -> None:
    _emit(Level.WARN, message)


def error(message: str) -> None:
    _emit(Level.ERROR, message)
