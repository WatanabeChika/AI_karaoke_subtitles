"""Forced alignment and ASS generation."""

import argparse
import json
import os
import re
import unicodedata
from typing import List

from ai_karaoke.alignment.aligner import DEFAULT_CTC_MODEL, KaraokeCtcAligner
from ai_karaoke.alignment.models import AlignedToken
from ai_karaoke.interfaces.console import (
    ConsoleReporter,
    ProgressReporter,
    configure_runtime_output,
)
from ai_karaoke.lyrics.parser import parse_lrc_data_strict
from ai_karaoke.pronunciation.ruby import apply_furigana_to_ass
from ai_karaoke.pronunciation.whisper import (
    DEFAULT_FURIGANA_WHISPER_MODEL,
    FURIGANA_WHISPER_MODELS,
    detect_whisper_readings,
    validate_furigana_whisper_model,
)
from ai_karaoke.subtitles.colors import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
)
from ai_karaoke.subtitles.karaoke_tags import (
    DEFAULT_KARA_ADVANCE_MS,
    DEFAULT_KARA_SEP_THRESHOLD_MS,
)
from ai_karaoke.subtitles.style import apply_kara_style_to_ass

DEFAULT_KARA_HANDLE_STYLE = "Default"
DEFAULT_KARA_TEMPLATE_COMMENT_STYLE = "K1"
DIALOGUE_END_PAD_SEC = 0.5


def format_ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def generate_ass_header() -> str:
    return """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def process_word_k_tags(word_text: str, word_duration: float) -> str:
    cleaned = word_text.replace("\n", "")
    if not cleaned:
        return ""
    split_pattern = (
        r"([\s\!\?\,\.\:\;\-\—\…\"\'\“\”\‘\’\(\)\[\]\{\}\<\>"
        r"\、\。\！\？\：\；\（\）\［\］\｛\｝\「\」\『\』\【\】\《\》]+)"
    )
    parts = [part for part in re.split(split_pattern, cleaned) if part]
    duration = int(round(word_duration * 100))
    assigned = False
    output: List[str] = []
    for part in parts:
        if re.match(split_pattern, part):
            output.append(f"{{\\kf0}}{part}")
        elif not assigned:
            output.append(f"{{\\kf{duration}}}{part}")
            assigned = True
        else:
            output.append(f"{{\\kf0}}{part}")
    return "".join(output)


def _speech_chars(text: str) -> tuple[str, List[int]]:
    indexed = []
    for index, original in enumerate(text):
        for char in unicodedata.normalize("NFKC", original).casefold():
            if unicodedata.category(char)[0] in "LN" or char == "'":
                indexed.append((char, index))
    return "".join(ch for ch, _ in indexed), [index for _, index in indexed]


def _find_original_token(text: str, token: str, start: int) -> tuple[int, int] | None:
    compact, _ = _speech_chars(token)
    if not compact:
        return None
    source, positions = _speech_chars(text)
    offset = next((i for i, position in enumerate(positions) if position >= start), len(positions))
    index = source.find(compact, offset)
    if index < 0:
        return None
    return positions[index], positions[index + len(compact) - 1] + 1


def render_dialogue(text: str, tokens: List[AlignedToken]) -> str:
    if not tokens:
        return ""
    timed_tokens = [token for token in tokens if token.end > token.start]
    line_start = min(token.start for token in timed_tokens) if timed_tokens else tokens[0].start
    current_time = line_start
    cursor = 0
    output: List[str] = []
    for token in tokens:
        location = _find_original_token(text, token.text, cursor)
        if not location:
            raise ValueError(f"Alignment token {token.text!r} not found in lyric {text!r}")
        token_start, token_end = location
        if token_start > cursor:
            output.append(f"{{\\kf0}}{text[cursor:token_start]}")
        visible = text[token_start:token_end]
        cursor = token_end
        gap = max(0.0, token.start - current_time)
        if gap > 0.02:
            output.append(f"{{\\kf{int(round(gap * 100))}}}")
        output.append(process_word_k_tags(visible, max(0.0, token.end - token.start)))
        current_time = max(current_time, token.end)
    if cursor < len(text):
        output.append(f"{{\\kf0}}{text[cursor:]}")
    event_end = max(line_start + 0.01, current_time + DIALOGUE_END_PAD_SEC)
    return (
        f"Dialogue: 0,{format_ass_time(line_start)},{format_ass_time(event_end)},"
        f"Default,,0,0,0,,{''.join(output)}\n"
    )


def _load_audio(vocal_path: str):
    import soundfile as sf

    audio, sample_rate = sf.read(vocal_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sample_rate


def build_alignment_report(lines: List[dict], aligned: List[List[AlignedToken]]) -> List[dict]:
    report = []
    previous_end = None
    previous_hint = None
    for index, (line, tokens) in enumerate(zip(lines, aligned), start=1):
        raw_start = min(token.start for token in tokens)
        timed_tokens = [token for token in tokens if token.end > token.start]
        start = min(token.start for token in timed_tokens) if timed_tokens else raw_start
        end = max(token.end for token in tokens)
        overlap = max(0.0, previous_end - start) if previous_end is not None else 0.0
        zero_duration = sum(token.end <= token.start for token in tokens)
        warnings = []
        if abs(start - line["start_time"]) > 4.0:
            warnings.append("large_lrc_drift")
        if overlap > 0.15 and line["start_time"] != previous_hint:
            warnings.append("unexpected_overlap")
        if zero_duration:
            warnings.append("zero_duration_tokens")
        if zero_duration / len(tokens) > 0.25:
            warnings.append("many_zero_duration_tokens")
        confidence_values = [token.score for token in tokens if token.score is not None]
        mean_confidence = (
            sum(confidence_values) / len(confidence_values) if confidence_values else None
        )
        if mean_confidence is not None and mean_confidence < 0.15:
            warnings.append("low_ctc_confidence")
        item = {
            "line": index, "text": line["text"], "lrc_hint": line["start_time"],
            "start": start, "raw_start": raw_start, "end": end,
            "lrc_drift": start - line["start_time"],
            "previous_overlap": overlap, "zero_duration_tokens": zero_duration,
            "token_count": len(tokens), "mean_confidence": mean_confidence,
            "warnings": warnings,
        }
        if line.get("harmony_decisions"):
            item["inline_harmonies"] = line["harmony_decisions"]
        report.append(item)
        previous_end = max(previous_end or end, end)
        previous_hint = line["start_time"]
    return report


def generate_karaoke_ass(
    vocal_path: str,
    lrc_path: str,
    output_path: str,
    base_name: str,
    lang: str = "ja",
    ctc_batch_size: int = 1,
    ctc_number_reading: str = "auto",
    use_whisper_readings: bool = True,
    furigana_whisper_model: str = DEFAULT_FURIGANA_WHISPER_MODEL,
    kara_advance_time_ms: int = DEFAULT_KARA_ADVANCE_MS,
    kara_sep_threshold_ms: int = DEFAULT_KARA_SEP_THRESHOLD_MS,
    kara_primary_color: str = DEFAULT_KARA_PRIMARY_COLOR,
    kara_accent_color: str = DEFAULT_KARA_ACCENT_COLOR,
    reporter: ProgressReporter | None = None,
) -> str:
    validate_furigana_whisper_model(furigana_whisper_model)
    reporter = reporter or ConsoleReporter()
    with reporter.stage(2, 5, "Parsing lyrics and loading audio"):
        lines = parse_lrc_data_strict(lrc_path, lang)
        if not lines:
            raise ValueError("No lyric lines found in LRC")
        audio, sample_rate = _load_audio(vocal_path)

    with reporter.stage(3, 5, "Aligning lyrics (MMS Karaoke CTC)"):
        aligner = KaraokeCtcAligner(DEFAULT_CTC_MODEL, ctc_batch_size, ctc_number_reading)
        result = aligner.align(audio, sample_rate, lines, lang)
        lines = result.lines
        aligned = result.aligned
        del aligner
        alignment_report = build_alignment_report(lines, aligned)

    readings = [dict(line["readings"]) for line in lines]
    pronunciation_report = []
    stage_four = "Checking Japanese pronunciation" if use_whisper_readings else "Skipping pronunciation ASR"
    with reporter.stage(4, 5, stage_four):
        if lang.lower().startswith("ja") and use_whisper_readings:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            whisper_readings, pronunciation_report = detect_whisper_readings(
                audio, sample_rate, lines, aligned, furigana_whisper_model
            )
            readings = [{**heard, **explicit} for explicit, heard in zip(readings, whisper_readings)]

    pronunciation_report_path = None
    with reporter.stage(5, 5, "Rendering reports and ASS"):
        output_dir = os.path.join(output_path, base_name)
        os.makedirs(output_dir, exist_ok=True)
        ass_path = os.path.join(output_dir, f"{base_name}.ass")
        alignment_report_path = os.path.join(output_dir, f"{base_name}_alignment_report.json")
        with open(alignment_report_path, "w", encoding="utf-8") as stream:
            json.dump(alignment_report, stream, ensure_ascii=False, indent=2)
        warning_count = sum(bool(item["warnings"]) for item in alignment_report)
        with open(ass_path, "w", encoding="utf-8") as stream:
            stream.write(generate_ass_header())
            for index, (line, tokens) in enumerate(zip(lines, aligned), start=1):
                try:
                    stream.write(render_dialogue(line["text"], tokens))
                except ValueError as exc:
                    raise ValueError(f"Lyric line {index}: {exc}") from exc

        if lang.lower().startswith("ja"):
            apply_furigana_to_ass(ass_path, readings)
            if use_whisper_readings:
                pronunciation_report_path = os.path.join(output_dir, f"{base_name}_pronunciation_report.json")
                with open(pronunciation_report_path, "w", encoding="utf-8") as stream:
                    json.dump(pronunciation_report, stream, ensure_ascii=False, indent=2)

        apply_kara_style_to_ass(
            ass_path=ass_path, handle_style=DEFAULT_KARA_HANDLE_STYLE,
            advance_time_ms=kara_advance_time_ms, sep_threshold_ms=kara_sep_threshold_ms,
            insert_template_comments=True, template_comment_style=DEFAULT_KARA_TEMPLATE_COMMENT_STYLE,
            kara_primary_color=kara_primary_color, kara_accent_color=kara_accent_color,
        )
    reporter.info(f"Alignment audit: {alignment_report_path} ({warning_count} warned lines)")
    if pronunciation_report_path is not None:
        reporter.info(f"Pronunciation audit: {pronunciation_report_path}")
    reporter.info(f"ASS generated: {ass_path}")
    return ass_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate karaoke ASS from vocal audio and LRC")
    parser.add_argument("--vocal", required=True)
    parser.add_argument("--lrc", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-name", required=True)
    parser.add_argument("--lang", default="ja")
    parser.add_argument("--ctc-batch-size", type=int, default=1)
    parser.add_argument(
        "--ctc-number-reading", choices=("auto", "ja", "en"), default="auto",
        help="How CTC reads Arabic digits: auto uses English only in Latin-only lines; ja/en force a mode",
    )
    parser.add_argument(
        "--furigana-whisper-model",
        choices=FURIGANA_WHISPER_MODELS,
        default=DEFAULT_FURIGANA_WHISPER_MODEL,
    )
    parser.add_argument("--no-whisper-readings", action="store_true")
    parser.add_argument("--kara-advance-ms", type=int, default=DEFAULT_KARA_ADVANCE_MS)
    parser.add_argument("--kara-sep-threshold-ms", type=int, default=DEFAULT_KARA_SEP_THRESHOLD_MS)
    parser.add_argument("--kara-primary-color", default=DEFAULT_KARA_PRIMARY_COLOR)
    parser.add_argument("--kara-accent-color", default=DEFAULT_KARA_ACCENT_COLOR)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    configure_runtime_output(args.verbose)
    reporter = ConsoleReporter(args.verbose)
    generate_karaoke_ass(
        vocal_path=args.vocal,
        lrc_path=args.lrc,
        output_path=args.output,
        base_name=args.base_name,
        lang=args.lang,
        ctc_batch_size=args.ctc_batch_size,
        ctc_number_reading=args.ctc_number_reading,
        use_whisper_readings=not args.no_whisper_readings,
        furigana_whisper_model=args.furigana_whisper_model,
        kara_advance_time_ms=args.kara_advance_ms,
        kara_sep_threshold_ms=args.kara_sep_threshold_ms,
        kara_primary_color=args.kara_primary_color,
        kara_accent_color=args.kara_accent_color,
        reporter=reporter,
    )


if __name__ == "__main__":
    main()
