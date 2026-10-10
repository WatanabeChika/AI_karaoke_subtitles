"""Use faster-whisper as optional evidence for Japanese lyric readings."""

from __future__ import annotations

import os

from ai_karaoke.pronunciation.evidence import (
    evidence_rank,
    local_reading_evidence_is_usable,
    propose_whisper_readings,
    select_whisper_evidence,
)
from ai_karaoke.pronunciation.reading import (
    JapaneseReadingConverter,
    contains_kanji,
    merge_readings,
    reading_text,
)

DEFAULT_FURIGANA_WHISPER_MODEL = "large-v3"
FURIGANA_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")


def validate_furigana_whisper_model(model_name: str) -> str:
    if model_name not in FURIGANA_WHISPER_MODELS:
        raise ValueError(f"Unknown pronunciation Whisper model: {model_name}")
    return model_name


def _prefer_cached_faster_whisper(model_name: str) -> str:
    if os.path.exists(model_name):
        return model_name
    repo_id = model_name if "/" in model_name else f"Systran/faster-whisper-{model_name}"
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(repo_id, local_files_only=True)
    except Exception:
        return model_name


def _collect_whisper_words(result, offset: float = 0.0) -> list[dict]:
    words: list[dict] = []
    for segment in result.segments:
        avg_logprob = getattr(segment, "avg_logprob", None)
        no_speech_prob = getattr(segment, "no_speech_prob", None)
        compression_ratio = getattr(segment, "compression_ratio", None)
        reliable = (
            (avg_logprob is None or avg_logprob >= -1.0)
            and (no_speech_prob is None or no_speech_prob <= 0.6)
            and (compression_ratio is None or compression_ratio <= 2.4)
        )
        segment_words = getattr(segment, "words", None) or []
        if not segment_words and getattr(segment, "text", "").strip():
            words.append({
                "text": segment.text,
                "start": offset + segment.start,
                "end": offset + segment.end,
                "probability": None,
                "segment_reliable": reliable,
            })
            continue
        for word in segment_words:
            words.append({
                "text": word.word,
                "start": offset + word.start,
                "end": offset + word.end,
                "probability": getattr(word, "probability", None),
                "segment_reliable": reliable,
            })
    return words


def _transcribe_whole_song(audio, sample_rate: int, model_name: str):
    import librosa
    import stable_whisper
    import torch

    if sample_rate != 16000:
        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=16000)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = stable_whisper.load_faster_whisper(
        _prefer_cached_faster_whisper(model_name),
        device=device,
        compute_type="int8_float16" if device == "cuda" else "int8",
    )
    result = model.transcribe(
        audio,
        language="ja",
        task="transcribe",
        condition_on_previous_text=False,
        temperature=0.0,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        hallucination_silence_threshold=2.0,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
        suppress_silence=True,
        regroup=False,
        word_timestamps=True,
        verbose=None,
    )
    return model, audio, _collect_whisper_words(result)


def _retry_whisper_line(model, audio, line_start: float, line_end: float) -> list[dict]:
    """Re-transcribe a boundary-safe local window when whole-song chunks crop a line."""
    start = max(0.0, line_start - 3.0)
    end = min(len(audio) / 16000, line_end + 3.0)
    chunk = audio[int(start * 16000):int(end * 16000)]
    result = model.transcribe(
        chunk,
        language="ja",
        task="transcribe",
        condition_on_previous_text=False,
        temperature=0.0,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        vad_filter=False,
        suppress_silence=False,
        regroup=False,
        word_timestamps=True,
        verbose=None,
    )
    return _collect_whisper_words(result, offset=start)


def _line_readings(line: dict) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return explicit, usable CTC, and effective pre-Whisper readings."""
    explicit = dict(line.get("readings", {}))
    effective = merge_readings(line.get("alignment_readings", {}), explicit)
    alignment = {
        base: reading
        for base, reading in effective.items()
        if base not in explicit
    }
    return explicit, alignment, effective


def _empty_alignment_report(line: dict) -> dict:
    explicit, alignment, effective = _line_readings(line)
    dictionary = reading_text(line["text"], JapaneseReadingConverter(effective))
    return {
        "lyric": line["text"],
        "heard": "",
        "heard_kana": "",
        "dictionary": dictionary,
        "final_kana": dictionary,
        "start": None,
        "end": None,
        "search_start": None,
        "search_end": None,
        "confidence": 0.0,
        "asr_accepted": False,
        "status": "no_alignment_evidence",
        "lyric_match": None,
        "lyric_coverage": 0.0,
        "prefix_complete": False,
        "suffix_complete": False,
        "rejection_reasons": ["no_alignment_tokens"],
        "proposals": [],
        "applied_readings": alignment,
        "alignment_readings": alignment,
        "explicit_readings": explicit,
    }


def detect_whisper_readings(
    audio,
    sample_rate,
    lines,
    aligned,
    model_name=DEFAULT_FURIGANA_WHISPER_MODEL,
):
    import torch

    model, transcribed_audio, words = _transcribe_whole_song(audio, sample_rate, model_name)
    reports = []
    readings = []
    for line, tokens in zip(lines, aligned):
        explicit, alignment, baseline = _line_readings(line)
        if not contains_kanji(line["text"]):
            readings.append(baseline)
            continue
        if not tokens:
            readings.append(baseline)
            reports.append(_empty_alignment_report(line))
            continue

        timed_tokens = [token for token in tokens if token.end > token.start]
        aligned_start = min(token.start for token in timed_tokens) if timed_tokens else tokens[0].start
        start = max(0.0, aligned_start - 2.0)
        end = min(len(audio) / sample_rate, max(token.end for token in tokens) + 2.0)
        evidence = select_whisper_evidence(
            words, start, end, lyric=line["text"], readings=baseline
        )
        if not evidence.get("prefix_complete") or not evidence.get("suffix_complete"):
            retry_words = _retry_whisper_line(
                model, transcribed_audio, aligned_start, max(token.end for token in tokens)
            )
            retry = select_whisper_evidence(
                retry_words, start, end, lyric=line["text"], readings=baseline
            )
            if evidence_rank(retry) > evidence_rank(evidence):
                evidence = retry

        heard = evidence["text"]
        proposals = (
            propose_whisper_readings(line["text"], heard, readings=baseline)
            if evidence["accepted"] or local_reading_evidence_is_usable(evidence)
            else []
        )
        dictionary = reading_text(line["text"], JapaneseReadingConverter(baseline))
        final_kana = dictionary
        machine_readings = dict(alignment)
        whisper_applied = False
        for proposal in proposals:
            if (
                proposal["accepted"]
                and not any(
                    proposal["base"] in base or base in proposal["base"]
                    for base in explicit
                )
            ):
                candidate = merge_readings(
                    machine_readings, {proposal["base"]: proposal["reading"]}
                )
                candidate_kana = reading_text(
                    line["text"],
                    JapaneseReadingConverter({**candidate, **explicit}),
                )
                if candidate_kana != final_kana:
                    machine_readings = candidate
                    final_kana = candidate_kana
                    whisper_applied = True
        effective = {**machine_readings, **explicit}
        readings.append(effective)
        heard_kana = reading_text(heard)
        if whisper_applied or alignment:
            status = "corrected"
        elif proposals:
            status = "explicit_reading_retained"
        elif evidence["accepted"] and heard_kana == dictionary:
            status = "matched"
        elif evidence["accepted"]:
            status = "mismatch_unresolved"
        elif heard:
            status = "incomplete_or_unreliable"
        else:
            status = "no_asr_evidence"
        reports.append({
            "lyric": line["text"],
            "heard": heard,
            "heard_kana": heard_kana,
            "dictionary": dictionary,
            "final_kana": final_kana,
            "start": evidence.get("start"),
            "end": evidence.get("end"),
            "search_start": start,
            "search_end": end,
            "confidence": evidence["confidence"],
            "asr_accepted": evidence["accepted"],
            "status": status,
            "lyric_match": evidence["match_ratio"],
            "lyric_coverage": evidence["lyric_coverage"],
            "prefix_complete": evidence["prefix_complete"],
            "suffix_complete": evidence["suffix_complete"],
            "rejection_reasons": evidence["reasons"],
            "proposals": proposals,
            "applied_readings": machine_readings,
            "alignment_readings": alignment,
            "explicit_readings": explicit,
        })
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return readings, reports
