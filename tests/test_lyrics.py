import tempfile
import unittest

from ai_karaoke.lyrics.parser import parse_lrc_data_strict, remove_inline_harmonies


class LyricsTests(unittest.TestCase):
    def test_provider_placeholders_and_theme_credits_are_not_lyrics(self):
        lines = self.parse(
            "[00:01.00] / <☆SOLO!个说>\n"
            "[00:02.00]undefined\n"
            "[00:03.00]TVアニメ「作品名」オープニングテーマ\n"
            "[00:04.00]本当の歌詞\n"
        )
        self.assertEqual([line["text"] for line in lines], ["本当の歌詞"])

    def parse(self, content):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".lrc") as stream:
            stream.write(content)
            stream.flush()
            return parse_lrc_data_strict(stream.name, "ja")

    def test_extracts_attached_reading_but_keeps_harmony_parentheses(self):
        lines = self.parse(
            "[ar:test]\n"
            "[00:01.00]頬(ほほ)をなでる (遠くで呼ぶ) / 中文翻译\n"
            "[00:05.00]次の行\n"
        )
        self.assertEqual(lines[0]["text"], "頬をなでる (遠くで呼ぶ)")
        self.assertEqual(lines[0]["readings"], {"頬": "ほほ"})

    def test_extracts_halfwidth_katakana_reading_and_removes_it_from_lyrics(self):
        lines = self.parse("[00:01.00]あの日確かに　未来(ｷｮｳ)が呼んでた\n")
        self.assertEqual(lines[0]["text"], "あの日確かに　未来が呼んでた")
        self.assertEqual(lines[0]["readings"], {"未来": "きょう"})

    def test_inline_harmonies_are_deferred_to_audio_alignment(self):
        lines = self.parse(
            "[00:01.00]みんなの声が(みんなの声) 背中を押して(背中を押して)\n"
            "[00:05.00]夜の匂いに (I’ll spend all thirty nights)\n"
            "[00:09.00](We are proud of the true youth stories)\n"
        )
        self.assertEqual(lines[0]["text"], "みんなの声が(みんなの声) 背中を押して(背中を押して)")
        self.assertEqual(lines[0]["main_text"], "みんなの声が 背中を押して")
        self.assertEqual(
            [item["text"] for item in lines[0]["inline_harmonies"]],
            ["みんなの声", "背中を押して"],
        )
        self.assertEqual(
            [(part["kind"], part["text"]) for part in lines[0]["inline_parts"]],
            [
                ("main", "みんなの声が"),
                ("harmony", "みんなの声"),
                ("main", "背中を押して"),
                ("harmony", "背中を押して"),
            ],
        )
        self.assertEqual(lines[1]["text"], "夜の匂いに (I’ll spend all thirty nights)")
        self.assertEqual(lines[1]["main_text"], "夜の匂いに")
        self.assertEqual(lines[2]["text"], "(We are proud of the true youth stories)")
        self.assertEqual(lines[2]["inline_harmonies"], [])

    def test_text_similarity_does_not_remove_inline_harmony_during_parsing(self):
        lines = self.parse(
            "[00:01.00]想いよひとつになれ (想いがひとつになるとき)\n"
            "[00:05.00]想いはひとつだよと (わかるんだ 想いはひとつ)\n"
        )
        self.assertEqual(
            [line["text"] for line in lines],
            [
                "想いよひとつになれ (想いがひとつになるとき)",
                "想いはひとつだよと (わかるんだ 想いはひとつ)",
            ],
        )

    def test_time_filter_can_remove_only_selected_inline_harmony(self):
        self.assertEqual(
            remove_inline_harmonies("主歌 (同時和声) 後半 (順番和声)", {0}),
            "主歌 後半 (順番和声)",
        )

    def test_discards_chinese_parenthetical_translation_but_keeps_short_harmony(self):
        lines = self.parse(
            "[00:01.00](ほんとは ほんとはね)\n"
            "[00:01.00]（说真的 其实我）\n"
            "[00:05.00]（愛）\n"
            "[00:05.00]（小小的）\n"
        )
        self.assertEqual([line["text"] for line in lines], ["(ほんとは ほんとはね)", "（愛）"])

    def test_discards_chinese_translation_with_iteration_symbol(self):
        lines = self.parse(
            "[00:01.00]今 ゴーイング ゴールインより\n"
            "[00:01.00]现在在going goal in（跑过去射门ヽ(´▽｀)/）\n"
        )
        self.assertEqual([line["text"] for line in lines], ["今 ゴーイング ゴールインより"])

    def test_preserves_multiple_japanese_lines_at_same_timestamp(self):
        lines = self.parse(
            "[00:01.00]君の声\n"
            "[00:01.00]（遠くで呼ぶ）\n"
            "[00:01.00]（愛）\n"
            "[00:01.00]你的声音\n"
            "[00:05.00]次の行\n"
        )
        self.assertEqual([line["text"] for line in lines[:3]], ["君の声", "（遠くで呼ぶ）", "（愛）"])

    def test_supports_multiple_time_tags_and_bom(self):
        lines = self.parse("\ufeff[00:01.2][00:03.250]歌う\n[00:05.00]終わり\n")
        self.assertEqual([line["start_time"] for line in lines], [1.2, 3.25, 5.0])

    def test_discards_decoration_only_rows_but_keeps_decorated_lyrics(self):
        lines = self.parse(
            "[00:01.00]・・・♪♡👩‍🎤ーー\n"
            "[00:02.00]☆ア・ナ・タ♡\n"
        )
        self.assertEqual([line["text"] for line in lines], ["☆ア・ナ・タ♡"])

    def test_translation_separator_works_inside_multiline_harmony_parentheses(self):
        lines = self.parse(
            "[00:01.00](We are proud / 中文翻译\n"
            "[00:05.00]of those days) / 另一行翻译\n"
        )
        self.assertEqual([line["text"] for line in lines], ["(We are proud", "of those days)"])
