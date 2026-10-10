import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ai_karaoke.application.pipeline import PipelineConfig, SongArtifacts, SongRequest
from ai_karaoke.web.artifacts import atomic_write_ass
from ai_karaoke.web.forms import render_form_page
from ai_karaoke.web.handler import STATIC_ASSETS
from ai_karaoke.web.jobs import BatchJob, job_worker


class WebBatchTests(unittest.TestCase):
    def _artifacts(self, root: Path, request: SongRequest) -> SongArtifacts:
        song_dir = root / request.base_name
        song_dir.mkdir(exist_ok=True)
        ass = song_dir / f"{request.base_name}.ass"
        vocal = song_dir / f"{request.base_name}_vocal.wav"
        accomp = song_dir / f"{request.base_name}_accomp.wav"
        report = song_dir / f"{request.base_name}_alignment_report.json"
        ass.write_text(
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
            encoding="utf-8",
        )
        vocal.write_bytes(b"vocal")
        accomp.write_bytes(b"accomp")
        report.write_text("[]", encoding="utf-8")
        return SongArtifacts(
            title=request.base_name,
            source_audio_path=request.audio_path,
            lyric_path=str(root / f"{request.base_name}.lrc"),
            output_dir=str(song_dir),
            vocal_path=str(vocal),
            accompaniment_path=str(accomp),
            ass_path=str(ass),
            alignment_report_path=str(report),
            pronunciation_report_path=None,
        )

    def test_web_batch_keeps_independent_results_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_audio = root / "first.flac"
            second_audio = root / "second.flac"
            first_audio.write_bytes(b"first")
            second_audio.write_bytes(b"second")
            requests = [SongRequest(str(first_audio)), SongRequest(str(second_audio))]
            job = BatchJob(requests)

            def fake_process(request, output, config, reporter):
                if request.base_name == "second":
                    raise RuntimeError("alignment failed")
                with reporter.stage(1, 5, "Separating vocals and accompaniment"):
                    pass
                return self._artifacts(root, request)

            with patch("ai_karaoke.web.jobs.process_song", side_effect=fake_process), patch(
                "ai_karaoke.web.jobs.release_runtime_memory"
            ):
                job_worker(job, requests, str(root), PipelineConfig())

            snapshot = job.snapshot()
            self.assertEqual(snapshot["status"], "partial")
            self.assertEqual(
                [item["status"] for item in snapshot["items"]],
                ["completed", "failed"],
            )
            self.assertTrue(job.artifact("0", "ass").endswith("first.ass"))
            self.assertIsNone(job.artifact("1", "ass"))

    def test_ass_save_is_atomic_and_rejects_non_ass_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.ass"
            path.write_text("[Events]\nFormat: old\n", encoding="utf-8")
            atomic_write_ass(str(path), "[Events]\nFormat: new\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "[Events]\nFormat: new\n")
            with self.assertRaisesRegex(ValueError, "有效的 ASS"):
                atomic_write_ass(str(path), "not subtitles")
            self.assertEqual(path.read_text(encoding="utf-8"), "[Events]\nFormat: new\n")

    def test_batch_archives_ass_or_downloadable_outputs_without_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "song.flac"
            lyric = root / "song.lrc"
            source.write_bytes(b"source")
            lyric.write_text("[00:00.00]song\n", encoding="utf-8")
            request = SongRequest(str(source), str(lyric))
            artifacts = self._artifacts(root, request)
            pronunciation = root / "song" / "song_pronunciation_report.json"
            pronunciation.write_text("[]", encoding="utf-8")
            artifacts = replace(artifacts, pronunciation_report_path=str(pronunciation))
            job = BatchJob([request])
            job.complete_item("0", artifacts)

            self.assertEqual(job.archive_files("ass"), [("song.ass", artifacts.ass_path)])
            all_files = job.archive_files("all")
            self.assertEqual(
                {name for name, _path in all_files},
                {"song/song_vocal.wav", "song/song_accomp.wav", "song/song.ass"},
            )
            self.assertTrue(job.snapshot()["items"][0]["artifacts"]["has_pronunciation_report"])
            archived_paths = {path for _name, path in all_files}
            self.assertNotIn(str(source), archived_paths)
            self.assertNotIn(str(lyric), archived_paths)
            with self.assertRaisesRegex(ValueError, "下载范围"):
                job.archive_files("source")

    def test_web_form_uses_split_assets_and_exposes_both_batch_downloads(self):
        page = render_form_page()
        self.assertIn('href="/assets/styles.css"', page)
        self.assertIn('src="/assets/app.js"', page)
        self.assertIn('id="downloadAss"', page)
        self.assertIn('id="downloadEverything"', page)
        self.assertIn('id="furigana_whisper_model"', page)
        self.assertIn('value="large-v3" selected', page)
        self.assertNotIn('name="alignment_backend"', page)
        self.assertNotIn('name="ctc_aligner_model"', page)
        self.assertNotIn('name="qwen_aligner_model"', page)
        self.assertNotIn('name="whisper_model"', page)

    def test_split_frontend_dependencies_are_served(self):
        expected = {
            "api.js", "app.js", "ass_preview.js", "base.css", "form.css",
            "progress.css", "responsive.css", "review.css", "review.js",
            "styles.css", "ui.js",
        }
        self.assertTrue(expected <= {path.name for path in STATIC_ASSETS.values()})
        self.assertTrue(all(path.is_file() for path in STATIC_ASSETS.values()))
