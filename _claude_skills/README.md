# Claude Code Skills — Video Ad Workflow

Custom skills built for processing Meta Ads Library videos for performance-marketing campaigns. Each folder is a self-contained skill. Drop the whole `skills/` folder into `~/.claude/skills/` on any machine and the skills become invocable as `/<skill-name>` in Claude Code.

## Skills

| Skill | Purpose |
|---|---|
| [classify-videos-by-language](classify-videos-by-language) | Detect spoken language via Whisper, move videos into per-language folders |
| [rename-language-folders](rename-language-folders) | Rename ISO-code / `Other_*` folders to native-script labels (e.g. `Other_km` → `ខ្មែរ`) |
| [rename-videos-by-language-date](rename-videos-by-language-date) | Rename mp4 files to `<LANG>_<DDMM><NN>.mp4` (e.g. `FR_120501.mp4`) |
| [analyze-ad-angles](analyze-ad-angles) | Cluster videos into creative angles via Whisper + Gemini multimodal; outputs CSVs and maintains a daily-stable taxonomy |
| [super-saiyan-translate](super-saiyan-translate) | High-quality two-phase translation: extract transcripts → human/LLM-translate in chat → dub with original BGM. Bypasses the project's auto-translate for better ad-hook fidelity |
| [meta-ads-prepare-ultimate](meta-ads-prepare-ultimate) | One-skill end-to-end pipeline on a single shared manifest (Whisper runs once): scan → organize → dub → brandpass → package. Multi-vertical + multi-language; per-video bgm_cluster routing, man/woman outro variants, freeze-to-EOF endcard trim. Supersedes chaining the separate skills below for large batches. |
| [meta-ads-prepare](meta-ads-prepare) | (formerly `brand-pass`) Prepare sources as Meta/TikTok ad creatives — Reels 1080×1920, Andromeda dedup evasion. Three pillars: (1) voice/BGM routing pre-step (`detect_voice.py`), (2) three audio modes — TTS re-dub / keep-original-voice / music-only, (3) freeze-to-EOF endcard trim (no over-trim on testimonials). Plus side-blur strip, max-content layout, royalty-free BGM pool by cluster. See [HANDOFF-text-reburn.md](meta-ads-prepare/HANDOFF-text-reburn.md) for the next phase. |
| [gen-outro](gen-outro) | Generate a branded outro card PNG (1080×1920) — app icon, title, subtitle, CTA. Themes: baby / tech / minimal. Preview before committing to a full meta-ads-prepare batch. |
| [trim-endcard](trim-endcard) | Standalone competitor outro/end-card trimmer (scene-change based). Use to clean a raw library before brand-passing. NOTE: the in-pipeline `meta-ads-prepare` endcard trim uses the newer freeze-to-EOF logic. |
| [split-campaigns](split-campaigns) | Voice/BGM classification (`detect_voice.py` → `voice_manifest.csv`) + campaign-split helper (`prep_campaign_split.py`). The voice-routing pre-step for meta-ads-prepare. |

## Dependencies
- The Video Translator project at `D:\Dev\Tools\Video Translator` (or wherever cloned) provides the `.venv` Python + ffmpeg / faster-whisper / Demucs.
- Skills expect that path to be available — update the `PROJECT` constant inside each script if the path differs on another machine.

## Notes
- All scripts force UTF-8 stdout to handle CJK / Arabic / Devanagari folder names on Windows.
- Caching is aggressive (Whisper transcripts, Demucs separations, per-video angle analyses) so daily reruns are fast.
