# eigenaugen

## Table of contents
* [Install](#install)
  - [What bootstrap installs](#what-bootstrap-installs)
  - [The eigenaugen command](#the-eigenaugen-command)
  - [After bootstrap](#after-bootstrap)
* [Usage](#usage)
  - [Review a pull request](#review-a-pull-request)
  - [Review axes](#review-axes)
  - [How it works](#how-it-works)
  - [Sandbox](#sandbox)
  - [Layout](#layout)
* [Contribute](#contribute)
  - [Workflow](#workflow)
  - [Lint](#lint)
  - [Test](#test)

## Install
Requirements: macOS 13+.

```sh
git clone git@github.com:tshchensek/eigenaugen.git
cd eigenaugen
./scripts/bootstrap.sh
```

Idempotent --- safe to re-run. Every dependency is checked first and
installed only if missing. `./scripts/bootstrap.sh -h` prints help.

### What bootstrap installs
In order:

| Dependency      | Source                                                    | Check                                  |
|-----------------|-----------------------------------------------------------|----------------------------------------|
| Homebrew        | https://brew.sh                                           | `brew` on PATH                         |
| Claude Code CLI | native installer, https://code.claude.com/docs/en/setup  | `claude` on PATH (incl. `~/.local/bin`)|
| `gh`            | `brew install gh`, https://cli.github.com/                | `gh` on PATH                           |
| `jq`            | `brew install jq`, https://formulae.brew.sh/formula/jq    | `jq` on PATH (macOS 15+ ships `/usr/bin/jq`) |
| `yq`            | `brew install yq`, https://formulae.brew.sh/formula/yq    | `yq` on PATH                           |
| shellcheck      | `brew install shellcheck`, https://www.shellcheck.net/    | `shellcheck` on PATH                   |
| uv              | `brew install uv`, https://docs.astral.sh/uv/             | `uv` on PATH                           |
| Python          | `uv python install` (prebuilt), version from `.python-version` | `uv python find --managed-python` |
| `.venv`         | `uv venv`                                                 | `.venv` runs on that uv-managed Python |
| ruff, pytest    | `uv pip install -r requirements.txt` into `.venv`         | uv skips satisfied pins                |
| `eigenaugen`    | `scripts/install.sh`, see [below](#the-eigenaugen-command) | shim in `~/.local/bin` points at this checkout |

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
   1. Determines the login shell: directory service record (`dscl`),
      else `$SHELL`.
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
```

| Argument / option | Default                | Meaning                                                      |
|-------------------|------------------------|--------------------------------------------------------------|
| `TARGET`          | required               | PR number (inside a clone), PR URL, or `OWNER/REPO`          |
| `NUMBER`          | --                     | PR number; only after `OWNER/REPO`                           |
| `--post`          | off                    | post the report as a COMMENT review on the reviewed commit   |
| `--model`         | `opus` (latest stable) | model alias or ID                                            |
| `--effort`        | `xhigh`                | `low`, `medium`, `high`, `xhigh`, `max`                      |

* PR URLs may end in `/changes` or `/files` (older name of the same
  tab), a query, or a fragment, as copied from the browser.
* `OWNER/REPO` also takes `HOST/OWNER/REPO` for GitHub Enterprise.

* Needs `claude` logged in with a claude.ai account; API keys in the
  environment are ignored. Needs `gh` authenticated.
* Exit codes: `0` report printed (or nothing to review), `1` error,
  `130` interrupted.

### Review axes
* concurrency
* race conditions
* edge cases (network failure, bad input)
* code hygiene (repo standards first, language standards as fallback)
* DRY
* injection/auth vulnerabilities
* error propagation
* documentation
* abstraction of magic values
* scalability: time and space complexity

Defined in `eigenaugen/src/prompts/system.md`.

### How it works
1. `gh pr view` resolves the PR and pins its base and head commits.
2. A shallow, blob-less clone of the head commit goes into a private
   temp directory, deleted on exit. Local clones are never touched.
3. `eigenaugen.src.agent_sdk` drives `claude` the way the Claude Agent SDK
   does: options become CLI flags, the prompt is a stream-json message
   on stdin, stdout is parsed into typed messages.
4. The agent pulls PR data from `eigenaugen.src.mcp_server`, a stdio MCP
   server the CLI runs as a subprocess. Its tools wrap `gh` and are
   pinned to the two commits, so pushes during a review change nothing.
5. The reviewer decides whether to start subagents and picks each one's
   model and effort (`haiku`, `sonnet`, `opus`), guided to the cheapest
   model that does the job. Defined roles: `standards-scout`,
   `code-tracer`.
6. The report goes to stdout; with `--post`, also to the PR. It ends
   with a footer:

   ```text
   Reviewed commit `a9d9d84ee3e8` with eigenaugen, effort `xhigh`
   - Run: claude-opus-5-5, $2.30 (API list price), 4.2 min, 69 turns
   - Models: claude-opus-5-5 $2.21, claude-haiku-5-5 $0.09
   - Subagents: 2 (code-tracer x1, standards-scout x1)
   - To continue this conversation: claude --resume 0b1c2d3e-...
   ```

   | Field     | Source                                                        |
   |-----------|---------------------------------------------------------------|
   | model     | main agent's model, resolved from the alias                   |
   | cost      | CLI's `total_cost_usd`: API list price over all models; a claude.ai plan is not billed per run |
   | time      | wall clock of the whole run: lookup, checkout, review         |
   | turns     | agent turns, summed over the session's results                |
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

| MCP tool           | Returns                                              |
|--------------------|------------------------------------------------------|
| `get_pull_request` | metadata: title, body, refs, labels, changed files   |
| `get_diff`         | pinned diff, whole PR or one file, paged             |
| `get_base_file`    | a file at the base commit, paged                     |

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

Progress lines and the report are stripped of terminal control
characters.

### Layout
| Path                           | Purpose                                              |
|--------------------------------|------------------------------------------------------|
| `scripts/install.sh`           | installs the `eigenaugen` shim, puts it on PATH      |
| `scripts/eigenaugen.sh`        | launcher the shim runs                               |
| `scripts/lib/shellrc.sh`       | login shell, startup file, PATH line                 |
| `eigenaugen/src/cli.py`        | `eigenaugen review` arguments, orchestration         |
| `eigenaugen/src/reviewer.py`   | agent options, sandbox, progress, result checks      |
| `eigenaugen/src/agent_sdk/`    | SDK-style client for the `claude` CLI                |
| `eigenaugen/src/mcp_server/`   | stdio MCP server with pinned GitHub tools            |
| `eigenaugen/src/github.py`     | `gh` wrappers: targets, resolve, diff, checkout, post |
| `eigenaugen/src/prompts/`      | system prompt, launch prompt, subagent definitions   |
| `tests/`                       | pytest suite; no network                             |

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
3. [Lint](#lint) and [test](#test).
4. Commit, push, open a PR:
   ```sh
   git push -u origin HEAD
   gh pr create --fill
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
