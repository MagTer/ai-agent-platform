"""Unit tests for post-deploy smoke check logic."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from stack.checks import SmokeResult, print_smoke_results, run_smoke_checks


class TestSmokeResult:
    """Tests for the SmokeResult dataclass."""

    def test_smoke_result_passed(self) -> None:
        result = SmokeResult(name="test", passed=True, message="OK", duration_ms=42.0)
        assert result.passed is True
        assert result.name == "test"
        assert result.message == "OK"
        assert result.duration_ms == 42.0

    def test_smoke_result_failed(self) -> None:
        result = SmokeResult(
            name="test", passed=False, message="connection refused", duration_ms=5.0
        )
        assert result.passed is False
        assert result.message == "connection refused"


class TestRunSmokeChecks:
    """Tests for run_smoke_checks()."""

    def _make_proc(self, returncode: int = 0, stdout: bytes = b"") -> MagicMock:
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = b""
        return proc

    @patch("stack.tooling.docker_exec")
    def test_all_checks_pass_on_success(self, mock_docker_exec: MagicMock) -> None:
        """When all docker exec calls succeed, all results should be passed=True."""
        mock_docker_exec.return_value = self._make_proc(0)

        results = run_smoke_checks("ai-agent-platform-dev")

        assert len(results) == 2  # 2 standard checks (no --full)
        assert all(r.passed for r in results)

    @patch("stack.tooling.docker_exec")
    def test_check_names_are_correct(self, mock_docker_exec: MagicMock) -> None:
        """Smoke checks should have the expected names."""
        mock_docker_exec.return_value = self._make_proc(0)

        results = run_smoke_checks("ai-agent-platform-dev")

        names = [r.name for r in results]
        assert "/platformadmin/api/health" in names
        assert "infrastructure status" in names

    @patch("stack.tooling.docker_exec")
    def test_full_mode_adds_fourth_check(self, mock_docker_exec: MagicMock) -> None:
        """With full=True, a fourth diagnostics check is appended."""
        mock_docker_exec.return_value = self._make_proc(0)

        results = run_smoke_checks("ai-agent-platform-prod", full=True)

        assert len(results) == 3
        assert results[-1].name == "full diagnostics"

    @patch("stack.tooling.docker_exec")
    def test_first_check_fails_on_nonzero_exit(self, mock_docker_exec: MagicMock) -> None:
        """A non-zero docker exec exit code marks the check as failed."""
        err = subprocess.CalledProcessError(1, "docker", stderr=b"curl: (7) Connection refused")
        mock_docker_exec.side_effect = [
            err,            # health fails (now check 1)
            self._make_proc(0),  # status passes (now check 2)
        ]

        results = run_smoke_checks("ai-agent-platform-dev")

        assert results[0].passed is False
        assert "Connection refused" in results[0].message
        assert results[1].passed is True

    @patch("stack.tooling.docker_exec")
    def test_exception_is_caught_and_marked_failed(self, mock_docker_exec: MagicMock) -> None:
        """Non-CalledProcessError exceptions are caught and result in passed=False."""
        mock_docker_exec.side_effect = RuntimeError("container not found")

        results = run_smoke_checks("ai-agent-platform-dev")

        assert all(not r.passed for r in results)
        assert "container not found" in results[0].message

    @patch("stack.tooling.docker_exec")
    def test_uses_correct_container_name(self, mock_docker_exec: MagicMock) -> None:
        """The container name is derived from the project_name argument."""
        mock_docker_exec.return_value = self._make_proc(0)

        run_smoke_checks("my-custom-project")

        calls = mock_docker_exec.call_args_list
        for call in calls:
            assert call.args[0] == "my-custom-project-agent-1"

    @patch("stack.tooling.docker_exec")
    def test_duration_is_recorded(self, mock_docker_exec: MagicMock) -> None:
        """Each result should have a non-negative duration_ms."""
        mock_docker_exec.return_value = self._make_proc(0)

        results = run_smoke_checks("ai-agent-platform-dev")

        for r in results:
            assert r.duration_ms >= 0.0


class TestPrintSmokeResults:
    """Tests for print_smoke_results()."""

    def test_all_passed_prints_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When all checks pass, the output contains a success message."""
        results = [
            SmokeResult("/platformadmin/api/health", True, "OK", 8.0),
            SmokeResult("infrastructure status", True, "HEALTHY", 120.0),
        ]
        print_smoke_results(results)

        out = capsys.readouterr().out
        assert "All smoke tests passed" in out

    def test_failed_check_prints_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When a check fails, the output contains the warning."""
        results = [
            SmokeResult("/v1/models", True, "OK", 12.0),
            SmokeResult("infrastructure status", False, "CRITICAL (LiteLLM unreachable)", 5.0),
        ]
        print_smoke_results(results)

        out = capsys.readouterr().out
        assert "Warning" in out
        assert "1 smoke check(s) failed" in out

    def test_all_failed_shows_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Warning message reflects the correct number of failures."""
        results = [
            SmokeResult("check-a", False, "error", 1.0),
            SmokeResult("check-b", False, "error", 1.0),
        ]
        print_smoke_results(results)

        out = capsys.readouterr().out
        assert "2 smoke check(s) failed" in out

    def test_each_result_is_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Every check name appears in the output."""
        results = [
            SmokeResult("/platformadmin/api/health", False, "timeout", 5.0),
        ]
        print_smoke_results(results)

        out = capsys.readouterr().out
        assert "/platformadmin/api/health" in out
