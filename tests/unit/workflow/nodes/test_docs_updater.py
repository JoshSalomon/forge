"""Unit tests for docs_updater and update_docs_repo nodes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.workflow_states import make_workflow_state


class TestUpdateDocumentationRouting:
    """Tests for update_documentation (same-repo, pre-PR) routing logic."""

    @pytest.mark.asyncio
    async def test_skips_when_no_workspace(self):
        """Routes to create_pr when no workspace exists."""
        from forge.workflow.nodes.docs_updater import update_documentation

        state = make_workflow_state(
            current_node="local_review",
            workspace_path=None,
        )
        result = await update_documentation(state)
        assert result["current_node"] == "create_pr"

    @pytest.mark.asyncio
    async def test_routes_to_create_pr_on_success(self):
        """Routes to create_pr after successful same-repo update."""
        from forge.workflow.nodes.docs_updater import update_documentation

        state = make_workflow_state(
            current_node="local_review",
            workspace_path="/tmp/test-workspace",
            current_repo="acme/backend",
            context={"branch_name": "forge/test-123", "guardrails": ""},
        )

        mock_result = MagicMock()
        mock_result.success = True

        with (
            patch("forge.workflow.nodes.docs_updater.get_settings") as mock_settings,
            patch("forge.workflow.nodes.docs_updater.ContainerRunner") as mock_runner_cls,
            patch("forge.workflow.nodes.docs_updater.GitOperations") as mock_git_cls,
            patch("forge.workflow.nodes.docs_updater.load_prompt", return_value="test prompt"),
        ):
            mock_settings.return_value = MagicMock()
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(return_value=mock_result)
            mock_runner_cls.return_value = mock_runner
            mock_git = MagicMock()
            mock_git.has_uncommitted_changes.return_value = False
            mock_git_cls.return_value = mock_git

            result = await update_documentation(state)

        assert result["current_node"] == "create_pr"
        assert result.get("last_error") is None

    @pytest.mark.asyncio
    async def test_routes_to_create_pr_on_failure(self):
        """Routes to create_pr even when container fails (non-blocking)."""
        from forge.workflow.nodes.docs_updater import update_documentation

        state = make_workflow_state(
            current_node="local_review",
            workspace_path="/tmp/test-workspace",
            current_repo="acme/backend",
            context={"branch_name": "forge/test-123", "guardrails": ""},
        )

        with (
            patch("forge.workflow.nodes.docs_updater.get_settings") as mock_settings,
            patch("forge.workflow.nodes.docs_updater.ContainerRunner") as mock_runner_cls,
            patch("forge.workflow.nodes.docs_updater.load_prompt", return_value="test prompt"),
        ):
            mock_settings.return_value = MagicMock()
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(side_effect=RuntimeError("container failed"))
            mock_runner_cls.return_value = mock_runner

            result = await update_documentation(state)

        assert result["current_node"] == "create_pr"
        assert result.get("last_error") is None


class TestUpdateDocsRepoRouting:
    """Tests for update_docs_repo (separate repo, post-merge) routing logic."""

    @pytest.mark.asyncio
    async def test_skips_when_no_docs_repo(self):
        """Returns state unchanged when forge.docs_repo is not set."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
        )

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
        ):
            mock_settings.return_value = MagicMock()
            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value=None)
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            result = await update_docs_repo(state)

        assert result is state

    @pytest.mark.asyncio
    async def test_skips_when_docs_repo_equals_current(self):
        """Returns state unchanged when docs_repo matches current_repo."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
        )

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
        ):
            mock_settings.return_value = MagicMock()
            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/backend")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            result = await update_docs_repo(state)

        assert result is state

    @pytest.mark.asyncio
    async def test_non_blocking_on_failure(self):
        """Returns state unchanged when docs repo update fails."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
        )

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient") as mock_github_cls,
            patch("forge.workflow.nodes.update_docs_repo.WorkspaceManager") as mock_manager_cls,
        ):
            mock_settings.return_value = MagicMock()
            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/docs")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira
            mock_github = MagicMock()
            mock_github.get_repository = AsyncMock(return_value={"default_branch": "main"})
            mock_github.close = AsyncMock()
            mock_github_cls.return_value = mock_github
            mock_manager = MagicMock()
            mock_manager.create_workspace.side_effect = RuntimeError("clone failed")
            mock_manager_cls.return_value = mock_manager

            result = await update_docs_repo(state)

        assert result is state

    @pytest.mark.asyncio
    async def test_creates_pr_and_returns_docs_pr_url(self):
        """Returns state with docs_pr_url set after a successful docs repo update."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
            fork_owner="forge-bot",
            fork_repo="backend",
            context={"branch_name": "forge/proj-123", "guardrails": ""},
        )

        mock_workspace = MagicMock()
        mock_workspace.path = MagicMock()

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient") as mock_github_cls,
            patch("forge.workflow.nodes.update_docs_repo.WorkspaceManager") as mock_manager_cls,
            patch("forge.workflow.nodes.update_docs_repo.GitOperations") as mock_git_cls,
            patch("forge.workflow.nodes.update_docs_repo.ContainerRunner") as mock_runner_cls,
            patch("forge.workflow.nodes.update_docs_repo.load_prompt", return_value="task prompt"),
            patch("forge.workflow.nodes.update_docs_repo._configure_forge_exclude"),
            patch(
                "forge.workflow.nodes.update_docs_repo._create_docs_pr",
                new=AsyncMock(return_value="https://github.com/acme/docs/pull/42"),
            ),
        ):
            mock_settings.return_value = MagicMock()

            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/docs")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            mock_github = MagicMock()
            mock_github.get_repository = AsyncMock(return_value={"default_branch": "main"})
            mock_github.close = AsyncMock()
            mock_github_cls.return_value = mock_github

            mock_manager = MagicMock()
            mock_manager.create_workspace.return_value = mock_workspace
            mock_manager_cls.return_value = mock_manager

            mock_git = MagicMock()
            mock_git.has_uncommitted_changes.return_value = False
            mock_git.has_commits_ahead.return_value = True
            mock_git_cls.return_value = mock_git

            mock_runner = MagicMock()
            mock_runner.run = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await update_docs_repo(state)

        assert result["docs_pr_url"] == "https://github.com/acme/docs/pull/42"

    @pytest.mark.asyncio
    async def test_skips_pr_when_no_commits(self):
        """Returns state unchanged when the agent made no documentation changes."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
            fork_owner="forge-bot",
            fork_repo="backend",
            context={"branch_name": "forge/proj-123", "guardrails": ""},
        )

        mock_workspace = MagicMock()
        mock_workspace.path = MagicMock()

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient") as mock_github_cls,
            patch("forge.workflow.nodes.update_docs_repo.WorkspaceManager") as mock_manager_cls,
            patch("forge.workflow.nodes.update_docs_repo.GitOperations") as mock_git_cls,
            patch("forge.workflow.nodes.update_docs_repo.ContainerRunner") as mock_runner_cls,
            patch("forge.workflow.nodes.update_docs_repo.load_prompt", return_value="task prompt"),
            patch("forge.workflow.nodes.update_docs_repo._configure_forge_exclude"),
        ):
            mock_settings.return_value = MagicMock()

            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/docs")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            mock_github = MagicMock()
            mock_github.get_repository = AsyncMock(return_value={"default_branch": "main"})
            mock_github.close = AsyncMock()
            mock_github_cls.return_value = mock_github

            mock_manager = MagicMock()
            mock_manager.create_workspace.return_value = mock_workspace
            mock_manager_cls.return_value = mock_manager

            mock_git = MagicMock()
            mock_git.has_uncommitted_changes.return_value = False
            mock_git.has_commits_ahead.return_value = False
            mock_git_cls.return_value = mock_git

            mock_runner = MagicMock()
            mock_runner.run = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            result = await update_docs_repo(state)

        assert result is state
        assert result.get("docs_pr_url") is None

    @pytest.mark.asyncio
    async def test_origin_fallback_when_no_fork_info(self):
        """Uses origin checkout when fork_owner/fork_repo are absent in state."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="post_merge_summary",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
            # No fork_owner / fork_repo — same-repo PR
            context={"branch_name": "forge/proj-123", "guardrails": ""},
        )

        mock_workspace = MagicMock()
        mock_workspace.path = MagicMock()

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient") as mock_github_cls,
            patch("forge.workflow.nodes.update_docs_repo.WorkspaceManager") as mock_manager_cls,
            patch("forge.workflow.nodes.update_docs_repo.GitOperations") as mock_git_cls,
            patch("forge.workflow.nodes.update_docs_repo.ContainerRunner") as mock_runner_cls,
            patch("forge.workflow.nodes.update_docs_repo.load_prompt", return_value="task prompt"),
            patch("forge.workflow.nodes.update_docs_repo._configure_forge_exclude"),
        ):
            mock_settings.return_value = MagicMock()

            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/docs")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            mock_github = MagicMock()
            mock_github.get_repository = AsyncMock(return_value={"default_branch": "main"})
            mock_github.close = AsyncMock()
            mock_github_cls.return_value = mock_github

            mock_manager = MagicMock()
            mock_manager.create_workspace.return_value = mock_workspace
            mock_manager_cls.return_value = mock_manager

            mock_git = MagicMock()
            mock_git.has_uncommitted_changes.return_value = False
            mock_git.has_commits_ahead.return_value = False
            mock_git_cls.return_value = mock_git

            mock_runner = MagicMock()
            mock_runner.run = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            await update_docs_repo(state)

        mock_git.add_fork_remote.assert_not_called()
        mock_git.checkout_branch.assert_any_call("forge/proj-123", remote="origin")

    @pytest.mark.asyncio
    async def test_malformed_current_repo_returns_state_cleanly(self):
        """Returns state unchanged when current_repo is empty or missing slash."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="malformedrepo",
            ticket_key="PROJ-123",
        )

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
        ):
            mock_settings.return_value = MagicMock()
            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/docs")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            result = await update_docs_repo(state)

        assert result is state

    @pytest.mark.asyncio
    async def test_deleted_branch_checkout_fallback_to_merge_commit(self):
        """Falls back to resolving and checking out the merge commit SHA via the GitHub PR API under a GitError."""
        from forge.workflow.nodes.update_docs_repo import update_docs_repo
        from forge.workspace.git_ops import GitError

        state = make_workflow_state(
            current_node="human_review_gate",
            current_repo="acme/backend",
            ticket_key="PROJ-123",
            current_pr_number="42",
            context={"branch_name": "forge/proj-123", "guardrails": ""},
        )

        mock_workspace = MagicMock()
        mock_workspace.path = MagicMock()

        with (
            patch("forge.workflow.nodes.update_docs_repo.get_settings") as mock_settings,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
            patch("forge.workflow.nodes.update_docs_repo.extract_project_key", return_value="PROJ"),
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient") as mock_github_cls,
            patch("forge.workflow.nodes.update_docs_repo.WorkspaceManager") as mock_manager_cls,
            patch("forge.workflow.nodes.update_docs_repo.GitOperations") as mock_git_cls,
            patch("forge.workflow.nodes.update_docs_repo.ContainerRunner") as mock_runner_cls,
            patch("forge.workflow.nodes.update_docs_repo.load_prompt", return_value="task prompt"),
            patch("forge.workflow.nodes.update_docs_repo._configure_forge_exclude"),
        ):
            mock_settings.return_value = MagicMock()

            mock_jira = MagicMock()
            mock_jira.get_project_docs_repo = AsyncMock(return_value="acme/docs")
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            # First client for metadata, second for pull request
            mock_github = MagicMock()
            mock_github.get_repository = AsyncMock(return_value={"default_branch": "main"})
            mock_github.get_pull_request = AsyncMock(return_value={"merge_commit_sha": "abcdef123"})
            mock_github.close = AsyncMock()
            mock_github_cls.return_value = mock_github

            mock_manager = MagicMock()
            mock_manager.create_workspace.return_value = mock_workspace
            mock_manager_cls.return_value = mock_manager

            mock_git = MagicMock()
            mock_git.checkout_branch.side_effect = GitError("Branch not found")
            mock_git.has_uncommitted_changes.return_value = False
            mock_git.has_commits_ahead.return_value = False
            mock_git_cls.return_value = mock_git

            mock_runner = MagicMock()
            mock_runner.run = AsyncMock()
            mock_runner_cls.return_value = mock_runner

            await update_docs_repo(state)

        # Verify checkout_commit was called with resolved merge commit SHA
        mock_git.checkout_commit.assert_called_once_with("abcdef123")

    @pytest.mark.asyncio
    async def test_create_docs_pr_directly(self):
        """Directly tests the _create_docs_pr helper to verify proper arguments."""
        from forge.config import Settings
        from forge.workflow.nodes.update_docs_repo import _create_docs_pr

        ticket_key = "PROJ-123"
        docs_repo = "acme/docs"
        branch_name = "forge/proj-123"
        base_branch = "main"
        settings = MagicMock(spec=Settings)

        mock_git = MagicMock()

        mock_fork_data = {
            "owner": {"login": "fork-owner"},
            "name": "docs-fork",
        }
        mock_pr_data = {
            "html_url": "https://github.com/acme/docs/pull/123",
            "number": 123,
        }

        with (
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient") as mock_github_cls,
            patch("forge.workflow.nodes.update_docs_repo.JiraClient") as mock_jira_cls,
        ):
            mock_github = MagicMock()
            mock_github.get_or_create_fork = AsyncMock(return_value=mock_fork_data)
            mock_github.sync_fork_with_upstream = AsyncMock()
            mock_github.create_pull_request = AsyncMock(return_value=mock_pr_data)
            mock_github.close = AsyncMock()
            mock_github_cls.return_value = mock_github

            mock_jira = MagicMock()
            mock_jira.add_comment = AsyncMock()
            mock_jira.close = AsyncMock()
            mock_jira_cls.return_value = mock_jira

            pr_url = await _create_docs_pr(
                ticket_key=ticket_key,
                docs_repo=docs_repo,
                git=mock_git,
                branch_name=branch_name,
                base_branch=base_branch,
                settings=settings,
            )

            # Assert results
            assert pr_url == "https://github.com/acme/docs/pull/123"

            # Verify GitHubClient calls
            mock_github.get_or_create_fork.assert_called_once_with("acme", "docs")
            mock_github.sync_fork_with_upstream.assert_called_once_with("fork-owner", "docs-fork")
            mock_github.create_pull_request.assert_called_once_with(
                owner="acme",
                repo="docs",
                title=f"[{ticket_key}] docs: update documentation for code changes",
                body=(
                    f"Automated documentation update for {ticket_key}.\n\n"
                    f"Code changes in the source repository made some documentation "
                    f"files stale. This PR updates them to reflect the current code."
                ),
                head="fork-owner:forge/proj-123",
                base="main",
            )
            mock_github.close.assert_called_once()

            # Verify GitOperations calls
            mock_git.add_fork_remote.assert_called_once_with("fork-owner", "docs-fork")
            mock_git.push_to_fork.assert_called_once_with()

            # Verify JiraClient calls
            mock_jira.add_comment.assert_called_once_with(
                ticket_key,
                "Documentation PR created: [acme/docs#123](https://github.com/acme/docs/pull/123)",
            )
            mock_jira.close.assert_called_once()


class TestExtraMountsInContainerRunner:
    """Tests for extra_mounts parameter in ContainerRunner."""

    def test_extra_mounts_added_to_podman_command(self):
        """Extra mounts are added as read-only volumes to the podman command."""
        from pathlib import Path

        from forge.sandbox.runner import ContainerRunner

        with patch("forge.sandbox.runner.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/usr/bin/podman"
            runner = ContainerRunner()

        cmd = runner._build_podman_command(
            workspace_path=Path("/tmp/workspace"),
            task_file=Path("/tmp/task.json"),
            config=runner._default_config(),
            container_name="test-container",
            extra_mounts=[(Path("/tmp/code-repo"), "/code-repo")],
        )

        assert "-v" in cmd
        mount_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-v"]
        code_mount = [m for m in mount_args if "/code-repo" in m]
        assert len(code_mount) == 1
        assert code_mount[0] == "/tmp/code-repo:/code-repo:ro,Z"

    def test_no_extra_mounts_by_default(self):
        """No extra mounts when parameter is None."""
        from pathlib import Path

        from forge.sandbox.runner import ContainerRunner

        with patch("forge.sandbox.runner.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/usr/bin/podman"
            runner = ContainerRunner()

        cmd = runner._build_podman_command(
            workspace_path=Path("/tmp/workspace"),
            task_file=Path("/tmp/task.json"),
            config=runner._default_config(),
            container_name="test-container",
        )

        mount_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-v"]
        code_mounts = [m for m in mount_args if "/code-repo" in m]
        assert len(code_mounts) == 0


class TestCreateDocsPRHelper:
    """Tests for the _create_docs_pr helper function directly."""

    @pytest.mark.asyncio
    async def test_create_docs_pr_success(self):
        """Verifies fork creation, syncing, pushing, PR creation, and Jira commenting are called with proper arguments."""
        from forge.config import Settings
        from forge.workflow.nodes.update_docs_repo import _create_docs_pr

        mock_settings = MagicMock(spec=Settings)
        mock_git = MagicMock()

        mock_github = MagicMock()
        mock_github.get_or_create_fork = AsyncMock(
            return_value={"owner": {"login": "fork-owner"}, "name": "docs-fork"}
        )
        mock_github.sync_fork_with_upstream = AsyncMock()
        mock_github.create_pull_request = AsyncMock(
            return_value={"html_url": "https://github.com/acme/docs/pull/42", "number": 42}
        )
        mock_github.close = AsyncMock()

        mock_jira = MagicMock()
        mock_jira.add_comment = AsyncMock()
        mock_jira.close = AsyncMock()

        with (
            patch("forge.workflow.nodes.update_docs_repo.GitHubClient", return_value=mock_github),
            patch("forge.workflow.nodes.update_docs_repo.JiraClient", return_value=mock_jira),
        ):
            pr_url = await _create_docs_pr(
                ticket_key="PROJ-123",
                docs_repo="acme/docs",
                git=mock_git,
                branch_name="forge/proj-123",
                base_branch="main",
                settings=mock_settings,
            )

        assert pr_url == "https://github.com/acme/docs/pull/42"
        mock_github.get_or_create_fork.assert_called_once_with("acme", "docs")
        mock_github.sync_fork_with_upstream.assert_called_once_with("fork-owner", "docs-fork")
        mock_git.add_fork_remote.assert_called_once_with("fork-owner", "docs-fork")
        mock_git.push_to_fork.assert_called_once()
        mock_github.create_pull_request.assert_called_once_with(
            owner="acme",
            repo="docs",
            title="[PROJ-123] docs: update documentation for code changes",
            body=(
                "Automated documentation update for PROJ-123.\n\n"
                "Code changes in the source repository made some documentation "
                "files stale. This PR updates them to reflect the current code."
            ),
            head="fork-owner:forge/proj-123",
            base="main",
        )
        mock_jira.add_comment.assert_called_once_with(
            "PROJ-123",
            "Documentation PR created: [acme/docs#42](https://github.com/acme/docs/pull/42)",
        )
