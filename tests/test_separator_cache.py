"""Tests for pipeline.dub.separator in-process cache (P6.A).

Mocks `demucs.api.Separator` so tests run without the demucs install.
Asserts:
- Separator instance reused across calls
- Subprocess fallback when demucs.api is missing
- DegradedError surfaces when both paths fail
- GPU OOM evicts the cached separator + marks GPU unavailable
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from pipeline import gpu_state
from pipeline.dub import separator as sep_mod
from pipeline.errors import DegradedError


@pytest.fixture(autouse=True)
def _reset_state(tmp_path):
    """Clear caches and sticky flag before each test."""
    sep_mod._separator_cache.clear()
    gpu_state.reset_for_tests()
    yield
    sep_mod._separator_cache.clear()
    gpu_state.reset_for_tests()


def _install_fake_demucs_api(monkeypatch, separator_class) -> None:
    """Inject a fake `demucs.api` module exposing `Separator`."""
    fake_module = MagicMock()
    fake_module.Separator = separator_class
    monkeypatch.setitem(sys.modules, "demucs.api", fake_module)


class TestSeparatorCacheReuse:
    def test_reuses_cached_instance(self, monkeypatch):
        """Two calls with the same model should return the same Separator."""
        instances = []

        def fake_separator(model, device):
            inst = MagicMock()
            inst.model = model
            inst.device = device
            instances.append(inst)
            return inst

        _install_fake_demucs_api(monkeypatch, fake_separator)

        sep1 = sep_mod._get_separator("htdemucs")
        sep2 = sep_mod._get_separator("htdemucs")
        assert sep1 is sep2
        assert len(instances) == 1

    def test_different_devices_cached_separately(self, monkeypatch):
        """Same model on GPU vs CPU should be 2 cache entries."""
        def fake_separator(model, device):
            return MagicMock(model=model, device=device)

        _install_fake_demucs_api(monkeypatch, fake_separator)

        # GPU first
        gpu_sep = sep_mod._get_separator("htdemucs")

        # Force CPU for next call
        gpu_state.mark_gpu_unavailable("test")
        cpu_sep = sep_mod._get_separator("htdemucs")

        assert gpu_sep is not cpu_sep
        assert len(sep_mod._separator_cache) == 2
        assert "htdemucs|cuda" in sep_mod._separator_cache
        assert "htdemucs|cpu" in sep_mod._separator_cache


class TestSubprocessFallback:
    def test_returns_none_when_demucs_api_missing(self, monkeypatch):
        """If `demucs.api` is not importable, _get_separator returns None."""
        # Block the import
        monkeypatch.setitem(sys.modules, "demucs.api", None)
        result = sep_mod._get_separator("htdemucs")
        assert result is None

    def test_separate_audio_uses_subprocess_fallback(self, monkeypatch, tmp_path):
        """When _get_separator returns None, separate_audio falls back to subprocess."""
        monkeypatch.setitem(sys.modules, "demucs.api", None)

        called = []

        def fake_subprocess(audio_path, demucs_dir, model):
            called.append((audio_path, demucs_dir, model))

        monkeypatch.setattr(sep_mod, "_separate_via_subprocess", fake_subprocess)

        # Create the demucs_dir + expected output structure
        demucs_dir = tmp_path / "_demucs_temp"
        demucs_dir.mkdir()

        result = sep_mod.separate_audio(
            str(tmp_path / "audio.wav"), str(demucs_dir), model="htdemucs"
        )
        assert len(called) == 1
        assert called[0][2] == "htdemucs"
        assert "vocals" in result
        assert "no_vocals" in result


class TestDegradedErrorWrapping:
    def test_both_paths_fail_raises_degraded(self, monkeypatch, tmp_path):
        """If subprocess fallback also raises, surface as DegradedError."""
        monkeypatch.setitem(sys.modules, "demucs.api", None)

        def fake_subprocess(audio_path, demucs_dir, model):
            raise RuntimeError("subprocess failed too")

        monkeypatch.setattr(sep_mod, "_separate_via_subprocess", fake_subprocess)

        with pytest.raises(DegradedError, match="Demucs source separation failed"):
            sep_mod.separate_audio(
                str(tmp_path / "audio.wav"), str(tmp_path), model="htdemucs"
            )


class TestEvictGpuSeparator:
    def test_evict_drops_only_gpu_entry(self, monkeypatch):
        """_evict_gpu_separator should remove cuda entry but keep cpu entry."""
        def fake_separator(model, device):
            return MagicMock(model=model, device=device)

        _install_fake_demucs_api(monkeypatch, fake_separator)

        # Cache GPU
        sep_mod._get_separator("htdemucs")
        # Force CPU then cache it too
        gpu_state.mark_gpu_unavailable("test")
        sep_mod._get_separator("htdemucs")

        assert len(sep_mod._separator_cache) == 2

        sep_mod._evict_gpu_separator("htdemucs")
        assert "htdemucs|cuda" not in sep_mod._separator_cache
        assert "htdemucs|cpu" in sep_mod._separator_cache


class TestSticky:
    def test_get_separator_uses_cpu_after_mark_unavailable(self, monkeypatch):
        """After mark_gpu_unavailable, _get_separator should request device='cpu'."""
        captured_devices = []

        def fake_separator(model, device):
            captured_devices.append(device)
            return MagicMock(model=model, device=device)

        _install_fake_demucs_api(monkeypatch, fake_separator)

        sep_mod._get_separator("htdemucs")  # cuda
        gpu_state.mark_gpu_unavailable("test")
        sep_mod._get_separator("htdemucs")  # cpu

        assert captured_devices == ["cuda", "cpu"]
