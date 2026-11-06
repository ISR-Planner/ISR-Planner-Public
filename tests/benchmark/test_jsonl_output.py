"""Tests for JSONL crash-safety.

Covers FR-014 crash-safety clause and US2 acceptance #3.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_agents.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("benchmark_agents", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = _load_module()


def test_flush_fsync_per_line(tmp_path, monkeypatch):
    fsync_calls = []
    real_fsync = os.fsync

    def counting_fsync(fd):
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(ba.os, "fsync", counting_fsync)
    write, close = ba.open_jsonl_writer(tmp_path)
    for i in range(3):
        write({"run_index": i, "data": "x"})
    close()
    # At least 3 fsyncs from writes + 1 from close
    assert len(fsync_calls) >= 3


def test_records_parseable_after_writes(tmp_path):
    write, close = ba.open_jsonl_writer(tmp_path)
    for i in range(5):
        write({"run_index": i, "value": f"record-{i}"})
    close()

    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 5
    for i, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed["run_index"] == i


def test_truncated_trailing_line_does_not_corrupt_prior_lines(tmp_path):
    """Simulate a crash mid-line: truncate the file partway through the 3rd line.
    The first two complete lines must still parse.
    """
    write, close = ba.open_jsonl_writer(tmp_path)
    write({"run_index": 0, "v": "first"})
    write({"run_index": 1, "v": "second"})
    close()

    path = tmp_path / "runs.jsonl"

    # Append a partial third line (no trailing newline) to simulate mid-line crash
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"run_index": 2, "v": "thir')
        f.flush()
        os.fsync(f.fileno())

    content = path.read_text()
    lines = content.splitlines()
    # First two are complete; third is partial
    assert len(lines) == 3
    assert json.loads(lines[0])["run_index"] == 0
    assert json.loads(lines[1])["run_index"] == 1
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[2])


def test_writer_appends_not_overwrites(tmp_path):
    """A second open of the same batch dir must append, not overwrite."""
    write1, close1 = ba.open_jsonl_writer(tmp_path)
    write1({"run_index": 0})
    close1()

    write2, close2 = ba.open_jsonl_writer(tmp_path)
    write2({"run_index": 1})
    close2()

    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_index"] == 0
    assert json.loads(lines[1])["run_index"] == 1
