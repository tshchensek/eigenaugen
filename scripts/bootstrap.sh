#!/bin/sh
# bootstrap.sh -- one-time install of the eigenaugen toolchain on macOS.
#
# Idempotent: safe to run repeatedly. Every dependency is checked first and
# installed only if missing:
#   * Homebrew
#   * Claude Code CLI (official native installer)
#   * gh, the GitHub CLI (brew)
#   * jq, yq, shellcheck for ./scripts/lintme.sh (brew; macOS 15+ ships jq)
#   * pyenv (brew) + its suggested build deps + shell init in your profile
#   * Python from .python-version (pyenv) + .venv
#   * ruff, pytest (pip into .venv, pinned in requirements.txt)
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
PYENV_DOCS_URL="https://github.com/pyenv/pyenv#b-set-up-your-shell-environment-for-pyenv"
BREW_PREFIXES="/opt/homebrew /usr/local"
# https://github.com/pyenv/pyenv/wiki#suggested-build-environment
PYENV_BUILD_DEPS="openssl@3 readline sqlite xz tcl-tk@8 libb2 zstd zlib pkgconf"
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
  5. Install pyenv + its suggested build deps via brew, and add pyenv
     init to your shell profile.
  6. Install the Python version in .python-version via pyenv.
  7. Create .venv from that Python and install requirements.txt
     (ruff, pytest).
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
# brew_install FORMULA -- install a library formula unless already present.
brew_install() {
  if brew list "$1" >/dev/null 2>&1; then
    log "$1 already installed"
  else
    log "installing $1..."
    brew install "$1"
  fi
}

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

# -- Shell profile -------------------------------------------------------------
LOGIN_SHELL="$(login_shell)"
PROFILE="$(rc_file "$LOGIN_SHELL")"

# ensure_profile_block MARKER TITLE BLOCK -- append BLOCK (sh syntax) under a
# "# TITLE" header unless PROFILE already contains MARKER.
ensure_profile_block() {
  if grep -qF "$1" "$PROFILE" 2>/dev/null; then
    log "$2 already configured in $PROFILE"
    return
  fi
  log "adding $2 to $PROFILE..."
  printf '\n# %s\n%s\n' "$2" "$3" >> "$PROFILE"
  warn "restart your shell (or: source $PROFILE) for $2 to take effect"
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

# -- pyenv + Python ------------------------------------------------------------
# https://github.com/pyenv/pyenv#b-set-up-your-shell-environment-for-pyenv
install_pyenv() {
  ensure_brew_cmd pyenv
  for dep in $PYENV_BUILD_DEPS; do
    brew_install "$dep"
  done

  # The block below is sh syntax; other shells need their own pyenv setup.
  if [ "$(shell_syntax "$LOGIN_SHELL")" != sh ]; then
    warn "pyenv shell init not added for login shell '$LOGIN_SHELL' -- see $PYENV_DOCS_URL"
    return
  fi
  init_shell=""
  case "$LOGIN_SHELL" in
    zsh|bash) init_shell=" $LOGIN_SHELL" ;;
  esac
  ensure_profile_block "pyenv init" "pyenv" "$(cat <<EOF
export PYENV_ROOT="\$HOME/.pyenv"
[ -d "\$PYENV_ROOT/bin" ] && export PATH="\$PYENV_ROOT/bin:\$PATH"
eval "\$(pyenv init -${init_shell})"
EOF
)"
}

setup_python() {
  [ -f "$PYTHON_VERSION_FILE" ] || die "missing $PYTHON_VERSION_FILE"
  py_version="$(tr -d '[:space:]' < "$PYTHON_VERSION_FILE")"

  if pyenv versions --bare | grep -qx "$py_version"; then
    log "python $py_version already installed"
  else
    log "installing python $py_version via pyenv (compiles; takes a few minutes)..."
    pyenv install "$py_version"
  fi
  py_bin="$(pyenv root)/versions/${py_version}/bin/python"

  venv_version="$("$VENV_DIR/bin/python" -c \
    'import platform; print(platform.python_version())' 2>/dev/null || true)"
  if [ "$venv_version" = "$py_version" ]; then
    log "venv already exists (python $py_version)"
  else
    if [ -d "$VENV_DIR" ]; then
      warn "venv is python '${venv_version:-broken}', want $py_version -- recreating"
    fi
    log "creating venv at $VENV_DIR..."
    "$py_bin" -m venv --clear "$VENV_DIR"
  fi
}

# pip skips requirements that are already satisfied, so this only installs
# what is missing or out of pin.
install_python_deps() {
  log "installing python deps from requirements.txt..."
  "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --quiet -r "$REQUIREMENTS"
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
install_pyenv
setup_python
install_python_deps
"$INSTALL_SCRIPT"

echo ""
log "bootstrap complete."
echo ""
echo "  next steps:"
echo "    exec \"\$SHELL\"                     # reload profile (pyenv, ~/.local/bin)"
echo "    eigenaugen review -h               # review a pull request"
echo "    source .venv/bin/activate          # put ruff + pytest on PATH"
echo "    gh auth login                      # if not yet authenticated"
echo "    claude                             # log in on first run"
echo ""
