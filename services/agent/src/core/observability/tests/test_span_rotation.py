"""Tests for span log rotation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.observability.tracing import _FileSpanExporter


def test_rotation_when_file_exceeds_size() -> None:
    """Test that span log rotates when file exceeds max size."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spans.jsonl"

        # Create exporter with tiny max size (1KB) and max 3 files
        exporter = _FileSpanExporter(str(log_path), max_size_mb=1, max_files=3)
        exporter._max_size_bytes = 1000  # Override to 1KB for testing

        # Write large batch to trigger rotation
        large_records = [{"data": "x" * 1000} for _ in range(10)]

        # First write - should create spans.jsonl
        exporter._write_batch_sync(large_records[:5])
        assert log_path.exists()

        # Second write - should trigger rotation to spans.jsonl.1
        exporter._write_batch_sync(large_records[5:])

        # Check that rotation happened
        rotated_file = Path(f"{log_path}.1")
        assert rotated_file.exists(), "Expected rotated file .1 to exist"
        assert log_path.exists(), "Expected new current file to exist"


def test_rotation_keeps_max_files() -> None:
    """Test that rotation deletes oldest files when exceeding max_files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spans.jsonl"

        # Create exporter with tiny max size and max 2 files
        exporter = _FileSpanExporter(str(log_path), max_size_mb=1, max_files=2)
        exporter._max_size_bytes = 1000  # Override to 1KB for testing

        large_records = [{"data": "x" * 1000} for _ in range(5)]

        # Write multiple times to trigger multiple rotations
        for _ in range(5):
            exporter._write_batch_sync(large_records)

        # Should have: spans.jsonl, spans.jsonl.1, spans.jsonl.2
        # Older files (.3, .4, etc.) should be deleted
        assert log_path.exists()
        assert Path(f"{log_path}.1").exists()
        assert Path(f"{log_path}.2").exists()
        assert not Path(f"{log_path}.3").exists(), "File .3 should be deleted (max 2 rotations)"


def test_no_rotation_when_file_is_small() -> None:
    """Test that rotation doesn't happen when file is below threshold."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spans.jsonl"

        # Create exporter with large max size (10MB)
        exporter = _FileSpanExporter(str(log_path), max_size_mb=10, max_files=3)

        # Write small batch
        small_records = [{"data": "test"} for _ in range(10)]
        exporter._write_batch_sync(small_records)

        # Verify no rotation happened
        assert log_path.exists()
        assert not Path(f"{log_path}.1").exists(), "Should not rotate for small files"


def test_rotated_files_contain_valid_json() -> None:
    """Test that rotated files contain valid JSONL data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spans.jsonl"

        exporter = _FileSpanExporter(str(log_path), max_size_mb=1, max_files=3)
        exporter._max_size_bytes = 1000  # Override to 1KB for testing

        # Write records with identifiable data
        batch1 = [{"batch": 1, "index": i} for i in range(5)]
        batch2 = [{"batch": 2, "index": i} for i in range(5)]

        exporter._write_batch_sync(batch1)
        # Trigger rotation
        exporter._write_batch_sync([{"data": "x" * 2000}])
        exporter._write_batch_sync(batch2)

        # Verify rotated file contains valid JSON
        rotated_file = Path(f"{log_path}.1")
        if rotated_file.exists():
            with rotated_file.open("r", encoding="utf-8") as f:
                lines = f.readlines()
                # Should be able to parse each line as JSON
                for line in lines:
                    data = json.loads(line.strip())
                    assert isinstance(data, dict), "Each line should be valid JSON"


def test_rotation_is_thread_safe() -> None:
    """Test that rotation uses locking for thread safety."""
    import threading

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spans.jsonl"
        exporter = _FileSpanExporter(str(log_path), max_size_mb=1, max_files=3)

        # Verify rotation lock exists
        assert hasattr(exporter, "_rotation_lock")
        assert isinstance(exporter._rotation_lock, threading.Lock)


def test_rotation_handles_missing_file() -> None:
    """Test that rotation handles case where file doesn't exist yet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "spans.jsonl"
        exporter = _FileSpanExporter(str(log_path), max_size_mb=1, max_files=3)

        # Call rotate on non-existent file (should not crash)
        exporter._rotate_if_needed()

        # Verify it didn't create any files
        assert not log_path.exists()
        assert not Path(f"{log_path}.1").exists()
