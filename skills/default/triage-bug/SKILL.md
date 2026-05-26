---
name: triage-bug
description: Evaluate a bug ticket for completeness before starting analysis. Use when triaging bug reports to determine if enough information is present to begin investigation.
---

# Bug Triage Skill

Determine whether a bug ticket contains enough information for a developer to begin investigating. You are **not** deciding whether the bug is fully understood — that is what analysis is for. Triage only blocks tickets where the absence of information makes it literally impossible to start looking.

## Evaluation Philosophy

**Default to passing.** Block a ticket only when a field is completely absent AND that absence genuinely prevents starting an investigation. When in doubt, pass.

A field is satisfied if any of the following are true:
- The information is present, even if brief or informal
- The information is clearly inferable from context
- An experienced engineer could construct it from what is provided
- The reporter explicitly states the information is unavailable, with a plausible reason
- The nature of the bug makes the field inapplicable

## Fields to Evaluate

1. **steps_to_reproduce** — How to trigger the bug. Satisfied if the mechanism or conditions are described well enough to attempt reproduction. A vague scenario is sufficient; a numbered list is not required.

2. **expected_vs_actual** — What happened vs. what should have happened.

3. **environment** — Runtime or infrastructure context. Satisfied for any internal codebase bug where the code itself is the environment.

4. **affected_versions** — Which version exhibits the bug. Satisfied for internal or unreleased projects without a formal version scheme.

5. **error_output** — Logs, stack traces, or error messages. Satisfied if the reporter states no error output exists — this may itself be the symptom.

6. **affected_component** — Which part of the system is involved. Satisfied if the reporter names any layer, service, or area (e.g. "event processing", "queue"). Does not need to be a specific file or class.

7. **disambiguating_context** — Only flag this if the description is so generic that completely different bugs could plausibly match it.

## Output

The output format is a forge protocol constraint — do not change it:
- If the ticket is sufficient to start an investigation: output only the bare string `sufficient`
- If one or more fields are genuinely missing and block investigation: output a bare JSON array of field names, e.g. `["steps_to_reproduce"]`

No markdown, no code fences, no explanation.
