"""Standalone furigana injection command."""

from __future__ import annotations

import argparse
import tempfile

from ai_karaoke.pronunciation.ruby import (
    annotate_karaoke_text_with_furigana,
    apply_furigana_to_ass,
)


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
    output = annotate_karaoke_text_with_furigana(text, MockConverter())
    assert "{\\kf22}東京|<とうきょう" in output

    sample_ass = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\kf10}東{\\kf10}京\n"
    )
    with tempfile.NamedTemporaryFile(
        "w+", encoding="utf-8", suffix=".ass", delete=True
    ) as stream:
        stream.write(sample_ass)
        stream.flush()
        apply_furigana_to_ass(stream.name)
        stream.seek(0)
        assert "東京|<とうきょう" in stream.read()


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
