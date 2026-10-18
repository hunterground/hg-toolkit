"""Read the subset of a Compose file the toolkit runs."""

from dataclasses    import dataclass, field
from pathlib        import Path
from urllib.parse   import urlparse
from hg.errors      import ToolError
import re
import yaml


FORBIDDEN = (
    "privileged",
    "network_mode",
    "pid",
    "ipc",
    "cap_add",
    "devices",
    "security_opt",
    "sysctls",
    "user",
)
DIGEST_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


@dataclass
class ComposeService:
    name:                str
    image:               str        | None
    build_context:       str        | None
    dockerfile:          str
    published:      tuple[int, ...]
    port_labels:    dict[str, int]
    environment:    dict[str, str]          = field(default_factory=dict)
    raw:            dict                    = field(default_factory=dict)

    @property
    def built(self) -> bool:
        return self.build_context is not None


def load_compose(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ToolError(f"{path.name} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ToolError(f"{path.name} must be a mapping")
    return data


## Helper Functions --------------------------------------------------------------
def _container_port(spec: object) -> int | None:
    if isinstance(spec, int) and not isinstance(spec, bool):
        return spec

    if isinstance(spec, str):
        body  = spec.split("/", 1)[0]
        parts = body.split(":")
        token = parts[-1]
        if token.isdigit():
            return int(token)
        return None

    if isinstance(spec, dict):
        target = spec.get("target")
        if isinstance(target, int) and not isinstance(target, bool):
            return target
        if isinstance(target, str) and target.isdigit():
            return int(target)
    return None


def _labels(service: dict) -> dict[str, str]:
    raw = service.get("labels") or {}
    if isinstance(raw, list):
        labels: dict[str, str] = {}
        for item in raw:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                labels[key] = value
        return labels

    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    return {}


def _environment(service: dict) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        env: dict[str, str] = {}
        for item in raw:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                env[key] = value
        return env

    if isinstance(raw, dict):
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    return {}


def _volume_problem(entry: object) -> str | None:
    if isinstance(entry, str):
        if "docker.sock" in entry:
            return "docker socket"

        source = entry.split(":", 1)[0]
        if source.startswith(("/", ".", "~")):
            return "host path"
        return None

    if isinstance(entry, dict):
        source = str(entry.get("source") or "")
        if "docker.sock" in source:
            return "docker socket"
        if entry.get("type") == "bind" or source.startswith(("/", ".", "~")):
            return "host path"
    return None


## Main Functions --------------------------------------------------------------
def parse_services(data: dict) -> tuple[list[ComposeService], list[str]]:
    errors: list[str] = []
    raw = data.get("services")
    if not isinstance(raw, dict) or not raw:
        return [], ["compose file needs a services mapping"]

    services: list[ComposeService] = []
    for name, service in raw.items():
        if not isinstance(service, dict):
            errors.append(f"compose service {name!r} must be a mapping")
            continue

        for key in FORBIDDEN:
            if key in service and service.get(key) not in (False, None, [], ""):
                errors.append(f"compose service {name} must not set {key}")

        volumes = service.get("volumes") or []
        if isinstance(volumes, list):
            for entry in volumes:
                problem = _volume_problem(entry)
                if problem:
                    errors.append(f"compose service {name} volume is a {problem}")
        build = service.get("build")
        context = None
        dockerfile = "Dockerfile"
        if isinstance(build, str):
            context = build
        elif isinstance(build, dict):
            context = build.get("context")
            if isinstance(build.get("dockerfile"), str):
                dockerfile = build["dockerfile"]
        elif build is not None:
            errors.append(f"compose service {name} build must be a string or mapping")

        image = service.get("image")
        if image is not None and not isinstance(image, str):
            errors.append(f"compose service {name} image must be a string")
            image = None

        published: list[int] = []
        ports = service.get("ports") or []
        if isinstance(ports, list):
            for entry in ports:
                port = _container_port(entry)
                if port is None:
                    errors.append(f"compose service {name} has a port that is not a container port")
                else:
                    published.append(port)

        labels: dict[str, int] = {}
        for key, value in _labels(service).items():
            if not key.startswith("hg.port."):
                continue

            port_name = key[len("hg.port.") :]
            if not str(value).isdigit():
                errors.append(f"compose service {name} label {key} must be a port number")
                continue
            labels[port_name] = int(value)
        services.append(
            ComposeService(
                name          = str(name),
                image         = image,
                build_context = context if isinstance(context, str) else None,
                dockerfile    = dockerfile,
                published     = tuple(published),
                port_labels   = labels,
                environment   = _environment(service),
                raw           = service,
            )
        )
    return services, errors


def http_host(value: str) -> str | None:
    if not value.startswith(("http://", "https://")):
        return None

    host = urlparse(value).hostname
    if not host or host == "localhost":
        return None

    try:
        int(host.replace(".", ""))
    except ValueError:
        return host
    return None
