import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_karaoke.application.pipeline import (
    PipelineConfig,
    SongArtifacts,
    build_song_requests,
    discover_audio_paths,
)


class BatchPipelineTests(unittest.TestCase):
    def test_directory_discovery_is_sorted_filtered_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "B.flac"
            second = root / "a.mp3"
            ignored = root / "notes.txt"
            for path in (first, second, ignored):
                path.write_bytes(b"test")
            paths = discover_audio_paths([str(second)], directory)
        self.assertEqual(paths, [str(second.resolve()), str(first.resolve())])

    def test_explicit_lyric_is_rejected_for_batch(self):
        with self.assertRaisesRegex(ValueError, "one input song"):
            build_song_requests(["/tmp/a.flac", "/tmp/b.flac"], "/tmp/a.lrc")

    def test_duplicate_song_names_cannot_overwrite_one_output_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory) / "left"
            right = Path(directory) / "right"
            left.mkdir()
            right.mkdir()
            (left / "same.flac").write_bytes(b"left")
            (right / "same.mp3").write_bytes(b"right")
            with self.assertRaisesRegex(ValueError, "duplicate song names"):
                discover_audio_paths([str(left / "same.flac"), str(right / "same.mp3")])

    def test_pipeline_defaults_match_ktv_countdown_contract(self):
        config = PipelineConfig()
        self.assertEqual(config.kara_advance_ms, 3000)
        self.assertEqual(config.kara_sep_threshold_ms, 10000)
        self.assertTrue(config.use_whisper_readings)
        self.assertEqual(config.furigana_whisper_model, "large-v3")
        self.assertFalse(hasattr(config, "alignment_backend"))

    def test_pronunciation_model_must_be_a_supported_size(self):
        with self.assertRaisesRegex(ValueError, "pronunciation Whisper model"):
            PipelineConfig(furigana_whisper_model="custom/model")

    def test_cli_batch_continues_after_one_song_fails(self):
        from ai_karaoke.interfaces import cli as cli_main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.flac"
            second = root / "second.flac"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            seen = []

            def fake_process(request, output, config, reporter):
                seen.append(request.base_name)
                if request.base_name == "first":
                    raise RuntimeError("first failed")
                ass = root / "second.ass"
                ass.write_text("[Events]\nFormat: x\n", encoding="utf-8")
                return SongArtifacts(
                    title="second",
                    source_audio_path=str(second),
                    lyric_path=str(root / "second.lrc"),
                    output_dir=str(root),
                    vocal_path=str(second),
                    accompaniment_path=str(second),
                    ass_path=str(ass),
                    alignment_report_path=str(ass),
                    pronunciation_report_path=None,
                )

            argv = ["main.py", "-i", str(first), str(second), "-o", str(root / "out")]
            with patch.object(sys, "argv", argv), patch(
                "ai_karaoke.interfaces.cli.process_song", side_effect=fake_process
            ):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    with self.assertRaises(SystemExit) as exit_result:
                        cli_main.main()
        self.assertEqual(exit_result.exception.code, 1)
        self.assertEqual(seen, ["first", "second"])
