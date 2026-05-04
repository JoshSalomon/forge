# Proposal: Repository Configuration via Jira Project Metadata

**Author:** eshulman2
**Date:** 2026-04-29
**Status:** Draft

## Summary

Move per-project GitHub repository configuration (`GITHUB_KNOWN_REPOS`, `GITHUB_DEFAULT_REPO`) from global `.env` settings to Jira project properties, making repo assignment project-specific and manageable without touching the host environment.

## Motivation

### Problem Statement

Repository assignment in Forge is global: `GITHUB_KNOWN_REPOS` is a comma-separated list shared across all projects. This causes two problems:

1. **No per-project scoping.** All projects see the same set of repos. A team running `MYPROJ` tickets has no way to restrict or extend repo choices independently of a team running `OTHERPROJ` tickets on the same Forge instance.
2. **Operational friction.** Adding a new repo for one project requires editing `.env` and is visible to all projects. On a shared Forge deployment this is noise at best, a security concern at worst.

The same issue applies to `GITHUB_DEFAULT_REPO` — there is one global default, not one per project.

### Current Workarounds

Teams list all repos from all projects in `GITHUB_KNOWN_REPOS` and rely on the Epic decomposition agent to assign repos correctly from context. This works but is fragile and leaks cross-project repo information.

## Proposal

### Overview

Store per-project repo configuration as Jira project properties, read at task time alongside the existing label-based repo hints. If a project has not set the required metadata, Forge posts a Jira comment on the triggering ticket explaining exactly what needs to be configured, rather than silently falling back to global `.env` values.

### Detailed Design

#### Jira Project Properties

Two property keys on the Jira project:

**`forge.repos`** — the set of repos available to this project for Epic/task assignment:
```json
["acme/backend", "acme/frontend", "acme/infra"]
```

**`forge.default_repo`** — the repo to assign when no explicit assignment is made:
```json
"acme/backend"
```

#### Updated Repo Resolution in Epic Decomposition

Current logic in `epic_decomposition.py` builds `available_repos` from:
1. Feature ticket labels (`repo:owner/repo-name`)
2. `settings.known_repos` (global)

Updated logic:
1. Feature ticket labels (`repo:owner/repo-name`)
2. `forge.repos` Jira project property (project-specific, required)

If `forge.repos` is not set on the project, Forge posts a blocking comment on the ticket and halts workflow for that ticket. No fallback to global settings.

`forge.default_repo` project property is similarly required when no label-based repo is resolvable. If absent, the same error comment is posted.

#### Jira Client Addition

Reuses `get_project_property` introduced in proposal 009. No additional Jira client changes needed.

#### Caching

Same in-memory cache strategy as proposal 009: property values are fetched once per project key per worker lifetime, with an optional TTL.

#### Error Behavior When Metadata Is Missing

When `forge.repos` is not set on a Jira project, Forge posts a comment on the triggering ticket and blocks workflow:

```
⚠️ Forge configuration required for project MYPROJ

This ticket cannot be processed because no repository configuration
has been set for this Jira project.

To fix this, a Jira project admin must set the following project property:

  Key:   forge.repos
  Value: ["owner/repo-name", "owner/other-repo"]

Optionally, also set:

  Key:   forge.default_repo
  Value: "owner/repo-name"

Once set, add the label `forge:retry` to this ticket to resume.
```

#### Migration

Existing deployments using `GITHUB_KNOWN_REPOS` / `GITHUB_DEFAULT_REPO` in `.env` must set project properties before Forge will process tickets for those projects. The `.env` variables are no longer read for repo resolution and can be removed after migration.

### User Experience

**Before (global `.env`):**
```env
GITHUB_KNOWN_REPOS=acme/backend,acme/frontend,acme/infra,other/repo
GITHUB_DEFAULT_REPO=acme/backend
```

**After (per project in Jira):**

Project `MYPROJ`:
```
forge.repos        → ["acme/backend", "acme/frontend"]
forge.default_repo → "acme/backend"
```

Project `OTHERPROJ`:
```
forge.repos        → ["other/repo"]
forge.default_repo → "other/repo"
```

**If a project has no metadata set**, Forge posts a comment on the ticket and stops:
```
⚠️ Forge configuration required for project MYPROJ

This ticket cannot be processed because no repository configuration
has been set for this Jira project.
...
```

**Worker log (success):**
```
INFO  Project MYPROJ: repos from Jira property: [acme/backend, acme/frontend]
INFO  Project MYPROJ: default repo: acme/backend
```

**Worker log (missing config):**
```
ERROR Project MYPROJ: forge.repos property not set — posting config instructions to ticket and blocking
```

## Alternatives Considered

| Alternative | Pros | Cons | Why Not |
|-------------|------|------|---------|
| Keep global `.env` | No change | Not per-project, friction to update | The problem is real at scale |
| Repo config in `CLAUDE.md` or a config file per project | Version-controlled | Requires file in every repo, not Jira-native | Forge config should live with the ticket system |
| Jira labels on the project (not properties) | Visible in UI | Labels are for issues, not projects | Wrong API surface |

## Implementation Plan

### Phases

1. **Phase 1: Project property read** — 0.5 days
   - Reuse `get_project_property` from proposal 009 (or implement it first if 009 is not yet merged).
   - Add `get_project_repos(project_key)` and `get_project_default_repo(project_key)` helpers that raise a structured `MissingProjectConfig` exception when the property is absent or malformed.

2. **Phase 2: Wire into epic decomposition** — 0.5 days
   - Replace direct `settings.known_repos` read with `get_project_repos`.
   - Replace `settings.github_default_repo` read with `get_project_default_repo`.
   - Catch `MissingProjectConfig`, post the instructional Jira comment, and set the ticket to `forge:blocked`.
   - Add tests covering property-set, missing-property (error path), and malformed-value cases.

### Dependencies

- [ ] `get_project_property` on `JiraClient` (shared with proposal 009 — whichever lands first provides it)

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Project property not set for an existing project | High (migration period) | Medium | Forge posts clear instructions on the ticket; workflow is blocked but recoverable with `forge:retry` |
| Jira API unavailable | Low | High | Fail fast with an error log; do not attempt repo resolution without confirmed project config |
| Project admin sets an invalid repo name | Low | Medium | Validate `owner/repo` format on read; post a Jira comment listing the malformed entry |

## Open Questions

- [ ] Should we add `forge.fork_owner` as a project property too, to allow per-project fork targets?
- [ ] Should there be a `forge config show <project>` CLI command that dumps all resolved project metadata (repos, default repo, skills) for debugging?

## References

- Current usage: `src/forge/workflow/nodes/epic_decomposition.py` (lines 63–75)
- Current config: `src/forge/config.py` (`github_known_repos`, `known_repos` property)
- Proposal 009 (skill packages via project metadata): `proposals/009-skill-installer.md`
