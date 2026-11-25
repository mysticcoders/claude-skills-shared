#!/usr/bin/env bash
# Create a review handoff directory and launch the reviewer agent in a Rex split.
#
# Usage: start.sh <codex|claude> <repo_root> <base_sha> <label> <context_file> <request_file>
#
# context_file and request_file are written by the caller beforehand and copied in
# as context.md and round-1.request.md, so they exist before the reviewer boots.
# Prints {"handoff": DIR, "block": BLOCK_ID} on success.
set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "usage: start.sh <codex|claude> <repo_root> <base_sha> <label> <context_file> <request_file>" >&2
  exit 64
fi

reviewer=$1 repo=$2 base_sha=$3 label=$4 context_file=$5 request_file=$6
skill_dir=$(cd "$(dirname "$0")/.." && pwd)

: "${REX_SESSION:?REX_SESSION is not set; run inside a Rex terminal}"
: "${REX_BLOCK:?REX_BLOCK is not set; run inside a Rex terminal}"

git -C "$repo" cat-file -e "${base_sha}^{commit}"

tmp_root=${TMPDIR:-/tmp}
handoff="${tmp_root%/}/rex-review/${label}-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$handoff"
handoff=$(cd "$handoff" && pwd -P)

sed -e "s|{{REPO}}|$repo|g" -e "s|{{BASE_SHA}}|$base_sha|g" -e "s|{{HANDOFF}}|$handoff|g" \
  "$skill_dir/protocol.md" > "$handoff/protocol.md"
cp "$context_file" "$handoff/context.md"
cp "$request_file" "$handoff/round-1.request.md"

prompt="You are the code reviewer in a review loop. Read $handoff/protocol.md and follow it exactly. This is round 1: start with $handoff/round-1.request.md."

case "$reviewer" in
  codex)
    cmd=(codex --cd "$repo" --sandbox workspace-write --ask-for-approval on-request
         --add-dir "$handoff" "$prompt")
    ;;
  claude)
    cmd=(claude --add-dir "$handoff"
         --allowedTools "Read" "Grep" "Glob"
           "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)" "Bash(git status:*)"
           "Bash(git blame:*)" "Bash(git ls-files:*)" "Bash(git rev-parse:*)"
           "Bash(touch $handoff/*)" "Write($handoff/**)"
         -- "$prompt")
    ;;
  *)
    echo "reviewer must be codex or claude, got: $reviewer" >&2
    exit 64
    ;;
esac

created=$(rex split -s "$REX_SESSION" -b "$REX_BLOCK" --split=right --ratio 45 \
  --label "review:$reviewer" --focus=false --keep-open -c "$repo" --json -- "${cmd[@]}")
block=$(printf '%s' "$created" | jq -er '.block_ids[0]')

printf '%s\n' "$reviewer" > "$handoff/reviewer"
printf '%s\n' "$block" > "$handoff/block"
jq -n --arg handoff "$handoff" --arg block "$block" '{handoff: $handoff, block: $block}'
