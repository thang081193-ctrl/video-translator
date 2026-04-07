"""Runtime preflight checks for GPU visibility and translation API keys."""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass

from dotenv import load_dotenv

from pipeline.config import cfg
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

    @property
    def total_translation_keys(self) -> int:
        return len(self.grok_keys) + len(self.gemini_keys) + len(self.vertex_keys)

    @property
    def has_grok(self) -> bool:
        return len(self.grok_keys) > 0


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

    return RuntimeStatus(
        cuda_available=cuda_available,
        gpu_count=gpu_count,
        gpu_name=gpu_name,
        gpu_probe_source=gpu_probe_source,
        grok_keys=grok_keys,
        gemini_keys=gemini_keys,
        vertex_keys=vertex_keys,
        invalid_grok_keys=invalid_grok,
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

    if status.has_grok:
        log.info("[OK] Grok API key detected.")
    else:
        log.warning("[WARN] Grok API key not found (set GROK_API_KEYS or GROK_API_KEY).")


def run_preflight(
    require_translation_keys: bool = True,
    require_grok: bool = False,
    require_cuda: bool = False,
) -> RuntimeStatus:
    status = collect_runtime_status()
    _print_summary(status)

    if require_cuda and not status.cuda_available:
        raise RuntimeError(
            "REQUIRE_CUDA is enabled but no GPU/CUDA device is visible. "
            "Check Vast instance type and runtime (--gpus all)."
        )

    if require_translation_keys and status.total_translation_keys == 0:
        raise RuntimeError(
            "No translation API key found. Add at least one of: "
            "GROK_API_KEYS, GEMINI_API_KEYS, VERTEX_API_KEYS."
        )

    if require_grok and not status.has_grok:
        raise RuntimeError(
            "REQUIRE_GROK is enabled but no valid Grok key was found. "
            "Set GROK_API_KEYS (keys must start with 'xai-')."
        )

    log.info("=== Preflight OK ===")
    return status


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight checks for GPU and API keys.")

    parser.set_defaults(
        require_translation_keys=None,
        require_grok=None,
        require_cuda=None,
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

    run_preflight(
        require_translation_keys=require_translation_keys,
        require_grok=require_grok,
        require_cuda=require_cuda,
    )


if __name__ == "__main__":
    main()
