"""
Challenge Scaffolder
Write a challenge tree from a catalog base.
"""

from importlib.resources    import as_file, files
from pathlib                import Path
from hg.catalog             import ID_HINT, ID_RE, Base, challenge_image_name, require_template
from hg.errors              import ToolError
import shutil
import yaml


## Constants -----------------------------------------------------------------
GITIGNORE = "dist/\n.hg/\n"
WRITEUP   = """# Writeup

Replace this stub with the intended solution path.
"""

SOLVE_SH = """#!/bin/sh
# Replace this stub. It must print the flag to stdout.
# The verifier sets CHALLENGE_HOST, CHALLENGE_PORT, and CHALLENGE_CONNECTION.
# It also sets CHALLENGE_FLAG_<ID> for each dynamic flag of this run.
echo "solve.sh is a stub" >&2
exit 1
"""


## Helper Functions ------------------------------------------------------------
def repo_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "hg").is_dir():
            return parent
    return None


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def ensure_new_dir(dest: Path) -> None:
    """
    Reject a destination inside this repo, or one that is not empty.

    Shared by `hg challenge scaffold` and `hg contest scaffold` so both refuse the
    same ways to overwrite or misplace a tree.
    """
    root = repo_root()
    if root is not None and _inside(dest, root):
        raise ToolError(f"destination is inside this repo: {dest}")
    if dest.exists():
        if not dest.is_dir():
            raise ToolError(f"destination exists and is not a directory: {dest}")
        if any(dest.iterdir()):
            raise ToolError(f"destination is not empty: {dest}")


def _manifest(base: Base, challenge_id: str, title: str, author: str) -> dict:
    """Scaffold of the challenge's manifest."""
    flag = {
        "id":       "flag",
        "type":     "dynamic",
        "path":     base.flag_path,
        "owner":    base.flag_owner,
        "mode":     base.flag_mode,
        "service":  base.member,
    }

    if base.runtime == "scenario":
        context_root = "hosts"
    else:
        context_root = "services"

    member = {
        "name": base.member,
        "build": {
            "context":   f"{context_root}/{base.member}",
            "dockerfile": "Dockerfile",
        },
        "image": {
            "name": challenge_image_name(
                base.category,
                challenge_id,
                base.member,
                multiple = False,
            )
        },
        "ports": [
            {
                "name":      port.name,
                "container": port.container,
                "protocol":  port.protocol,
                "publish":   port.publish,
            }
            for port in base.ports
        ],
        "resources": {"memoryMb": base.memory_mb, "cpuMhz": base.cpu_mhz},
    }

    health: dict = {"type": base.health_type}
    if base.health_type == "ready-file":
        health["path"] = base.health_path
    elif base.health_type == "http":
        health["port"] = base.health_port
        health["path"] = base.health_path or "/"
    else:
        health["port"] = base.health_port

    member["health"] = health
    spec: dict = {
        "runtime":  base.runtime,
        "spawn":    "image",
        "platform": list(base.platforms),
        "flags":    [flag],
        "scripts":  {"test": "private/solve.sh"},
        "networks": [
            {
                "name":     "default",
                "internal": True,
                "members":  [base.member],
            }
        ],
        "player": {
            "entry":        f"{base.member}.{base.entry_port}",
            "connection":   base.connection,
        },
    }
    if base.placement:
        spec["placement"] = base.placement
    if base.runtime == "scenario":
        member["users"] = [{"name": "player", "passwordFrom": "generated"}]
        spec["hosts"]   = [member]
    else:
        spec["services"] = [member]

    return {
        "apiVersion": "hg.challenge/v1",
        "kind":       "Challenge",
        "metadata": {
            "id":           challenge_id,
            "title":        title,
            "category":     base.category,
            "description":  "Replace this description.",
            "difficulty":   3,
            "author":       author,
            "value":        100,
            "state":        "hidden",
            "version":      "1",
        },
        "spec": spec,
    }


def _copy_template(template: str, dest: Path) -> None:
    source = files("hg") / "templates" / template
    if not source.is_dir():
        raise ToolError(f"missing template directory: {template}")
    with as_file(source) as source_dir:
        for path in source_dir.rglob("*"):
            if not path.is_file():
                continue
            target = dest / path.relative_to(source_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            shutil.copymode(path, target)


## Main Functions --------------------------------------------------------------
def create_challenge(
    base_name:          str,
    challenge_id:       str,
    dest:               Path,
    title:              str     | None = None,
    author:             str            = "author",
) -> Path:
    # ensure the slug is valid, author should ensure the slug is available
    if not ID_RE.fullmatch(challenge_id):
        raise ToolError(
            f"id must match {ID_HINT} "
            f"(got {challenge_id!r})"
        )

    base = require_template(base_name)
    dest = dest.expanduser().resolve()
    ensure_new_dir(dest)
    if base.runtime == "scenario":
        context = dest / "hosts" / base.member
    else:
        context = dest / "services" / base.member

    dest.mkdir(parents=True, exist_ok=True)
    private = dest / "private"
    private.mkdir()
    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dest / "challenge.yaml").write_text(
        yaml.safe_dump(
            _manifest(base, challenge_id, title or challenge_id, author),
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    (private / "writeup.md").write_text(WRITEUP, encoding="utf-8")
    solve = private / "solve.sh"
    solve.write_text(SOLVE_SH, encoding="utf-8")
    solve.chmod(0o755)
    _copy_template(base.template or "", context)
    return dest
