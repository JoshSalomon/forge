# Proposal: Per-Node Model Tier Selection

**Author:** jsalomon
**Date:** 2026-05-04
**Status:** Draft

## Summary

Forge currently uses a single LLM model for all orchestrator tasks and optionally a second model for container tasks. This proposal introduces three symbolic model tiers — `fast`, `standard`, `advanced` — with per-node assignment, so that simple tasks (PR descriptions, comment classification) use a cheaper/faster model while complex tasks (PRD generation, code implementation) use a more capable one. For the PoC, tiers are hardcoded to Haiku, Sonnet, and Opus respectively, with a central config for node-to-tier mapping that can be overridden for field testing.

## Motivation

### Problem Statement

Today, every orchestrator call uses `LLM_MODEL` (default: `claude-sonnet-4-5`) regardless of task complexity. This means:

1. **Wasted cost and latency**: Simple tasks like generating a PR description or classifying a Jira comment use the same model as complex tasks like writing a PRD or implementing code. Sonnet is overkill for "parse this comment and decide if it's an approval or a question."

2. **No way to use the best model for hard tasks**: If you set `LLM_MODEL=claude-opus-4-5` to get better PRDs, every CI log analysis and comment classification also uses Opus — slow and expensive. If you set it to Sonnet for cost, your PRDs and implementations suffer.

3. **Container model is the only override**: `CONTAINER_LLM_MODEL` lets you use a different model inside containers, but all container tasks (implementation, CI fix, local review, validation fix) share the same model. You can't run implementation on Opus and CI fixes on Haiku.

### Current Workarounds

Set `LLM_MODEL` to the model needed for the most critical tasks (usually Sonnet or Opus) and accept the cost/latency overhead for simpler tasks. There is no per-node override.

## Proposal

### Overview

Introduce a `ModelTier` enum with three levels. Each workflow node declares a default tier. A central config (`NODE_MODEL_OVERRIDES`) lets admins reassign tiers per node. The `ForgeAgent` and container entrypoint resolve the tier to a concrete model name at invocation time.

For the PoC, the tier-to-model mapping is hardcoded:

| Tier | Model | Use Case |
|------|-------|----------|
| `fast` | `claude-haiku-4-5@20251001` | Simple classification, formatting, PR descriptions |
| `standard` | `claude-sonnet-4-5@20250929` | Spec generation, epic decomposition, task generation, reviews, CI fixes |
| `advanced` | `claude-opus-4-5@20251101` | PRD generation, code implementation, review feedback implementation |

### Detailed Design

#### `ModelTier` enum

```python
from enum import StrEnum

class ModelTier(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    ADVANCED = "advanced"
```

Added to `src/forge/models/workflow.py` alongside the existing enums.

#### Default node-to-tier mapping

A module-level dict in a new file `src/forge/models/model_tiers.py`:

```python
NODE_DEFAULT_TIERS: dict[str, ModelTier] = {
    # Planning stages — need deep reasoning
    "generate_prd": ModelTier.ADVANCED,
    "regenerate_prd": ModelTier.ADVANCED,
    "generate_spec": ModelTier.STANDARD,
    "regenerate_spec": ModelTier.STANDARD,
    "decompose_epics": ModelTier.STANDARD,
    "regenerate_all_epics": ModelTier.STANDARD,
    "update_single_epic": ModelTier.STANDARD,
    "generate_tasks": ModelTier.STANDARD,
    "regenerate_all_tasks": ModelTier.STANDARD,
    "update_single_task": ModelTier.STANDARD,

    # Bug workflow
    "analyze_bug": ModelTier.ADVANCED,
    "regenerate_rca": ModelTier.ADVANCED,

    # Implementation — needs strongest reasoning
    "implement_task": ModelTier.ADVANCED,
    "implement_bug_fix": ModelTier.ADVANCED,
    "implement_review": ModelTier.ADVANCED,

    # Reviews and fixes — solid reasoning but not maximum
    "local_review": ModelTier.STANDARD,
    "ai_review": ModelTier.STANDARD,
    "attempt_ci_fix": ModelTier.STANDARD,
    "validation_fix": ModelTier.STANDARD,

    # Simple tasks — fast model sufficient
    "answer_question": ModelTier.STANDARD,
    "generate_pr_body": ModelTier.FAST,
    "sync_pr_description": ModelTier.FAST,
}
```

Any node not in this mapping defaults to `ModelTier.STANDARD`.

#### Tier-to-model resolution (PoC)

A function in `model_tiers.py`:

```python
TIER_TO_MODEL: dict[ModelTier, str] = {
    ModelTier.FAST: "claude-haiku-4-5@20251001",
    ModelTier.STANDARD: "claude-sonnet-4-5@20250929",
    ModelTier.ADVANCED: "claude-opus-4-5@20251101",
}

def resolve_model_for_node(
    node_name: str,
    overrides: dict[str, str] | None = None,
) -> str:
    """Resolve the concrete model name for a workflow node.

    Args:
        node_name: The workflow node name (e.g., "generate_prd").
        overrides: Optional node-to-tier overrides from config.

    Returns:
        Concrete model name string.
    """
    # Check for admin override first
    if overrides and node_name in overrides:
        tier_name = overrides[node_name]
        tier = ModelTier(tier_name)
    else:
        tier = NODE_DEFAULT_TIERS.get(node_name, ModelTier.STANDARD)

    return TIER_TO_MODEL[tier]
```

#### Config — `NODE_MODEL_OVERRIDES`

A new setting in `config.py`:

```python
node_model_overrides: str = Field(
    default="",
    description=(
        "JSON dict mapping node names to model tiers. "
        "Overrides the default tier for specific nodes. "
        'Example: \'{"generate_prd": "standard", "implement_task": "standard"}\''
    ),
)

@property
def parsed_node_model_overrides(self) -> dict[str, str]:
    """Parse node model overrides from JSON string."""
    if not self.node_model_overrides:
        return {}
    import json
    return json.loads(self.node_model_overrides)
```

Usage in `.env`:

```bash
# Override specific nodes (e.g., downgrade everything to standard for cost testing)
NODE_MODEL_OVERRIDES={"generate_prd": "standard", "implement_task": "standard"}

# Or upgrade CI fix to advanced for a tricky project
NODE_MODEL_OVERRIDES={"attempt_ci_fix": "advanced"}
```

#### Integration — `ForgeAgent`

`ForgeAgent._create_model()` currently takes an optional `model_name` parameter. The change is minimal — workflow nodes pass the resolved model name when invoking the agent:

```python
# In a workflow node (e.g., prd_generation.py)
model_name = resolve_model_for_node(
    "generate_prd",
    overrides=settings.parsed_node_model_overrides,
)
result = await agent.run_task(
    prompt=prompt,
    model_name=model_name,
    ...
)
```

`ForgeAgent._create_model()` already handles the `model_name` parameter — it falls back to `settings.claude_model` when `None`. With this change, callers pass the tier-resolved model explicitly instead of relying on the fallback.

#### Integration — Container entrypoint

For container-based nodes (`implement_task`, `implement_bug_fix`, `local_review`, `attempt_ci_fix`, `validation_fix`), the model is passed via the `LLM_MODEL` environment variable. The `ContainerRunner._build_env_vars()` method currently sets this from `settings.container_model`. The change:

```python
# In ContainerRunner or the workflow node that calls it
def _build_env_vars(self, config, container_skill_paths="", model_name=None):
    env = {}
    # Use node-specific model if provided, else fall back to container default
    env["LLM_MODEL"] = model_name or self.settings.container_model
    ...
```

The workflow node resolves the tier and passes `model_name` to `ContainerRunner.run()`.

#### What doesn't change

- `LLM_MODEL` env var still works as the global fallback (becomes the `standard` tier default in practice)
- `CONTAINER_LLM_MODEL` still works as the container fallback
- `detect_model_provider()` still works — it operates on concrete model names, which is what `resolve_model_for_node()` returns
- Vertex AI / Gemini support is unaffected — the tier system maps to model name strings, and the provider detection + client creation path is unchanged

### User Experience

**Default behavior (no config changes):**

```
[Forge processing AISOS-376]
generate_prd       → claude-opus-4-5@20251101 (advanced)
spec_approval_gate → (no LLM call)
generate_spec      → claude-sonnet-4-5@20250929 (standard)
implement_task     → claude-opus-4-5@20251101 (advanced)
generate_pr_body   → claude-haiku-4-5@20251001 (fast)
ci_evaluator       → (no LLM call — just GitHub API)
attempt_ci_fix     → claude-sonnet-4-5@20250929 (standard)
```

**Admin overrides for cost testing:**

```bash
# .env — downgrade everything to standard for a cost-sensitive project
NODE_MODEL_OVERRIDES={"generate_prd": "standard", "implement_task": "standard", "analyze_bug": "standard"}
```

**Admin overrides for quality testing:**

```bash
# .env — upgrade CI fix for a project with complex CI
NODE_MODEL_OVERRIDES={"attempt_ci_fix": "advanced"}
```

## Future Directions

This proposal intentionally scopes the PoC to three hardcoded tiers mapped to Anthropic models. The following extensions are anticipated but not designed here:

### Configurable tier-to-model mapping

Replace the hardcoded `TIER_TO_MODEL` dict with a config-driven mapping, allowing admins to point tiers at any model:

```bash
MODEL_TIER_FAST=gemini-2.5-flash
MODEL_TIER_STANDARD=claude-sonnet-4-5@20250929
MODEL_TIER_ADVANCED=claude-opus-4-5@20251101
```

This enables mixed-provider setups (e.g., Gemini for fast tasks, Claude for advanced) and local model integration (e.g., a local Ollama instance for fast-tier tasks).

### User-defined tier names

Allow admins to define arbitrary tier names beyond fast/standard/advanced. A project might define `code-review`, `planning`, `implementation` as tiers with different model assignments.

### Per-skill model tier overrides

Skills could declare a preferred tier in their YAML block:

```yaml
model_tier: advanced
```

This would override the central node-to-tier mapping for the specific skill. The resolution order would be: skill override → admin config override → node default.

### Skill-model compatibility metadata

Skills could declare which models they've been tested and approved on:

```yaml
tested_on:
  - claude-sonnet-4-5
  - gemini-2.5-pro
  - qwen-3.5
```

The orchestrator could warn or block when a skill is invoked with an untested model, ensuring quality. This would link to model families rather than symbolic tier names, since compatibility is about the model's actual capabilities, not the tier abstraction.

## Alternatives Considered

| Alternative | Pros | Cons | Why Not |
|-------------|------|------|---------|
| Keep single model, rely on `CONTAINER_LLM_MODEL` | No code changes | Can't differentiate between simple and complex orchestrator tasks; all container tasks share one model | Current pain point |
| Per-node env vars (e.g., `MODEL_GENERATE_PRD=opus`) | No abstraction layer needed | Dozens of env vars; no grouping; hard to reason about cost/capability tradeoffs | Doesn't scale; tiers provide meaningful grouping |
| Model selection in workflow graph edges | Graph encodes the model choice | Mixes infrastructure concerns into workflow logic; harder to override without graph changes | Separation of concerns — model selection is config, not workflow |
| Fully configurable tier system (no hardcoding) | Future-proof from day one | Over-engineered for PoC; more config surface to test; delays delivery | YAGNI — hardcoded tiers are sufficient for the PoC; migration path is clear |

## Implementation Plan

### Phases

1. **Phase 1: ModelTier enum and resolution** — Add `ModelTier` enum, `NODE_DEFAULT_TIERS` mapping, `TIER_TO_MODEL` mapping, and `resolve_model_for_node()` function. Add `NODE_MODEL_OVERRIDES` to `Settings`. Unit tests for resolution with and without overrides. (~half day)

2. **Phase 2: ForgeAgent integration** — Update workflow nodes that invoke `ForgeAgent` to resolve and pass the tier-specific model name. Update `ForgeAgent.run_task()`, `generate_prd()`, `generate_spec()`, etc. to accept and forward the model name. (~half day)

3. **Phase 3: Container integration** — Update `ContainerRunner._build_env_vars()` and `ContainerRunner.run()` to accept a model name. Update container-invoking workflow nodes (`implement_task`, `local_review`, `attempt_ci_fix`, etc.) to resolve and pass the tier-specific model. (~half day)

4. **Phase 4: Tests** — Unit tests for tier resolution, override parsing, model passthrough in ForgeAgent, model passthrough in ContainerRunner. Flow test verifying different nodes use different models. (~half day)

### Dependencies

- [ ] All workflow nodes that invoke `ForgeAgent` or `ContainerRunner` need to be updated to pass `model_name`
- [ ] `ForgeAgent._create_model()` already supports `model_name` parameter — no change needed
- [ ] Container entrypoint already reads `LLM_MODEL` from env — no change needed
- [ ] The existing `LLM_MODEL` and `CONTAINER_LLM_MODEL` settings should continue to work as fallbacks

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Haiku (fast tier) produces low-quality output for tasks assumed to be simple | Med | Low | Easy to override via `NODE_MODEL_OVERRIDES`; defaults are conservative (only PR descriptions and comment sync use fast) |
| Opus (advanced tier) is rate-limited or expensive, slowing down workflows | Med | Med | Admin can downgrade all nodes to standard via overrides; rate limiting already handled by ForgeAgent retry logic |
| Default tier mapping is wrong for some projects | High | Low | Override mechanism exists specifically for this; field testing will refine defaults |
| Adding model_name parameter to many workflow nodes is a large diff | Med | Low | Mechanical change — each node just adds one `resolve_model_for_node()` call; no logic changes |

## Open Questions

- [ ] Should the tier resolution be logged at INFO level so admins can see which model each node is using? Useful for debugging cost/quality, but could be noisy.
- [ ] When `LLM_MODEL` is set to a non-Anthropic model (e.g., Gemini), should the tier system be bypassed entirely (all nodes use `LLM_MODEL`), or should it still apply with the hardcoded Anthropic models for fast/advanced?
- [ ] Should there be a `forge check-tiers` CLI command that prints the resolved model for every node (incorporating overrides)?

## References

- `src/forge/config.py` — current `llm_model`, `container_llm_model` settings
- `src/forge/integrations/agents/agent.py` — `ForgeAgent._create_model()` with existing `model_name` parameter
- `containers/entrypoint.py` — container model selection via `LLM_MODEL` env var
- `src/forge/sandbox/runner.py` — `ContainerRunner._build_env_vars()` setting `LLM_MODEL`
