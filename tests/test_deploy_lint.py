"""Lint tests for P6.B deployment artifacts.

Not real unit tests — these are "shipping config lint" tests that keep
the Dockerfile, GitHub Actions workflow, .dockerignore, and bash scripts
in sync with architectural expectations. Cheap to run, fast to catch
accidental regressions (e.g., someone removes ngrok from Dockerfile).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent


class TestDockerfile:
    """Verify the Dockerfile contains the critical P6.B pieces."""

    @pytest.fixture
    def dockerfile(self) -> str:
        return (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    def test_uses_cuda_12_3_base(self, dockerfile):
        """Base image is CUDA 12.3 — proven stable with faster-whisper."""
        assert "nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04" in dockerfile

    def test_is_multi_stage_build(self, dockerfile):
        """Dockerfile must use builder + runtime stages to keep image small."""
        assert "FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04 AS builder" in dockerfile
        assert "FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04 AS runtime" in dockerfile
        assert "COPY --from=builder /opt/venv" in dockerfile

    def test_pre_bakes_whisper_medium(self, dockerfile):
        """Whisper medium model must be downloaded at build time."""
        assert "WhisperModel('medium'" in dockerfile

    def test_pre_bakes_whisper_large_v3(self, dockerfile):
        """P6.B addition: large-v3 also pre-baked (own layer to avoid invalidating medium)."""
        assert "WhisperModel('large-v3'" in dockerfile

    def test_pre_bakes_easyocr_en_vi(self, dockerfile):
        """EasyOCR en + vi readers must be pre-downloaded."""
        assert "easyocr.Reader(['en'])" in dockerfile
        assert "easyocr.Reader(['vi'])" in dockerfile

    def test_pre_bakes_demucs_htdemucs(self, dockerfile):
        """Demucs htdemucs model must be cached at build time."""
        # Accepts either the modern demucs.api path or the pretrained fallback
        assert "htdemucs" in dockerfile
        assert "Separator" in dockerfile or "pretrained.get_model" in dockerfile

    def test_installs_ngrok(self, dockerfile):
        """P6.B addition: ngrok binary was missing from old Dockerfile."""
        assert "ngrok-v3-stable-linux-amd64.tgz" in dockerfile
        assert "chmod +x /usr/local/bin/ngrok" in dockerfile

    def test_installs_cloudflared(self, dockerfile):
        """Cloudflared must be installed for trycloudflare quick tunnel."""
        assert "cloudflared" in dockerfile

    def test_installs_noto_fonts(self, dockerfile):
        """Noto fonts required for OCR rendering (P4.2)."""
        assert "fonts-noto-cjk" in dockerfile
        assert "fonts-noto-core" in dockerfile
        assert "fonts-noto-extra" in dockerfile

    def test_has_healthcheck(self, dockerfile):
        """HEALTHCHECK directive for Docker health status."""
        assert "HEALTHCHECK" in dockerfile
        assert "/api/health" in dockerfile

    def test_exposes_port_3456(self, dockerfile):
        """Web app port must be exposed."""
        assert "EXPOSE 3456" in dockerfile

    def test_uses_buildkit_cache_mount(self, dockerfile):
        """BuildKit pip cache mount speeds up rebuilds by 3-5 min."""
        assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile

    def test_hf_download_timeout_env(self, dockerfile):
        """Large-v3 download needs extended timeout (default 10s too short)."""
        assert "HF_HUB_DOWNLOAD_TIMEOUT" in dockerfile


class TestDockerignore:
    """Verify .dockerignore excludes secrets + dev artifacts."""

    @pytest.fixture
    def dockerignore(self) -> str:
        return (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    def test_excludes_env(self, dockerignore):
        """Never ship .env (contains API keys)."""
        assert ".env" in dockerignore

    def test_excludes_git(self, dockerignore):
        """Don't bloat image with .git history."""
        assert ".git/" in dockerignore

    def test_excludes_tests(self, dockerignore):
        """Tests shouldn't ship in production image."""
        assert "tests/" in dockerignore

    def test_excludes_uploads(self, dockerignore):
        """Don't ship any local test uploads."""
        assert "uploads/" in dockerignore

    def test_excludes_claude_tooling(self, dockerignore):
        """.claude/ is dev tooling, not runtime."""
        assert ".claude/" in dockerignore

    def test_excludes_pycache(self, dockerignore):
        """No __pycache__ in shipped image."""
        assert "__pycache__/" in dockerignore


class TestGitHubWorkflow:
    """Verify .github/workflows/docker-build.yml is sane."""

    @pytest.fixture
    def workflow_path(self) -> Path:
        return PROJECT_ROOT / ".github" / "workflows" / "docker-build.yml"

    @pytest.fixture
    def workflow_data(self, workflow_path) -> dict:
        return yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    def test_workflow_file_exists(self, workflow_path):
        assert workflow_path.exists(), "Missing .github/workflows/docker-build.yml"

    def test_triggers_on_main_push(self, workflow_data):
        """Must build on every push to main (default branch)."""
        # YAML `on:` can be a dict or a list; in our file it's a dict.
        # Python's yaml parser sometimes converts `on:` key to True (boolean)
        # because YAML 1.1 treats "on" as a boolean. Handle both cases.
        on_config = workflow_data.get("on") or workflow_data.get(True)
        assert on_config is not None, "Missing 'on:' trigger config"
        assert "push" in on_config
        assert "main" in on_config["push"]["branches"]

    def test_has_workflow_dispatch(self, workflow_data):
        """Manual trigger should be available from the Actions tab."""
        on_config = workflow_data.get("on") or workflow_data.get(True)
        assert "workflow_dispatch" in on_config

    def test_has_packages_write_permission(self, workflow_data):
        """GHCR push requires `packages: write`."""
        perms = workflow_data.get("permissions", {})
        assert perms.get("packages") == "write", f"Expected packages:write, got {perms}"

    def test_uses_buildx_action(self, workflow_data):
        """docker/setup-buildx-action enables BuildKit cache."""
        steps = workflow_data["jobs"]["build"]["steps"]
        uses_list = [s.get("uses", "") for s in steps]
        assert any("docker/setup-buildx-action" in u for u in uses_list)

    def test_uses_login_action(self, workflow_data):
        """docker/login-action for GHCR authentication."""
        steps = workflow_data["jobs"]["build"]["steps"]
        uses_list = [s.get("uses", "") for s in steps]
        assert any("docker/login-action" in u for u in uses_list)

    def test_uses_metadata_action(self, workflow_data):
        """docker/metadata-action generates tags (latest, sha-X, etc)."""
        steps = workflow_data["jobs"]["build"]["steps"]
        uses_list = [s.get("uses", "") for s in steps]
        assert any("docker/metadata-action" in u for u in uses_list)

    def test_uses_build_push_action(self, workflow_data):
        """docker/build-push-action pushes to the registry."""
        steps = workflow_data["jobs"]["build"]["steps"]
        uses_list = [s.get("uses", "") for s in steps]
        assert any("docker/build-push-action" in u for u in uses_list)

    def test_pushes_to_ghcr(self, workflow_data):
        """Registry must be ghcr.io (per locked decision)."""
        env = workflow_data.get("env", {})
        assert env.get("REGISTRY") == "ghcr.io"

    def test_image_name_uses_repo(self, workflow_data):
        """Image name should be ${{ github.repository }} (auto-resolves)."""
        env = workflow_data.get("env", {})
        assert "${{ github.repository }}" in env.get("IMAGE_NAME", "")

    def test_uses_gha_cache(self, workflow_data):
        """GitHub Actions cache speeds up rebuilds (~15 min → ~5 min)."""
        steps = workflow_data["jobs"]["build"]["steps"]
        build_step = next((s for s in steps if "docker/build-push-action" in s.get("uses", "")), None)
        assert build_step is not None
        with_params = build_step.get("with", {})
        assert "type=gha" in with_params.get("cache-from", "")
        assert "type=gha" in with_params.get("cache-to", "")


class TestBashScripts:
    """Verify deploy/vastai-installer.sh + vastai-start.sh look sane."""

    def _read(self, name: str) -> str:
        return (PROJECT_ROOT / "deploy" / name).read_text(encoding="utf-8")

    def test_installer_exists(self):
        assert (PROJECT_ROOT / "deploy" / "vastai-installer.sh").exists()

    def test_start_script_exists(self):
        assert (PROJECT_ROOT / "deploy" / "vastai-start.sh").exists()

    def test_installer_has_shebang(self):
        content = self._read("vastai-installer.sh")
        assert content.startswith("#!/bin/bash"), "Installer missing #!/bin/bash shebang"

    def test_start_script_has_shebang(self):
        content = self._read("vastai-start.sh")
        assert content.startswith("#!/bin/bash"), "Start script missing #!/bin/bash shebang"

    def test_installer_uses_set_euo(self):
        """set -euo pipefail is mandatory for robust bash scripts."""
        content = self._read("vastai-installer.sh")
        assert "set -euo pipefail" in content

    def test_start_script_uses_set_euo(self):
        content = self._read("vastai-start.sh")
        assert "set -euo pipefail" in content

    def test_installer_reads_env_vars_not_flags(self):
        """Secrets must come from env vars, never CLI flags (bash history leak)."""
        content = self._read("vastai-installer.sh")
        # Should reference NGROK_TOKEN and GEMINI_KEYS from environment
        assert 'NGROK_TOKEN="${NGROK_TOKEN:-}"' in content
        assert 'GEMINI_KEYS="${GEMINI_KEYS:-}"' in content

    def test_installer_has_sentinel_dir(self):
        """Resume capability requires sentinel files."""
        content = self._read("vastai-installer.sh")
        assert "SENTINEL_DIR=" in content
        assert ".done" in content  # sentinel file extension

    def test_installer_has_9_steps(self):
        """Installer advertises 9 steps."""
        content = self._read("vastai-installer.sh")
        assert "TOTAL_STEPS=9" in content
        # Each step should have a run_step call
        run_step_count = len(re.findall(r"^run_step \d+ ", content, flags=re.MULTILINE))
        assert run_step_count == 9, f"Expected 9 run_step calls, got {run_step_count}"

    def test_installer_has_error_trap(self):
        """on_error handler must be wired via trap."""
        content = self._read("vastai-installer.sh")
        assert "trap 'on_error" in content

    def test_start_script_has_dual_tunnel(self):
        """Must start ngrok AND cloudflared in parallel."""
        content = self._read("vastai-start.sh")
        assert "ngrok http" in content
        assert "cloudflared tunnel" in content
        assert "trycloudflare" in content or "--url" in content

    def test_start_script_parses_tunnel_urls(self):
        """Must extract tunnel URLs from logs / API."""
        content = self._read("vastai-start.sh")
        # Ngrok URL parsed from local API
        assert "127.0.0.1:4040/api/tunnels" in content
        # Cloudflared URL parsed from log
        assert "trycloudflare.com" in content

    def test_start_script_kills_old_processes(self):
        """Idempotent restart: kill old uvicorn/ngrok/cloudflared first."""
        content = self._read("vastai-start.sh")
        assert "pkill -f uvicorn" in content
        assert "pkill -f ngrok" in content
        assert "pkill -f" in content and "cloudflared" in content

    def test_start_script_traps_sigterm(self):
        """Graceful shutdown of child processes on SIGTERM/SIGINT."""
        content = self._read("vastai-start.sh")
        assert "trap " in content and ("SIGTERM" in content or "SIGINT" in content)
