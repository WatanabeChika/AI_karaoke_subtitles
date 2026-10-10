import tempfile
import unittest
from pathlib import Path

from ai_karaoke.subtitles.style import apply_kara_style_to_ass


class KaraStyleTests(unittest.TestCase):
    def _style_line(
        self,
        start: str,
        advance_ms: int = 3000,
        prior: tuple[str, str] | None = None,
    ):
        events = []
        if prior:
            events.append(
                f"Dialogue: 0,{prior[0]},{prior[1]},Default,,0,0,0,,{{\\kf10}}前\n"
            )
        events.append(
            f"Dialogue: 0,{start},0:00:12.00,Default,,0,0,0,,{{\\kf10}}歌\n"
        )
        sample = (
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,50,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            + "".join(events)
        )
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".ass") as stream:
            stream.write(sample)
            stream.flush()
            apply_kara_style_to_ass(
                stream.name,
                advance_time_ms=advance_ms,
                sep_threshold_ms=1000,
                insert_template_comments=False,
            )
            dialogues = [
                line.rstrip().split(",", 9)
                for line in Path(stream.name).read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue:")
            ]
        return dialogues[-1]

    def test_new_sequence_uses_three_one_second_countdown_dots(self):
        fields = self._style_line("0:00:10.00")
        self.assertEqual(fields[1], "0:00:07.00")
        self.assertTrue(fields[9].startswith("{\\kf100}●{\\kf100}●{\\kf100}●"))

    def test_song_start_limits_countdown_to_available_whole_seconds(self):
        two = self._style_line("0:00:02.50")
        one = self._style_line("0:00:01.20")
        none = self._style_line("0:00:00.90")
        self.assertTrue(two[9].startswith("{\\k50}{\\kf100}●{\\kf100}●"))
        self.assertTrue(one[9].startswith("{\\k20}{\\kf100}●"))
        self.assertTrue(none[9].startswith("{\\k90}{\\kf10}歌"))

    def test_user_advance_limit_and_slot_collision_reduce_dot_count(self):
        limited = self._style_line("0:00:10.00", advance_ms=2000)
        collided = self._style_line(
            "0:00:10.00",
            advance_ms=3000,
            prior=("0:00:04.00", "0:00:08.40"),
        )
        self.assertEqual(limited[9].count("●"), 2)
        self.assertEqual(collided[1], "0:00:08.40")
        self.assertEqual(collided[9].count("●"), 1)
