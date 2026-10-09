"""Exception types reported to the user as one-line errors."""

import shlex
from collections.abc import Sequence

# Leading argv items shown in a failed-command message; later items can be
# long (prompts, JSON) or noisy.
ARGV_PREVIEW_LEN = 3


class EigenaugenError(Exception):
    """A failure the user can act on; printed without a traceback."""


class CommandError(EigenaugenError):
    """An external command exited non-zero or could not be started."""

    def __init__(
        self, argv: Sequence[str], returncode: int | None, stderr: str
    ) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr.strip()
        preview = shlex.join(self.argv[:ARGV_PREVIEW_LEN])
        status = "could not start" if returncode is None else f"exit {returncode}"
        detail = self.stderr or "no error output"
        super().__init__(f"`{preview}` failed ({status}): {detail}")
