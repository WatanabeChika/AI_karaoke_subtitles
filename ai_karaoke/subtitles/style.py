"""Apply alternating K1/K2 karaoke presentation to generated ASS subtitles."""

from __future__ import annotations

from ai_karaoke.subtitles.colors import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
    ass_color_to_tag_color,
    normalize_kara_color,
    validate_distinct_kara_colors,
)
from ai_karaoke.subtitles.document import (
    find_section_bounds,
    format_ass_time_from_ms,
    is_stylable_dialogue,
    parse_ass_time_to_ms,
)
from ai_karaoke.subtitles.karaoke_tags import (
    DEFAULT_KARA_ADVANCE_MS,
    DEFAULT_KARA_SEP_THRESHOLD_MS,
    build_sequence_lead,
    normalize_zero_duration_k_parts,
)
from ai_karaoke.subtitles.templates import ensure_k_styles, ensure_template_comments


def apply_kara_style_to_ass(
    ass_path: str,
    handle_style: str = "Default",
    advance_time_ms: int = DEFAULT_KARA_ADVANCE_MS,
    sep_threshold_ms: int = DEFAULT_KARA_SEP_THRESHOLD_MS,
    insert_template_comments: bool = True,
    template_comment_style: str = "K1",
    kara_primary_color: str = DEFAULT_KARA_PRIMARY_COLOR,
    kara_accent_color: str = DEFAULT_KARA_ACCENT_COLOR,
) -> dict[str, int]:
    primary_color = normalize_kara_color(kara_primary_color, "kara_primary_color")
    accent_color = normalize_kara_color(kara_accent_color, "kara_accent_color")
    validate_distinct_kara_colors(primary_color, accent_color)
    primary_tag = ass_color_to_tag_color(primary_color)
    accent_tag = ass_color_to_tag_color(accent_color)

    with open(ass_path, "r", encoding="utf-8") as stream:
        lines = stream.readlines()
    event_start, event_end = find_section_bounds(lines, "[Events]")
    if event_start < 0:
        return {"styled_lines": 0, "max_style_index": 0, "inserted_comments": 0}

    styled_lines = 0
    max_style_index = 0
    previous_start = None
    previous_end = None
    previous_style = 2
    slot_events: dict[int, int] = {}
    for index in range(event_start + 1, event_end):
        raw = lines[index]
        if not raw.startswith("Dialogue:"):
            continue
        fields = raw[len("Dialogue: ") :].rstrip("\n").split(",", 9)
        if len(fields) < 10 or not is_stylable_dialogue(
            False, fields[3], fields[8], handle_style
        ):
            continue

        fields[9] = normalize_zero_duration_k_parts(fields[9])
        start_ms = parse_ass_time_to_ms(fields[1])
        end_ms = parse_ass_time_to_ms(fields[2])
        is_new_sequence = previous_start is None or start_ms - previous_end > sep_threshold_ms
        style_index = 1 if is_new_sequence else 3 - previous_style
        line_event_start = (
            max(0, start_ms - advance_time_ms) if is_new_sequence else max(0, previous_start)
        )

        prior_index = slot_events.get(style_index)
        if prior_index is not None:
            prior_fields = lines[prior_index][len("Dialogue: ") :].rstrip("\n").split(",", 9)
            prior_end = parse_ass_time_to_ms(prior_fields[2])
            if prior_end > line_event_start:
                if prior_end <= start_ms:
                    line_event_start = prior_end
                else:
                    line_event_start = start_ms
                    prior_fields[2] = format_ass_time_from_ms(start_ms)
                    lines[prior_index] = "Dialogue: " + ",".join(prior_fields) + "\n"

        slot_events[style_index] = index
        previous_start, previous_end, previous_style = start_ms, end_ms, style_index
        fields[9] = build_sequence_lead(
            start_ms - line_event_start, is_new_sequence
        ) + fields[9]
        fields[1] = format_ass_time_from_ms(line_event_start)
        fields[3] = f"K{style_index}"
        lines[index] = "Dialogue: " + ",".join(fields) + "\n"
        styled_lines += 1
        max_style_index = max(max_style_index, style_index)

    ensure_k_styles(lines, max_style_index, primary_color, accent_color)
    inserted_comments = 0
    if insert_template_comments:
        inserted_comments = ensure_template_comments(
            lines,
            base_style=template_comment_style,
            accent_tag=accent_tag,
            primary_tag=primary_tag,
        )
    with open(ass_path, "w", encoding="utf-8") as stream:
        stream.writelines(lines)
    return {
        "styled_lines": styled_lines,
        "max_style_index": max_style_index,
        "inserted_comments": inserted_comments,
    }
