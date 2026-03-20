#!/usr/bin/env python3
"""Video Translator — Transcribe, translate, and dub video subtitles."""

import argparse
import os
import shutil
import sys
import time

from dotenv import load_dotenv

from pipeline.audio import extract_audio, check_ffmpeg
from pipeline.transcribe import transcribe
from pipeline.translate import translate_segments
from pipeline.subtitle import generate_srt
from pipeline.burn import burn_subtitles
from pipeline.dub import build_dubbed_audio, dub_video


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Translate video subtitles using Whisper + Gemini API"
    )
    parser.add_argument("input", help="Path to video file")
    parser.add_argument("-t", "--target", required=False, help="Target language code (e.g., vi, ja, ko)")
    parser.add_argument("-s", "--source", default=None, help="Source language code (auto-detect if omitted)")
    parser.add_argument("--whisper-model", default="medium", choices=["tiny", "base", "medium", "large-v3"], help="Whisper model size (default: medium)")
    parser.add_argument("--burn", action="store_true", help="Burn subtitles into video")
    parser.add_argument("-o", "--output-dir", default=None, help="Output directory (default: same as video)")
    parser.add_argument("--transcribe-only", action="store_true", help="Only transcribe, skip translation")
    parser.add_argument("--no-cache", action="store_true", help="Force re-transcribe and re-translate")
    parser.add_argument("--batch-size", type=int, default=20, help="Segments per translation API call (default: 20)")
    # Dubbing options
    parser.add_argument("--dub", action="store_true", help="Generate dubbed video with TTS voice")
    parser.add_argument("--bgm", default=None, help="Path to background music file (required with --dub)")
    parser.add_argument("--tts-voice", default=None, help="Override default TTS voice (e.g., vi-VN-NamMinhNeural)")

    args = parser.parse_args()

    # Validate args
    if not args.transcribe_only and not args.target:
        parser.error("--target is required unless --transcribe-only is set")

    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")

    if args.dub and not args.bgm:
        parser.error("--bgm is required when using --dub")

    if args.dub and args.bgm and not os.path.isfile(args.bgm):
        parser.error(f"Background music file not found: {args.bgm}")

    if not os.path.isfile(args.input):
        print(f"Error: Video file not found: {args.input}")
        sys.exit(1)

    # Check ffmpeg
    try:
        check_ffmpeg()
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Setup output dir
    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.input))
    os.makedirs(output_dir, exist_ok=True)
    video_base = os.path.splitext(os.path.basename(args.input))[0]
    use_cache = not args.no_cache

    total_steps = 7 if args.dub else 5
    start_time = time.time()

    # Step 1: Extract audio
    print(f"\n[Step 1/{total_steps}] Extracting audio...")
    wav_path = extract_audio(args.input, output_dir)
    print(f"  Audio extracted: {wav_path}")

    # Step 2: Transcribe
    print(f"\n[Step 2/{total_steps}] Transcribing with Whisper ({args.whisper_model})...")
    segments, detected_lang = transcribe(
        wav_path,
        model_name=args.whisper_model,
        source_lang=args.source,
        cache_dir=output_dir,
        use_cache=use_cache,
    )

    source_lang = args.source or detected_lang

    if args.transcribe_only:
        # Generate SRT with original text
        srt_path = os.path.join(output_dir, f"{video_base}.{source_lang}.srt")
        print(f"\n[Step 3/{total_steps}] Generating transcript SRT...")
        generate_srt(segments, srt_path, text_key="text")
        _cleanup(wav_path)
        elapsed = time.time() - start_time
        print(f"\nDone! ({elapsed:.1f}s)")
        print(f"  Transcript: {srt_path}")
        return

    # Step 3: Translate
    target_lang = args.target
    cache_path = os.path.join(output_dir, f"{video_base}.{source_lang}_{target_lang}.translated.json")
    print(f"\n[Step 3/{total_steps}] Translating {source_lang} -> {target_lang} (Gemini)...")
    translated_segments = translate_segments(
        segments,
        source_lang=source_lang,
        target_lang=target_lang,
        batch_size=args.batch_size,
        cache_path=cache_path,
        use_cache=use_cache,
    )

    # Step 4: Generate SRT
    srt_path = os.path.join(output_dir, f"{video_base}.{target_lang}.srt")
    print(f"\n[Step 4/{total_steps}] Generating SRT...")
    generate_srt(translated_segments, srt_path, text_key="translated_text")

    # Step 5: Burn subtitles (if not dubbing) or skip
    if not args.dub:
        if args.burn:
            video_ext = os.path.splitext(args.input)[1]
            output_video = os.path.join(output_dir, f"{video_base}.{target_lang}{video_ext}")
            print(f"\n[Step 5/{total_steps}] Burning subtitles into video...")
            burn_subtitles(args.input, srt_path, output_video)
        else:
            print(f"\n[Step 5/{total_steps}] Skipped (use --burn to embed subtitles in video)")

        _cleanup(wav_path)
        elapsed = time.time() - start_time
        print(f"\nDone! ({elapsed:.1f}s)")
        print(f"  SRT file: {srt_path}")
        if args.burn:
            print(f"  Video: {output_video}")
        return

    # --- Dubbing pipeline ---

    # Step 5: Generate TTS + speed adjust
    video_ext = os.path.splitext(args.input)[1]
    dubbed_audio_path = os.path.join(output_dir, f"{video_base}.{target_lang}.dubbed.m4a")
    print(f"\n[Step 5/{total_steps}] Generating TTS audio...")
    build_dubbed_audio(
        translated_segments,
        lang=target_lang,
        bgm_path=args.bgm,
        output_path=dubbed_audio_path,
        output_dir=output_dir,
        custom_voice=args.tts_voice,
    )

    # Step 6: Merge dubbed audio into video
    output_video = os.path.join(output_dir, f"{video_base}.{target_lang}.dubbed{video_ext}")
    print(f"\n[Step 6/{total_steps}] Merging dubbed audio into video...")
    dub_video(args.input, dubbed_audio_path, output_video)

    # Step 7: Burn subtitles into dubbed video (optional)
    if args.burn:
        burned_video = os.path.join(output_dir, f"{video_base}.{target_lang}.dubbed.subbed{video_ext}")
        print(f"\n[Step 7/{total_steps}] Burning subtitles into dubbed video...")
        burn_subtitles(output_video, srt_path, burned_video)
    else:
        print(f"\n[Step 7/{total_steps}] Skipped (use --burn to also embed subtitles)")

    # Cleanup
    _cleanup(wav_path)
    tts_temp = os.path.join(output_dir, "_tts_temp")
    if os.path.isdir(tts_temp):
        shutil.rmtree(tts_temp, ignore_errors=True)

    elapsed = time.time() - start_time
    print(f"\nDone! ({elapsed:.1f}s)")
    print(f"  SRT file: {srt_path}")
    print(f"  Dubbed video: {output_video}")
    if args.burn:
        print(f"  Dubbed + subtitled: {burned_video}")


def _cleanup(wav_path: str):
    """Remove temporary WAV file."""
    try:
        os.remove(wav_path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
