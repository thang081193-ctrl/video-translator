# Multi-stage Dockerfile for Video Translator (P6.B)
#
# Stage 1 (builder): pip-install everything into a venv at /opt/venv,
#   pre-download all ML models into HF/torch caches.
# Stage 2 (runtime): minimal image, COPY the venv + caches from builder.
#
# Result: ~7-8 GB image with Whisper medium + large-v3 + EasyOCR en+vi
# + Demucs htdemucs + cloudflared + ngrok all baked in. Cold start on
# Vast.ai goes from ~15 min (manual install + downloads) to ~3-5 min
# (docker pull + run).
#
# Build with BuildKit: `DOCKER_BUILDKIT=1 docker build -t vt .`

# ──────────────────────────────────────────────────────────────────────────
# STAGE 1 — builder
# ──────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0 \
    HF_HUB_DOWNLOAD_TIMEOUT=600

# Build deps + python (slim — no fonts/nginx/cloudflared in builder)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3

# Create venv at /opt/venv (will be copied to runtime stage)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# PyTorch with CUDA 12 (~2 GB layer, kept separate so app deps don't invalidate it)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# App dependencies (separate layer for fast rebuild on requirements change)
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt

# Pre-download Whisper medium (~1.5 GB) — own layer
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu')"

# Pre-download Whisper large-v3 (~3 GB) — own layer (P6.B addition)
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu')"

# Pre-download EasyOCR readers (~1 GB total) — own layer
RUN python3 -c "import easyocr; easyocr.Reader(['en']); easyocr.Reader(['vi'])"

# Pre-download Demucs htdemucs (~80 MB) — own layer
# Try the modern demucs.api first (P6.A in-process path), fall back to
# pretrained.get_model() if the API isn't exposed in the installed version.
RUN python3 -c "\
try:\n\
    from demucs.api import Separator\n\
    Separator(model='htdemucs')\n\
    print('Demucs htdemucs cached via demucs.api.Separator')\n\
except ImportError:\n\
    from demucs import pretrained\n\
    pretrained.get_model('htdemucs')\n\
    print('Demucs htdemucs cached via demucs.pretrained.get_model')"

# ──────────────────────────────────────────────────────────────────────────
# STAGE 2 — runtime
# ──────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime system deps: ffmpeg + nginx + fonts + jq + curl (for healthcheck/tunnel parse)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    ffmpeg nginx apache2-utils curl jq ca-certificates \
    fonts-noto-cjk fonts-noto-core fonts-noto-extra fonts-dejavu-core \
    fonts-noto-ui-core fonts-noto-unhinted \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3
# Note: fonts-noto-extra includes Bengali, Telugu, Tamil, Devanagari, Arabic,
# Greek, Cyrillic, Thai. fonts-noto-cjk covers CJK.

# Install cloudflared (named tunnel + trycloudflare quick tunnel support)
RUN curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
    -o /tmp/cloudflared.deb && dpkg -i /tmp/cloudflared.deb && rm /tmp/cloudflared.deb

# Install ngrok binary (P6.B addition — was missing from old Dockerfile)
RUN curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz \
    | tar xz -C /usr/local/bin && \
    chmod +x /usr/local/bin/ngrok

# Copy venv (with all Python deps) from builder
COPY --from=builder /opt/venv /opt/venv

# Copy ML model caches from builder (~5 GB total)
# - Whisper: ~/.cache/huggingface/hub/
# - EasyOCR: ~/.EasyOCR/
# - Demucs: ~/.cache/torch/hub/
COPY --from=builder /root/.cache /root/.cache
COPY --from=builder /root/.EasyOCR /root/.EasyOCR

# Copy app code (after .dockerignore filters out tests, .git, secrets, etc)
COPY . /app
WORKDIR /app

# nginx config + startup script
COPY deploy/nginx.conf /etc/nginx/sites-available/default
COPY deploy/start.sh /start.sh
RUN chmod +x /start.sh && \
    chmod +x deploy/vastai-start.sh deploy/vastai-installer.sh 2>/dev/null || true

EXPOSE 3456

# Health check — verify web server is responding
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:3456/api/health || exit 1

CMD ["/start.sh"]
