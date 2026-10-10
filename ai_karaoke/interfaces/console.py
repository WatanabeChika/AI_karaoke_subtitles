"""Consistent, quiet-by-default command-line output."""

from __future__ import annotations

import os
import sys
import threading
import time
import warnings
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol


class ProgressReporter(Protocol):
    def stage(self, current: int, total: int, message: str) -> AbstractContextManager[None]: ...

    def info(self, message: str) -> None: ...

    def debug(self, message: str) -> None: ...


def configure_runtime_output(verbose: bool = False) -> None:
    """Disable third-party progress bars and known non-actionable warnings."""
    if verbose:
        return
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    warnings.filterwarnings(
        "ignore",
        message=r"Dictionary\.create\(\) is deprecated, use Dictionary\.tokenizer\(\) instead",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*found in sys\.modules after import of package.*",
        category=RuntimeWarning,
    )


class ConsoleReporter:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    @contextmanager
    def stage(self, current: int, total: int, message: str):
        label = f"[{current}/{total}] {message}"
        started = time.monotonic()
        interactive = sys.stdout.isatty() and not self.verbose
        finished = threading.Event()
        worker = None

        if interactive:
            def show_elapsed():
                while not finished.is_set():
                    elapsed = int(time.monotonic() - started)
                    spinner = "|/-\\"[elapsed % 4]
                    print(f"\r{label}  {spinner} {elapsed // 60:02}:{elapsed % 60:02}", end="", flush=True)
                    finished.wait(0.2)

            worker = threading.Thread(target=show_elapsed, daemon=True)
            worker.start()
        else:
            print(label, flush=True)

        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            finished.set()
            if worker:
                worker.join()
            elapsed = time.monotonic() - started
            status = "OK" if succeeded else "FAILED"
            print(f"\r{label}  {status} ({elapsed:.1f}s)" if interactive else f"      {status} ({elapsed:.1f}s)", flush=True)

    def info(self, message: str) -> None:
        print(f"      {message}", flush=True)

    def batch_item(self, current: int, total: int, title: str) -> None:
        prefix = "\n" if current > 1 else ""
        print(f"{prefix}Song {current}/{total}: {title}", flush=True)

    def error(self, message: str) -> None:
        print(f"      ERROR: {message}", file=sys.stderr, flush=True)

    def debug(self, message: str) -> None:
        if self.verbose and message.strip():
            print(message.rstrip(), flush=True)


def dependency_output_tail(output: str, line_count: int = 30) -> str:
    """Return a readable diagnostic tail from carriage-return-heavy output."""
    normalized = output.replace("\r", "\n")
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    return "\n".join(lines[-line_count:])
