"""`run.py dub --revoice-source` on an organized source must queue a real dub.

After `organize` an EN source lives at <src>/English/EN_DDMMNN.mp4 — exactly the
name an EN dub gets. The EN->EN re-dub used to find the original there, record
it as `dubbed_outputs.en` and skip it (a silent no-op; without that guard the
worker would have overwritten the source). The re-dub now goes to
<src>/_redub/English/, which `scan` ignores.

run.py is a skill script, not a package, so it is loaded by path. The process
pool is replaced by an inline stub that records each job and fakes success —
no TTS, Demucs or ffmpeg runs.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from concurrent.futures import Future
from pathlib import Path

import pytest

_RUN_PY = (Path(__file__).resolve().parents[1]
           / "_claude_skills" / "meta-ads-prepare-ultimate" / "run.py")


@pytest.fixture(scope="module")
def run():
    """Import run.py, undoing what it does to the process at import time:
    re-wrapped stdout/stderr, lowered priority, thread-count env vars and its
    folder prepended to sys.path (the repo root has its own run.py)."""
    streams = sys.stdout, sys.stderr
    env_keys = set(os.environ)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "path", list(sys.path))
        if sys.platform == "win32":
            import ctypes
            mp.setattr(ctypes.windll.kernel32, "SetPriorityClass", lambda *a: 1)
        else:
            mp.setattr(os, "nice", lambda inc: 0)
        spec = importlib.util.spec_from_file_location("ultimate_run", _RUN_PY)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        finally:
            for wrapper, orig in zip((sys.stdout, sys.stderr), streams):
                if wrapper is not orig:
                    wrapper.detach()  # else its GC closes the buffer orig shares
            sys.stdout, sys.stderr = streams
            for k in set(os.environ) - env_keys:
                del os.environ[k]
    return mod


@pytest.fixture
def queued(run, monkeypatch):
    """Jobs submitted to the (stubbed) process pool, in order."""
    jobs = []

    def fake_dub(job):
        Path(job["out_path"]).write_bytes(b"\0" * 1024)
        return ("ok", job["id"], "")

    fake = {run._dub_worker: fake_dub,
            run._brand_worker: lambda job: ("ok", job["id"], 0.0, "")}

    class _InlinePool:
        def __init__(self, max_workers=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def submit(self, fn, job):
            jobs.append(job)
            fut = Future()
            fut.set_result(fake[fn](job))
            return fut

    monkeypatch.setattr(run, "ProcessPoolExecutor", _InlinePool)
    return jobs


@pytest.fixture
def organized(run, tmp_path):
    """A voiced EN clip as `organize` leaves it, with an EN->EN brand swap filled."""
    path = tmp_path / "English" / "EN_081001.mp4"
    path.parent.mkdir()
    path.write_bytes(b"\0" * 200_000)  # past dub's 100 KB "already dubbed" gate
    run.M.save_manifest(tmp_path, {"src_root": str(tmp_path), "videos": [{
        "id": "ad1", "orig_name": "ad1_20261008T101010.mp4",
        "src_path": str(path), "organized_path": str(path),
        "renamed": "EN_081001.mp4", "language_folder": "English", "lang_code": "EN",
        "has_voice": True, "vertical": "scanner", "angle": "scan",
        "transcript": "Get iScanner now",
        "segments": [{"id": 0, "start": 0.0, "end": 2.0, "text": "Get iScanner now",
                      "translations": {"en": "Get Scanner App now"}}],
    }]})
    return path


def _cli(run, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["run.py", *argv])
    run.main()


def _video(run, src):
    return run.M.load_manifest(src)["videos"][0]


def test_revoice_source_dubs_beside_the_organized_original(
        run, queued, organized, tmp_path, monkeypatch):
    _cli(run, monkeypatch, "dub", "--src", str(tmp_path),
         "--target-langs", "en", "--revoice-source")

    assert len(queued) == 1
    job = queued[0]
    assert job["tlang"] == "en"
    assert job["src_path"] == str(organized)
    assert Path(job["out_path"]) != organized
    assert Path(job["out_path"]) == tmp_path / "_redub" / "English" / "EN_081001.mp4"
    assert organized.stat().st_size == 200_000  # the source is untouched
    assert _video(run, tmp_path)["dubbed_outputs"]["en"] == job["out_path"]
    # A later `scan` still sees only the source, not the re-dub.
    assert run.M.collect_videos(tmp_path) == [organized]


def test_brandpass_ships_the_redub_under_the_organized_name(
        run, queued, organized, tmp_path, monkeypatch):
    _cli(run, monkeypatch, "dub", "--src", str(tmp_path),
         "--target-langs", "en", "--revoice-source")
    redub = queued[0]["out_path"]
    dst = tmp_path / "out"
    _cli(run, monkeypatch, "brandpass", "--src", str(tmp_path), "--dst", str(dst),
         "--target-langs", "en", "--no-sign")

    brand = queued[1]
    assert brand["input_path"] == redub
    assert Path(brand["out_path"]) == dst / "VOICED_English" / "scan" / "EN_081001.mp4"


def test_without_revoice_the_original_is_the_en_output(
        run, queued, organized, tmp_path, monkeypatch):
    _cli(run, monkeypatch, "dub", "--src", str(tmp_path), "--target-langs", "en")

    assert queued == []
    assert _video(run, tmp_path)["dubbed_outputs"]["en"] == str(organized)
    assert not (tmp_path / "_redub").exists()
