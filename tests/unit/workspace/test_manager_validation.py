"""Tests for ticket key validation in WorkspaceManager.create_workspace."""

import pytest

from forge.workspace.manager import WorkspaceManager


class TestCreateWorkspaceTicketKeyValidation:
    def test_rejects_path_traversal_ticket_key(self, tmp_path):
        manager = WorkspaceManager(base_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid ticket key"):
            manager.create_workspace(repo_name="org/repo", ticket_key="../../etc-1")

    def test_accepts_valid_ticket_key(self, tmp_path):
        manager = WorkspaceManager(base_dir=tmp_path)
        ws = manager.create_workspace(repo_name="org/repo", ticket_key="FORGE-123")
        assert ws.ticket_key == "FORGE-123"
        assert "FORGE-123" in str(ws.path)
