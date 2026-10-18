"""
Run a solver script against the local graph and check it produced the flag.

`verify` couples build, run, and solve, then tears the graph down. Iterating on
an exploit needs the opposite: assume `run` already left a graph going, reuse the
host port it published, and report whether the script found the flag. That is what
`solve` does here.

The environment the script sees is the same one `verify` exports, so a solver
written for CI works unchanged: `CHALLENGE_HOST`, `CHALLENGE_PORT`,
`CHALLENGE_CONNECTION`, plus `CHALLENGE_FLAG_<ID>` and
`CHALLENGE_USER_<MEMBER>_<NAME>` for the values a run generated. Flag contents
are not in the manifest, so they are read back out of the running containers,
which also proves the graph is live.
"""

from collections.abc    import Callable
from dataclasses        import dataclass, field
from pathlib            import Path
from hg.errors          import ToolError
from hg.lint            import require_lint
from hg.run             import (
    InjectedFlag,
    InjectedUser,
    PortReader,
    read_docker_port,
)
from hg.state           import read_state
import os
import re
import subprocess


Solve = Callable[[Path, dict[str, str]], tuple[int, str]]


@dataclass(frozen=True)
class SolveEnv:
    """What a solver script is given, and what it is expected to find."""

    host:            str
    port:            int
    connection:      str
    env:        dict[str, str]
    flags:      tuple[InjectedFlag, ...] = ()
    users:      tuple[InjectedUser, ...] = ()
    warnings:   tuple[str, ...] = ()


def _docker_cat(container: str, path: str) -> str | None:
    """Read `path` out of a running container, or None if it is not there."""
    completed = subprocess.run(
        ["docker", "exec", container, "cat", path],
        check   = False,
        text    = True,
        capture_output = True,
    )
    if completed.returncode != 0:
        return None
    content = completed.stdout.strip()
    return content or None


def recover_flags(
    spec: dict,
    challenge_id: str,
    state: dict,
    read_file: Callable[[str, str], str | None] | None = None,
) -> tuple[InjectedFlag, ...]:
    """Read each dynamic flag back out of the container that owns it.

    A service may carry more than one dynamic flag, so this walks the manifest
    rather than the recorded containers: only the manifest knows every path.
    Returns nothing when no flag is dynamic, which is the case for a static or
    regex-only flag set.
    """
    reader = read_file or _docker_cat
    if not any(
        isinstance(flag, dict) and flag.get("type") == "dynamic"
        for flag in (spec.get("flags") or [])
    ):
        return ()
    containers = {
        container["name"]
        for container in state.get("containers") or []
        if isinstance(container, dict) and isinstance(container.get("name"), str)
    }
    recovered: list[InjectedFlag] = []
    for index, flag in enumerate(spec.get("flags") or []):
        if not isinstance(flag, dict) or flag.get("type") != "dynamic":
            continue
        member = flag.get("service")
        path = flag.get("path")
        if not isinstance(member, str) or not isinstance(path, str):
            continue
        container = f"hg-{challenge_id}-{member}"
        if container not in containers:
            raise ToolError(
                f"container {container} for flag {member!r} is not part of the run; "
                "start the graph with hg challenge run"
            )
        content = reader(container, path)
        if content is None:
            raise ToolError(
                f"cannot read {path} from {container}; the graph is not running. "
                "Start it with hg challenge run"
            )
        recovered.append(
            InjectedFlag(
                id=str(flag.get("id") or f"flag-{index + 1}"),
                service=member,
                path=path,
                owner=str(flag.get("owner") or ""),
                mode=str(flag.get("mode") or ""),
                content=content,
            )
        )
    return tuple(recovered)


def _recover_users(state: dict) -> tuple[InjectedUser, ...]:
    return tuple(
        InjectedUser(
            member   = item.get("member",   ""),
            name     = item.get("name",     ""),
            password = item.get("password", ""),
            fields   = item.get("fields", {}),
        )
        for item in state.get("users", [])
        if isinstance(item, dict)
    )


def _env_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def _resolve_port(
    state:          dict,
    port:           int | None,
    read_port:      PortReader,
) -> int | None:
    if port is not None:
        return port

    recorded = state.get("port")
    if isinstance(recorded, int):
        return recorded

    container      = state.get("entry_container")
    container_port = state.get("entry_container_port")
    if isinstance(container, str) and isinstance(container_port, int):
        return read_port(container, container_port)
    return None


def plan_solve(
    root:           Path,
    *,
    connection:     str         | None = None,
    host:           str         | None = None,
    port:           int         | None = None,
    read_port:      PortReader  | None = None,
    read_file:      Callable[[str, str], str | None] | None = None,
) -> SolveEnv:
    """Build the solver environment from the live graph, without running anything."""
    data = require_lint(root)
    spec = data["spec"]
    if spec.get("runtime") == "static":
        raise ToolError(
            "a static challenge has no runtime graph to solve against; "
            "run the script directly or use hg challenge verify"
        )
    challenge_id = data["metadata"]["id"]
    state = read_state(root)
    if state is None:
        raise ToolError(
            "no running graph for this challenge; start one with hg challenge run"
        )

    reader = read_port or read_docker_port
    resolved_port = _resolve_port(state, port, reader)
    if resolved_port is None:
        raise ToolError(
            "cannot work out the player port for the running graph; "
            "re-run hg challenge run or pass --port"
        )

    overrode = port is not None or host is not None
    host     = host or _text(state.get("host")) or "127.0.0.1"
    warnings: list[str] = []
    if connection is None:
        recorded = _text(state.get("connection"))
        if recorded and not overrode:
            # The recorded string is what the run actually rendered, so prefer it
            # over re-deriving: the manifest may use a template we would get wrong.
            connection = recorded
        else:
            connection = _render_connection(_connection_template(spec), host, resolved_port)
            if overrode and recorded:
                warnings.append("host/port overridden; re-rendered the connection from the manifest")
            elif not recorded:
                warnings.append("run recorded no connection; rendered one from the manifest")

    flags = recover_flags(spec, challenge_id, state, read_file)
    users = _recover_users(state)

    env = os.environ.copy()
    env["CHALLENGE_HOST"] = host
    env["CHALLENGE_PORT"] = str(resolved_port)
    env["CHALLENGE_CONNECTION"] = connection
    for flag in flags:
        env[f"CHALLENGE_FLAG_{_env_slug(flag.id)}"] = flag.content

    for user in users:
        env[f"CHALLENGE_USER_{_env_slug(user.member)}_{_env_slug(user.name)}"] = user.password

    return SolveEnv(
        host       = host,
        port       = resolved_port,
        connection = connection,
        env        = env,
        flags      = flags,
        users      = users,
        warnings   = tuple(warnings),
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _connection_template(spec: dict) -> str:
    player = spec.get("player") if isinstance(spec.get("player"), dict) else {}
    connection = player.get("connection")
    return connection if isinstance(connection, str) and connection else "{url}"


def _render_connection(template: str, host: str, port: int) -> str:
    url = f"http://{host}:{port}"
    return template.replace("{url}", url).replace("{host}", host).replace("{port}", str(port))


def solver_matches(stdout: str, flags: list, injected: dict[str, str], logic: str) -> bool:
    """Whether stdout proves each expected flag, honoring `spec.logic`."""
    checks: list[bool] = []
    for flag in flags:
        if not isinstance(flag, dict):
            continue

        kind = flag.get("type")
        if kind == "dynamic":
            content = injected.get(flag.get("id") or "")
            checks.append(bool(content) and content in stdout)
        elif kind == "static":
            checks.append(isinstance(flag.get("content"), str) and flag["content"] in stdout)
        elif kind == "regex" and isinstance(flag.get("content"), str):
            checks.append(re.search(flag["content"], stdout) is not None)
    if not checks:
        return False
    if logic == "all":
        return all(checks)
    return any(checks)


def check_graph(
    root:   Path,
    *,
    runner: Callable[[list[str]], int] | None = None,
) -> tuple[str, ...]:
    """
    Confirm the recorded containers are still up.

    Returns a warning per container that is gone, so a stale state file is
    reported rather than silently producing a confusing solver failure. Never
    raises for this: a slow-booting app is the solver's problem to wait out.
    """
    state = read_state(root)
    if state is None:
        return ()

    docker = runner or _quiet_docker
    gone: list[str] = []
    for container in state.get("containers") or []:
        if not isinstance(container, dict):
            continue

        name = container.get("name")
        if not isinstance(name, str):
            continue
        if docker(["docker", "inspect", "-f", "{{.State.Running}}", name]) != 0:
            gone.append(name)
    return tuple(f"{name} is not running" for name in gone)


def _quiet_docker(argv: list[str]) -> int:
    completed = subprocess.run(
        argv,
        check  = False,
        stdout = subprocess.PIPE,
        stderr = subprocess.DEVNULL,
        text   = True,
    )
    if completed.returncode != 0:
        return completed.returncode
    return 0 if completed.stdout.strip() == "true" else 1


def run_solver(
    root:       Path,
    env:        dict[str, str],
    rel:             str,
    solve:      Solve           | None = None,
) -> tuple[int, str]:
    script = (root / rel).resolve()
    if solve is not None:
        return solve(script, env)
    if not script.is_file():
        raise ToolError(f"solver script not found: {rel}")
    completed = subprocess.run(
        ["sh", str(script)],
        cwd   = root,
        env   = env,
        text  = True,
        check = False,
        capture_output = True,
    )
    return completed.returncode, completed.stdout


@dataclass(frozen=True)
class SolveOutcome:
    env:        SolveEnv
    code:            int
    stdout:          str
    ok:              bool
    warnings:   tuple[str, ...] = field(default_factory=tuple)


def solve_challenge(
    root:       Path,
    script:     str     | None = None,
    *,
    solve:      Solve   | None = None,
    connection: str     | None = None,
    host:       str     | None = None,
    port:       int     | None = None,
    read_port:  PortReader                       | None = None,
    read_file:  Callable[[str, str], str | None] | None = None,
    check:      bool           = True,
) -> SolveOutcome:
    """Run the solver against the live graph and report whether it found the flag."""
    data = require_lint(root)
    spec = data["spec"]
    target = script or spec.get("scripts", {}).get("test")
    if not isinstance(target, str) or not target:
        raise ToolError("no solver script given and spec.scripts.test is not set")

    env_spec = plan_solve(
        root,
        connection = connection,
        host       = host,
        port       = port,
        read_port  = read_port,
        read_file  = read_file,
    )
    warnings = list(env_spec.warnings)
    if check:
        warnings.extend(check_graph(root))

    logic = spec.get("logic") or "any"
    injected = {flag.id: flag.content for flag in env_spec.flags}
    code, stdout = run_solver(root, env_spec.env, target, solve)
    if code != 0:
        return SolveOutcome(env_spec, code, stdout, False, tuple(warnings))
    ok = solver_matches(stdout, spec.get("flags") or [], injected, logic)
    return SolveOutcome(env_spec, code, stdout, ok, tuple(warnings))
