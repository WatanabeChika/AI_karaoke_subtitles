# Pipeline design

## Data flow

1. `ai_karaoke/lyrics/parser.py` turns untrusted LRC text into ordered lyric records:
   `start_time`, `end_time` (hints only), display `text`, explicit `readings`,
   bracket-free `main_text`, and undecided `inline_harmonies`.
2. `ai_karaoke/audio/separation.py` always produces vocal and accompaniment stems with Mel-Band
   RoFormer. Official-instrumental cancellation is no longer part of the pipeline.
3. `ai_karaoke/alignment/aligner.py` orchestrates the singing-tuned MMS CTC model.
   `text.py` owns transcript units, `core.py` owns the CTC path calculation,
   `windows.py` owns bounded context and recovery, and `harmony.py` owns temporal
   harmony classification. These modules preserve model timestamps and emit an
   audit report instead of shifting failures to make them look continuous.
4. `ai_karaoke/pronunciation/reading.py`, `evidence.py`, and `whisper.py` choose
   Japanese readings; `ruby.py` writes them into timed ASS text. In the subtitle
   stage, `karaoke_tags.py` owns timing-tag normalization and `style.py` remains
   the final K1/K2 composition step.

## Alignment decisions

- Convert Japanese text to context-sensitive readings, romanize those readings for
  MMS CTC, and retain each unit's original display text. Replacing the display text
  itself would lose the identity needed to render timestamps back to the lyric.
- Align small blocks with their complete previous and next lyric lines as context.
  This avoids cropping the first or last sung syllable without making repeated
  choruses compete in a single whole-song transcript.
- Treat LRC timestamps as search hints. They bound the window but never overwrite
  model timestamps. Drift, overlap, and zero-duration tokens are recorded in
  `*_alignment_report.json`.
- Preserve same-timestamp Japanese lines as possible harmonies. Translation lines
  are removed by language/script evidence, not by blindly dropping duplicates.
- Do not remove inline harmony by lyric similarity. With the CTC backend, align
  the bracket-free main lyric and every inline harmony independently in the same
  bounded window, then also test the alternative that all displayed fragments
  were sung serially. Remove a harmony when it has confident temporal overlap,
  near-total overlap, or the serial hypothesis can fit it only by compressing its
  alignment units unnaturally. Store both timing hypotheses, confidence, overlap,
  and the final decision in `*_alignment_report.json`.

## Pronunciation decisions

Precedence is:

1. An attached LRC reading such as `頬(ほほ)`.
2. A Whisper-derived reading when the heard kana difference maps to exactly one
   kanji unit and the evidence passes confidence, lyric-similarity, and coverage
   checks. A single edge kanji may use one-sided evidence; multi-kanji edge guesses
   are rejected.
3. The lexical-context Sudachi/kanjiconv reading.

Whisper is never allowed to alter non-kanji text. Unanchored discrepancies remain
in `*_pronunciation_report.json`; they do not silently modify the subtitle. The
report contains every lyric line with kanji, including matched lines and lines with
no usable ASR evidence, and records dictionary, heard, final, explicit, and applied
readings separately.

Sudachi morpheme readings are consumed once in their original sentence context,
then the resolved readings are reused while rendering split karaoke tags
(`奏で` must remain `かなで`, and `一途` must remain `いちず`).
kanjiconv's custom compound readings and fallback dictionary remain available.

The pronunciation report uses these statuses:

| Status | Meaning |
| --- | --- |
| `matched` | The complete selected ASR span matches the baseline reading. |
| `corrected` | At least one Whisper reading changes the final annotation. |
| `explicit_reading_retained` | An LRC reading takes priority over a proposed correction. |
| `mismatch_unresolved` | Reliable ASR differs, but no safe kanji correction is available. |
| `incomplete_or_unreliable` | ASR evidence is present but fails whole-line checks. |
| `no_asr_evidence` | Neither whole-song ASR nor the local retry supplies words. |

`dictionary` contains the baseline reading after LRC overrides; `final_kana`
contains the chosen reading. `applied_readings` contains effective Whisper
overrides, and `explicit_readings` contains LRC overrides. Whole-line
`asr_accepted` is independent of local correction: a missing ending need not block
a well-supported correction earlier in the line. Edge coverage compares kana
sequences, so an alternate pronunciation can also trigger an edge mismatch.

In an actual large-v3 smoke test, the request for hiragana-only output still
returned kanji. Therefore the implementation treats Whisper as evidence about the
heard word form, not as an authoritative kana generator. If Whisper repeats the
same ambiguous kanji, kanjiconv remains the result and the report exposes the
uncertainty.

The production path intentionally exposes one model per processing stage. Model
comparisons belong in isolated experiments; the CLI and GUI both use Mel-Band
RoFormer for separation, MMS Karaoke CTC for timing, and faster-whisper plus
kanjiconv for optional Japanese pronunciation checks.
