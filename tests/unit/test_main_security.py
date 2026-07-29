"""Tests for security-related configuration in create_app."""

from unittest.mock import patch

from starlette.middleware.cors import CORSMiddleware


class TestCORSConfiguration:
    def test_allow_credentials_is_false(self):
        """CORS must not reflect origin with credentials."""
        from forge.main import create_app

        app = create_app()
        cors = next(
            m
            for m in app.user_middleware
            if m.cls is CORSMiddleware
        )
        assert cors.kwargs["allow_credentials"] is False


class TestOpenAPIDocsConfiguration:
    def test_docs_enabled_by_default(self):
        from forge.main import create_app

        app = create_app()
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

    def test_docs_disabled_when_setting_is_true(self):
        from forge.config import Settings

        mock_settings = Settings(
            _env_file=None,
            disable_openapi_docs=True,
            jira_url="https://jira.example.com",
            jira_email="test@example.com",
            jira_api_token="fake",
            github_app_id="1",
            github_private_key="fake-key",
            github_webhook_secret="secret",
            jira_webhook_secret="secret",
            anthropic_api_key="fake",
        )

        with patch("forge.main.get_settings", return_value=mock_settings):
            from forge.main import create_app

            app = create_app()

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None
