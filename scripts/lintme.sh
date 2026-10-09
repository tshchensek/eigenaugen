#!/bin/sh
# lintme.sh -- lint the repo's Python, JSON, YAML and shell files.
#
# Runs every selected linter, then exits non-zero if any of them failed:
#   * python: ruff check + ruff format (fixes in place unless -c)
#   * json:   jq empty (parse check)
#   * yaml:   yq (parse check)
#   * shell:  shellcheck -x
set -eu

# shellcheck source=SCRIPTDIR/lib/common.sh
. "$(dirname "$0")/lib/common.sh"

# -- Constants -----------------------------------------------------------------
LINTERS="python json yaml shell"
# Directory basenames never searched for lintable files.
PRUNE_DIRS=".git .venv .ruff_cache .pytest_cache"
VENV_BIN="$PROJECT_ROOT/.venv/bin"

CHECK_ONLY=false

# -- Help ----------------------------------------------------------------------
help() {
  cat <<EOF
lintme.sh -- lint the repo's Python, JSON, YAML and shell files

USAGE
  ./scripts/lintme.sh [-c] [-h] [LINTER...]

OPTIONS
  -c  Check only; do not let ruff fix or reformat files (use in CI)
  -h  Show this help and exit

LINTERS (default: all)
  python  ruff check --fix + ruff format (with -c: ruff check + ruff format --check)
  json    jq empty on *.json
  yaml    yq on *.yaml, *.yml
  shell   shellcheck -x on *.sh

NOTES
  * Skips: $PRUNE_DIRS
  * Uses ruff from .venv when present. Missing tools: $BOOTSTRAP_HINT
  * Exits 1 if any linter fails; every selected linter still runs.
EOF
  exit 0
}

# -- Parse flags ---------------------------------------------------------------
while getopts ":ch" opt; do
  case "$opt" in
    c) CHECK_ONLY=true ;;
    h) help ;;
    \?) die "unknown option: -${OPTARG}" ;;
  esac
done
shift $((OPTIND - 1))

# -- Helpers -------------------------------------------------------------------
# find_files GLOB... -- print project files whose basename matches any GLOB,
# NUL-separated, skipping PRUNE_DIRS. Pipe into `xargs -0 -r`.
find_files() {
  for glob do
    shift
    set -- "$@" -o -name "$glob"
  done
  shift  # drop the leading -o
  set -- \( "$@" \) -type f -print0
  for dir in $PRUNE_DIRS; do
    set -- "$@" -o -type d -name "$dir" -prune
  done
  find . "$@"
}

# -- Linters -------------------------------------------------------------------
# https://docs.astral.sh/ruff/
lint_python() {
  require_cmd ruff
  rc=0
  if [ "$CHECK_ONLY" = true ]; then
    ruff check . || rc=1
    ruff format --check . || rc=1
  else
    ruff check --fix . || rc=1
    ruff format . || rc=1
  fi
  return "$rc"
}

# https://jqlang.org/manual/
lint_json() {
  require_cmd jq
  find_files '*.json' | xargs -0 -r jq empty
}

# https://mikefarah.gitbook.io/yq/
lint_yaml() {
  require_cmd yq
  find_files '*.yaml' '*.yml' | xargs -0 -r yq 'true' >/dev/null
}

# https://www.shellcheck.net/wiki/
lint_shell() {
  require_cmd shellcheck
  find_files '*.sh' | xargs -0 -r shellcheck -x
}

# -- Main ----------------------------------------------------------------------
# shellcheck disable=SC2086 # split into one argument per linter
[ "$#" -gt 0 ] || set -- $LINTERS
for linter do
  case " $LINTERS " in
    *" $linter "*) ;;
    *) die "unknown linter: $linter (choose from: $LINTERS)" ;;
  esac
done

cd "$PROJECT_ROOT"
PATH="$VENV_BIN:$PATH"

failed=""
for linter do
  log "linting $linter..."
  if "lint_$linter"; then
    log "$linter ok"
  else
    warn "$linter failed"
    failed="$failed $linter"
  fi
done

[ -z "$failed" ] || die "lint failed:$failed"
log "lint passed"
