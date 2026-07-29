---
max_retries: 2
---

# Implementation Review — 5-Dimension Deep Review

Review the code changes by running 5 independent review passes. Run `git diff origin/main...HEAD` to see all changes. Read `.forge/task.json` to understand the task requirements.

For each pass, evaluate independently — do not let findings in one pass influence another. Report all findings together at the end.

---

## Pass 1: Simplicity / DRY / Elegance

Is the code clean? Could anything be simpler?

- Duplicated logic that should be extracted into a shared function
- Overly verbose code that could be expressed more concisely
- Unnecessary indirection or wrapper layers
- Complex conditionals that could be simplified
- Copy-pasted blocks with minor variations

**Flag format:** `[Pass 1: Simplicity] file:line — description — severity`

---

## Pass 2: Bugs / Functional Correctness

Are there edge cases missed? Race conditions? Error handling gaps?

- Unhandled error paths or bare `except:` blocks
- Off-by-one errors, boundary conditions, empty input handling
- Race conditions in async code (missing awaits, unsynchronized state)
- Resource leaks (unclosed files, connections, sessions)
- Type mismatches or incorrect assumptions about input shapes
- Security issues (injection, unsanitized input, exposed secrets)

**Flag format:** `[Pass 2: Correctness] file:line — description — severity`

---

## Pass 3: Project Conventions / Abstractions

Does the code follow existing patterns in the codebase?

- Naming conventions: `X | None` not `Optional[X]`, `StrEnum` for string enums
- `contextlib.suppress()` instead of empty try/except
- Unused parameters prefixed with `_`
- Logging via `logging.getLogger(__name__)`, not `print()`
- pytest patterns: fixtures, `pytest.raises()`, `@pytest.mark.asyncio`
- Type hints on all public functions with PEP 604 unions
- Abstractions that don't match the project's existing architecture

**Flag format:** `[Pass 3: Conventions] file:line — description — severity`

---

## Pass 4: Over-Engineering (Ponytail Review)

Hunt for unnecessary complexity. The diff's best outcome is getting shorter.

Tags:
- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

Scope: over-engineering and complexity only. Correctness bugs and security are covered by Pass 2.

**Flag format:** `[Pass 4: Ponytail] file:line — tag: description. replacement. — severity`

End this pass with: `net: -<N> lines possible.` or `Lean already.`

---

## Pass 5: Task-Implementation Alignment

Read `.forge/task.json` carefully. Compare every requirement against the actual implementation.

### 5a. Requirements Coverage

For each requirement or acceptance criterion in the task:
- Is it implemented? Point to the specific code that fulfills it.
- Is it implemented correctly, or does the code only partially address it?
- Are there requirements that were missed entirely?
- Are there implemented features that were NOT requested (scope creep)?

### 5b. Test Thoroughness

For every piece of functionality implemented:
- Does a corresponding test exist?
- Does the test actually exercise the behavior, or is it shallow (e.g., only checks that a function is callable, only checks the happy path, asserts on type but not value)?
- Are edge cases tested (empty input, error paths, boundary values)?
- Are assertions specific? Tests that assert `is not None` or `isinstance()` without checking actual values are shallow.
- Do tests verify behavior, not implementation details? (e.g., testing the output, not that an internal method was called)

### 5c. Gaps Summary

List explicitly:
1. Requirements from the task that have NO implementation
2. Requirements that are implemented but have NO tests
3. Tests that exist but are shallow (explain why for each)
4. Functionality implemented that was not in the task requirements

**Flag format:** `[Pass 5: Alignment] description — severity`

---

## Severity Classification

Classify every finding as one of:

| Severity | Meaning |
|----------|---------|
| **critical** | Crashes, data loss, security holes, missing core requirements |
| **high** | Bugs in non-critical paths, missing error handling, missing tests for public API, shallow tests that don't verify behavior, partial requirement coverage, significant convention violations |
| **medium** | Minor convention violations, over-engineering |
| **low** | Minor style issues, small simplification opportunities, non-blocking suggestions |

## Verdict

Determine your verdict based on the review cycle. Check `.forge/reviews/` for previous cycle files — if none exist, this is cycle 1.

### Cycle 1 (first review — no previous cycle files exist)

Be strict. The implementation has not been reviewed yet, so catch everything up front.

- Any finding of severity **medium or higher** → `REJECTED`
- Only **low** severity findings or no findings → `APPROVED`

### Cycle 2+ (retry after feedback — previous cycle files exist)

The implementation has already been revised based on prior feedback. Apply normal blocking rules.

- Any **critical** or **high** severity finding → `REJECTED`
- Only **medium** or **low** findings or no findings → `APPROVED`

Output exactly one marker:

```
APPROVED
```

```
REJECTED
```

## Feedback Format

On rejection (cycle 1 example — medium findings block):

```
REJECTED

Pass 1 — Simplicity:
- [Pass 1: Simplicity] src/forge/module.py:42 — duplicated validation logic, extract to helper — medium

Pass 2 — Correctness:
- [Pass 2: Correctness] src/forge/handler.py:87 — bare except swallows TypeError, use specific exception — high

Pass 3 — Conventions:
- [Pass 3: Conventions] src/forge/utils.py:15 — uses Optional[str] instead of str | None — low

Pass 4 — Ponytail:
- [Pass 4: Ponytail] src/forge/adapter.py:L30-55 — yagni: AbstractAdapter with one implementation. Inline it. — medium
- net: -25 lines possible.

Pass 5 — Alignment:
- [Pass 5: Alignment] Task requires input validation for empty strings — no implementation found — critical
- [Pass 5: Alignment] test_handler.py:test_process only checks return type, not return value — shallow test — medium

Required changes:
1. Implement empty string validation per task requirement
2. Add value assertion to test_process: verify actual output matches expected
3. Handle TypeError specifically in handler.py:87
4. Extract duplicated validation logic into shared helper
5. Inline AbstractAdapter — only one implementation exists
```

On rejection (cycle 2+ example — only high/critical block):

```
REJECTED

Pass 2 — Correctness:
- [Pass 2: Correctness] src/forge/handler.py:92 — new catch block still uses generic Exception, should catch httpx.HTTPError — high

Pass 5 — Alignment:
- [Pass 5: Alignment] test_handler.py:test_empty_input asserts no exception raised but does not verify output value — medium (non-blocking on retry)

Required changes:
1. Narrow exception type in handler.py:92 to httpx.HTTPError
```

On approval with notes:

```
APPROVED

Pass 4 — Ponytail:
- [Pass 4: Ponytail] src/forge/utils.py:L12-18 — shrink: manual loop builds list. Use list comprehension. — low
- net: -4 lines possible.

All task requirements implemented and tested. No blocking issues.
```

## Important

- Be specific: include file path and line number for every finding
- Be actionable: state exactly what needs to change
- Read the FULL task description before starting Pass 5 — do not skim
- For Pass 5, err on the side of flagging shallow tests. A test that passes but proves nothing is worse than no test.
