import argparse
import html
import json
import os
import re
import traceback
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from kara_style import DEFAULT_KARA_ACCENT_COLOR, DEFAULT_KARA_PRIMARY_COLOR, validate_distinct_kara_colors

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large", "turbo")
DEFAULT_WHISPER_MODEL = "medium"
WHISPER_MODEL_VRAM = {
    "tiny": "~1GB",
    "base": "~1GB",
    "small": "~2GB",
    "medium": "~5GB",
    "large": "~10GB",
    "turbo": "~6GB",
}
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_gui.html")
ASS_COLOR_PATTERN = re.compile(r"^&[Hh][0-9A-Fa-f]{8}$")
TIME_INT_PATTERN = re.compile(r"^\d+$")

PROGRESS_STEPS = {
    "pending": ("等待开始", 0),
    "validating": ("正在检查输入参数...", 10),
    "resolving_lyric": ("正在定位歌词文件...", 20),
    "separating": ("正在分离人声和伴奏...", 45),
    "aligning": ("正在对齐歌词并生成字幕...", 80),
    "completed": ("任务完成", 100),
    "failed": ("任务失败", 100),
}

class JobState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.running = False
            self.status = "pending"
            self.stage = PROGRESS_STEPS["pending"][0]
            self.progress = PROGRESS_STEPS["pending"][1]
            self.history: list[str] = []
            self.result_path = ""
            self.error = ""

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "status": self.status,
                "stage": self.stage,
                "progress": self.progress,
                "history": list(self.history),
                "result_path": self.result_path,
                "error": self.error,
            }

    def start(self) -> None:
        with self._lock:
            self.running = True
            self.status = "running"
            self.stage = PROGRESS_STEPS["pending"][0]
            self.progress = PROGRESS_STEPS["pending"][1]
            self.history = []
            self.result_path = ""
            self.error = ""

    def update_stage(self, key: str) -> None:
        stage_text, percent = PROGRESS_STEPS[key]
        with self._lock:
            self.stage = stage_text
            self.progress = percent
            self.history.append(stage_text)

    def complete(self, ass_path: str) -> None:
        stage_text, percent = PROGRESS_STEPS["completed"]
        with self._lock:
            self.status = "completed"
            self.running = False
            self.stage = stage_text
            self.progress = percent
            self.result_path = ass_path
            self.history.append(stage_text)

    def fail(self, err: str) -> None:
        stage_text, percent = PROGRESS_STEPS["failed"]
        with self._lock:
            self.status = "failed"
            self.running = False
            self.stage = stage_text
            self.progress = percent
            self.error = err
            self.history.append(f"{stage_text}: {err}")


JOB_STATE = JobState()
PICKER_LOCK = threading.Lock()


def _clean_input_path(raw: str) -> str:
    value = raw.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    if value.lower().startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path or "")
        if os.name == "nt":
            # On Windows file URI may look like /C:/path/to/file.
            if re.match(r"^/[A-Za-z]:/", value):
                value = value[1:]
            value = value.replace("/", "\\")

    value = os.path.expandvars(os.path.expanduser(value))
    if value:
        value = os.path.normpath(value)
    return value


def _open_native_picker(field: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        raise RuntimeError(f"无法打开文件选择器: {e}") from e

    with PICKER_LOCK:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        try:
            if field == "song":
                return filedialog.askopenfilename(title="选择歌曲文件")
            if field == "lyric":
                return filedialog.askopenfilename(
                    title="选择歌词文件",
                    filetypes=[("LRC Files", "*.lrc *.LRC"), ("All Files", "*.*")],
                )
            if field == "inst":
                return filedialog.askopenfilename(title="选择官方伴奏文件")
            if field == "output":
                return filedialog.askdirectory(title="选择输出目录")
            raise ValueError(f"不支持的选择字段: {field}")
        finally:
            root.destroy()


def _resolve_lyric(lyric_path: str | None, song_path: str, base_name: str) -> str:
    if lyric_path:
        return lyric_path

    song_dir = os.path.dirname(os.path.abspath(song_path))
    fallback_candidates = [
        os.path.join(song_dir, f"{base_name}.lrc"),
        os.path.join(song_dir, f"{base_name}.LRC"),
    ]
    resolved = next((p for p in fallback_candidates if os.path.exists(p)), None)
    if resolved:
        return resolved
    raise FileNotFoundError("未提供歌词文件，且未在歌曲同目录发现同名 .lrc/.LRC 文件。")


def _render_form_page() -> str:
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        tpl = f.read()

    model_options = []
    for name in WHISPER_MODELS:
        selected = " selected" if name == DEFAULT_WHISPER_MODEL else ""
        vram = WHISPER_MODEL_VRAM.get(name, "")
        label = f"{name} ({vram})" if vram else name
        model_options.append(f'<option value="{name}"{selected}>{label}</option>')

    return (
        tpl.replace("{{MODEL_OPTIONS}}", "\n".join(model_options))
        .replace("{{DEFAULT_PRIMARY_COLOR}}", html.escape(DEFAULT_KARA_PRIMARY_COLOR))
        .replace("{{DEFAULT_ACCENT_COLOR}}", html.escape(DEFAULT_KARA_ACCENT_COLOR))
    )


def _normalize_web_color(raw_color: str, arg_name: str) -> str:
    token = raw_color.strip()
    if not ASS_COLOR_PATTERN.match(token):
        raise ValueError(f"{arg_name} 必须为 ASS 颜色格式，例如 &H00FFFFFF")
    return token.upper()


def _run_pipeline(form: dict[str, str]) -> str:
    JOB_STATE.update_stage("validating")

    song = _clean_input_path(form.get("song", ""))
    lyric = _clean_input_path(form.get("lyric", ""))
    lyric = lyric or None
    output = _clean_input_path(form.get("output", ""))
    inst = _clean_input_path(form.get("inst", ""))
    inst = inst or None
    lang = form.get("lang", "").strip() or "ja"
    whisper_model = form.get("whisper_model", "").strip() or DEFAULT_WHISPER_MODEL
    kara_advance_ms_raw = form.get("kara_advance_ms", "3000").strip()
    kara_sep_threshold_ms_raw = form.get("kara_sep_threshold_ms", "10000").strip()
    primary_color_raw = form.get("kara_primary_color", DEFAULT_KARA_PRIMARY_COLOR).strip()
    accent_color_raw = form.get("kara_accent_color", DEFAULT_KARA_ACCENT_COLOR).strip()

    if not song:
        raise ValueError("请输入歌曲路径。")
    if not os.path.exists(song):
        raise FileNotFoundError(f"歌曲不存在: {song}")
    if lyric and not os.path.exists(lyric):
        raise FileNotFoundError(f"LRC 不存在: {lyric}")
    if inst and not os.path.exists(inst):
        raise FileNotFoundError(f"官方伴奏不存在: {inst}")
    if not output:
        raise ValueError("请输入输出目录。")
    if whisper_model not in WHISPER_MODELS:
        raise ValueError(f"Whisper 模型非法: {whisper_model}")

    if not TIME_INT_PATTERN.match(kara_advance_ms_raw):
        raise ValueError("kara 提前时间必须是非负整数。")
    if not TIME_INT_PATTERN.match(kara_sep_threshold_ms_raw):
        raise ValueError("kara 分段阈值必须是非负整数。")

    kara_advance_ms = int(kara_advance_ms_raw)
    kara_sep_threshold_ms = int(kara_sep_threshold_ms_raw)

    if kara_advance_ms < 0 or kara_sep_threshold_ms < 0:
        raise ValueError("kara 时间参数不能为负数。")

    primary_color = _normalize_web_color(primary_color_raw, "kara_primary_color")
    accent_color = _normalize_web_color(accent_color_raw, "kara_accent_color")
    validate_distinct_kara_colors(primary_color, accent_color)

    # Lazy import heavy pipeline modules so web GUI startup does not depend on
    # torch/torchaudio/stable-whisper import success.
    from separator import separate_audio
    from aligner import generate_karaoke_ass

    base_name = os.path.splitext(os.path.basename(song))[0]
    os.makedirs(output, exist_ok=True)

    JOB_STATE.update_stage("resolving_lyric")
    resolved_lyric = _resolve_lyric(lyric, song, base_name)

    JOB_STATE.update_stage("separating")
    vocal_path, _ = separate_audio(
        audio_path=song,
        output_path=output,
        base_name=base_name,
        official_inst_path=inst,
    )

    JOB_STATE.update_stage("aligning")
    ass_path = generate_karaoke_ass(
        vocal_path=vocal_path,
        lrc_path=resolved_lyric,
        output_path=output,
        base_name=base_name,
        lang=lang,
        whisper_model=whisper_model,
        kara_advance_time_ms=kara_advance_ms,
        kara_sep_threshold_ms=kara_sep_threshold_ms,
        kara_primary_color=primary_color,
        kara_accent_color=accent_color,
    )
    return ass_path


def _job_worker(form: dict[str, str]) -> None:
    try:
        ass_path = _run_pipeline(form)
        JOB_STATE.complete(ass_path)
    except Exception as e:
        JOB_STATE.fail(str(e))


class KaraokeWebHandler(BaseHTTPRequestHandler):
    def _send_html(self, body: str, code: int = HTTPStatus.OK) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, code: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/":
                self._send_html(_render_form_page())
                return
            if path == "/healthz":
                self._send_json({"ok": True, "status": "alive"})
                return
            if path == "/status":
                self._send_json(JOB_STATE.snapshot())
                return
            if path == "/reset":
                if JOB_STATE.snapshot()["running"]:
                    self._send_json({"ok": False, "error": "任务运行中，无法重置。"}, code=HTTPStatus.CONFLICT)
                    return
                JOB_STATE.reset()
                self._send_json({"ok": True})
                return
            if path == "/pick":
                query = parse_qs(parsed.query, keep_blank_values=True)
                field = (query.get("field", [""])[0] or "").strip()
                try:
                    selected = _open_native_picker(field)
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)}, code=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json({"ok": True, "path": selected or ""})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
        except Exception as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Server error: {e}")
            traceback.print_exc()

    def do_POST(self) -> None:
        try:
            if self.path != "/run":
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return

            if JOB_STATE.snapshot()["running"]:
                self._send_json(
                    {"ok": False, "error": "已有任务在运行，请等待当前任务结束。"},
                    code=HTTPStatus.CONFLICT,
                )
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(content_length).decode("utf-8", errors="replace")
            params = {k: v[0] for k, v in parse_qs(payload, keep_blank_values=True).items()}

            JOB_STATE.start()
            worker = threading.Thread(target=_job_worker, args=(params,), daemon=True)
            worker.start()
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, code=HTTPStatus.INTERNAL_SERVER_ERROR)
            traceback.print_exc()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web GUI for ai_karaoke")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=7860, help="Bind port")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), KaraokeWebHandler)
    print(f"Web GUI running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
