"""Tests for pipeline.gpu_state — sticky GPU/CPU flag + cache helper.

P6.A contract: once any model loader trips on CUDA, all subsequent loaders
in the same process should skip GPU. The flag never recovers without a
process restart (or test reset).
"""

from __future__ import annotations

import threading

import pytest

from pipeline import gpu_state
from pipeline.config import cfg


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset sticky state before AND after each test."""
    gpu_state.reset_for_tests()
    yield
    gpu_state.reset_for_tests()


class TestShouldUseGpu:
    def test_initial_state_uses_gpu(self):
        """Default state allows GPU attempts."""
        assert gpu_state.should_use_gpu() is True

    def test_after_mark_unavailable_returns_false(self):
        gpu_state.mark_gpu_unavailable("test reason")
        assert gpu_state.should_use_gpu() is False

    def test_force_cpu_config_overrides(self, monkeypatch):
        """cfg.gpu.force_cpu = True hard-overrides should_use_gpu.

        Config is frozen, so monkeypatch the module-level `cfg` import
        inside gpu_state to a new Config with force_cpu=True.
        """
        from dataclasses import replace
        from pipeline.config import cfg as original_cfg
        new_cfg = replace(original_cfg, gpu=replace(original_cfg.gpu, force_cpu=True))
        monkeypatch.setattr(gpu_state, "cfg", new_cfg)
        assert gpu_state.should_use_gpu() is False


class TestMarkGpuUnavailable:
    def test_idempotent(self):
        """Second call should not overwrite the first reason."""
        gpu_state.mark_gpu_unavailable("first")
        gpu_state.mark_gpu_unavailable("second")
        assert gpu_state.get_unavailable_reason() == "first"

    def test_reason_stored(self):
        gpu_state.mark_gpu_unavailable("Whisper OOM")
        assert gpu_state.get_unavailable_reason() == "Whisper OOM"

    def test_reset_clears_reason(self):
        gpu_state.mark_gpu_unavailable("test")
        gpu_state.reset_for_tests()
        assert gpu_state.get_unavailable_reason() is None
        assert gpu_state.should_use_gpu() is True


class TestEmptyCudaCache:
    def test_no_op_without_torch(self, monkeypatch):
        """Should not raise even if torch is not importable."""
        import sys
        # Temporarily hide torch
        orig_torch = sys.modules.get("torch")
        sys.modules["torch"] = None  # type: ignore
        try:
            gpu_state.empty_cuda_cache()  # must not raise
        finally:
            if orig_torch is not None:
                sys.modules["torch"] = orig_torch
            else:
                sys.modules.pop("torch", None)

    def test_no_op_when_cuda_unavailable(self):
        """Should not raise on CPU-only machines (most likely scenario)."""
        # Just call it — if torch is installed but no CUDA, it should swallow
        gpu_state.empty_cuda_cache()


class TestThreadSafety:
    def test_concurrent_should_use_gpu_calls(self):
        """100 threads polling should_use_gpu while 1 thread marks unavailable."""
        results = []
        barrier = threading.Barrier(11)

        def reader():
            barrier.wait()
            for _ in range(50):
                results.append(gpu_state.should_use_gpu())

        def writer():
            barrier.wait()
            gpu_state.mark_gpu_unavailable("racing")

        threads = [threading.Thread(target=reader) for _ in range(10)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No exceptions = pass. Final state should be unavailable.
        assert gpu_state.should_use_gpu() is False
        # Some early reads may have been True, later reads False — both OK
        assert len(results) == 500
