"""Create K1/K2 ASS styles and optional Aegisub template comments."""

from __future__ import annotations

from ai_karaoke.subtitles.document import find_section_bounds


def _template_comments(base_style: str, accent_tag: str, primary_tag: str) -> list[str]:
    return [
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},,0,0,0,code syl all,fxgroup.kara=syl.inline_fx==""\n',
        f'Comment: 1,0:00:00.00,0:00:00.00,{base_style},overlay,0,0,0,template syl noblank all fxgroup kara,!retime("line",0,0)!{{\\pos($center,$middle)\\an5\\shad0\\1c{accent_tag}\\3c{primary_tag}\\clip(!$sleft-3!,0,!$sleft-3!,1080)\\t($sstart,$send,\\clip(!$sleft-3!,0,!$sright+3!,1080))\\bord5}}\n',
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},,0,0,0,template syl all fxgroup kara,!retime("line",0,0)!{{\\pos($center,$middle)\\an5}}\n',
        f'Comment: 1,0:00:18.65,0:00:20.65,{base_style},overlay,0,0,0,template furi all,!retime("line",0,0)!{{\\pos($center,!$middle+10!)\\an5\\shad0\\1c{accent_tag}\\3c{primary_tag}\\clip(!$sleft-3!,0,!$sleft-3!,1080)\\t($sstart,$send,\\clip(!$sleft-3!,0,!$sright+3!,1080))\\bord5}}\n',
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},,0,0,0,template furi all,!retime("line",0,0)!{{\\pos($center,!$middle+10!)\\an5}}\n',
        f'Comment: 0,0:00:00.00,0:00:00.00,{base_style},music,0,0,0,template fx no_k,!retime("line",0,0)!{{\\pos($center,!$middle!)\\an5\\1c&H505050&\\3c&HFFFFFFF&}}\n',
    ]


def _make_style_line(
    style_name: str,
    odd_template: bool,
    primary_color: str,
    accent_color: str,
) -> str:
    if odd_template:
        alignment, margin_left, margin_right, margin_vertical = 1, 120, 30, 220
    else:
        alignment, margin_left, margin_right, margin_vertical = 3, 30, 120, 40
    return (
        f"Style: {style_name},Noto Serif JP Black,80,{primary_color},{accent_color},"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,{alignment},"
        f"{margin_left},{margin_right},{margin_vertical},1\n"
    )


def ensure_k_styles(
    lines: list[str],
    max_style_index: int,
    primary_color: str,
    accent_color: str,
) -> None:
    if max_style_index <= 0:
        return
    style_start, style_end = find_section_bounds(lines, "[V4+ Styles]")
    if style_start < 0:
        return
    existing_indices: dict[str, int] = {}
    insert_position = style_end
    for index in range(style_start + 1, style_end):
        if not lines[index].startswith("Style: "):
            continue
        insert_position = index + 1
        name = lines[index][len("Style: ") :].strip().split(",", 1)[0]
        existing_indices[name] = index
    additions: list[str] = []
    for style_index in range(1, max_style_index + 1):
        name = f"K{style_index}"
        line = _make_style_line(
            name,
            odd_template=style_index % 2 == 1,
            primary_color=primary_color,
            accent_color=accent_color,
        )
        if name in existing_indices:
            lines[existing_indices[name]] = line
        else:
            additions.append(line)
    if additions:
        lines[insert_position:insert_position] = additions


def ensure_template_comments(
    lines: list[str],
    base_style: str,
    accent_tag: str,
    primary_tag: str,
) -> int:
    event_start, event_end = find_section_bounds(lines, "[Events]")
    if event_start < 0:
        return 0
    marker = 'code syl all,fxgroup.kara=syl.inline_fx==""'
    if any(marker in lines[index] for index in range(event_start + 1, event_end)):
        return 0
    format_position = next(
        (
            index
            for index in range(event_start + 1, event_end)
            if lines[index].startswith("Format:")
        ),
        -1,
    )
    if format_position < 0:
        return 0
    comments = _template_comments(base_style, accent_tag, primary_tag)
    lines[format_position + 1 : format_position + 1] = comments
    return len(comments)
