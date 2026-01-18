from __future__ import annotations

from pathlib import Path
from typing import Optional


PLACEHOLDER_TOKENS = {"TODO"}


def read_text(path: Path, required: bool = False) -> str:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def is_effectively_empty(text: str) -> bool:
    if not text or not text.strip():
        return True
    lines = [line.strip() for line in text.splitlines()]
    content = [
        line
        for line in lines
        if line and not line.startswith("#") and line.upper() not in PLACEHOLDER_TOKENS
    ]
    return len(content) == 0


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
