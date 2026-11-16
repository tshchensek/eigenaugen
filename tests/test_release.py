"""scripts/release.sh against throwaway git repos and a fake gh; no network."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_gitrepo import IDENTITY, commit_all, git

ROOT = Path(__file__).resolve().parent.parent
# What release.sh needs of the repo it lives in.
SCRIPT_FILES = (Path("scripts/release.sh"), Path("scripts/lib/common.sh"))
SHIPPED_FILE = Path("eigenaugen/app.py")
DATE = "2026-11-15"
BASE_VERSION = "0.1.0"


def changelog(*versions: str) -> str:
    """A CHANGELOG.md with one section per version, newest first."""
    sections = "".join(f"## {v} - {DATE}\n\n- change {v}\n\n" for v in versions)
    return f"# Changelog\nIntro.\n\n{sections}"


def write(repo: Path, path: str | Path, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def set_version(repo: Path, *versions: str) -> None:
    """VERSION = the first of VERSIONS; CHANGELOG.md has them all."""
    write(repo, "VERSION", f"{versions[0]}\n")
    write(repo, "CHANGELOG.md", changelog(*versions))


def release(repo: Path, *args: str, bin_dir: Path | None = None):
    env = dict(os.environ)
    if bin_dir:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [str(repo / "scripts" / "release.sh"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def init_repo(tmp_path: Path, monkeypatch, version: str | None) -> Path:
    """A repo with release.sh and a shipped file on main, VERSION (unless
    None) at VERSION; HEAD on a topic branch."""
    for key, value in IDENTITY.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    for path in SCRIPT_FILES:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, repo / path)
    write(repo, SHIPPED_FILE, "x = 1\n")
    write(repo, "README.md", "readme\n")
    if version is not None:
        set_version(repo, version)
    git(repo, "init", "--quiet", "--initial-branch=main")
    commit_all(repo, "base")
    git(repo, "switch", "--quiet", "-c", "topic")
    return repo


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    return init_repo(tmp_path, monkeypatch, BASE_VERSION)


def test_shipped_change_needs_a_bump(repo):
    write(repo, SHIPPED_FILE, "x = 2\n")
    commit_all(repo, "change")
    result = release(repo, "check", "main")
    assert result.returncode == 1
    assert str(SHIPPED_FILE) in result.stderr
    assert "must be greater than main's (0.1.0)" in result.stderr


def test_bumped_shipped_change_passes(repo):
    write(repo, SHIPPED_FILE, "x = 2\n")
    set_version(repo, "0.2.0", BASE_VERSION)
    commit_all(repo, "change")
    result = release(repo, "check", "main")
    assert result.returncode == 0, result.stderr
    assert "0.1.0 -> 0.2.0" in result.stdout


@pytest.mark.parametrize(
    "path", ["README.md", "tests/test_x.py", ".github/workflows/ci.yml", "LICENSE"]
)
def test_unshipped_changes_need_no_bump(repo, path):
    write(repo, path, "changed\n")
    commit_all(repo, "docs")
    result = release(repo, "check", "main")
    assert result.returncode == 0, result.stderr
    assert "needs no bump" in result.stdout


@pytest.mark.parametrize(
    ("old", "new", "ok"),
    [
        ("0.1.9", "0.1.10", True),
        ("0.9.0", "0.10.0", True),
        ("0.1.0", "1.0.0", True),
        ("1.2.3", "1.2.3", False),
        ("0.2.0", "0.1.9", False),
        ("1.0.0", "0.9.9", False),
    ],
)
def test_versions_compare_numerically(tmp_path, monkeypatch, old, new, ok):
    repo = init_repo(tmp_path, monkeypatch, old)
    write(repo, SHIPPED_FILE, "x = 2\n")
    set_version(repo, new, old)
    commit_all(repo, "change")
    assert (release(repo, "check", "main").returncode == 0) is ok


@pytest.mark.parametrize("version", ["1.2", "01.2.3", "1.2.3-rc.1", "v1.2.3", ""])
def test_version_must_be_major_minor_patch(repo, version):
    write(repo, "VERSION", f"{version}\n")
    result = release(repo, "check", "main")
    assert result.returncode == 1
    assert "VERSION must be MAJOR.MINOR.PATCH" in result.stderr


def test_version_file_must_be_one_line(repo):
    write(repo, "VERSION", "0.2.0\n0.3.0\n")
    assert release(repo, "check", "main").returncode == 1


@pytest.mark.parametrize(
    "text",
    [
        changelog(BASE_VERSION),  # top section is the old version
        changelog(BASE_VERSION, "0.2.0"),  # new section not on top
        "# Changelog\n\n## 0.2.0\n\n- no date\n",
        "# Changelog\n\n## 0.2.0 - 15.11.2026\n\n- bad date\n",
        "# Changelog\n\n## 0.2.0 - 2026-11-15\n\n\n## 0.1.0 - 2026-11-14\n- x\n",
        "# Changelog\n",
    ],
)
def test_changelog_must_open_with_the_version(repo, text):
    write(repo, SHIPPED_FILE, "x = 2\n")
    write(repo, "VERSION", "0.2.0\n")
    write(repo, "CHANGELOG.md", text)
    result = release(repo, "check", "main")
    assert result.returncode == 1
    assert '"## 0.2.0 - YYYY-MM-DD" section' in result.stderr


def test_uncommitted_and_untracked_files_count(repo):
    write(repo, "eigenaugen/new.py", "y = 1\n")
    result = release(repo, "check", "main")
    assert result.returncode == 1
    assert "eigenaugen/new.py" in result.stderr


def test_moving_a_file_out_of_a_shipped_path_counts(repo):
    (repo / "tests").mkdir()
    git(repo, "mv", str(SHIPPED_FILE), "tests/app.py")
    commit_all(repo, "move")
    result = release(repo, "check", "main")
    assert result.returncode == 1
    assert str(SHIPPED_FILE) in result.stderr


def test_only_changes_since_the_merge_base_count(repo):
    git(repo, "switch", "--quiet", "main")
    write(repo, SHIPPED_FILE, "x = 2\n")
    set_version(repo, "0.2.0", BASE_VERSION)
    commit_all(repo, "main moves on")
    git(repo, "switch", "--quiet", "topic")
    write(repo, "README.md", "docs\n")
    commit_all(repo, "docs")
    assert release(repo, "check", "main").returncode == 0


def test_base_without_version_file_accepts_any_version(tmp_path, monkeypatch):
    repo = init_repo(tmp_path, monkeypatch, None)
    write(repo, SHIPPED_FILE, "x = 2\n")
    set_version(repo, BASE_VERSION)
    commit_all(repo, "add versions")
    result = release(repo, "check", "main")
    assert result.returncode == 0, result.stderr
    assert "main has no VERSION" in result.stdout


def test_check_needs_a_commit(repo):
    result = release(repo, "check", "no-such-branch")
    assert result.returncode == 1
    assert "not a commit: no-such-branch" in result.stderr


def test_notes_print_the_top_section_body(repo):
    write(repo, "CHANGELOG.md", f"# C\n\n## 0.1.0 - {DATE}\n\n\nA\n\nB\n\n\n")
    result = release(repo, "notes")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "A\n\nB\n"


def fake_gh(bin_dir: Path, found: int) -> tuple[Path, Path]:
    """A gh whose tag lookup finds FOUND tags; return its call and stdin logs."""
    calls, stdin = bin_dir / "gh.calls", bin_dir / "gh.stdin"
    write(
        bin_dir,
        "gh",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{calls}"
case "$1" in
  api) echo {found} ;;
  release) cat > "{stdin}" ;;
esac
""",
    )
    (bin_dir / "gh").chmod(0o755)
    return calls, stdin


def test_publish_creates_tag_and_release(repo, tmp_path):
    calls, stdin = fake_gh(tmp_path / "bin", found=0)
    result = release(repo, "publish", bin_dir=tmp_path / "bin")
    assert result.returncode == 0, result.stderr
    sha = git(repo, "rev-parse", "HEAD")
    api, create = calls.read_text().splitlines()
    assert api.startswith("api repos/{owner}/{repo}/git/matching-refs/tags/v0.1.0 ")
    assert create == (
        f"release create v0.1.0 --target {sha} --title v0.1.0 --notes-file -"
    )
    assert stdin.read_text() == "- change 0.1.0\n"


def test_publish_skips_an_existing_tag(repo, tmp_path):
    calls, stdin = fake_gh(tmp_path / "bin", found=1)
    result = release(repo, "publish", bin_dir=tmp_path / "bin")
    assert result.returncode == 0, result.stderr
    assert "v0.1.0 exists" in result.stdout
    assert len(calls.read_text().splitlines()) == 1
    assert not stdin.exists()


def test_repo_changelog_opens_with_its_version():
    result = subprocess.run(
        [str(ROOT / "scripts" / "release.sh"), "notes"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
