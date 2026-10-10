import re
import unittest

from ai_karaoke.alignment.harmony import assess_harmony_overlap
from ai_karaoke.alignment.models import AlignedToken
from ai_karaoke.alignment.text import build_alignment_units, romanize_pronunciation
from ai_karaoke.application.generator import build_alignment_report, render_dialogue


class AlignmentTests(unittest.TestCase):
    def test_harmony_removal_uses_timing_not_text_similarity(self):
        main = [AlignedToken("different main lyric", 10.0, 15.0, 0.5)]
        simultaneous = [AlignedToken("unrelated harmony", 10.5, 14.5, 0.3)]
        sequential_duplicate = [AlignedToken("different main lyric", 15.2, 17.0, 0.5)]
        weak_forced_match = [AlignedToken("unrelated harmony", 10.5, 16.5, 0.02)]
        boundary_locked = [AlignedToken("unrelated harmony", 10.0, 14.2, 0.07)]
        near_total = [AlignedToken("unrelated harmony", 10.0, 15.0, 0.02)]
        squeezed_serial = [
            AlignedToken("quiet", 15.0, 15.12, 0.01),
            AlignedToken("harmony", 15.12, 15.30, 0.01),
        ]
        sequential_serial = [
            AlignedToken("audible", 15.0, 15.35, 0.2),
            AlignedToken("harmony", 15.35, 15.75, 0.2),
        ]
        self.assertTrue(assess_harmony_overlap(main, simultaneous)["remove"])
        self.assertFalse(assess_harmony_overlap(main, sequential_duplicate)["remove"])
        self.assertFalse(assess_harmony_overlap(main, weak_forced_match)["remove"])
        self.assertEqual(
            assess_harmony_overlap(main, boundary_locked)["evidence"],
            "boundary_locked_overlap",
        )
        self.assertEqual(
            assess_harmony_overlap(main, near_total, sequential_serial)["evidence"],
            "near_total_overlap",
        )
        self.assertEqual(
            assess_harmony_overlap(main, weak_forced_match, squeezed_serial)["evidence"],
            "serial_squeeze_overlap",
        )
        self.assertFalse(
            assess_harmony_overlap(main, weak_forced_match, sequential_serial)["remove"]
        )

    def test_render_preserves_original_punctuation_and_spacing(self):
        lyric = "夜の匂いに (I'll spend)"
        tokens = [
            AlignedToken("夜の匂いに", 1.0, 1.8),
            AlignedToken("I'll", 2.0, 2.4),
            AlignedToken("spend", 2.5, 3.0),
        ]
        karaoke_text = render_dialogue(lyric, tokens).rstrip().split(",", 9)[9]
        self.assertEqual(re.sub(r"\{\\kf\d+\}", "", karaoke_text), lyric)

    def test_report_flags_drift_without_rewriting_timestamps(self):
        report = build_alignment_report(
            [{"text": "歌", "start_time": 1.0}],
            [[AlignedToken("歌", 6.0, 6.5)]],
        )
        self.assertEqual(report[0]["start"], 6.0)
        self.assertIn("large_lrc_drift", report[0]["warnings"])

    def test_zero_duration_leading_token_does_not_start_line_at_window_edge(self):
        tokens = [AlignedToken("頬", 0.0, 0.0), AlignedToken("を", 2.4, 3.1)]
        self.assertTrue(render_dialogue("頬を", tokens).startswith("Dialogue: 0,0:00:02.40,"))
        report = build_alignment_report([{"text": "頬を", "start_time": 2.4}], [tokens])
        self.assertIn("zero_duration_tokens", report[0]["warnings"])

    def test_japanese_alignment_units_are_mora_or_word_sized(self):
        units = build_alignment_units("頬をきょうもずっと", {"頬": "ほほ"})
        self.assertEqual(
            [(unit.text, romanize_pronunciation(unit.pronunciation)) for unit in units],
            [("頬", "hoho"), ("を", "wo"), ("きょ", "kyo"), ("う", "u"),
             ("も", "mo"), ("ず", "zu"), ("っと", "tto")],
        )

    def test_numeric_alignment_units_have_selectable_reading(self):
        japanese = build_alignment_units("1,2,3で地面蹴って")
        english = build_alignment_units("1,2,3で地面蹴って", number_reading="en")
        self.assertEqual([unit.text for unit in japanese[:3]], ["1", "2", "3"])
        self.assertEqual([unit.pronunciation for unit in japanese[:3]], ["いち", "に", "さん"])
        self.assertEqual([unit.pronunciation for unit in english[:3]], ["one", "two", "three"])
        self.assertEqual(romanize_pronunciation("1", "en"), "one")

    def test_unknown_compound_kanji_stays_one_ctc_unit(self):
        self.assertEqual(
            [(unit.text, unit.pronunciation) for unit in build_alignment_units("正念場")],
            [("正念場", "しょうねんば")],
        )
        self.assertEqual(
            [(unit.text, unit.pronunciation) for unit in build_alignment_units("地面")],
            [("地面", "じめん")],
        )

    def test_low_ctc_confidence_is_audited(self):
        report = build_alignment_report(
            [{"text": "歌", "start_time": 1.0}],
            [[AlignedToken("歌", 1.0, 1.5, 0.02)]],
        )
        self.assertIn("low_ctc_confidence", report[0]["warnings"])
