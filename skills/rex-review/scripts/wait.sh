#!/usr/bin/env bash
# Wait for the reviewer to finish round N by polling for <handoff>/round-N.done.
#
# Usage: wait.sh <handoff> <round> <block> [timeout_seconds]
#
# Exit 0: round done (prints the findings path). Exit 2: timed out.
# Exit 3: the reviewer pane's program has exited.
set -euo pipefail

handoff=$1 round=$2 block=$3 timeout=${4:-540}
marker="$handoff/round-$round.done"
findings="$handoff/round-$round.findings.md"
deadline=$(( $(date +%s) + timeout ))

while (( $(date +%s) < deadline )); do
  if [[ -e $marker && -s $findings ]]; then
    echo "$findings"
    exit 0
  fi
  if state=$(rex block call -s "$REX_SESSION" -b "$block" process 2>/dev/null); then
    if [[ $(printf '%s' "$state" | jq -r '.last_exit != null') == true ]]; then
      echo "reviewer exited: $(printf '%s' "$state" | jq -c '.last_exit')" >&2
      exit 3
    fi
  else
    echo "reviewer pane $block no longer exists" >&2
    exit 3
  fi
  sleep 5
done

echo "timed out after ${timeout}s waiting for $marker" >&2
exit 2
