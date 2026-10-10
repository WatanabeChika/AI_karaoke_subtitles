"""Command-line interface for batch karaoke generation."""

import argparse
import sys

from ai_karaoke.application.pipeline import (
    NUMBER_READING_MODES,
    PipelineConfig,
    build_song_requests,
    discover_audio_paths,
    process_song,
)
from ai_karaoke.interfaces.console import ConsoleReporter, configure_runtime_output
from ai_karaoke.pronunciation.whisper import (
    DEFAULT_FURIGANA_WHISPER_MODEL,
    FURIGANA_WHISPER_MODELS,
)
from ai_karaoke.subtitles.colors import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
)
from ai_karaoke.subtitles.karaoke_tags import (
    DEFAULT_KARA_ADVANCE_MS,
    DEFAULT_KARA_SEP_THRESHOLD_MS,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI karaoke subtitles (Mel-Band RoFormer + global CTC + furigana)")
    parser.add_argument(
        "-i", "--input", action="append", nargs="+", default=[], metavar="AUDIO",
        help="Input song path(s). The option may be repeated.",
    )
    parser.add_argument(
        "--input-dir",
        help="Add all supported audio files directly inside this directory",
    )
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

    parser.add_argument("--lang", default="ja", help="Song language, e.g. ja/zh/en")
    parser.add_argument(
        "--ctc-batch-size",
        type=int,
        default=1,
        help="CTC emission batch size. Keep at 1 for GPUs with 6GB VRAM.",
    )
    parser.add_argument(
        "--ctc-number-reading", choices=NUMBER_READING_MODES, default="auto",
        help="How CTC reads Arabic digits: auto uses English only in Latin-only lines; ja/en force a mode",
    )
    parser.add_argument(
        "--furigana-whisper-model",
        choices=FURIGANA_WHISPER_MODELS,
        default=DEFAULT_FURIGANA_WHISPER_MODEL,
        help="Whisper model size used for Japanese pronunciation checks",
    )
    parser.add_argument("--no-whisper-readings", action="store_true", help="Skip Whisper pronunciation checks")

    parser.add_argument(
        "--kara-advance-ms",
        type=int,
        default=DEFAULT_KARA_ADVANCE_MS,
        help=(
            "Kara advance time (ms). "
            "By default, consecutive lines of lyrics that alternate are considered the same sequence, "
            "and the first line of that sequence will be advanced by this amount of time."
        ),
    )
    parser.add_argument(
        "--kara-sep-threshold-ms",
        type=int,
        default=DEFAULT_KARA_SEP_THRESHOLD_MS,
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
    parser.add_argument("--verbose", action="store_true", help="Show third-party diagnostic output")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    configure_runtime_output(args.verbose)
    reporter = ConsoleReporter(args.verbose)

    try:
        raw_inputs = [path for group in args.input for path in group]
        audio_paths = discover_audio_paths(raw_inputs, args.input_dir)
        requests = build_song_requests(audio_paths, args.lyric)
        config = PipelineConfig(
            lang=args.lang,
            ctc_batch_size=args.ctc_batch_size,
            ctc_number_reading=args.ctc_number_reading,
            use_whisper_readings=not args.no_whisper_readings,
            furigana_whisper_model=args.furigana_whisper_model,
            kara_advance_ms=args.kara_advance_ms,
            kara_sep_threshold_ms=args.kara_sep_threshold_ms,
            kara_primary_color=args.kara_primary_color,
            kara_accent_color=args.kara_accent_color,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"AI Karaoke: {len(requests)} song(s), {config.lang}, MMS Karaoke CTC")
    completed = []
    failures: list[tuple[str, str]] = []
    for index, request in enumerate(requests, start=1):
        reporter.batch_item(index, len(requests), request.base_name)
        try:
            completed.append(process_song(request, args.output, config, reporter))
        except Exception as exc:
            failures.append((request.base_name, str(exc)))
            reporter.error(str(exc))

    print(f"\nBatch complete: {len(completed)} succeeded, {len(failures)} failed")
    for artifacts in completed:
        print(f"  OK  {artifacts.title}: {artifacts.ass_path}")
    for title, error in failures:
        print(f"  FAIL  {title}: {error}", file=sys.stderr)
    if failures:
        parser.exit(1)


if __name__ == "__main__":
    main()
