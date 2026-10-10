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


def _is_kana(char: str) -> bool:
    return "\u3040" <= char <= "\u30ff" or "\u31f0" <= char <= "\u31ff"


def _split_kana_mora(text: str) -> list[str]:
    small = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮヵヶ")
    morae: list[str] = []
    pending_sokuon = ""
    for char in text:
        if char in "っッ":
            pending_sokuon += char
        elif char in small or char == "ー" or unicodedata.combining(char):
            if morae:
                morae[-1] += char
            elif pending_sokuon:
                pending_sokuon += char
            else:
                morae.append(char)
        elif _is_kana(char):
            morae.append(pending_sokuon + char)
            pending_sokuon = ""
        elif pending_sokuon:
            morae.append(pending_sokuon)
            pending_sokuon = ""
    if pending_sokuon:
        morae.append(pending_sokuon)
    return morae


def _digit_reading(digit: str, number_reading: str) -> str:
    table = ENGLISH_DIGITS if number_reading == "en" else JAPANESE_DIGITS
    return table[digit]


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
    if contains_kanji(original):
        return _split_kanji_reading(original, reading)
    if original and all(_is_kana(char) or char.isspace() for char in original):
        return [AlignmentUnit(mora, mora) for mora in _split_kana_mora(original)]
    if any(char.isdigit() for char in original):
        return [
            AlignmentUnit(char, _digit_reading(char, number_reading))
            for char in original
            if char.isdigit()
        ]
    return [
        AlignmentUnit(match.group(), match.group())
        for match in re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)*", original)
    ]


def resolve_number_reading(text: str, mode: str) -> str:
    if mode not in NUMBER_READING_MODES:
        raise ValueError(f"number_reading must be one of {NUMBER_READING_MODES}, got {mode!r}")
    if mode != "auto":
        return mode
    has_latin = any("A" <= char.upper() <= "Z" for char in text)
    has_kana = any(_is_kana(char) for char in text)
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

    resolved_number_reading = resolve_number_reading(text, number_reading)
    if any(char.isdigit() for char in text):
        table = ENGLISH_DIGITS if resolved_number_reading == "en" else JAPANESE_DIGITS
        text = "".join(table.get(char, char) for char in text)
    converted = kakasi().convert(text)
    romanized = "".join(item.get("hepburn", "") for item in converted).casefold()
    romanized = unicodedata.normalize("NFKD", romanized).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z']", "", romanized)
