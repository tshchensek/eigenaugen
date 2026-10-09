#!/bin/sh
# bootstrap.sh -- one-time install of the eigenaugen toolchain on macOS.
#
# Idempotent: safe to run repeatedly. Every dependency is checked first and
# installed only if missing:
#   * Homebrew
#   * Claude Code CLI (official native installer)
#   * gh, the GitHub CLI (brew)
#   * jq, yq, shellcheck for ./scripts/lintme.sh (brew; macOS 15+ ships jq)
#   * uv, the Python version and package manager (brew)
#   * Python from .python-version (uv-managed, prebuilt) + .venv
#   * ruff, pytest (uv pip into .venv, pinned in requirements.txt)
#   * the eigenaugen command in ~/.local/bin, which goes on PATH
set -eu

# shellcheck source=SCRIPTDIR/lib/common.sh
. "$(dirname "$0")/lib/common.sh"
# Sourced before anything changes PATH: it records the inherited PATH.
# shellcheck source=SCRIPTDIR/lib/shellrc.sh
. "$(dirname "$0")/lib/shellrc.sh"

# -- Constants -----------------------------------------------------------------
HOMEBREW_INSTALL_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
CLAUDE_INSTALL_URL="https://claude.ai/install.sh"
INSTALL_SCRIPT="$PROJECT_ROOT/scripts/install.sh"
BREW_PREFIXES="/opt/homebrew /usr/local"
PYTHON_VERSION_FILE="$PROJECT_ROOT/.python-version"
REQUIREMENTS="$PROJECT_ROOT/requirements.txt"
VENV_DIR="$PROJECT_ROOT/.venv"
# CLIs that requirements.txt must provide inside .venv.
VENV_TOOLS="ruff pytest"
# Linters used by lintme.sh; brew formula names match their CLI names.
LINT_TOOLS="jq yq shellcheck"

# -- Help ----------------------------------------------------------------------
help() {
  cat <<EOF
bootstrap.sh -- one-time install of the eigenaugen toolchain on macOS

USAGE
  ./scripts/bootstrap.sh [-h]

OPTIONS
  -h  Show this help and exit

WHAT IT DOES
  1. Install Homebrew if absent.
  2. Install the Claude Code CLI via $CLAUDE_INSTALL_URL
     (into $LOCAL_BIN).
  3. Install gh (GitHub CLI) via brew.
  4. Install $LINT_TOOLS via brew, unless already on PATH (macOS 15+
     ships /usr/bin/jq).
  5. Install uv via brew.
  6. Install the Python version in .python-version via uv (prebuilt;
     nothing compiles).
  7. Create .venv from that Python and install requirements.txt
     (ruff, pytest) via uv.
  8. Run scripts/install.sh: install the eigenaugen command into
     $LOCAL_BIN and, if $LOCAL_BIN is not on PATH, add it in the
     login shell's startup file.

NOTES
  * Re-running is safe -- every step checks existing state first.
  * Startup file: found from the login shell (dscl, else \$SHELL):
    zsh ~/.zshrc, bash the first of ~/.bash_profile ~/.bash_login
    ~/.profile, fish ~/.config/fish/config.fish, tcsh ~/.tcshrc or
    ~/.cshrc, csh ~/.cshrc, other POSIX shells ~/.profile.
EOF
  exit 0
}

# -- Parse flags ---------------------------------------------------------------
while getopts ":h" opt; do
  case "$opt" in
    h) help ;;
    \?) die "unknown option: -${OPTARG}" ;;
  esac
done

[ "$(uname)" = "Darwin" ] || die "this script is macOS-only"

# -- Helpers -------------------------------------------------------------------
# ensure_brew_cmd CMD [FORMULA] -- install FORMULA (default: CMD) unless CMD
# is already on PATH, regardless of how it got there.
ensure_brew_cmd() {
  if has_cmd "$1"; then
    log "$1 already installed ($(command -v "$1"))"
  else
    log "installing ${2:-$1}..."
    brew install "${2:-$1}"
  fi
}

# -- Homebrew ------------------------------------------------------------------
install_homebrew() {
  if has_cmd brew; then
    log "homebrew already installed"
    return
  fi
  log "installing homebrew..."
  /bin/bash -c "$(curl -fsSL "$HOMEBREW_INSTALL_URL")"
}

setup_brew_env() {
  for prefix in $BREW_PREFIXES; do
    if [ -x "${prefix}/bin/brew" ]; then
      eval "$("${prefix}/bin/brew" shellenv)"
      return
    fi
  done
  die "homebrew not found after install"
}

# -- Claude Code CLI -----------------------------------------------------------
# https://code.claude.com/docs/en/setup
install_claude() {
  export PATH="$LOCAL_BIN:$PATH"
  if has_cmd claude; then
    log "claude already installed ($(command -v claude))"
  else
    log "installing claude via official installer ($CLAUDE_INSTALL_URL)..."
    curl -fsSL "$CLAUDE_INSTALL_URL" | bash
    has_cmd claude || die "claude install completed but the binary is not on PATH"
  fi
}

# -- gh ------------------------------------------------------------------------
# https://cli.github.com/
install_gh() {
  ensure_brew_cmd gh
  if gh auth status >/dev/null 2>&1; then
    log "gh already authenticated"
  else
    warn "gh is not authenticated -- run: gh auth login"
  fi
}

# -- Lint tools ----------------------------------------------------------------
# https://formulae.brew.sh/formula/jq
# https://formulae.brew.sh/formula/yq
# https://formulae.brew.sh/formula/shellcheck
install_lint_tools() {
  for tool in $LINT_TOOLS; do
    ensure_brew_cmd "$tool"
  done
}

# -- uv + Python ---------------------------------------------------------------
# https://docs.astral.sh/uv/
# https://docs.astral.sh/uv/concepts/python-versions/
install_uv() {
  ensure_brew_cmd uv
}

# managed_python VERSION -- print the path of the uv-managed Python VERSION;
# fails if uv has not installed it. --system skips .venv interpreters.
managed_python() {
  uv python find --managed-python --system "$1" 2>/dev/null
}

# base_prefix PYTHON -- print the real path of the installation PYTHON runs
# on (for a venv: the Python it was created from). Prints nothing if PYTHON
# does not run.
base_prefix() {
  "$1" -c 'import os, sys; print(os.path.realpath(sys.base_prefix))' \
    2>/dev/null || true
}

setup_python() {
  [ -f "$PYTHON_VERSION_FILE" ] || die "missing $PYTHON_VERSION_FILE"
  py_version="$(tr -d '[:space:]' < "$PYTHON_VERSION_FILE")"

  if py_bin="$(managed_python "$py_version")"; then
    log "python $py_version already installed ($py_bin)"
  else
    log "installing python $py_version via uv..."
    # --no-bin: do not add python3.X to ~/.local/bin; only .venv uses it.
    uv python install --no-bin "$py_version"
    py_bin="$(managed_python "$py_version")" \
      || die "uv installed python $py_version but cannot find it"
  fi

  # Comparing installations, not version strings, also replaces a venv built
  # on the same version from another source (e.g. pyenv).
  want_base="$(base_prefix "$py_bin")"
  [ -n "$want_base" ] || die "python $py_version does not run: $py_bin"
  venv_base="$(base_prefix "$VENV_DIR/bin/python")"
  if [ "$venv_base" = "$want_base" ]; then
    log "venv already exists (python $py_version)"
  else
    if [ -d "$VENV_DIR" ]; then
      warn "venv runs on '${venv_base:-broken}', want $want_base -- recreating"
    fi
    log "creating venv at $VENV_DIR..."
    # A patch-version request pins the venv to that patch; uv would otherwise
    # link it to the minor version's latest installed patch.
    uv venv --quiet --clear --managed-python --python "$py_version" "$VENV_DIR"
  fi
}

# uv skips requirements that are already satisfied, so this only installs
# what is missing or out of pin.
install_python_deps() {
  log "installing python deps from requirements.txt..."
  uv pip install --quiet --python "$VENV_DIR" -r "$REQUIREMENTS"
  for tool in $VENV_TOOLS; do
    "$VENV_DIR/bin/$tool" --version >/dev/null 2>&1 \
      || die "$tool missing from .venv -- add it to requirements.txt"
    log "$tool ready ($("$VENV_DIR/bin/$tool" --version 2>&1 | head -n 1))"
  done
}

# -- Main ----------------------------------------------------------------------
install_homebrew
setup_brew_env

install_claude
install_gh
install_lint_tools
install_uv
setup_python
install_python_deps
"$INSTALL_SCRIPT"

echo ""
log "bootstrap complete."
echo ""
echo "  next steps:"
echo "    exec \"\$SHELL\"                     # reload profile (~/.local/bin)"
echo "    eigenaugen review -h               # review a pull request"
echo "    source .venv/bin/activate          # put ruff + pytest on PATH"
echo "    gh auth login                      # if not yet authenticated"
echo "    claude                             # log in on first run"
echo ""
