"""
Publish a built challenge image to the author's public registry.

`hg challenge build` tags `challenges/<category>/<id>:local`. This module
retags that image under the author's Docker Hub namespace, pushes it with
the Docker credentials already on the machine, and records `image.registry`
and `image.digest` in `challenge.yaml`. It does not log in and it does not
store a Docker password.
"""

from collections.abc    import Callable, Mapping
from dataclasses        import dataclass
from pathlib            import Path
from hg.errors          import ToolError
from hg.lint            import MANIFEST_NAME, load_manifest
import json
import os
import re
import subprocess


Docker = Callable[[list[str]], tuple[int, str, str]]

_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_TAG       = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST    = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ImageRow:
    """One built member as `hg images list` prints it."""

    member:     str
    local:      str
    present:    bool
    reference:  str | None


@dataclass(frozen=True)
class Published:
    """One image pushed to the author's registry."""

    member:     str
    reference:  str


def default_docker(argv: list[str]) -> tuple[int, str, str]:
    """Run docker and return the exit code, stdout, and stderr."""
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def publish_name(logical: str, namespace: str) -> str:
    """
    Docker Hub repository for a logical challenge image.

    `challenges/web/web-note-01` becomes `<namespace>/challenges-web-web-note-01`.
    Hub repository names have one slash, so every `/` in the logical name
    becomes `-`.
    """
    return f"{namespace}/{logical.replace('/', '-')}"


def resolve_namespace(namespace: str | None, env: Mapping[str, str] | None = None) -> str:
    """The Hub user or org from `--namespace` or `HG_REGISTRY`."""
    source = os.environ if env is None else env
    raw = (namespace or source.get("HG_REGISTRY") or "").strip()
    if not raw:
        raise ToolError("pass --namespace or set HG_REGISTRY to the Docker Hub user or org")
    if not _NAMESPACE.fullmatch(raw):
        raise ToolError(
            f"namespace {raw!r} must be a Docker Hub user or org, such as xathrya"
        )
    return raw


def list_images(root: Path, docker: Docker | None = None) -> list[ImageRow]:
    """Each built member, its `:local` tag, and the published reference if pinned."""
    run = docker or default_docker
    manifest = load_manifest(root)
    rows: list[ImageRow] = []
    for member, logical, image in _built(manifest):
        local = f"{logical}:local"
        code, _stdout, _stderr = run(["docker", "image", "inspect", local])
        registry = image.get("registry") if isinstance(image.get("registry"), str) else None
        digest = image.get("digest") if isinstance(image.get("digest"), str) else None
        reference = None
        if registry and digest and _DIGEST.fullmatch(digest):
            reference = f"{registry}@{digest}"
        rows.append(ImageRow(member=member, local=local, present=code == 0, reference=reference))
    return rows


def push_images(
    root:       Path,
    namespace:  str | None = None,
    *,
    docker:     Docker | None = None,
    env:        Mapping[str, str] | None = None,
) -> list[Published]:
    """
    Tag each built image, push it, and write `image.registry` and `image.digest`.

    The manifest is edited only after every push has a registry digest.
    A missing `:local` tag or a failed push leaves `challenge.yaml` unchanged.
    """
    run = docker or default_docker
    hub = resolve_namespace(namespace, env)
    manifest = load_manifest(root)
    version = _version(manifest.get("metadata"))
    built = _built(manifest)

    locals_missing: list[str] = []
    for _member, logical, _image in built:
        local = f"{logical}:local"
        code, _stdout, _stderr = run(["docker", "image", "inspect", local])
        if code != 0:
            locals_missing.append(local)
    if locals_missing:
        listed = ", ".join(locals_missing)
        raise ToolError(f"{listed} is not present; run hg challenge build")

    published: list[Published] = []
    pins: list[tuple[str, str, str]] = []
    for member, logical, _image in built:
        local = f"{logical}:local"
        repo = publish_name(logical, hub)
        remote = f"{repo}:{version}"
        _check(run(["docker", "tag", local, remote]), f"docker tag failed for {remote}")
        _check(run(["docker", "push", remote]), f"docker push failed for {remote}")
        code, stdout, stderr = run([
            "docker", "image", "inspect", "--format", "{{json .RepoDigests}}", remote,
        ])
        _check((code, stdout, stderr), f"cannot read the registry digest of {remote}")
        digest = _repo_digest(stdout, repo)
        if digest is None:
            raise ToolError(f"docker push did not record a registry digest for {remote}")
        pins.append((logical, repo, digest))
        published.append(Published(member=member, reference=f"{repo}@{digest}"))

    path = root / MANIFEST_NAME
    text = path.read_text(encoding="utf-8")
    for logical, repo, digest in pins:
        text = _pin_image(text, logical, repo, digest)
    path.write_text(text, encoding="utf-8")
    return published


def _built(manifest: dict) -> list[tuple[str, str, dict]]:
    spec = manifest.get("spec")
    if not isinstance(spec, dict):
        raise ToolError("challenge.yaml needs spec")
    if spec.get("runtime") == "static":
        raise ToolError("a static challenge has no image to push")
    if spec.get("spawn") == "compose":
        raise ToolError(
            "spawn compose is not published by hg images push; pass --image to hg challenge push"
        )
    runtime = spec.get("runtime")
    if runtime == "scenario":
        members = spec.get("hosts")
    elif runtime == "container":
        members = spec.get("services")
    else:
        raise ToolError(f"runtime {runtime!r} has no image to push")
    if not isinstance(members, list):
        raise ToolError("no built image to push")

    built: list[tuple[str, str, dict]] = []
    for member in members:
        if not isinstance(member, dict) or not isinstance(member.get("build"), dict):
            continue
        image = member.get("image") if isinstance(member.get("image"), dict) else {}
        logical = image.get("name")
        name = member.get("name")
        if not isinstance(name, str) or not isinstance(logical, str) or not logical:
            raise ToolError("each built service needs a name and image.name")
        built.append((name, logical, image))
    if not built:
        raise ToolError("no built image to push")
    return built


def _version(metadata: object) -> str:
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not isinstance(version, str) or not _TAG.fullmatch(version):
        raise ToolError("metadata.version must be a Docker tag")
    return version


def _check(result: tuple[int, str, str], message: str) -> None:
    code, stdout, stderr = result
    if code == 0:
        return
    detail = (stderr or stdout).strip()
    if detail:
        raise ToolError(f"{message}\n{detail}")
    raise ToolError(message)


def _repo_digest(payload: str, repo: str) -> str | None:
    try:
        listed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(listed, list):
        return None
    for item in listed:
        if not isinstance(item, str) or "@" not in item:
            continue
        name, digest = item.rsplit("@", 1)
        if not _DIGEST.fullmatch(digest):
            continue
        if name == repo or name.endswith("/" + repo):
            return digest
    return None


def _pin_image(text: str, logical: str, registry: str, digest: str) -> str:
    """Insert or replace `registry` and `digest` under the image named `logical`."""
    newline = "\r\n" if "\r\n" in text else "\n"
    ends = text.endswith(("\n", "\r\n"))
    lines = text.splitlines()
    pattern = re.compile(
        rf"^(\s*)name:\s*(['\"]?){re.escape(logical)}\2\s*(?:#.*)?$"
    )
    matches = [index for index, line in enumerate(lines) if pattern.match(line)]
    if len(matches) != 1:
        raise ToolError(f"cannot find image.name {logical!r} in {MANIFEST_NAME}")

    index = matches[0]
    matched = pattern.match(lines[index])
    assert matched is not None
    indent = matched.group(1)
    image_at = _image_key(lines, index, indent)
    end = _block_end(lines, image_at)

    key = re.compile(rf"^{re.escape(indent)}(?:registry|digest):")
    kept: list[str] = []
    for cursor, line in enumerate(lines):
        if image_at < cursor < end and key.match(line):
            continue
        kept.append(line)

    name_at = next(cursor for cursor, line in enumerate(kept) if pattern.match(line))
    kept[name_at + 1:name_at + 1] = [
        f"{indent}registry: {registry}",
        f"{indent}digest: {digest}",
    ]
    body = newline.join(kept)
    if ends:
        body += newline
    return body


def _image_key(lines: list[str], index: int, indent: str) -> int:
    for cursor in range(index - 1, -1, -1):
        raw = lines[cursor]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = raw[:len(raw) - len(raw.lstrip())]
        if len(line_indent) < len(indent) and (stripped == "image:" or stripped.startswith("image:")):
            return cursor
        if len(line_indent) < len(indent):
            break
    raise ToolError(f"image.name is not under an image: key in {MANIFEST_NAME}")


def _block_end(lines: list[str], image_at: int) -> int:
    raw = lines[image_at]
    image_indent = raw[:len(raw) - len(raw.lstrip())]
    end = image_at + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or line.strip().startswith("#"):
            end += 1
            continue
        line_indent = line[:len(line) - len(line.lstrip())]
        if len(line_indent) <= len(image_indent):
            break
        end += 1
    return end
