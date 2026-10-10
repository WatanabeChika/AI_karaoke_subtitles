"""Model-backed orchestration for low-memory karaoke CTC alignment."""

from __future__ import annotations

import math
import os

from ai_karaoke.alignment.core import align_sequence, build_alignment_sequence
from ai_karaoke.alignment.harmony import filter_overlapping_inline_harmonies
from ai_karaoke.alignment.models import AlignedToken, AlignmentResult
from ai_karaoke.alignment.text import NUMBER_READING_MODES, build_alignment_units
from ai_karaoke.alignment.windows import (
    block_windows,
    build_primary_blocks,
    context_blocks,
    parallel_window,
    recover_drifted_primary_lines,
    resolve_primary_overlaps,
    split_primary_and_parallel,
)

DEFAULT_CTC_MODEL = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"
SAMPLE_RATE = 16000
WINDOW_SECONDS = 30
CONTEXT_SECONDS = 2


def _prefer_cached_model(model_name: str) -> str:
    if os.path.exists(model_name):
        return model_name
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(model_name, local_files_only=True)
    except Exception:
        return model_name


class KaraokeCtcAligner:
    def __init__(
        self,
        model_name: str = DEFAULT_CTC_MODEL,
        batch_size: int = 1,
        number_reading: str = "auto",
    ) -> None:
        import torch
        from transformers import AutoModelForCTC

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.batch_size = max(1, batch_size)
        if number_reading not in NUMBER_READING_MODES:
            raise ValueError(f"number_reading must be one of {NUMBER_READING_MODES}")
        self.number_reading = number_reading
        self.model = AutoModelForCTC.from_pretrained(
            _prefer_cached_model(model_name),
            dtype=self.dtype,
        ).to(self.device).eval()
        if self.model.config.vocab_size != 32 or self.model.config.pad_token_id != 29:
            raise ValueError(
                "The selected CTC model does not use the karaoke-ja-Latn vocabulary "
                "(expected vocab_size=32 and pad_token_id=29)"
            )

    def align(self, audio, sample_rate: int, lines: list[dict], lang: str) -> AlignmentResult:
        import torch
        import torchaudio
        from ctc_forced_aligner import generate_emissions

        if not lang.lower().startswith(("ja", "en")):
            raise ValueError("The karaoke Japanese/Latin CTC model supports Japanese and Latin lyrics")

        waveform = torch.from_numpy(audio).to(torch.float32)
        if sample_rate != SAMPLE_RATE:
            waveform = torchaudio.functional.resample(waveform, sample_rate, SAMPLE_RATE)
        variance = waveform.var()
        waveform = (waveform - waveform.mean()) / torch.sqrt(variance + 1e-7)
        waveform = waveform.to(device=self.device, dtype=self.dtype)

        emissions, stride_ms = generate_emissions(
            self.model,
            waveform,
            window_length=WINDOW_SECONDS,
            context_length=CONTEXT_SECONDS,
            batch_size=self.batch_size,
        )
        emissions = emissions.cpu()
        duration = len(waveform) / SAMPLE_RATE
        del waveform
        del self.model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        lines = filter_overlapping_inline_harmonies(
            emissions, stride_ms, lines, duration, self.number_reading
        )
        units_per_line = [
            build_alignment_units(line["text"], line.get("readings"), self.number_reading)
            for line in lines
        ]
        for index, units in enumerate(units_per_line, start=1):
            if not units:
                raise ValueError(
                    f"Lyric line {index} has no alignable text: {lines[index - 1]['text']!r}"
                )

        primary_indices, parallel_indices = split_primary_and_parallel(lines)
        blocks = build_primary_blocks(lines, primary_indices)
        contexts = context_blocks(blocks, primary_indices)
        windows = block_windows(lines, contexts, duration)
        aligned: list[list[AlignedToken]] = [[] for _ in lines]
        for block, context, (window_start, window_end) in zip(blocks, contexts, windows):
            frame_start = max(0, int(window_start * 1000 / stride_ms))
            frame_end = min(len(emissions), int(math.ceil(window_end * 1000 / stride_ms)))
            tokens, owners = build_alignment_sequence(context, units_per_line)
            local = align_sequence(
                emissions[frame_start:frame_end],
                stride_ms,
                tokens,
                owners,
                len(lines),
                offset_sec=frame_start * stride_ms / 1000,
            )
            for line_index in block:
                aligned[line_index] = local[line_index]

        recover_drifted_primary_lines(
            emissions,
            stride_ms,
            lines,
            units_per_line,
            primary_indices,
            aligned,
            duration,
        )
        resolve_primary_overlaps(
            emissions,
            stride_ms,
            primary_indices,
            units_per_line,
            aligned,
            duration,
        )

        for index in parallel_indices:
            window_start, window_end = parallel_window(lines, index, duration)
            frame_start = max(0, int(window_start * 1000 / stride_ms))
            frame_end = min(len(emissions), int(math.ceil(window_end * 1000 / stride_ms)))
            tokens, owners = build_alignment_sequence([index], units_per_line)
            local = align_sequence(
                emissions[frame_start:frame_end],
                stride_ms,
                tokens,
                owners,
                len(lines),
                offset_sec=frame_start * stride_ms / 1000,
            )
            aligned[index] = local[index]

        for index, tokens in enumerate(aligned, start=1):
            if not tokens:
                raise RuntimeError(
                    f"CTC returned no timestamps for lyric line {index}: "
                    f"{lines[index - 1]['text']}"
                )
        return AlignmentResult(lines, aligned)
