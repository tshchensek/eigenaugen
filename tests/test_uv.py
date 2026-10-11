"""scripts/lib/uv.sh, run against fake uv and curl binaries; no network."""

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
PY_VERSION = "3.14.8"
DOWNLOAD_LINE = f"cpython-{PY_VERSION}-macos-aarch64-none    <download available>"
# Release asset directories, one per architecture uv.sh supports.
ASSETS = ("uv-aarch64-apple-darwin", "uv-x86_64-apple-darwin")
RELEASES_URL = "https://github.com/astral-sh/uv/releases/download/"
SELECT = f'select_uv {PY_VERSION}; printf "uv_bin=%s\\n" "$uv_bin"'


def run(root: Path, body: str, bin_dir: Path) -> subprocess.CompletedProcess:
    """Source the libs as ROOT/scripts/bootstrap.sh would, then run BODY."""
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    script = f'set -eu; . "{LIB}/common.sh"; . "{LIB}/uv.sh"; {body}'
    return subprocess.run(
        ["/bin/sh", "-c", script, str(scripts / "bootstrap.sh")],
        env={"HOME": str(root), "PATH": f"{bin_dir}:{SYSTEM_PATH}"},
        capture_output=True,
        text=True,
        check=False,
    )


def selected(result: subprocess.CompletedProcess) -> str:
    return result.stdout.splitlines()[-1].removeprefix("uv_bin=")


def write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def fake_uv_script(version: str, knows_python: bool) -> str:
    """A uv that reports VERSION and lists PY_VERSION only if KNOWS_PYTHON."""
    listing = f"echo '{DOWNLOAD_LINE}'" if knows_python else ":"
    return f"""#!/bin/sh
case "$1" in
  --version) echo "uv {version} (fake)" ;;
  python) [ "$2" = list ] && {listing} ;;
esac
"""


def fake_curl(bin_dir: Path, tarball: Path | None = None) -> Path:
    """Install a curl that serves TARBALL (or fails); return its call log."""
    log = bin_dir / "curl.log"
    serve = f'cp "{tarball}" "$out"' if tarball else "exit 22"
    write_exe(
        bin_dir / "curl",
        f"""#!/bin/sh
printf '%s\\n' "$@" >> "{log}"
while [ "$#" -gt 0 ]; do
  [ "$1" = -o ] && out="$2"
  shift
done
{serve}
""",
    )
    return log


def release_tarball(path: Path, uv_script: str) -> str:
    """Write a uv release-style tarball for every arch; return its SHA-256."""
    data = uv_script.encode()
    with tarfile.open(path, "w:gz") as tar:
        for asset in ASSETS:
            info = tarfile.TarInfo(f"{asset}/uv")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin_sha256(sha256: str) -> str:
    return f"PINNED_UV_SHA256_AARCH64={sha256}; PINNED_UV_SHA256_X86_64={sha256}; "


def pinned_version(tmp_path: Path) -> str:
    result = run(tmp_path, 'printf %s "$PINNED_UV_VERSION"', tmp_path / "bin")
    return result.stdout


def test_select_uv_keeps_uv_on_path_that_knows_the_python(tmp_path):
    bin_dir = tmp_path / "bin"
    user_uv = write_exe(bin_dir / "uv", fake_uv_script("9.9.9", knows_python=True))
    curl_log = fake_curl(bin_dir)
    result = run(tmp_path, SELECT, bin_dir)
    assert result.returncode == 0, result.stderr
    assert selected(result) == str(user_uv)
    assert not curl_log.exists()
    assert not (tmp_path / ".toolchain").exists()


def test_select_uv_downloads_pinned_uv_when_path_uv_is_too_old(tmp_path):
    bin_dir = tmp_path / "bin"
    old = fake_uv_script("0.0.1", knows_python=False)
    user_uv = write_exe(bin_dir / "uv", old)
    version = pinned_version(tmp_path)
    tarball = tmp_path / "uv.tar.gz"
    sha256 = release_tarball(tarball, fake_uv_script(version, knows_python=True))
    curl_log = fake_curl(bin_dir, tarball)

    result = run(tmp_path, pin_sha256(sha256) + SELECT, bin_dir)
    assert result.returncode == 0, result.stderr
    pinned = tmp_path / ".toolchain/bin/uv"
    assert selected(result) == str(pinned)
    assert os.access(pinned, os.X_OK)
    assert f"cannot install python {PY_VERSION}" in result.stdout
    assert user_uv.read_text() == old
    [url] = [a for a in curl_log.read_text().split() if a.startswith("https://")]
    assert url.startswith(f"{RELEASES_URL}{version}/uv-")
    assert sorted(p.name for p in (tmp_path / ".toolchain").iterdir()) == ["bin"]

    again = run(tmp_path, pin_sha256(sha256) + SELECT, bin_dir)
    assert again.returncode == 0, again.stderr
    assert "already downloaded" in again.stdout
    assert curl_log.read_text().count(RELEASES_URL) == 1


def test_select_uv_without_uv_on_path_uses_pinned_uv(tmp_path):
    bin_dir = tmp_path / "bin"
    curl_log = fake_curl(bin_dir)
    pinned = write_exe(
        tmp_path / ".toolchain/bin/uv",
        fake_uv_script(pinned_version(tmp_path), knows_python=True),
    )
    result = run(tmp_path, SELECT, bin_dir)
    assert result.returncode == 0, result.stderr
    assert selected(result) == str(pinned)
    assert "uv not on PATH" in result.stdout
    assert not curl_log.exists()


def test_install_pinned_uv_rejects_checksum_mismatch(tmp_path):
    bin_dir = tmp_path / "bin"
    tarball = tmp_path / "uv.tar.gz"
    release_tarball(
        tarball, fake_uv_script(pinned_version(tmp_path), knows_python=True)
    )
    fake_curl(bin_dir, tarball)
    result = run(tmp_path, "install_pinned_uv", bin_dir)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    # No uv and no temp dir left behind.
    assert list((tmp_path / ".toolchain").rglob("*")) == [tmp_path / ".toolchain/bin"]


def test_select_uv_dies_when_pinned_uv_cannot_install(tmp_path):
    bin_dir = tmp_path / "bin"
    fake_curl(bin_dir)
    write_exe(
        tmp_path / ".toolchain/bin/uv",
        fake_uv_script(pinned_version(tmp_path), knows_python=False),
    )
    result = run(tmp_path, SELECT, bin_dir)
    assert result.returncode != 0
    assert "bump PINNED_UV_VERSION" in result.stderr


def test_python_install_dir_is_exported_into_toolchain(tmp_path):
    body = "sh -c 'printf %s \"$UV_PYTHON_INSTALL_DIR\"'"
    result = run(tmp_path, body, tmp_path / "bin")
    assert result.stdout == f"{tmp_path}/.toolchain/python"
