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
DATA_DIRNAME = "data"
REPOS_DIRNAME = "repos"


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

    @property
    def data(self) -> Path:
        """Review context, diffs, and the coverage record. Read by the MCP
        server; outside the agent's file tools."""
        return self.root / DATA_DIRNAME

    @property
    def repos(self) -> Path:
        """Clones of other repositories with context PRs; no checkout."""
        return self.root / REPOS_DIRNAME

    def repo_clone(self, host: str, owner: str, repo: str) -> Path:
        return self.repos / host / owner / repo


@contextmanager
def workspace() -> Iterator[Workspace]:
    """Create a unique scratch directory; delete it however the run ends."""
    # Resolved, so every path the agent sees matches what the CLI reports
    # (macOS: /var is a symlink to /private/var).
    root = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX)).resolve()
    try:
        ws = Workspace(root)
        for directory in (ws.session, ws.data, ws.repos):
            directory.mkdir()
        yield ws
    finally:
        shutil.rmtree(root, ignore_errors=True)
