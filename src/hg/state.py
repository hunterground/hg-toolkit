"""
Store the state during local deployment and testing.
The `.hg/run.json` reattach to a running graph.

A local run happens in one process and the solver may run in another, or hours
later, so the host port and the generated flag contents have to outlive the
process. The file is gitignored (the scaffold writes `.hg/`) and holds nothing
secret: the flag contents are per-instance canaries.

This module deals in plain dicts so both `run` and `solve` can use it without
depending on each other.
"""

from pathlib    import Path
from hg.errors  import ToolError
import json


STATE_DIR = ".hg"
STATE_FILE = "run.json"


def state_path(root: Path) -> Path:
    return root / STATE_DIR / STATE_FILE


def write_state(root: Path, payload: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_state(root: Path) -> dict | None:
    path = state_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError(f"{path.relative_to(root).as_posix()} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"{path.relative_to(root).as_posix()} must be a mapping")
    return data


def clear_state(root: Path) -> None:
    path = state_path(root)
    if path.is_file():
        path.unlink()
