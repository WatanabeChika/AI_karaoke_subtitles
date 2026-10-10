"""Thread-safe batch job state and sequential processing worker."""

from __future__ import annotations

import gc
import os
import threading
import traceback
import uuid
from contextlib import contextmanager

from ai_karaoke.application.pipeline import (
    PipelineConfig,
    SongArtifacts,
    SongRequest,
    process_song,
)


def _localized_stage(message: str) -> str:
    translations = {
        "Separating vocals and accompaniment": "分离人声与伴奏",
        "Parsing lyrics and loading audio": "解析歌词并载入音频",
        "Checking Japanese pronunciation": "检查日语读音",
        "Skipping pronunciation ASR": "跳过听音纠音",
        "Rendering reports and ASS": "生成报告与 ASS",
    }
    if message.startswith("Aligning lyrics"):
        return "对齐歌词" + message[len("Aligning lyrics"):]
    return translations.get(message, message)


class BatchJob:
    def __init__(self, requests: list[SongRequest]) -> None:
        self.id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self.status = "running"
        self.error = ""
        self.current_index = 0
        self.items = [
            {
                "id": str(index),
                "title": request.base_name,
                "status": "pending",
                "stage": "等待处理",
                "progress": 0,
                "error": "",
            }
            for index, request in enumerate(requests)
        ]
        self._artifacts: dict[str, SongArtifacts] = {}

    def begin_item(self, index: int) -> None:
        with self._lock:
            self.current_index = index
            self.items[index].update(
                status="running", stage="准备处理", progress=0, error=""
            )

    def update_stage(self, item_id: str, stage: str, progress: int) -> None:
        with self._lock:
            item = self.items[int(item_id)]
            item["stage"] = stage
            item["progress"] = max(item["progress"], min(100, progress))

    def complete_item(self, item_id: str, artifacts: SongArtifacts) -> None:
        with self._lock:
            self.items[int(item_id)].update(
                status="completed", stage="处理完成，等待审核", progress=100
            )
            self._artifacts[item_id] = artifacts

    def fail_item(self, item_id: str, error: str) -> None:
        with self._lock:
            self.items[int(item_id)].update(
                status="failed", stage="处理失败", progress=100, error=error
            )

    def finish(self, fatal_error: str = "") -> None:
        with self._lock:
            succeeded = sum(item["status"] == "completed" for item in self.items)
            failed = sum(item["status"] == "failed" for item in self.items)
            if fatal_error:
                self.status = "failed"
                self.error = fatal_error
            elif succeeded and failed:
                self.status = "partial"
            elif succeeded:
                self.status = "completed"
            else:
                self.status = "failed"

    def snapshot(self) -> dict:
        with self._lock:
            items = []
            for item in self.items:
                copy = dict(item)
                artifacts = self._artifacts.get(item["id"])
                if artifacts:
                    copy["artifacts"] = {
                        "ass_name": os.path.basename(artifacts.ass_path),
                        "audio_name": os.path.basename(artifacts.source_audio_path),
                        "has_pronunciation_report": bool(
                            artifacts.pronunciation_report_path
                            and os.path.isfile(artifacts.pronunciation_report_path)
                        ),
                    }
                items.append(copy)
            progress = round(sum(item["progress"] for item in items) / len(items)) if items else 0
            return {
                "job_id": self.id,
                "status": self.status,
                "running": self.status == "running",
                "progress": progress,
                "completed": sum(item["status"] == "completed" for item in items),
                "failed": sum(item["status"] == "failed" for item in items),
                "total": len(items),
                "current_index": self.current_index,
                "items": items,
                "error": self.error,
            }

    def artifact(self, item_id: str, kind: str) -> str | None:
        with self._lock:
            artifacts = self._artifacts.get(item_id)
            if not artifacts:
                return None
            paths = {
                "source": artifacts.source_audio_path,
                "vocal": artifacts.vocal_path,
                "ass": artifacts.ass_path,
                "alignment_report": artifacts.alignment_report_path,
                "pronunciation_report": artifacts.pronunciation_report_path,
            }
            path = paths.get(kind)
            return path if path and os.path.isfile(path) else None

    def archive_files(self, scope: str) -> list[tuple[str, str]]:
        if scope not in ("ass", "all"):
            raise ValueError("未知的下载范围")
        with self._lock:
            files: list[tuple[str, str]] = []
            for artifacts in self._artifacts.values():
                paths = [artifacts.ass_path]
                if scope == "all":
                    paths = [artifacts.vocal_path, artifacts.accompaniment_path, artifacts.ass_path]
                for path in paths:
                    if path and os.path.isfile(path):
                        archive_name = os.path.basename(path)
                        if scope == "all":
                            archive_name = f"{artifacts.title}/{archive_name}"
                        files.append((archive_name, path))
            return files


class JobRegistry:
    def __init__(self, keep: int = 8) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, BatchJob] = {}
        self._order: list[str] = []
        self.keep = keep

    def create(self, requests: list[SongRequest]) -> BatchJob:
        with self._lock:
            if any(job.snapshot()["running"] for job in self._jobs.values()):
                raise RuntimeError("已有任务在运行，请等待当前批次结束")
            job = BatchJob(requests)
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self.keep:
                self._jobs.pop(self._order.pop(0), None)
            return job

    def get(self, job_id: str | None) -> BatchJob | None:
        with self._lock:
            if not job_id:
                job_id = self._order[-1] if self._order else None
            return self._jobs.get(job_id) if job_id else None


JOB_REGISTRY = JobRegistry()


class WebJobReporter:
    def __init__(self, job: BatchJob, item_id: str):
        self.job = job
        self.item_id = item_id

    @contextmanager
    def stage(self, current: int, total: int, message: str):
        stage = _localized_stage(message)
        self.job.update_stage(self.item_id, stage, round((current - 1) * 100 / total))
        yield
        self.job.update_stage(self.item_id, stage, round(current * 100 / total))

    def info(self, _message: str) -> None:
        return

    def debug(self, _message: str) -> None:
        return


def release_runtime_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def job_worker(
    job: BatchJob,
    requests: list[SongRequest],
    output: str,
    config: PipelineConfig,
) -> None:
    try:
        for index, request in enumerate(requests):
            item_id = str(index)
            job.begin_item(index)
            try:
                artifacts = process_song(
                    request, output, config, reporter=WebJobReporter(job, item_id)
                )
            except Exception as exc:
                job.fail_item(item_id, str(exc))
            else:
                job.complete_item(item_id, artifacts)
            finally:
                release_runtime_memory()
        job.finish()
    except Exception as exc:
        job.finish(str(exc))
        traceback.print_exc()
