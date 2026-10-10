import re
import unittest
from pathlib import Path

from ai_karaoke.web.forms import render_form_page


class PackagingTests(unittest.TestCase):
    project_root = Path(__file__).resolve().parents[1]

    def test_whisper_toggle_matches_neighboring_control_height(self):
        page = render_form_page()
        styles = (
            self.project_root / "ai_karaoke" / "web" / "static" / "form.css"
        ).read_text(encoding="utf-8")
        self.assertIn('<span class="field-label">读音检查</span>', page)
        self.assertRegex(styles, r"(?s)\.toggle-panel\s*\{[^}]*height:\s*2\.85rem")
        self.assertNotIn("align-self: stretch", styles)

    def test_requirements_only_lists_direct_runtime_dependencies(self):
        requirements = [
            line.strip()
            for line in (self.project_root / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        names = {re.split(r"[<>= @\[]", line, maxsplit=1)[0].casefold() for line in requirements}
        self.assertTrue({"stable-ts", "librosa", "soundfile", "kanjiconv"} <= names)
        self.assertTrue({"pykakasi", "transformers", "melband-roformer-infer"} <= names)
        self.assertNotIn("numpy", names)
        self.assertNotIn("scipy", names)
        self.assertNotIn("pyyaml", names)
        self.assertNotIn("ml-collections", names)
        self.assertTrue(any("ctc-forced-aligner.git@64293cc" in line for line in requirements))

    def test_windows_installer_uses_packaged_app_and_shared_requirements(self):
        installer = (
            self.project_root / "windows_online_installer" / "install_online.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('$projectDirectories = @("ai_karaoke")', installer)
        self.assertIn('"requirements.txt"', installer)
        self.assertIn("pip install --upgrade -r", installer)
        self.assertIn("torch torchaudio torchcodec", installer)
        self.assertIn("python=3.10 ffmpeg git", installer)
        self.assertIn("Application import check", installer)
        self.assertIn("from ai_karaoke.web.handler import KaraokeWebHandler", installer)
