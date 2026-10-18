"""
Remove the local challenge image.

Containers and networks created by `hg challenge run` are removed first 
so the image is not in use. 
Base images are left in place. 
A tag that is already gone is success.
"""

from pathlib    import Path
from hg.compose import load_compose, parse_services
from hg.errors  import ToolError
from hg.lint    import _relative_inside, require_lint
from hg.run     import Runner, default_runner, quiet_runner, stop_challenge


## Helper Functions ------------------------------------------------------------
def _members(spec: dict) -> list[dict]:
    if spec.get("runtime") == "scenario":
        return list(spec.get("hosts") or [])
    return list(spec.get("services") or [])


def _local_tag(member: dict) -> str | None:
    image = member.get("image") if isinstance(member.get("image"), dict) else {}
    if not isinstance(member.get("build"), dict):
        return None

    name = image.get("name")
    if not isinstance(name, str) or not name:
        return None
    return f"{name}:local"


def _remove_tag(tag: str, run: Runner, quiet: Runner, removed: list[str]) -> None:
    if quiet(["docker", "image", "inspect", tag]) != 0:
        return
    if run(["docker", "image", "rm", tag]) != 0:
        raise ToolError(f"docker image rm failed for {tag}")
    removed.append(tag)


## Main Functions --------------------------------------------------------------
def clean_challenge(root: Path, runner: Runner | None = None) -> list[str]:
    data = require_lint(root)
    spec = data["spec"]
    if spec.get("runtime") == "static":
        return []

    if runner is None:
        run:   Runner = default_runner
        quiet: Runner = quiet_runner
    else:
        run = quiet = runner

    # Stop the same graph run would create, then drop the images it built.
    stop_challenge(root, runner)

    if spec.get("spawn") == "compose":
        compose = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
        rel  = compose.get("file") or "compose.yaml"
        path = _relative_inside(root, rel)
        if path is None:
            raise ToolError(f"compose file {rel!r} must stay inside the challenge root")

        services, _errors = parse_services(load_compose(path))
        removed: list[str] = []
        for service in services:
            if service.built and service.image:
                _remove_tag(f"{service.image}:local", run, quiet, removed)
        return removed

    removed = []
    for member in _members(spec):
        tag = _local_tag(member)
        if tag is None:
            continue
        _remove_tag(tag, run, quiet, removed)
    return removed
