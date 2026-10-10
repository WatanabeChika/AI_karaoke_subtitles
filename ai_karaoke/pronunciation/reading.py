"""Japanese text normalization and context-sensitive dictionary readings."""

from __future__ import annotations

import re
import unicodedata
import warnings


def _is_japanese_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0x4E00 <= code <= 0x9FFF
        or ch in "々〆〇ヶー"
    )


def contains_kanji(text: str) -> bool:
    return any(0x4E00 <= ord(ch) <= 0x9FFF or ch in "々〆〇ヶ" for ch in text)


def is_pronounceable_japanese_segment(text: str) -> bool:
    trimmed = [ch for ch in text if not ch.isspace()]
    return bool(trimmed) and all(_is_japanese_char(ch) for ch in trimmed)


def katakana_to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
        for char in text
    )


def speech_text(text: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKC", text)
        if unicodedata.category(ch)[0] in "LN"
    )


def merge_readings(
    baseline: dict[str, str], overrides: dict[str, str]
) -> dict[str, str]:
    """Apply higher-priority readings even across different word boundaries."""
    retained = {
        base: reading
        for base, reading in baseline.items()
        if not any(base in key or key in base for key in overrides)
    }
    return {**retained, **overrides}


class JapaneseReadingConverter:
    def __init__(self, readings=None):
        from kanjiconv import KanjiConv

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Dictionary\.create\(\) is deprecated, use Dictionary\.tokenizer\(\) instead",
                category=DeprecationWarning,
            )
            self.converter = KanjiConv(separator="", use_custom_readings=True)
        self.readings = readings or {}

    def convert(self, text):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Dictionary\.create\(\) is deprecated, use Dictionary\.tokenizer\(\) instead",
                category=DeprecationWarning,
            )
            return self._convert(text)

    def _convert(self, text):
        custom_compounds = (
            self.converter.custom_readings.get("compound", {})
            if self.converter.use_custom_readings
            else {}
        )
        keys = sorted(self.readings, key=len, reverse=True)
        sections = re.split(f"({'|'.join(map(re.escape, keys))})", text) if keys else [text]
        result = []
        for section in sections:
            if not section:
                continue
            if section in self.readings:
                result.append({"orig": section, "hira": self.readings[section]})
                continue
            for word in self.converter.tokenizer.tokenize(section):
                surface = word.surface()
                if not contains_kanji(surface):
                    result.append({"orig": surface, "hira": surface})
                    continue
                # Sudachi preserves lexical context for forms such as 奏で and 未だ.
                raw_reading = word.reading_form() or surface
                if surface in custom_compounds or raw_reading == surface:
                    raw_reading = self.converter.to_hiragana(surface)
                reading = katakana_to_hiragana(raw_reading)
                suffix = re.search(r"[ぁ-ゖァ-ヿー]+$", surface)
                if suffix:
                    tail = suffix.group()
                    hira_tail = katakana_to_hiragana(tail)
                    if reading.endswith(hira_tail) and len(surface) > len(tail):
                        result.append({"orig": surface[:-len(tail)], "hira": reading[:-len(hira_tail)]})
                        result.append({"orig": tail, "hira": tail})
                        continue
                result.append({"orig": surface, "hira": reading})
        return result


def build_converter(readings=None) -> JapaneseReadingConverter:
    try:
        return JapaneseReadingConverter(readings)
    except ImportError as exc:
        raise RuntimeError(
            "Missing kanjiconv dependency. Install with: pip install kanjiconv"
        ) from exc


def reading_text(text: str, converter=None) -> str:
    converter = converter or JapaneseReadingConverter()
    reading = "".join(item["hira"] for item in converter.convert(speech_text(text)))
    return katakana_to_hiragana("".join(reading.split()))
