import argparse
import os

from aligner import DEFAULT_WHISPER_MODEL, generate_karaoke_ass
from kara_style import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
    normalize_kara_color,
    validate_distinct_kara_colors,
)
from separator import separate_audio


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI karaoke subtitles (Demucs + Whisper + Furigana + Kara Style)")
    parser.add_argument("-i", "--input", required=True, help="Input song path")
    parser.add_argument(
        "-l",
        "--lyric",
        default=None,
        help=(
            "Input LRC path. Optional: if omitted, the pipeline will try "
            "to find a same-name .lrc file next to the input song."
        ),
    )
    parser.add_argument("-o", "--output", required=True, help="Output directory")

    parser.add_argument("--inst", default=None, help="Official instrumental path")
    parser.add_argument("--lang", default="ja", help="Song language, e.g. ja/zh/en")
    parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        help=(
            "Whisper model used by aligner. "
            "Common choices: tiny/base/small/medium/large/turbo. "
            "Approx VRAM (FP16): tiny/base~1GB, small~2GB, medium~5GB, large~10GB, turbo~6GB."
        ),
    )

    parser.add_argument(
        "--kara-advance-ms",
        type=int,
        default=3000,
        help=(
            "Kara advance time (ms). "
            "By default, consecutive lines of lyrics that alternate are considered the same sequence, "
            "and the first line of that sequence will be advanced by this amount of time."
        ),
    )
    parser.add_argument(
        "--kara-sep-threshold-ms",
        type=int,
        default=10000,
        help=(
            "Kara sequence separation threshold (ms). "
            "When the interval between two lines of lyrics exceeds this value, "
            "the next line will not follow the previous one, but will start a new sequence."
        ),
    )

    parser.add_argument(
        "--kara-primary-color",
        default=DEFAULT_KARA_PRIMARY_COLOR,
        help="K-style primary color. Supports ASS code (&H00FFFFFF) or names like white/red/blue",
    )
    parser.add_argument(
        "--kara-accent-color",
        default=DEFAULT_KARA_ACCENT_COLOR,
        help="K-style accent color. Supports ASS code (&H002F1CD8) or names like white/red/blue",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        primary_color = normalize_kara_color(args.kara_primary_color, "--kara-primary-color")
        accent_color = normalize_kara_color(args.kara_accent_color, "--kara-accent-color")
        validate_distinct_kara_colors(primary_color, accent_color)
    except ValueError as e:
        parser.error(str(e))

    base_name = os.path.splitext(os.path.basename(args.input))[0]
    os.makedirs(args.output, exist_ok=True)

    lyric_path = args.lyric
    if lyric_path is None:
        song_dir = os.path.dirname(os.path.abspath(args.input))
        fallback_candidates = [
            os.path.join(song_dir, f"{base_name}.lrc"),
            os.path.join(song_dir, f"{base_name}.LRC"),
        ]
        lyric_path = next((p for p in fallback_candidates if os.path.exists(p)), None)
        if lyric_path:
            print(f"Lyrics auto-detected: {lyric_path}")
        else:
            parser.error(
                "No lyrics provided. Please pass --lyric, or place a same-name .lrc file next to the song file."
            )

    print("=" * 50)
    print("AI Karaoke Pipeline")
    print(f"Song: {base_name}")
    print(f"Language: {args.lang}")
    print("=" * 50)

    try:
        vocal_path, _ = separate_audio(
            audio_path=args.input,
            output_path=args.output,
            base_name=base_name,
            official_inst_path=args.inst,
        )

        generate_karaoke_ass(
            vocal_path=vocal_path,
            lrc_path=lyric_path,
            output_path=args.output,
            base_name=base_name,
            lang=args.lang,
            whisper_model=args.whisper_model,
            kara_advance_time_ms=args.kara_advance_ms,
            kara_sep_threshold_ms=args.kara_sep_threshold_ms,
            kara_primary_color=primary_color,
            kara_accent_color=accent_color,
        )
    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    main()
