"""Standalone ASS karaoke styling command."""

from __future__ import annotations

import argparse
import tempfile

from ai_karaoke.subtitles.colors import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
    normalize_kara_color,
)
from ai_karaoke.subtitles.karaoke_tags import (
    DEFAULT_KARA_ADVANCE_MS,
    DEFAULT_KARA_SEP_THRESHOLD_MS,
    normalize_zero_duration_k_parts,
)
from ai_karaoke.subtitles.style import apply_kara_style_to_ass


def _run_self_tests() -> None:
    assert normalize_kara_color("&h00ffffff", "x") == "&H00FFFFFF"
    assert normalize_kara_color("red", "x") == "&H000000FF"
    normalized = normalize_zero_duration_k_parts(
        r"{\k10}{\kf0} {\kf46}Go{\kf0}!{\kf42}{\kf0} {\kf8}Go{\kf0}!"
    )
    assert "{\\kf46}Go!" in normalized
    sample = (
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,"
        "100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,{\\kf50}A{\\kf50}B\n"
    )
    with tempfile.NamedTemporaryFile(
        "w+", encoding="utf-8", suffix=".ass", delete=True
    ) as stream:
        stream.write(sample)
        stream.flush()
        stats = apply_kara_style_to_ass(stream.name)
        assert stats["styled_lines"] == 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Ruby_Kara style layering to ASS subtitles")
    parser.add_argument("-i", "--input", help="Input ASS file path")
    parser.add_argument("--handle-style", default="Default")
    parser.add_argument("--advance-time-ms", type=int, default=DEFAULT_KARA_ADVANCE_MS)
    parser.add_argument("--sep-threshold-ms", type=int, default=DEFAULT_KARA_SEP_THRESHOLD_MS)
    parser.add_argument("--no-template-comments", action="store_true")
    parser.add_argument("--template-comment-style", default="K1")
    parser.add_argument("--kara-primary-color", default=DEFAULT_KARA_PRIMARY_COLOR)
    parser.add_argument("--kara-accent-color", default=DEFAULT_KARA_ACCENT_COLOR)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _run_self_tests()
        print("Self-test passed: kara_style")
        return
    if not args.input:
        parser.error("--input is required unless --self-test is used")
    stats = apply_kara_style_to_ass(
        ass_path=args.input,
        handle_style=args.handle_style,
        advance_time_ms=args.advance_time_ms,
        sep_threshold_ms=args.sep_threshold_ms,
        insert_template_comments=not args.no_template_comments,
        template_comment_style=args.template_comment_style,
        kara_primary_color=args.kara_primary_color,
        kara_accent_color=args.kara_accent_color,
    )
    print(
        "Kara style applied, "
        f"processed lines: {stats['styled_lines']}, "
        f"max style: K{stats['max_style_index'] if stats['max_style_index'] else 0}, "
        f"new template comments: {stats['inserted_comments']}"
    )


if __name__ == "__main__":
    main()
