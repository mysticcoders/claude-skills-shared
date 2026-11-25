#!/usr/bin/env bash
# Tell the reviewer that round N is ready by typing a one-line message into its pane.
#
# Usage: nudge.sh <block> <handoff> <round>
#
# Refuses if round-N.request.md is missing, so the reviewer never reads a half-written request.
set -euo pipefail

block=$1 handoff=$2 round=$3
request="$handoff/round-$round.request.md"

[[ -s $request ]] || { echo "missing $request" >&2; exit 1; }
[[ $block != "${REX_BLOCK:-}" ]] || { echo "refusing to send to this agent's own pane" >&2; exit 1; }

msg="Round $round is ready. Read $request, then re-review per $handoff/protocol.md and write round-$round.findings.md and round-$round.done."
rex send -s "$REX_SESSION" -b "$block" "$msg"
sleep 0.5
rex send-key -s "$REX_SESSION" -b "$block" enter
