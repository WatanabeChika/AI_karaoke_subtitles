import argparse
import os
import re
import tempfile
from typing import Dict, List

import stable_whisper
import torchaudio
import torchaudio.transforms as T

from furigana import apply_furigana_to_ass
from kara_style import (
    DEFAULT_KARA_ACCENT_COLOR,
    DEFAULT_KARA_PRIMARY_COLOR,
    apply_kara_style_to_ass,
)

DEFAULT_KARA_HANDLE_STYLE = "Default"
DEFAULT_KARA_TEMPLATE_COMMENT_STYLE = "K1"
TRANSLATION_PAIR_TOLERANCE_SEC = 0.05
DIALOGUE_END_PAD_SEC = 0.5
DEFAULT_WHISPER_MODEL = "medium"


def format_ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _char_profile(text: str) -> Dict[str, int]:
    kana = 0
    cjk = 0
    latin = 0
    for ch in text:
        code = ord(ch)
        if 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
            kana += 1
        elif 0x4E00 <= code <= 0x9FFF:
            cjk += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            latin += 1
    return {"kana": kana, "cjk": cjk, "latin": latin}


def _language_text_score(text: str, lang: str) -> float:
    profile = _char_profile(text)
    compact_len = len([c for c in text if not c.isspace()])
    lang_l = lang.lower()

    if lang_l.startswith("ja"):
        return profile["kana"] * 4.0 + profile["cjk"] * 1.0 + profile["latin"] * 0.3 + compact_len * 0.01
    if lang_l.startswith("zh"):
        return profile["cjk"] * 3.0 + profile["kana"] * 0.2 + profile["latin"] * 0.2 + compact_len * 0.01
    if lang_l.startswith("en"):
        return profile["latin"] * 3.0 - (profile["kana"] + profile["cjk"]) * 0.8 + compact_len * 0.01
    return compact_len * 1.0


def _select_preferred_line(cluster: List[Dict], lang: str) -> Dict:
    candidates = cluster
    lang_l = lang.lower()

    # For Japanese lyrics, prefer lines that contain kana when available.
    # This avoids long Chinese translation/explanation lines at near-identical
    # timestamps overpowering short Japanese originals by raw length.
    if lang_l.startswith("ja"):
        kana_candidates = [item for item in cluster if _char_profile(item["text"])["kana"] > 0]
        if kana_candidates:
            candidates = kana_candidates

    best = candidates[0]
    best_score = _language_text_score(best["text"], lang)
    for item in candidates[1:]:
        score = _language_text_score(item["text"], lang)
        if score > best_score:
            best = item
            best_score = score
    return best


def _drop_interleaved_translations(lyric_candidates: List[Dict], lang: str) -> List[Dict]:
    if not lyric_candidates:
        return []

    clusters: List[List[Dict]] = []
    current_cluster: List[Dict] = [lyric_candidates[0]]
    for item in lyric_candidates[1:]:
        if item["time"] - current_cluster[-1]["time"] <= TRANSLATION_PAIR_TOLERANCE_SEC:
            current_cluster.append(item)
        else:
            clusters.append(current_cluster)
            current_cluster = [item]
    clusters.append(current_cluster)

    filtered: List[Dict] = []
    for cluster in clusters:
        if len(cluster) == 1:
            filtered.append(cluster[0])
        else:
            filtered.append(_select_preferred_line(cluster, lang))
    return filtered


def parse_lrc_data_strict(lrc_path: str, lang: str = "ja") -> List[Dict]:
    raw_lines: List[Dict] = []
    staff_pattern = (
        r"^([作编]?词|[作编]?曲|演唱|后期|混音|制作|原唱|翻唱|策划|美工|和声|吉他|贝斯|鼓|键盘|弦乐|"
        r"Vocal|Lyric[s]?|Music|Arrangement|Mix|Master|Staff|by|翻译)\s*[:：\s]"
    )

    with open(lrc_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or re.match(r"^\[[a-zA-Z]+:.*?\]$", line):
                continue

            match = re.match(r"^\[(\d+):(\d+\.\d+)\](.*)", line)
            if not match:
                continue

            mins, secs, text = match.groups()
            current_time = int(mins) * 60 + float(secs)
            text = text.strip()

            if re.search(r"\s+/\s+|\s*／\s*", text):
                text = re.split(r"\s+/\s+|\s*／\s*", text)[0].strip()

            is_lyric = True
            if not text or re.match(staff_pattern, text, re.IGNORECASE) or re.match(r"^(\[.*?\]|【.*?】)$", text):
                is_lyric = False
            # Drop consecutive same-timestamp duplicate lyric lines, but do not
            # let metadata/staff lines suppress a real lyric line that happens
            # to share the same timestamp (e.g. many 00:00.000 headers).
            if (
                raw_lines
                and text
                and raw_lines[-1]["time"] == current_time
                and raw_lines[-1]["is_lyric"]
                and is_lyric
            ):
                continue

            raw_lines.append({"time": current_time, "text": text, "is_lyric": is_lyric})

    lyric_candidates = [line for line in raw_lines if line["is_lyric"]]
    lyric_candidates = _drop_interleaved_translations(lyric_candidates, lang=lang)

    lyric_lines: List[Dict] = []
    for i, line in enumerate(lyric_candidates):
        end_time = None
        if i + 1 < len(lyric_candidates):
            end_time = lyric_candidates[i + 1]["time"]

        if end_time is None:
            end_time = line["time"] + 10.0

        lyric_lines.append(
            {
                "start_time": line["time"],
                "end_time": end_time,
                "text": line["text"],
            }
        )

    return lyric_lines


def generate_ass_header() -> str:
    return """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def process_word_k_tags(word_text: str, word_duration: float) -> str:
    cleaned = word_text.replace("\n", "")
    if not cleaned:
        return ""

    split_pattern = (
        r"([\s\!\?\,\.\:\;\-\—\…\"\'\“\”\‘\’\(\)\[\]\{\}\<\>"
        r"\、\。\！\？\：\；\（\）\［\］\｛\｝\「\」\『\』\【\】\《\》]+)"
    )
    parts = [p for p in re.split(split_pattern, cleaned) if p]
    if not parts:
        return ""

    k_main = int(round(word_duration * 100))
    result: List[str] = []
    duration_assigned = False

    for part in parts:
        is_punct_or_space = bool(re.match(split_pattern, part))
        if is_punct_or_space:
            result.append(f"{{\\kf0}}{part}")
            continue

        if not duration_assigned:
            result.append(f"{{\\kf{k_main}}}{part}")
            duration_assigned = True
        else:
            result.append(f"{{\\kf0}}{part}")

    return "".join(result)


def _load_audio_mono_16k(vocal_path: str):
    waveform, sr = torchaudio.load(vocal_path)
    if sr != 16000:
        resampler = T.Resample(sr, 16000, dtype=waveform.dtype)
        waveform = resampler(waveform)
    return waveform[0].numpy()


def _align_line_to_dialogue(
    model,
    audio_array,
    lyric_lines: List[Dict],
    idx: int,
    lang: str,
) -> str:
    line = lyric_lines[idx]
    line_start = line["start_time"]
    line_end = line["end_time"]

    pad_pre = 0.2
    if idx > 0:
        prev_end = lyric_lines[idx - 1]["end_time"]
        pad_pre = min(pad_pre, max(0.0, line_start - prev_end))

    chunk_start = max(0.0, line_start - pad_pre)
    chunk_end = line_end + 0.5

    start_sample = int(chunk_start * 16000)
    end_sample = int(chunk_end * 16000)
    chunk_audio = audio_array[start_sample:end_sample]

    if len(chunk_audio) <= 1600:
        return ""

    result = model.align(chunk_audio, line["text"], language=lang)
    all_words = [w for segment in result.segments for w in segment.words]

    current_time = line_start
    dialogue_text = ""

    for w in all_words:
        abs_word_start = max(line_start, min(chunk_start + w.start, line_end))
        abs_word_end = max(line_start, min(chunk_start + w.end, line_end))
        word_dur = max(0.0, abs_word_end - abs_word_start)

        gap = max(0.0, abs_word_start - current_time)
        if gap > 0.02:
            dialogue_text += f"{{\\kf{int(round(gap * 100))}}}"

        dialogue_text += process_word_k_tags(w.word, word_dur)
        current_time = abs_word_start + word_dur

    if not dialogue_text:
        return ""

    line_event_end = min(line_end, current_time + DIALOGUE_END_PAD_SEC)
    line_event_end = max(line_start + 0.01, line_event_end)

    start_ass = format_ass_time(line_start)
    end_ass = format_ass_time(line_event_end)
    return f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{dialogue_text}\n"


def generate_karaoke_ass(
    vocal_path: str,
    lrc_path: str,
    output_path: str,
    base_name: str,
    lang: str = "ja",
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    kara_advance_time_ms: int = 100,
    kara_sep_threshold_ms: int = 200000,
    kara_primary_color: str = DEFAULT_KARA_PRIMARY_COLOR,
    kara_accent_color: str = DEFAULT_KARA_ACCENT_COLOR,
) -> str:
    print("\n[2/4] Parsing LRC timestamps...")
    lyric_lines = parse_lrc_data_strict(lrc_path, lang=lang)

    print("\n[3/4] Loading audio and Whisper model...")
    audio_array = _load_audio_mono_16k(vocal_path)
    print(f"      Whisper model: {whisper_model}")
    model = stable_whisper.load_model(whisper_model)

    print("\n[4/4] Running constrained word alignment...")
    output_dir = os.path.join(output_path, base_name)
    os.makedirs(output_dir, exist_ok=True)
    ass_file_path = os.path.join(output_dir, f"{base_name}.ass")

    with open(ass_file_path, "w", encoding="utf-8") as f:
        f.write(generate_ass_header())
        for idx in range(len(lyric_lines)):
            dialogue = _align_line_to_dialogue(model, audio_array, lyric_lines, idx, lang)
            if dialogue:
                f.write(dialogue)

    if lang.lower().startswith("ja"):
        print("\n[Furigana] Injecting hiragana annotation...")
        try:
            changed_count = apply_furigana_to_ass(ass_file_path)
            print(f"      done, changed lines: {changed_count}")
        except Exception as e:
            print(f"      skipped: {e}")

    print("\n[Kara] Applying layered karaoke style...")
    try:
        stats = apply_kara_style_to_ass(
            ass_path=ass_file_path,
            handle_style=DEFAULT_KARA_HANDLE_STYLE,
            advance_time_ms=kara_advance_time_ms,
            sep_threshold_ms=kara_sep_threshold_ms,
            insert_template_comments=True,
            template_comment_style=DEFAULT_KARA_TEMPLATE_COMMENT_STYLE,
            kara_primary_color=kara_primary_color,
            kara_accent_color=kara_accent_color,
        )
        print(
            "      done, "
            f"processed lines: {stats['styled_lines']}, "
            f"max style: K{stats['max_style_index'] if stats['max_style_index'] else 0}, "
            f"new template comments: {stats['inserted_comments']}"
        )
    except Exception as e:
        print(f"      skipped: {e}")

    print("\nDone")
    print(f"ASS generated: {ass_file_path}")
    return ass_file_path


def _run_self_tests() -> None:
    assert format_ass_time(61.23) == "0:01:01.23"
    assert process_word_k_tags("Go!", 0.46) == "{\\kf46}Go{\\kf0}!"

    lrc_content = """[ti:test]\n[00:01.00]作词:abc\n[00:02.00]第一行\n[00:02.00]第一行重复\n[00:05.50]第二行 / second\n"""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".lrc", delete=True) as tmp:
        tmp.write(lrc_content)
        tmp.flush()
        lines = parse_lrc_data_strict(tmp.name)

    assert len(lines) == 2
    assert lines[0]["text"] == "第一行"
    assert lines[0]["start_time"] == 2.0
    assert lines[0]["end_time"] == 5.5
    assert lines[1]["text"] == "第二行"

    ja_with_translation = """[00:10.00]走り出した\n[00:10.03]开始奔跑\n[00:14.00]強くなるよ\n[00:14.04]会变得更强\n"""
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".lrc", delete=True) as tmp:
        tmp.write(ja_with_translation)
        tmp.flush()
        lines = parse_lrc_data_strict(tmp.name, lang="ja")
    assert len(lines) == 2
    assert lines[0]["text"] == "走り出した"
    assert lines[1]["text"] == "強くなるよ"

    # At the beginning of some LRC files, multiple metadata lines can share
    # the same timestamp as the first lyric line.
    same_ts_with_staff = (
        "[00:00.000]作词 : 指原莉乃\n"
        "[00:00.000]作曲 : 栗原 暁/前田 佑\n"
        "[00:00.000]编曲 : 前田 佑/門脇大輔\n"
        "[00:00.000]あの色も　この色も\n"
        "[00:00.000][by:为什么那么难112]\n"
        "[00:00.000]这一道颜色 那一道颜色\n"
        "[00:03.762]カラフル　溢(あふ)れる\n"
        "[00:03.762]汇聚成为 缤纷画卷\n"
    )
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".lrc", delete=True) as tmp:
        tmp.write(same_ts_with_staff)
        tmp.flush()
        lines = parse_lrc_data_strict(tmp.name, lang="ja")
    assert len(lines) == 2
    assert lines[0]["text"] == "あの色も　この色も"
    assert abs(lines[0]["start_time"] - 0.0) < 1e-6
    assert lines[1]["text"] == "カラフル　溢(あふ)れる"

    # Chinese explanatory text can be much longer than the Japanese original,
    # but when lang=ja we should still prefer kana-containing Japanese lyrics.
    long_zh_vs_ja = (
        "[00:45.410]宴会拉开序慕\n"
        "[00:45.414]宴の幕開け開始\n"
        "[00:50.050]烟熏奶酪\n"
        "[00:50.054]スモークチーズ\n"
        "[00:51.760]做好了的Quiche（法国料理的一种。在馅饼的盘子中铺上馅饼的材料，加入培根，火腿，芝士等材料，浇上混入生奶油，牛奶的咸味蛋液，经烘箱烤制而成。用作餐前菜。）\n"
        "[00:51.768]できたてのキッシュ\n"
        "[00:53.840]红朴叶包起来的虹鳟\n"
        "[00:53.848]朴葉で包んだヒメマス\n"
    )
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".lrc", delete=True) as tmp:
        tmp.write(long_zh_vs_ja)
        tmp.flush()
        lines = parse_lrc_data_strict(tmp.name, lang="ja")
    assert len(lines) == 4
    assert lines[0]["text"] == "宴の幕開け開始"
    assert lines[1]["text"] == "スモークチーズ"
    assert lines[2]["text"] == "できたてのキッシュ"
    assert lines[3]["text"] == "朴葉で包んだヒメマス"

    # Dialogue end should follow aligned lyric end rather than the loose LRC boundary.
    current_time = 3.00
    line_end = 10.00
    event_end = min(line_end, current_time + DIALOGUE_END_PAD_SEC)
    assert abs(event_end - 3.5) < 1e-3


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate karaoke ASS from vocal audio and LRC")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    parser.add_argument("--vocal", help="Vocal wav path")
    parser.add_argument("--lrc", help="LRC path")
    parser.add_argument("--output", help="Output directory")
    parser.add_argument("--base-name", help="Output base name")
    parser.add_argument("--lang", default="ja", help="Language code")
    parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        help=(
            "Whisper model name, e.g. tiny/base/small/medium/large/turbo "
            "(approx VRAM: tiny/base~1GB, small~2GB, medium~5GB, large~10GB, turbo~6GB)."
        ),
    )
    parser.add_argument(
        "--kara-advance-ms",
        type=int,
        default=100,
        help=(
            "Kara advance time in milliseconds. For a continuous alternating sequence "
            "of two lyric lines, the first line appears this much earlier."
        ),
    )
    parser.add_argument(
        "--kara-sep-threshold-ms",
        type=int,
        default=200000,
        help=(
            "Kara sequence gap threshold in milliseconds. When the gap between adjacent "
            "lines exceeds this value, the next line starts a new sequence."
        ),
    )
    parser.add_argument("--kara-primary-color", default=DEFAULT_KARA_PRIMARY_COLOR)
    parser.add_argument("--kara-accent-color", default=DEFAULT_KARA_ACCENT_COLOR)
    args = parser.parse_args()

    if args.self_test:
        _run_self_tests()
        print("Self-test passed: aligner")
        return

    required = [args.vocal, args.lrc, args.output, args.base_name]
    if any(v is None for v in required):
        parser.error("--vocal --lrc --output --base-name are required unless --self-test is used")

    generate_karaoke_ass(
        vocal_path=args.vocal,
        lrc_path=args.lrc,
        output_path=args.output,
        base_name=args.base_name,
        lang=args.lang,
        whisper_model=args.whisper_model,
        kara_advance_time_ms=args.kara_advance_ms,
        kara_sep_threshold_ms=args.kara_sep_threshold_ms,
        kara_primary_color=args.kara_primary_color,
        kara_accent_color=args.kara_accent_color,
    )


if __name__ == "__main__":
    main()
