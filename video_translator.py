#!/usr/bin/env python3
"""Video Translator — Transcribe, translate, and dub video subtitles."""

import argparse
import os
import shutil
import sys
import time

from dotenv import load_dotenv

from pipeline.audio import extract_audio, extract_audio_hq, get_video_info, check_ffmpeg
from pipeline.transcribe import transcribe
from pipeline.translate import translate_segments
from pipeline.subtitle import generate_srt
from pipeline.burn import burn_subtitles, burn_ocr_overlay, burn_with_overlays
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
    # OCR options
    parser.add_argument("--ocr", action="store_true", help="Detect and translate on-screen text (OCR)")
    parser.add_argument("--ocr-quality", default="fast", choices=["fast", "quality", "premium"],
                        help="OCR quality: fast (drawtext), quality (overlay), premium (inpaint+overlay)")

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
    if args.ocr:
        total_steps += 1
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

    # Step 3.5 (optional): OCR — detect and translate on-screen text
    ocr_filter = None
    ocr_overlays = None
    step_offset = 0
    if args.ocr:
        step_offset = 1
        print(f"\n[Step 4/{total_steps}] Detecting on-screen text (OCR, {args.ocr_quality} mode)...")
        try:
            from pipeline.ocr import (
                extract_key_frames, detect_text_regions,
                group_persistent_texts, group_persistent_texts_tracked,
                translate_ocr_texts, generate_drawtext_filter,
                generate_overlay_images, inpaint_all_regions,
            )

            video_info = get_video_info(args.input)
            frames_dir = os.path.join(output_dir, "_ocr_frames")
            key_frames = extract_key_frames(args.input, frames_dir, interval=2.0)

            print("  Running OCR on key frames...")
            frame_results = detect_text_regions(key_frames, lang=source_lang)

            if args.ocr_quality == "premium":
                text_groups = group_persistent_texts_tracked(frame_results)
            else:
                text_groups = group_persistent_texts(frame_results)

            if text_groups:
                print(f"  Translating {len(text_groups)} on-screen texts...")
                text_groups = translate_ocr_texts(text_groups, source_lang, target_lang)

                # ffmpeg drawtext cannot render diacritics (Vietnamese, French, etc.)
                ocr_qual = args.ocr_quality
                _DRAWTEXT_UNSAFE = {"vi", "fr", "es", "de", "pt", "it", "ru", "th", "hi", "ar"}
                if ocr_qual == "fast" and target_lang in _DRAWTEXT_UNSAFE:
                    print(f"  Auto-upgrading OCR fast->quality (drawtext can't render {target_lang} diacritics)")
                    ocr_qual = "quality"

                if ocr_qual == "fast":
                    ocr_filter = generate_drawtext_filter(
                        text_groups, video_info["width"], video_info["height"], target_lang
                    )
                elif ocr_qual == "quality":
                    print("  Rendering overlay images...")
                    ocr_overlays = generate_overlay_images(
                        text_groups, video_info["width"], video_info["height"],
                        output_dir, target_lang,
                    )
                elif ocr_qual == "premium":
                    print("  Inpainting text regions...")
                    inpaint_patches = inpaint_all_regions(args.input, text_groups, frames_dir)
                    print("  Rendering overlay images...")
                    ocr_overlays = generate_overlay_images(
                        text_groups, video_info["width"], video_info["height"],
                        output_dir, target_lang, inpaint_patches=inpaint_patches,
                    )
            else:
                print("  No persistent on-screen text found.")

            if ocr_qual != "premium":
                shutil.rmtree(frames_dir, ignore_errors=True)
        except ImportError as e:
            print(f"  OCR dependencies not available ({e}), skipping...")

    # Step 4: Generate SRT
    srt_step = 4 + step_offset
    srt_path = os.path.join(output_dir, f"{video_base}.{target_lang}.srt")
    print(f"\n[Step {srt_step}/{total_steps}] Generating SRT...")
    generate_srt(translated_segments, srt_path, text_key="translated_text")

    # Step 5: Burn subtitles / OCR overlay (if not dubbing) or skip
    burn_step = 5 + step_offset
    if not args.dub:
        has_ocr_work = ocr_filter or ocr_overlays
        if args.burn or has_ocr_work:
            video_ext = os.path.splitext(args.input)[1]
            output_video = os.path.join(output_dir, f"{video_base}.{target_lang}{video_ext}")
            print(f"\n[Step {burn_step}/{total_steps}] Burning subtitles/text overlay into video...")

            if ocr_overlays:
                burn_with_overlays(
                    args.input, ocr_overlays, output_video,
                    srt_path=srt_path if args.burn else None,
                )
            elif args.burn and ocr_filter:
                burn_subtitles(args.input, srt_path, output_video, ocr_filter=ocr_filter)
            elif args.burn:
                burn_subtitles(args.input, srt_path, output_video)
            elif ocr_filter:
                burn_ocr_overlay(args.input, ocr_filter, output_video)
        else:
            print(f"\n[Step {burn_step}/{total_steps}] Skipped (use --burn to embed subtitles in video)")
            output_video = None

        # Cleanup
        _cleanup(wav_path)
        for temp_dir in ["_ocr_frames", "_ocr_overlays", "_tts_temp"]:
            temp_path = os.path.join(output_dir, temp_dir)
            if os.path.isdir(temp_path):
                shutil.rmtree(temp_path, ignore_errors=True)

        elapsed = time.time() - start_time
        print(f"\nDone! ({elapsed:.1f}s)")
        print(f"  SRT file: {srt_path}")
        if output_video and (args.burn or has_ocr_work):
            print(f"  Video: {output_video}")
        return

    # --- Dubbing pipeline ---

    video_ext = os.path.splitext(args.input)[1]

    # Guard: skip dub if no speech segments detected
    if not translated_segments:
        tts_step = burn_step
        print(f"\n[Step {tts_step}/{total_steps}] No speech segments detected, skipping dubbing.")
        # Still handle OCR overlay if present
        has_ocr_work = ocr_filter or ocr_overlays
        if has_ocr_work:
            output_video = os.path.join(output_dir, f"{video_base}.{target_lang}{video_ext}")
            print(f"  Burning OCR text overlay...")
            if ocr_overlays:
                burn_with_overlays(args.input, ocr_overlays, output_video)
            elif ocr_filter:
                burn_ocr_overlay(args.input, ocr_filter, output_video)
            print(f"  Output: {output_video}")

        _cleanup(wav_path)
        for temp_dir in ["_tts_temp", "_ocr_frames", "_ocr_overlays"]:
            temp_path = os.path.join(output_dir, temp_dir)
            if os.path.isdir(temp_path):
                shutil.rmtree(temp_path, ignore_errors=True)
        elapsed = time.time() - start_time
        print(f"\nDone! ({elapsed:.1f}s)")
        print(f"  SRT file: {srt_path}")
        return

    # Step: Generate TTS + speed adjust
    tts_step = burn_step
    dubbed_audio_path = os.path.join(output_dir, f"{video_base}.{target_lang}.dubbed.m4a")
    print(f"\n[Step {tts_step}/{total_steps}] Generating TTS audio...")
    build_dubbed_audio(
        translated_segments,
        lang=target_lang,
        bgm_path=args.bgm,
        output_path=dubbed_audio_path,
        output_dir=output_dir,
        custom_voice=args.tts_voice,
    )

    # Step: Merge dubbed audio into video
    merge_step = tts_step + 1
    output_video = os.path.join(output_dir, f"{video_base}.{target_lang}.dubbed{video_ext}")
    print(f"\n[Step {merge_step}/{total_steps}] Merging dubbed audio into video...")
    dub_video(args.input, dubbed_audio_path, output_video)

    # Step: Burn subtitles / OCR into dubbed video (optional)
    final_step = merge_step + 1
    has_ocr_work = ocr_filter or ocr_overlays
    burned_video = None
    if args.burn or has_ocr_work:
        burned_video = os.path.join(output_dir, f"{video_base}.{target_lang}.dubbed.subbed{video_ext}")
        print(f"\n[Step {final_step}/{total_steps}] Burning subtitles/text overlay into dubbed video...")

        if ocr_overlays:
            burn_with_overlays(
                output_video, ocr_overlays, burned_video,
                srt_path=srt_path if args.burn else None,
            )
        elif args.burn and ocr_filter:
            burn_subtitles(output_video, srt_path, burned_video, ocr_filter=ocr_filter)
        elif args.burn:
            burn_subtitles(output_video, srt_path, burned_video)
        elif ocr_filter:
            burn_ocr_overlay(output_video, ocr_filter, burned_video)
    else:
        print(f"\n[Step {final_step}/{total_steps}] Skipped (use --burn to also embed subtitles)")

    # Cleanup
    _cleanup(wav_path)
    for temp_dir in ["_tts_temp", "_ocr_frames", "_ocr_overlays"]:
        temp_path = os.path.join(output_dir, temp_dir)
        if os.path.isdir(temp_path):
            shutil.rmtree(temp_path, ignore_errors=True)

    elapsed = time.time() - start_time
    print(f"\nDone! ({elapsed:.1f}s)")
    print(f"  SRT file: {srt_path}")
    print(f"  Dubbed video: {output_video}")
    if burned_video:
        print(f"  Dubbed + subtitled: {burned_video}")


def _cleanup(wav_path: str):
    """Remove temporary WAV file."""
    try:
        os.remove(wav_path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
