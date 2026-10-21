"""test_status.py — 状态机、Gaussian 错误识别、轮询。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gtransfer_lib.status import (  # noqa: E402
    JobState,
    JobStatus,
    classify_exit,
    parse_gaussian_error,
    poll_until_terminal,
)


def test_classify_success():
    assert classify_exit(0) == (JobStatus.SUCCESS, None)


def test_classify_exit_code_failure():
    status, hint = classify_exit(2)
    assert status == JobStatus.FAILED
    assert "退出码 2" in (hint or "")


def test_classify_log_error_overrides_exit0():
    """exit 0 但日志有错误 → FAILED。"""
    status, hint = classify_exit(0, "Normal termination\nError termination\n")
    assert status == JobStatus.FAILED
    assert hint == "Gaussian 错误终止"


def test_parse_l_code():
    hint = parse_gaussian_error("L9999 in opt\n")
    assert hint and "L9999" in hint


def test_parse_generic_error_line():
    hint = parse_gaussian_error("Error: convergence failure\n")
    assert hint and "convergence" in hint


def test_parse_no_error():
    assert parse_gaussian_error("Normal termination of Gaussian 16\n") is None


def test_poll_until_terminal():
    calls = {"n": 0}

    def state_fn():
        calls["n"] += 1
        if calls["n"] < 4:
            return JobState(job_id="j", status=JobStatus.RUNNING)
        return JobState(job_id="j", status=JobStatus.SUCCESS)

    state = poll_until_terminal(state_fn, poll_interval=0.001)
    assert state.status == JobStatus.SUCCESS
    assert calls["n"] == 4


def test_poll_timeout():
    def state_fn():
        return JobState(job_id="j", status=JobStatus.RUNNING)

    with pytest.raises(TimeoutError):
        poll_until_terminal(state_fn, poll_interval=0.001, timeout=0.02)


def test_poll_on_poll_callback():
    """on_poll 仅在遇到非终态（RUNNING/PENDING）时触发。"""
    seen = []
    calls = {"n": 0}

    def state_fn():
        calls["n"] += 1
        if calls["n"] == 1:
            return JobState(job_id="j", status=JobStatus.RUNNING)
        return JobState(job_id="j", status=JobStatus.SUCCESS)

    poll_until_terminal(state_fn, poll_interval=0.001, on_poll=lambda s: seen.append(s))
    assert len(seen) == 1
    assert seen[0].status == JobStatus.RUNNING
