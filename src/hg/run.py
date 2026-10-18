"""Start a challenge graph locally and inject generated flags.

``spec.flags`` names each file. This command fills ``content`` for ``type:
dynamic`` slots and passes the result as ``FLAGS`` on the named service or
host. Generated users are passed as ``USERS``. The manifest is not modified.
"""

from collections.abc    import Callable
from dataclasses        import dataclass, field, replace
from pathlib            import Path
from hg.compose         import load_compose, parse_services
from hg.errors          import ToolError
from hg.lint            import _relative_inside, require_lint, topological_members
from hg.state           import clear_state
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
import yaml


Runner      = Callable[[list[str]], int]
ContentFor  = Callable[[dict], str]
PasswordFor = Callable[[dict], str]
HostPortFor = Callable[[int], int]
"""
Map a container port to a host port at plan time.

Tests inject this to pin host ports so a plan is deterministic. Production
runs leave it unset and let Docker choose.
"""
PortReader  = Callable[[str, int], int | None]
"""
Read the host port Docker bound for `(container, container_port)`, or None.
"""


## Definitions ----------------------------------------------------------------------------
@dataclass(frozen=True)
class InjectedFlag:
    id:         str
    service:    str
    path:       str
    owner:      str
    mode:       str
    content:    str


@dataclass(frozen=True)
class InjectedUser:
    member:     str
    name:       str
    password:   str
    fields:     dict = field(default_factory=dict)
    """
    The user's non-secret manifest fields (uid, gid, group, groups, home,
    shell, gecos, locked). Forwarded verbatim into the `USERS` payload.
    Generated accounts carry `password`; a static `password` in the manifest
    is carried through as-is when no `passwordFrom` is set.
    """


@dataclass(frozen=True)
class RunningContainer:
    name:       str
    image:      str


@dataclass(frozen=True)
class HealthProbe:
    kind:               str
    container:          str
    host:               str | None = None
    port:               int | None = None
    path:               str | None = None
    command:            str | None = None
    status:             int        = 200
    container_port:     int | None = None


@dataclass(frozen=True)
class Step:
    argv:           list[str]
    error:               str
    quiet:               bool      = False
    optional:            bool      = False
    probe:      HealthProbe | None = None


@dataclass(frozen=True)
class PortSlot:
    """A published port whose host side Docker assigns when the container starts."""

    container:      str
    container_port: int
    protocol:       str = "tcp"


@dataclass(frozen=True)
class RunPlan:
    steps:              tuple[Step, ...]
    probe:              HealthProbe
    connection:              str
    host:                    str
    port:                    int        | None
    flags:              tuple[InjectedFlag, ...]
    users:              tuple[InjectedUser, ...]
    containers:         tuple[RunningContainer, ...]
    override:                str        | None = None
    port_slots:         tuple[PortSlot, ...]   = ()
    entry_container:         str        | None = None
    entry_container_port:    int        | None = None
    connection_template:     str               = "{url}"


@dataclass(frozen=True)
class RunResult:
    connection:             str
    host:                   str
    port:                   int | None
    flags:              tuple[InjectedFlag, ...]
    users:              tuple[InjectedUser, ...]
    containers:         tuple[RunningContainer, ...]
    entry_container:         str | None = None
    entry_container_port:    int | None = None


## Helper Functions ----------------------------------------------------------------------
def random_flag_content() -> str:
    """
    A local canary. Thirty-two hex characters, wrapped as `hg{...}`.

    The platform renders `hg{%random%}` from 16 random bytes, so a solver
    that checks the length sees the same shape here and in production.
    """
    return "hg{" + secrets.token_hex(16) + "}"


def random_password(_user: dict) -> str:
    return secrets.token_hex(8)


def read_docker_port(container: str, container_port: int) -> int | None:
    """Ask Docker which host port it bound for a published container port.

    Returns None when Docker reports no mapping, which is what happens when the
    port was never published. Probing from the host instead would be wrong: the
    daemon binds inside its own VM, so a host-side socket test cannot see it.
    """
    completed = subprocess.run(
        ["docker", "port", container, f"{container_port}/tcp"],
        check = False,
        text  = True,
        capture_output = True,
    )
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        _host, _, port = line.rpartition(":")
        if port.strip().isdigit():
            return int(port.strip())
    return None


def default_runner(argv: list[str]) -> int:
    completed = subprocess.run(argv, check=False, stdout=subprocess.PIPE)
    return completed.returncode


def quiet_runner(argv: list[str]) -> int:
    completed = subprocess.run(
        argv,
        check  = False,
        stdout = subprocess.DEVNULL,
        stderr = subprocess.DEVNULL,
    )
    return completed.returncode


def _members(spec: dict) -> list[dict]:
    if spec.get("runtime") == "scenario":
        return list(spec.get("hosts") or [])
    return list(spec.get("services") or [])


def _cpus(mhz: int) -> str:
    return f"{mhz / 1000:.3f}".rstrip("0").rstrip(".")


def _check_content(content: str) -> str:
    if not isinstance(content, str) or not content or any(ch in content for ch in "\n\r\x00"):
        raise ToolError("generated flag content must be a single line")
    return content


def inject_flags(spec: dict, content_for: ContentFor) -> tuple[InjectedFlag, ...]:
    injected: list[InjectedFlag] = []
    for index, flag in enumerate(spec.get("flags") or []):
        if not isinstance(flag, dict) or flag.get("type") != "dynamic":
            continue

        flag_id = flag.get("id")
        if not isinstance(flag_id, str) or not flag_id:
            flag_id = f"flag-{index + 1}"

        injected.append(
            InjectedFlag(
                id      = flag_id,
                service = flag["service"],
                path    = flag["path"],
                owner   = flag["owner"],
                mode    = flag["mode"],
                content = _check_content(content_for(flag)),
            )
        )
    return tuple(injected)


_USER_FIELDS = ("uid", "gid", "group", "groups", "home", "shell", "gecos", "locked")

def _user_entry(user: dict, password: str | None) -> dict:
    """
    Render one manifest user into a `USERS` payload entry.

    `passwordFrom: generated` resolves to the injected password; a static
    `password` is carried through. Everything else is forwarded verbatim.
    When there is no password, the key is omitted so the runtime locks a new
    account instead of setting an empty password.
    """
    entry = {key: user[key] for key in _USER_FIELDS if key in user}
    entry["name"] = user["name"]
    if password:
        entry["password"] = password
    return entry

def inject_users(members: list[dict], password_for: PasswordFor) -> tuple[InjectedUser, ...]:
    injected: list[InjectedUser] = []
    for member in members:
        for user in member.get("users") or []:
            if not isinstance(user, dict):
                continue

            name = user.get("name")
            if not isinstance(name, str) or not name:
                continue

            if user.get("passwordFrom") == "generated":
                password = _check_content(password_for(user))
            elif isinstance(user.get("password"), str) and user["password"]:
                password = user["password"]
            else:
                # No password: a new account will be locked by the runtime.
                password = ""

            injected.append(
                InjectedUser(
                    member   = member["name"],
                    name     = name,
                    password = password,
                    fields   = _user_entry(user, password),
                )
            )
    return tuple(injected)


def _inject_compose_users(compose: dict, password_for: PasswordFor) -> tuple[InjectedUser, ...]:
    """Injected users from ``spec.compose.users`` keyed by compose service name."""
    raw = compose.get("users")
    if not isinstance(raw, dict):
        return ()

    injected: list[InjectedUser] = []
    for service, entries in raw.items():
        if not isinstance(entries, list):
            continue

        for user in entries:
            if not isinstance(user, dict):
                continue

            name = user.get("name")
            if not isinstance(name, str) or not name:
                continue

            if user.get("passwordFrom") == "generated":
                password = _check_content(password_for(user))
            elif isinstance(user.get("password"), str) and user["password"]:
                password = user["password"]
            else:
                password = ""

            injected.append(
                InjectedUser(
                    member   = service,
                    name     = name,
                    password = password,
                    fields   = _user_entry(user, password),
                )
            )
    return tuple(injected)

def flags_payload(items: list[InjectedFlag]) -> str:
    payload = {
        "flags": [
            {
                "path":     item.path,
                "content":  item.content,
                "owner":    item.owner,
                "mode":     item.mode,
            }
            for item in items
        ]
    }
    return json.dumps(payload, separators=(",", ":"))


def users_payload(items: list[InjectedUser]) -> str:
    payload = {"users": [item.fields for item in items]}
    return json.dumps(payload, separators=(",", ":"))


def _entry(spec: dict, members: list[dict]) -> tuple[dict, dict, str]:
    player = spec.get("player")
    entry = player.get("entry") if isinstance(player, dict) else None
    if not isinstance(entry, str) or "." not in entry:
        raise ToolError("spec.player.entry must be <service>.<port>")

    member_name, port_name = entry.split(".", 1)
    member = next((item for item in members if item.get("name") == member_name), None)
    if member is None:
        raise ToolError(f"spec.player.entry service {member_name!r} is not in this challenge")

    ports = member.get("ports") or []
    port = next((item for item in ports if isinstance(item, dict) and item.get("name") == port_name), None)
    if port is None:
        raise ToolError(f"spec.player.entry port {port_name!r} is not on {member_name}")

    template = "{url}"
    if isinstance(player, dict) and isinstance(player.get("connection"), str):
        template = player["connection"]
    return member, port, template


def render_connection(template: str, host: str, port: int) -> str:
    url = f"http://{host}:{port}"
    return template.replace("{url}", url).replace("{host}", host).replace("{port}", str(port))


def _publish_arg(port: dict, host_port: int | None) -> str | None:
    if not port.get("publish"):
        return None

    container = int(port["container"])
    protocol  = port.get("protocol") or "tcp"
    # An empty host port asks Docker to pick a free one inside its own VM.
    host    = "" if host_port is None else str(host_port)
    binding = f"127.0.0.1:{host}:{container}"
    if protocol != "tcp":
        binding = f"{binding}/{protocol}"
    return binding


def _probe(member: dict, container: str, published: dict[int, int | None]) -> HealthProbe:
    health = member.get("health") if isinstance(member.get("health"), dict) else {}
    kind   = health.get("type") or "tcp"
    status = health.get("status") if isinstance(health.get("status"), int) else 200
    if kind == "ready-file":
        path = health.get("path") if isinstance(health.get("path"), str) else "/run/hg/ready"
        return HealthProbe(kind="ready-file", container=container, path=path)

    if kind == "exec" and isinstance(health.get("command"), str):
        return HealthProbe(kind="exec", container=container, command=health["command"])

    health_port    = health.get("port")
    container_port = int(health_port) if isinstance(health_port, int) else None
    # None means "Docker has not assigned a host port yet"; the runtime fills it.
    host_port = published.get(container_port) if container_port is not None else None
    path = health.get("path") if isinstance(health.get("path"), str) else None
    return HealthProbe(
        kind           = kind if kind in ("tcp", "http") else "tcp",
        container      = container,
        host           = "127.0.0.1" if host_port else None,
        port           = host_port,
        path           = path,
        status         = status,
        container_port = container_port,
    )


def _http_status(host: str, port: int, path: str, status: int) -> bool:
    request = (
        f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    ).encode()
    try:
        with socket.create_connection((host, port), timeout=1) as sock:
            sock.sendall(request)
            data = b""
            while b"\r\n" not in data and len(data) <= 512:
                chunk = sock.recv(256)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return False
    line = data.split(b"\r\n", 1)[0].decode("latin1", "replace")
    parts = line.split()
    return len(parts) >= 2 and parts[1] == str(status)


def _exec_probe(probe: HealthProbe) -> list[str]:
    port = probe.container_port
    if probe.kind == "http":
        path = probe.path or "/"
        script = (
            "python -c \"import urllib.request; "
            f"r=urllib.request.urlopen('http://127.0.0.1:{port}{path}', timeout=2); "
            f"raise SystemExit(0 if r.status == {probe.status} else 1)\""
        )
    else:
        script = (
            "python -c \"import socket; "
            f"socket.create_connection(('127.0.0.1', {port}), 2).close()\""
        )
    return ["docker", "exec", probe.container, "sh", "-c", script]


def _plan_image(
    data:           dict,
    content_for:    ContentFor,
    password_for:   PasswordFor,
    host_port_for:  HostPortFor | None,
) -> RunPlan:
    metadata = data["metadata"]
    spec     = data["spec"]

    challenge_id = metadata["id"]
    members  = topological_members(_members(spec))
    flags    = inject_flags(spec, content_for)
    users    = inject_users(members, password_for)
    by_service: dict[str, list[InjectedFlag]] = {}

    for flag in flags:
        by_service.setdefault(flag.service, []).append(flag)

    by_user: dict[str, list[InjectedUser]] = {}
    for user in users:
        by_user.setdefault(user.member, []).append(user)

    networks = [item for item in (spec.get("networks") or []) if isinstance(item, dict)]
    named_networks = [
        (network, f"hg-{challenge_id}-{network['name']}")
        for network in networks
        if isinstance(network.get("name"), str) and network.get("name")
    ]
    entry_member, entry_port, template = _entry(spec, members)
    label = f"hg.challenge.id={challenge_id}"
    steps:          list[Step] = []
    containers:     list[RunningContainer] = []
    run_steps:      list[Step] = []
    connect_steps:  list[Step] = []
    probes:         dict[str, HealthProbe] = {}
    host_ports:     dict[str, dict[int, int | None]] = {}
    port_slots:     list[PortSlot] = []

    for member in members:
        name = member["name"]
        image_meta = member.get("image") if isinstance(member.get("image"), dict) else {}
        if isinstance(member.get("build"), dict):
            image = f"{image_meta['name']}:local"
        else:
            image = f"{image_meta['name']}@{image_meta['digest']}"
        container = f"hg-{challenge_id}-{name}"
        containers.append(RunningContainer(name=container, image=image))
        steps.append(
            Step(
                ["docker", "image", "inspect", image],
                error=f"{image} is not present locally; build it with hg challenge build",
            )
        )
        published:      dict[int, int | None] = {}
        publish_args:   list[str] = []
        for port in member.get("ports") or []:
            if not isinstance(port, dict) or not port.get("publish"):
                continue

            container_port = int(port["container"])
            host_port      = host_port_for(container_port) if host_port_for is not None else None
            published[container_port] = host_port
            if host_port is None:
                port_slots.append(
                    PortSlot(
                        container      = container,
                        container_port = container_port,
                        protocol       = port.get("protocol", "tcp"),
                    )
                )
            binding = _publish_arg(port, host_port)
            if binding is not None:
                publish_args.extend(["-p", binding])
        host_ports[name] = published
        probes[name]     = _probe(member, container, published)
        member_nets = [
            net
            for network, net in named_networks
            if name in network.get("members", [])
        ]
        argv = [
            "docker",
            "run",
            "--detach",
            "--name",       container,
            "--hostname",   name,
            "--label",      label,
        ]
        if member_nets:
            argv.extend(["--network", member_nets[0], "--network-alias", name])

        resources = member.get("resources") if isinstance(member.get("resources"), dict) else {}

        memory    = resources.get("memoryMb")
        if isinstance(memory, int) and not isinstance(memory, bool) and memory > 0:
            argv.extend(["--memory", f"{memory}m"])

        cpu = resources.get("cpuMhz")
        if isinstance(cpu, int) and not isinstance(cpu, bool) and cpu > 0:
            argv.extend(["--cpus", _cpus(cpu)])

        shm = member.get("shmMb")
        if isinstance(shm, int) and not isinstance(shm, bool) and shm > 0:
            argv.extend(["--shm-size", f"{shm}m"])

        for item in member.get("env") or []:
            if not isinstance(item, dict):
                continue

            key   = item.get("name")
            value = item.get("value")
            if not isinstance(key, str) or not key or key in ("FLAGS", "USERS") or not isinstance(value, str):
                continue
            argv.extend(["-e", f"{key}={value}"])

        owned_users = by_user.get(name) or []
        if owned_users:
            argv.extend(["-e", f"USERS={users_payload(owned_users)}"])

        owned = by_service.get(name) or []
        if owned:
            argv.extend(["-e", f"FLAGS={flags_payload(owned)}"])

        argv.extend(publish_args)
        argv.append(image)

        run_argv = argv
        for dep in member.get("dependsOn") or []:
            run_steps.append(Step(argv=[], error="", probe=probes[dep]))

        run_steps.append(Step(run_argv, error=f"docker run failed for {image}"))
        for extra in member_nets[1:]:
            connect_steps.append(
                Step(
                    ["docker", "network", "connect", "--alias", name, extra, container],
                    error = f"docker network connect failed for {container}",
                )
            )

    remove_steps: list[Step] = [
        Step(["docker", "rm", "-f", container.name], error="", quiet=True, optional=True)
        for container in containers
    ]
    remove_steps.extend(
        Step(["docker", "network", "rm", net], error="", quiet=True, optional=True)
        for _network, net in named_networks
    )
    create_steps: list[Step] = []
    for _network, net in named_networks:
        # Deliberately a normal bridge, not --internal. Docker's bridge driver
        # skips per-port DNAT on internal networks, so a published port would be
        # silently dropped and the player URL would be unreachable. Isolation is
        # the deployed platform's job; locally the published ports bind to
        # 127.0.0.1 only, so the challenge stays unreachable off-host.
        argv = ["docker", "network", "create", "--label", label, net]
        create_steps.append(Step(argv, error=f"docker network create failed for {net}"))

    entry_name = entry_member["name"]
    entry_container_port = int(entry_port["container"])
    entry_host_port = host_ports[entry_name][entry_container_port]
    host = "127.0.0.1"
    connection = (
        render_connection(template, host, entry_host_port)
        if entry_host_port is not None
        else ""
    )
    ordered = tuple(steps + remove_steps + create_steps + run_steps + connect_steps)
    return RunPlan(
        steps      = ordered,
        probe      = probes[entry_name],
        connection = connection,
        host       = host,
        port       = entry_host_port,
        flags      = flags,
        users      = users,
        containers = tuple(containers),
        port_slots = tuple(port_slots),
        entry_container      = f"hg-{challenge_id}-{entry_name}",
        entry_container_port = entry_container_port,
        connection_template  = template,
    )


def _plan_compose(
    data:               dict,
    root:               Path,
    content_for:        ContentFor,
    host_port_for:      HostPortFor     | None,
    password_for:       PasswordFor,
) -> RunPlan:
    spec         = data["spec"]
    challenge_id = data["metadata"]["id"]
    compose      = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
    rel          = compose.get("file", "compose.yaml")

    compose_path = _relative_inside(root, rel)
    if compose_path is None:
        raise ToolError(f"compose file {rel!r} must stay inside the challenge root")

    services, errors = parse_services(load_compose(compose_path))
    if errors:
        raise ToolError("\n".join(errors))

    flags = inject_flags(spec, content_for)
    by_service: dict[str, list[InjectedFlag]] = {}
    for flag in flags:
        by_service.setdefault(flag.service, []).append(flag)

    users = _inject_compose_users(compose, password_for)
    by_user: dict[str, list[InjectedUser]] = {}
    for user in users:
        by_user.setdefault(user.member, []).append(user)

    player = spec.get("player") if isinstance(spec.get("player"), dict) else {}
    entry = player.get("entry")
    if not isinstance(entry, str) or "." not in entry:
        raise ToolError("spec.player.entry must be <service>.<port>")

    member_name, port_name = entry.split(".", 1)
    service = next((item for item in services if item.name == member_name), None)
    if service is None or port_name not in service.port_labels:
        raise ToolError(f"spec.player.entry {entry!r} is not on the compose file")

    project = f"hg-{challenge_id}"
    override_services: dict[str, dict] = {}
    containers: list[RunningContainer] = []
    entry_container = f"hg-{challenge_id}-{member_name}"
    entry_container_port = service.port_labels[port_name]
    entry_host_port: int | None = None
    port_slots: list[PortSlot] = []
    for item in services:
        container_name = f"hg-{challenge_id}-{item.name}"
        image = item.image or ""
        if item.built and item.image:
            image = f"{item.image}:local"
        containers.append(RunningContainer(name=container_name, image=image))

        body: dict = {"container_name": container_name}
        if item.built and item.image:
            body["image"] = image

        environment: dict[str, str] = {}
        owned = by_service.get(item.name) or []
        if owned:
            environment["FLAGS"] = flags_payload(owned)

        owned_users = by_user.get(item.name) or []
        if owned_users:
            environment["USERS"] = users_payload(owned_users)

        if environment:
            body["environment"] = environment

        ports: list[str] = []
        for container_port in item.published:
            # An empty host port asks Docker to pick a free one inside its VM.
            host_port = host_port_for(container_port) if host_port_for is not None else None
            host = "" if host_port is None else str(host_port)
            ports.append(f"127.0.0.1:{host}:{container_port}")
            if host_port is None:
                port_slots.append(
                    PortSlot(container=container_name, container_port=container_port)
                )
            if item.name == member_name and container_port == entry_container_port:
                entry_host_port = host_port

        if ports:
            body["ports"] = ports
        override_services[item.name] = body
    template = player.get("connection") if isinstance(player.get("connection"), str) else "{url}"
    connection = (
        render_connection(template, "127.0.0.1", entry_host_port)
        if entry_host_port is not None
        else ""
    )
    override = yaml.safe_dump({"services": override_services}, sort_keys=False)
    compose_file = str(compose_path)
    steps = (
        Step(
            ["docker", "compose", "-f", compose_file, "-p", project, "down", "--remove-orphans"],
            error    = "",
            quiet    = True,
            optional = True,
        ),
        Step(
            [
                "docker",
                "compose",
                "-f",   compose_file,
                "-f",
                "__OVERRIDE__",
                "-p",   project,
                "up",
                "-d",
            ],
            error = "docker compose up failed",
        ),
    )
    probe_kind = "http" if port_name in ("http", "https") else "tcp"
    return RunPlan(
        steps = steps,
        probe = HealthProbe(
            kind           = probe_kind,
            container      = entry_container,
            host           = "127.0.0.1" if entry_host_port is not None else None,
            port           = entry_host_port,
            path           = "/" if probe_kind == "http" else None,
            container_port = entry_container_port,
        ),
        connection = connection,
        host       = "127.0.0.1",
        port       = entry_host_port,
        flags      = flags,
        users      = users,
        containers = tuple(containers),
        override   = override,
        port_slots = tuple(port_slots),
        entry_container      = entry_container,
        entry_container_port = entry_container_port,
        connection_template  = template,
    )


def plan_run(
    data:           dict,
    contents:       ContentFor      | None = None,
    *,
    root:           Path            | None = None,
    host_port_for:  HostPortFor     | None = None,
    password_for:   PasswordFor     | None = None,
) -> RunPlan:
    content_for = contents or (lambda _flag: random_flag_content())
    passwords   = password_for or random_password
    if data["spec"].get("spawn") == "compose":
        if root is None:
            raise ToolError("compose spawn needs the challenge root")
        return _plan_compose(data, root, content_for, host_port_for, passwords)
    return _plan_image(data, content_for, passwords, host_port_for)


def _execute(steps: tuple[Step, ...], run: Runner, quiet: Runner, wait: Callable[[HealthProbe], None] | None) -> None:
    for step in steps:
        if step.probe is not None:
            if wait is not None:
                wait(step.probe)
            continue
        code = (quiet if step.quiet else run)(step.argv)
        if code != 0 and not step.optional:
            raise ToolError(step.error)


def _wait_until(predicate: Callable[[], bool], timeout: float, message: str) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise ToolError(message)
        time.sleep(0.2)


def check_health(probe: HealthProbe, run: Runner | None = None) -> bool:
    """Try the player health check once. True when the entry answers."""
    try:
        wait_health(probe, run or quiet_runner, timeout=0)
    except ToolError:
        return False
    return True


def wait_health(probe: HealthProbe, run: Runner, timeout: float = 60) -> None:
    if probe.kind == "http" and probe.host and probe.port:
        path = probe.path or "/"

        def http_ready() -> bool:
            return _http_status(probe.host or "", probe.port or 0, path, probe.status)

        _wait_until(http_ready, timeout, f"health check timed out for {probe.container}")
        return

    if probe.kind == "tcp" and probe.host and probe.port:
        def ready() -> bool:
            try:
                with socket.create_connection((probe.host, probe.port), timeout=1):
                    return True
            except OSError:
                return False

        _wait_until(ready, timeout, f"health check timed out for {probe.container}")
        return

    if probe.kind in ("tcp", "http") and probe.container_port and not probe.port:
        argv = _exec_probe(probe)
        _wait_until(lambda: run(argv) == 0, timeout, f"health check timed out for {probe.container}")
        return

    if probe.kind == "ready-file" and probe.path:
        argv = ["docker", "exec", probe.container, "test", "-f", probe.path]
        _wait_until(lambda: run(argv) == 0, timeout, f"health check timed out for {probe.container}")
        return

    if probe.kind == "exec" and probe.command:
        argv = ["docker", "exec", probe.container, "sh", "-c", probe.command]
        _wait_until(lambda: run(argv) == 0, timeout, f"health check timed out for {probe.container}")


def _bind_override(steps: tuple[Step, ...], override: Path) -> tuple[Step, ...]:
    bound: list[Step] = []
    for step in steps:
        argv = [str(override) if arg == "__OVERRIDE__" else arg for arg in step.argv]
        bound.append(
            Step(argv, error=step.error, quiet=step.quiet, optional=step.optional, probe=step.probe)
        )
    return tuple(bound)


def _missing_port(container: str | None, container_port: int | None) -> ToolError:
    target = container or "the entry container"
    return ToolError(
        f"docker did not publish container port {container_port} on {target}; "
        "the player entry would be unreachable. Check that the port sets publish: true "
        "and that the Docker network is not internal."
    )


def _resolve_ports(
    plan:           RunPlan,
    read_port:      PortReader,
) -> tuple[RunPlan, RunResult]:
    """
    Fill in the host ports Docker chose, then render the connection and probe.

    Docker assigns an empty-host-port binding inside its own VM, so the ports are
    only knowable after the containers start. Missing the entry port is fatal
    rather than a health timeout, so the failure names the real cause.
    """
    slots = plan.port_slots
    entry_port: int | None = plan.port
    if plan.entry_container is not None and plan.entry_container_port is not None:
        entry_port = next(
            (
                read_port(slot.container, slot.container_port)
                for slot in slots
                if slot.container == plan.entry_container
                and slot.container_port == plan.entry_container_port
            ),
            plan.port,
        )
        if entry_port is None:
            raise _missing_port(plan.entry_container, plan.entry_container_port)

    connection = (
        render_connection(plan.connection_template, plan.host, entry_port)
        if entry_port is not None
        else plan.connection
    )

    probe = plan.probe
    if probe.kind in ("tcp", "http") and probe.port is None:
        discovered = next(
            (
                read_port(slot.container, slot.container_port)
                for slot in slots
                if slot.container == probe.container
                and slot.container_port == probe.container_port
            ),
            None,
        )
        if discovered is None:
            raise _missing_port(probe.container, probe.container_port)
        probe = replace(probe, host="127.0.0.1", port=discovered)
    resolved = replace(plan, connection=connection, port=entry_port, probe=probe)
    result = RunResult(
        connection = connection,
        host       = plan.host,
        port       = entry_port,
        flags      = plan.flags,
        users      = plan.users,
        containers = plan.containers,
        entry_container      = plan.entry_container,
        entry_container_port = plan.entry_container_port,
    )
    return resolved, result


def run_challenge(
    root:           Path,
    runner:         Runner      | None = None,
    *,
    contents:       ContentFor  | None = None,
    timeout:        float              = 60,
    host_port_for:  HostPortFor | None = None,
    password_for:   PasswordFor | None = None,
    read_port:      PortReader  | None = None,
) -> RunResult:
    data = require_lint(root)
    plan = plan_run(
        data,
        contents,
        root=root,
        host_port_for=host_port_for,
        password_for=password_for,
    )
    if runner is None:
        run: Runner = default_runner
        quiet: Runner = quiet_runner
        do_wait = True
    else:
        run = quiet = runner
        do_wait = False

    reader:         PortReader         = read_port or read_docker_port
    override_path:  Path        | None = None
    result:         RunResult   | None = None
    try:
        steps = plan.steps
        if plan.override is not None:
            handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yml", delete=False)
            handle.write(plan.override)
            handle.close()
            override_path = Path(handle.name)
            steps = _bind_override(steps, override_path)

        _execute(steps, run, quiet, (lambda probe: wait_health(probe, quiet, timeout)) if do_wait else None)
        if plan.port_slots:
            plan, result = _resolve_ports(plan, reader)
        if do_wait:
            try:
                wait_health(plan.probe, quiet, timeout)
            except ToolError as exc:
                lines = [str(exc), *(f"{flag.id} {flag.content}" for flag in plan.flags)]
                lines.extend(f"user {user.member} {user.name} {user.password}" for user in plan.users)
                raise ToolError("\n".join(lines)) from exc
    finally:
        if override_path is not None:
            os.unlink(override_path)
    if result is None:
        result = RunResult(
            connection  = plan.connection,
            host        = plan.host,
            port        = plan.port,
            flags       = plan.flags,
            users       = plan.users,
            containers  = plan.containers,
            entry_container      = plan.entry_container,
            entry_container_port = plan.entry_container_port,
        )
    return result


def stop_challenge(root: Path, runner: Runner | None = None) -> list[str]:
    """
    Stop the local graph started by `run` without touching the images.

    Removes each container and network this challenge would create, then reports
    what was removed. 
    Names are the ones `run` uses, so it works even when the graph is only partly up
    or has already been stopped: a name that is already gone is success. 
    Base images and the built `:local` tags stay, so the next `run` starts again without a rebuild.
    """
    data = require_lint(root)
    spec = data["spec"]
    if spec.get("runtime") == "static":
        clear_state(root)
        return []

    challenge_id = data["metadata"]["id"]
    if runner is None:
        run: Runner = default_runner
        quiet: Runner = quiet_runner
    else:
        run = quiet = runner

    removed: list[str] = []
    if spec.get("spawn") == "compose":
        compose = spec.get("compose") if isinstance(spec.get("compose"), dict) else {}
        rel = compose.get("file") or "compose.yaml"
        path = _relative_inside(root, rel)
        if path is None:
            raise ToolError(f"compose file {rel!r} must stay inside the challenge root")

        project = f"hg-{challenge_id}"
        quiet(["docker", "compose", "-f", str(path), "-p", project, "down", "--remove-orphans"])
        clear_state(root)
        return [project]

    for member in _members(spec):
        name = f"hg-{challenge_id}-{member['name']}"
        quiet(["docker", "rm", "-f", name])
        removed.append(name)

    for network in spec.get("networks") or []:
        if not isinstance(network, dict):
            continue

        name = network.get("name")
        if not isinstance(name, str) or not name:
            continue

        net = f"hg-{challenge_id}-{name}"
        quiet(["docker", "network", "rm", net])
        removed.append(net)
    clear_state(root)
    return removed
