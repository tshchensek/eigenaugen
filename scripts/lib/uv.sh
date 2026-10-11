#!/bin/sh
# uv.sh -- pick the uv bootstrap.sh runs, without changing the user's uv.
# Source it after common.sh; do not execute.
#
# The uv on PATH is used if it can install the Python in .python-version.
# If it is missing or too old (uv only knows the Pythons released before it
# was built), a pinned uv is downloaded into TOOLCHAIN_DIR instead. Either
# way, the Pythons uv installs go to TOOLCHAIN_DIR, not the user's uv dirs.
#
# https://docs.astral.sh/uv/reference/cli/#uv-python-list
# https://docs.astral.sh/uv/reference/environment/#uv_python_install_dir

# Bootstrap-owned uv and Pythons; gitignored. rm -rf it and .venv to undo.
TOOLCHAIN_DIR="$PROJECT_ROOT/.toolchain"
PINNED_UV="$TOOLCHAIN_DIR/bin/uv"
# Bump when .python-version moves past the Pythons this uv knows; select_uv
# dies with a hint when it does.
PINNED_UV_VERSION="0.13.0"
PINNED_UV_URL="https://github.com/astral-sh/uv/releases/download/$PINNED_UV_VERSION"
# SHA-256 of uv-<arch>-apple-darwin.tar.gz for PINNED_UV_VERSION. Pinned here
# because a checksum fetched beside the tarball proves it intact, not
# genuine. On a bump, copy them from the release's .sha256 files after
# `gh attestation verify <tarball> -R astral-sh/uv` passes.
PINNED_UV_SHA256_AARCH64="a9c1b29002cf3c83f07fa9cd8a887a3be0107d90e23189721221e7257db8e3d6"
PINNED_UV_SHA256_X86_64="5f44dcbde809b632f47c36fadb241cb4d6f9af71d0c8f172b5d2026d3dde742c"
# Read by every uv call. In the user's ~/.local/share/uv/python, installing a
# new patch would relink the user's own venvs on that minor version to it.
UV_PYTHON_INSTALL_DIR="$TOOLCHAIN_DIR/python"
export UV_PYTHON_INSTALL_DIR

# uv_can_install UV VERSION -- succeed if the uv binary UV runs and can
# download Python VERSION for this Mac. A uv too old to know VERSION prints
# nothing here; `uv python install` would fail with "No download found".
uv_can_install() {
  [ -n "$("$1" python list --only-downloads --all-versions "$2" 2>/dev/null)" ]
}

# install_pinned_uv -- download uv PINNED_UV_VERSION to PINNED_UV unless it
# is already there; dies on a checksum mismatch. Sets an EXIT trap while
# downloading so a failure leaves no temp dir behind.
install_pinned_uv() {
  case "$("$PINNED_UV" --version 2>/dev/null)" in
    "uv $PINNED_UV_VERSION "*)
      log "uv $PINNED_UV_VERSION already downloaded ($PINNED_UV)"
      return
      ;;
  esac
  case "$(uname -m)" in
    arm64)
      _uv_asset="uv-aarch64-apple-darwin"
      _uv_sha256="$PINNED_UV_SHA256_AARCH64"
      ;;
    x86_64)
      _uv_asset="uv-x86_64-apple-darwin"
      _uv_sha256="$PINNED_UV_SHA256_X86_64"
      ;;
    *) die "no uv build for this architecture: $(uname -m)" ;;
  esac
  log "downloading uv $PINNED_UV_VERSION to $PINNED_UV..."
  mkdir -p "$(dirname "$PINNED_UV")"
  # Unpack beside the target, then rename: never a half-written uv.
  _uv_tmp="$(mktemp -d "$TOOLCHAIN_DIR/.tmp.XXXXXX")"
  trap 'rm -rf "$_uv_tmp"' EXIT
  _uv_tarball="$_uv_tmp/$_uv_asset.tar.gz"
  curl -fsSL -o "$_uv_tarball" "$PINNED_UV_URL/$_uv_asset.tar.gz"
  _uv_got="$(shasum -a 256 "$_uv_tarball" | cut -d ' ' -f 1)"
  [ "$_uv_got" = "$_uv_sha256" ] \
    || die "uv download checksum mismatch: got $_uv_got, want $_uv_sha256"
  tar -xzf "$_uv_tarball" -C "$_uv_tmp" "$_uv_asset/uv"
  mv -f "$_uv_tmp/$_uv_asset/uv" "$PINNED_UV"
  rm -rf "$_uv_tmp"
  trap - EXIT
}

# select_uv VERSION -- set uv_bin to a uv that can install Python VERSION:
# the uv on PATH if it can, else the pinned uv. Never installs, upgrades or
# replaces the uv on PATH.
select_uv() {
  if has_cmd uv; then
    uv_bin="$(command -v uv)"
    if uv_can_install "$uv_bin" "$1"; then
      log "uv already installed ($uv_bin)"
      return
    fi
    _uv_version="$("$uv_bin" --version 2>/dev/null | cut -d ' ' -f 2)"
    warn "uv ${_uv_version:-?} ($uv_bin) cannot install python $1 --" \
      "leaving it as is, using uv $PINNED_UV_VERSION in $TOOLCHAIN_DIR"
  else
    log "uv not on PATH -- using uv $PINNED_UV_VERSION in $TOOLCHAIN_DIR"
  fi
  install_pinned_uv
  uv_bin="$PINNED_UV"
  uv_can_install "$uv_bin" "$1" \
    || die "uv $PINNED_UV_VERSION cannot install python $1 --" \
      "bump PINNED_UV_VERSION in scripts/lib/uv.sh"
}
