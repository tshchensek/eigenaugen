#!/bin/sh
# common.sh -- shared helpers for scripts/*.sh. Source it; do not execute.
#
# Sets PROJECT_ROOT and provides logging + command checks.

# Sourced files cannot locate themselves portably; every caller lives in
# scripts/, so the repo root is one level above $0.
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PROJECT_ROOT

BOOTSTRAP_HINT="run ./scripts/bootstrap.sh"
# User-level executables: Claude Code's native installer and the eigenaugen
# command both live here.
# shellcheck disable=SC2034 # used by the scripts that source this file
LOCAL_BIN="$HOME/.local/bin"

# -- Logging -------------------------------------------------------------------
log()  { printf '\033[0;32m[INFO]\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
die()  { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

# -- Commands ------------------------------------------------------------------
has_cmd() { command -v "$1" >/dev/null 2>&1; }

# require_cmd CMD... -- die unless every CMD is on PATH.
require_cmd() {
  for cmd do
    has_cmd "$cmd" || die "$cmd not found -- $BOOTSTRAP_HINT"
  done
}
