# Video Translator — Project Status

## Current State: Phase 2 Complete (Dubbing Pipeline)

### What's Working

**Phase 1 — Subtitle Translation Pipeline** (DONE)
- [x] Extract audio from video (ffmpeg, WAV 16kHz mono)
- [x] Transcribe with Whisper (faster-whisper, GPU/CPU auto-fallback)
- [x] Translate via Gemini API (batch, context-aware, multi-key rotation)
- [x] Generate SRT subtitles
- [x] Burn subtitles into video (optional, ffmpeg libass)
- [x] Caching (transcript + translation JSON, skip on re-run)

**Phase 2 — Video Dubbing** (DONE)
- [x] TTS voice generation per segment (edge-tts, free, 15+ languages)
- [x] Speed adjustment to fit subtitle timing (ffmpeg atempo, chain for >2x)
- [x] Fade in/out at segment boundaries (15ms, anti-click)
- [x] Silence gaps between segments
- [x] Background music mixing (user-provided file, looped, volume 10%)
- [x] Audio limiter to prevent clipping (alimiter 0.95)
- [x] Merge dubbed audio into video (copy video stream, no re-encode)
- [x] Optional burn subtitles on dubbed video

**Infrastructure**
- [x] Multi API key rotation (GEMINI_API_KEYS, auto-switch on 429)
- [x] 5 Gemini API keys configured
- [x] Batch size validation (--batch-size >= 1)
- [x] ffmpeg + ffprobe checks at startup

### CLI Usage

```bash
# Translate subtitles only
python video_translator.py video.mp4 -t vi

# Translate + burn subtitles
python video_translator.py video.mp4 -t vi --burn

# Full dubbing (TTS + background music)
python video_translator.py video.mp4 -t vi --dub --bgm music.mp3

# Dubbing + burn subtitles
python video_translator.py video.mp4 -t vi --dub --bgm music.mp3 --burn

# Custom TTS voice
python video_translator.py video.mp4 -t vi --dub --bgm music.mp3 --tts-voice vi-VN-NamMinhNeural

# Transcribe only (no translation)
python video_translator.py video.mp4 --transcribe-only

# Force re-process (ignore cache)
python video_translator.py video.mp4 -t vi --no-cache
```

### Project Structure

```
video-translator/
├── video_translator.py       # CLI entry point (argparse)
├── .env                      # API keys (gitignored)
├── .env.example              # Template
├── requirements.txt          # Python deps
├── STATUS.md                 # This file
├── pipeline/
│   ├── audio.py              # Step 1: Extract audio (ffmpeg)
│   ├── transcribe.py         # Step 2: Whisper STT (faster-whisper)
│   ├── translate.py          # Step 3: Gemini API translation + key rotation
│   ├── subtitle.py           # Step 4: Generate SRT
│   ├── burn.py               # Step 5: Burn subs into video (ffmpeg)
│   └── dub.py                # Step 5-7: TTS + mix BGM + merge audio
```

### Tech Stack

| Component | Tool | Notes |
|-----------|------|-------|
| Audio extraction | ffmpeg | WAV 16kHz mono |
| Speech-to-Text | faster-whisper | GPU auto-fallback to CPU |
| Translation | Gemini 2.0 Flash | 5 keys, auto-rotation |
| TTS | edge-tts | Free, Microsoft, 322+ voices |
| Audio mixing | ffmpeg | alimiter, volume control |
| Subtitle | Custom SRT writer | No external lib |

### Known Issues / Bugs Fixed
- [x] P1: Crash when no subtitle segments (unbound variable `i`)
- [x] P1: --batch-size=0 causes ZeroDivisionError
- [x] P2: SRT index jumps on empty segments
- [x] P2: Timestamp millisecond rounding to 1000
- [x] P2: Missing ffprobe check
- [x] P3: Cache not fingerprinted by model/source
- [x] Audio clipping/distortion when mixing TTS + BGM (added alimiter + fade)

### Phase 3 — TODO (Not Started)
- [ ] GUI (Gradio / Streamlit)
- [ ] Batch processing (multiple videos)
- [ ] Dual subtitles (original + translated)
- [ ] Speaker diarization (pyannote-audio)
- [ ] Voice cloning / dubbing (ElevenLabs, high quality)
- [ ] YouTube URL support (auto download)
- [ ] Progress bar / real-time tracking
- [ ] Vocal separation from original (isolate BGM from source video)

### System Requirements
- Python 3.10+
- ffmpeg + ffprobe in PATH
- CUDA (optional, speeds up Whisper 10-20x)
- Internet (for Gemini API + edge-tts)
