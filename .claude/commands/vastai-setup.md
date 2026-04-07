---
description: "Full setup command for new Vast.ai GPU instance — installs all deps, clones repo, downloads models, installs ngrok"
---

Give the user the full Vast.ai setup command. They will paste it into Jupyter Terminal on a fresh Vast.ai instance.

**IMPORTANT**: Ask the user for their GitHub personal access token first (the repo is private). Also ask if they want to use existing Gemini API keys or new ones.

The full setup command (single line, paste into terminal):

```
apt-get update && apt-get install -y --no-install-recommends ffmpeg curl git fonts-noto-cjk fonts-noto-core fonts-noto-extra fonts-dejavu-core && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cu121 && pip install --no-cache-dir "faster-whisper>=1.0.0" "google-genai>=1.0.0" "python-dotenv>=1.0.0" "edge-tts>=6.1.0" "fastapi>=0.110.0" "uvicorn>=0.29.0" "python-multipart>=0.0.9" "easyocr>=1.7.0" "opencv-python>=4.8.0" "numpy>=1.24.0" "Pillow>=10.0.0" "demucs>=4.0.0" "soundfile>=0.12.0" && curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz | tar xz -C /usr/local/bin && ngrok config add-authtoken <NGROK_TOKEN> && git clone https://<GITHUB_TOKEN>@github.com/thang081193-ctrl/video-translator.git /workspace/video-translator && cd /workspace/video-translator && echo 'GEMINI_API_KEYS=<KEYS>' > .env && python3 -c "from faster_whisper import WhisperModel; WhisperModel('medium', device='cpu')" && python3 -c "import easyocr; easyocr.Reader(['en']); easyocr.Reader(['vi'])" && echo "=== SETUP COMPLETE ==="
```

Replace:
- `<NGROK_TOKEN>` with ngrok authtoken (from https://dashboard.ngrok.com/get-started/your-authtoken)
- `<GITHUB_TOKEN>` with GitHub PAT
- `<KEYS>` with comma-separated Gemini API keys

After setup, remind them to run `/vastai-start` to start the server.
