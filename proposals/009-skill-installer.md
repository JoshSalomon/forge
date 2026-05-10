# Proposal: Skill Packages via Jira Project Metadata

**Author:** eshulman2
**Date:** 2026-04-29
**Status:** Implemented

## Summary

Store skill package sources as Jira project properties so the worker automatically fetches and installs the right skills for each project without any CLI intervention or `.env` changes. A CLI command remains available as a secondary tool for local development and one-off installs.

## Motivation

### Problem Statement

Skills are the primary customization mechanism in Forge. Every team using Forge needs custom skills tuned to their stack. The current path is to manually author skill files in `skills/<project>/`. Several pain points arise:

1. **No external sourcing.** There is no way to pull skills from a shared git repo — only copy-paste.
2. **No per-project config.** Skill configuration, like repo config, is global (`.env`). Two projects cannot have different skill sources without separate deployments.
3. **Bootstrap friction.** A new project has no skills until someone manually authors them.
4. **Operational overhead.** Installing or updating skills requires shelling into the host and restarting nothing (the worker already re-resolves skills per task), but there is no standard mechanism to do it.

### Current Workarounds

Teams copy skill files manually between projects or maintain a single shared `skills/default/` that grows to cover all stacks.

## Proposal

### Overview

Use Jira project properties to store a list of skill package sources for each project. When the worker processes a ticket, it reads the project's `forge.skills` property, fetches any packages not yet present (or at a stale commit), copies their skill directories into `skills/<project>/`, and proceeds. The resolver and container runner are unchanged.

Jira project properties are key-value metadata on the project itself, managed via the Jira REST API. They are already scoped per project, editable by project admins, and readable with the existing Jira credentials.

### Detailed Design

#### Jira Project Property

Property key: `forge.skills`

Value (JSON):
```json
[
  {
    "source": "https://github.com/acme/forge-skills-python",
    "ref": "v1.2.0",
    "path": ""
  },
  {
    "source": "https://github.com/acme/tooling",
    "ref": "a3f8c21",
    "skill_mapping": {
      "generate-prd": "docs/prompts/prd",
      "implement-task": "ai/skills/implement"
    }
  }
]
```

`ref` is optional. If omitted, the worker fetches `HEAD` of the default branch. `ref` can be a tag, branch, or commit SHA. Each entry must have either `path` or `skill_mapping`, not both.

#### Skill Package Format

There are two modes for sourcing skills from a repo, controlled by which field is present in the property entry. They are mutually exclusive.

**`path` mode — dedicated skill repos:**

Use when the source repo is structured exclusively as a Forge skill package. Every immediate subdirectory under `path` is treated as a skill and copied wholesale — no filtering, no `SKILL.md` discovery. The expectation is that the repo author has ensured only skill directories exist under that path.

```
forge-skills-python/         ← repo root
  generate-prd/
    SKILL.md
    prd-template.md
  implement-task/
    SKILL.md
```

Property entry:
```json
{"source": "https://github.com/acme/forge-skills-python", "ref": "v1.2", "path": ""}
```
`path` may be a subdirectory if skills are not at the repo root:
```json
{"source": "https://github.com/acme/monorepo", "ref": "v1", "path": "forge-skills/"}
```

**`skill_mapping` mode — any repo:**

Use when the source repo is not structured for Forge, or when you want to consume only specific skills from a larger repo. `skill_mapping` maps the installed skill name to a directory path (relative to the repo root) that contains a `SKILL.md`.

```json
{
  "source": "https://github.com/acme/tooling",
  "ref": "v1",
  "skill_mapping": {
    "generate-prd": "docs/prompts/prd",
    "implement-task": "ai/skills/implement"
  }
}
```

Each value must be a directory containing a `SKILL.md`. The installed skill name (key) is what appears in `skills/<project>/` on disk. The source repo requires no Forge-specific structure or manifest files.

#### Worker Fetch Flow

At the start of `setup_workspace` (before any skill resolution), the worker:

1. Reads the `forge.skills` project property via Jira API, keyed on the project extracted from the ticket key.
2. Compares each entry against `skills/skills.lock`. If the source and resolved ref match the lock, skip. Otherwise fetch.
3. For each stale or missing entry: clone into a temp dir, then copy, then delete the temp dir:
   - If `ref` is a branch or tag: `git clone --depth 1 --branch <ref> <url> <tempdir>`
   - If `ref` is a commit SHA: `git clone <url> <tempdir> && git -C <tempdir> checkout <sha>`
   - **`path` mode:** copy every immediate subdirectory of `<tempdir>/<path>/` into `skills/<project>/`.
   - **`skill_mapping` mode:** for each `skill_name → dir` pair, verify `<tempdir>/<dir>/SKILL.md` exists, then copy `<tempdir>/<dir>/` to `skills/<project>/<skill_name>/`.
   - Delete `<tempdir>` unconditionally (success or failure).
4. Update the lock entry and proceed to skill resolution as normal.

Step 1 result is cached in memory (keyed by project key) for the lifetime of the worker process to avoid a Jira API call on every task.

#### Lock File

`skills/skills.lock` tracks what was fetched and when, for auditability:

```yaml
packages:
  - source: https://github.com/acme/forge-skills-python
    ref: v1.2.0
    resolved_commit: a3f8c21
    mode: path
    path: ""
    target: myproj
    skills:
      - generate-prd
      - implement-task
    fetched_at: 2026-04-29T14:00:00Z
  - source: https://github.com/acme/tooling
    ref: main
    resolved_commit: b9d4e12
    mode: skill_mapping
    skill_mapping:
      generate-prd: docs/prompts/prd
    target: myproj
    skills:
      - generate-prd
    fetched_at: 2026-04-29T14:00:00Z
```

The lock is updated on the host by the worker. It is not used to gate fetches (the project property is the source of truth); it exists for auditing and debugging.

#### Jira Client Addition

A new method on `JiraClient`:

```python
async def get_project_property(self, project_key: str, property_key: str) -> Any | None:
    """Fetch a project-level property value, or None if not set."""
```

Using the existing Jira REST endpoint: `GET /rest/api/3/project/{projectKey}/properties/{propertyKey}`.

#### CLI as Secondary Tool

`forge skills install <source> --project <key>` remains available for:
- Local development and testing of new skill packages before publishing
- One-off installs from local paths (not suitable for Jira property storage)
- Environments where the project property is not set

It performs the same fetch-and-copy logic but writes directly to `skills/<project>/` and updates the lock file, without touching Jira.

```
forge skills install <source> (--project <key> | --default) [--ref <ref>]
forge skills list
forge skills update [--project <key>]
```

### User Experience

**Setting up skills for a new project (Jira UI or API):**
```
Project: MYPROJ
Property key: forge.skills
Value:
[
  {"source": "https://github.com/acme/forge-skills-python", "ref": "v1.2", "path": ""},
  {"source": "https://github.com/acme/tooling", "ref": "main", "skill_mapping": {"generate-prd": "docs/prompts/prd"}}
]
```

**Worker log on next task for MYPROJ-101:**
```
INFO  Fetching skill packages for project MYPROJ
INFO  forge-skills-python: fetching v1.2 from github.com/acme/forge-skills-python
INFO  forge-skills-python: installed generate-prd, implement-task (2 skills)
INFO  Skills resolved for MYPROJ-101: [skills/default/, skills/myproj/]
```

**Subsequent tasks (cache hit):**
```
INFO  Skills resolved for MYPROJ-102: [skills/default/, skills/myproj/]
```

**Local install for testing:**
```bash
$ forge skills install ./my-draft-skill --project myproj
Installed 1 skill into skills/myproj/: generate-prd (overwrote existing)
```

## Alternatives Considered

| Alternative | Pros | Cons | Why Not |
|-------------|------|------|---------|
| CLI-only installer | Simple, no Jira dependency | Manual step on every update, per-host | Operational friction defeats the purpose |
| `gh skill` (GitHub CLI) | Existing tool, maintained | Targets `.claude/skills/`, not Forge dirs; adds `gh` dependency | Wrong target, wrong abstraction |
| `.env` skill sources | Simple | Global, not per-project; requires restart awareness | Same problem as `GITHUB_KNOWN_REPOS` |
| Git submodules | Native git tracking | Complex UX, merge conflicts | Installer abstraction is simpler |

## Implementation Plan

### Phases

1. **Phase 1: Jira project property client** — 1 day
   - Add `get_project_property` to `JiraClient`.
   - Add in-memory cache (dict keyed by project key, populated lazily).

2. **Phase 2: Fetch and install logic** — 2 days
   - `src/forge/skills/installer.py`: fetch, copy, lock file read/write.
   - Wire into `setup_workspace` node before skill resolution.

3. **Phase 3: CLI sub-command** — 1 day
   - Add `forge skills install / list / update` under `cli.py`.
   - Reuses installer module from Phase 2.

### Dependencies

- [ ] `git` on host (already assumed by workspace manager)
- [ ] `skills/skills.lock` added to `.gitignore` or committed — recommend committed for auditability

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Malicious skill package replaces built-in skills | Low | Medium | Skills affect container prompts only, not orchestrator logic; project admins control the property |
| Jira API unavailable at task start | Low | Low | Fall back to whatever is already on disk; log a warning |
| Skill update mid-sprint changes agent behavior unexpectedly | Medium | Medium | Pin to a tag or commit SHA in the property value |
| Local modifications to installed skills overwritten on update | Medium | Medium | Log a warning when overwriting a file with local git changes |
| `skill_mapping` entry points to a dir without `SKILL.md` | Low | Low | Validate on fetch; log an error and skip the entry rather than copying an incomplete skill |

## Open Questions

- [ ] Should the in-memory cache have a TTL (e.g. 1 hour) so long-running workers pick up property changes without restart?
- [ ] Should `forge skills update` also push the resolved commit back to the Jira property (replacing a branch ref with a commit SHA for reproducibility)?
- [ ] Should overwriting locally modified skill files on update be a hard error or a warning?

## References

- Current resolver: `src/forge/skills/resolver.py`
- Proposal 004 (dynamic skill loading, implemented): `proposals/004-dynamic-skill-loading.md`
- Proposal 010 (project metadata for repo config): `proposals/010-project-metadata-repos.md`
