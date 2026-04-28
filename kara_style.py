import argparse
import math
import re
import tempfile
import unicodedata
from typing import Dict, List, Tuple


K_TAG_PATTERN = re.compile(r"\{\\(k(?:f|o)?)(\d+)\}([^{}]*)")
ASS_COLOR_PATTERN = re.compile(r"^&H[0-9A-F]{8}$")

DEFAULT_KARA_PRIMARY_COLOR = "&H00FFFFFF"
DEFAULT_KARA_ACCENT_COLOR = "&H002F1CD8"

NAMED_ASS_COLORS: Dict[str, str] = {
    "white": "&H00FFFFFF",
    "black": "&H00000000",
    "red": "&H000000FF",
    "green": "&H0000FF00",
    "blue": "&H00FF0000",
    "yellow": "&H0000FFFF",
    "cyan": "&H00FFFF00",
    "magenta": "&H00FF00FF",
    "gray": "&H00808080",
    "grey": "&H00808080",
    "orange": "&H0000A5FF",
    "purple": "&H00800080",
    "pink": "&H00CBC0FF",
}


def normalize_kara_color(color: str, arg_name: str) -> str:
    token = color.strip()
    mapped = NAMED_ASS_COLORS.get(token.lower())
    if mapped:
        return mapped

    normalized = token.upper()
    if not ASS_COLOR_PATTERN.match(normalized):
        raise ValueError(
            f"{arg_name} must be ASS color (&H00FFFFFF) or named color "
            f"({', '.join(sorted(NAMED_ASS_COLORS.keys()))})"
        )
    return normalized


def validate_distinct_kara_colors(primary_color: str, accent_color: str) -> None:
    if primary_color[-6:] == accent_color[-6:]:
        raise ValueError("kara_primary_color and kara_accent_color must be different")


def _ass_color_to_tag_color(color: str) -> str:
    return f"&H{color[-6:]}&"


def _template_comments(base_style: str, accent_tag: str, primary_tag: str) -> List[str]:
    return [
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},,0,0,0,code syl all,fxgroup.kara=syl.inline_fx==""\n',
        f'Comment: 1,0:00:00.00,0:00:00.00,{base_style},overlay,0,0,0,template syl noblank all fxgroup kara,!retime("line",0,0)!{{\\pos($center,$middle)\\an5\\shad0\\1c{accent_tag}\\3c{primary_tag}\\clip(!$sleft-3!,0,!$sleft-3!,1080)\\t($sstart,$send,\\clip(!$sleft-3!,0,!$sright+3!,1080))\\bord5}}\n',
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},,0,0,0,template syl all fxgroup kara,!retime("line",0,0)!{{\\pos($center,$middle)\\an5}}\n',
        f'Comment: 1,0:00:18.65,0:00:20.65,{base_style},overlay,0,0,0,template furi all,!retime("line",0,0)!{{\\pos($center,!$middle+10!)\\an5\\shad0\\1c{accent_tag}\\3c{primary_tag}\\clip(!$sleft-3!,0,!$sleft-3!,1080)\\t($sstart,$send,\\clip(!$sleft-3!,0,!$sright+3!,1080))\\bord5}}\n',
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},,0,0,0,template furi all,!retime("line",0,0)!{{\\pos($center,!$middle+10!)\\an5}}\n',
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},music,0,0,0,template fx no_k,!retime("line",0,0)!{{\\pos($center,!$middle!)\\an5\\1c&H505050&\\3c&HFFFFFFF&}}\n',
    ]


def _parse_ass_time_to_ms(t: str) -> int:
    h, m, rest = t.split(":")
    s, cs = rest.split(".")
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(cs) * 10


def _format_ass_time_from_ms(ms: int) -> str:
    ms = max(0, ms)
    total_cs = ms // 10
    h = total_cs // (3600 * 100)
    rem = total_cs % (3600 * 100)
    m = rem // (60 * 100)
    rem = rem % (60 * 100)
    s = rem // 100
    cs = rem % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _find_section_bounds(lines: List[str], section_name: str) -> Tuple[int, int]:
    start = -1
    end = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == section_name:
            start = i
            break
    if start < 0:
        return -1, -1
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("[") and lines[j].strip().endswith("]"):
            end = j
            break
    return start, end


def _make_style_line(style_name: str, odd_template: bool, primary_color: str, accent_color: str) -> str:
    if odd_template:
        align, margin_l, margin_r, margin_v = 1, 120, 30, 220
    else:
        align, margin_l, margin_r, margin_v = 3, 30, 120, 40

    return (
        f"Style: {style_name},Noto Serif JP Black,80,{primary_color},{accent_color},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,{align},"
        f"{margin_l},{margin_r},{margin_v},1\n"
    )


def _ensure_k_styles(lines: List[str], max_style_i: int, primary_color: str, accent_color: str) -> None:
    if max_style_i <= 0:
        return

    style_start, style_end = _find_section_bounds(lines, "[V4+ Styles]")
    if style_start < 0:
        return

    existing_indices: Dict[str, int] = {}
    insert_pos = style_end
    for i in range(style_start + 1, style_end):
        if not lines[i].startswith("Style: "):
            continue
        insert_pos = i + 1
        body = lines[i][len("Style: ") :].strip()
        name = body.split(",", 1)[0]
        existing_indices[name] = i

    to_insert: List[str] = []
    for i in range(1, max_style_i + 1):
        name = f"K{i}"
        line = _make_style_line(name, odd_template=(i % 2 == 1), primary_color=primary_color, accent_color=accent_color)
        if name in existing_indices:
            lines[existing_indices[name]] = line
        else:
            to_insert.append(line)

    if to_insert:
        lines[insert_pos:insert_pos] = to_insert


def _ensure_template_comments(lines: List[str], base_style: str, accent_tag: str, primary_tag: str) -> int:
    event_start, event_end = _find_section_bounds(lines, "[Events]")
    if event_start < 0:
        return 0

    marker = 'code syl all,fxgroup.kara=syl.inline_fx==""'
    for i in range(event_start + 1, event_end):
        if marker in lines[i]:
            return 0

    format_pos = -1
    for i in range(event_start + 1, event_end):
        if lines[i].startswith("Format:"):
            format_pos = i
            break
    if format_pos < 0:
        return 0

    comments = _template_comments(base_style=base_style, accent_tag=accent_tag, primary_tag=primary_tag)
    lines[format_pos + 1 : format_pos + 1] = comments
    return len(comments)


def _check_subtitle_line(is_comment: bool, style: str, effect: str, handle_style: str) -> bool:
    if style != handle_style:
        return False
    if not is_comment:
        return effect in ("", "karaoke")
    return effect == "karaoke"


def _is_punctuation_text(text: str) -> bool:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    return all(unicodedata.category(c).startswith("P") for c in chars)


def _has_ruby_marker(text: str) -> bool:
    return "|<" in text or "｜＜" in text


def _split_k_text(text: str) -> List[Dict]:
    parts: List[Dict] = []
    pos = 0
    for m in K_TAG_PATTERN.finditer(text):
        if m.start() > pos:
            parts.append({"type": "raw", "text": text[pos : m.start()]})
        parts.append(
            {
                "type": "k",
                "tag": m.group(1),
                "duration": int(m.group(2)),
                "text": m.group(3),
                "drop": False,
            }
        )
        pos = m.end()
    if pos < len(text):
        parts.append({"type": "raw", "text": text[pos:]})
    return parts


def _render_k_text(parts: List[Dict]) -> str:
    out: List[str] = []
    for p in parts:
        if p["type"] == "raw":
            out.append(p["text"])
        elif not p.get("drop", False):
            out.append(f"{{\\{p['tag']}{p['duration']}}}{p['text']}")
    return "".join(out)


def _find_prev_timed(parts: List[Dict], idx: int) -> int:
    j = idx - 1
    while j >= 0:
        p = parts[j]
        if p["type"] == "k" and not p.get("drop", False) and p["duration"] > 0:
            return j
        j -= 1
    return -1


def _find_next_timed(parts: List[Dict], idx: int) -> int:
    j = idx + 1
    while j < len(parts):
        p = parts[j]
        if p["type"] == "k" and not p.get("drop", False) and p["duration"] > 0:
            return j
        j += 1
    return -1


def _normalize_zero_duration_k_parts(text: str) -> str:
    parts = _split_k_text(text)

    for i, p in enumerate(parts):
        if p["type"] != "k" or p.get("drop", False):
            continue
        if p["duration"] != 0 or not p["text"] or p["text"].isspace():
            continue

        is_punct = _is_punctuation_text(p["text"])
        prev_idx = _find_prev_timed(parts, i)
        next_idx = _find_next_timed(parts, i)

        if is_punct:
            # Keep punctuation in its own zero-duration syllable when it is
            # next to ruby text, otherwise punctuation may be rendered inside
            # furigana area after later k-tag normalization.
            prev_has_ruby = prev_idx >= 0 and _has_ruby_marker(parts[prev_idx]["text"])
            next_has_ruby = next_idx >= 0 and _has_ruby_marker(parts[next_idx]["text"])
            if prev_idx >= 0 and not (prev_has_ruby or next_has_ruby):
                parts[prev_idx]["text"] += p["text"]
                p["drop"] = True
            continue

        if not is_punct and next_idx >= 0 and parts[next_idx]["duration"] > 1:
            p["duration"] = 1
            parts[next_idx]["duration"] -= 1
            continue

        if not is_punct and prev_idx >= 0 and parts[prev_idx]["duration"] > 1:
            p["duration"] = 1
            parts[prev_idx]["duration"] -= 1
            continue

        if not is_punct and next_idx >= 0:
            prefix = p["text"]
            for j in range(i + 1, next_idx):
                mid = parts[j]
                if mid["type"] == "k" and not mid.get("drop", False) and mid["duration"] == 0:
                    prefix += mid["text"]
                    mid["drop"] = True
            parts[next_idx]["text"] = prefix + parts[next_idx]["text"]
            p["drop"] = True
            continue

        if prev_idx >= 0:
            parts[prev_idx]["text"] += p["text"]
            p["drop"] = True
        elif next_idx >= 0:
            parts[next_idx]["text"] = p["text"] + parts[next_idx]["text"]
            p["drop"] = True

    return _render_k_text(parts)


def apply_kara_style_to_ass(
    ass_path: str,
    handle_style: str = "Default",
    advance_time_ms: int = 100,
    sep_threshold_ms: int = 200000,
    insert_template_comments: bool = True,
    template_comment_style: str = "K1",
    kara_primary_color: str = DEFAULT_KARA_PRIMARY_COLOR,
    kara_accent_color: str = DEFAULT_KARA_ACCENT_COLOR,
) -> Dict[str, int]:
    primary_color = normalize_kara_color(kara_primary_color, "kara_primary_color")
    accent_color = normalize_kara_color(kara_accent_color, "kara_accent_color")
    validate_distinct_kara_colors(primary_color, accent_color)

    primary_tag = _ass_color_to_tag_color(primary_color)
    accent_tag = _ass_color_to_tag_color(accent_color)

    with open(ass_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    event_start, event_end = _find_section_bounds(lines, "[Events]")
    if event_start < 0:
        return {
            "styled_lines": 0,
            "max_style_index": 0,
            "inserted_comments": 0,
        }

    layers: List[Dict[str, int]] = []
    styled_lines = 0
    max_style_index = 0

    for i in range(event_start + 1, event_end):
        raw = lines[i]
        if not raw.startswith("Dialogue:"):
            continue

        payload = raw[len("Dialogue: ") :].rstrip("\n")
        fields = payload.split(",", 9)
        if len(fields) < 10:
            continue

        effect = fields[8]
        style = fields[3]
        if not _check_subtitle_line(False, style, effect, handle_style):
            continue

        fields[9] = _normalize_zero_duration_k_parts(fields[9])

        start_ms = _parse_ass_time_to_ms(fields[1])
        end_ms = _parse_ass_time_to_ms(fields[2])

        layer_i = 0
        while True:
            if layer_i >= len(layers):
                layers.append(
                    {
                        "last_start_anchor": start_ms,
                        "last_end": start_ms,
                        "has_line": 0,
                        "next_style_offset": 0,
                    }
                )

            cur_layer = layers[layer_i]
            if start_ms >= cur_layer["last_end"]:
                prev_start = cur_layer["last_start_anchor"]
                prev_end = cur_layer["last_end"]
                is_new_sequence = (cur_layer["has_line"] == 0) or (start_ms - prev_end > sep_threshold_ms)
                base_style = 1 + 2 * layer_i
                if is_new_sequence:
                    style_i = base_style
                    line_event_start = max(0, start_ms - advance_time_ms)
                    next_style_offset = 1
                else:
                    style_i = base_style + cur_layer["next_style_offset"]
                    line_event_start = max(0, prev_start)
                    next_style_offset = 1 - cur_layer["next_style_offset"]

                layers[layer_i] = {
                    "last_start_anchor": start_ms,
                    "last_end": end_ms,
                    "has_line": 1,
                    "next_style_offset": next_style_offset,
                }
                break

            layer_i += 1

        k_lead = math.floor((start_ms - line_event_start) / 10)
        fields[9] = f"{{\\k{k_lead}}}{fields[9]}"
        fields[1] = _format_ass_time_from_ms(line_event_start)
        fields[3] = f"K{style_i}"

        lines[i] = "Dialogue: " + ",".join(fields) + "\n"
        styled_lines += 1
        max_style_index = max(max_style_index, style_i)

    _ensure_k_styles(lines, max_style_index, primary_color=primary_color, accent_color=accent_color)

    inserted_comments = 0
    if insert_template_comments:
        inserted_comments = _ensure_template_comments(
            lines,
            base_style=template_comment_style,
            accent_tag=accent_tag,
            primary_tag=primary_tag,
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return {
        "styled_lines": styled_lines,
        "max_style_index": max_style_index,
        "inserted_comments": inserted_comments,
    }


def _run_self_tests() -> None:
    assert normalize_kara_color("&h00ffffff", "x") == "&H00FFFFFF"
    assert normalize_kara_color("white", "x") == "&H00FFFFFF"
    assert normalize_kara_color("red", "x") == "&H000000FF"
    try:
        validate_distinct_kara_colors("&H00FFFFFF", "&H11FFFFFF")
        raise AssertionError("Expected distinct color validation to fail")
    except ValueError:
        pass

    s1 = r"{\k10}{\kf0} {\kf46}Go{\kf0}!{\kf42}{\kf0} {\kf8}Go{\kf0}!"
    n1 = _normalize_zero_duration_k_parts(s1)
    assert "{\\kf46}Go!" in n1

    s2 = r"{\k10}{\kf4}Wow{\kf0}!{\kf0} {\kf0}Shout{\kf0} {\kf36}it"
    n2 = _normalize_zero_duration_k_parts(s2)
    assert "{\\kf1}Shout" in n2

    s3 = r"{\kf0}「{\kf24}東|<とう{\kf24}京|<きょう{\kf0}」"
    n3 = _normalize_zero_duration_k_parts(s3)
    assert "{\\kf0}「" in n3
    assert "{\\kf0}」" in n3

    sample = """[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,{\\kf50}A{\\kf50}B\n"""

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass", delete=True) as tmp:
        tmp.write(sample)
        tmp.flush()
        stats = apply_kara_style_to_ass(
            tmp.name,
            kara_primary_color="&H00FFFFFF",
            kara_accent_color="&H002F1CD8",
        )
        assert stats["styled_lines"] == 1
        tmp.seek(0)
        content = tmp.read()
        assert "Style: K1,Noto Serif JP Black,80,&H00FFFFFF,&H002F1CD8" in content
        assert "\\1c&H2F1CD8&\\3c&HFFFFFF&" in content

    zero_start_sample = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.60,Default,,0,0,0,,{\\kf30}A{\\kf30}B
"""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass", delete=True) as tmp:
        tmp.write(zero_start_sample)
        tmp.flush()
        apply_kara_style_to_ass(tmp.name, insert_template_comments=False)
        with open(tmp.name, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.startswith("Dialogue:")]
        fields = lines[0].split(",", 9)
        assert fields[1] == "0:00:00.00"
        assert fields[3] == "K1"
        assert fields[9].startswith("{\\k0}")

    sequence_sample = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:01.40,Default,,0,0,0,,{\k10}A
Dialogue: 0,0:00:01.45,0:00:01.90,Default,,0,0,0,,{\k10}B
Dialogue: 0,0:00:01.95,0:00:02.30,Default,,0,0,0,,{\k10}C
"""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass", delete=True) as tmp:
        tmp.write(sequence_sample)
        tmp.flush()
        apply_kara_style_to_ass(tmp.name, insert_template_comments=False)
        with open(tmp.name, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.startswith("Dialogue:")]
        starts = [line.split(",", 9)[1] for line in lines]
        styles = [line.split(",", 9)[3] for line in lines]
        assert starts[0] == "0:00:00.90"
        assert starts[1] == "0:00:01.00"
        assert starts[2] == "0:00:01.45"
        assert styles == ["K1", "K2", "K1"]

    threshold_break_sample = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:01.40,Default,,0,0,0,,{\\k10}A
Dialogue: 0,0:00:06.00,0:00:06.40,Default,,0,0,0,,{\\k10}B
"""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass", delete=True) as tmp:
        tmp.write(threshold_break_sample)
        tmp.flush()
        apply_kara_style_to_ass(tmp.name, insert_template_comments=False, sep_threshold_ms=1000)
        with open(tmp.name, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.startswith("Dialogue:")]
        starts = [line.split(",", 9)[1] for line in lines]
        styles = [line.split(",", 9)[3] for line in lines]
        assert starts[0] == "0:00:00.90"
        assert starts[1] == "0:00:05.90"
        assert styles == ["K1", "K1"]

    # Sequence check must use previous end -> next start gap, not previous start -> next start.
    end_gap_sample = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:05.80,Default,,0,0,0,,{\\k10}A
Dialogue: 0,0:00:06.00,0:00:06.40,Default,,0,0,0,,{\\k10}B
"""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass", delete=True) as tmp:
        tmp.write(end_gap_sample)
        tmp.flush()
        apply_kara_style_to_ass(tmp.name, insert_template_comments=False, sep_threshold_ms=1000)
        with open(tmp.name, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.startswith("Dialogue:")]
        starts = [line.split(",", 9)[1] for line in lines]
        styles = [line.split(",", 9)[3] for line in lines]
        assert starts[0] == "0:00:00.90"
        # Gap from previous end is 200ms <= 1000ms, so this stays in the same sequence.
        assert starts[1] == "0:00:01.00"
        assert styles == ["K1", "K2"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Ruby_Kara style layering to ASS subtitles")
    parser.add_argument("-i", "--input", help="Input ASS file path")
    parser.add_argument("--handle-style", default="Default", help="Original style name to process")
    parser.add_argument("--advance-time-ms", type=int, default=100, help="Advance time in milliseconds")
    parser.add_argument("--sep-threshold-ms", type=int, default=200000, help="Gap threshold in milliseconds")
    parser.add_argument("--no-template-comments", action="store_true", help="Do not inject the 6 template comment lines")
    parser.add_argument("--template-comment-style", default="K1", help="Style name used by injected template comments")
    parser.add_argument(
        "--kara-primary-color",
        default=DEFAULT_KARA_PRIMARY_COLOR,
        help="K-style primary color (ASS code or name like white/red/blue)",
    )
    parser.add_argument(
        "--kara-accent-color",
        default=DEFAULT_KARA_ACCENT_COLOR,
        help="K-style accent color (ASS code or name like white/red/blue)",
    )
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
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
