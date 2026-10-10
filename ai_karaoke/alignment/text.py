"""Convert display lyrics into units understood by the karaoke CTC model."""

from __future__ import annotations

import re
import unicodedata

from ai_karaoke.alignment.models import AlignmentUnit
from ai_karaoke.pronunciation.reading import JapaneseReadingConverter, contains_kanji

NUMBER_READING_MODES = ("auto", "ja", "en")
JAPANESE_DIGITS = {
    "0": "ぜろ", "1": "いち", "2": "に", "3": "さん",
    "4": "よん", "5": "ご", "6": "ろく", "7": "なな",
    "8": "はち", "9": "きゅう",
}
ENGLISH_DIGITS = {
    "0": "zero", "1": "one", "2": "two", "3": "three",
    "4": "four", "5": "five", "6": "six", "7": "seven",
    "8": "eight", "9": "nine",
}
KATAKANA_COMPATIBILITY_READINGS = {
    **dict(zip(
        "ㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ",
        "クシストヌハヒフヘホムラリルレロ",
    )),
    "ヷ": "ヴァ",
    "ヸ": "ヴィ",
    "ヹ": "ヴェ",
    "ヺ": "ヴォ",
}
KANA_ITERATION_MARKS = "ゝゞヽヾ"
KANA_MODIFIERS = "っッーｰ" + KANA_ITERATION_MARKS


def _is_kana_letter(char: str) -> bool:
    """Return whether *char* is a kana letter, excluding block punctuation."""
    code = ord(char)
    return (
        0x3041 <= code <= 0x3096
        or code == 0x309F
        or 0x30A1 <= code <= 0x30FA
        or code == 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9D
    )


def _is_kana_component(char: str) -> bool:
    return (
        _is_kana_letter(char)
        or char in KANA_MODIFIERS
        or char in "ﾞﾟ"
        or unicodedata.combining(char) != 0
    )


def _is_decoration(text: str) -> bool:
    """Return whether text contains no letter or number meant to be sung."""
    return bool(text) and not any(
        unicodedata.category(char)[0] in "LN"
        and char not in "ーｰﾞﾟ" + KANA_ITERATION_MARKS
        for char in text
    )


def _text_clusters(text: str) -> list[str]:
    """Group combining and halfwidth sound marks with their base character."""
    clusters: list[str] = []
    for char in text:
        if clusters and (unicodedata.combining(char) or char in "ﾞﾟ"):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def _normalize_kana(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    expanded = "".join(
        KATAKANA_COMPATIBILITY_READINGS.get(char, char) for char in normalized
    )
    return unicodedata.normalize("NFC", expanded)


def _repeat_kana(previous: str, mark: str) -> str:
    if not previous:
        return ""
    base = "".join(
        char for char in unicodedata.normalize("NFD", previous[-1])
        if not unicodedata.combining(char)
    )
    if not base:
        return ""
    if mark in "ゞヾ":
        return unicodedata.normalize("NFC", base[-1] + "\u3099")
    return base[-1]


def _split_kana_mora(text: str) -> list[AlignmentUnit]:
    small = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮヵヶ")
    morae: list[list[str]] = []
    pending_display = ""
    pending_reading = ""
    for display in _text_clusters(text):
        reading = _normalize_kana(display)
        if reading.isspace() or (_is_decoration(reading) and reading not in KANA_MODIFIERS):
            continue
        if reading in "っッ":
            pending_display += display
            pending_reading += reading
            continue
        if reading in KANA_ITERATION_MARKS:
            previous = morae[-1][1] if morae else pending_reading
            repeated = _repeat_kana(previous, reading)
            if repeated:
                morae.append([pending_display + display, pending_reading + repeated])
            pending_display = pending_reading = ""
            continue
        if (reading in small or reading == "ー") and morae:
            morae[-1][0] += display
            morae[-1][1] += reading
            continue
        if any(_is_kana_letter(char) for char in reading):
            morae.append([pending_display + display, pending_reading + reading])
            pending_display = pending_reading = ""
    if pending_reading:
        morae.append([pending_display, pending_reading])
    return [AlignmentUnit(display, reading) for display, reading in morae if reading]


def _ascii_digit(digit: str) -> str | None:
    try:
        return str(unicodedata.digit(digit))
    except (TypeError, ValueError):
        return None


def _digit_reading(digit: str, number_reading: str) -> str:
    table = ENGLISH_DIGITS if number_reading == "en" else JAPANESE_DIGITS
    value = _ascii_digit(digit)
    if value is None or value not in table:
        raise ValueError(f"Unsupported numeric lyric character: {digit!r}")
    return table[value]


def _split_kanji_reading(original: str, reading: str) -> list[AlignmentUnit]:
    if len(original) <= 1 or not all(contains_kanji(char) for char in original):
        return [AlignmentUnit(original, reading or original)]
    # Lexical readings cannot generally be assigned safely to individual kanji.
    return [AlignmentUnit(original, reading or original)]


def _expand_reading_unit(
    original: str,
    reading: str,
    number_reading: str = "ja",
) -> list[AlignmentUnit]:
    # Sudachi may emit empty analysis nodes beside punctuation such as an
    # ellipsis. They do not represent lyric text and must never enter CTC.
    if not original:
        return []
    if contains_kanji(original):
        return _split_kanji_reading(original, reading)
    if original and all(_is_kana_component(char) or char.isspace() for char in original):
        return _split_kana_mora(original)
    if any(char.isdigit() for char in original):
        return [
            AlignmentUnit(char, _digit_reading(char, number_reading))
            for char in original
            if char.isdigit()
        ]
    normalized = unicodedata.normalize("NFKC", original)
    if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)*", normalized):
        return [AlignmentUnit(original, normalized)]
    if _is_decoration(original):
        return []
    # Keep unsupported letters visible in the transcript validation error.
    # Silently dropping them would produce a plausible but incorrect alignment.
    return [AlignmentUnit(original, reading or original)]


def resolve_number_reading(text: str, mode: str) -> str:
    if mode not in NUMBER_READING_MODES:
        raise ValueError(f"number_reading must be one of {NUMBER_READING_MODES}, got {mode!r}")
    if mode != "auto":
        return mode
    normalized = unicodedata.normalize("NFKC", text)
    has_latin = any("A" <= char.upper() <= "Z" for char in normalized)
    has_kana = any(_is_kana_letter(char) for char in normalized)
    return "en" if has_latin and not has_kana else "ja"


def build_alignment_units(
    text: str,
    readings: dict[str, str] | None = None,
    number_reading: str = "auto",
) -> list[AlignmentUnit]:
    resolved_number_reading = resolve_number_reading(text, number_reading)
    converter = JapaneseReadingConverter(readings)
    units: list[AlignmentUnit] = []
    for item in converter.convert(text):
        units.extend(
            _expand_reading_unit(
                item.get("orig", ""),
                item.get("hira", ""),
                resolved_number_reading,
            )
        )
    return units


def romanize_pronunciation(text: str, number_reading: str = "auto") -> str:
    from pykakasi import kakasi

    text = unicodedata.normalize("NFKC", text)
    resolved_number_reading = resolve_number_reading(text, number_reading)
    if any(char.isdigit() for char in text):
        table = ENGLISH_DIGITS if resolved_number_reading == "en" else JAPANESE_DIGITS
        text = "".join(table.get(_ascii_digit(char) or "", char) for char in text)
    converted = kakasi().convert(text)
    romanized = "".join(item.get("hepburn", "") for item in converted).casefold()
    romanized = unicodedata.normalize("NFKD", romanized).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z']", "", romanized)
