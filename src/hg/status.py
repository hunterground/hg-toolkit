"""
Report whether a local challenge graph is still running.

`run` starts the containers and records the connection in `.hg/run.json`.
`stop` deletes that file. Status therefore names the containers from the
manifest, the same names `stop` removes, and asks Docker what is actually up.
The state file only fills in the connection, flags, and users from the last run.
"""

from collections.abc    import Callable
from dataclasses        import dataclass
from datetime           import datetime, timezone
from pathlib            import Path
from hg.compose         import load_compose, parse_services
from hg.errors          import ToolError
from hg.lint            import _relative_inside, require_lint
from hg.run             import (
    HealthProbe,
    _entry,
    _members,
    _probe,
    check_health,
    render_connection,
)
from hg.state           import read_state
import json
import subprocess


Docker = Callable[[list[str]], tuple[int, str, str]]
"""Run a docker command and return its exit code, stdout, and stderr."""

Probe = Callable[[HealthProbe], bool]

_INSPECT_FORMAT = (
    '{"Status":{{json .State.Status}},'
    '"ExitCode":{{.State.ExitCode}},'
    '"StartedAt":{{json .State.StartedAt}},'
    '"FinishedAt":{{json .State.FinishedAt}},'
    '"Health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
    '"Image":{{json .Config.Image}},'
    '"Ports":{{json .NetworkSettings.Ports}}}'
)

_DAEMON = (
    "Cannot connect to the Docker daemon",
    "Is the docker daemon running",
    "error during connect",
)


## Definitions ----------------------------------------------------------------------------
@dataclass(frozen=True)
class ContainerState:
    name:               str
    image:              str
    state:              str
    detail:             str
    ports:              str


@dataclass(frozen=True)
class ChallengeStatus:
    summary:            str
    challenge_id:       str
    connection:         str
    flags:              tuple[tuple[str, str], ...]
    users:              tuple[tuple[str, str], ...]
    containers:         tuple[ContainerState, ...]
    networks:           tuple[tuple[str, bool], ...]
    health:             str
    warnings:           tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.summary in ("running", "static")


@dataclass(frozen=True)
class _ContainerSpec:
    name:               str
    image:              str


@dataclass(frozen=True)
class _Graph:
    challenge_id:           str
    static:                 bool                           = False
    containers:             tuple[_ContainerSpec, ...]     = ()
    networks:               tuple[str, ...]                = ()
    entry_container:        str                            = ""
    entry_container_port:   int | None                     = None
    template:               str                            = "{url}"
    probe_member:           dict | None                    = None
    probe_kind:             str                            = ""


## Main Functions -----------------------------------------------------------------------
def status_challenge(
    root:       Path,
    *,
    docker:     Docker      | None = None,
    probe:      Probe       | None = None,
    now:        datetime    | None = None,
) -> ChallengeStatus:
    """
    Describe the local graph for this challenge.

    A static challenge has no containers. Otherwise each expected container is
    `running`, `exited`, `missing`, or another Docker state. The summary is
    `running` when every container is up, `partial` when only some are,
    `stopped` when a run was recorded or a container still exists, and
    `not started` when nothing is up and no run was recorded.
    """
    data  = require_lint(root)
    graph = _graph(data, root)
    if graph.static:
        return ChallengeStatus(
            summary      = "static",
            challenge_id = graph.challenge_id,
            connection   = "",
            flags        = (),
            users        = (),
            containers   = (),
            networks     = (),
            health       = "",
            warnings     = (),
        )

    inspect = docker or _docker
    moment  = now    or datetime.now(timezone.utc)
    state   = read_state(root)
    containers, entry_port = _containers(graph, inspect, moment)
    networks = tuple((name, _network_present(name, inspect)) for name in graph.networks)
    flags, users, recorded_port, recorded_host, recorded_connection = _recorded(state)

    warnings: list[str] = []
    host = recorded_host or "127.0.0.1"
    connection = recorded_connection
    if entry_port is not None and recorded_port is not None and recorded_port != entry_port:
        warnings.append(
            f"recorded port {recorded_port} does not match the published port {entry_port}"
        )
        connection = render_connection(graph.template, host, entry_port)
    elif not connection and entry_port is not None:
        connection = render_connection(graph.template, host, entry_port)

    health = _health(graph, containers, entry_port, probe)
    if health == "failed":
        warnings.append("player entry health check failed")

    return ChallengeStatus(
        summary      = _summary(containers, recorded=state is not None),
        challenge_id = graph.challenge_id,
        connection   = connection,
        flags        = flags,
        users        = users,
        containers   = containers,
        networks     = networks,
        health       = health,
        warnings     = tuple(warnings),
    )


## Helper Functions ----------------------------------------------------------------------
def _docker(argv: list[str]) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            check           = False,
            text            = True,
            capture_output  = True,
        )
    except FileNotFoundError as exc:
        raise ToolError("docker is not available") from exc
    return completed.returncode, completed.stdout, completed.stderr


def _graph(data: dict, root: Path) -> _Graph:
    spec = data["spec"]
    challenge_id = data["metadata"]["id"]
    if spec.get("runtime") == "static":
        return _Graph(challenge_id=challenge_id, static=True)
    if spec.get("spawn") == "compose":
        return _compose_graph(data, root, challenge_id)
    return _image_graph(spec, challenge_id)


def _image_graph(spec: dict, challenge_id: str) -> _Graph:
    members = _members(spec)
    containers = tuple(
        _ContainerSpec(name=f"hg-{challenge_id}-{member['name']}", image=_planned_image(member))
        for member in members
        if isinstance(member.get("name"), str) and member.get("name")
    )
    networks = tuple(
        f"hg-{challenge_id}-{network['name']}"
        for network in spec.get("networks") or []
        if isinstance(network, dict)
        and isinstance(network.get("name"), str)
        and network.get("name")
    )
    entry_member, entry_port, template = _entry(spec, members)
    return _Graph(
        challenge_id         = challenge_id,
        containers           = containers,
        networks             = networks,
        entry_container      = f"hg-{challenge_id}-{entry_member['name']}",
        entry_container_port = int(entry_port["container"]),
        template             = template,
        probe_member         = entry_member,
    )


def _compose_graph(data: dict, root: Path, challenge_id: str) -> _Graph:
    spec = data["spec"]
    compose = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
    rel = compose.get("file") or "compose.yaml"
    path = _relative_inside(root, rel)
    if path is None:
        raise ToolError(f"compose file {rel!r} must stay inside the challenge root")

    services, errors = parse_services(load_compose(path))
    if errors:
        raise ToolError("\n".join(errors))

    containers: list[_ContainerSpec] = []
    for item in services:
        image = item.image or ""
        if item.built and item.image:
            image = f"{item.image}:local"
        containers.append(_ContainerSpec(name=f"hg-{challenge_id}-{item.name}", image=image))

    player = spec.get("player") if isinstance(spec.get("player"), dict) else {}
    entry = player.get("entry")
    if not isinstance(entry, str) or "." not in entry:
        raise ToolError("spec.player.entry must be <service>.<port>")

    member_name, port_name = entry.split(".", 1)
    service = next((item for item in services if item.name == member_name), None)
    if service is None or port_name not in service.port_labels:
        raise ToolError(f"spec.player.entry {entry!r} is not on the compose file")

    template = player.get("connection") if isinstance(player.get("connection"), str) else "{url}"
    return _Graph(
        challenge_id         = challenge_id,
        containers           = tuple(containers),
        entry_container      = f"hg-{challenge_id}-{member_name}",
        entry_container_port = service.port_labels[port_name],
        template             = template,
        probe_kind           = "http" if port_name in ("http", "https") else "tcp",
    )


def _planned_image(member: dict) -> str:
    image_meta = member.get("image") if isinstance(member.get("image"), dict) else {}
    name = image_meta.get("name")
    if not isinstance(name, str) or not name:
        return ""
    if isinstance(member.get("build"), dict):
        return f"{name}:local"
    digest = image_meta.get("digest")
    if isinstance(digest, str) and digest:
        return f"{name}@{digest}"
    return name


def _containers(
    graph:      _Graph,
    docker:     Docker,
    now:        datetime,
) -> tuple[tuple[ContainerState, ...], int | None]:
    rows: list[ContainerState] = []
    entry_port: int | None = None
    for spec in graph.containers:
        parsed = _inspect_container(spec.name, docker)
        if parsed is None:
            rows.append(ContainerState(
                name   = spec.name,
                image  = spec.image,
                state  = "missing",
                detail = "",
                ports  = "",
            ))
            continue

        if spec.name == graph.entry_container:
            entry_port = _host_port(parsed.get("Ports"), graph.entry_container_port)
        image = parsed.get("Image")
        rows.append(ContainerState(
            name   = spec.name,
            image  = image if isinstance(image, str) and image else spec.image,
            state  = str(parsed.get("Status") or "unknown"),
            detail = _detail(parsed, now),
            ports  = _format_ports(parsed.get("Ports")),
        ))
    return tuple(rows), entry_port


def _inspect_container(name: str, docker: Docker) -> dict | None:
    code, stdout, stderr = docker(["docker", "inspect", "--format", _INSPECT_FORMAT, name])
    _raise_if_daemon(stderr, code)
    if code != 0 or not stdout.strip():
        return None

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"docker inspect returned invalid JSON for {name}") from exc

    if not isinstance(parsed, dict):
        raise ToolError(f"docker inspect returned invalid JSON for {name}")
    return parsed


def _network_present(name: str, docker: Docker) -> bool:
    code, _stdout, stderr = docker(["docker", "network", "inspect", name])
    _raise_if_daemon(stderr, code)
    return code == 0


def _raise_if_daemon(stderr: str, code: int) -> None:
    if code != 0 and any(token in stderr for token in _DAEMON):
        raise ToolError("docker is not available")


def _recorded(
    state:      dict    | None,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], int | None, str, str]:
    if state is None:
        return (), (), None, "", ""

    flags: list[tuple[str, str]] = []
    for flag in state.get("flags") or []:
        if not isinstance(flag, dict):
            continue

        flag_id = flag.get("id")
        content = flag.get("content")
        if isinstance(flag_id, str) and flag_id and isinstance(content, str) and content:
            flags.append((flag_id, content))

    users: list[tuple[str, str]] = []
    for user in state.get("users") or []:
        if not isinstance(user, dict):
            continue
        member = user.get("member") if isinstance(user.get("member"), str) else ""
        name = user.get("name") if isinstance(user.get("name"), str) else ""
        password = user.get("password") if isinstance(user.get("password"), str) else ""
        who = f"{member} {name}".strip()
        if who or password:
            users.append((who, password))

    port = state.get("port")
    recorded_port = port if isinstance(port, int) and not isinstance(port, bool) else None
    host = state.get("host") if isinstance(state.get("host"), str) else ""
    connection = state.get("connection") if isinstance(state.get("connection"), str) else ""
    return tuple(flags), tuple(users), recorded_port, host, connection


def _health(
    graph:      _Graph,
    containers: tuple[ContainerState, ...],
    entry_port: int     | None,
    probe:      Probe   | None,
) -> str:
    entry = next((item for item in containers if item.name == graph.entry_container), None)
    if entry is None or entry.state != "running":
        return ""

    if graph.probe_member is not None and graph.entry_container_port is not None:
        published    = {graph.entry_container_port: entry_port}
        health_probe = _probe(graph.probe_member, graph.entry_container, published)
    elif graph.probe_kind and graph.entry_container_port is not None:
        health_probe = HealthProbe(
            kind           = graph.probe_kind,
            container      = graph.entry_container,
            host           = "127.0.0.1" if entry_port else None,
            port           = entry_port,
            path           = "/" if graph.probe_kind == "http" else None,
            container_port = graph.entry_container_port,
        )
    else:
        return ""

    check = probe or check_health
    return "ok" if check(health_probe) else "failed"


def _summary(containers: tuple[ContainerState, ...], *, recorded: bool) -> str:
    running = sum(item.state == "running" for item in containers)
    if containers and running == len(containers):
        return "running"
    if running:
        return "partial"
    if recorded or any(item.state != "missing" for item in containers):
        return "stopped"
    return "not started"


def _detail(parsed: dict, now: datetime) -> str:
    status = parsed.get("Status") or ""
    health = parsed.get("Health") if isinstance(parsed.get("Health"), str) else ""
    if status == "running":
        started = _parse_docker_time(parsed.get("StartedAt"))
        text = f"Up {_span(started, now)}" if started else "Up"
        if health:
            text = f"{text} ({health})"
        return text
    if status == "exited":
        finished = _parse_docker_time(parsed.get("FinishedAt"))
        text = f"Exited ({parsed.get('ExitCode')})"
        if finished:
            text = f"{text} {_span(finished, now)} ago"
        return text
    return str(status)


def _parse_docker_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dot = text.find(".")
    if dot != -1:
        end = dot + 1
        while end < len(text) and text[end].isdigit():
            end += 1
        fraction = text[dot + 1:end][:6]
        text = text[:dot] + "." + fraction + text[end:]

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _span(start: datetime, now: datetime) -> str:
    seconds = int((now - start).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        count, unit = seconds, "second"
    elif seconds < 3600:
        count, unit = seconds // 60, "minute"
    elif seconds < 86400:
        count, unit = seconds // 3600, "hour"
    else:
        count, unit = seconds // 86400, "day"
    if count != 1:
        unit += "s"
    return f"{count} {unit}"


def _format_ports(ports: object) -> str:
    if not isinstance(ports, dict):
        return ""

    rendered: list[str] = []
    for key in sorted(ports):
        bindings = ports[key]
        if not isinstance(bindings, list):
            continue

        for binding in bindings:
            if not isinstance(binding, dict):
                continue

            host_port = binding.get("HostPort")
            if not isinstance(host_port, str) or not host_port:
                continue

            host_ip = binding.get("HostIp")
            host = host_ip if isinstance(host_ip, str) and host_ip else "0.0.0.0"
            rendered.append(f"{host}:{host_port}->{key}")
    return ", ".join(rendered)


def _host_port(ports: object, container_port: int | None) -> int | None:
    if not isinstance(ports, dict) or container_port is None:
        return None

    keys = [f"{container_port}/tcp", f"{container_port}/udp"]
    keys.extend(key for key in sorted(ports) if str(key).split("/", 1)[0] == str(container_port))
    seen: set[str] = set()
    for key in keys:
        if key in seen or key not in ports:
            continue

        seen.add(key)
        bindings = ports[key]
        if not isinstance(bindings, list):
            continue

        for binding in bindings:
            if not isinstance(binding, dict):
                continue

            host_port = binding.get("HostPort")
            if isinstance(host_port, str) and host_port.isdigit():
                return int(host_port)
    return None
