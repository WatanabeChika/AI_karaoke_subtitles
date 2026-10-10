import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_karaoke.alignment.models import AlignedToken
from ai_karaoke.application.generator import render_dialogue
from ai_karaoke.pronunciation.evidence import (
    propose_whisper_readings,
    select_whisper_evidence,
)
from ai_karaoke.pronunciation.reading import JapaneseReadingConverter, merge_readings
from ai_karaoke.pronunciation.ruby import (
    annotate_karaoke_text_with_furigana,
    apply_furigana_to_ass,
)
from ai_karaoke.pronunciation.whisper import detect_whisper_readings

TRANSCRIBE = "ai_karaoke.pronunciation.whisper._transcribe_whole_song"
RETRY = "ai_karaoke.pronunciation.whisper._retry_whisper_line"


class FuriganaTests(unittest.TestCase):
    def test_higher_priority_reading_removes_overlapping_lower_priority_key(self):
        self.assertEqual(
            merge_readings({"明日": "あした", "空": "そら"}, {"明": "みょう"}),
            {"空": "そら", "明": "みょう"},
        )

    def test_lrc_reading_overrides_dictionary(self):
        sample = (
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\kf20}頬{\\kf10}を\n"
        )
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            stream.write(sample)
            stream.flush()
            apply_furigana_to_ass(stream.name, [{"頬": "ほほ"}])
            content = Path(stream.name).read_text(encoding="utf-8")
        self.assertIn("頬|<ほほ", content)

    def test_whisper_override_requires_two_unique_kana_anchors(self):
        self.assertEqual(
            propose_whisper_readings("君は生物だよ", "きみはなまものだよ"),
            [{"base": "生物", "dictionary": "せいぶつ", "reading": "なまもの", "accepted": True}],
        )
        self.assertEqual(
            propose_whisper_readings("君は生物だよ", "君は生ものだよ"),
            [{"base": "生物", "dictionary": "せいぶつ", "reading": "なまもの", "accepted": True}],
        )
        self.assertEqual(propose_whisper_readings("生物だよ", "なまものだよ"), [])

    def test_single_initial_kanji_can_use_unique_right_anchor(self):
        self.assertEqual(
            propose_whisper_readings("未だ知らない", "まだ知らない"),
            [{"base": "未", "dictionary": "いま", "reading": "ま", "accepted": True}],
        )
        self.assertEqual(propose_whisper_readings("奏でた夢重ね", "かなでた夢重ね"), [])
        self.assertEqual(propose_whisper_readings("何度でも君と", "どでも君と"), [])

    def test_variant_spelling_can_merge_a_whole_heard_kanji_word(self):
        self.assertEqual(
            propose_whisper_readings(
                "砂漠で迷ってるよ每日は辛い",
                "砂漠で迷ってるような毎日は辛い",
            ),
            [{
                "base": "每日", "dictionary": "ごとひ",
                "reading": "まいにち", "accepted": True,
            }],
        )
        self.assertEqual(propose_whisper_readings("每日每日", "毎日毎日"), [])
        self.assertEqual(
            propose_whisper_readings(
                "砂漠で迷ってるよ每日は辛い",
                "砂漠で迷ってるような毎日は辛い",
                {"日": "にち"},
            ),
            [{
                "base": "每", "dictionary": "ごと",
                "reading": "まい", "accepted": True,
            }],
        )

    def test_dictionary_reading_uses_lexical_context_for_compound_verbs(self):
        converter = JapaneseReadingConverter()
        self.assertEqual(
            [(item["orig"], item["hira"]) for item in converter.convert("奏でた夢重ね")],
            [("奏", "かな"), ("で", "で"), ("た", "た"),
             ("夢", "ゆめ"), ("重", "かさ"), ("ね", "ね")],
        )
        self.assertEqual(
            [(item["orig"], item["hira"]) for item in converter.convert("まっすぐに照らす瞳")],
            [("まっすぐ", "まっすぐ"), ("に", "に"),
             ("照", "て"), ("らす", "らす"), ("瞳", "ひとみ")],
        )

    def test_dictionary_reading_keeps_kanjiconv_custom_dictionary(self):
        converter = JapaneseReadingConverter()
        converter.converter.custom_readings["compound"]["行く"] = "ゆく"
        self.assertEqual(
            converter.convert("激を飛ばす、ここへ行く"),
            [{"orig": "激", "hira": "げき"}, {"orig": "を", "hira": "を"},
             {"orig": "飛", "hira": "と"}, {"orig": "ばす", "hira": "ばす"},
             {"orig": "、", "hira": "、"}, {"orig": "ここ", "hira": "ここ"},
             {"orig": "へ", "hira": "へ"}, {"orig": "行", "hira": "ゆ"},
             {"orig": "く", "hira": "く"}],
        )

    @patch(RETRY, return_value=[])
    @patch(TRANSCRIBE)
    def test_pronunciation_report_includes_matched_and_unheard_kanji_lines(
        self, transcribe, _retry
    ):
        audio = [0.0] * 192000
        transcribe.return_value = (
            object(), audio,
            [
                {"text": "奏でた夢重ね", "start": 1.0, "end": 2.5,
                 "probability": 0.95, "segment_reliable": True},
                {"text": "まっすぐに照らす瞳", "start": 4.0, "end": 6.0,
                 "probability": 0.95, "segment_reliable": True},
            ],
        )
        lines = [
            {"text": "奏でた夢重ね", "start_time": 1.0, "readings": {}},
            {"text": "まっすぐに照らす瞳", "start_time": 4.0, "readings": {}},
            {"text": "未だ見ぬ空", "start_time": 9.0, "readings": {}},
        ]
        aligned = [
            [AlignedToken("奏でた夢重ね", 1.0, 2.5)],
            [AlignedToken("まっすぐに照らす瞳", 4.0, 6.0)],
            [AlignedToken("未だ見ぬ空", 9.0, 10.0)],
        ]
        readings, reports = detect_whisper_readings(audio, 16000, lines, aligned)
        self.assertEqual(readings, [{}, {}, {}])
        self.assertEqual([item["lyric"] for item in reports], [line["text"] for line in lines])
        self.assertEqual([item["status"] for item in reports], ["matched", "matched", "no_asr_evidence"])
        self.assertEqual(reports[0]["dictionary"], "かなでたゆめかさね")
        self.assertEqual(reports[1]["dictionary"], "まっすぐにてらすひとみ")

    @patch(RETRY, return_value=[])
    @patch(TRANSCRIBE)
    def test_explicit_lrc_reading_is_reported_as_retained(self, transcribe, _retry):
        audio = [0.0] * 80000
        transcribe.return_value = (
            object(), audio,
            [{"text": "まだ知らない", "start": 1.0, "end": 2.5,
              "probability": 0.95, "segment_reliable": True}],
        )
        lines = [{"text": "未だ知らない", "start_time": 1.0, "readings": {"未": "み"}}]
        aligned = [[AlignedToken("未だ知らない", 1.0, 2.5)]]
        readings, reports = detect_whisper_readings(audio, 16000, lines, aligned)
        self.assertEqual(readings, [{"未": "み"}])
        self.assertEqual(reports[0]["status"], "explicit_reading_retained")
        self.assertEqual(reports[0]["applied_readings"], {})
        self.assertEqual(reports[0]["explicit_readings"], {"未": "み"})
        self.assertEqual(reports[0]["final_kana"], "みだしらない")

    @patch(RETRY, return_value=[])
    @patch(TRANSCRIBE)
    def test_ctc_reading_reaches_report_and_final_furigana(self, transcribe, _retry):
        audio = [0.0] * 80000
        transcribe.return_value = (
            object(), audio,
            [{"text": "あしたのことはわかんない", "start": 1.0, "end": 3.0,
              "probability": 0.95, "segment_reliable": True}],
        )
        line = {
            "text": "明日のことはわかんない", "start_time": 1.0,
            "readings": {}, "alignment_readings": {"明日": "あした"},
        }
        aligned = [[AlignedToken(line["text"], 1.0, 3.0)]]
        readings, reports = detect_whisper_readings(audio, 16000, [line], aligned)
        self.assertEqual(readings, [{"明日": "あした"}])
        self.assertEqual(reports[0]["dictionary"], "あしたのことはわかんない")
        self.assertEqual(reports[0]["final_kana"], "あしたのことはわかんない")
        self.assertEqual(reports[0]["alignment_readings"], {"明日": "あした"})
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            stream.write(render_dialogue(line["text"], aligned[0]))
            stream.flush()
            apply_furigana_to_ass(stream.name, readings)
            content = Path(stream.name).read_text(encoding="utf-8")
        self.assertIn("明日|<あした", content)

    @patch(RETRY, return_value=[])
    @patch(TRANSCRIBE)
    def test_explicit_reading_wins_over_overlapping_ctc_reading(self, transcribe, _retry):
        audio = [0.0] * 80000
        transcribe.return_value = (object(), audio, [])
        line = {
            "text": "明日は", "start_time": 1.0,
            "readings": {"明": "みょう"},
            "alignment_readings": {"明日": "あした"},
        }
        readings, reports = detect_whisper_readings(
            audio, 16000, [line], [[AlignedToken(line["text"], 1.0, 2.0)]]
        )
        self.assertEqual(readings, [{"明": "みょう"}])
        self.assertEqual(reports[0]["alignment_readings"], {})
        self.assertEqual(reports[0]["final_kana"], "みょうひは")

    def test_ass_keeps_kana_text_and_contextual_kanji_reading(self):
        lyrics = ["奏でた夢重ね", "まっすぐに照らす瞳"]
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            for index, lyric in enumerate(lyrics):
                stream.write(render_dialogue(
                    lyric, [AlignedToken(lyric, index * 4.0, index * 4.0 + 3.0)]
                ))
            stream.flush()
            apply_furigana_to_ass(stream.name, [{}, {}])
            content = Path(stream.name).read_text(encoding="utf-8")
        self.assertIn("奏|<かな", content)
        self.assertNotIn("奏|<そう", content)
        self.assertIn("まっすぐ", content)
        self.assertIn("照|<て", content)
        self.assertIn("瞳|<ひとみ", content)

    def test_ass_reuses_whole_line_reading_across_empty_karaoke_tags(self):
        lyric = "一途に未来を呼ぶこころ"
        tokens = [
            AlignedToken("一途", 1.0, 1.88), AlignedToken("に", 1.88, 2.32),
            AlignedToken("未来", 2.36, 3.12), AlignedToken("を", 3.12, 3.44),
            AlignedToken("呼ぶ", 3.44, 4.0), AlignedToken("こころ", 4.0, 5.94),
        ]
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            stream.write(render_dialogue(lyric, tokens))
            stream.flush()
            apply_furigana_to_ass(stream.name, [{}])
            content = Path(stream.name).read_text(encoding="utf-8")
        self.assertIn(r"一途|<いちず", content)
        self.assertNotIn(r"一途|<いっと", content)

    def test_furigana_only_splits_compounds_with_safe_mora_boundaries(self):
        converter = JapaneseReadingConverter()
        safe = annotate_karaoke_text_with_furigana(r"{\kf30}正{\kf20}念{\kf10}場", converter)
        unsafe = annotate_karaoke_text_with_furigana(r"{\kf30}地{\kf20}面", converter)
        self.assertIn("正念場|<しょうねんば", safe)
        self.assertIn("地面|<じめん", unsafe)
        self.assertNotIn("地|<じ", unsafe)
        self.assertNotIn("面|<ん", unsafe)

    def test_missing_alignment_line_is_kept_in_pronunciation_report(self):
        with patch(TRANSCRIBE) as transcribe:
            transcribe.return_value = (object(), [0.0] * 16000, [])
            readings, reports = detect_whisper_readings(
                [0.0] * 16000, 16000,
                [{"text": "奏でた夢重ね", "start_time": 1.0, "readings": {}}],
                [[]],
            )
        self.assertEqual(readings, [{}])
        self.assertEqual(reports[0]["lyric"], "奏でた夢重ね")
        self.assertEqual(reports[0]["status"], "no_alignment_evidence")

    @patch(RETRY, return_value=[])
    @patch(TRANSCRIBE)
    def test_partial_asr_correction_is_reported_and_applied_without_rewriting_kana(
        self, transcribe, _retry
    ):
        audio = [0.0] * 96000
        transcribe.return_value = (
            object(), audio,
            [{"text": "まっさらな扉開く", "start": 1.0, "end": 3.0,
              "probability": 0.95, "segment_reliable": True}],
        )
        lyric = "真っ新な扉開くなら"
        lines = [{"text": lyric, "start_time": 1.0, "readings": {}}]
        aligned = [[AlignedToken(lyric, 1.0, 3.5)]]
        readings, reports = detect_whisper_readings(audio, 16000, lines, aligned)
        self.assertFalse(reports[0]["asr_accepted"])
        self.assertEqual(reports[0]["status"], "corrected")
        self.assertEqual(reports[0]["applied_readings"], {"新": "さら"})
        self.assertEqual(reports[0]["final_kana"], "まっさらなとびらひらくなら")
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            stream.write(render_dialogue(lyric, aligned[0]))
            stream.flush()
            apply_furigana_to_ass(stream.name, readings)
            content = Path(stream.name).read_text(encoding="utf-8")
        self.assertIn("新|<さら", content)
        self.assertIn("っ", content)
        self.assertIn("なら", content)

    def test_whisper_evidence_rejects_low_confidence_hallucination(self):
        evidence = select_whisper_evidence(
            [{"text": "ご視聴ありがとうございました", "start": 10.0, "end": 12.0,
              "probability": 0.2, "segment_reliable": False}],
            9.5, 12.5,
        )
        self.assertFalse(evidence["accepted"])
        self.assertIn("low_word_probability", evidence["reasons"])
        self.assertIn("unreliable_segment", evidence["reasons"])

    def test_whisper_evidence_rejects_missing_start_even_with_high_similarity(self):
        evidence = select_whisper_evidence(
            [{"text": "どでも君とめぐりあう", "start": 10.0, "end": 13.0,
              "probability": 0.95, "segment_reliable": True}],
            9.0, 14.0, lyric="何度でも君とめぐりあう",
        )
        self.assertFalse(evidence["accepted"])
        self.assertFalse(evidence["prefix_complete"])
        self.assertIn("missing_lyric_start", evidence["reasons"])

    def test_incomplete_sentence_can_still_offer_anchored_reading(self):
        evidence = select_whisper_evidence(
            [{"text": "まっさらな扉開く", "start": 10.0, "end": 13.0,
              "probability": 0.95, "segment_reliable": True}],
            9.0, 14.0, lyric="真っ新な扉開くなら",
        )
        self.assertFalse(evidence["accepted"])
        self.assertIn("missing_lyric_end", evidence["reasons"])
        self.assertEqual(
            propose_whisper_readings("真っ新な扉開くなら", evidence["text"]),
            [{"base": "新", "dictionary": "しん", "reading": "さら", "accepted": True}],
        )

    def test_internal_asr_deletion_still_maps_to_the_only_changed_kanji(self):
        self.assertEqual(
            propose_whisper_readings("真っ新な扉開くなら", "まさらな扉開くなら"),
            [{"base": "新", "dictionary": "しん", "reading": "さら", "accepted": True}],
        )

    @patch(RETRY, return_value=[])
    @patch(TRANSCRIBE)
    def test_repeated_harmony_word_cannot_globally_override_main_reading(
        self, transcribe, _retry
    ):
        lyric = "想いよひとつになれ (想いがひとつになるとき)"
        heard = "思いよひとつになれ この時"
        audio = [0.0] * 160000
        transcribe.return_value = (
            object(), audio,
            [{"text": heard, "start": 0.0, "end": 8.86,
              "probability": 0.77, "segment_reliable": True}],
        )
        lines = [{"text": lyric, "start_time": 1.19, "readings": {}}]
        aligned = [[AlignedToken(lyric, 1.54, 7.2)]]
        readings, reports = detect_whisper_readings(audio, 16000, lines, aligned)
        self.assertEqual(propose_whisper_readings(lyric, heard), [])
        self.assertEqual(readings, [{}])
        self.assertEqual(reports[0]["applied_readings"], {})
        self.assertEqual(
            reports[0]["final_kana"],
            "おもいよひとつになれおもいがひとつになるとき",
        )
        self.assertFalse(reports[0]["asr_accepted"])
        self.assertIn("low_lyric_coverage", reports[0]["rejection_reasons"])
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            stream.write(render_dialogue(lyric, aligned[0]))
            stream.flush()
            apply_furigana_to_ass(stream.name, readings)
            content = Path(stream.name).read_text(encoding="utf-8")
        self.assertEqual(content.count("想|<おも"), 2)
        self.assertNotIn("想|<この", content)
