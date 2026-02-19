"""Unit tests for the wiki import service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.wiki.service import (
    WikiImportError,
    _collect_page_paths,
    _get_ado_credentials,
    full_import,
)


class TestCollectPagePaths:
    """Tests for the _collect_page_paths helper."""

    def test_empty_tree(self) -> None:
        paths: list[str] = []
        _collect_page_paths({}, paths)
        assert paths == []

    def test_flat_tree(self) -> None:
        tree: dict[str, object] = {
            "path": "/",
            "subPages": [
                {"path": "/Page1"},
                {"path": "/Page2"},
            ],
        }
        paths: list[str] = []
        _collect_page_paths(tree, paths)
        assert paths == ["/Page1", "/Page2"]

    def test_nested_tree(self) -> None:
        tree: dict[str, object] = {
            "path": "/",
            "subPages": [
                {
                    "path": "/Section",
                    "subPages": [
                        {"path": "/Section/Child1"},
                        {"path": "/Section/Child2"},
                    ],
                }
            ],
        }
        paths: list[str] = []
        _collect_page_paths(tree, paths)
        assert paths == ["/Section", "/Section/Child1", "/Section/Child2"]

    def test_root_excluded(self) -> None:
        tree: dict[str, object] = {
            "path": "/",
            "subPages": [{"path": "/SomePage"}],
        }
        paths: list[str] = []
        _collect_page_paths(tree, paths)
        assert "/" not in paths
        assert "/SomePage" in paths

    def test_node_without_path(self) -> None:
        tree: dict[str, object] = {
            "subPages": [{"path": "/Page1"}],
        }
        paths: list[str] = []
        _collect_page_paths(tree, paths)
        assert paths == ["/Page1"]

    def test_deep_nesting(self) -> None:
        tree: dict[str, object] = {
            "path": "/",
            "subPages": [
                {
                    "path": "/A",
                    "subPages": [
                        {
                            "path": "/A/B",
                            "subPages": [{"path": "/A/B/C"}],
                        }
                    ],
                }
            ],
        }
        paths: list[str] = []
        _collect_page_paths(tree, paths)
        assert paths == ["/A", "/A/B", "/A/B/C"]


class TestGetAdoCredentials:
    """Tests for _get_ado_credentials."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_encryption_key(self) -> None:
        mock_session = AsyncMock()
        with patch("core.wiki.service.get_settings") as mock_settings:
            mock_settings.return_value.credential_encryption_key = None
            result = await _get_ado_credentials(uuid4(), mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_credential(self) -> None:
        mock_session = AsyncMock()
        with (
            patch("core.wiki.service.get_settings") as mock_settings,
            patch("core.wiki.service.CredentialService") as mock_cred_cls,
        ):
            mock_settings.return_value.credential_encryption_key = "key"
            mock_cred_service = AsyncMock()
            mock_cred_service.get_credential_with_metadata.return_value = None
            mock_cred_cls.return_value = mock_cred_service
            result = await _get_ado_credentials(uuid4(), mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_org_url_with_project(self) -> None:
        mock_session = AsyncMock()
        with (
            patch("core.wiki.service.get_settings") as mock_settings,
            patch("core.wiki.service.CredentialService") as mock_cred_cls,
        ):
            mock_settings.return_value.credential_encryption_key = "key"
            mock_cred_service = AsyncMock()
            mock_cred_service.get_credential_with_metadata.return_value = (
                "my-pat",
                {"organization_url": "https://dev.azure.com/MyOrg/MyProject"},
            )
            mock_cred_cls.return_value = mock_cred_service
            result = await _get_ado_credentials(uuid4(), mock_session)

        assert result is not None
        pat, org_url, project = result
        assert pat == "my-pat"
        assert org_url == "https://dev.azure.com/MyOrg"
        assert project == "MyProject"

    @pytest.mark.asyncio
    async def test_returns_none_when_org_url_missing(self) -> None:
        mock_session = AsyncMock()
        with (
            patch("core.wiki.service.get_settings") as mock_settings,
            patch("core.wiki.service.CredentialService") as mock_cred_cls,
        ):
            mock_settings.return_value.credential_encryption_key = "key"
            mock_cred_service = AsyncMock()
            mock_cred_service.get_credential_with_metadata.return_value = (
                "my-pat",
                {"organization_url": ""},
            )
            mock_cred_cls.return_value = mock_cred_service
            result = await _get_ado_credentials(uuid4(), mock_session)
        assert result is None


class TestFullImport:
    """Tests for the full_import function."""

    @pytest.mark.asyncio
    async def test_raises_when_no_credentials(self) -> None:
        mock_session = AsyncMock()
        with patch("core.wiki.service._get_ado_credentials", return_value=None):
            with pytest.raises(WikiImportError, match="Azure DevOps credentials not configured"):
                await full_import(uuid4(), mock_session)

    @pytest.mark.asyncio
    async def test_raises_when_no_project(self) -> None:
        mock_session = AsyncMock()
        with patch(
            "core.wiki.service._get_ado_credentials",
            return_value=("pat", "https://dev.azure.com/MyOrg", None),
        ):
            with pytest.raises(WikiImportError, match="Project not specified"):
                await full_import(uuid4(), mock_session)

    @pytest.mark.asyncio
    async def test_sets_status_error_on_api_failure(self) -> None:
        context_id = uuid4()

        # Build a mock WikiImport record
        mock_record = MagicMock()
        mock_record.status = "idle"

        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = mock_record

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_execute_result

        with (
            patch(
                "core.wiki.service._get_ado_credentials",
                return_value=("pat", "https://dev.azure.com/MyOrg", "MyProject"),
            ),
            patch(
                "core.wiki.service.fetch_wiki_page_tree",
                side_effect=Exception("API unreachable"),
            ),
        ):
            with pytest.raises(WikiImportError, match="Import failed"):
                await full_import(context_id, mock_session)

        # The record status should have been set to "error"
        assert mock_record.status == "error"
        assert mock_record.last_error is not None

    @pytest.mark.asyncio
    async def test_returns_no_pages_summary(self) -> None:
        context_id = uuid4()

        mock_record = MagicMock()
        mock_record.status = "idle"

        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = mock_record

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_execute_result

        with (
            patch(
                "core.wiki.service._get_ado_credentials",
                return_value=("pat", "https://dev.azure.com/MyOrg", "MyProject"),
            ),
            patch("core.wiki.service.fetch_wiki_page_tree", return_value=[]),
        ):
            result = await full_import(context_id, mock_session)

        assert result == "No wiki pages found."
        assert mock_record.status == "completed"
