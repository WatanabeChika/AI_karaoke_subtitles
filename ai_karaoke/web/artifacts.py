"""Safe persistence helpers for user-edited subtitle artifacts."""

from __future__ import annotations

import os
import tempfile


def atomic_write_ass(path: str, content: str) -> None:
    if "[Events]" not in content or "Format:" not in content:
        raise ValueError("内容不像有效的 ASS 字幕：缺少 [Events] 或 Format")
    directory = os.path.dirname(path)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=directory,
            suffix=".ass.tmp",
            delete=False,
        ) as stream:
            temp_path = stream.name
            stream.write(content)
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
