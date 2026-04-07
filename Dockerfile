FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    ffmpeg nginx apache2-utils curl \
    fonts-noto-cjk fonts-noto-core fonts-noto-extra fonts-dejavu-core \
    fonts-noto-ui-core fonts-noto-unhinted \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3
# Note: fonts-noto-extra includes Bengali, Telugu, Tamil, Devanagari, Arabic,
# Greek, Cyrillic, Thai, and most other scripts. fonts-noto-cjk covers CJK.

# Install cloudflared for Cloudflare Tunnel
RUN curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
    -o /tmp/cloudflared.deb && dpkg -i /tmp/cloudflared.deb && rm /tmp/cloudflared.deb

# PyTorch with CUDA 12
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# App dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-download ML models (avoid cold start delay)
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu')"
RUN python3 -c "import easyocr; easyocr.Reader(['en']); easyocr.Reader(['vi'])"

# Copy app code
COPY . /app
WORKDIR /app

# nginx config
COPY deploy/nginx.conf /etc/nginx/sites-available/default

# Startup script
COPY deploy/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80

# Health check — verify web server is responding
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:3456/api/health || exit 1

CMD ["/start.sh"]
