"""Render and validate the local web application's input form."""

from __future__ import annotations

import html
import os
import re
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from ai_karaoke.application.pipeline import (
    PipelineConfig,
    SongRequest,
    build_song_requests,
    discover_audio_paths,
)
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

FURIGANA_MODEL_LABELS = {
    "tiny": "tiny · 最快",
    "base": "base · 较快",
    "small": "small · 均衡",
    "medium": "medium · 较精细",
    "large-v3": "large-v3 · 精度优先（推荐）",
}
HTML_PATH = Path(__file__).resolve().parent / "static" / "index.html"
PICKER_LOCK = threading.Lock()


def clean_input_path(raw: str) -> str:
    value = raw.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    if value.lower().startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path or "")
        if os.name == "nt":
            if re.match(r"^/[A-Za-z]:/", value):
                value = value[1:]
            value = value.replace("/", "\\")
    value = os.path.expandvars(os.path.expanduser(value))
    return os.path.normpath(value) if value else ""


def _split_path_lines(raw: str) -> list[str]:
    return [path for line in raw.splitlines() if (path := clean_input_path(line))]


def open_native_picker(field: str) -> list[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError(f"无法打开文件选择器: {exc}") from exc

    with PICKER_LOCK:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            if field == "songs":
                return list(filedialog.askopenfilenames(
                    title="选择一首或多首歌曲",
                    filetypes=[
                        ("Audio Files", "*.flac *.wav *.mp3 *.m4a *.aac *.ogg *.opus *.wma"),
                        ("All Files", "*.*"),
                    ],
                ))
            if field == "lyric":
                selected = filedialog.askopenfilename(
                    title="选择歌词文件",
                    filetypes=[("LRC Files", "*.lrc *.LRC"), ("All Files", "*.*")],
                )
                return [selected] if selected else []
            if field in ("input_dir", "output"):
                title = "选择歌曲目录" if field == "input_dir" else "选择输出目录"
                selected = filedialog.askdirectory(title=title)
                return [selected] if selected else []
            raise ValueError(f"不支持的选择字段: {field}")
        finally:
            root.destroy()


def render_form_page() -> str:
    template = HTML_PATH.read_text(encoding="utf-8")
    model_options = []
    for name in FURIGANA_WHISPER_MODELS:
        selected = " selected" if name == DEFAULT_FURIGANA_WHISPER_MODEL else ""
        model_options.append(
            f'<option value="{name}"{selected}>{FURIGANA_MODEL_LABELS[name]}</option>'
        )
    replacements = {
        "{{FURIGANA_MODEL_OPTIONS}}": "\n".join(model_options),
        "{{DEFAULT_ADVANCE_MS}}": str(DEFAULT_KARA_ADVANCE_MS),
        "{{DEFAULT_SEP_THRESHOLD_MS}}": str(DEFAULT_KARA_SEP_THRESHOLD_MS),
        "{{DEFAULT_PRIMARY_COLOR}}": html.escape(DEFAULT_KARA_PRIMARY_COLOR),
        "{{DEFAULT_ACCENT_COLOR}}": html.escape(DEFAULT_KARA_ACCENT_COLOR),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def _parse_non_negative_int(form: dict[str, str], name: str, default: int) -> int:
    raw = form.get(name, str(default)).strip()
    if not re.fullmatch(r"\d+", raw):
        raise ValueError(f"{name} 必须是非负整数")
    return int(raw)


def parse_submission(form: dict[str, str]) -> tuple[list[SongRequest], str, PipelineConfig]:
    explicit_paths = _split_path_lines(form.get("song_paths", ""))
    input_dir = clean_input_path(form.get("input_dir", "")) or None
    output = clean_input_path(form.get("output", ""))
    lyric = clean_input_path(form.get("lyric", "")) or None
    if not output:
        raise ValueError("请选择输出目录")
    audio_paths = discover_audio_paths(explicit_paths, input_dir)
    requests = build_song_requests(audio_paths, lyric)
    config = PipelineConfig(
        lang=form.get("lang", "ja").strip() or "ja",
        ctc_batch_size=_parse_non_negative_int(form, "ctc_batch_size", 1),
        ctc_number_reading=form.get("ctc_number_reading", "auto").strip(),
        use_whisper_readings=form.get("whisper_readings", "") == "on",
        furigana_whisper_model=form.get(
            "furigana_whisper_model", DEFAULT_FURIGANA_WHISPER_MODEL
        ).strip() or DEFAULT_FURIGANA_WHISPER_MODEL,
        kara_advance_ms=_parse_non_negative_int(
            form, "kara_advance_ms", DEFAULT_KARA_ADVANCE_MS
        ),
        kara_sep_threshold_ms=_parse_non_negative_int(
            form, "kara_sep_threshold_ms", DEFAULT_KARA_SEP_THRESHOLD_MS
        ),
        kara_primary_color=form.get("kara_primary_color", DEFAULT_KARA_PRIMARY_COLOR),
        kara_accent_color=form.get("kara_accent_color", DEFAULT_KARA_ACCENT_COLOR),
    )
    return requests, os.path.abspath(output), config
