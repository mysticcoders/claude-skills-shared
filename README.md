# claude-skills-shared

A small set of [Claude Code](https://claude.com/claude-code) skills for reviewing
and shipping code. Most also work as Codex skills.

## Install

```sh
git clone https://github.com/mysticcoders/claude-skills-shared.git ~/workspace/claude-skills-shared
~/workspace/claude-skills-shared/install.sh --dry-run
~/workspace/claude-skills-shared/install.sh
```

`install.sh` symlinks each skill into `~/.claude/skills/`, so `git pull` updates
them in place. Run it again after pulling to pick up new skills. A real directory
already at a skill's path is moved to `~/.claude/skills/.backup-<timestamp>/`,
never deleted.

## Skills

| Skill | What it does | Needs |
| --- | --- | --- |
| `rex-review` | Opens a [Rex](https://www.superlogical.com/rex) split with Codex or Claude as a reviewer and loops review and fix until the reviewer has no important findings | Rex, `codex` or `claude`, `jq`, `gh` for PRs |
| `retro` | Retrospective on a repo's recent agent sessions and PRs: finds where agents struggled and proposes fixes to the environment | `gh`, Python 3 with `uv` |
| `pr-impact-review` | Classifies a PR by structural blast radius (operational, structural, strategic) and reviews to that level | `git`, `gh`, `rg`, `jq` |
| `ship-latest` | Branch, commit, push, open a PR, watch CI to green, squash-merge | `git`, `gh` |
| `wcag` | WCAG 2.1 accessibility audit of a URL or a built `dist/` with axe-core | `uv`, Playwright browsers |

Each skill's `SKILL.md` documents its usage and triggers.

## Contributing

Skills here must not contain personal or client data: names, accounts, home or
vault paths, hostnames, addresses, tokens. Check before committing:

```sh
rg -n -i '/Users/|/home/|token=|api[_-]?key' skills -g '!*.min.js'
```

## License

MIT, see [LICENSE](LICENSE).

### Third-party

- `skills/wcag/src/wcag_audit/axe.min.js` is [axe-core](https://github.com/dequelabs/axe-core)
  v4.10.3, Copyright (c) 2015-2025 Deque Systems, Inc., distributed under the
  [Mozilla Public License 2.0](https://mozilla.org/MPL/2.0/). Its license notice is
  kept at the top of the file.
