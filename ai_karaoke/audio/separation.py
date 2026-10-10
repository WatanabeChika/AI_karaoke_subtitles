"""Separate a song with Mel-Band RoFormer without instrumental cancellation."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Tuple

from ai_karaoke.interfaces.console import (
    ConsoleReporter,
    ProgressReporter,
    configure_runtime_output,
    dependency_output_tail,
)


def separate_audio(
    audio_path: str,
    output_path: str,
    base_name: str,
    reporter: ProgressReporter | None = None,
    verbose: bool = False,
) -> Tuple[str, str]:
    import librosa
    import soundfile as sf

    output_dir = os.path.join(output_path, base_name)
    os.makedirs(output_dir, exist_ok=True)
    final_vocal = os.path.join(output_dir, f"{base_name}_vocal.wav")
    final_accomp = os.path.join(output_dir, f"{base_name}_accomp.wav")
    reporter = reporter or ConsoleReporter(verbose)
    with reporter.stage(1, 5, "Separating vocals and accompaniment"):
        with tempfile.TemporaryDirectory(prefix="karaoke_separation_") as temp_dir:
            input_dir = os.path.join(temp_dir, "input")
            result_dir = os.path.join(temp_dir, "result")
            os.makedirs(input_dir)
            mix, _ = librosa.load(audio_path, sr=44100, mono=False)
            sf.write(os.path.join(input_dir, "song.wav"), mix.T if mix.ndim == 2 else mix, 44100, subtype="FLOAT")

            process = subprocess.run(
                [sys.executable, "-m", "mel_band_roformer.inference", "--input_folder", input_dir, "--store_dir", result_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if process.returncode:
                diagnostic = dependency_output_tail(process.stdout)
                raise RuntimeError(f"Mel-Band RoFormer failed:\n{diagnostic}")
            reporter.debug(process.stdout)
            shutil.copyfile(os.path.join(result_dir, "song_vocals.wav"), final_vocal)
            shutil.copyfile(os.path.join(result_dir, "song_instrumental.wav"), final_accomp)

    reporter.info(f"Vocal: {final_vocal}")
    reporter.info(f"Accompaniment: {final_accomp}")
    return final_vocal, final_accomp


def main() -> None:
    parser = argparse.ArgumentParser(description="Separate audio with Mel-Band RoFormer")
    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("-b", "--base-name", required=True)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    configure_runtime_output(args.verbose)
    separate_audio(
        args.input,
        args.output,
        args.base_name,
        reporter=ConsoleReporter(args.verbose),
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
