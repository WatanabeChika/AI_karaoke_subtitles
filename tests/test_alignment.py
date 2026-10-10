import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from ai_karaoke.alignment.core import align_sequence, build_alignment_sequence
from ai_karaoke.alignment.harmony import assess_harmony_overlap
from ai_karaoke.alignment.models import AlignedToken
from ai_karaoke.alignment.readings import alternate_reading, refine_ambiguous_readings
from ai_karaoke.alignment.text import build_alignment_units, romanize_pronunciation
from ai_karaoke.application.generator import build_alignment_report, render_dialogue


class AlignmentTests(unittest.TestCase):
    @patch("ctc_forced_aligner.get_alignments")
    def test_unknown_audio_is_allowed_at_edges_and_between_lines(self, get_alignments):
        labels = ["<star>", "a", "<star>", "b", "u", "<star>"]
        get_alignments.return_value = (
            [SimpleNamespace(label=label, start=index, end=index)
             for index, label in enumerate(labels)],
            np.full(6, -0.2),
            "[PAD]",
        )
        units = [
            [build_alignment_units("あ")[0]],
            [build_alignment_units("ぶ")[0]],
        ]
        tokens, owners = build_alignment_sequence([0, 1], units)

        aligned = align_sequence(torch.zeros(6, 33), 20.0, tokens, owners, 2)

        self.assertEqual(get_alignments.call_args.args[1], [
            "<star>", "a", "<star>", "b u", "<star>"
        ])
        self.assertEqual([(token.start, token.end) for token in aligned[0]], [(0.02, 0.04)])
        self.assertEqual([(token.start, token.end) for token in aligned[1]], [(0.06, 0.1)])

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

    def test_symbols_stay_visible_without_becoming_ctc_units(self):
        lyric = "☆ア・ナ・タ♡ A・B…👩‍🎤"
        units = build_alignment_units(lyric)
        ctc_tokens, owners = build_alignment_sequence([0], [units])

        self.assertTrue(all(token.strip() for token in ctc_tokens))
        self.assertNotIn("・", [unit.text for _, unit in owners])
        aligned = [
            AlignedToken(unit.text, index, index + 0.2)
            for index, (_, unit) in enumerate(owners, start=1)
        ]
        rendered = render_dialogue(lyric, aligned).rstrip().split(",", 9)[9]
        self.assertEqual(re.sub(r"\{\\kf\d+\}", "", rendered), lyric)

    def test_empty_dictionary_nodes_around_ellipsis_do_not_enter_ctc(self):
        lyric = "君の…君のこんなにも"
        units = build_alignment_units(lyric)
        ctc_tokens, owners = build_alignment_sequence([0], [units])

        self.assertTrue(ctc_tokens)
        self.assertTrue(all(unit.text and unit.pronunciation for _, unit in owners))
        aligned = [
            AlignedToken(unit.text, index, index + 0.2)
            for index, (_, unit) in enumerate(owners, start=1)
        ]
        rendered = render_dialogue(lyric, aligned).rstrip().split(",", 9)[9]
        self.assertEqual(re.sub(r"\{\\kf\d+\}", "", rendered), lyric)

    def test_compatibility_kana_and_unicode_digits_are_normalized(self):
        units = build_alignment_units("ｶﾞｯﾂﾎﾟｰｽﾞ １２③")
        ctc_tokens, _ = build_alignment_sequence([0], [units])

        self.assertTrue(all(token.strip() for token in ctc_tokens))
        self.assertEqual(
            "".join(romanize_pronunciation(unit.pronunciation) for unit in units[:4]),
            "gattsupoozu",
        )
        self.assertEqual([unit.pronunciation for unit in units[-3:]], ["いち", "に", "さん"])

    def test_kana_block_modifiers_never_create_empty_ctc_tokens(self):
        units = build_alignment_units("くゞ クヾ ㇷ゚ ヷヸヹヺ ー・゛")
        ctc_tokens, _ = build_alignment_sequence([0], [units])

        self.assertTrue(ctc_tokens)
        self.assertTrue(all(token.strip() for token in ctc_tokens))

    def test_unreadable_letters_are_not_silently_discarded(self):
        units = build_alignment_units("한")
        with self.assertRaisesRegex(ValueError, "Cannot romanize lyric unit"):
            build_alignment_sequence([0], [units])

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

    def test_ambiguous_kanji_offers_an_independent_alignment_reading(self):
        unit = build_alignment_units("明日")[0]
        self.assertEqual(unit.pronunciation, "あす")
        self.assertEqual(alternate_reading(unit), "あした")

    def test_repeated_word_cannot_create_a_line_wide_ctc_reading(self):
        units = build_alignment_units("明日と明日")
        aligned = [[
            AlignedToken(unit.text, index, index + 0.2, 0.01)
            for index, unit in enumerate(units)
        ]]
        lines = [{
            "text": "明日と明日", "start_time": 0.0, "readings": {},
        }]

        refine_ambiguous_readings(
            torch.zeros(100, 33), 20.0, lines, [units], aligned, 2.0
        )

        self.assertNotIn("alignment_readings", lines[0])

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
