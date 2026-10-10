"""HTTP routes for the local generation and subtitle review application."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import threading
import traceback
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ai_karaoke.web.artifacts import atomic_write_ass
from ai_karaoke.web.forms import open_native_picker, parse_submission, render_form_page
from ai_karaoke.web.jobs import JOB_REGISTRY, BatchJob, job_worker

GUI_DIR = Path(__file__).resolve().parent / "static"
STATIC_ASSETS = {
    "/assets/styles.css": GUI_DIR / "styles.css",
    "/assets/base.css": GUI_DIR / "base.css",
    "/assets/form.css": GUI_DIR / "form.css",
    "/assets/progress.css": GUI_DIR / "progress.css",
    "/assets/review.css": GUI_DIR / "review.css",
    "/assets/responsive.css": GUI_DIR / "responsive.css",
    "/assets/ass_preview.js": GUI_DIR / "ass_preview.js",
    "/assets/api.js": GUI_DIR / "api.js",
    "/assets/app.js": GUI_DIR / "app.js",
    "/assets/review.js": GUI_DIR / "review.js",
    "/assets/ui.js": GUI_DIR / "ui.js",
    "/assets/mark.svg": GUI_DIR / "mark.svg",
}
MAX_FORM_BYTES = 1 * 1024 * 1024
MAX_ASS_BYTES = 12 * 1024 * 1024


class KaraokeWebHandler(BaseHTTPRequestHandler):
    server_version = "AIKaraokeLocal/1.0"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        code: int = HTTPStatus.OK,
        disposition: str | None = None,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_html(self, body: str, code: int = HTTPStatus.OK) -> None:
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", code)

    def _send_json(self, payload: dict, code: int = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            code,
        )

    def _read_body(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > limit:
            raise ValueError("请求内容过大")
        return self.rfile.read(length)

    def _query_job_item(self, parsed) -> tuple[BatchJob, str]:
        query = parse_qs(parsed.query, keep_blank_values=True)
        job = JOB_REGISTRY.get((query.get("job_id", [""])[0] or "").strip())
        item_id = (query.get("item_id", [""])[0] or "").strip()
        if not job or not item_id.isdigit():
            raise FileNotFoundError("任务或歌曲不存在")
        return job, item_id

    def _send_file(self, path: str, download: bool = False) -> None:
        file_size = os.path.getsize(path)
        start, end = 0, max(0, file_size - 1)
        code = HTTPStatus.OK
        range_header = self.headers.get("Range", "")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            elif last:
                length = min(int(last), file_size)
                start = file_size - length
            if start > end or start >= file_size:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            code = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1 if file_size else 0
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if code == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        if download:
            filename = os.path.basename(path)
            self.send_header(
                "Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}"
            )
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        with open(path, "rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_html(render_form_page())
        elif path in STATIC_ASSETS:
            self._send_file(str(STATIC_ASSETS[path]))
        elif path == "/healthz":
            self._send_json({"ok": True, "status": "alive"})
        elif path == "/status":
            query = parse_qs(parsed.query, keep_blank_values=True)
            job = JOB_REGISTRY.get((query.get("job_id", [""])[0] or "").strip())
            self._send_json(
                job.snapshot() if job else {"status": "idle", "running": False, "items": []}
            )
        elif path == "/pick":
            query = parse_qs(parsed.query, keep_blank_values=True)
            selected = open_native_picker((query.get("field", [""])[0] or "").strip())
            self._send_json({"ok": True, "paths": selected})
        elif path == "/artifact":
            job, item_id = self._query_job_item(parsed)
            query = parse_qs(parsed.query, keep_blank_values=True)
            kind = (query.get("kind", [""])[0] or "").strip()
            artifact_path = job.artifact(item_id, kind)
            if not artifact_path:
                raise FileNotFoundError("产物不存在")
            self._send_file(artifact_path, download=query.get("download", ["0"])[0] == "1")
        elif path == "/ass":
            job, item_id = self._query_job_item(parsed)
            ass_path = job.artifact(item_id, "ass")
            if not ass_path:
                raise FileNotFoundError("ASS 文件不存在")
            self._send_json({"ok": True, "content": Path(ass_path).read_text(encoding="utf-8")})
        elif path == "/download-batch":
            self._send_batch_archive(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def _send_batch_archive(self, parsed) -> None:
        query = parse_qs(parsed.query, keep_blank_values=True)
        job = JOB_REGISTRY.get((query.get("job_id", [""])[0] or "").strip())
        if not job:
            raise FileNotFoundError("任务不存在")
        scope = (query.get("scope", ["ass"])[0] or "ass").strip()
        artifacts = job.archive_files(scope)
        if not artifacts:
            raise FileNotFoundError("暂无可下载的生成文件")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for archive_name, artifact_path in artifacts:
                bundle.write(artifact_path, arcname=archive_name)
        filename = "ai_karaoke_ass.zip" if scope == "ass" else "ai_karaoke_all_files.zip"
        self._send_bytes(
            archive.getvalue(),
            "application/zip",
            disposition=f"attachment; filename={filename}",
        )

    def do_GET(self) -> None:
        try:
            self._handle_get()
        except (BrokenPipeError, ConnectionResetError):
            return
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.NOT_FOUND)
        except (OSError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            traceback.print_exc()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        try:
            if urlparse(self.path).path != "/run":
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return
            payload = self._read_body(MAX_FORM_BYTES).decode("utf-8", errors="replace")
            form = {
                key: values[0]
                for key, values in parse_qs(payload, keep_blank_values=True).items()
            }
            requests, output, config = parse_submission(form)
            job = JOB_REGISTRY.create(requests)
            threading.Thread(
                target=job_worker,
                args=(job, requests, output, config),
                daemon=True,
            ).start()
            self._send_json({"ok": True, "job_id": job.id}, HTTPStatus.ACCEPTED)
        except RuntimeError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT)
        except (OSError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            traceback.print_exc()

    def do_PUT(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path != "/ass":
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return
            job, item_id = self._query_job_item(parsed)
            ass_path = job.artifact(item_id, "ass")
            if not ass_path:
                raise FileNotFoundError("ASS 文件不存在")
            atomic_write_ass(ass_path, self._read_body(MAX_ASS_BYTES).decode("utf-8"))
            self._send_json({"ok": True, "message": "字幕已保存"})
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.NOT_FOUND)
        except (OSError, UnicodeError, ValueError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            traceback.print_exc()
