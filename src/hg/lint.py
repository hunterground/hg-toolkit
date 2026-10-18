"""
Challenge Linting.
Local checks for a challenge tree.
"""

from dataclasses            import dataclass, field
from importlib.resources    import files
from pathlib                import Path
from jsonschema             import Draft202012Validator
from jsonschema.exceptions  import ValidationError
from hg.catalog             import MEMBER_RE, base_for_image, challenge_image_name
from hg.compose             import DIGEST_IMAGE, http_host, load_compose, parse_services
from hg.errors              import ToolError
from hg.scaffold            import SOLVE_SH, WRITEUP
import json
import re
import subprocess
import yaml


## Constants --------------------------------------------------------------------
# manifest filename at the challenge root
MANIFEST_NAME = "challenge.yaml"

_INSTRUCTION  = re.compile(r"^([A-Za-z]+)\s*(.*)$")
_MODE         = re.compile(r"^0[0-7]{3}$")
_OWNER        = re.compile(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$")
_TOKEN        = re.compile(r"\{([^{}]+)\}")
_FLAG_LITERAL = re.compile(r"(?:hg|flag)\{[^}\n]+\}")
_CONNECTION_TOKENS = frozenset({"url", "host", "port", "user"})

# range/debian uid.table pins these: sshd 101, _chrony 103, dnsmasq 104,
# www-data 33, ctf 65533, and 102 plus 105-149 stay reserved. A scenario
# manifest must not claim any of them for a dynamic account.
_RESERVED_SCENARIO_UIDS = frozenset({33, 101, 102, 103, 104, 65533}) | frozenset(range(105, 150))

def _load_schema() -> dict:
    raw = (files("hg") / "schema" / "challenge-v1.json").read_text(encoding="utf-8")
    return json.loads(raw)


_SCHEMA    = _load_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA)


## Definitions ------------------------------------------------------------------
@dataclass
class DockerfileInfo:
    from_images:        list[str]      = field(default_factory=list)
    arg_defaults:       dict[str, str] = field(default_factory=dict)
    instructions:       list[str]      = field(default_factory=list)
    final_instructions: list[str]      = field(default_factory=list)


## Helper Functions ------------------------------------------------------------
def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buf = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue

        piece = stripped[:-1].rstrip() if stripped.endswith("\\") else stripped
        buf = f"{buf} {piece}".strip() if buf else piece
        if stripped.endswith("\\"):
            continue

        if buf:
            lines.append(buf)
        buf = ""

    if buf:
        lines.append(buf)
    return lines


def _from_image(rest: str) -> str | None:
    tokens = rest.split()
    image  = None
    index  = 0
    while index < len(tokens):
        token = tokens[index]
        if token.upper() == "AS":
            break
        if token.startswith("--"):
            index += 1 if "=" in token else 2
            continue
        image = token
        index += 1
    return image


def _expand_image(token: str, args: dict[str, str]) -> str:
    if token.startswith("${") and token.endswith("}"):
        name = token[2:-1]
        if name not in args:
            return token
        return args[name]
    return token


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _relative_inside(root: Path, raw: str) -> Path | None:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved = (root / path).resolve()
    if not _is_inside(resolved, root):
        return None
    return resolved


def _members(spec: dict, key: str) -> list[dict]:
    value = spec.get(key) or []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _schema_errors(data: dict) -> list[str]:
    errors: list[str] = []
    found: list[ValidationError] = sorted(
        _VALIDATOR.iter_errors(data),
        key=lambda err: [str(part) for part in err.path],
    )
    for err in found:
        loc = ".".join(str(part) for part in err.path) or "file"
        errors.append(f"{loc}: {err.message}")
    return errors


def parse_dockerfile(text: str) -> DockerfileInfo:
    info = DockerfileInfo()
    final: list[str] = []
    seen_from = False
    for line in _logical_lines(text):
        match = _INSTRUCTION.match(line)
        if not match:
            continue

        name = match.group(1).upper()
        rest = match.group(2).strip()
        info.instructions.append(name)
        if name == "ARG":
            if "=" in rest:
                key, value = rest.split("=", 1)
                info.arg_defaults[key.strip()] = value.strip().strip("\"'")
            else:
                info.arg_defaults.setdefault(rest.split()[0], "")
        elif name == "FROM":
            seen_from = True
            final = []
            token = _from_image(rest)
            if token is None:
                info.from_images.append("")
            else:
                info.from_images.append(_expand_image(token, info.arg_defaults))
        elif seen_from:
            final.append(name)
    info.final_instructions = final
    return info


## Main Functions --------------------------------------------------------------
def challenge_root(path: Path | None) -> Path:
    """Resolve the challenge root directory."""
    start = (path or Path.cwd()).resolve()
    if start.is_file() and start.name == MANIFEST_NAME:
        return start.parent

    manifest = start / MANIFEST_NAME
    if manifest.is_file():
        return start
    raise ToolError(f"no {MANIFEST_NAME} in {start}")


def load_manifest(root: Path) -> dict:
    path = root / MANIFEST_NAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ToolError(f"{MANIFEST_NAME} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"{MANIFEST_NAME} must be a mapping")
    return data


def topological_members(members: list[dict]) -> list[dict]:
    by_name = {member["name"]: member for member in members if isinstance(member.get("name"), str)}
    order: list[dict] = []
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(name: str) -> None:
        if name in permanent:
            return
        if name in temporary:
            raise ToolError(f"dependsOn cycle at {name}")

        temporary.add(name)
        member = by_name[name]
        for dep in member.get("dependsOn") or []:
            if dep not in by_name:
                raise ToolError(f"dependsOn {dep!r} is not a member")
            visit(dep)

        temporary.remove(name)
        permanent.add(name)
        order.append(member)

    for member in members:
        name = member.get("name")
        if isinstance(name, str):
            visit(name)
    return order


def _check_depends(members: list[dict], errors: list[str]) -> None:
    names = {member.get("name") for member in members}
    for member in members:
        name = member.get("name")
        deps = member.get("dependsOn") or []
        if not isinstance(deps, list):
            continue

        for dep in deps:
            if dep == name:
                errors.append(f"{name} dependsOn itself")
            elif dep not in names:
                errors.append(f"{name} dependsOn {dep!r} is not a member")

    try:
        topological_members(members)
    except ToolError as exc:
        errors.append(str(exc))


def _gitignore_has_dist(root: Path) -> bool:
    path = root / ".gitignore"
    if not path.is_file():
        return False
    return any(line.strip() == "dist/" for line in path.read_text(encoding="utf-8").splitlines())


def _scan_tree(root: Path, errors: list[str]) -> None:
    if not root.is_dir():
        return

    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if "private" in path.relative_to(root).parts:
            continue
        if path.stat().st_size > 1_000_000:
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        if _FLAG_LITERAL.search(text):
            errors.append(f"flag literal in {path.relative_to(root).as_posix()}")


def _check_dockerfile(root: Path, dockerfile: Path, label: str, errors: list[str]) -> DockerfileInfo | None:
    if not dockerfile.is_file():
        errors.append(f"dockerfile not found: {label}")
        return None

    info = parse_dockerfile(dockerfile.read_text(encoding="utf-8"))
    if not info.from_images:
        errors.append(f"{label} must have a FROM")
    elif base_for_image(info.from_images[-1]) is None:
        errors.append(f"FROM {info.from_images[-1]!r} is not an allowlisted base tag")

    if "ENTRYPOINT" in info.final_instructions:
        errors.append(f"{label} must not set ENTRYPOINT")
    if "USER" in info.final_instructions:
        errors.append(f"{label} must not set USER")
    return info


def _platform_subset(spec: dict, info: DockerfileInfo, errors: list[str]) -> None:
    if not info.from_images:
        return

    base = base_for_image(info.from_images[-1])
    if base is None:
        return

    declared = spec.get("platform") or []
    if not isinstance(declared, list) or not declared:
        errors.append("spec.platform is required")
        return

    extra = [item for item in declared if item not in base.platforms]
    if extra:
        errors.append(
            f"spec.platform {extra} is outside {base.name} platforms {list(base.platforms)}"
        )


def _check_built_member(
    root:                    Path,
    spec:               dict,
    member:             dict,
    *,
    context_root:            str,
    multiple:                bool,
    category:                str,
    challenge_id:            str,
    errors:             list[str],
) -> None:
    name  = member.get("name")
    build = member.get("build") if isinstance(member.get("build"), dict) else {}
    context_raw = build.get("context")
    if not isinstance(name, str) or not isinstance(context_raw, str):
        errors.append("each built service or host needs a name and a build context")
        return

    expected = f"{context_root}/{name}"
    context_path = Path(context_raw)
    if context_path.is_absolute() or ".." in context_path.parts or "private" in context_path.parts:
        errors.append(f"build context {context_raw!r} must be {expected} and must not be private/")
        return

    if context_raw != expected:
        errors.append(f"build context {context_raw!r} must be {expected} and must not be private/")
        return
    context = (root / context_path).resolve()
    if not _is_inside(context, root):
        errors.append(f"build context {context_raw!r} must stay inside the challenge root")
        return

    dockerfile_raw  = build.get("dockerfile", "Dockerfile")
    dockerfile_path = Path(dockerfile_raw)
    if dockerfile_path.is_absolute() or ".." in dockerfile_path.parts:
        errors.append(f"dockerfile {dockerfile_raw!r} must stay inside {expected}")
        return

    dockerfile = (context / dockerfile_path).resolve()
    if not _is_inside(dockerfile, context):
        errors.append(f"dockerfile {dockerfile_raw!r} must stay inside {expected}")
        return

    info = _check_dockerfile(root, dockerfile, f"{expected}/{dockerfile_raw}", errors)
    if info is not None:
        _platform_subset(spec, info, errors)

    image = member.get("image") if isinstance(member.get("image"), dict) else {}
    expected_image = challenge_image_name(category, challenge_id, name, multiple=multiple)
    if image.get("name") != expected_image:
        errors.append(f"image.name must be {expected_image!r} (got {image.get('name')!r})")


def _check_sidecar(member: dict, errors: list[str]) -> None:
    name  = member.get("name")
    image = member.get("image") if isinstance(member.get("image"), dict) else {}
    image_name = image.get("name")

    digest = image.get("digest")
    if not isinstance(image_name, str) or not image_name:
        errors.append(f"sidecar {name} needs image.name")
        return
    if image_name.startswith("challenges/"):
        errors.append(f"sidecar {name} image.name must not be a challenge image")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        errors.append(f"sidecar {name} needs image.digest sha256:<64 hex>")


def _check_ports(member: dict, errors: list[str]) -> None:
    names: set[str] = set()
    for port in member.get("ports") or []:
        if not isinstance(port, dict):
            continue

        name = port.get("name")
        if name in names:
            errors.append(f"{member.get('name')} port {name!r} is duplicated")
        if isinstance(name, str):
            names.add(name)


def _check_health(member: dict, runtime: str, errors: list[str]) -> None:
    name   = member.get("name")
    health = member.get("health") if isinstance(member.get("health"), dict) else {}
    kind   = health.get("type")
    if runtime == "scenario":
        if kind != "ready-file" or health.get("path") != "/run/hg/ready":
            errors.append(f"host {name} health must be ready-file at /run/hg/ready")
        return

    ports      = member.get("ports") or []
    containers = {
        port.get("container")
        for port in ports
        if isinstance(port, dict)
    }
    if kind in ("tcp", "http"):
        if health.get("port") not in containers:
            errors.append(f"{name} health port is not a port on that member")
    if kind == "http":
        path = health.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            errors.append(f"{name} http health needs a path")
    if kind == "exec" and not health.get("command"):
        errors.append(f"{name} exec health needs a command")
    if kind not in ("tcp", "http", "exec"):
        errors.append(f"{name} health type must be tcp, http, or exec")


def _check_env(member: dict, names: set[str], errors: list[str]) -> None:
    for item in member.get("env") or []:
        if not isinstance(item, dict):
            continue

        key = item.get("name")
        if key in ("FLAGS", "USERS"):
            errors.append(f"{member.get('name')} env must not set {key}")

        value = item.get("value")
        if not isinstance(value, str):
            continue

        host = http_host(value)
        if host is not None and host not in names:
            errors.append(f"{member.get('name')} env host {host!r} is not a member")


def _check_resources(member: dict, errors: list[str]) -> None:
    resources = member.get("resources")
    if not isinstance(resources, dict):
        errors.append(f"{member.get('name')} resources are required")


def _check_player(spec: dict, members: list[dict], errors: list[str]) -> None:
    player = spec.get("player")
    if not isinstance(player, dict):
        errors.append("spec.player is required")
        return

    entry = player.get("entry")
    if not isinstance(entry, str) or "." not in entry:
        errors.append("spec.player.entry must be <member>.<port>")
        return

    member_name, port_name = entry.split(".", 1)
    member = next((item for item in members if item.get("name") == member_name), None)
    if member is None:
        errors.append(f"spec.player.entry service {member_name!r} is not in this challenge")
        return
    ports = member.get("ports") or []
    port  = next((item for item in ports if isinstance(item, dict) and item.get("name") == port_name), None)
    if port is None:
        errors.append(f"spec.player.entry port {port_name!r} is not on {member_name}")
        return
    if not port.get("publish"):
        errors.append(f"spec.player.entry port {port_name!r} must set publish: true")

    connection = player.get("connection")
    if not isinstance(connection, str) or not connection:
        errors.append("spec.player.connection is required")
        return

    tokens  = set(_TOKEN.findall(connection))
    unknown = tokens - _CONNECTION_TOKENS
    if unknown:
        errors.append(f"spec.player.connection has unknown tokens {sorted(unknown)}")
    if "url" in tokens and port_name not in ("http", "https"):
        errors.append("spec.player.connection {url} requires an http or https port")


def _check_networks(spec: dict, members: list[dict], errors: list[str]) -> None:
    networks = spec.get("networks")
    if not isinstance(networks, list) or not networks:
        errors.append("image spawn requires networks")
        return

    seen: set[str] = set()
    names = {member.get("name") for member in members}
    for network in networks:
        if not isinstance(network, dict):
            continue

        for member in network.get("members") or []:
            if member not in names:
                errors.append(f"network member {member!r} is not a service or host")
            seen.add(member)

    missing = [name for name in names if isinstance(name, str) and name not in seen]
    if missing:
        errors.append(f"networks must list every member (missing {missing})")


def _check_flags(spec: dict, names: set[str], runtime: str, errors: list[str]) -> None:
    flags = spec.get("flags")
    if not isinstance(flags, list):
        return

    seen: set[str] = set()
    for index, flag in enumerate(flags):
        if not isinstance(flag, dict):
            errors.append(f"spec.flags[{index}] must be a mapping")
            continue

        flag_id = flag.get("id")
        if isinstance(flag_id, str):
            if flag_id in seen:
                errors.append(f"spec.flags[{index}].id {flag_id!r} is duplicated")
            seen.add(flag_id)

        kind = flag.get("type")
        if runtime == "static":
            if kind not in ("static", "regex"):
                errors.append(f"spec.flags[{index}].type must be static or regex")
            if not isinstance(flag.get("content"), str) or not flag.get("content"):
                errors.append(f"spec.flags[{index}].content is required")
            if kind == "regex" and isinstance(flag.get("content"), str):
                try:
                    re.compile(flag["content"])
                except re.error:
                    errors.append(f"spec.flags[{index}].content is not a regex")
            continue

        if kind != "dynamic":
            errors.append(f"spec.flags[{index}].type must be dynamic")
        if "content" in flag:
            errors.append(f"spec.flags[{index}] must not set content")

        path = flag.get("path")
        if not isinstance(path, str) or not path.startswith("/") or ".." in Path(path).parts:
            errors.append(f"spec.flags[{index}].path must be absolute")
        if not isinstance(flag.get("owner"), str) or not _OWNER.fullmatch(flag.get("owner") or ""):
            errors.append(f"spec.flags[{index}].owner must be user:group")
        if not isinstance(flag.get("mode"), str) or not _MODE.fullmatch(flag.get("mode") or ""):
            errors.append(f"spec.flags[{index}].mode must be a four-digit octal mode")

        service = flag.get("service")
        if not isinstance(service, str) or not service:
            errors.append(f"spec.flags[{index}].service is required")
        elif service not in names:
            errors.append(f"spec.flags[{index}].service {service!r} is not a service or host")


def _check_users(
    members:        list[dict],
    runtime:             str,
    errors:         list[str],
) -> None:
    """
    Validate per-member `users[]`: the runtime contract minus secrets.

    Duplicate names within one member are rejected. `gid` requires `group`
    (the runtime itself refuses `gid` without it). A scenario member must not
    claim a uid the host image already pins in `uid.table`.
    """
    for member in members:
        member_name = member.get("name")
        users = member.get("users")
        if not isinstance(users, list):
            continue

        seen: set[str] = set()
        for index, user in enumerate(users):
            label = f"{member_name}.users[{index}]"
            if not isinstance(user, dict):
                errors.append(f"{label} must be a mapping")
                continue

            name = user.get("name")
            if isinstance(name, str):
                if name in seen:
                    errors.append(f"{label}.name {name!r} is duplicated in {member_name}")
                seen.add(name)
            if "gid" in user and not user.get("group"):
                errors.append(f"{label}.gid requires a group")

            uid = user.get("uid")
            if (
                runtime == "scenario"
                and isinstance(uid, int)
                and not isinstance(uid, bool)
                and uid in _RESERVED_SCENARIO_UIDS
            ):
                errors.append(f"{label}.uid {uid} is reserved on range hosts")


def _check_compose_users(
    compose: dict,
    services: list,
    errors: list[str],
) -> None:
    """Validate ``spec.compose.users`` keyed by compose service name."""
    users = compose.get("users")
    if not isinstance(users, dict):
        return
    names = {service.name for service in services}
    for service, entries in users.items():
        if service not in names:
            errors.append(f"spec.compose.users[{service!r}] is not a compose service")
            continue
        if not isinstance(entries, list):
            errors.append(f"spec.compose.users[{service!r}] must be an array")
            continue
        seen: set[str] = set()
        for index, user in enumerate(entries):
            label = f"spec.compose.users[{service!r}][{index}]"
            if not isinstance(user, dict):
                errors.append(f"{label} must be a mapping")
                continue
            name = user.get("name")
            if isinstance(name, str):
                if name in seen:
                    errors.append(f"{label}.name {name!r} is duplicated in {service}")
                seen.add(name)
            if "gid" in user and not user.get("group"):
                errors.append(f"{label}.gid requires a group")


def _check_script(root: Path, raw: str, label: str, errors: list[str]) -> None:
    path = _relative_inside(root, raw)
    if path is None or not path.is_file():
        errors.append(f"{label} is missing")


def _check_files(root: Path, spec: dict, errors: list[str]) -> None:
    files = spec.get("files") or []
    if not isinstance(files, list):
        return
    needs_build = False
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if source == "staging" and not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256") or ""):
            errors.append(f"spec.files[{index}] source staging requires sha256")
        if source == "build":
            needs_build = True
    if needs_build:
        if "build" not in (spec.get("scripts") or {}):
            errors.append("scripts.build is required when a file source is build")
        elif not _relative_inside(root, spec["scripts"]["build"]):
            errors.append("scripts.build is missing")
        else:
            _check_script(root, spec["scripts"]["build"], "scripts.build", errors)
        if not _gitignore_has_dist(root):
            errors.append("dist/ must be gitignored when a file source is build")


def _lint_image_members(
    root: Path,
    spec: dict,
    members: list[dict],
    *,
    context_root: str,
    runtime: str,
    category: str,
    challenge_id: str,
    errors: list[str],
) -> None:
    if spec.get("compose"):
        errors.append("spawn image must not set spec.compose")
    if (root / "compose.yaml").is_file():
        errors.append("spawn image must not include compose.yaml")
    built = [member for member in members if isinstance(member.get("build"), dict)]
    multiple = len(built) > 1
    names = {member.get("name") for member in members if isinstance(member.get("name"), str)}
    if len(names) != len(members):
        errors.append("member names must be unique")
    for member in members:
        has_build = isinstance(member.get("build"), dict)
        has_digest = isinstance(member.get("image"), dict) and "digest" in member.get("image", {})
        if has_build and has_digest:
            errors.append(f"{member.get('name')} must not set both build and image.digest")
        if has_build:
            _check_built_member(
                root,
                spec,
                member,
                context_root=context_root,
                multiple=multiple,
                category=category,
                challenge_id=challenge_id,
                errors=errors,
            )
        elif has_digest:
            _check_sidecar(member, errors)
        else:
            errors.append(f"{member.get('name')} needs a build or an image digest")
        _check_ports(member, errors)
        _check_health(member, runtime, errors)
        _check_env(member, names, errors)
        _check_resources(member, errors)
    _check_depends(members, errors)
    _check_networks(spec, members, errors)
    _check_player(spec, members, errors)
    _check_flags(spec, names, runtime, errors)
    _check_users(members, runtime, errors)


def _lint_compose(
    root: Path,
    spec: dict,
    *,
    category: str,
    challenge_id: str,
    errors: list[str],
) -> None:
    if spec.get("services") or spec.get("hosts") or spec.get("networks"):
        errors.append("spawn compose must not set services, hosts, or networks")
    compose = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
    rel = compose.get("file") or "compose.yaml"
    path = _relative_inside(root, rel)
    if path is None:
        errors.append(f"compose file {rel!r} must stay inside the challenge root")
        return
    if "private" in Path(rel).parts:
        errors.append("compose file must not be under private/")
        return
    if not path.is_file():
        errors.append(f"compose file not found: {rel}")
        return
    try:
        document = load_compose(path)
    except ToolError as exc:
        errors.append(str(exc))
        return
    services, parse_errors = parse_services(document)
    errors.extend(parse_errors)
    if not services:
        return
    built = [service for service in services if service.built]
    multiple = len(built) > 1
    names = {service.name for service in services}
    for service in services:
        if not MEMBER_RE.fullmatch(service.name):
            errors.append(f"compose service {service.name!r} must match {MEMBER_RE.pattern}")
        for key, value in service.environment.items():
            if key in ("FLAGS", "USERS"):
                errors.append(f"compose service {service.name} env must not set {key}")
            host = http_host(value)
            if host is not None and host not in names:
                errors.append(f"compose service {service.name} env host {host!r} is not a member")
        if service.built:
            context = _relative_inside(root, service.build_context or "")
            if context is None or "private" in Path(service.build_context or "").parts:
                errors.append(
                    f"compose service {service.name} build context must stay inside the challenge root"
                )
                continue
            dockerfile = (context / service.dockerfile).resolve()
            if not _is_inside(dockerfile, context):
                errors.append(f"compose service {service.name} dockerfile must stay inside the context")
                continue
            info = _check_dockerfile(
                root,
                dockerfile,
                f"{service.build_context}/{service.dockerfile}",
                errors,
            )
            if info is not None:
                _platform_subset(spec, info, errors)
                _scan_tree(context, errors)
            expected = challenge_image_name(
                category,
                challenge_id,
                service.name,
                multiple=multiple,
            )
            if service.image != expected:
                errors.append(
                    f"compose service {service.name} image must be {expected!r} (got {service.image!r})"
                )
        else:
            if not isinstance(service.image, str) or not DIGEST_IMAGE.fullmatch(service.image):
                errors.append(
                    f"compose service {service.name} image must be name@sha256:<64 hex>"
                )
    _check_flags(spec, names, "container", errors)
    _check_compose_users(compose, services, errors)
    _check_compose_player(spec, services, errors)


def _check_compose_player(spec: dict, services: list, errors: list[str]) -> None:
    player = spec.get("player")
    if not isinstance(player, dict):
        errors.append("spec.player is required")
        return
    entry = player.get("entry")
    if not isinstance(entry, str) or "." not in entry:
        errors.append("spec.player.entry must be <member>.<port>")
        return
    member_name, port_name = entry.split(".", 1)
    service = next((item for item in services if item.name == member_name), None)
    if service is None:
        errors.append(f"spec.player.entry service {member_name!r} is not in this challenge")
        return
    published = service.port_labels.get(port_name)
    if published is None:
        errors.append(
            f"compose service {member_name} needs label hg.port.{port_name} set to a container port"
        )
    elif published not in service.published:
        errors.append(f"spec.player.entry port {port_name!r} is not published on {member_name}")
    connection = player.get("connection")
    if not isinstance(connection, str) or not connection:
        errors.append("spec.player.connection is required")
        return
    tokens = set(_TOKEN.findall(connection))
    unknown = tokens - _CONNECTION_TOKENS
    if unknown:
        errors.append(f"spec.player.connection has unknown tokens {sorted(unknown)}")
    if "url" in tokens and port_name not in ("http", "https"):
        errors.append("spec.player.connection {url} requires an http or https port")


def _git(root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _manifest_rel(root: Path) -> str | None:
    top = _git(root, ["rev-parse", "--show-toplevel"])
    if top is None:
        return None
    try:
        rel = root.resolve().relative_to(Path(top.strip()).resolve())
    except ValueError:
        return None
    if rel == Path("."):
        return MANIFEST_NAME
    return f"{rel.as_posix()}/{MANIFEST_NAME}"


def _strict(root: Path, data: dict, errors: list[str]) -> None:
    spec = data["spec"]
    runtime = spec.get("runtime")
    writeup = root / "private" / "writeup.md"
    if writeup.is_file() and writeup.read_text(encoding="utf-8") == WRITEUP:
        errors.append("private/writeup.md is still the scaffold stub")
    solve = root / "private" / "solve.sh"
    if solve.is_file() and solve.read_text(encoding="utf-8") == SOLVE_SH:
        errors.append("private/solve.sh is still the scaffold stub")
    if runtime in ("container", "scenario") and spec.get("spawn") != "compose":
        for key in ("services", "hosts"):
            for member in _members(spec, key):
                build = member.get("build")
                if isinstance(build, dict) and "fromDigest" not in build:
                    errors.append(f"{member.get('name')} build.fromDigest is required")
    if spec.get("spawn") == "compose":
        compose = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
        digests = compose.get("fromDigest") if isinstance(compose.get("fromDigest"), dict) else {}
        rel = compose.get("file") or "compose.yaml"
        path = _relative_inside(root, rel)
        if path is not None and path.is_file():
            try:
                services, _parse_errors = parse_services(load_compose(path))
            except ToolError:
                services = []
            for service in services:
                if service.built and service.name not in digests:
                    errors.append(f"compose service {service.name} fromDigest is required")
    tracked = _git(root, ["ls-files", "--", "dist"])
    if tracked and tracked.strip():
        errors.append("dist/ bytes are tracked in git")
    for item in spec.get("files") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        listed = _git(root, ["ls-files", "--", name, f"dist/{name}"])
        if listed and listed.strip():
            errors.append(f"player file {name} is tracked in git")
    rel_manifest = _manifest_rel(root)
    if rel_manifest is None:
        return
    main_text = _git(root, ["show", f"main:{rel_manifest}"])
    if not main_text:
        return
    try:
        main_data = yaml.safe_load(main_text)
    except yaml.YAMLError:
        return
    if not isinstance(main_data, dict):
        return
    main_meta = main_data.get("metadata") if isinstance(main_data.get("metadata"), dict) else {}
    main_id = main_meta.get("id")
    if isinstance(main_id, str) and main_id != data["metadata"].get("id"):
        errors.append(f"metadata.id must stay {main_id!r} once main has an id")
    if data["metadata"].get("state") != "visible":
        return
    diff = _git(
        root,
        ["diff", "--name-only", "main", "--", "services", "hosts", "compose.yaml"],
    )
    if diff and diff.strip():
        errors.append("state visible cannot change services, hosts, or compose.yaml")
    main_spec = main_data.get("spec") if isinstance(main_data.get("spec"), dict) else {}
    if main_spec.get("files") != spec.get("files"):
        errors.append("state visible cannot change spec.files")


def _semantic(root: Path, data: dict) -> list[str]:
    errors: list[str] = []
    metadata = data["metadata"]
    challenge_id = metadata["id"]
    category = metadata["category"]
    spec = data["spec"]
    runtime = spec.get("runtime")
    spawn = spec.get("spawn")
    if not (root / "private" / "writeup.md").is_file():
        errors.append("private/writeup.md is missing")
    scripts = spec.get("scripts") if isinstance(spec.get("scripts"), dict) else {}
    test = scripts.get("test")
    if isinstance(test, str):
        _check_script(root, test, "scripts.test", errors)
    _check_files(root, spec, errors)
    _scan_tree(root / "services", errors)
    _scan_tree(root / "hosts", errors)

    if runtime == "static":
        if spawn or spec.get("services") or spec.get("hosts") or spec.get("compose"):
            errors.append("static runtime must not set spawn, services, hosts, or compose")
        _check_flags(spec, set(), "static", errors)
        if not spec.get("files"):
            errors.append("static runtime requires spec.files")
        return errors

    if runtime == "scenario":
        if spawn != "image":
            errors.append("scenario spawn must be image")
        if spec.get("services"):
            errors.append("scenario runtime has no services")
        hosts = _members(spec, "hosts")
        if not hosts:
            errors.append("scenario runtime requires hosts")
        else:
            _lint_image_members(
                root,
                spec,
                hosts,
                context_root="hosts",
                runtime="scenario",
                category=category,
                challenge_id=challenge_id,
                errors=errors,
            )
        return errors

    if runtime != "container":
        errors.append(f"spec.runtime must be container, scenario, or static (got {runtime!r})")
        return errors
    if spawn not in ("image", "compose"):
        errors.append("container runtime requires spec.spawn image or compose")
        return errors
    if spec.get("hosts"):
        errors.append("container runtime has no hosts")
    if spawn == "compose":
        _lint_compose(
            root,
            spec,
            category=category,
            challenge_id=challenge_id,
            errors=errors,
        )
        return errors
    services = _members(spec, "services")
    if not services:
        errors.append("container runtime requires services")
    else:
        _lint_image_members(
            root,
            spec,
            services,
            context_root = "services",
            runtime      = "container",
            category     = category,
            challenge_id = challenge_id,
            errors       = errors,
        )
    return errors


def lint_challenge(root: Path, *, strict: bool = False) -> list[str]:
    try:
        data = load_manifest(root)
    except ToolError as exc:
        return [str(exc)]

    schema_errors = _schema_errors(data)
    if schema_errors:
        return schema_errors

    errors = _semantic(root, data)
    if strict:
        _strict(root, data, errors)
    return errors


def require_lint(root: Path, *, strict: bool = False) -> dict:
    errors = lint_challenge(root, strict=strict)
    if errors:
        raise ToolError("\n".join(errors))
    return load_manifest(root)
