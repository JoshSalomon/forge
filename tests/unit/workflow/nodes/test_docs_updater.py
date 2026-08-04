"""Unit tests for docs_updater node."""

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
