from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class RunContext:
    role: str
    repo_root: Path
    fs_base: Path
    allow_write: bool
    shell_cwd: Path
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
