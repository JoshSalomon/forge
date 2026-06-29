# Proposal: Auto-Review Primitive for Skills

**Author:** jsalomon
**Date:** 2026-05-04
**Status:** Accepted — Approach A (container-internal loop) selected for implementation

## Summary

Add an opt-in auto-review mechanism as a new primitive for skills. Any skill can include a `review.md` file alongside its `SKILL.md`. When present, Forge automatically runs a review cycle after the skill's worker agent completes: a separate reviewer agent evaluates the work against the review criteria, and on rejection the worker retries with the reviewer's feedback. The loop repeats until approval or a configurable retry limit is reached.

This is designed as a simple, composable primitive — not a replacement for the existing human approval gates or the `local_review` / `implement_review` nodes. It gives skill authors a way to add automated quality checks to any skill without modifying the workflow graph.

## Motivation

### Problem Statement

Today, quality control in Forge happens at fixed points in the workflow:

1. **Human approval gates** — for PRD, spec, epics, tasks (manual, asynchronous)
2. **`local_review` node** — reviews git diff for code quality before PR (generic, not skill-aware)
3. **`ci_evaluator`** — runs upstream CI after PR creation (external, slow feedback loop)

There's no mechanism for a skill author to say "after this skill runs, automatically check that the output meets these specific criteria before proceeding." For example:

- A `generate-prd` skill might want to auto-check that the PRD includes acceptance criteria, a scope section, and no unresolved TODOs
- An `implement-task` skill might want to verify that all new functions have docstrings and tests
- A `fix-ci` skill might want to re-run the failing check locally before pushing

Today, these checks would require either modifying the workflow graph (adding nodes) or embedding review logic into the skill's own SKILL.md (making the worker review its own work — confirmation bias).

### Current Workarounds

1. Include review instructions in SKILL.md itself — the worker agent self-reviews, but this is unreliable (confirmation bias)
2. Rely on `local_review` — but it's generic, not skill-specific, and only applies to code changes
3. Add dedicated workflow nodes — works but requires code changes to the graph for each new review type

## What We Agree On

The following design decisions are settled:

### Skill file structure

A `review.md` file lives alongside `SKILL.md` in the skill directory:

```
skills/default/implement-task/
├── SKILL.md          # worker instructions (existing)
└── review.md         # reviewer instructions (new, optional)
```

Per-project overrides work the same as SKILL.md — `skills/{project}/implement-task/review.md` overrides `skills/default/implement-task/review.md`.

If `review.md` does not exist, no review runs — current behavior is preserved.

### review.md format

Pure prose with an optional `max_retries` directive. The reviewer agent reads it as instructions, just like the worker reads SKILL.md:

```markdown
---
max_retries: 3
---

# Code Implementation Review

Review the code changes in this workspace against the following criteria:

## Criteria

1. **Test coverage**: Every new public function must have at least one test
2. **Error handling**: No bare except clauses; all errors must be specific
3. **Documentation**: Public APIs must have docstrings
4. **No debug code**: No print statements, console.log, or TODO/FIXME in committed code

## Instructions

Run `git diff origin/main...HEAD` to see the changes.

If all criteria are met, approve the work.
If any criteria are not met, reject with specific feedback listing:
- Which criterion failed
- Which file and line
- What needs to change
```

### Review scope

The reviewer gets the same workspace access as the worker — full filesystem, git history, ability to run commands. The review.md instructions define what the reviewer should look at. No artificial restrictions.

### Applicability

Any skill can have a review.md — not limited to container/execution skills. A PRD generation skill could have a reviewer that checks for completeness. The mechanism is generic.

### Agent architecture

The worker agent runs first, completes its task, and commits. Then a **separate** reviewer agent runs against the same workspace. On rejection, the **worker agent** gets the feedback and retries. The worker retains its context across review cycles (either via live process or conversation history replay — see open decision below).

### Retry limit

Configurable per-skill via `max_retries` in review.md frontmatter. Falls back to a global default (`AUTO_REVIEW_MAX_RETRIES`, default: 5). When exhausted, the skill is treated as completed (the last attempt's output is used).

## Architecture Decision: Container-Internal Loop (Approach A)

**Decision: Approach A — container-internal loop with file-based observability — has been selected for implementation.** Approach B (orchestrator-managed loop) is documented below for reference but will not be implemented in this phase.

---

### Approach A: Container-Internal Loop (Selected)

The review loop runs entirely inside the container, managed by the entrypoint. The orchestrator is unaware of it.

**How it works:**

1. Container starts, entrypoint runs the worker agent (existing behavior)
2. Worker agent completes, commits changes
3. Entrypoint checks if `review.md` exists for this skill
4. If yes: spawns a reviewer agent in the same container with review.md instructions
5. Reviewer outputs verdict (approve/reject + feedback)
6. If rejected and retries remain: re-invokes the worker agent with reviewer feedback appended to the original prompt. The worker agent is a new invocation but in the same container — same filesystem, same installed dependencies, same git state
7. Loop until approved or max_retries exhausted
8. Container exits with final status

**Worker context on retry:** The worker is a fresh agent invocation but gets:
- The workspace with all its prior commits (git history is preserved)
- The conversation history from its prior run (loaded from `.forge/history/`)
- The reviewer's feedback as additional context in the prompt

**Review protocol:** Convention-based markers in the reviewer's output. The entrypoint scans for `APPROVED` or `REJECTED` keywords (similar to how `local_review` scans for "unfixed" + "breaking" today). Reviewer feedback is captured as everything after the verdict marker.

**Entrypoint changes:**

```python
# Pseudocode for entrypoint review loop
worker_result = run_agent(skill="implement-task", prompt=task_prompt)
save_history(worker_result)

review_config = load_review_md(skill_name)
if not review_config:
    commit_and_exit(EXIT_SUCCESS)

for cycle in range(review_config.max_retries):
    reviewer_result = run_agent(
        prompt=review_config.instructions,
        # Reviewer sees the workspace as-is after worker
    )
    verdict, feedback = parse_verdict(reviewer_result)

    # Write per-cycle file for orchestrator to poll
    write_cycle_file(cycle, verdict, feedback)

    if verdict == "APPROVED":
        commit_and_exit(EXIT_SUCCESS)

    # Re-run worker with feedback
    worker_result = run_agent(
        skill="implement-task",
        prompt=task_prompt + f"\n\n## Reviewer Feedback\n{feedback}",
        history=load_history(),  # prior conversation context
    )
    save_history(worker_result)

# Exhausted retries — use last attempt
commit_and_exit(EXIT_SUCCESS)
```

**File-based observability:**

The workspace directory is a shared mount between the container and the host. The entrypoint writes a JSON file after each review cycle:

```
.forge/review_cycle_1.json
.forge/review_cycle_2.json
.forge/review_cycle_3.json
```

Each file contains:

```json
{
  "cycle": 1,
  "max_cycles": 3,
  "verdict": "rejected",
  "feedback": "Missing test coverage for auth_handler.py — no tests for login() or verify_token()",
  "skill": "implement-task",
  "elapsed_seconds": 142,
  "timestamp": "2026-05-04T14:32:00Z"
}
```

The orchestrator polls the workspace directory while waiting for the container to finish. When it detects a new `review_cycle_N.json` file, it can:

1. **Record Prometheus metrics** — `forge_review_cycles_total`, `forge_review_verdicts{verdict="rejected"}`, `forge_review_duration_seconds`
2. **Post Jira comment** — "Review cycle 1/3: rejected — missing test coverage for auth_handler.py"
3. **Log at INFO level** — for worker log grep

This gives near-real-time observability without the orchestrator managing the loop. The container writes, the orchestrator reads — the filesystem is the communication channel.

**Orchestrator polling (pseudocode):**

```python
async def run_container_with_review_polling(self, ...):
    process = await asyncio.create_subprocess_exec(*cmd, ...)
    last_cycle_seen = 0

    while process.returncode is None:
        # Check for new review cycle files
        cycle_file = workspace / f".forge/review_cycle_{last_cycle_seen + 1}.json"
        if cycle_file.exists():
            data = json.loads(cycle_file.read_text())
            record_review_cycle(data)       # Prometheus
            await post_jira_comment(data)   # Jira visibility
            last_cycle_seen += 1

        await asyncio.sleep(5)  # poll interval

    stdout, stderr = await process.communicate()
    return ContainerResult(...)
```

**Pros:**
- Orchestrator unchanged structurally — no new nodes, no graph edges, no state fields
- Worker context is preserved well (same container, history replay)
- Simple to implement — entrypoint is already a Python script that orchestrates agent runs
- Reviewer uses exact same toolchain and environment as worker (same container)
- Natural fit — the review is a quality check on the worker's output, not a workflow stage
- **Near-real-time observability** — orchestrator polls cycle files for Prometheus metrics and Jira comments while container runs
- **Full post-hoc debugging** — cycle files persist in workspace for inspection

**Cons:**
- **Container timeout pressure** — the review loop runs within the container's timeout budget. 3 review cycles of a worker + reviewer = 6 agent invocations inside one container, which could hit the 2-hour timeout
- **No human intervention point** — if the review loop is stuck, there's no way to inject guidance mid-loop. The container runs to completion or timeout
- **Polling adds minor orchestrator complexity** — a background task to watch for cycle files while waiting for the container process
- **Review state not in checkpoint** — if the orchestrator crashes mid-container, the review cycle count is not checkpointed (but the cycle files persist in the workspace for recovery)

---

### Approach B: Orchestrator-Managed Loop (Not Selected — Reference Only)

The orchestrator manages the review loop as new workflow nodes. The container runs once per invocation (worker or reviewer), and the orchestrator decides whether to re-invoke. This approach was considered but not selected for the PoC due to higher implementation complexity. It remains a viable migration path if human intervention mid-loop becomes a requirement.

**How it works:**

1. Existing skill node runs as today (e.g., `implement_task` spawns container, worker agent runs, container exits)
2. Orchestrator checks if `review.md` exists for this skill
3. If yes: spawns a **new container** with reviewer agent and review.md instructions
4. Reviewer outputs structured verdict (JSON: `{"verdict": "approve|reject", "feedback": "..."}`)
5. Orchestrator reads verdict from `.forge/review-result.json`
6. If rejected and retries remain: re-invokes worker container with feedback
7. Loop at orchestrator level with checkpoint state tracking cycle count
8. On approval or exhaustion: proceed to next node

**Review protocol:** Structured JSON output written to `.forge/review-result.json`. The orchestrator parses it deterministically.

**State additions:**

```python
class ReviewLoopState(TypedDict, total=False):
    review_cycle: int           # current review attempt
    review_verdict: str | None  # last verdict (approve/reject)
    review_feedback: str | None # last reviewer feedback
```

**Graph changes:** New conditional edges after any skill-based node:

```
implement_task → skill_review → (approved) → local_review
                     ↑    ↓
                     └────┘ (rejected, worker retry)
```

**Pros:**
- **Full observability** — review cycles appear in checkpoint state, can be logged to Jira, tracked in metrics
- **Human intervention** — if the loop gets stuck, `forge:retry` or checkpoint patching can intervene
- **Timeout isolation** — each worker/reviewer run gets its own container timeout, not a shared budget
- **Consistent with existing patterns** — follows the same node→container→result pattern as `attempt_ci_fix`

**Cons:**
- **Worker loses context** — each worker invocation is a fresh container. The worker gets its conversation history from `.forge/history/` but doesn't have the live process context
- **More complex** — new nodes, new state fields, new graph edges, new routing logic
- **Two containers per cycle** — worker container + reviewer container, with startup overhead each time
- **Every skill-based node needs wiring** — `implement_task`, `generate_prd`, `generate_spec`, etc. all need the review conditional edge

---

### Comparison

| Aspect | A: Container-Internal + File Polling | B: Orchestrator-Managed |
|--------|--------------------------------------|------------------------|
| **Implementation effort** | Small — entrypoint loop + orchestrator file polling | Large — new nodes, state, graph edges |
| **Worker context preservation** | Good — same container, history replay | Partial — history replay from file, no live context |
| **Prometheus metrics** | Yes — orchestrator reads cycle files, records metrics | Yes — direct from orchestrator |
| **Jira visibility** | Yes — orchestrator posts comments when cycle files appear | Yes — direct from orchestrator |
| **Debugging** | Cycle files persist in workspace + container logs | Checkpoint state + logs |
| **Human intervention mid-loop** | None — container runs to completion | Possible via forge:retry, checkpoint patch |
| **Container timeout** | Shared budget (risk of timeout) | Isolated per invocation |
| **Orchestrator graph changes** | None — only a polling loop in `ContainerRunner` | Significant — new nodes, edges, state |
| **Consistency with codebase** | New pattern (entrypoint loop + file polling) | Follows existing patterns (node→container→result) |
| **PoC speed** | Fast to implement | Slower to implement |

### Decision Rationale

**Approach A with file-based observability was selected.** The original concern about Approach A was poor observability — the orchestrator couldn't see what was happening inside the container. File-based polling closes this gap:

- **Metrics**: orchestrator records Prometheus counters/histograms from cycle files in near-real-time
- **Jira**: orchestrator posts per-cycle comments as they happen ("Review cycle 1/3: rejected — missing test coverage")
- **Debugging**: cycle files persist in workspace for post-hoc analysis

The remaining gaps vs Approach B — human intervention mid-loop and checkpoint persistence — are acceptable for the PoC. The timeout concern is manageable via container timeout increases and per-skill `max_retries`.

**Migration path to Approach B remains open** if human intervention mid-loop becomes a requirement. The review.md format, cycle file format, and review protocol are compatible with both approaches — the migration would move the loop from entrypoint to orchestrator, not redesign the review mechanism.

**Important implementation note:** The orchestrator must perform a final sweep of all `review_cycle_*.json` files after the container exits, before proceeding to the next workflow step. Without this, the final approval cycle file may be missed if the container exits between poll intervals. Additionally, cycle files must be copied to a persistent location before `teardown_workspace` destroys the workspace.

## Future Directions

### Reviewer model selection

With the per-node model tier system (proposal 010), the reviewer agent could use a different model tier than the worker. A `model_tier: fast` directive in review.md would let the reviewer run on Haiku while the worker runs on Opus — fast, cheap review cycles.

### Skill-model compatibility metadata

Skills could declare which models they've been tested on:

```yaml
tested_on:
  - claude-sonnet-4-5
  - gemini-2.5-pro
```

The orchestrator could warn when a skill runs on an untested model. This is a separate proposal but complements the auto-review primitive — a reviewer could be specifically tuned for a model family.

### Cross-skill review

A review.md could reference criteria from other skills or shared review libraries. For example, a "security review" skill could be applied as a reviewer to any implementation skill.

## References

- `src/forge/skills/resolver.py` — skill resolution with per-project overrides
- `containers/entrypoint.py` — container entrypoint that manages agent invocations
- `src/forge/workflow/nodes/local_reviewer.py` — existing pre-PR review pattern (Approach B analogy)
- `src/forge/workflow/nodes/ci_evaluator.py` — existing orchestrator-managed fix loop (Approach B analogy)
- `.forge/history/{task_key}.json` — existing conversation history persistence
