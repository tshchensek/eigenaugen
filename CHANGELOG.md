# Changelog
What changed in each eigenaugen version, newest first. The current
version is in [VERSION](VERSION); how versions are bumped and released:
[README, Versioning](README.md#versioning).

Each version has one section, headed `## X.Y.Z - YYYY-MM-DD`, with
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) groups (Added,
Changed, Deprecated, Removed, Fixed, Security). Its body becomes the
notes of GitHub Release `vX.Y.Z`.

## 0.1.0 - 2026-11-15
First versioned release.

### Added
- `eigenaugen review`: reviews one GitHub PR with Claude Code in a
  read-only sandbox; report to stdout, `--post` as a COMMENT review.
- Review axes in `prompts/system.md`, including Schema compatibility and
  Cross-PR conflicts.
- Diffs computed locally in blob-less clones, paged and chunked; a run
  that leaves review pages unread fails and is never posted.
- Stack discovery (GitHub stacked PRs, base/head branch chains), and
  `--stack`, `--related`, `--no-stack`.
- `scripts/bootstrap.sh` and `scripts/install.sh` on macOS and Linux
  (glibc, x86_64 or aarch64).
- Versions: `VERSION`, `eigenaugen --version`, the version in the review
  footer, this changelog; CI checks bumps on PRs and releases new
  versions from `main`.
