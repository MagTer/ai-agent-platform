"""Unit tests for the wiki import service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.wiki.service import (
    WikiImportError,
    _get_ado_credentials,
    clone_wiki_pages,
    full_import,
)


class TestCloneWikiPages:
    """Tests for clone_wiki_pages function."""

    @pytest.mark.asyncio
    async def test_clone_with_empty_repo(self) -> None:
        """Test that clone_wiki_pages handles empty repos."""
        with (
            patch("core.wiki.service._get_wiki_clone_url") as mock_get_url,
            patch("core.wiki.service.asyncio.create_subprocess_exec") as mock_exec,
            patch("core.wiki.service.Path") as mock_path,
        ):
            # Mock URL discovery
            mock_get_url.return_value = (
                "MyWiki",
                "https://x-token:pat@dev.azure.com/MyOrg/MyProject/_git/MyWiki",
            )

            # Mock successful git clone
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc

            # Mock empty file discovery
            mock_path.return_value.rglob.return_value = []

            # Call the function
            wiki_name, pages = await clone_wiki_pages(
                "pat123", "https://dev.azure.com/MyOrg", "MyProject"
            )

            # Verify results
            assert wiki_name == "MyWiki"
            assert len(pages) == 0

    @pytest.mark.asyncio
    async def test_clone_raises_on_git_failure(self) -> None:
        """Test that clone_wiki_pages raises on git clone failure."""
        with (
            patch("core.wiki.service._get_wiki_clone_url") as mock_get_url,
            patch("core.wiki.service.asyncio.create_subprocess_exec") as mock_exec,
        ):
            # Mock URL discovery
            mock_get_url.return_value = (
                "MyWiki",
                "https://x-token:pat@dev.azure.com/MyOrg/MyProject/_git/MyWiki",
            )

            # Mock failed git clone
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Authentication failed"))
            mock_exec.return_value = mock_proc

            # Call should raise
            with pytest.raises(WikiImportError, match="Git clone failed"):
                await clone_wiki_pages("pat123", "https://dev.azure.com/MyOrg", "MyProject")


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
    async def test_sets_status_error_on_clone_failure(self) -> None:
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
                "core.wiki.service.clone_wiki_pages",
                side_effect=Exception("Git clone failed"),
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
            patch("core.wiki.service.clone_wiki_pages", return_value=("MyWiki", [])),
        ):
            result = await full_import(context_id, mock_session)

        assert result == "No wiki pages found."
        assert mock_record.status == "completed"
