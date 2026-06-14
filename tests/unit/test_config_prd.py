"""Tests for PRD approval configuration settings."""

from forge.config import Settings


class TestPrdApprovalConfig:
    def test_default_prd_approval_mode_is_jira(self):
        settings = Settings(
            jira_base_url="https://test.atlassian.net",
            jira_api_token="test",
            jira_user_email="test@example.com",
            github_token="test",
            anthropic_api_key="test",
        )
        assert settings.prd_approval_mode == "jira"

    def test_prd_uses_github_pr_false_by_default(self):
        settings = Settings(
            jira_base_url="https://test.atlassian.net",
            jira_api_token="test",
            jira_user_email="test@example.com",
            github_token="test",
            anthropic_api_key="test",
        )
        assert settings.prd_uses_github_pr is False

    def test_prd_uses_github_pr_true_when_set(self):
        settings = Settings(
            jira_base_url="https://test.atlassian.net",
            jira_api_token="test",
            jira_user_email="test@example.com",
            github_token="test",
            anthropic_api_key="test",
            prd_approval_mode="github-pr",
        )
        assert settings.prd_uses_github_pr is True

    def test_default_proposals_path(self):
        settings = Settings(
            jira_base_url="https://test.atlassian.net",
            jira_api_token="test",
            jira_user_email="test@example.com",
            github_token="test",
            anthropic_api_key="test",
        )
        assert settings.prd_proposals_path == "proposals"

    def test_default_proposals_repo_is_empty(self):
        settings = Settings(
            jira_base_url="https://test.atlassian.net",
            jira_api_token="test",
            jira_user_email="test@example.com",
            github_token="test",
            anthropic_api_key="test",
        )
        assert settings.prd_proposals_repo == ""
