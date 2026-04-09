"""Tests for web.worker.auto_stop_check() (P6.C verification).

The auto-stop logic already existed at web/worker.py:153-165 since
Phase 3 but was never tested. P6.C adds this test to lock in the
contract: idle + both env vars → call `vastai stop instance`.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

import web.worker as worker


@pytest.fixture
def reset_activity():
    """Save + restore `last_activity` between tests."""
    saved = worker.last_activity
    yield
    worker.last_activity = saved


class TestAutoStop:
    def test_skipped_without_env_vars(self, reset_activity):
        """No CONTAINER_ID → no subprocess call even if idle."""
        worker.last_activity = time.time() - 1000 * 60  # 1000 min ago
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run") as mock_run:
                worker.auto_stop_check()
                mock_run.assert_not_called()

    def test_skipped_without_api_key(self, reset_activity):
        """CONTAINER_ID set but CONTAINER_API_KEY missing → skip."""
        worker.last_activity = time.time() - 1000 * 60
        env = {"CONTAINER_ID": "test-123"}
        with patch.dict(os.environ, env, clear=True):
            with patch("subprocess.run") as mock_run:
                worker.auto_stop_check()
                mock_run.assert_not_called()

    def test_skipped_when_recent_activity(self, reset_activity):
        """Recent activity (within idle_timeout) → no subprocess call."""
        worker.last_activity = time.time()  # just now
        env = {"CONTAINER_ID": "test-123", "CONTAINER_API_KEY": "fake-key"}
        with patch.dict(os.environ, env, clear=True):
            with patch("subprocess.run") as mock_run:
                worker.auto_stop_check()
                mock_run.assert_not_called()

    def test_triggers_when_idle_long_enough(self, reset_activity):
        """Idle > idle_timeout + both env vars → call vastai stop instance."""
        from pipeline.config import cfg
        # Set last_activity way past the idle timeout
        worker.last_activity = time.time() - (cfg.web.idle_timeout + 100)
        env = {"CONTAINER_ID": "test-123", "CONTAINER_API_KEY": "fake-key"}
        with patch.dict(os.environ, env, clear=True):
            with patch("subprocess.run") as mock_run:
                worker.auto_stop_check()
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                assert args[0] == "vastai"
                assert "stop" in args
                assert "instance" in args
                assert "test-123" in args
                assert "fake-key" in args

    def test_subprocess_error_does_not_crash(self, reset_activity):
        """If vastai command itself crashes, auto_stop_check must swallow exception.

        Critical: this function runs in a background maintenance loop. A
        crash here would kill the loop and break job processing.
        """
        from pipeline.config import cfg
        worker.last_activity = time.time() - (cfg.web.idle_timeout + 100)
        env = {"CONTAINER_ID": "test-123", "CONTAINER_API_KEY": "fake-key"}
        with patch.dict(os.environ, env, clear=True):
            with patch("subprocess.run", side_effect=RuntimeError("network down")):
                # Must NOT raise
                worker.auto_stop_check()
