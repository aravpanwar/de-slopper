import hashlib
import json
from pathlib import Path

from deslopper.fsutil import write_atomic


def digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class Marker:
    """Resume point for a directory run: which files are already swept."""

    def __init__(self, path: Path):
        self.path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.done: dict[str, str] = data if isinstance(data, dict) else {}

    def has(self, name: str, source: str) -> bool:
        return self.done.get(name) == digest(source)

    def record(self, name: str, source: str) -> None:
        self.done[name] = digest(source)
        write_atomic(self.path, json.dumps(self.done, indent=2).encode("utf-8"))
