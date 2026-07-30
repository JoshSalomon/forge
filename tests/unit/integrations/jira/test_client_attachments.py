"""Unit tests for JiraClient attachment operations."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from forge.integrations.jira.client import JiraClient


class TestJiraClientAttachments:
    """Tests for attachment helper methods in JiraClient."""

    @pytest.fixture
    def mock_client(self):
        """Create JiraClient with mocked settings."""
        with patch("forge.integrations.jira.client.get_settings") as mock_settings:
            mock_settings.return_value.jira_base_url = "https://test.atlassian.net"
            mock_settings.return_value.jira_api_token = MagicMock()
            mock_settings.return_value.jira_api_token.get_secret_value.return_value = "token"
            mock_settings.return_value.jira_user_email = "test@example.com"

            client = JiraClient()
            return client

    @pytest.mark.asyncio
    async def test_list_attachments_success(self, mock_client):
        """list_attachments successfully fetches and parses attachment list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "fields": {
                "attachment": [
                    {
                        "id": "10001",
                        "filename": "spec.md",
                        "content": "https://test.atlassian.net/rest/api/3/attachment/content/10001",
                        "size": 1234,
                    },
                    {
                        "id": "10002",
                        "filename": "design.png",
                        "content": "https://test.atlassian.net/rest/api/3/attachment/content/10002",
                        "size": 5678,
                    },
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(mock_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            attachments = await mock_client.list_attachments("TEST-123")

        assert len(attachments) == 2
        assert attachments[0]["id"] == "10001"
        assert attachments[0]["filename"] == "spec.md"
        assert attachments[0]["content"] == "https://test.atlassian.net/rest/api/3/attachment/content/10001"
        assert attachments[0]["content_url"] == "https://test.atlassian.net/rest/api/3/attachment/content/10001"

        assert attachments[1]["id"] == "10002"
        assert attachments[1]["filename"] == "design.png"

        mock_http.request.assert_called_once_with(
            "GET",
            "/issue/TEST-123",
            params={"fields": "attachment"},
        )

    @pytest.mark.asyncio
    async def test_download_attachment_success(self, mock_client):
        """download_attachment successfully fetches binary content."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake binary file content"
        mock_response.raise_for_status = MagicMock()

        content_url = "https://test.atlassian.net/rest/api/3/attachment/content/10001"

        with patch.object(mock_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            content = await mock_client.download_attachment(content_url)

        assert content == b"fake binary file content"
        mock_http.request.assert_called_once_with("GET", content_url)

    @pytest.mark.asyncio
    async def test_delete_attachment_success(self, mock_client):
        """delete_attachment successfully deletes specified attachment."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()

        with patch.object(mock_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            await mock_client.delete_attachment("10001")

        mock_http.request.assert_called_once_with("DELETE", "/attachment/10001")

    @pytest.mark.asyncio
    async def test_add_attachment_success(self, mock_client):
        """add_attachment successfully uploads file as multipart/form-data and sets token header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": "10003",
                "filename": "test-file.json",
                "content": "https://test.atlassian.net/rest/api/3/attachment/content/10003",
            }
        ]
        mock_response.raise_for_status = MagicMock()

        file_content = b'{"key": "value"}'

        with patch.object(mock_client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.headers = httpx.Headers()
            mock_http.request = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            result = await mock_client.add_attachment(
                issue_key="TEST-123",
                filename="test-file.json",
                content=file_content,
            )

        assert result["id"] == "10003"
        assert result["filename"] == "test-file.json"

        mock_http.request.assert_called_once()
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "/issue/TEST-123/attachments"
        assert kwargs["headers"]["X-Atlassian-Token"] == "no-check"
        assert kwargs["files"] == {"file": ("test-file.json", file_content, "application/json")}
