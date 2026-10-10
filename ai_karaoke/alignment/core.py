"""Low-level CTC transcript and emission alignment primitives."""

from __future__ import annotations

import math

from ai_karaoke.alignment.models import AlignedToken, AlignmentUnit
from ai_karaoke.alignment.text import romanize_pronunciation


class KaraokeTokenizer:
    """Tokenizer used when the karaoke alignment model was trained."""

    def __init__(self) -> None:
        self._vocab = {char: index for index, char in enumerate("|abcdefghijklmnopqrstuvwxyz'")}
        self._vocab["[UNK]"] = 28
        self._vocab["[PAD]"] = 29
        self._vocab["<s>"] = 30
        self._vocab["</s>"] = 31
        self.vocab_size = 32
        self.pad_token_id = 29

    def get_vocab(self) -> dict[str, int]:
        return dict(self._vocab)


def build_alignment_sequence(
    line_indices: list[int],
    units_per_line: list[list[AlignmentUnit]],
) -> tuple[list[str], list[tuple[int, AlignmentUnit]]]:
    tokens: list[str] = []
    owners: list[tuple[int, AlignmentUnit]] = []
    for line_index in line_indices:
        for unit in units_per_line[line_index]:
            romanized = romanize_pronunciation(unit.pronunciation)
            if not romanized:
                raise ValueError(
                    f"Cannot romanize lyric unit {unit.text!r} "
                    f"from pronunciation {unit.pronunciation!r}"
                )
            tokens.append(" ".join(romanized))
            owners.append((line_index, unit))
    return tokens, owners


def _raw_token_spans(tokens, segments, blank):
    """Group actual label frames without assigning surrounding blank audio."""
    spans = []
    segment_index = 0
    for token in tokens:
        token_segments = []
        for character in token.split():
            while segment_index < len(segments) and segments[segment_index].label == blank:
                segment_index += 1
            if segment_index >= len(segments) or segments[segment_index].label != character:
                actual = segments[segment_index].label if segment_index < len(segments) else "<end>"
                raise RuntimeError(f"CTC path mismatch: expected {character!r}, got {actual!r}")
            token_segments.append(segments[segment_index])
            segment_index += 1
        spans.append(token_segments)
    return spans


def align_sequence(
    emissions,
    stride_ms: float,
    tokens: list[str],
    owners: list[tuple[int, AlignmentUnit]],
    line_count: int,
    offset_sec: float = 0.0,
) -> list[list[AlignedToken]]:
    from ctc_forced_aligner import get_alignments

    segments, scores, blank = get_alignments(emissions, tokens, KaraokeTokenizer())
    spans = _raw_token_spans(tokens, segments, blank)
    if len(spans) != len(owners):
        raise RuntimeError(f"CTC returned {len(spans)} spans for {len(owners)} transcript units")

    aligned: list[list[AlignedToken]] = [[] for _ in range(line_count)]
    for owner, span in zip(owners, spans):
        if not span:
            raise RuntimeError(f"CTC returned an empty span for lyric unit {owner[1].text!r}")
        start_frame = span[0].start
        end_frame = span[-1].end + 1
        start = offset_sec + start_frame * stride_ms / 1000
        end = offset_sec + end_frame * stride_ms / 1000
        if end <= start:
            end = start + stride_ms / 1000
        label_scores = [
            float(scores[segment.start : segment.end + 1].mean())
            for segment in span
        ]
        mean_log_score = sum(label_scores) / len(label_scores)
        confidence = math.exp(max(-20.0, min(0.0, mean_log_score)))
        line_index, unit = owner
        aligned[line_index].append(AlignedToken(unit.text, start, end, confidence))
    return aligned
