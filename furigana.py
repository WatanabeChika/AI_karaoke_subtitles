import argparse
import re
import tempfile
from typing import Dict, List


K_TAG_PATTERN = re.compile(r"\{\\(k(?:f|o)?)(\d+)\}([^{}]*)")


def _is_japanese_char(ch: str) -> bool:
    code = ord(ch)
    if 0x3040 <= code <= 0x309F:
        return True
    if 0x30A0 <= code <= 0x30FF:
        return True
    if 0x31F0 <= code <= 0x31FF:
        return True
    if 0x4E00 <= code <= 0x9FFF:
        return True
    if ch in "々〆〇ヶー":
        return True
    return False


def _contains_kanji(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or ch in "々〆〇ヶ":
            return True
    return False


def _is_pronounceable_japanese_segment(text: str) -> bool:
    if not text:
        return False
    trimmed = [ch for ch in text if not ch.isspace()]
    if not trimmed:
        return False
    return all(_is_japanese_char(ch) for ch in trimmed)


def _split_karaoke_text(text: str) -> List[Dict]:
    parts: List[Dict] = []
    pos = 0
    for match in K_TAG_PATTERN.finditer(text):
        if match.start() > pos:
            parts.append({"type": "raw", "text": text[pos:match.start()]})

        parts.append(
            {
                "type": "k",
                "tag": match.group(1),
                "duration": int(match.group(2)),
                "text": match.group(3),
            }
        )
        pos = match.end()

    if pos < len(text):
        parts.append({"type": "raw", "text": text[pos:]})
    return parts


def _render_karaoke_text(parts: List[Dict]) -> str:
    result: List[str] = []
    for part in parts:
        if part["type"] == "raw":
            result.append(part["text"])
        else:
            result.append(f"{{\\{part['tag']}{part['duration']}}}{part['text']}")
    return "".join(result)


def _distribute_duration(duration: int, length: int) -> List[int]:
    if length <= 0:
        return []
    base = duration // length
    rem = duration % length
    return [base + (1 if i < rem else 0) for i in range(length)]


def _build_ruby_text(orig: str, hira: str) -> str:
    if _contains_kanji(orig) and hira and hira != orig:
        return f"{orig}|<{hira}"
    return orig


def _merge_group_by_furigana(group: List[Dict], converter) -> List[Dict]:
    combined = "".join(seg["text"] for seg in group)
    if not combined:
        return group

    converted = converter.convert(combined)
    if not converted:
        return group

    converted_orig = "".join(item.get("orig", "") for item in converted)
    if converted_orig != combined:
        return group

    char_durations: List[int] = []
    for seg in group:
        char_durations.extend(_distribute_duration(seg["duration"], len(seg["text"])))

    if len(char_durations) != len(combined):
        return group

    merged: List[Dict] = []
    offset = 0
    total_old = sum(seg["duration"] for seg in group)

    for item in converted:
        orig = item.get("orig", "")
        if not orig:
            continue
        span = len(orig)
        token_duration = sum(char_durations[offset : offset + span])
        offset += span

        merged.append(
            {
                "type": "k",
                "tag": group[0]["tag"],
                "duration": token_duration,
                "text": _build_ruby_text(orig, item.get("hira", "")),
            }
        )

    if not merged or offset != len(char_durations):
        return group

    total_new = sum(seg["duration"] for seg in merged)
    drift = total_old - total_new
    if drift != 0:
        merged[-1]["duration"] += drift
    return merged


def annotate_karaoke_text_with_furigana(text: str, converter) -> str:
    parts = _split_karaoke_text(text)
    out_parts: List[Dict] = []
    i = 0

    while i < len(parts):
        part = parts[i]
        if part["type"] != "k" or not _is_pronounceable_japanese_segment(part["text"]):
            out_parts.append(part)
            i += 1
            continue

        start = i
        while i < len(parts) and parts[i]["type"] == "k" and _is_pronounceable_japanese_segment(parts[i]["text"]):
            i += 1

        group = parts[start:i]
        out_parts.extend(_merge_group_by_furigana(group, converter))

    return _render_karaoke_text(out_parts)


def _build_converter():
    try:
        from pykakasi import kakasi
    except ImportError as e:
        raise RuntimeError("Missing dependency pykakasi. Install with: pip install pykakasi") from e
    return kakasi()


def apply_furigana_to_ass(ass_path: str) -> int:
    converter = _build_converter()

    with open(ass_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changed_lines = 0
    new_lines: List[str] = []

    for line in lines:
        if not line.startswith("Dialogue:"):
            new_lines.append(line)
            continue

        fields = line.rstrip("\n").split(",", 9)
        if len(fields) < 10:
            new_lines.append(line)
            continue

        old_text = fields[9]
        new_text = annotate_karaoke_text_with_furigana(old_text, converter)
        if new_text != old_text:
            changed_lines += 1
            fields[9] = new_text
            new_lines.append(",".join(fields) + "\n")
        else:
            new_lines.append(line)

    with open(ass_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return changed_lines


def _run_self_tests() -> None:
    class MockConverter:
        def convert(self, text):
            if text == "東京タワー":
                return [
                    {"orig": "東京", "hira": "とうきょう"},
                    {"orig": "タワー", "hira": "たわー"},
                ]
            return [{"orig": text, "hira": text}]

    text = r"{\kf10}東{\kf12}京{\kf8}タ{\kf8}ワ{\kf8}ー{\kf0}!"
    out = annotate_karaoke_text_with_furigana(text, MockConverter())
    assert "{\\kf22}東京|<とうきょう" in out

    sample_ass = """[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\kf10}東{\\kf10}京\n"""

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass", delete=True) as tmp:
        tmp.write(sample_ass)
        tmp.flush()

        class LocalConverter:
            def convert(self, text):
                return [{"orig": "東京", "hira": "とうきょう"}] if text == "東京" else [{"orig": text, "hira": text}]

        with open(tmp.name, "r", encoding="utf-8") as f:
            lines = f.readlines()
        fields = lines[-1].rstrip("\n").split(",", 9)
        fields[9] = annotate_karaoke_text_with_furigana(fields[9], LocalConverter())
        lines[-1] = ",".join(fields) + "\n"
        with open(tmp.name, "w", encoding="utf-8") as f:
            f.writelines(lines)

        with open(tmp.name, "r", encoding="utf-8") as f:
            content = f.read()
        assert "東京|<とうきょう" in content


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject hiragana ruby into ASS karaoke tags")
    parser.add_argument("-i", "--input", help="Input ASS file path")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    args = parser.parse_args()

    if args.self_test:
        _run_self_tests()
        print("Self-test passed: furigana")
        return

    if not args.input:
        parser.error("--input is required unless --self-test is used")

    changed = apply_furigana_to_ass(args.input)
    print(f"Furigana applied, changed lines: {changed}")


if __name__ == "__main__":
    main()
