# PR Commands

Forge listens for commands posted as comments on GitHub pull requests during CI validation stages.

## Available Commands

### `/forge skip-gate <name>`

Bypass a specific CI check. Use this for infrastructure failures unrelated to your code (cloud outages, quota exhaustion, flaky test runners).

```
/forge skip-gate <check-name-substring>
```

**Examples:**

```
/forge skip-gate e2e-openstack-ovn
/forge skip-gate e2e-openstack        ← skips all checks containing this substring
/forge skip-gate flaky-integration
```

**What Forge does:**

1. Replies on the PR confirming the skip with the matched check name
2. Posts an audit comment on the Jira ticket
3. Re-evaluates CI treating the skipped check as passing

**Persistence:** Skips persist across pushes. If the same infrastructure check fails again after the next commit, it is still treated as passing.

**Matching:** Case-insensitive substring match against the full check name.

---

### `/forge unskip-gate <name>`

Remove a previously set skip.

```
/forge unskip-gate e2e-openstack-ovn
```

Forge confirms the removal and re-evaluates CI without the skip.

## When Commands Are Active

PR commands only work when Forge's workflow is in a CI stage:

- `wait_for_ci_gate`
- `ci_evaluator`
- `attempt_ci_fix`

Commands on PRs outside these stages are ignored.

## Permanently Ignored Checks

Some checks are always pending and are permanently ignored regardless of skip commands. Configure the list with `CI_IGNORED_CHECKS` in `.env`.

Common examples: `tide` (Prow's merge-queue controller), status checks that reflect queue position rather than test results.

## Audit Trail

Every skip and unskip action is recorded as a comment on the Jira ticket, so there's a clear record of which checks were bypassed and when.
