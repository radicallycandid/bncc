#!/usr/bin/env bash
# PostToolUse hook for `git push`: waits for GitHub Actions runs on the
# pushed commit and surfaces failures (failing job name + log tail) on
# stderr with a non-zero exit, so Claude sees the result and can't claim
# "shipped" without verifying CI.
#
# Silently exits 0 when:
#   - the Bash command wasn't a git push
#   - we're not in a git repo
#   - gh isn't installed
#   - the repo has no GitHub Actions workflows
#   - no runs were triggered for HEAD (e.g. push to a branch that doesn't
#     match any workflow's `on.push.branches`)
set -u

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')

# Match `git push` anywhere in the command (handles `git commit ... && git push`)
if ! printf '%s' "$cmd" | grep -qE '(^|[^a-zA-Z])git[[:space:]]+push([^a-zA-Z]|$)'; then
    exit 0
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then exit 0; fi
if ! command -v gh >/dev/null 2>&1; then exit 0; fi
if ! find .github/workflows -maxdepth 1 \( -name '*.yml' -o -name '*.yaml' \) -print -quit 2>/dev/null | grep -q .; then
    exit 0
fi

sha=$(git rev-parse HEAD)
short=$(git rev-parse --short HEAD)

# GitHub takes a moment to register the push and create runs. Poll briefly.
runs=""
for _ in 1 2 3 4 5 6; do
    runs=$(gh run list --commit "$sha" --json databaseId,workflowName --limit 20 2>/dev/null || echo "")
    if [ -n "$runs" ] && [ "$runs" != "[]" ]; then break; fi
    sleep 2
done
if [ -z "$runs" ] || [ "$runs" = "[]" ]; then
    # No workflows triggered for this commit (e.g. branch filter excluded it). Silent.
    exit 0
fi

failed=0
while IFS= read -r run; do
    id=$(printf '%s' "$run" | jq -r '.databaseId')
    name=$(printf '%s' "$run" | jq -r '.workflowName')
    if gh run watch "$id" --exit-status >/dev/null 2>&1; then
        printf 'CI ✓ %s on %s (run %s)\n' "$name" "$short" "$id" >&2
    else
        printf 'CI ✗ %s on %s (run %s)\n' "$name" "$short" "$id" >&2
        printf '── failing log tail ──\n' >&2
        gh run view "$id" --log-failed 2>/dev/null | tail -40 >&2 || true
        printf '── end ──\n' >&2
        failed=1
    fi
done < <(printf '%s' "$runs" | jq -c '.[]')

exit "$failed"
