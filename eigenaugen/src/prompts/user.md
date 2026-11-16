Review pull request $pr_url (key `$pr_key`).

| Input | Value |
|-------|-------|
| Base commit (pinned) | `$base_sha` |
| Merge base, where the diff starts | `$merge_base_sha` |
| Head commit (pinned) | `$head_sha` |
| Diff | $diff_summary |
| Review mode | `$mode` |
| Checkout of the head commit | `$checkout` |
| Destination | `$destination` |
| Caller `CLAUDE_CODE_ENTRYPOINT` | `$caller_entrypoint` |

The GitHub tools are pinned to these commits. Your working directory is empty; pass `$checkout` or paths under it to Read, Grep, Glob, and the subagents.

Other PRs of this review (pass the key as `pr` to the GitHub tools):

$context_table
