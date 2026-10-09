"""Per-run scratch directory, removed on exit."""

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

WORKDIR_PREFIX = "eigenaugen-"
SESSION_DIRNAME = "session"
CHECKOUT_DIRNAME = "src"


@dataclass(frozen=True)
class Workspace:
    """Layout of one run's scratch directory (mode 0700, from mkdtemp)."""

    root: Path

    @property
    def session(self) -> Path:
        """The agent's working directory. It holds no repository content,
        so no CLAUDE.md from the PR is auto-loaded as instructions."""
        return self.root / SESSION_DIRNAME

    @property
    def checkout(self) -> Path:
        """Checkout of the PR head commit, granted via --add-dir."""
        return self.root / CHECKOUT_DIRNAME


@contextmanager
def workspace() -> Iterator[Workspace]:
    """Create a unique scratch directory; delete it however the run ends."""
    # Resolved, so every path the agent sees matches what the CLI reports
    # (macOS: /var is a symlink to /private/var).
    root = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX)).resolve()
    try:
        ws = Workspace(root)
        ws.session.mkdir()
        yield ws
    finally:
        shutil.rmtree(root, ignore_errors=True)
