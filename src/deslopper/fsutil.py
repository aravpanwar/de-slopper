import os
from pathlib import Path


def write_atomic(path: Path, data: bytes) -> None:
    # A crash halfway through a write must never leave a truncated cache
    # entry or marker behind, because both are trusted on the next run.
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".part")
    scratch.write_bytes(data)
    os.replace(scratch, path)
