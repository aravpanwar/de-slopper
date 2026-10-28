import hashlib
import json
import os
from pathlib import Path
from typing import Any

from deslopper.fsutil import write_atomic


def default_root() -> Path:
    override = os.environ.get("DESLOPPER_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "de-slopper"


class Cache:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else default_root()

    @staticmethod
    def key(prompt_version: str, model: str, kind: str, source: str,
            context: dict[str, Any]) -> str:
        # The context goes into the key because a prompt may use it (for
        # example the module name), and a hit must mean the same prompt.
        payload = json.dumps(
            [prompt_version, model, kind, source, context],
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        try:
            data = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
            reply = data["reply"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return reply if isinstance(reply, str) else None

    def put(self, key: str, reply: str) -> None:
        body = json.dumps({"reply": reply}, ensure_ascii=False)
        write_atomic(self.root / f"{key}.json", body.encode("utf-8"))
