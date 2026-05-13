"""Tests for checkpoint — load/save/wipe operations."""

import json
import tempfile
from pathlib import Path

import pytest

from job_hunter.utils import atomic_write_json


class TestAtomicWriteJson:
    def test_roundtrip(self, tmp_path: Path):
        data = {"stage": "collect", "jobs": [{"title": "DevOps Engineer"}]}
        path = tmp_path / "checkpoint.json"

        atomic_write_json(path, data)

        result = json.loads(path.read_text(encoding="utf-8"))
        assert result == data
        assert result["jobs"][0]["title"] == "DevOps Engineer"

    def test_overwrites_previous(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        path.write_text('{"old": true}', encoding="utf-8")

        atomic_write_json(path, {"new": True})

        result = json.loads(path.read_text(encoding="utf-8"))
        assert result == {"new": True}
        assert "old" not in result

    def test_no_tmp_file_after_write(self, tmp_path: Path):
        """Verify the .tmp file never exists after write completes."""
        path = tmp_path / "checkpoint.json"
        atomic_write_json(path, {"key": "value"})

        assert not path.with_suffix(".tmp").exists()

    def test_handles_empty_dict(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        atomic_write_json(path, {})
        assert json.loads(path.read_text(encoding="utf-8")) == {}

    def test_handles_unicode(self, tmp_path: Path):
        path = tmp_path / "checkpoint.json"
        atomic_write_json(path, {"company": "São Paulo", "niño": "español"})
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["company"] == "São Paulo"
        assert result["niño"] == "español"