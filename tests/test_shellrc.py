"""scripts/lib/shellrc.sh and scripts/install.sh, run against a temp HOME."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LIB = SCRIPTS / "lib"
INSTALL = SCRIPTS / "install.sh"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
LINE_SH = 'export PATH="$HOME/.local/bin:$PATH"'
LINE_FISH = (
    'contains -- "$HOME/.local/bin" $PATH; or set -gx PATH "$HOME/.local/bin" $PATH'
)
LINE_CSH = 'setenv PATH "$HOME/.local/bin:$PATH"'


def clean_env(home: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(home), "PATH": SYSTEM_PATH, **extra}


def lib(home: Path, *call: str, **extra: str) -> subprocess.CompletedProcess:
    """Source the libraries and run one function call in a clean env."""
    script = f'. "{LIB}/common.sh"; . "{LIB}/shellrc.sh"; "$@"'
    return subprocess.run(
        ["/bin/sh", "-c", script, "sh", *call],
        env=clean_env(home, **extra),
        capture_output=True,
        text=True,
        check=True,
    )


def out(home: Path, *call: str, **extra: str) -> str:
    return lib(home, *call, **extra).stdout.strip()


def touch(home: Path, *names: str) -> None:
    for name in names:
        (home / name).touch()


@pytest.mark.parametrize(
    "existing,expected",
    [
        ((), ".bash_profile"),
        ((".profile",), ".profile"),
        ((".bash_login", ".profile"), ".bash_login"),
        ((".bash_profile", ".profile"), ".bash_profile"),
    ],
)
def test_rc_file_bash_follows_login_lookup_order(tmp_path, existing, expected):
    touch(tmp_path, *existing)
    assert out(tmp_path, "rc_file", "bash") == str(tmp_path / expected)


@pytest.mark.parametrize(
    "existing,expected", [((), ".tcshrc"), ((".cshrc",), ".cshrc")]
)
def test_rc_file_tcsh_falls_back_to_cshrc(tmp_path, existing, expected):
    touch(tmp_path, *existing)
    assert out(tmp_path, "rc_file", "tcsh") == str(tmp_path / expected)


def test_rc_file_other_shells(tmp_path):
    assert out(tmp_path, "rc_file", "zsh") == f"{tmp_path}/.zshrc"
    assert out(tmp_path, "rc_file", "zsh", ZDOTDIR="/z") == "/z/.zshrc"
    assert out(tmp_path, "rc_file", "fish") == f"{tmp_path}/.config/fish/config.fish"
    assert (
        out(tmp_path, "rc_file", "fish", XDG_CONFIG_HOME="/x") == "/x/fish/config.fish"
    )
    for shell in ("sh", "dash", "ksh", "mksh"):
        assert out(tmp_path, "rc_file", shell) == f"{tmp_path}/.profile"
    assert out(tmp_path, "rc_file", "csh") == f"{tmp_path}/.cshrc"
    assert out(tmp_path, "rc_file", "nu") == ""


def test_path_line_and_home_relative(tmp_path):
    rel = out(tmp_path, "home_relative", f"{tmp_path}/.local/bin")
    assert rel == "$HOME/.local/bin"
    assert out(tmp_path, "home_relative", "/opt/bin") == "/opt/bin"
    assert out(tmp_path, "path_line", "zsh", rel) == LINE_SH
    assert out(tmp_path, "path_line", "fish", rel) == LINE_FISH
    assert out(tmp_path, "path_line", "tcsh", rel) == LINE_CSH


@pytest.mark.parametrize(
    "shell,args,source",
    [
        ("sh", ["-c"], "."),
        ("bash", ["--norc", "--noprofile", "-c"], "."),
        ("zsh", ["-f", "-c"], "."),
        ("ksh", ["-c"], "."),
        ("dash", ["-c"], "."),
        ("csh", ["-f", "-c"], "source"),
        ("tcsh", ["-f", "-c"], "source"),
        ("fish", ["--no-config", "-c"], "source"),
    ],
)
def test_written_line_works_in_its_shell(tmp_path, shell, args, source):
    binary = shutil.which(shell)
    if binary is None:
        pytest.skip(f"{shell} not installed")
    rc = tmp_path / "rc"
    rc.write_text(out(tmp_path, "path_line", shell, "$HOME/.local/bin") + "\n")
    result = subprocess.run(
        [binary, *args, f"{source} {rc}; echo $PATH"],
        env=clean_env(tmp_path),
        capture_output=True,
        text=True,
        check=True,
    )
    # fish prints PATH as a space-separated list, the others colon-separated.
    first = result.stdout.split()[0].split(":")[0]
    assert first == f"{tmp_path}/.local/bin"


def test_ensure_on_path_appends_once(tmp_path):
    bin_dir = f"{tmp_path}/.local/bin"
    first = lib(tmp_path, "ensure_on_path", bin_dir, "fish")
    rc = tmp_path / ".config/fish/config.fish"
    assert rc.read_text().count(LINE_FISH) == 1
    assert "adding" in first.stdout
    second = lib(tmp_path, "ensure_on_path", bin_dir, "fish")
    assert rc.read_text().count(LINE_FISH) == 1
    assert "already added" in second.stdout


def test_ensure_on_path_skips_dir_already_on_path(tmp_path):
    bin_dir = f"{tmp_path}/.local/bin"
    result = lib(
        tmp_path,
        "ensure_on_path",
        bin_dir,
        "zsh",
        EIGENAUGEN_INHERITED_PATH=f"/usr/bin:{bin_dir}/",
    )
    assert "already on PATH" in result.stdout
    assert not (tmp_path / ".zshrc").exists()


def test_ensure_on_path_unsupported_shell_only_warns(tmp_path):
    result = lib(tmp_path, "ensure_on_path", f"{tmp_path}/.local/bin", "nu")
    assert "not supported" in result.stdout
    assert list(tmp_path.iterdir()) == []


def run_install(home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(INSTALL)],
        env=clean_env(home),
        capture_output=True,
        text=True,
        check=False,
    )


def startup_files(home: Path) -> list[Path]:
    return [
        p
        for p in home.rglob("*")
        if p.is_file() and ".local" not in p.parts and ".local/bin" in p.read_text()
    ]


def test_install_writes_shim_and_path_once(tmp_path):
    assert run_install(tmp_path).returncode == 0
    shim = tmp_path / ".local/bin/eigenaugen"
    assert shim.stat().st_mode & stat.S_IXUSR
    assert f"exec '{SCRIPTS}/eigenaugen.sh' \"$@\"" in shim.read_text()
    [rc] = startup_files(tmp_path)
    before = rc.read_text()

    again = run_install(tmp_path)
    assert again.returncode == 0
    assert "already installed" in again.stdout
    assert rc.read_text() == before


def test_install_refuses_to_replace_foreign_file(tmp_path):
    shim = tmp_path / ".local/bin/eigenaugen"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\necho mine\n")
    result = run_install(tmp_path)
    assert result.returncode != 0
    assert "not an eigenaugen shim" in result.stderr
    assert shim.read_text() == "#!/bin/sh\necho mine\n"


def test_install_rewrites_stale_shim(tmp_path):
    assert run_install(tmp_path).returncode == 0
    shim = tmp_path / ".local/bin/eigenaugen"
    lines = shim.read_text().splitlines()
    shim.write_text("\n".join([*lines[:2], "exec '/moved/away' \"$@\""]) + "\n")
    result = run_install(tmp_path)
    assert result.returncode == 0
    assert "installing" in result.stdout
    assert f"'{SCRIPTS}/eigenaugen.sh'" in shim.read_text()
    assert os.access(shim, os.X_OK)
