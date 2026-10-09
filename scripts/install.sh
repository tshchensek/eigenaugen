#!/bin/sh
# install.sh -- install the `eigenaugen` command into ~/.local/bin and put
# ~/.local/bin on PATH in the login shell's startup file.
#
# Idempotent. The command is a small shim that runs this checkout's
# scripts/eigenaugen.sh; re-run after moving the repo.
set -eu

# shellcheck source=SCRIPTDIR/lib/common.sh
. "$(dirname "$0")/lib/common.sh"
# shellcheck source=SCRIPTDIR/lib/shellrc.sh
. "$(dirname "$0")/lib/shellrc.sh"

# -- Constants -----------------------------------------------------------------
COMMAND_NAME="eigenaugen"
LAUNCHER="$PROJECT_ROOT/scripts/eigenaugen.sh"
SHIM="$LOCAL_BIN/$COMMAND_NAME"
# Second line of every shim; marks files this script may replace.
SHIM_MARKER="# eigenaugen shim, written by scripts/install.sh"

# -- Help ----------------------------------------------------------------------
help() {
  cat <<EOF
install.sh -- install the eigenaugen command into $LOCAL_BIN

USAGE
  ./scripts/install.sh [-h]

OPTIONS
  -h  Show this help and exit

WHAT IT DOES
  1. Write $SHIM, a shim that runs
     $LAUNCHER
  2. If $LOCAL_BIN is not on PATH: find the login shell, find its
     startup file, and append a line putting $LOCAL_BIN on PATH.

NOTES
  * Re-running is safe; a line is never appended twice.
  * Supported login shells: $SH_SYNTAX_SHELLS $FISH_SHELL $CSH_SYNTAX_SHELLS.
  * Uninstall: rm $SHIM
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

# -- Shim ----------------------------------------------------------------------
# shim_content -- print the shim. The launcher path is single-quoted, with
# any single quote in it escaped.
shim_content() {
  quoted="$(printf '%s' "$LAUNCHER" | sed "s/'/'\\\\''/g")"
  cat <<EOF
#!/bin/sh
$SHIM_MARKER -- re-run it after moving the repo.
exec '$quoted' "\$@"
EOF
}

install_shim() {
  if [ -e "$SHIM" ] && ! grep -qF "$SHIM_MARKER" "$SHIM"; then
    die "$SHIM exists and is not an eigenaugen shim -- move it away and re-run"
  fi
  content="$(shim_content)"
  if [ -f "$SHIM" ] && [ "$(cat "$SHIM")" = "$content" ]; then
    log "$COMMAND_NAME already installed ($SHIM)"
    return
  fi
  log "installing $COMMAND_NAME to $SHIM..."
  mkdir -p "$LOCAL_BIN"
  # Write beside the target, then rename: never a half-written command.
  tmp="$SHIM.tmp.$$"
  printf '%s\n' "$content" > "$tmp"
  chmod 755 "$tmp"
  mv -f "$tmp" "$SHIM"
}

# -- Main ----------------------------------------------------------------------
[ -x "$LAUNCHER" ] || die "missing or not executable: $LAUNCHER"
install_shim
ensure_on_path "$LOCAL_BIN"
