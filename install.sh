#!/usr/bin/env bash
# Symlink every skill in this repo into ~/.claude/skills/<name>.
#
# Usage: ./install.sh [--dry-run]
#
# Existing links are replaced. A real directory in the way is moved to
# ~/.claude/skills/.backup-<timestamp>/ first, never deleted.
set -euo pipefail

repo=$(cd "$(dirname "$0")" && pwd -P)
dest="$HOME/.claude/skills"
backup="$dest/.backup-$(date +%Y%m%d-%H%M%S)"
dry=0
[[ ${1:-} == --dry-run ]] && dry=1

run() {
  if (( dry )); then echo "would: $*"; else "$@"; fi
}

mkdir -p "$dest"
for src in "$repo"/skills/*/; do
  src=${src%/}
  name=$(basename "$src")
  target="$dest/$name"
  if [[ -L $target ]]; then
    [[ $(readlink "$target") == "$src" ]] && { echo "ok      $name"; continue; }
    run rm "$target"
  elif [[ -e $target ]]; then
    run mkdir -p "$backup"
    run mv "$target" "$backup/$name"
    echo "backup  $name -> $backup/$name"
  fi
  run ln -s "$src" "$target"
  echo "linked  $name"
done
