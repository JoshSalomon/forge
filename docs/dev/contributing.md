# Contributing to Forge

Thanks for your interest. This document explains what kind of contributions Forge welcomes and how to make them.

## Have an idea or need help?

Open a [GitHub issue](https://github.com/Forge-sdlc/forge/issues). Issues are the right place for feature ideas, questions, bug reports, and anything else you want to discuss before writing code. If your idea is larger in scope — a new workflow type, a significant change to the pipeline, a new integration — you can also submit a proposal following the format in `proposals/TEMPLATE.md`. Proposals give everyone a chance to read, comment, and align before implementation starts.

The point is to work on things together. An early conversation usually leads to a better outcome than a PR that surprises everyone.

## Customize Forge for your project

Skills are the primary customization mechanism — they define how Forge generates PRDs, analyzes CI failures, implements code, and more. Since the [skill installer](https://github.com/forge-sdlc/forge/pull/34) landed, teams keep their skills in their own Git repository and point Forge at it via a Jira project property (think of a _plugin_). You never need to fork the Forge repo to customize behavior.

### 1. Author your skills

Create a Git repository (anywhere) with one directory per skill you want to override:

```
forge-skills-myteam/
├── generate-prd/
│   ├── SKILL.md
│   └── prd-template.md
└── analyze-ci/
    └── SKILL.md
```

You only need the skills you're actually changing. Any skill you don't provide falls back to `skills/default/` automatically. See the [Skills Authoring Guide](../skills/authoring.md) for what belongs in a skill.

### 2. Configure your Jira project

Use `forge project-setup` to set repos and skill sources in one command:

```bash
forge project-setup MYPROJ \
  --repo myorg/myrepo \
  --default-repo myorg/myrepo \
  --add-skill source=https://github.com/myorg/forge-skills-myteam,ref=v1.0,path=
```

This writes three Jira project properties — `forge.repos`, `forge.default_repo`, and `forge.skills` — that Forge reads per ticket.

For a monorepo where skills live in a subdirectory, use `skill_mapping` mode instead of `path`:

```bash
forge project-setup MYPROJ \
  --add-skill source=https://github.com/myorg/tooling,ref=main,mapping=generate-prd:ai/prompts/prd
```

### 3. Auto-sync

The Forge worker reads the `forge.skills` project property at the start of every workflow and fetches any packages that have changed (SHA-based comparison against `skills/skills.lock`). No restart needed — push a new tag to your skills repo, update the `ref` via `forge project-setup`, and the next workflow run picks it up.

### Local development

While authoring skills, install directly from a local path:

```bash
# From a local directory
forge skills install /path/to/forge-skills-myteam --project MYPROJ

# From a Git URL (one-off, without setting the Jira property)
forge skills install https://github.com/myorg/forge-skills-myteam --project MYPROJ --ref v1.0

# See what's installed
forge skills list

# Re-fetch everything in the lock file to pick up upstream changes
forge skills update --project MYPROJ
```

## Other ways to contribute

### Bug fixes

If something in the core workflow is broken, a focused fix with a test is always welcome. Keep the scope tight — a bug fix should fix the bug and nothing else.

### Default skill improvements

The skills in `skills/default/` should work for any software project regardless of stack. If you find something in a default skill that's OpenShift-specific, Java-specific, or otherwise not genuinely general — that's a bug. Fix it and submit a PR.

If you want to improve a default skill's quality (better structure, clearer instructions, a missing edge case) — open an issue first to discuss, especially for significant changes that affect everyone.

### New workflow ideas

Open a [GitHub issue](https://github.com/Forge-sdlc/forge/issues) or submit a proposal in `proposals/` before writing any code. An early conversation saves everyone time and means the implementation is more likely to land.

### Documentation

Typos, clarifications, and missing explanations are always welcome.

## Development setup

See the [Developer Guide](../developer-guide.md) for the full local setup, including Redis, the API server, the worker, payload-based testing, and debugging tools.

Before submitting a PR, make sure these pass:

```bash
uv run pytest tests/unit/ -v
uv run ruff check src/
uv run mypy src/forge/
```

## Pull request guidelines

- **One thing per PR.** A skill set, a bug fix, or a doc improvement — not all three.
- **Tests for code changes.** New logic needs tests. Skills don't need tests, but the resolver does.
- **No unrelated cleanup.** If you notice something off while working on your change, open a separate issue.
- **Short description.** What does this change and why? One paragraph is enough.

## Questions

Open a [GitHub issue](https://github.com/Forge-sdlc/forge/issues) — for "how do I" questions, sharing what your team built, or early feedback before you start writing code.
