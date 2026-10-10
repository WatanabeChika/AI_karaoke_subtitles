"""Match uniquely heard kanji words across compatible spellings and token splits."""

from __future__ import annotations

from ai_karaoke.pronunciation.reading import (
    JapaneseReadingConverter,
    contains_kanji,
    reading_text,
    speech_text,
)


def _kanji_only(text: str) -> bool:
    return bool(text) and all(contains_kanji(char) for char in text)


def _canonical_spelling(text: str, converter: JapaneseReadingConverter) -> str:
    parts = []
    for word in converter.converter.tokenizer.tokenize(text):
        surface = word.surface()
        normalized = word.normalized_form()
        # Only spelling variants preserve source offsets. Lemmas such as
        # 奏で -> 奏でる must not change the word's identity or kana suffix.
        if (
            len(normalized) == len(surface)
            and _kanji_only(surface)
            and _kanji_only(normalized)
        ):
            parts.append(normalized)
        else:
            parts.append(surface)
    return "".join(parts)


def propose_spelling_readings(
    units: list[dict], heard: str, converter: JapaneseReadingConverter
) -> list[dict]:
    """Use a unique kanji spelling as evidence for a whole lexical reading."""
    source = "".join(unit["orig"] for unit in units)
    canonical_source = _canonical_spelling(source, converter)
    heard_units = converter.convert(speech_text(heard))
    canonical_heard = _canonical_spelling(speech_text(heard), converter)
    starts, ends = {}, {}
    offset = 0
    for index, unit in enumerate(units):
        starts[offset] = index
        offset += len(unit["orig"])
        ends[offset] = index + 1

    proposals = []
    for heard_unit in heard_units:
        heard_base = heard_unit["orig"]
        if not _kanji_only(heard_base):
            continue
        canonical_base = _canonical_spelling(heard_base, converter)
        if canonical_source.count(canonical_base) != 1 or canonical_heard.count(canonical_base) != 1:
            continue
        start = canonical_source.index(canonical_base)
        end = start + len(canonical_base)
        if start not in starts or end not in ends:
            continue
        matched_units = units[starts[start]:ends[end]]
        base = source[start:end]
        if not _kanji_only(base):
            continue
        dictionary = reading_text("".join(unit["hira"] for unit in matched_units), converter)
        reading = reading_text(heard_unit["hira"], converter)
        if not reading or reading == dictionary or len(reading) > 16:
            continue
        proposals.append({
            "base": base, "dictionary": dictionary,
            "reading": reading, "accepted": True,
        })
    return proposals
