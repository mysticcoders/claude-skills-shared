#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["click>=8.1"]
# ///
"""Boil a repository's recent agent sessions and GitHub activity down to digests a retro can read.

Agent transcripts are hundreds of megabytes; an analysis pass can only afford the signal. This reads
Claude Code transcripts (main sessions and their subagents) and Codex sessions whose working directory
is the repository or one of its worktrees, plus the repository's pull requests, and writes small
Markdown digests: what the owner asked and corrected, what failed and how often, what was denied,
which files churned, which checks went red before green, and where reviews and CI pushed back.

Nothing here calls a model, and nothing leaves the machine except read-only gh API calls. The digests
quote transcripts and are private: they live under ~/.cache/retro and must not be pasted into issues.
"""
import datetime as dt
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import click

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
CODEX_ARCHIVED = Path.home() / ".codex" / "archived_sessions"
CODEX_STATE = Path.home() / ".codex" / "state_5.sqlite"
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(code|token|access_token|refresh_token|id_token|api_key|apikey|key|secret|"
                r"client_secret|password|passwd|sig|signature|auth)=([^&\s\"'<>]{6,})"), r"\1=[redacted]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
                r"xox[abpors]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|ya29\.[0-9A-Za-z_-]{20,}|"
                r"lkn_[A-Za-z0-9]{16,}|knd_[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),
     "[redacted]"),
]
CACHE = Path.home() / ".cache" / "retro"
CORRECTION = re.compile(
    r"\b(no|nope|don'?t|do not|stop|wrong|not what|that'?s not|instead|revert|undo|why (did|do|are) you|"
    r"i said|i asked|again|still (broken|wrong|failing|not)|you (missed|forgot|broke))\b", re.I)
CHECK_COMMAND = re.compile(
    r"(Scripts/test\.sh|swift (test|build)|xcodebuild|tools/check-|ratchets\.py|agent-sensors|pytest|"
    r"pnpm (test|lint|typecheck|check)|npm (test|run)|swiftlint|ruff|mypy|gh pr checks|cargo (test|build))")
HOOK_FEEDBACK = re.compile(r"(Stop hook feedback|[A-Za-z]+ hook (blocking error|feedback)|hook returned blocking|"
                          r"agent sensors found problems)", re.I)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NOISE_PREFIXES = ("<local-command", "<bash-input>", "<bash-stdout>", "<bash-stderr>", "<command-name>",
                  "<command-message>", "<task-notification>", "<system-reminder>", "Caveat:")
MAX_TEXT = 500


def clip(text, limit=MAX_TEXT):
    """Collapse whitespace and truncate text for a digest line."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_time(value):
    """Parse an ISO timestamp or epoch seconds into an aware datetime, or None."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return dt.datetime.fromtimestamp(int(value), dt.timezone.utc)
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def encode_project(path):
    """Return the directory name Claude Code uses under ~/.claude/projects for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def repo_paths(repo):
    """Return the repository root and every worktree path git knows for it."""
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo, capture_output=True, text=True)
    paths = {str(repo)}
    for line in out.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(line[len("worktree "):])
    return paths


def claude_project_dirs(repo, paths):
    """Find Claude Code project folders for the repository, its worktrees and agent worktrees inside it."""
    names = {encode_project(p) for p in paths}
    nested = encode_project(Path(repo) / ".claude" / "worktrees") + "-"
    found = []
    if CLAUDE_PROJECTS.exists():
        for folder in CLAUDE_PROJECTS.iterdir():
            if folder.is_dir() and (folder.name in names or folder.name.startswith(nested)):
                found.append(folder)
    return sorted(found)


def in_window(first, last, since, until):
    """Return whether a session's time span overlaps the window."""
    return bool(first and last and last >= since and first <= until)


class Digest:
    """Signals extracted from one agent session, rendered as a compact Markdown digest."""

    def __init__(self, agent, session_id, source):
        self.agent = agent
        self.session_id = session_id
        self.source = source
        self.label = ""
        self.cwd = ""
        self.branches = Counter()
        self.first = None
        self.last = None
        self.human = []
        self.interrupts = []
        self.errors = []
        self.denials = []
        self.hook_feedback = []
        self.edits = Counter()
        self.failed_commands = Counter()
        self.checks = []
        self.compactions = 0
        self.peer_messages = 0
        self.tool_calls = 0
        self.prs = set()
        self.posted_comments = set()
        self.skills = Counter()
        self.subagents = []
        self.parent = None

    def absorb(self, child):
        """Merge a subagent's signals into this digest, keeping the parent's identity and owner messages."""
        self.stamp(child.first)
        self.stamp(child.last)
        self.interrupts += child.interrupts
        self.errors += child.errors
        self.denials += child.denials
        self.hook_feedback += child.hook_feedback
        self.edits.update(child.edits)
        self.failed_commands.update(child.failed_commands)
        self.checks = sorted(self.checks + child.checks, key=lambda c: c[0])
        self.compactions += child.compactions
        self.tool_calls += child.tool_calls
        self.prs |= child.prs
        self.posted_comments |= child.posted_comments
        if child.label and child.label not in self.subagents:
            self.subagents.append(child.label)

    def stamp(self, when):
        """Widen the session's time span to include a timestamp."""
        if when:
            self.first = when if not self.first or when < self.first else self.first
            self.last = when if not self.last or when > self.last else self.last

    def note_command(self, command, failed, when, output=""):
        """Record a shell command's outcome: check runs, repeated failures, PR comments it posted."""
        command = clip(command, 220)
        if CHECK_COMMAND.search(command):
            self.checks.append((when, "fail" if failed else "pass", command))
        if failed:
            self.failed_commands[re.sub(r"\d{3,}", "N", command)] += 1
        for match in re.finditer(r"gh (?:pr (?:comment|review)\s+|issue comment\s+|api \S*/(?:issues|pulls)/)(\d+)",
                                 command):
            self.posted_comments.add(int(match.group(1)))
        for match in re.finditer(r"github\.com/[^/\s]+/[^/\s]+/pull/(\d+)", output or ""):
            self.prs.add(int(match.group(1)))

    def check_cycles(self):
        """Count fail-then-pass cycles per check command, the cost of finding problems late."""
        state, cycles = {}, Counter()
        for _, result, command in self.checks:
            key = CHECK_COMMAND.search(command).group(0)
            if state.get(key) == "fail" and result == "pass":
                cycles[key] += 1
            state[key] = result
        return cycles

    def render(self):
        """Render the digest as Markdown, capped so a subagent can read many of them."""
        duration = (self.last - self.first) if self.first and self.last else dt.timedelta()
        lines = [f"# {self.agent} session {self.session_id}",
                 f"- source: {self.source}",
                 f"- label: {self.label or '-'}",
                 f"- cwd: {self.cwd}  branches: {', '.join(b for b, _ in self.branches.most_common(5)) or '-'}",
                 f"- span: {self.first:%Y-%m-%d %H:%M} to {self.last:%Y-%m-%d %H:%M} UTC ({duration})",
                 f"- tool calls: {self.tool_calls}  human messages: {len(self.human)}  interrupts: "
                 f"{len(self.interrupts)}  errors: {len(self.errors)}  denials: {len(self.denials)}  "
                 f"compactions: {self.compactions}  peer messages: {self.peer_messages}",
                 f"- PRs linked: {', '.join(f'#{n}' for n in sorted(self.prs)) or '-'}",
                 f"- PRs it commented on: {', '.join(f'#{n}' for n in sorted(self.posted_comments)) or '-'}",
                 f"- skills used: {', '.join(f'{k} x{v}' for k, v in self.skills.most_common()) or '-'}"]
        if self.subagents:
            lines.append(f"- subagents: {len(self.subagents)} ("
                         + "; ".join(clip(s, 80) for s in self.subagents[:12]) + ")")
        sections = [
            ("Owner messages (C = looks like a correction)",
             [f"{w:%m-%d %H:%M} {'C ' if CORRECTION.search(t) else ''}{clip(t)}" for w, t in self.human[:80]]),
            ("Interrupts", [f"{w:%m-%d %H:%M}" for w in self.interrupts[:30]]),
            ("Permission denials", [f"{w:%m-%d %H:%M} [{k}] {clip(c, 200)}" for w, k, c in self.denials[:30]]),
            ("Hook feedback", [f"{w:%m-%d %H:%M} {clip(t, 400)}" for w, t in self.hook_feedback[:20]]),
            ("Tool errors", [f"{w:%m-%d %H:%M} {tool}: {clip(inp, 160)} -> {clip(err, 240)}"
                             for w, tool, inp, err in self.errors[:60]]),
            ("Commands that failed more than once", [f"x{n} {c}" for c, n in self.failed_commands.most_common(20) if n > 1]),
            ("Check fail-to-pass cycles", [f"x{n} {c}" for c, n in self.check_cycles().most_common()]),
            ("Files edited five or more times", [f"x{n} {p}" for p, n in self.edits.most_common(25) if n >= 5]),
        ]
        for title, items in sections:
            if items:
                lines += ["", f"## {title}", *[f"- {i}" for i in items]]
        return "\n".join(lines) + "\n"


def text_of(content):
    """Return the text parts of a Claude message content field."""
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def tool_input_summary(name, data):
    """Summarize a tool call's input: the command, file path or description that identifies it."""
    if not isinstance(data, dict):
        return clip(data, 200)
    for key in ("command", "file_path", "path", "pattern", "url", "description", "skill", "query"):
        if data.get(key):
            return f"{key}={clip(data[key], 200)}"
    return clip(json.dumps(data), 200)


def read_claude_file(path, digest, since, until):
    """Fold one Claude Code transcript file into a digest; returns False if it has no records in the window."""
    calls = {}
    any_record = False
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = record.get("type")
            when = parse_time(record.get("timestamp"))
            if kind in ("ai-title", "custom-title"):
                digest.label = record.get("customTitle") or record.get("aiTitle") or record.get("title") or digest.label
            if kind == "pr-link":
                number = record.get("prNumber") or record.get("number")
                if number:
                    digest.prs.add(int(number))
            if kind not in ("user", "assistant", "system") or not when or not (since <= when <= until):
                continue
            any_record = True
            digest.stamp(when)
            digest.cwd = digest.cwd or record.get("cwd", "")
            if record.get("gitBranch"):
                digest.branches[record["gitBranch"]] += 1
            if kind == "system":
                if "compact" in str(record.get("subtype", "")):
                    digest.compactions += 1
                continue
            content = (record.get("message") or {}).get("content")
            if kind == "assistant":
                for block in content or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        digest.tool_calls += 1
                        name, data = block.get("name"), block.get("input") or {}
                        calls[block.get("id")] = (name, data)
                        if name in EDIT_TOOLS and isinstance(data, dict) and data.get("file_path"):
                            digest.edits[data["file_path"]] += 1
                        if name == "Skill" and isinstance(data, dict):
                            digest.skills[data.get("skill", "?")] += 1
                continue
            origin = record.get("origin") or {}
            text = text_of(content)
            if origin.get("kind") == "peer":
                digest.peer_messages += 1
            if "[Request interrupted by user" in text:
                digest.interrupts.append(when)
            elif origin.get("kind") == "human" and text and not text.lstrip().startswith(NOISE_PREFIXES):
                digest.human.append((when, text))
            if HOOK_FEEDBACK.search(text):
                digest.hook_feedback.append((when, text))
            if record.get("toolDenialKind"):
                result = record.get("toolUseResult")
                digest.denials.append((when, record["toolDenialKind"], clip(result, 200)))
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                name, data = calls.get(block.get("tool_use_id"), ("?", {}))
                body = block.get("content")
                body = body if isinstance(body, str) else text_of(body) or json.dumps(body)[:600]
                if HOOK_FEEDBACK.search(body[:400]):
                    digest.hook_feedback.append((when, body))
                if name == "Bash" and isinstance(data, dict):
                    digest.note_command(data.get("command", ""), bool(block.get("is_error")), when, body)
                if block.get("is_error"):
                    digest.errors.append((when, name, tool_input_summary(name, data), body))
    return any_record


def collect_claude(repo, paths, since, until):
    """Build digests for Claude Code sessions in the window, folding each session's subagents into it."""
    digests = []
    for folder in claude_project_dirs(repo, paths):
        for transcript in sorted(folder.glob("*.jsonl")):
            if dt.datetime.fromtimestamp(transcript.stat().st_mtime, dt.timezone.utc) < since:
                continue
            digest = Digest("claude", transcript.stem, f"{folder.name}/{transcript.name}")
            if not read_claude_file(transcript, digest, since, until):
                continue
            for sub in sorted((folder / transcript.stem / "subagents").glob("*.jsonl")):
                meta_path = sub.with_suffix(".meta.json")
                meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
                digest.subagents.append(meta.get("description") or meta.get("agentType") or sub.stem)
                read_claude_file(sub, digest, since, until)
            digests.append(digest)
    return digests


def scrub(text):
    """Redact credentials (URL token parameters, bearer tokens, private keys, known key prefixes) from digest text."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def open_lines(path):
    """Yield the text lines of a session file, decompressing zstd (.zst) rollouts through the zstd CLI."""
    if path.suffix == ".zst":
        proc = subprocess.Popen(["zstd", "-dcq", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace")
        try:
            yield from proc.stdout
        finally:
            proc.stdout.close()
            proc.kill()
            proc.wait()
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        yield from handle


def codex_candidates(paths):
    """List Codex rollout files to scan: indexed by cwd from state_5.sqlite when present, else every rollout on disk."""
    found = []
    if CODEX_STATE.exists():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{CODEX_STATE}?mode=ro", uri=True)
            rows = con.execute("select rollout_path, cwd from threads").fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        for rollout, cwd in rows:
            cwd = str(cwd or "").removeprefix("file://")
            if not any(cwd == p or cwd.startswith(p + "/") for p in paths) or not rollout:
                continue
            base = Path(rollout)
            name = base.name.removesuffix(".zst")
            options = [base, Path(str(base) + ".zst"), CODEX_ARCHIVED / name, CODEX_ARCHIVED / (name + ".zst")]
            match = next((o for o in options if o.exists()), None)
            if match:
                found.append(match)
        if found:
            return sorted(set(found))
    for root in (CODEX_SESSIONS, CODEX_ARCHIVED):
        if root.exists():
            found += list(root.rglob("*.jsonl")) + list(root.rglob("*.jsonl.zst"))
    return sorted(set(found))


def codex_meta(path):
    """Return a Codex session's metadata record payload (cwd, git, parent thread), or an empty dict."""
    for _, raw in zip(range(20), open_lines(path)):
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "session_meta":
            return record.get("payload") or {}
    return {}


def collect_codex(paths, since, until):
    """Build digests for Codex sessions whose working directory is the repository or a worktree."""
    digests = []
    for session in codex_candidates(paths):
        if dt.datetime.fromtimestamp(session.stat().st_mtime, dt.timezone.utc) < since:
            continue
        meta = codex_meta(session)
        cwd = str(meta.get("cwd", "")).removeprefix("file://")
        if not any(cwd == p or cwd.startswith(p + "/") for p in paths):
            continue
        stem = session.name.removesuffix(".zst").removesuffix(".jsonl")
        try:
            where = str(session.relative_to(CODEX_SESSIONS))
        except ValueError:
            where = str(session.relative_to(CODEX_ARCHIVED.parent))
        digest = Digest("codex", str(meta.get("id") or stem.split("-", 6)[-1]), where)
        digest.cwd = cwd
        digest.parent = meta.get("parent_thread_id")
        digest.label = meta.get("agent_path") or ""
        branch = (meta.get("git") or {}).get("branch")
        if branch:
            digest.branches[branch] += 1
        for raw in open_lines(session):
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            when = parse_time(record.get("timestamp"))
            payload = record.get("payload") or {}
            if record.get("type") != "event_msg" or not when or not (since <= when <= until):
                continue
            digest.stamp(when)
            if payload.get("type") == "turn_aborted":
                digest.interrupts.append(when)
                continue
            item = payload.get("item") or {} if payload.get("type") == "item_completed" else {}
            kind = item.get("type")
            if kind == "UserMessage":
                text = " ".join(c.get("text", "") for c in item.get("content") or [] if isinstance(c, dict))
                if text and not text.lstrip().startswith(("# AGENTS.md", "<environment_context", "<user_instructions")):
                    digest.human.append((when, text))
            elif kind == "CommandExecution":
                digest.tool_calls += 1
                command = item.get("command")
                command = command[-1] if isinstance(command, list) else command
                failed = str(item.get("exit_code", "0")) not in ("0", "None") or item.get("status") == "failed"
                digest.note_command(command or "", failed, when, item.get("aggregated_output", ""))
                if failed:
                    digest.errors.append((when, "exec", clip(command, 160),
                                          f"exit {item.get('exit_code')}: {item.get('aggregated_output', '')}"))
            elif kind == "FileChange":
                digest.tool_calls += 1
                for file in (item.get("changes") or {}):
                    digest.edits[file] += 1
            elif kind == "McpToolCall":
                digest.tool_calls += 1
                if item.get("status") == "failed":
                    digest.errors.append((when, f"mcp:{item.get('server')}.{item.get('tool')}",
                                          clip(item.get("arguments"), 160), clip(item.get("result"), 240)))
            elif kind == "ContextCompaction":
                digest.compactions += 1
            elif kind == "SubAgentActivity" and item.get("kind") == "started":
                digest.subagents.append(item.get("agent_path") or "subagent")
        if digest.first:
            digests.append(digest)
    return fold_codex_subagents(digests)


def fold_codex_subagents(digests):
    """Fold Codex subagent threads into their parent session's digest, as Claude subagents already are."""
    by_id = {d.session_id: d for d in digests}
    kept = []
    for digest in digests:
        parent = by_id.get(digest.parent) if digest.parent else None
        if parent and parent is not digest:
            parent.absorb(digest)
        else:
            kept.append(digest)
    return kept


def gh_json(repo, *args):
    """Run a read-only gh command in the repository and parse its JSON output, or return None."""
    result = subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return None


def collect_github(repo, since, max_prs):
    """Summarize PRs updated in the window: review pushback, CI failures before green, fixes after opening."""
    owner = (gh_json(repo, "api", "user") or {}).get("login", "")
    name = (gh_json(repo, "repo", "view", "--json", "nameWithOwner") or {}).get("nameWithOwner", "")
    prs = gh_json(repo, "pr", "list", "--state", "all", "--limit", str(max_prs), "--search",
                  f"updated:>={since:%Y-%m-%d}", "--json",
                  "number,title,state,createdAt,mergedAt,headRefName,additions,deletions,author") or []
    lines = [f"# GitHub activity for {name or repo} since {since:%Y-%m-%d}",
             f"- owner account: {owner or '?'} (agents also post as this account; see each session's "
             "'PRs it commented on')", f"- PRs: {len(prs)}", ""]
    for pr in sorted(prs, key=lambda p: p["number"]):
        number = pr["number"]
        detail = gh_json(repo, "pr", "view", str(number), "--json", "reviews,comments,commits") or {}
        inline = gh_json(repo, "api", f"repos/{name}/pulls/{number}/comments", "--paginate") or []
        runs = gh_json(repo, "run", "list", "--branch", pr["headRefName"], "--limit", "40", "--json",
                       "conclusion,workflowName,createdAt,headSha") or []
        created = parse_time(pr["createdAt"])
        later = [c for c in detail.get("commits", []) if (parse_time(c.get("committedDate")) or created) > created]
        failed = [r for r in runs if r.get("conclusion") in ("failure", "cancelled", "timed_out")]
        lines.append(f"## #{number} {clip(pr['title'], 120)}")
        lines.append(f"- {pr['state']}, +{pr['additions']}/-{pr['deletions']}, branch {pr['headRefName']}, "
                     f"author {pr['author'].get('login')}, commits after opening: {len(later)}, "
                     f"failed CI runs: {len(failed)} of {len(runs)}")
        for run in failed[:8]:
            lines.append(f"  - CI {run.get('conclusion')}: {run.get('workflowName')} at {run.get('createdAt')}")
        for review in detail.get("reviews", []):
            if review.get("body") or review.get("state") not in ("COMMENTED",):
                lines.append(f"  - review {review.get('state')} by {review.get('author', {}).get('login')}: "
                             f"{clip(review.get('body'), 400)}")
        for comment in inline:
            lines.append(f"  - line comment by {comment.get('user', {}).get('login')} on {comment.get('path')}:"
                         f"{comment.get('line') or comment.get('original_line')}: {clip(comment.get('body'), 400)}")
        for comment in detail.get("comments", []):
            lines.append(f"  - comment by {comment.get('author', {}).get('login')}: {clip(comment.get('body'), 300)}")
        lines.append("")
    return "\n".join(lines) + "\n", len(prs)


def collect_rules(repo):
    """List the repository's rule sources so analysts can check whether a finding is already covered."""
    candidates = ["AGENTS.md", "CLAUDE.md", "docs/glossary.md", "docs/adr/README.md", ".swiftlint.yml",
                  "tools/ratchets.py", "tools/ratchet-baseline.json", "tools/agent-sensors.py",
                  ".claude/settings.json", "eslint.config.mjs", "scripts/agent-sensors.mjs"]
    present = [c for c in candidates if (Path(repo) / c).exists()]
    lines = ["# Rule sources in this repository", *[f"- {p}" for p in present]]
    baseline = Path(repo) / "tools" / "ratchet-baseline.json"
    if baseline.exists():
        raised = json.loads(baseline.read_text()).get("raised", [])
        lines += ["", "## Ratchet raises", *[f"- {r}" for r in raised]] if raised else []
    return "\n".join(lines) + "\n"


@click.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
@click.option("--since", help="Start of the window (YYYY-MM-DD). Default: the last retro, else 7 days ago.")
@click.option("--until", "until_", help="End of the window (YYYY-MM-DD). Default: now.")
@click.option("--max-prs", default=60, show_default=True, help="Most PRs to summarize from GitHub.")
@click.option("--no-github", is_flag=True, help="Skip GitHub; digest transcripts only.")
@click.option("--mark-done", is_flag=True, help="Record now as the last retro for this repository and exit.")
def main(repo, since, until_, max_prs, no_github, mark_done):
    """Write retro digests for REPO under ~/.cache/retro/<repo>/<date>/ and print an index."""
    repo = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True,
                               text=True, check=True).stdout.strip())
    state = CACHE / repo.name / "last-run"
    now = dt.datetime.now(dt.timezone.utc)
    if mark_done:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(now.isoformat())
        click.echo(f"Recorded {now:%Y-%m-%d %H:%M} UTC as the last retro for {repo.name}.")
        return
    start = parse_time(since + "T00:00:00+00:00") if since else (
        parse_time(state.read_text().strip()) if state.exists() else now - dt.timedelta(days=7))
    end = parse_time(until_ + "T23:59:59+00:00") if until_ else now
    out = CACHE / repo.name / f"{now:%Y-%m-%d-%H%M}"
    (out / "sessions").mkdir(parents=True, exist_ok=True)
    paths = repo_paths(repo)
    digests = collect_claude(repo, paths, start, end) + collect_codex(paths, start, end)
    oldest = min((d.first for d in digests if d.first), default=None)
    index = [f"# Retro digests for {repo.name}", f"- window: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC",
             f"- sessions: {len(digests)} (claude {sum(d.agent == 'claude' for d in digests)}, "
             f"codex {sum(d.agent == 'codex' for d in digests)})"]
    if oldest and oldest > start + dt.timedelta(days=2):
        index.append(f"- WARNING: the oldest session found starts {oldest:%Y-%m-%d}, well after the window "
                     "opens. Older transcripts were probably deleted (Claude Code's cleanupPeriodDays).")
    index += ["", "| digest | agent | span | tool calls | owner msgs | corrections | interrupts | errors | "
              "denials | PRs | label |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for digest in sorted(digests, key=lambda d: d.first):
        file = out / "sessions" / f"{digest.first:%m%d-%H%M}-{digest.agent}-{digest.session_id[:8]}.md"
        file.write_text(scrub(digest.render()), encoding="utf-8")
        corrections = sum(1 for _, t in digest.human if CORRECTION.search(t))
        index.append(f"| {file.name} | {digest.agent} | {digest.first:%m-%d %H:%M}–{digest.last:%m-%d %H:%M} | "
                     f"{digest.tool_calls} | {len(digest.human)} | {corrections} | {len(digest.interrupts)} | "
                     f"{len(digest.errors)} | {len(digest.denials)} | {' '.join(f'#{n}' for n in sorted(digest.prs)[:6])} | "
                     f"{clip(digest.label, 50)} |")
    if not no_github:
        github, count = collect_github(repo, start, max_prs)
        (out / "github.md").write_text(scrub(github), encoding="utf-8")
        index.append(f"\n- github.md: {count} PRs")
    (out / "rules.md").write_text(collect_rules(repo), encoding="utf-8")
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    click.echo(f"{out}\n" + "\n".join(index))


if __name__ == "__main__":
    main()
