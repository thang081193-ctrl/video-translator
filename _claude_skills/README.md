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

## Dependencies
- The Video Translator project at `D:\Dev\Tools\Video Translator` (or wherever cloned) provides the `.venv` Python + ffmpeg / faster-whisper / Demucs.
- Skills expect that path to be available — update the `PROJECT` constant inside each script if the path differs on another machine.

## Notes
- All scripts force UTF-8 stdout to handle CJK / Arabic / Devanagari folder names on Windows.
- Caching is aggressive (Whisper transcripts, Demucs separations, per-video angle analyses) so daily reruns are fast.
