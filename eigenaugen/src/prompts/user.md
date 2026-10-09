Review pull request $pr_url.

| Input | Value |
|-------|-------|
| Base commit (pinned) | `$base_sha` |
| Head commit (pinned) | `$head_sha` |
| Checkout of the head commit | `$checkout` |
| Destination | `$destination` |
| Caller `CLAUDE_CODE_ENTRYPOINT` | `$caller_entrypoint` |

The GitHub tools are pinned to these commits. Your working directory is empty; pass `$checkout` or paths under it to Read, Grep, Glob, and the subagents.
