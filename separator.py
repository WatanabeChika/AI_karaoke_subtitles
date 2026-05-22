import argparse
import os
import shutil
import subprocess
import sys
from typing import Tuple

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import correlate


CALC_SR = 44100


def _build_subprocess_env() -> dict:
    env = os.environ.copy()
    python_dir = os.path.dirname(sys.executable)
    env_root = os.path.dirname(python_dir)
    candidates = [
        python_dir,
        os.path.join(env_root, "Scripts"),
        os.path.join(env_root, "Library", "bin"),
        os.path.join(env_root, "Library", "usr", "bin"),
        env_root,
    ]
    existing = [p for p in candidates if os.path.isdir(p)]
    old_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(existing + ([old_path] if old_path else []))
    return env


def _build_demucs_command(output_dir: str, temp_audio: str) -> list[str]:
    cli_name = "demucs.exe" if os.name == "nt" else "demucs"
    if shutil.which(cli_name) or shutil.which("demucs"):
        runner = "demucs"
        return [runner, "--two-stems=vocals", "-o", output_dir, "-n", "htdemucs", temp_audio]

    # Fallback for isolated runtime where script entrypoint may be missing from PATH.
    return [sys.executable, "-m", "demucs.separate", "--two-stems=vocals", "-o", output_dir, "-n", "htdemucs", temp_audio]


def _ensure_stereo_channels(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return np.vstack((audio, audio))
    return audio


def _align_native_track(native_inst: np.ndarray, native_shift: int) -> np.ndarray:
    if native_inst.ndim == 1:
        native_inst = native_inst.reshape(-1, 1)

    if native_shift > 0:
        padding = np.zeros((native_shift, native_inst.shape[1]), dtype=native_inst.dtype)
        return np.vstack((padding, native_inst))
    if native_shift < 0:
        return native_inst[-native_shift:, :]
    return native_inst


def _align_calc_track(inst: np.ndarray, shift: int) -> np.ndarray:
    if shift > 0:
        return np.pad(inst, ((0, 0), (shift, 0)), mode="constant")
    if shift < 0:
        return inst[:, -shift:]
    return inst


def extract_vocal_by_phase(
    original_path: str,
    inst_path: str,
    vocal_out_path: str,
    accomp_out_path: str,
) -> None:
    print("\n[1/4] Official instrumental detected, running phase cancellation...")

    orig, _ = librosa.load(original_path, sr=CALC_SR, mono=False)
    inst, _ = librosa.load(inst_path, sr=CALC_SR, mono=False)

    orig = _ensure_stereo_channels(orig)
    inst = _ensure_stereo_channels(inst)

    calc_length = min(CALC_SR * 30, orig.shape[1], inst.shape[1])
    orig_mono = librosa.to_mono(orig[:, :calc_length])
    inst_mono = librosa.to_mono(inst[:, :calc_length])

    print("      calculating offset...")
    correlation = correlate(orig_mono, inst_mono, mode="full", method="fft")
    shift = np.argmax(correlation) - (len(inst_mono) - 1)
    shift_sec = shift / CALC_SR

    print("      aligning native instrumental...")
    native_inst, native_sr = sf.read(inst_path)
    native_shift = int(round(shift_sec * native_sr))
    native_inst_aligned = _align_native_track(native_inst, native_shift)
    sf.write(accomp_out_path, native_inst_aligned, native_sr)

    print("      extracting vocal for alignment...")
    inst_aligned = _align_calc_track(inst, shift)

    min_len = min(orig.shape[1], inst_aligned.shape[1])
    orig_cut = orig[:, :min_len]
    inst_cut = inst_aligned[:, :min_len]

    rms_orig = np.sqrt(np.mean(orig_cut**2))
    rms_inst = np.sqrt(np.mean(inst_cut**2))
    amplitude_ratio = rms_orig / (rms_inst + 1e-10)

    vocal_extracted = orig_cut - (inst_cut * amplitude_ratio)
    sf.write(vocal_out_path, vocal_extracted.T, CALC_SR, subtype="PCM_16")
    print("      done")


def separate_audio(
    audio_path: str,
    output_path: str,
    base_name: str,
    official_inst_path: str | None = None,
) -> Tuple[str, str]:
    ext = os.path.splitext(audio_path)[1]
    output_dir = os.path.join(output_path, base_name)
    os.makedirs(output_dir, exist_ok=True)

    final_vocal = os.path.join(output_dir, f"{base_name}_vocal.wav")
    final_accomp = os.path.join(output_dir, f"{base_name}_accomp.wav")

    if official_inst_path and os.path.exists(official_inst_path):
        extract_vocal_by_phase(audio_path, official_inst_path, final_vocal, final_accomp)
    else:
        print("\n[1/4] No official instrumental, running Demucs...")
        temp_audio = os.path.join(output_dir, f"temp_process{ext}")
        shutil.copy(audio_path, temp_audio)
        demucs_cmd = _build_demucs_command(output_dir=output_dir, temp_audio=temp_audio)
        try:
            subprocess.run(demucs_cmd, check=True, env=_build_subprocess_env())
        except FileNotFoundError as e:
            raise RuntimeError(
                "Demucs command not found. Please ensure demucs is installed in the runtime environment."
            ) from e

        separated_dir = os.path.join(output_dir, "htdemucs", "temp_process")
        shutil.copy(os.path.join(separated_dir, "vocals.wav"), final_vocal)
        shutil.copy(os.path.join(separated_dir, "no_vocals.wav"), final_accomp)

        os.remove(temp_audio)
        shutil.rmtree(os.path.join(output_dir, "htdemucs"))
        print("      done")

    print(f"Vocal ready: {final_vocal}")
    print(f"Accompaniment ready: {final_accomp}")
    return final_vocal, final_accomp


def _run_self_tests() -> None:
    mono = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    stereo = _ensure_stereo_channels(mono)
    assert stereo.shape == (2, 3)

    native = np.array([[1.0], [2.0], [3.0]], dtype=np.float32)
    shifted_pos = _align_native_track(native, 2)
    assert shifted_pos.shape[0] == 5
    assert shifted_pos[0, 0] == 0.0 and shifted_pos[1, 0] == 0.0

    shifted_neg = _align_native_track(native, -1)
    assert shifted_neg.shape[0] == 2
    assert shifted_neg[0, 0] == 2.0

    inst = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
    calc_pos = _align_calc_track(inst, 1)
    assert calc_pos.shape[1] == 4 and calc_pos[0, 0] == 0.0

    calc_neg = _align_calc_track(inst, -1)
    assert calc_neg.shape[1] == 2 and calc_neg[0, 0] == 2.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Separate audio into vocal and accompaniment")
    parser.add_argument("--self-test", action="store_true", help="Run built-in tests")
    parser.add_argument("-i", "--input", help="Input audio path")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("-b", "--base-name", help="Output base name")
    parser.add_argument("--inst", default=None, help="Official instrumental path")
    args = parser.parse_args()

    if args.self_test:
        _run_self_tests()
        print("Self-test passed: separator")
        return

    if not args.input or not args.output or not args.base_name:
        parser.error("--input --output --base-name are required unless --self-test is used")

    separate_audio(
        audio_path=args.input,
        output_path=args.output,
        base_name=args.base_name,
        official_inst_path=args.inst,
    )


if __name__ == "__main__":
    main()
