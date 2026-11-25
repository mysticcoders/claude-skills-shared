#!/usr/bin/env bash
# pr-signals.sh — compute deterministic structural-impact signals for a PR diff.
#
# Usage:
#   pr-signals.sh [BASE_REF]        # default: origin/main
#
# Emits a single JSON object on stdout. Computes facts only — it does NOT classify.
# Classification is the agent's job (see SKILL.md), because ambiguity must escalate
# and that requires judgment this script deliberately does not exercise.
#
# Reads optional repo config from .pr-impact.toml (see pr-impact.example.toml).
# Exit codes: 0 ok; 1 not a git repo / no diff; 2 bad base ref.

set -euo pipefail

BASE="${1:-origin/main}"

git rev-parse --git-dir >/dev/null 2>&1 || { echo '{"error":"not a git repository"}'; exit 1; }
git rev-parse --verify "$BASE" >/dev/null 2>&1 || { echo "{\"error\":\"unknown base ref: $BASE\"}"; exit 2; }

RANGE="${BASE}...HEAD"
FILES=$(git diff --name-only "$RANGE" || true)
[[ -n "$FILES" ]] || { echo '{"error":"empty diff"}'; exit 1; }

CFG=".pr-impact.toml"
read_globs() {  # read_globs <section-key>
  [[ -f "$CFG" ]] || return 0
  awk -v key="$1" '
    $0 ~ "^"key"[[:space:]]*=" {inlist=1}
    inlist {
      n=split($0, parts, /"/)
      for (i=2; i<=n; i+=2) print parts[i]
      if ($0 ~ /\]/) inlist=0
    }' "$CFG" 2>/dev/null || true
}

BOUNDARY_GLOBS=$(read_globs "boundary_paths")
STRATEGIC_GLOBS=$(read_globs "strategic_paths")

match_globs() {  # match_globs <glob-list>  ; reads FILES
  local globs="$1" f g
  [[ -n "$globs" ]] || return 0
  while IFS= read -r f; do
    while IFS= read -r g; do
      [[ -n "$g" ]] || continue
      case "$f" in $g) echo "$f"; break ;; esac
    done <<< "$globs"
  done <<< "$FILES"
}

# --- workspace / package detection -------------------------------------------
WS_ROOTS=""
if [[ -f pnpm-workspace.yaml ]]; then
  WS_ROOTS=$(awk '/^ *- /{gsub(/[-"'"'"' ]/,""); sub(/\/\*+$/,""); print}' pnpm-workspace.yaml 2>/dev/null || true)
fi
[[ -n "$WS_ROOTS" ]] || WS_ROOTS=$'apps\npackages\nservices\ncrates\nlibs'

pkg_of() {  # first two path segments when under a workspace root, else first segment
  local f="$1" root seg1 seg2 rest
  seg1="${f%%/*}"
  rest="${f#*/}"; seg2="${rest%%/*}"
  while IFS= read -r root; do
    [[ -n "$root" ]] || continue
    if [[ "$seg1" == "$root" && "$f" == */*/* ]]; then echo "$seg1/$seg2"; return; fi
  done <<< "$WS_ROOTS"
  echo "$seg1"
}

PACKAGES=$(while IFS= read -r f; do pkg_of "$f"; done <<< "$FILES" | sort -u)

# --- signal: dependency manifests --------------------------------------------
DEPS=$(echo "$FILES" | grep -E '(^|/)(package\.json|pnpm-lock\.yaml|yarn\.lock|package-lock\.json|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock|requirements.*\.txt|pyproject\.toml|uv\.lock|Gemfile|build\.gradle.*|pom\.xml)$' || true)

# --- signal: schema / wire contracts -----------------------------------------
SCHEMA=$(echo "$FILES" | grep -E '(migrations?/|\.sql$|\.proto$|schema\.(ts|js|py|rb|prisma|graphql|gql)$|openapi.*\.(ya?ml|json)$|\.d\.ts$)' || true)

# --- signal: exported surface changes ----------------------------------------
# Restrict to code files — the keyword regex matches ordinary prose otherwise
CODE_FILES=$(echo "$FILES" | grep -E '\.(ts|tsx|js|jsx|mjs|cjs|go|rs|py|rb|java|kt|swift|c|h|cc|cpp|cs|php|scala|ex|exs)$' || true)
if [[ -n "$CODE_FILES" ]]; then
  # shellcheck disable=SC2086
  EXPORT_HUNKS=$(git diff -U0 "$RANGE" -- $(echo "$CODE_FILES" | tr '\n' ' ') 2>/dev/null \
    | grep -E '^[+-][^+-]' \
    | grep -E '\b(export|public |pub fn|pub struct|pub enum|def |func |class )' \
    | grep -vE '^\+\+\+|^---' || true)
else
  EXPORT_HUNKS=""
fi
EXPORTS_ADDED=$(printf '%s' "$EXPORT_HUNKS" | grep -c '^+' || true); EXPORTS_ADDED="${EXPORTS_ADDED:-0}"
EXPORTS_REMOVED=$(printf '%s' "$EXPORT_HUNKS" | grep -c '^-' || true); EXPORTS_REMOVED="${EXPORTS_REMOVED:-0}"

# --- signal: fan-in (best effort) --------------------------------------------
# For each touched package, count OTHER packages that reference it by name.
FANIN=""
if command -v rg >/dev/null 2>&1; then
  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] || continue
    base="${pkg##*/}"
    hits=$( { rg -l --no-messages -g '!node_modules' -g '!.git' -g '!dist' \
             -e "from ['\"].*${base}" -e "require\(['\"].*${base}" -e "\"${base}\":" . 2>/dev/null || true; } \
           | while IFS= read -r h; do p=$(pkg_of "${h#./}"); [[ "$p" != "$pkg" ]] && echo "$p"; done | sort -u | grep -c . || true )
    hits="${hits:-0}"
    FANIN+="${pkg}=${hits}"$'\n'
  done <<< "$PACKAGES"
fi

BOUNDARY=$(match_globs "$BOUNDARY_GLOBS" | sort -u)
STRATEGIC=$(match_globs "$STRATEGIC_GLOBS" | sort -u)

# Changes to the rules themselves are always strategic.
SELF=$(echo "$FILES" | grep -E '(^|/)(\.pr-impact\.toml|\.github/)' || true)

jq -n \
  --arg base "$BASE" \
  --arg files "$FILES" --arg packages "$PACKAGES" --arg deps "$DEPS" \
  --arg schema "$SCHEMA" --arg boundary "$BOUNDARY" --arg strategic "$STRATEGIC" \
  --arg self "$SELF" --arg fanin "$FANIN" \
  --argjson ea "${EXPORTS_ADDED:-0}" --argjson er "${EXPORTS_REMOVED:-0}" '
  def lines: split("\n") | map(select(length>0));
  {
    base_ref: $base,
    files_changed: ($files|lines),
    packages_touched: ($packages|lines),
    packages_count: ($packages|lines|length),
    deps_changed: ($deps|lines),
    schema_changed: ($schema|lines),
    exports_added: $ea,
    exports_removed: $er,
    boundary_files: ($boundary|lines),
    strategic_files: ($strategic|lines),
    rules_or_ci_touched: ($self|lines),
    fan_in: ($fanin|lines|map(split("=")|{package:.[0], other_packages_importing:(.[1]|tonumber)})),
    notes: [
      "fan_in is best-effort textual matching; verify before relying on a zero.",
      "exports_* counts diff lines, not resolved symbols; a rename shows as +1/-1.",
      "This script does NOT classify. Ambiguity must escalate — see SKILL.md."
    ]
  }'
