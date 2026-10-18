"""
Challenge Scaffolder
Write a challenge tree from a catalog base, or a static handout when the base is none.
"""

from collections.abc        import Callable
from importlib.resources    import as_file, files
from pathlib                import Path
from hg.catalog             import (
    CATEGORIES,
    ID_HINT,
    ID_RE,
    Base,
    challenge_image_name,
    hub_ref,
    require_template,
)
from hg.errors              import ToolError
import re
import shutil
import subprocess
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

# A static challenge has no running graph, so the stub must not mention the
# connection variables the container solver receives.
STATIC_SOLVE_SH = """#!/bin/sh
# Replace this stub. It must print the flag to stdout.
# A static challenge has no host, port, or connection.
echo "solve.sh is a stub" >&2
exit 1
"""

# Placeholder the static manifest and the handout share until the author edits both.
FLAG_CONTENT = "hg{replace-me}"

BUILD_SH = f"""#!/bin/sh
set -eu
mkdir -p dist
printf '%s\\n' 'Replace this handout. The flag is {FLAG_CONTENT}.' > dist/handout.txt
"""

# Sentinel for a challenge with no instance. It is not a catalog base.
NONE = "none"

# Same shape the schema requires for build.fromDigest.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def base_digest(tag: str) -> str:
    """
    Manifest-list digest of a catalog image on Docker Hub.

    `imagetools inspect` reports the index digest, so the pin resolves on
    every platform the scaffold lists. A digest taken after a `--platform`
    pull is one architecture and fails on the other.
    """
    hub = hub_ref(tag)
    argv = [
        "docker", "buildx", "imagetools", "inspect",
        "--format", "{{.Manifest.Digest}}",
        hub,
    ]
    try:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise ToolError(f"cannot read digest of {hub}: {exc.strerror or exc}") from exc
    digest = completed.stdout.strip()
    if completed.returncode != 0 or not DIGEST_RE.fullmatch(digest):
        detail = (completed.stderr or completed.stdout).strip()
        message = f"cannot read digest of {hub}"
        if detail:
            message = f"{message}\n{detail}"
        raise ToolError(message)
    return digest


def _manifest(base: Base, challenge_id: str, title: str, author: str, from_digest: str) -> dict:
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
            "context":    f"{context_root}/{base.member}",
            "dockerfile": "Dockerfile",
            # The catalog base tag. A later edit of the Dockerfile tag needs a new digest.
            "fromDigest": from_digest,
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


def _static_manifest(challenge_id: str, title: str, author: str, category: str) -> dict:
    """Scaffold of a challenge that has no instance."""
    return {
        "apiVersion": "hg.challenge/v1",
        "kind":       "Challenge",
        "metadata": {
            "id":           challenge_id,
            "title":        title,
            "category":     category,
            "description":  "Replace this description.",
            "difficulty":   3,
            "author":       author,
            "value":        100,
            "state":        "hidden",
            "version":      "1",
        },
        "spec": {
            "runtime": "static",
            "flags": [
                {"id": "flag", "type": "static", "content": FLAG_CONTENT},
            ],
            "scripts": {"test": "private/solve.sh", "build": "private/build.sh"},
            "files":   [{"name": "handout.txt", "source": "build"}],
        },
    }


def _write_tree(dest: Path, manifest: dict, solve_text: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    private = dest / "private"
    private.mkdir()
    (dest / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (dest / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    (private / "writeup.md").write_text(WRITEUP, encoding="utf-8")
    solve = private / "solve.sh"
    solve.write_text(solve_text, encoding="utf-8")
    solve.chmod(0o755)
    return private


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
    base_name:          str                         = NONE,
    challenge_id:       str                  | None = None,
    dest:               Path                 | None = None,
    title:              str                  | None = None,
    author:             str                         = "author",
    category:           str                  | None = None,
    digest_for:         Callable[[str], str] | None = None,
) -> Path:
    # ensure the slug is valid, author should ensure the slug is available
    if challenge_id is None or dest is None:
        raise ToolError("id and destination are required")
    if not ID_RE.fullmatch(challenge_id):
        raise ToolError(
            f"id must match {ID_HINT} "
            f"(got {challenge_id!r})"
        )

    if base_name == NONE:
        if category is None:
            raise ToolError("category is required when the base is none")
        if category not in CATEGORIES:
            known = ", ".join(sorted(CATEGORIES))
            raise ToolError(f"category {category!r} is not on the platform ({known})")
        dest = dest.expanduser().resolve()
        ensure_new_dir(dest)
        private = _write_tree(
            dest,
            _static_manifest(challenge_id, title or challenge_id, author, category),
            STATIC_SOLVE_SH,
        )
        build = private / "build.sh"
        build.write_text(BUILD_SH, encoding="utf-8")
        build.chmod(0o755)
        return dest

    if category is not None:
        raise ToolError("category is set by the base")

    base = require_template(base_name)
    dest = dest.expanduser().resolve()
    ensure_new_dir(dest)
    # Resolve the digest before creating the tree, so a hub failure leaves no partial directory.
    digest = (digest_for or base_digest)(base.image)
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise ToolError(f"base digest must be sha256:<64 hex> (got {digest!r})")
    if base.runtime == "scenario":
        context = dest / "hosts" / base.member
    else:
        context = dest / "services" / base.member

    _write_tree(
        dest,
        _manifest(base, challenge_id, title or challenge_id, author, digest),
        SOLVE_SH,
    )
    _copy_template(base.template or "", context)
    return dest
