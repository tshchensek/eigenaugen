#!/bin/sh
# release.sh -- check version bumps and publish releases; CI runs both.
#
# VERSION holds MAJOR.MINOR.PATCH. CHANGELOG.md opens with that version's
# section. A change to any shipped file bumps VERSION; each new VERSION on
# main becomes tag vVERSION and a GitHub Release.
set -eu

# shellcheck source=SCRIPTDIR/lib/common.sh
. "$(dirname "$0")/lib/common.sh"

# -- Constants -----------------------------------------------------------------
VERSION_FILE="VERSION"
CHANGELOG_FILE="CHANGELOG.md"
TAG_PREFIX="v"
# SemVer core version, no leading zeros: https://semver.org/#spec-item-2
VERSION_RE='(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
# Paths that do not ship (docs, tests, CI, lint config): a change to only
# these needs no new version. Anything else, including a new top-level
# file, counts as shipped. Extended regex over `git diff --name-only`.
UNSHIPPED_RE='^(\.github/|tests/|(README|CHANGELOG|AGENTS|CLAUDE)\.md$|LICENSE$|ruff\.toml$|\.gitignore$)'

# -- Help ----------------------------------------------------------------------
help() {
  cat <<EOF
release.sh -- check version bumps and publish releases (run by CI)

USAGE
  ./scripts/release.sh [-h] COMMAND [ARG]

COMMANDS
  check BASE  Fail unless $VERSION_FILE is MAJOR.MINOR.PATCH, $CHANGELOG_FILE
              opens with its section, and, if the working tree changes
              shipped files since its merge base with BASE, $VERSION_FILE
              is greater than BASE's.
  notes       Print the body of $VERSION_FILE's $CHANGELOG_FILE section.
  publish     Unless tag ${TAG_PREFIX}VERSION exists on GitHub, create it at HEAD
              with a GitHub Release whose notes are \`notes\`. Needs gh
              with contents: write.

OPTIONS
  -h  Show this help and exit

NOTES
  * Section heading: "## X.Y.Z - YYYY-MM-DD", newest first.
  * Unshipped paths (no bump needed): $UNSHIPPED_RE
  * Examples:
      ./scripts/release.sh check main     # before opening a PR
      ./scripts/release.sh notes
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
shift $((OPTIND - 1))

# -- Versions ------------------------------------------------------------------
# valid_version V -- succeed if V is exactly MAJOR.MINOR.PATCH.
valid_version() {
  case "$1" in
    '' | *[!0-9.]*) return 1 ;;  # also rejects newlines and spaces
  esac
  printf '%s\n' "$1" | grep -Eqx "$VERSION_RE"
}

# read_version -- print the working tree's version; die unless valid.
read_version() {
  [ -f "$VERSION_FILE" ] || die "missing $VERSION_FILE"
  _version="$(cat "$VERSION_FILE")"
  valid_version "$_version" \
    || die "$VERSION_FILE must be MAJOR.MINOR.PATCH, got: $_version"
  printf '%s\n' "$_version"
}

# version_gt A B -- succeed if version A is greater than version B.
version_gt() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    split(a, x, "."); split(b, y, ".")
    for (i = 1; i <= 3; i++)
      if (x[i] != y[i]) exit !(x[i] + 0 > y[i] + 0)
    exit 1
  }'
}

# changelog_notes VERSION -- print the body of CHANGELOG.md's first section,
# blank lines around it trimmed; fail unless its heading is
# "## VERSION - YYYY-MM-DD" and the body is not empty.
changelog_notes() {
  [ -f "$CHANGELOG_FILE" ] || die "missing $CHANGELOG_FILE"
  awk -v prefix="## $1 - " '
    /^## / {
      if (seen) exit
      seen = 1
      date = substr($0, length(prefix) + 1)
      bad = (index($0, prefix) != 1) \
        || (date !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/)
      next
    }
    seen { body[++n] = $0 }
    END {
      if (!seen || bad) exit 1
      first = 1
      while (first <= n && body[first] ~ /^[ \t]*$/) first++
      last = n
      while (last >= first && body[last] ~ /^[ \t]*$/) last--
      if (first > last) exit 1
      for (i = first; i <= last; i++) print body[i]
    }
  ' "$CHANGELOG_FILE"
}

# require_notes VERSION -- print changelog_notes VERSION, or die.
require_notes() {
  changelog_notes "$1" || die "$CHANGELOG_FILE must open with a" \
    "\"## $1 - YYYY-MM-DD\" section that has a body"
}

# -- Commands ------------------------------------------------------------------
check() {
  [ "$#" -eq 1 ] || die "usage: $0 check BASE"
  base="$1"
  git rev-parse -q --verify "$base^{commit}" >/dev/null \
    || die "not a commit: $base"
  version="$(read_version)"
  require_notes "$version" >/dev/null
  merge_base="$(git merge-base "$base" HEAD)" \
    || die "no merge base of $base and HEAD"
  # --no-renames: a file moved out of a shipped path still counts. Untracked
  # files count too, for checks before committing.
  shipped="$( {
    git diff --name-only --no-renames "$merge_base"
    git ls-files --others --exclude-standard
  } | grep -Ev "$UNSHIPPED_RE" || true)"
  if [ -z "$shipped" ]; then
    log "no shipped file changed; $version needs no bump"
    return
  fi
  if ! git cat-file -e "$base:$VERSION_FILE" 2>/dev/null; then
    log "$base has no $VERSION_FILE; $version is the first version"
    return
  fi
  base_version="$(git show "$base:$VERSION_FILE")"
  valid_version "$base_version" \
    || die "$base's $VERSION_FILE is not MAJOR.MINOR.PATCH: $base_version"
  version_gt "$version" "$base_version" \
    || die "shipped files changed ($(printf '%s' "$shipped" | tr '\n' ' ')), so" \
      "$VERSION_FILE ($version) must be greater than $base's" \
      "($base_version) -- bump it and add its $CHANGELOG_FILE section"
  log "version $base_version -> $version"
}

notes() {
  [ "$#" -eq 0 ] || die "usage: $0 notes"
  version="$(read_version)"
  require_notes "$version"
}

publish() {
  [ "$#" -eq 0 ] || die "usage: $0 publish"
  require_cmd gh
  version="$(read_version)"
  tag="$TAG_PREFIX$version"
  release_notes="$(require_notes "$version")"
  # Prefix match; the filter keeps only this exact tag.
  # https://docs.github.com/en/rest/git/refs#list-matching-references
  found="$(gh api "repos/{owner}/{repo}/git/matching-refs/tags/$tag" \
    --jq "map(select(.ref == \"refs/tags/$tag\")) | length")" \
    || die "cannot list tags"
  if [ "$found" != 0 ]; then
    log "$tag exists; nothing to release"
    return
  fi
  sha="$(git rev-parse HEAD)"
  log "releasing $tag at $sha"
  # https://cli.github.com/manual/gh_release_create
  printf '%s\n' "$release_notes" \
    | gh release create "$tag" --target "$sha" --title "$tag" --notes-file -
}

# -- Main ----------------------------------------------------------------------
[ "$#" -gt 0 ] || die "missing COMMAND -- run $0 -h"
subcommand="$1"
shift
cd "$PROJECT_ROOT"
case "$subcommand" in
  check | notes | publish) "$subcommand" "$@" ;;
  *) die "unknown command: $subcommand (choose from: check notes publish)" ;;
esac
