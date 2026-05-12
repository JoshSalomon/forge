# Orchestrator Integration Tests

This directory contains integration tests for the workflow orchestrator and related node implementations.

## Test Files

### test_task_implementation_status.py

Integration tests for task implementation status comments (SC-002 specification).

**Purpose**: Verify that Jira status comments are posted correctly during task implementation workflow execution.

**Test Scenarios**:

1. **TS-003: Single task receives start and completion comments**
   - `test_single_task_receives_start_comment`: Verifies "🔨 Forge is implementing this task." is posted
   - `test_single_task_receives_completion_comment_on_success`: Verifies both start and "✅ Implementation complete. Running local code review before PR." comments
   - `test_single_task_no_completion_comment_on_failure`: Verifies no completion comment when task fails

2. **TS-013: Multiple tasks receive independent comments (no cross-contamination)**
   - `test_multiple_tasks_receive_independent_start_comments`: Verifies each task gets its own start comment with correct task_key
   - `test_multiple_tasks_receive_independent_completion_comments`: Verifies each task gets completion comments independently without cross-contamination

3. **Failure Scenarios**
   - `test_task_implementation_fails_midway_no_completion_comment`: Verifies no completion comment when container fails
   - `test_multiple_tasks_partial_failure_only_successful_get_completion`: Verifies only successful tasks get completion comments

4. **Error Handling**
   - `test_workflow_continues_when_start_comment_posting_fails`: Verifies workflow continues when Jira start comment fails
   - `test_workflow_continues_when_completion_comment_posting_fails`: Verifies workflow continues when Jira completion comment fails
   - `test_workflow_continues_when_all_comment_posting_fails`: Verifies workflow continues even with complete Jira outage

**Running the tests**:
```bash
uv run pytest tests/integration/orchestrator/test_task_implementation_status.py -v
```

**Mock Strategy**:
- JiraClient is mocked to avoid external API calls
- ContainerRunner is mocked with configurable success/failure results
- Tests verify exact comment text matches specification
- Tests verify workflow continues despite Jira failures (error suppression)

### test_local_review_status_comments.py

Integration tests for local review status comments.

**Purpose**: Verify that Jira status comments are posted correctly during local review workflow execution, covering first pass with no issues, multiple fix passes, and pass number tracking.

**Test Scenarios**:

1. **TS-004: First pass with no issues posts only initial comment**
   - `test_first_pass_no_issues_posts_only_initial_comment`: Verifies only "🔍 Running local code review on changes before creating PR." is posted when first pass finds no issues

2. **TS-005: 3-pass scenario posts initial + 3 fix comments with correct numbering**
   - `test_three_pass_scenario_posts_all_comments_with_correct_numbering`: Verifies initial + fix comments for multiple passes (with MAX_REVIEW_ATTEMPTS=2)
   - `test_three_pass_scenario_with_max_attempts_override`: Verifies 3-pass scenario by overriding MAX_REVIEW_ATTEMPTS to 3

3. **5+ pass scenario posts all fix comments with correct incrementing numbers**
   - `test_five_plus_pass_scenario_posts_all_comments_with_incrementing_numbers`: Verifies 6 passes post correct comments with incrementing pass numbers (pass 2, 3, 4, 5, 6)

4. **Pass number resets between features**
   - `test_pass_number_resets_when_transitioning_from_implementation_to_local_review`: Verifies pass_number resets to 1 when implementation.py transitions to local_review
   - `test_pass_number_resets_for_new_feature`: Verifies pass_number initializes to 1 for new features

5. **Pass number persists across iterations within same feature**
   - `test_pass_number_persists_and_increments_within_same_feature`: Verifies pass_number persists and increments across review iterations
   - `test_pass_number_increments_correctly_across_multiple_iterations`: Verifies pass_number increments correctly across 4 passes

6. **Error Handling**
   - `test_workflow_continues_when_comment_posting_fails`: Verifies workflow continues when initial comment posting fails
   - `test_workflow_continues_when_fix_comment_posting_fails`: Verifies workflow continues when fix pass comment posting fails

**Running the tests**:
```bash
uv run pytest tests/integration/orchestrator/test_local_review_status_comments.py -v
```

**Mock Strategy**:
- JiraClient is mocked to avoid external API calls
- ContainerRunner is mocked with configurable unfixed issues results
- GitOperations is mocked to simulate commits
- Tests verify exact comment text matches specification
- Tests verify pass_number tracking across iterations
- Tests verify workflow continues despite Jira failures (error suppression)

### test_workflow_execution.py

Integration tests for LangGraph workflow execution.

**Status**: Currently skipped pending update for pluggable workflows architecture.

### test_task_handoff.py

Integration tests for task handoff between workflow nodes.

## Running All Integration Tests

```bash
# Run all orchestrator integration tests
uv run pytest tests/integration/orchestrator/ -v

# Run specific test file
uv run pytest tests/integration/orchestrator/test_task_implementation_status.py -v

# Run specific test class
uv run pytest tests/integration/orchestrator/test_task_implementation_status.py::TestTaskImplementationStatusCommentsTS003 -v

# Run specific test
uv run pytest tests/integration/orchestrator/test_task_implementation_status.py::TestTaskImplementationStatusCommentsTS003::test_single_task_receives_start_comment -v
```

## Test Maintenance

When updating task implementation behavior:

1. Update the corresponding tests in `test_task_implementation_status.py`
2. Ensure exact comment text matches the specification
3. Verify error handling tests still pass (workflow should never fail due to comment posting)
4. Run the full test suite to check for regressions

## Dependencies

These integration tests require:
- pytest
- pytest-asyncio (for async test support)
- unittest.mock (standard library)
- forge.workflow modules
- forge.integrations.jira modules

## Test Coverage Checklist

### Task Implementation Status Comments
- [x] TS-003: Single task receives both start and completion comments
- [x] TS-013: Multiple tasks receive independent comments (no cross-contamination)
- [x] No completion comment when task implementation fails
- [x] Workflow continues when comment posting fails
- [x] Exact comment text verification
- [x] Error logging verification (via caplog fixture)

### Local Review Status Comments
- [x] TS-004: First pass with no issues posts only initial comment
- [x] TS-005: 3-pass scenario posts initial + fix comments with correct numbering
- [x] 5+ pass scenario posts all fix comments with correct incrementing numbers
- [x] Pass number resets between features
- [x] Pass number persists across iterations within same feature
- [x] Workflow continues when comment posting fails
