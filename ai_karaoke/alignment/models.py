"""Data contracts and timing helpers shared by the alignment stage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlignedToken:
    text: str
    start: float
    end: float
    score: float | None = None


@dataclass(frozen=True)
class AlignmentUnit:
    text: str
    pronunciation: str


@dataclass(frozen=True)
class AlignmentResult:
    lines: list[dict]
    aligned: list[list[AlignedToken]]


def line_start(tokens: list[AlignedToken]) -> float:
    return min(token.start for token in tokens)


def line_end(tokens: list[AlignedToken]) -> float:
    return max(token.end for token in tokens)


def mean_token_confidence(tokens: list[AlignedToken]) -> float:
    scores = [token.score for token in tokens if token.score is not None]
    return sum(scores) / len(scores) if scores else 0.0
