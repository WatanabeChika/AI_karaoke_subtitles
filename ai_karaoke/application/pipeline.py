"""Shared song-processing contract for the CLI and local web application."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ai_karaoke.alignment.text import NUMBER_READING_MODES
from ai_karaoke.application.generator import generate_karaoke_ass
from ai_karaoke.audio.separation import separate_audio
from ai_karaoke.interfaces.console import ConsoleReporter, ProgressReporter
from ai_karaoke.pronunciation.whisper import (
    DEFAULT_FURIGANA_WHISPER_MODEL,
    validate_furigana_whisper_model,
)
from ai_karaoke.subtitles.colors import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
    normalize_kara_color,
    validate_distinct_kara_colors,
)
from ai_karaoke.subtitles.karaoke_tags import (
    DEFAULT_KARA_ADVANCE_MS,
    DEFAULT_KARA_SEP_THRESHOLD_MS,
)

AUDIO_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"})
@dataclass(frozen=True)
class PipelineConfig:
    lang: str = "ja"
    ctc_batch_size: int = 1
    ctc_number_reading: str = "auto"
    use_whisper_readings: bool = True
    furigana_whisper_model: str = DEFAULT_FURIGANA_WHISPER_MODEL
    kara_advance_ms: int = DEFAULT_KARA_ADVANCE_MS
    kara_sep_threshold_ms: int = DEFAULT_KARA_SEP_THRESHOLD_MS
    kara_primary_color: str = DEFAULT_KARA_PRIMARY_COLOR
    kara_accent_color: str = DEFAULT_KARA_ACCENT_COLOR

    def __post_init__(self) -> None:
        if not self.lang.strip():
            raise ValueError("Song language cannot be empty")
        if self.ctc_batch_size < 1:
            raise ValueError("CTC batch size must be at least 1")
        if self.ctc_number_reading not in NUMBER_READING_MODES:
            raise ValueError(f"Unknown CTC number reading mode: {self.ctc_number_reading}")
        validate_furigana_whisper_model(self.furigana_whisper_model)
        if self.kara_advance_ms < 0 or self.kara_sep_threshold_ms < 0:
            raise ValueError("Karaoke timing values cannot be negative")

        primary = normalize_kara_color(self.kara_primary_color, "kara_primary_color")
        accent = normalize_kara_color(self.kara_accent_color, "kara_accent_color")
        validate_distinct_kara_colors(primary, accent)
        object.__setattr__(self, "kara_primary_color", primary)
        object.__setattr__(self, "kara_accent_color", accent)


@dataclass(frozen=True)
class SongRequest:
    audio_path: str
    lyric_path: str | None = None

    @property
    def base_name(self) -> str:
        return os.path.splitext(os.path.basename(self.audio_path))[0]


@dataclass(frozen=True)
class SongArtifacts:
    title: str
    source_audio_path: str
    lyric_path: str
    output_dir: str
    vocal_path: str
    accompaniment_path: str
    ass_path: str
    alignment_report_path: str
    pronunciation_report_path: str | None


def discover_audio_paths(explicit_paths: Iterable[str], input_dir: str | None = None) -> list[str]:
    candidates = [path for path in explicit_paths if path]
    if input_dir:
        directory = Path(input_dir).expanduser()
        if not directory.is_dir():
            raise NotADirectoryError(f"Input directory does not exist: {directory}")
        candidates.extend(
            str(path) for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold())
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        )

    unique: list[str] = []
    seen: set[str] = set()
    for raw_path in candidates:
        path = os.path.abspath(os.path.expanduser(raw_path))
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    if not unique:
        raise ValueError("No input audio files were provided or found")

    missing = [path for path in unique if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError(f"Input audio does not exist: {missing[0]}")

    duplicate_names: dict[str, list[str]] = {}
    for path in unique:
        name = os.path.splitext(os.path.basename(path))[0]
        duplicate_names.setdefault(os.path.normcase(name), []).append(path)
    collisions = [paths for paths in duplicate_names.values() if len(paths) > 1]
    if collisions:
        joined = ", ".join(collisions[0])
        raise ValueError(f"Batch contains duplicate song names that would share an output folder: {joined}")
    return unique


def build_song_requests(audio_paths: list[str], lyric_path: str | None = None) -> list[SongRequest]:
    if lyric_path and len(audio_paths) != 1:
        raise ValueError("--lyric can only be used with one input song; batch jobs use same-name LRC files")
    resolved_lyric = os.path.abspath(os.path.expanduser(lyric_path)) if lyric_path else None
    if resolved_lyric and not os.path.isfile(resolved_lyric):
        raise FileNotFoundError(f"Lyrics do not exist: {resolved_lyric}")
    return [SongRequest(path, resolved_lyric) for path in audio_paths]


def resolve_lyric_path(request: SongRequest) -> str:
    if request.lyric_path:
        return request.lyric_path
    song_dir = os.path.dirname(request.audio_path)
    for extension in (".lrc", ".LRC"):
        candidate = os.path.join(song_dir, f"{request.base_name}{extension}")
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        f"No lyrics for {request.base_name!r}; place a same-name .lrc file next to the audio"
    )


def process_song(
    request: SongRequest,
    output_path: str,
    config: PipelineConfig,
    reporter: ProgressReporter | None = None,
) -> SongArtifacts:
    reporter = reporter or ConsoleReporter()
    output_path = os.path.abspath(os.path.expanduser(output_path))
    os.makedirs(output_path, exist_ok=True)
    lyric_path = resolve_lyric_path(request)
    reporter.info(f"Lyrics: {lyric_path}")

    vocal_path, accompaniment_path = separate_audio(
        audio_path=request.audio_path,
        output_path=output_path,
        base_name=request.base_name,
        reporter=reporter,
    )
    ass_path = generate_karaoke_ass(
        vocal_path=vocal_path,
        lrc_path=lyric_path,
        output_path=output_path,
        base_name=request.base_name,
        lang=config.lang,
        ctc_batch_size=config.ctc_batch_size,
        ctc_number_reading=config.ctc_number_reading,
        use_whisper_readings=config.use_whisper_readings,
        furigana_whisper_model=config.furigana_whisper_model,
        kara_advance_time_ms=config.kara_advance_ms,
        kara_sep_threshold_ms=config.kara_sep_threshold_ms,
        kara_primary_color=config.kara_primary_color,
        kara_accent_color=config.kara_accent_color,
        reporter=reporter,
    )

    output_dir = os.path.dirname(ass_path)
    pronunciation_report = os.path.join(output_dir, f"{request.base_name}_pronunciation_report.json")
    return SongArtifacts(
        title=request.base_name,
        source_audio_path=request.audio_path,
        lyric_path=lyric_path,
        output_dir=output_dir,
        vocal_path=vocal_path,
        accompaniment_path=accompaniment_path,
        ass_path=ass_path,
        alignment_report_path=os.path.join(output_dir, f"{request.base_name}_alignment_report.json"),
        pronunciation_report_path=pronunciation_report if os.path.isfile(pronunciation_report) else None,
    )
