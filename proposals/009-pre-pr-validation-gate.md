# Proposal: Pre-PR Validation Gate via Skill-Defined Checks

**Author:** jsalomon
**Date:** 2026-05-03
**Status:** Draft

## Summary

Before opening a pull request, Forge should run project-specific validation checks (lint, type-check, unit tests, build) inside a container and fix failures in a validate→fix→re-validate loop. Today, CI failures are only discovered after the PR is opened, causing noisy push churn on the upstream repo and wasting CI runner time. This proposal adds an opt-in `pre_pr_validation` node that reads check definitions from a `validate-pre-pr` skill, generates a validation script, runs all checks in a single lightweight container, and on failure spawns a separate agent container to fix the issues. The loop repeats until all blocking checks pass or the retry budget is exhausted. `local_review` remains unchanged and runs once after validation passes, performing its normal diff quality review.

## Motivation

### Problem Statement

Forge's current flow is: implement → local_review (code quality only) → create PR → wait for GitHub CI → fix failures → push → re-trigger CI. Each CI fix cycle takes 10–30 minutes of upstream CI runner time, creates a new commit on the PR, and triggers notification noise for reviewers watching the PR. For projects with 5–10 CI checks, a single PR can accumulate 3–5 fix pushes before CI goes green.

The root cause: Forge never runs the project's own validation suite before pushing. The container agent runs tests at its discretion during implementation, but there is no orchestrator-level gate that enforces "lint passes, types check, tests pass" before a PR is created.

This is especially painful for projects with expensive or slow upstream CI (OpenShift, Kubernetes) where each CI run consumes significant shared infrastructure and a failing PR blocks merge queues.

### Current Workarounds

1. The container agent sometimes runs tests during implementation, but this is best-effort — the agent decides what to run and may skip checks.
2. `local_review` reviews the git diff for code quality issues but does not execute any validation commands.
3. After PR creation, `ci_evaluator` detects failures and `attempt_ci_fix` tries to fix them, but each attempt requires a push + full upstream CI re-run.

None of these catch failures before the PR is opened.

## Proposal

### Overview

Add an opt-in `validate-pre-pr` skill that defines structured check commands inside a YAML block. A new `pre_pr_validation` node — inserted between `implement_task` and `local_review` — reads the skill, generates a bash script from the check definitions, and runs all checks in a **single lightweight container** invocation. If blocking checks fail, the node spawns a **separate agent container** (`validation_fix`) with the failure output and the skill's fix guidance to repair the issues. The flow then loops back to `pre_pr_validation` for re-validation. The loop repeats up to `max_cycles` times before either blocking the workflow or proceeding with annotations, as configured per-check via a single `blocking` field (`required`, `best-effort`, or `advisory`).

`local_review` is **not part of the validation loop**. It runs once after all required/best-effort checks pass (or cycles are exhausted), performing its existing job of reviewing the full diff for code quality. This keeps local_review's scope unchanged and avoids compounding its internal retry logic (up to 2 passes) with the validation cycle.

Projects that don't define the skill skip validation entirely — current behavior is preserved.

### Detailed Design

#### Skill format — `validate-pre-pr/SKILL.md`

The skill is a markdown file with an embedded `yaml:checks` fenced code block. The orchestrator parses the structured block; the agent reads the prose sections for fix guidance.

````markdown
# Pre-PR Validation

Run these checks before opening a pull request. Failures in blocking checks
must be fixed before the PR can be created.

## Checks

```yaml:checks
max_cycles: 3

checks:
  - name: lint
    command: "ruff check src/"
    blocking: required
    timeout: 120

  - name: type-check
    command: "mypy src/forge/"
    blocking: best-effort
    timeout: 300

  - name: unit-tests
    command: "pytest tests/unit/ -x --tb=short"
    blocking: required
    timeout: 600

  - name: format-check
    command: "ruff format --check src/"
    blocking: advisory
    timeout: 60
```

## Fix Guidance

When a required or best-effort check fails, focus on:
- **lint**: Fix the reported violations. Do not disable rules.
- **type-check**: Add type annotations or fix type errors. Avoid `# type: ignore`.
- **unit-tests**: Read the failure output carefully. Fix the code, not the test.
- **format-check**: Run `ruff format src/` to auto-fix.
````

Field definitions:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_cycles` | int | 3 | Maximum validate→fix→re-validate loops before exhaustion |
| `checks[].name` | string | required | Human-readable check name (used in logs, PR annotations) |
| `checks[].command` | string | required | Shell command to execute in the workspace |
| `checks[].blocking` | string | `"required"` | `"required"`: must pass, block workflow if exhausted. `"best-effort"`: try to fix, proceed with PR annotation if exhausted. `"advisory"`: report only, never fix or block. |
| `checks[].timeout` | int | 300 | Per-check timeout in seconds |

The skill is resolved via the existing `skills/resolver.py` — project-specific overrides at `skills/{project}/validate-pre-pr/SKILL.md` take precedence over `skills/default/validate-pre-pr/SKILL.md`.

#### YAML parsing

The orchestrator extracts the YAML block by searching for a fenced code block with info string `yaml:checks`:

```python
import re
import yaml

CHECKS_BLOCK_RE = re.compile(
    r"```yaml:checks\s*\n(.*?)```",
    re.DOTALL,
)

def parse_validation_checks(skill_content: str) -> dict:
    match = CHECKS_BLOCK_RE.search(skill_content)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))
```

If the skill exists but contains no `yaml:checks` block, validation is skipped (the skill is treated as prose-only for agent guidance).

#### New state fields — `PRIntegrationState`

```python
class PRIntegrationState(TypedDict, total=False):
    # ... existing fields ...
    validation_cycle: int                    # current cycle count (0-based)
    validation_results: list[dict[str, Any]] # per-check results from last run
    validation_exhausted: list[str]          # check names that exhausted retries
    validation_advisory_failures: list[str]  # advisory/best-effort check names that failed
```

Initialized to `validation_cycle=0`, empty lists for the rest.

#### New node — `pre_pr_validation`

A new workflow node that:

1. **Resolves skill**: Calls `resolve_skill_paths(ticket_key, skills_dir)` to find `validate-pre-pr/SKILL.md`.
2. **No skill → skip**: If no skill found, routes directly to `local_review` with no validation results. Current behavior preserved.
3. **Parse checks**: Extracts the `yaml:checks` block from the skill content.
4. **Generate script**: Builds a bash script that runs all checks sequentially, capturing per-check exit code, stdout, stderr, and elapsed time into a JSON results file.
5. **Run script in container**: Spawns a **single lightweight container** that executes the generated script. One container invocation runs all checks — no per-check container overhead.
6. **Read results**: Parses `.forge/validation-results.json` written by the script.
7. **Route**:
   - All checks pass → `local_review` (standard diff quality review, no fix loop)
   - `required` or `best-effort` failures + cycles remaining → `validation_fix` (agent fixes issues)
   - Cycles exhausted + any `required` still failing → `escalate_blocked`
   - Cycles exhausted + only `best-effort` still failing → `local_review` then `create_pr` (with annotations)
   - Only `advisory` failures → `local_review` (advisory results included for awareness, no fix attempted)

#### Validation script generation

The orchestrator generates a bash script from the parsed YAML checks. The script runs each command sequentially, captures results, and writes a JSON file:

```bash
#!/bin/bash
# Auto-generated by Forge pre_pr_validation node
RESULTS_FILE=".forge/validation-results.json"
echo '[' > "$RESULTS_FILE"

# --- Check: lint ---
START=$(date +%s)
OUTPUT=$(bash -c 'ruff check src/' 2>&1)
EXIT_CODE=$?
ELAPSED=$(( $(date +%s) - START ))
# Append JSON entry (truncate output to last 2000 chars)
cat >> "$RESULTS_FILE" << ENTRY
  {"name": "lint", "command": "ruff check src/", "blocking": "required",
   "exit_code": $EXIT_CODE, "output": "...", "passed": $([ $EXIT_CODE -eq 0 ] && echo true || echo false),
   "elapsed_seconds": $ELAPSED},
ENTRY

# --- Check: type-check ---
# ... same pattern for each check ...

echo ']' >> "$RESULTS_FILE"
```

In practice, the script generation will use a proper JSON builder (Python's `json.dumps` to write a helper script, or a small Python wrapper script) to avoid shell quoting issues with command output. The key point: **one script, one container, all checks**.

#### Container execution — lightweight runner

The validation container reuses the same image as the Deep Agents container (guaranteeing all project toolchains are available) but with a different entrypoint — just `bash /tmp/validate.sh`. No agent framework, no MCP tools, no LLM calls. This makes it fast to start and predictable to execute.

```python
# Pseudocode for validation container
cmd = [
    "podman", "run", "--rm",
    "-v", f"{workspace_path}:/workspace:Z",
    "-v", f"{script_path}:/tmp/validate.sh:ro,Z",
    "-w", "/workspace",
    "--memory", config.memory_limit,
    "--cpus", config.cpu_limit,
    "--network", config.network_mode,
    "--timeout", str(max_check_timeout),
    config.image,
    "bash", "/tmp/validate.sh",
]
```

After the container exits, the orchestrator reads `.forge/validation-results.json` from the workspace.

#### New node — `validation_fix`

When `required` or `best-effort` checks fail, a **separate agent container** is spawned to fix the issues. This uses the standard Deep Agents container (same as `implement_task` and `local_review`) with a dedicated prompt:

```markdown
## Validation Fix

The following pre-PR validation checks failed. Fix the issues so they pass on re-validation.

### Failed Checks

{validation_failures_with_output}

### Fix Guidance

{fix_guidance_prose_from_skill}

## Instructions

1. Read the failure output for each check carefully.
2. Fix the underlying issues in the source code.
3. Do NOT disable linting rules, suppress type errors, or skip tests unless the failure is a false positive.
4. Commit your fixes with message: "[{ticket_key}] fix: address pre-PR validation failures (cycle {cycle})"
```

The `fix_guidance_prose` comes from the "Fix Guidance" section of the skill markdown — project-specific instructions the skill author wrote for how to approach each type of failure.

After `validation_fix` commits its fixes, the flow routes back to `pre_pr_validation` for re-validation (incrementing `validation_cycle`).

#### `local_review` — unchanged

`local_review` is **not modified** and is **not part of the validation loop**. It runs once after all blocking validation checks pass (or validation is skipped), performing its existing job:

1. Review `git diff origin/main...HEAD` for code quality issues
2. Fix breaking issues in-place (up to 2 internal passes)
3. Route to `create_pr`

The only change to `local_review`'s routing is the inbound edge — it now receives flow from `pre_pr_validation` instead of directly from `implement_task`. Its outbound routing to `create_pr` remains the same.

#### PR body annotations

When validation proceeds with unresolved `best-effort` or `advisory` failures, the PR body includes an annotation section:

```markdown
## ⚠️ Pre-PR Validation Notes

The following checks could not be resolved after 3 validation cycles:

| Check | Status | Details |
|-------|--------|---------|
| type-check | ❌ Failed | `mypy src/forge/` — 2 errors remaining (see logs) |
| format-check | ⚠️ Advisory | `ruff format --check src/` — 3 files need formatting |

These checks were classified as `best-effort` or `advisory` in the project's validation skill.
```

#### Graph changes

Both feature and bug workflow graphs are updated:

**Feature workflow** (`workflow/feature/graph.py`):
```
implement_task → (all tasks done) → pre_pr_validation
                                         │
                              ┌──────────┼──────────────────────┐
                              │          │                       │
                              │    (required/best-effort    (exhausted +
                              │     failures, cycles         required still
                              │     remaining)               failing)
                              │          │                       │
                              │          ▼                       ▼
                              │    validation_fix          escalate_blocked
                              │          │
                              │          └──→ pre_pr_validation (re-validate)
                              │
                         (all pass or
                          no skill or
                          exhausted with only
                          best-effort/advisory)
                              │
                              ▼
                        local_review → create_pr
```

**Bug workflow** (`workflow/bug/graph.py`):
```
implement_bug_fix → pre_pr_validation → (same branching as above)
```

New nodes:
- `pre_pr_validation` — runs validation script in lightweight container
- `validation_fix` — spawns agent container to fix failures

New edges:
- `implement_task` → `pre_pr_validation` (replaces direct edge to `local_review`)
- `implement_bug_fix` → `pre_pr_validation` (replaces direct edge to `local_review` / `create_pr`)
- `pre_pr_validation` → `local_review` (all checks pass, no skill, or exhausted with only `best-effort`/`advisory` failures)
- `pre_pr_validation` → `validation_fix` (`required` or `best-effort` failures, cycles remaining)
- `pre_pr_validation` → `escalate_blocked` (exhausted with `required` checks still failing)
- `validation_fix` → `pre_pr_validation` (re-validate after fix, incrementing `validation_cycle`)
- `local_review` → `create_pr` (unchanged — local_review no longer routes back to validation)

Resume routing in `route_by_ticket_type` / `route_entry`:
```python
elif current_node == "pre_pr_validation":
    return "pre_pr_validation"
elif current_node == "validation_fix":
    return "validation_fix"
```

### User Experience

**Project with validation skill configured:**

```
[Forge, on AISOS-376 Jira ticket]
Implementation complete for AISOS-376-1. Running pre-PR validation...

[Forge runs generated validation script in lightweight container]

Pre-PR validation results (cycle 1/3):
  ✅ lint — passed (2.1s)
  ❌ type-check — failed (45.3s): 3 type errors
  ✅ unit-tests — passed (120.4s)
  ⚠️ format-check — advisory failure (1.2s): 2 files

Blocking failures found. Spawning agent to fix...

[validation_fix agent reads failure output + fix guidance, fixes type errors, commits]

Re-validating (cycle 2/3)...

[Forge runs validation script again in lightweight container]

  ✅ lint — passed (2.0s)
  ✅ type-check — passed (44.8s)
  ✅ unit-tests — passed (118.9s)
  ⚠️ format-check — advisory failure (1.1s): 2 files

All blocking checks passed. Running local review...

[local_review reviews full diff for code quality — single pass, unchanged behavior]

Creating PR...
```

**Validation exhausted — required check still failing:**

```
[Forge, after 3 cycles]
Pre-PR validation failed after 3 cycles. Required checks still failing:
  ❌ unit-tests (required) — 2 test failures remaining

Workflow blocked. Add forge:retry label to retry from validation.
```

**Validation exhausted — only best-effort check failing:**

```
[Forge, after 3 cycles]
Pre-PR validation: 3 cycles exhausted. Only best-effort checks remaining.
Proceeding with annotations...

Creating PR with validation notes...

[PR body includes ⚠️ Pre-PR Validation Notes section]
```

**Project without validation skill:**

```
[Forge, on PROJ-999]
No validate-pre-pr skill configured. Skipping pre-PR validation.
Proceeding to local review...

[current behavior, unchanged]
```

## Alternatives Considered

| Alternative | Pros | Cons | Why Not |
|-------------|------|------|---------|
| Feed validation failures into `local_review` loop | One fewer node; reuses existing review infrastructure | Compounds local_review's internal retry (2 passes) with validation cycles — up to 6 agent containers per PR; conflates code quality review with CI fix; makes local_review's scope unpredictable | Separation of concerns: validation runs checks, validation_fix fixes them, local_review reviews code quality. Each node does one thing. |
| Run all checks via separate containers (one per check) | Per-check isolation; parallel execution possible | Container startup overhead (~5-10s) multiplied by number of checks per cycle; unnecessary isolation since checks share the same workspace | Single container with generated script is faster and simpler — all checks need the same workspace and toolchain |
| Run checks inside `implement_task` container | Fastest — no extra container; agent has full context | No orchestrator visibility into what ran; can't retry at orchestrator level; agent already has a complex job | Orchestrator needs control over the validate→fix loop |
| Parse upstream CI config (GitHub Actions YAML) | Auto-discovers checks without manual skill definition | Massive complexity — Actions YAML has matrix builds, services, custom runners, secrets; most steps can't run locally | Projects differ too much; parsing CI config is a project in itself |
| Separate `checks.yaml` file alongside `SKILL.md` | Clean YAML parsing; standard tooling | Two files to keep in sync; partial override ambiguity when only one file is overridden per project | Single-file skill with embedded YAML is simpler and matches existing skill patterns |
| Run checks on host instead of container | Faster startup; no container overhead | Breaks isolation model; host may not have toolchains installed; unsafe for arbitrary commands | Container isolation is a design principle of Forge |
| Default validation skill with auto-detection | Works out of the box for common stacks | May run unexpected commands on unfamiliar projects; false confidence; overrides needed anyway for real projects | Opt-in is safer; projects adopt when ready |

## Implementation Plan

### Phases

1. **Phase 1: YAML parser and skill loading** — Add `parse_validation_checks()` to extract `yaml:checks` blocks from skill markdown. Add `validate-pre-pr` to the skill resolver's known skill names. Unit tests for parsing edge cases (missing block, malformed YAML, missing fields with defaults). (~half day)

2. **Phase 2: Validation script generator** — Implement `generate_validation_script(checks)` that produces a bash script running all checks sequentially and writing per-check results to `.forge/validation-results.json`. The script captures exit code, stdout/stderr (truncated to last 2000 chars), and elapsed time per check. Unit tests for script generation and result parsing. (~half day)

3. **Phase 3: Validation state and node** — Add `validation_cycle`, `validation_results`, `validation_exhausted`, `validation_advisory_failures` to `PRIntegrationState`. Implement `pre_pr_validation` node: skill resolution, script generation, lightweight container execution (same image, `bash` entrypoint), result parsing, routing logic. (~1 day)

4. **Phase 4: `validation_fix` node** — Implement the agent container node that receives failure output and fix guidance from the skill, fixes the code, and commits. Add prompt template `validate-fix.md`. (~half day)

5. **Phase 5: Graph wiring** — Add `pre_pr_validation` and `validation_fix` nodes and new edges to both feature and bug workflow graphs. Rewire `implement_task` → `pre_pr_validation` (was `local_review`). Add `validation_fix` → `pre_pr_validation` loop edge. Add resume routing for both new nodes in worker. Update `create_initial_feature_state` and `create_initial_bug_state` with new fields. (~half day)

6. **Phase 6: PR annotation** — When validation proceeds with known failures, inject the "Pre-PR Validation Notes" section into the PR body via `pr_creation.py`. (~half day)

7. **Phase 7: Tests** — Unit tests for the validation node (skip when no skill, all pass, required failure, best-effort failure, advisory failure, exhaustion with required still failing, exhaustion with only best-effort remaining). Unit tests for `validation_fix` node. Flow test for the validate→fix→re-validate loop. Integration test for the full implement→validate→review→PR path. (~1 day)

### Dependencies

- [ ] `ContainerRunner` must support running with a custom entrypoint (e.g., `bash /tmp/validate.sh`) instead of the Deep Agents entrypoint — add a `run_script()` method or an `entrypoint_override` parameter
- [ ] `resolve_skill_paths()` must be callable with a specific skill name (currently resolves directories; need to resolve a specific skill file)
- [ ] New prompt template `validate-fix.md` for the `validation_fix` agent container
- [ ] `.forge/validation-results.json` must be excluded from git commits (already covered — `.forge/` is in the container's gitignore handling)

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Check commands fail due to missing dependencies in container | High (first use per project) | Med | Document that check commands must work inside the Forge container image; allow projects to extend the image or use a custom image |
| Validate→fix loop runs indefinitely on unfixable errors | Low (capped by `max_cycles`) | Med | `max_cycles` hard cap; `blocking` level determines whether to block (`required`) or proceed (`best-effort`) |
| `validation_fix` agent makes things worse (introduces new failures) | Med | Med | Re-validation catches regressions immediately; cycle count limits damage; blocking checks ensure no net-negative PRs |
| YAML block parsing breaks on edge cases (nested YAML, special characters) | Low | Low | Use `yaml.safe_load()`; unit test edge cases; graceful fallback (skip validation on parse error) |
| Generated bash script has quoting/escaping issues with check output | Med | Low | Use a Python wrapper script inside the container instead of raw bash for JSON serialization; unit test with adversarial command output |
| Container startup overhead makes the loop slow | Low | Low | 2 containers per cycle (lightweight validation + agent fix); lightweight container starts in ~2-3s; 3 cycles = ~6-9s overhead total, negligible |
| Skill author writes a destructive command (e.g., `rm -rf /`) | Low | High | Commands run in an ephemeral container with mounted workspace; host is protected; workspace is disposable |

## Open Questions

- [ ] Should the validation script be generated as pure bash or as a small Python script that runs inside the container? Python avoids shell quoting issues and produces clean JSON, but adds a dependency on Python being available in the container image (it is in the current devcontainers/universal image, but may not be in custom images).
- [ ] Should `max_cycles` be overridable per-check (some checks are cheap to retry, others expensive), or is a global cap sufficient?
- [ ] Should validation results be posted as a Jira comment for visibility, or only written to the workspace and PR body?
- [ ] For the CI fix path (`attempt_ci_fix`): should pre-PR validation also run after CI fixes before re-pushing, to catch regressions early? This would add the validation loop to the CI fix cycle as well.
- [ ] Should checks run sequentially (current design) or in parallel where possible? Sequential is simpler and avoids resource contention; parallel would be faster for independent checks (e.g., lint and type-check can run simultaneously).

## References

- [Proposal 005: CI Gate Skip](005-ci-gate-skip-command.md) — related CI control mechanism
- [Proposal 007: implement_review Node](007-implement-review-node.md) — similar pattern of adding a dedicated node with a fix loop
- `src/forge/workflow/nodes/ci_evaluator.py` — existing post-PR CI evaluation and fix pipeline
- `src/forge/workflow/nodes/local_reviewer.py` — current pre-PR code review (quality only, no check execution)
- `src/forge/skills/resolver.py` — skill resolution mechanism
- `containers/entrypoint.py:detect_test_command()` — existing heuristic test detection (replaced by explicit skill config)
