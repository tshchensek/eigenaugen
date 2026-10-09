"""Run external commands; failures raise instead of returning codes."""

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from eigenaugen.src.errors import CommandError, EigenaugenError

ENCODING = "utf-8"
# Undecodable bytes (binary diffs, odd file names) become U+FFFD instead of
# aborting the run.
DECODE_ERRORS = "replace"


def require(*commands: str) -> None:
    """Raise unless every command is on PATH."""
    missing = [cmd for cmd in commands if shutil.which(cmd) is None]
    if missing:
        raise EigenaugenError(
            f"not on PATH: {', '.join(missing)} -- run ./scripts/bootstrap.sh"
        )


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> str:
    """Run ARGV and return its stdout; raise CommandError on failure.

    Without INPUT_TEXT, stdin is /dev/null so nothing can block on a prompt.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            input=input_text,
            stdin=subprocess.DEVNULL if input_text is None else None,
            capture_output=True,
            text=True,
            encoding=ENCODING,
            errors=DECODE_ERRORS,
            check=False,
        )
    except OSError as exc:
        raise CommandError(argv, None, str(exc)) from exc
    if completed.returncode != 0:
        raise CommandError(argv, completed.returncode, completed.stderr)
    return completed.stdout
