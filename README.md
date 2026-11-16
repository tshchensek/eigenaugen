# eigenaugen

## Table of contents
* [Install](#install)
  - [What bootstrap installs](#what-bootstrap-installs)
  - [The eigenaugen command](#the-eigenaugen-command)
  - [After bootstrap](#after-bootstrap)
* [Usage](#usage)
  - [Review a pull request](#review-a-pull-request)
  - [Review axes](#review-axes)
  - [Large diffs](#large-diffs)
  - [Stacks and related PRs](#stacks-and-related-prs)
  - [How it works](#how-it-works)
  - [Sandbox](#sandbox)
  - [Layout](#layout)
* [Contribute](#contribute)
  - [Workflow](#workflow)
  - [Versioning](#versioning)
  - [Lint](#lint)
  - [Test](#test)
  - [CI](#ci)

## Install
Requirements: macOS 13+, or Linux (glibc, x86_64 or aarch64).

```sh
git clone git@github.com:tshchensek/eigenaugen.git
cd eigenaugen
./scripts/bootstrap.sh
```

Idempotent --- safe to re-run. Every dependency is checked first and
installed only if missing. `./scripts/bootstrap.sh -h` prints help.

Bootstrap never puts a uv on your PATH, nor upgrades or replaces yours.
The Python it installs, and uv when yours is missing or too old, live in
`.toolchain/` (gitignored); undo with `rm -rf .toolchain .venv`.

### What bootstrap installs
In order:

| Dependency      | Source                                                    | Check                                  |
|-----------------|-----------------------------------------------------------|----------------------------------------|
| Homebrew        | https://brew.sh (macOS only)                              | `brew` on PATH                         |
| Claude Code CLI | native installer, https://code.claude.com/docs/en/setup  | `claude` on PATH (incl. `~/.local/bin`)|
| `gh`            | `brew install gh`, https://cli.github.com/                | `gh` on PATH                           |
| `jq`            | `brew install jq`, https://formulae.brew.sh/formula/jq    | `jq` on PATH (macOS 15+ ships `/usr/bin/jq`) |
| `yq`            | `brew install yq`, https://formulae.brew.sh/formula/yq    | `yq` on PATH                           |
| shellcheck      | `brew install shellcheck`, https://www.shellcheck.net/    | `shellcheck` on PATH                   |
| uv              | `uv` on PATH, else uv `PINNED_UV_VERSION` (`scripts/lib/uv.sh`) downloaded to `.toolchain/bin/`, SHA-256 pinned, https://docs.astral.sh/uv/ | `uv python list --only-downloads` knows the Python in `.python-version` |
| Python          | `uv python install` (prebuilt) into `.toolchain/python/`, version from `.python-version` | `uv python find --managed-python` |
| `.venv`         | `uv venv`                                                 | `.venv` runs on that uv-managed Python |
| ruff, pytest    | `uv pip install -r requirements.txt` into `.venv`         | uv skips satisfied pins                |
| `eigenaugen`    | `scripts/install.sh`, see [below](#the-eigenaugen-command) | shim in `~/.local/bin` points at this checkout |

On Linux, bootstrap does not install `gh`, `jq`, `yq` or shellcheck: it
stops at the first one missing and names it. Install them with your
package manager and re-run.

Startup-file edits, appended only if absent, to the file of your login
shell (see [below](#the-eigenaugen-command)):
* `~/.local/bin` on PATH, unless already on PATH (claude, eigenaugen)

### The eigenaugen command
```sh
./scripts/install.sh            # bootstrap runs this; run it alone after moving the repo
./scripts/install.sh -h         # help
rm ~/.local/bin/eigenaugen      # uninstall
```

1. Writes `~/.local/bin/eigenaugen`, a shim that runs this checkout's
   `scripts/eigenaugen.sh`. Refuses to replace a file it did not write.
2. Checks whether `~/.local/bin` is on the PATH you ran it with. If not:
   1. Determines the login shell: account record (`dscl` on macOS,
      `getent passwd` on Linux), else `$SHELL`.
   2. Determines that shell's startup file (table).
   3. Appends one line, in that shell's syntax, putting `~/.local/bin`
      first on PATH. Never appends it twice.

| Login shell                       | Startup file                                                   | Line                                       |
|-----------------------------------|----------------------------------------------------------------|--------------------------------------------|
| zsh                               | `${ZDOTDIR:-$HOME}/.zshrc`                                     | `export PATH="$HOME/.local/bin:$PATH"`     |
| bash                              | first existing of `~/.bash_profile`, `~/.bash_login`, `~/.profile` (bash login lookup order) | same                |
| sh, dash, ksh, ksh93, mksh, oksh  | `~/.profile`                                                   | same                                       |
| fish                              | `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish`           | `contains -- ... $PATH; or set -gx PATH ...` |
| tcsh                              | `~/.tcshrc`, else an existing `~/.cshrc`                       | `setenv PATH "$HOME/.local/bin:$PATH"`     |
| csh                               | `~/.cshrc`                                                     | same                                       |
| anything else                     | none; prints a warning                                         | none                                       |

### After bootstrap
```sh
exec "$SHELL"                  # reload profile (PATH)
source .venv/bin/activate      # put ruff + pytest on PATH
gh auth login                  # once per machine
claude                         # log in on first run
```

## Usage
### Review a pull request
```sh
# inside a clone of the repo
eigenaugen review 123

# anywhere
eigenaugen review https://github.com/OWNER/REPO/pull/123
eigenaugen review https://github.com/OWNER/REPO/pull/123/changes
eigenaugen review OWNER/REPO 123

eigenaugen review 123 --post          # also post it to the PR
eigenaugen review 123 > review.md     # report on stdout, progress on stderr
eigenaugen review -h                  # help
eigenaugen --version                  # installed version

# stacks and related PRs
eigenaugen review 122 --stack 120,121,122,123   # stack given, bottom first
eigenaugen review 122 --related OTHER/REPO#45   # cross-check a PR elsewhere
eigenaugen review 122 --no-stack                # skip stack discovery
```

| Argument / option | Default                | Meaning                                                      |
|-------------------|------------------------|--------------------------------------------------------------|
| `TARGET`          | required               | PR number (inside a clone), PR URL, or `OWNER/REPO`          |
| `NUMBER`          | --                     | PR number; only after `OWNER/REPO`                           |
| `--post`          | off                    | post the report as a COMMENT review on the reviewed commit   |
| `--model`         | `opus` (latest stable) | model alias or ID                                            |
| `--effort`        | `xhigh`                | `low`, `medium`, `high`, `xhigh`, `max`                      |
| `--stack PR,...`  | discovered             | TARGET's stack, bottom first, TARGET included; replaces discovery |
| `--related PR`    | none                   | related PR, any repository, cross-checked like a stack member; repeatable |
| `--no-stack`      | off                    | discover no stacks (TARGET's or related PRs')                |

* PR URLs may end in `/changes` or `/files` (older name of the same
  tab), a query, or a fragment, as copied from the browser.
* `OWNER/REPO` also takes `HOST/OWNER/REPO` for GitHub Enterprise.
* `PR` in `--stack` and `--related`: `NUMBER` (TARGET's repository), a
  PR URL, or `[HOST/]OWNER/REPO#NUMBER`.

* Needs `claude` logged in with a claude.ai account; API keys in the
  environment are ignored. Needs `gh` authenticated.
* Exit codes: `0` report printed (or nothing to review), `1` error or
  diff not fully read (report printed, never posted), `130` interrupted.

### Review axes
Defined, each with its checklist, only in
[`eigenaugen/src/prompts/system.md`](eigenaugen/src/prompts/system.md#review-axes).
`eigenaugen review -h` lists them.

### Large diffs
Every review reads the whole diff, whatever its size.

| Mechanism | What it does |
|-----------|--------------|
| Local diff | `git diff <merge base> <head>` in the clone; no GitHub API diff or file limits |
| Pages | at most 40,000 characters each (one tool result); a file cut by a page break resumes on the next page, which says where (old and new line numbers) |
| Chunks | up to 4 pages: one subagent's share |
| Skim chunks | lockfiles, vendored, generated, minified, binary files; by path, `linguist-generated` and `linguist-vendored` gitattributes read at the merge base (so a PR cannot mark its own files), never migrations or schema dumps |
| Review mode | `direct` (up to 5 review pages): the reviewer reads every page; `delegate`: one `diff-reviewer` subagent per review chunk |
| Completeness check | the MCP server records every page it serves; afterwards the CLI resumes the session with the unread pages, up to 3 times |

A review that still left pages unread prints its report with
`UNREAD: ...` in the footer, exits `1`, and is not posted.

### Stacks and related PRs
A review finds the stack its PR belongs to, in this order:
1. GitHub stacked pull requests (`stack` of the GraphQL `PullRequest`):
   https://docs.github.com/en/pull-requests/get-started/about-stacked-prs
2. Branch chain (Graphite, git-town, spr, by hand): the PR's base branch
   is another open PR's head branch, followed down to the default branch
   and up through every PR stacked on top.
3. `--stack`, which replaces discovery; for stacks neither finds (ghstack,
   Sapling).

`--related` adds PRs in any repository, for example the client of an API
the PR changes; their stacks are found the same way. Merged and closed
stack members are left out.

| The reviewer | Rule |
|--------------|------|
| does not flag | issues a PR above in the stack, or a related PR, resolves; it lists them under Context instead, unless the state in between is a blocker |
| flags | conflicts with other PRs: contracts, overlapping changes, merge and deploy order, PRs built on an outdated head of this one (axis Cross-PR conflicts) |
| reads | other PRs with the same tools and a `pr` key, or with one `cross-checker` subagent per PR |

Every PR is pinned like the reviewed one. Each repository gets one
blob-less clone; only the reviewed PR's head is checked out.

### How it works
1. `gh pr view` resolves the PR and pins its base and head commits.
2. Stack discovery and `--related` add context PRs, each pinned too.
   GitHub's compare API gives each PR's merge base.
3. One shallow, blob-less clone per repository goes into a private
   temp directory, deleted on exit; the reviewed head is checked out.
   Local clones are never touched. Diffs are computed locally.
4. `eigenaugen.src.agent_sdk` drives `claude` the way the Claude Agent SDK
   does: options become CLI flags, the prompt is a stream-json message
   on stdin, stdout is parsed into typed messages.
5. The agent pulls PR data from `eigenaugen.src.mcp_server`, a stdio MCP
   server the CLI runs as a subprocess. It serves the review's PRs only,
   from the clones, pinned, so pushes during a review change nothing.
6. The reviewer decides whether to start subagents and picks each one's
   model and effort (`haiku`, `sonnet`, `opus`), guided to the cheapest
   model that does the job. Defined roles: `standards-scout`,
   `code-tracer`, `diff-reviewer`, `cross-checker`.
7. The CLI checks that every review page of the diff was read and
   resumes the session if not ([Large diffs](#large-diffs)).
8. The report goes to stdout; with `--post`, also to the PR. It ends
   with a footer:

   ```text
   Reviewed commit `a9d9d84ee3e8` with eigenaugen 0.1.0, effort `xhigh`
   - Diff: 12 files, +300 -20; 3 review pages in 1 chunk; all read
   - Context: GitHub stack #577, 3 PRs of octo/repo#575
   - Run: claude-opus-5-5, $2.30 (API list price), 4.2 min, 69 turns
   - Models: claude-opus-5-5 $2.21, claude-haiku-5-5 $0.09
   - Subagents: 2 (code-tracer x1, standards-scout x1)
   - To continue this conversation: claude --resume 0b1c2d3e-...
   ```

   | Field     | Source                                                        |
   |-----------|---------------------------------------------------------------|
   | version   | eigenaugen's `VERSION` ([Versioning](#versioning))            |
   | Diff      | the reviewed diff: files, lines, review pages and chunks, skimmed files, pages left unread |
   | Context   | stacks found or given, related PRs; omitted when there are none |
   | model     | main agent's model, resolved from the alias                   |
   | cost      | CLI's `total_cost_usd`: API list price over all models; a claude.ai plan is not billed per run |
   | time      | wall clock of the whole run: lookup, checkout, review         |
   | turns     | agent turns, summed over the session's results and resumptions |
   | Models    | CLI's per-model cost: main agent, subagents, and the CLI's own helper calls (`WebSearch` runs on haiku) |
   | Subagents | subagents spawned, by type                                    |
   | resume    | the review's session ID; omitted from reviews posted with `--post` |

   Resuming (`claude --resume <id>`, from any directory) reopens the
   review's conversation with everything the reviewer read. Know that:
   * the checkout is gone (deleted after every run); run it inside a
     clone if the follow-up needs files.
   * it runs with your normal Claude Code settings, tools, and
     permission prompts, not the review sandbox, while untrusted PR
     content is in its context.
   * transcripts live in `~/.claude/projects/`, one folder per review,
     and are pruned by Claude Code's `cleanupPeriodDays` setting.

Every MCP tool takes an optional `pr` key (`OWNER/REPO#NUMBER`) naming
one of the review's PRs; default: the reviewed PR.

| MCP tool           | Returns                                                          |
|--------------------|------------------------------------------------------------------|
| `get_pull_request` | live metadata: title, body, refs, labels, up to 100 changed files |
| `get_diff_index`   | diff sizes, review mode, review chunks, skimmed files; one chunk's files and pages |
| `get_diff`         | one page of the pinned diff: a chunk or one file                 |
| `get_file`         | a file at the head, base, or merge-base commit, paged            |

### Sandbox
PR content is untrusted and may contain prompt injection. The agent
session:
* has read-only tools only: Read, Grep, Glob, subagents, WebSearch, the
  MCP tools. No shell, edits, or WebFetch.
* can read only its empty working directory and the checkout
  (`--restricted`).
* ignores user, project, and local settings, the user's MCP servers,
  and skills.
* denies anything not pre-approved instead of prompting
  (`--permission-mode dontAsk`).
* starts in a directory without `CLAUDE.md`; the PR's `CLAUDE.md` is
  never loaded as instructions.
* cannot write to GitHub. Only `--post`, after the agent finishes, does.
* reaches only the review's PRs through the MCP tools: a `pr` key not
  in the review is refused. Diffs, context, and the page record live in
  a data directory the file tools cannot read.

Progress lines and the report are stripped of terminal control
characters.

### Layout
| Path                           | Purpose                                              |
|--------------------------------|------------------------------------------------------|
| `VERSION`                      | eigenaugen's version, `MAJOR.MINOR.PATCH`            |
| `CHANGELOG.md`                 | what changed in each version                         |
| `scripts/install.sh`           | installs the `eigenaugen` shim, puts it on PATH      |
| `scripts/eigenaugen.sh`        | launcher the shim runs                               |
| `scripts/lib/shellrc.sh`       | login shell, startup file, PATH line                 |
| `scripts/lib/uv.sh`            | bootstrap's uv: the one on PATH, else a pinned one   |
| `scripts/release.sh`           | version bump check, releases (CI runs both)          |
| `eigenaugen/src/cli.py`        | `eigenaugen review` arguments, orchestration         |
| `eigenaugen/src/prepare.py`    | gathers the review's PRs, clones, diffs, context     |
| `eigenaugen/src/stacks.py`     | stack discovery: GitHub stacks, branch chains, `--stack` |
| `eigenaugen/src/gitrepo.py`    | local clones: fetch, checkout, diff, files, attributes |
| `eigenaugen/src/diffs.py`      | unified diff parsing                                 |
| `eigenaugen/src/diffplan.py`   | pages, chunks, skim classes, review mode, diff index |
| `eigenaugen/src/coverage.py`   | record of served pages; unread pages                 |
| `eigenaugen/src/context.py`    | review context file shared with the MCP server       |
| `eigenaugen/src/reviewer.py`   | agent options, sandbox, progress, result checks, resumption |
| `eigenaugen/src/agent_sdk/`    | SDK-style client for the `claude` CLI                |
| `eigenaugen/src/mcp_server/`   | stdio MCP server with pinned tools for the review's PRs |
| `eigenaugen/src/github.py`     | `gh` wrappers: targets, resolve, compare, GraphQL, post |
| `eigenaugen/src/prompts/`      | system prompt, launch and continuation prompts, subagent definitions |
| `tests/`                       | pytest suite; no network                             |
| `.github/workflows/ci.yml`     | CI: lint, test, version check; releases from `main`  |

The prompts are self-contained: they copy the communication, citation,
and URL formatting rules instead of pointing at this repo's `AGENTS.md`.

## Contribute
Coding, shell and Python conventions live in [AGENTS.md](AGENTS.md).
Read it before your first change.

### Workflow
1. Branch off `main`:
   ```sh
   git switch main && git pull
   git switch -c <topic>
   ```
2. Make changes. New Python dependency: add a pinned line to
   `requirements.txt`, then re-run `./scripts/bootstrap.sh`.
3. Changed a shipped file: bump `VERSION`, add its `CHANGELOG.md`
   section ([Versioning](#versioning)), then
   `./scripts/release.sh check main`.
4. [Lint](#lint) and [test](#test).
5. Commit, push, open a PR:
   ```sh
   git push -u origin HEAD
   gh pr create --fill
   ```

### Versioning
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). `VERSION`
holds `MAJOR.MINOR.PATCH`; [CHANGELOG.md](CHANGELOG.md) says what changed
in each version.

* Every PR that changes a shipped file bumps `VERSION` and opens
  `CHANGELOG.md` with a `## X.Y.Z - YYYY-MM-DD` section. CI's `version`
  job enforces it, so every such merge to `main` is a release.
* Shipped: every path except `.github/`, `tests/`, `README.md`,
  `CHANGELOG.md`, `AGENTS.md`, `CLAUDE.md`, `LICENSE`, `ruff.toml`,
  `.gitignore` (`UNSHIPPED_RE` in `scripts/release.sh`). A new top-level
  file counts as shipped.
* Which part to bump:

  | Part    | When                     | Examples                                                    |
  |---------|--------------------------|-------------------------------------------------------------|
  | `MAJOR` | breaks users or scripts  | option removed or renamed, exit code changed, report contract (`## Verdict`, footer) changed |
  | `MINOR` | new behavior             | new option, review axis, MCP tool, or subagent; prompt change that changes what reviews find |
  | `PATCH` | fixes, no new behavior   | bug fix, prompt wording, pin or dependency bump             |

  While `MAJOR` is `0`, breaking changes bump `MINOR`
  ([SemVer item 4](https://semver.org/spec/v2.0.0.html#spec-item-4)).
* Releases: when lint and test pass on `main`, CI's `release` job runs
  `./scripts/release.sh publish`. Unless tag `vX.Y.Z` exists, it creates
  it on that commit with a GitHub Release whose notes are the version's
  changelog section.
* `eigenaugen --version` prints the version; the review footer and the
  MCP server's `serverInfo` carry it.

```sh
./scripts/release.sh check main     # working tree (untracked files too) vs main
./scripts/release.sh notes          # VERSION's changelog section
./scripts/release.sh -h             # help, rules
```

### Lint
```sh
./scripts/lintme.sh                 # all linters; ruff fixes + formats in place
./scripts/lintme.sh -c              # check only, modifies nothing (CI)
./scripts/lintme.sh json yaml       # subset
./scripts/lintme.sh -h              # help
```

| Linter   | Files                                 | Tool                                        |
|----------|---------------------------------------|---------------------------------------------|
| `python` | `*.py`, code blocks in `*.md`         | `ruff check --fix` + `ruff format`          |
| `json`   | `*.json`                              | `jq empty` (parse check)                    |
| `yaml`   | `*.yaml`, `*.yml`                     | `yq` (parse check, not style)               |
| `shell`  | `*.sh`                                | `shellcheck -x` (follows sourced files)     |

* Every selected linter runs; exit code is 1 if any failed.
* Skips `.git`, `.venv`, `.ruff_cache`, `.pytest_cache`.
* Uses ruff from `.venv` even when the venv is not activated.
* ruff config: `ruff.toml`. `AGENTS.md` is excluded so its code blocks are
  never reformatted.

Shell scripts: `#!/bin/sh` shebang (POSIX; shellcheck rejects bashisms
such as `local`), mode `755`. Shared helpers live in
`scripts/lib/common.sh` (logging, `PROJECT_ROOT`, `LOCAL_BIN`) and
`scripts/lib/shellrc.sh` (startup files); source them with:

```sh
# shellcheck source=SCRIPTDIR/lib/common.sh
. "$(dirname "$0")/lib/common.sh"
# shellcheck source=SCRIPTDIR/lib/shellrc.sh
. "$(dirname "$0")/lib/shellrc.sh"
```

### Test
```sh
pytest
```

### CI
GitHub Actions, `.github/workflows/ci.yml`. Runs on pushes to `main`, on
pull requests, and on demand (`gh workflow run ci.yml`).

| Job       | Runs after | Runs on       | Steps                                               |
|-----------|------------|---------------|-----------------------------------------------------|
| `lint`    | --         | every run     | `./scripts/lintme.sh -c` (all linters, check only)  |
| `test`    | `lint`     | every run     | `pytest`                                            |
| `version` | --         | pull requests | `./scripts/release.sh check HEAD^1` (merge commit vs base) |
| `release` | `test`     | `main`        | `./scripts/release.sh publish`                      |

* `lint` and `test` run on `macos-latest` and `ubuntu-latest`, like the
  tool itself; `version` and `release` on `ubuntu-latest`.
* Only `release` may write to the repo (`contents: write`: tags,
  releases); every other job reads only. A failed release: re-run it with
  `gh workflow run ci.yml` on `main`.
* `.github/actions/setup-venv` builds `.venv` the way `bootstrap.sh` does:
  uv, the Python in `.python-version`, `requirements.txt`.
* Third-party actions are pinned to commit SHAs; the tag is in a trailing
  comment. The local action is referenced as `$/...` (GitHub's
  self-repository syntax: this repo at the running commit).
* A new push to a PR cancels its running CI.
