"""
Build challenge images locally. Never pushes.
"""

from collections.abc    import Callable
from pathlib            import Path
from hg.catalog         import base_for_image, builder_for_image, hub_ref
from hg.compose         import load_compose, parse_services
from hg.errors          import ToolError
from hg.lint            import _relative_inside, parse_dockerfile, require_lint
import os
import subprocess


Runner = Callable[[list[str]], int]


## Helper Functions ---------------------------------------------------------------------------------
def host_platform() -> str:
    """get the host platform to adjust the image"""
    machine = os.uname().machine
    if machine in ("x86_64", "amd64"):
        return "linux/amd64"
    if machine in ("aarch64", "arm64"):
        return "linux/arm64"
    raise ToolError(f"unsupported host architecture: {machine}")


def default_runner(argv: list[str]) -> int:
    completed = subprocess.run(argv, check=False)
    return completed.returncode


def fetch_image(run: Runner, tag: str) -> bool:
    """
    Make a bare base tag available locally.

    Returns True when the tag is already present, or when it was pulled from
    Docker Hub and retagged. Returns False when the hub image is missing.
    A failed retag after a successful pull raises ToolError.
    """
    if run(["docker", "image", "inspect", tag]) == 0:
        return True

    hub = hub_ref(tag)
    if run(["docker", "pull", "--platform", host_platform(), hub]) != 0:
        return False
    if run(["docker", "tag", hub, tag]) != 0:
        raise ToolError(f"pulled {hub} but cannot tag it as {tag}")
    return True


def ensure_image(run: Runner, tag: str) -> None:
    """Inspect a base tag; pull it from Docker Hub and retag when missing."""
    if fetch_image(run, tag):
        return
    raise ToolError(f"cannot fetch base image {tag} from {hub_ref(tag)}")


def _run_script(root: Path, rel: str, run: Runner) -> None:
    if run(["sh", str((root / rel).resolve())]) != 0:
        raise ToolError(f"{rel} failed")


def _produce_files(root: Path, spec: dict) -> None:
    for item in spec.get("files") or []:
        if not isinstance(item, dict) or item.get("source") != "build":
            continue

        path = root / "dist" / item["name"]
        if not path.is_file():
            raise ToolError(f"dist/{item['name']} was not produced")


def _build_args(info, base, digest: str | None) -> list[str]:
    declared = set(info.arg_defaults)
    args: list[str] = []
    primary = base.build_args[0][0] if base.build_args else None
    for key, value in base.build_args:
        if key not in declared:
            continue
        if digest and key == primary:
            # The digest belongs to the hub repo. A bare name such as
            # base/web-flask@sha256:… is docker.io/base/web-flask, which
            # does not exist, so the pull is denied.
            name = value.split(":", 1)[0]
            value = f"{hub_ref(name)}@{digest}"
        args.append(f"{key}={value}")
    return args


def _build_image(
    run:            Runner,
    *,
    context:        Path,
    dockerfile:     Path,
    tag:            str,
    digest:         str     | None,
    platform:       str,
) -> None:
    info  = parse_dockerfile(dockerfile.read_text(encoding="utf-8"))
    image = info.from_images[-1]
    base  = base_for_image(image)
    if base is None:
        raise ToolError(f"FROM {image!r} is not an allowlisted base tag")

    # Compile stages use bare builder tags. Pull and retag those from the hub.
    # Upstream images and scratch stay as written.
    for stage in info.from_images[:-1]:
        if builder_for_image(stage) is not None:
            ensure_image(run, stage)

    # The Dockerfile may pin a version tag (base/ruby:2.7). Inspect that
    # image; the catalog entry's :latest tag is the unpinned default.
    for inspect_tag in (image, *base.extra_inspect):
        ensure_image(run, inspect_tag)

    argv = [
        "docker",
        "buildx",
        "build",
        "--load",
        "--platform",   platform,
        "--tag",        tag,
        "--file",       str(dockerfile),
    ]
    for item in _build_args(info, base, digest):
        argv.extend(["--build-arg", item])

    argv.append(str(context))
    if run(argv) != 0:
        raise ToolError(f"docker build failed for {tag}")


def _member_context(root: Path, member: dict) -> tuple[Path, Path]:
    build   = member["build"]
    context = (root / build["context"]).resolve()
    dockerfile_name = build.get("dockerfile") or "Dockerfile"
    return context, (context / dockerfile_name).resolve()


def build_challenge(
    root:           Path,
    runner:         Runner  | None = None,
    platform:       str     | None = None,
) -> list[str]:
    run  = runner or default_runner
    data = require_lint(root)
    spec = data["spec"]
    runtime = spec.get("runtime")
    scripts = spec.get("scripts", {})
    if scripts.get("build"):
        _run_script(root, scripts["build"], run)
        _produce_files(root, spec)
    if runtime == "static":
        return []

    platform = platform or host_platform()
    tags: list[str] = []
    if spec.get("spawn") == "compose":
        compose = spec.get("compose", {})
        rel = compose.get("file", "compose.yaml")

        path = _relative_inside(root, rel)
        if path is None:
            raise ToolError(f"compose file {rel!r} must stay inside the challenge root")

        services, errors = parse_services(load_compose(path))
        if errors:
            raise ToolError("\n".join(errors))

        digests = compose.get("fromDigest", {})
        for service in services:
            if not service.built or not service.image:
                if service.image:
                    if run(["docker", "image", "inspect", service.image]) != 0:
                        raise ToolError(f"{service.image} is not present locally")
                continue

            context = _relative_inside(root, service.build_context or "")
            if context is None:
                raise ToolError(f"compose service {service.name} build context is outside the challenge")

            dockerfile = (context / service.dockerfile).resolve()
            tag = f"{service.image}:local"
            _build_image(
                run,
                context     = context,
                dockerfile  = dockerfile,
                tag         = tag,
                digest      = digests.get(service.name),
                platform    = platform,
            )
            tags.append(tag)
        return tags

    members = spec.get("services") or spec.get("hosts") or []
    for member in members:
        image = member.get("image") if isinstance(member.get("image"), dict) else {}
        if not isinstance(member.get("build"), dict):
            digest = image.get("digest")
            ref = f"{image.get('name')}@{digest}"
            if run(["docker", "image", "inspect", ref]) != 0:
                raise ToolError(f"{ref} is not present locally")
            continue

        context, dockerfile = _member_context(root, member)
        tag = f"{image['name']}:local"
        _build_image(
            run,
            context     = context,
            dockerfile  = dockerfile,
            tag         = tag,
            digest      = (member.get("build") or {}).get("fromDigest"),
            platform    = platform,
        )
        tags.append(tag)
    return tags
