#!/bin/sh
# shellrc.sh -- find the login shell and its startup file, and put a
# directory on PATH there. Source it after common.sh; do not execute.
#
# Supported login shells, grouped by startup-file syntax:
#   sh:   zsh bash sh dash ksh ksh93 mksh oksh
#   fish: fish
#   csh:  tcsh csh
# Other shells get a warning instead of an edit.

# PATH as the user's shell set it, before any script changed it. Exported
# so child scripts (bootstrap.sh -> install.sh) check the original.
: "${EIGENAUGEN_INHERITED_PATH:=$PATH}"
export EIGENAUGEN_INHERITED_PATH

SH_SYNTAX_SHELLS="zsh bash sh dash ksh ksh93 mksh oksh"
CSH_SYNTAX_SHELLS="tcsh csh"
FISH_SHELL="fish"
RC_COMMENT="# Added by eigenaugen (scripts/install.sh)"

# login_shell -- print the basename of the user's login shell. The directory
# service record is what new terminals start, even right after chsh; $SHELL
# is the fallback.
login_shell() {
  _shell_path="$(dscl . -read "/Users/$(id -un)" UserShell 2>/dev/null \
    | sed -n 's/^UserShell: //p')"
  basename "${_shell_path:-${SHELL:-sh}}"
}

# shell_syntax SHELL -- print SHELL's startup-file syntax: sh, fish, or csh.
# Prints nothing for unsupported shells.
shell_syntax() {
  case " $SH_SYNTAX_SHELLS " in *" $1 "*) echo sh; return ;; esac
  case " $CSH_SYNTAX_SHELLS " in *" $1 "*) echo csh; return ;; esac
  if [ "$1" = "$FISH_SHELL" ]; then echo fish; fi
}

# first_existing FILE... -- print the first FILE that exists, else the first
# FILE.
first_existing() {
  _default="$1"
  for _file do
    if [ -e "$_file" ]; then
      printf '%s\n' "$_file"
      return
    fi
  done
  printf '%s\n' "$_default"
}

# rc_file SHELL -- print the startup file SHELL reads when a new terminal
# starts it. Prints nothing for unsupported shells.
rc_file() {
  case "$1" in
    zsh) printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
    # macOS terminals start bash as a login shell, which reads only the first
    # of these that exists; creating .bash_profile would hide a .profile.
    bash) first_existing "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile" ;;
    fish) printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish" ;;
    # tcsh reads .cshrc only when .tcshrc does not exist.
    tcsh) first_existing "$HOME/.tcshrc" "$HOME/.cshrc" ;;
    csh) printf '%s\n' "$HOME/.cshrc" ;;
    *)
      if [ "$(shell_syntax "$1")" = sh ]; then
        printf '%s\n' "$HOME/.profile"
      fi
      ;;
  esac
}

# home_relative DIR -- print DIR with a leading $HOME written as a literal
# "$HOME", so the startup file expands it at shell start.
home_relative() {
  case "$1" in
    "$HOME"/*) printf '%s\n' "\$HOME/${1#"$HOME"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

# path_line SHELL DIR -- print the startup-file line, in SHELL's syntax, that
# puts DIR first on PATH.
# shellcheck disable=SC2016 # $PATH is for the startup file to expand
path_line() {
  case "$(shell_syntax "$1")" in
    fish) printf 'contains -- "%s" $PATH; or set -gx PATH "%s" $PATH\n' "$2" "$2" ;;
    csh) printf 'setenv PATH "%s:$PATH"\n' "$2" ;;
    *) printf 'export PATH="%s:$PATH"\n' "$2" ;;
  esac
}

# ensure_on_path DIR [SHELL] -- unless DIR is on the inherited PATH, append a
# line putting it there to the startup file of SHELL (default: the login
# shell). Never appends the same line twice.
ensure_on_path() {
  _dir="$1"
  _shell="${2:-$(login_shell)}"
  case ":$EIGENAUGEN_INHERITED_PATH:" in
    *":$_dir:"* | *":$_dir/:"*)
      log "$_dir already on PATH"
      return
      ;;
  esac
  _rc="$(rc_file "$_shell")"
  if [ -z "$_rc" ]; then
    warn "$_dir is not on PATH and login shell '$_shell' is not supported -- add it to PATH yourself"
    return
  fi
  _line="$(path_line "$_shell" "$(home_relative "$_dir")")"
  if grep -qxF "$_line" "$_rc" 2>/dev/null; then
    log "$_dir already added to PATH in $_rc"
  else
    log "adding $_dir to PATH in $_rc (login shell: $_shell)..."
    mkdir -p "$(dirname "$_rc")"
    printf '\n%s\n%s\n' "$RC_COMMENT" "$_line" >> "$_rc"
  fi
  warn "open a new terminal (or: source $_rc) to put $_dir on PATH"
}
