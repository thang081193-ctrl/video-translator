"""Runtime preflight checks for GPU visibility and translation API keys."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

# Allow running as a bare script path (`python3 pipeline/preflight.py`): without
# this, sys.path[0] is the pipeline/ dir, not the repo root, so `from pipeline...`
# raises ModuleNotFoundError. The box tail scripts invoke it exactly that way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from pipeline.config import cfg
from pipeline.errors import FatalError
from pipeline.logger import get_logger

log = get_logger("Preflight")


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env var with a default fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return _is_truthy(raw)


def _split_keys(value: str | None) -> list[str]:
    if not value:
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


def _load_provider_keys(multi_name: str, single_name: str) -> list[str]:
    keys = _split_keys(os.getenv(multi_name, ""))
    single = os.getenv(single_name, "").strip()
    if single:
        keys.append(single)
    # Preserve order while de-duplicating
    return list(dict.fromkeys(keys))


@dataclass
class RuntimeStatus:
    cuda_available: bool
    gpu_count: int
    gpu_name: str | None
    gpu_probe_source: str
    grok_keys: list[str]
    gemini_keys: list[str]
    vertex_keys: list[str]
    invalid_grok_keys: list[str]
    disk_free_mb_cache: int = 0   # P6.C: disk free at ~/.cache/huggingface
    disk_free_mb_uploads: int = 0 # P6.C: disk free at ./uploads

    @property
    def total_translation_keys(self) -> int:
        return len(self.grok_keys) + len(self.gemini_keys) + len(self.vertex_keys)

    @property
    def has_grok(self) -> bool:
        return len(self.grok_keys) > 0


def _disk_free_mb(path: str) -> int:
    """Return free disk space at `path` in MB, or 0 if path doesn't exist / os error."""
    try:
        usage = shutil.disk_usage(path)
        return int(usage.free / (1024 * 1024))
    except (OSError, FileNotFoundError):
        return 0


def _detect_gpu() -> tuple[bool, int, str | None, str]:
    # Probe via torch first (best signal for our app runtime).
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            name = torch.cuda.get_device_name(0) if count > 0 else None
            return True, count, name, "torch"
    except Exception:
        pass

    # Fallback probe via nvidia-smi (useful when torch not fully ready yet).
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=cfg.ffmpeg.nvidia_smi_timeout,
            check=False,
        )
        if result.returncode == 0:
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if names:
                return True, len(names), names[0], "nvidia-smi"
    except Exception:
        pass

    return False, 0, None, "none"


def collect_runtime_status() -> RuntimeStatus:
    grok_keys_all = _load_provider_keys("GROK_API_KEYS", "GROK_API_KEY")
    invalid_grok = [k for k in grok_keys_all if not k.startswith("xai-")]
    grok_keys = [k for k in grok_keys_all if k.startswith("xai-")]

    gemini_keys = _load_provider_keys("GEMINI_API_KEYS", "GEMINI_API_KEY")
    vertex_keys = _load_provider_keys("VERTEX_API_KEYS", "VERTEX_API_KEY")

    cuda_available, gpu_count, gpu_name, gpu_probe_source = _detect_gpu()

    # P6.C: probe disk space at model cache + uploads dirs
    cache_dir = os.path.expanduser("~/.cache/huggingface")
    uploads_dir = os.path.join(os.getcwd(), "uploads")
    disk_free_cache = _disk_free_mb(cache_dir) or _disk_free_mb(os.path.expanduser("~"))
    disk_free_uploads = _disk_free_mb(uploads_dir) or _disk_free_mb(os.getcwd())

    return RuntimeStatus(
        cuda_available=cuda_available,
        gpu_count=gpu_count,
        gpu_name=gpu_name,
        gpu_probe_source=gpu_probe_source,
        grok_keys=grok_keys,
        gemini_keys=gemini_keys,
        vertex_keys=vertex_keys,
        invalid_grok_keys=invalid_grok,
        disk_free_mb_cache=disk_free_cache,
        disk_free_mb_uploads=disk_free_uploads,
    )


def _print_summary(status: RuntimeStatus):
    log.info("=== Runtime preflight ===")
    if status.cuda_available:
        gpu_label = status.gpu_name or "Unknown GPU"
        log.info(
            f"[OK] GPU detected: {gpu_label} "
            f"(count={status.gpu_count}, via {status.gpu_probe_source})"
        )
    else:
        log.warning("[WARN] GPU/CUDA not detected. Whisper will run on CPU.")

    log.info(
        "[INFO] Translation keys: "
        f"total={status.total_translation_keys} "
        f"(Grok={len(status.grok_keys)}, Gemini={len(status.gemini_keys)}, "
        f"Vertex={len(status.vertex_keys)})"
    )

    if status.invalid_grok_keys:
        log.warning(
            f"[WARN] Some GROK_API_KEY(S) do not start with 'xai-' and will be ignored: "
            f"{len(status.invalid_grok_keys)} key(s)"
        )

    # Tier classification — explicit free vs paid posture.
    # Reuses the same logic as factory.classify_tier() so the message matches
    # the one emitted at first translate call.
    from pipeline.providers.factory import classify_tier, _log_tier_banner
    fake_keys = (
        [{"provider": "gemini", "key": k} for k in status.gemini_keys]
        + [{"provider": "grok", "key": k} for k in status.grok_keys]
        + [{"provider": "vertex", "key": k} for k in status.vertex_keys]
    )
    if fake_keys:
        _log_tier_banner(classify_tier(fake_keys))


def run_preflight(
    require_translation_keys: bool = True,
    require_grok: bool = False,
    require_cuda: bool = False,
    require_disk_space: bool = False,
    min_disk_mb: int = 5000,
) -> RuntimeStatus:
    status = collect_runtime_status()
    _print_summary(status)

    if require_cuda and not status.cuda_available:
        raise FatalError(
            "REQUIRE_CUDA is enabled but no GPU/CUDA device is visible. "
            "Check Vast instance type and runtime (--gpus all)."
        )

    if require_translation_keys and status.total_translation_keys == 0:
        raise FatalError(
            "No translation API key found. Add at least one of: "
            "GROK_API_KEYS, GEMINI_API_KEYS, VERTEX_API_KEYS."
        )

    if require_grok and not status.has_grok:
        raise FatalError(
            "REQUIRE_GROK is enabled but no valid Grok key was found. "
            "Set GROK_API_KEYS (keys must start with 'xai-')."
        )

    # P6.C: optional disk space guard (opt-in via REQUIRE_DISK_SPACE=1)
    if require_disk_space and status.disk_free_mb_cache < min_disk_mb:
        raise FatalError(
            f"Insufficient disk space for model cache: "
            f"{status.disk_free_mb_cache}MB free, need {min_disk_mb}MB. "
            f"Clean up /workspace or mount a larger volume."
        )

    log.info("=== Preflight OK ===")
    return status


def run_strict() -> None:
    """STRICT box-side preflight (per AUDIT_final.md Part 2c).

    Fail fast (in ~seconds) with the EXACT missing item BEFORE any render starts,
    instead of stalling 10 min into a batch on a lazy model download or a missing
    binary. Asserts the full Vast.ai render environment:

      * ffmpeg / ffprobe on PATH
      * importable: torch, torchaudio, faster_whisper, easyocr, cv2, soundfile,
        edge_tts, PIL  +  demucs.api.Separator
      * whisper tiny/small/medium caches + easyocr packs + demucs htdemucs ON DISK
      * fc-list has CJK / Thai fonts
      * ffmpeg has the libx264 encoder (NVENC is blocked on consumer Vast)
      * torch.cuda available (catches the cu121-on-cu130 silent CPU-fall)
      * /etc/vast.env present IF REQUIRE_SELFSTOP is set (self-STOP would silently disable)
      * >= 50 GB free on the work volume

    Prints "PREFLIGHT FAIL" with each missing item and sys.exit(1); else
    "PREFLIGHT OK" and sys.exit(0). This is the intended FIRST line of full_batch.sh.
    """
    import glob
    import sys

    fail: list[str] = []

    # --- required binaries ---
    for binary in ("ffmpeg", "ffprobe"):
        if not shutil.which(binary):
            fail.append(f"missing binary: {binary}")

    # --- importable modules (report the exact module that failed) ---
    for mod in (
        "torch",
        "torchaudio",
        "faster_whisper",
        "easyocr",
        "cv2",
        "soundfile",
        "edge_tts",
        "PIL",
    ):
        try:
            __import__(mod)
        except Exception as exc:  # ImportError or a broken transitive
            fail.append(f"import {mod}: {exc}")
    # demucs 4.0.1 from pip ships WITHOUT demucs.api; the pipeline falls back to
    # the demucs.separate subprocess (pipeline/dub/separator.py). Accept EITHER so
    # a perfectly working box is not failed for a non-existent optional submodule.
    try:
        from demucs.api import Separator  # noqa: F401
    except Exception:
        try:
            from demucs.separate import main as _demucs_main  # noqa: F401
        except Exception as exc:
            fail.append(f"demucs unusable (neither demucs.api nor demucs.separate): {exc}")

    # --- model caches ON DISK (presence, not a lazy re-download) ---
    hf_hub = os.path.expanduser("~/.cache/huggingface/hub")
    for tag in ("tiny", "small", "medium"):
        if not glob.glob(os.path.join(hf_hub, f"*whisper*{tag}*")):
            fail.append(f"whisper '{tag}' model not on disk ({hf_hub})")
    if not glob.glob(os.path.expanduser("~/.EasyOCR/model/*")):
        fail.append("easyocr packs not on disk (~/.EasyOCR/model)")
    # get_model('htdemucs') caches HASH-named .th checkpoints under torch hub
    # (e.g. .../checkpoints/955717e8-8726e21a.th) — never a file literally named
    # 'htdemucs'. Presence of any demucs .th checkpoint == warmed.
    if not (
        glob.glob(os.path.expanduser("~/.cache/torch/hub/checkpoints/*.th"))
        or glob.glob(os.path.expanduser("~/.cache/torch/hub/**/*htdemucs*"), recursive=True)
    ):
        fail.append("demucs htdemucs not on disk (~/.cache/torch/hub/checkpoints/*.th)")

    # --- fonts: CJK + Thai coverage for outro text ---
    try:
        fc = subprocess.run(
            ["fc-list"], capture_output=True, text=True, timeout=30, check=False
        ).stdout
        if not any(key in fc for key in ("CJK", "Thai")):
            fail.append("CJK/Thai fonts missing (apt fonts-noto-cjk + fonts-noto-extra)")
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        fail.append(f"fc-list unavailable: {exc}")

    # --- ffmpeg has libx264 (NVENC is blocked on consumer Vast GPUs) ---
    if shutil.which("ffmpeg"):
        try:
            enc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            ).stdout
            if "libx264" not in enc:
                fail.append("ffmpeg libx264 encoder missing")
        except subprocess.SubprocessError as exc:
            fail.append(f"ffmpeg -encoders probe failed: {exc}")

    # --- GPU: torch.cuda available (catches cu121-on-cu130 silent CPU-fall) ---
    try:
        import torch  # type: ignore

        if not torch.cuda.is_available():
            fail.append("torch.cuda not available (cu121-on-cu130 silent-CPU bug?)")
    except Exception as exc:
        fail.append(f"cuda probe: {exc}")

    # --- self-STOP creds, only if explicitly required ---
    if env_flag("REQUIRE_SELFSTOP", False) and not os.path.exists("/etc/vast.env"):
        fail.append("/etc/vast.env missing (self-STOP would silently disable)")

    # --- disk: need >= 50 GB free on the work volume ---
    work_path = "/workspace" if os.path.isdir("/workspace") else os.getcwd()
    try:
        free = shutil.disk_usage(work_path).free
        if free < 50 * (1024**3):
            fail.append(f"<50GB free on {work_path} ({free // (1024**3)}GB)")
    except OSError as exc:
        fail.append(f"disk check on {work_path} failed: {exc}")

    if fail:
        print("PREFLIGHT FAIL:\n  " + "\n  ".join(fail))
        sys.exit(1)
    print("PREFLIGHT OK")
    sys.exit(0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight checks for GPU and API keys.")

    parser.set_defaults(
        require_translation_keys=None,
        require_grok=None,
        require_cuda=None,
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Run the STRICT box-side render preflight (binaries, imports, model "
            "caches on disk, fonts, libx264, CUDA, disk). Exits 1 on the first "
            "missing item. Intended as the first line of full_batch.sh."
        ),
    )

    translation_group = parser.add_mutually_exclusive_group()
    translation_group.add_argument(
        "--require-translation-keys",
        dest="require_translation_keys",
        action="store_true",
        help="Fail if all translation API key providers are missing.",
    )
    translation_group.add_argument(
        "--allow-missing-translation-keys",
        dest="require_translation_keys",
        action="store_false",
        help="Do not fail when translation API keys are missing.",
    )

    grok_group = parser.add_mutually_exclusive_group()
    grok_group.add_argument(
        "--require-grok",
        dest="require_grok",
        action="store_true",
        help="Fail if no valid Grok key is configured.",
    )
    grok_group.add_argument(
        "--allow-missing-grok",
        dest="require_grok",
        action="store_false",
        help="Allow startup without Grok key.",
    )

    cuda_group = parser.add_mutually_exclusive_group()
    cuda_group.add_argument(
        "--require-cuda",
        dest="require_cuda",
        action="store_true",
        help="Fail if no CUDA GPU is detected.",
    )
    cuda_group.add_argument(
        "--allow-missing-cuda",
        dest="require_cuda",
        action="store_false",
        help="Allow startup without CUDA GPU.",
    )

    return parser.parse_args()


def main():
    load_dotenv()
    args = _parse_args()

    # STRICT mode short-circuits the standard key/CUDA-flag preflight: it runs the
    # full box-side render-environment assertion and exits 0/1 itself.
    if args.strict:
        run_strict()
        return

    require_translation_keys = (
        args.require_translation_keys
        if args.require_translation_keys is not None
        else env_flag("REQUIRE_TRANSLATION_KEYS", True)
    )
    require_grok = (
        args.require_grok
        if args.require_grok is not None
        else env_flag("REQUIRE_GROK", False)
    )
    require_cuda = (
        args.require_cuda
        if args.require_cuda is not None
        else env_flag("REQUIRE_CUDA", False)
    )
    # P6.C: opt-in disk guard (Docker + installer set this)
    require_disk_space = env_flag("REQUIRE_DISK_SPACE", False)

    run_preflight(
        require_translation_keys=require_translation_keys,
        require_grok=require_grok,
        require_cuda=require_cuda,
        require_disk_space=require_disk_space,
    )


if __name__ == "__main__":
    main()
