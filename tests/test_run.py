"""
    HunterGround Toolkit
    Test for running the challenge
"""

from hg.errors      import ToolError
from hg.run         import plan_run, random_flag_content, run_challenge, wait_health
from hg.scaffold    import create_challenge
import json
import re
import socket
import pytest
import yaml


## Helper Functions ----------------------------------------------------------------------
def _runner(calls, status=None):
    def run(argv):
        calls.append(argv)
        if status is not None:
            return status(argv)
        return 0

    return run


def _manifest(root):
    return yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))


def _write_manifest(root, manifest):
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _flags_env(argv):
    for index, arg in enumerate(argv):
        if arg == "-e" and argv[index + 1].startswith("FLAGS="):
            return json.loads(argv[index + 1].split("=", 1)[1])
    return None


def _fixed(flag):
    return "hg{" + flag["id"] + "}"


def _same_port(port: int) -> int:
    return port


## Main Tests ---------------------------------------------------------------------------
def test_run_injects_manifest_slots_and_prints_the_connection(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    before = (root / "challenge.yaml").read_text(encoding="utf-8")
    calls = []
    result = run_challenge(root, _runner(calls), contents=_fixed, host_port_for=_same_port)

    payload = json.dumps(
        {
            "flags": [
                {
                    "path": "/app/flag.txt",
                    "content": "hg{flag}",
                    "owner": "nginx:nginx",
                    "mode": "0400",
                }
            ]
        },
        separators=(",", ":"),
    )
    runs = [argv for argv in calls if argv[1] == "run"]
    assert runs == [
        [
            "docker",
            "run",
            "--detach",
            "--name",
            "hg-web-xss-01-web",
            "--hostname",
            "web",
            "--label",
            "hg.challenge.id=web-xss-01",
            "--network",
            "hg-web-xss-01-default",
            "--network-alias",
            "web",
            "--memory",
            "128m",
            "--cpus",
            "0.2",
            "-e",
            f"FLAGS={payload}",
            "-p",
            "127.0.0.1:80:80",
            "challenges/web/web-xss-01:local",
        ]
    ]
    assert ["docker", "image", "inspect", "challenges/web/web-xss-01:local"] in calls
    assert ["docker", "rm", "-f", "hg-web-xss-01-web"] in calls
    assert [
        "docker",
        "network",
        "create",
        "--label",
        "hg.challenge.id=web-xss-01",
        "hg-web-xss-01-default",
    ] in calls
    kinds = []
    for argv in calls:
        if argv[1:3] == ["image", "inspect"]:
            kinds.append("inspect")
        elif argv[1:3] == ["rm", "-f"]:
            kinds.append("rm")
        elif argv[1:3] == ["network", "rm"]:
            kinds.append("network-rm")
        elif argv[1:3] == ["network", "create"]:
            kinds.append("network-create")
        elif argv[1] == "run":
            kinds.append("run")
    assert kinds == ["inspect", "rm", "network-rm", "network-create", "run"]
    assert result.connection == "http://127.0.0.1:80"
    assert [(flag.id, flag.content) for flag in result.flags] == [("flag", "hg{flag}")]
    assert result.containers[0].name == "hg-web-xss-01-web"
    assert (root / "challenge.yaml").read_text(encoding="utf-8") == before
    assert "hg{flag}" not in before


def test_each_run_draws_a_new_flag(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    first_calls = []
    second_calls = []
    run_challenge(root, _runner(first_calls), host_port_for=_same_port)
    run_challenge(root, _runner(second_calls), host_port_for=_same_port)
    first = _flags_env(next(argv for argv in first_calls if argv[1] == "run"))
    second = _flags_env(next(argv for argv in second_calls if argv[1] == "run"))
    assert first["flags"][0]["content"] != second["flags"][0]["content"]
    assert re.fullmatch(r"hg\{[0-9a-f]{32}\}", first["flags"][0]["content"])
    assert first["flags"][0]["path"] == "/app/flag.txt"
    assert first["flags"][0]["owner"] == "nginx:nginx"
    assert first["flags"][0]["mode"] == "0400"


def test_random_flag_content_shape():
    values = {random_flag_content() for _ in range(20)}
    assert len(values) == 20
    assert all(re.fullmatch(r"hg\{[0-9a-f]{32}\}", value) for value in values)


def test_flags_follow_the_manifest_and_stay_on_their_service(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    bot = root / "services" / "bot"
    bot.mkdir()
    (bot / "Dockerfile").write_text(
        (root / "services" / "web" / "Dockerfile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = _manifest(root)
    web = manifest["spec"]["services"][0]
    web["image"]["name"] = "challenges/web/web-xss-01-web"
    manifest["spec"]["services"] = [
        web,
        {
            "name": "bot",
            "build": {"context": "services/bot", "dockerfile": "Dockerfile"},
            "image": {"name": "challenges/web/web-xss-01-bot"},
            "ports": [{"name": "report", "container": 3000, "protocol": "tcp", "publish": False}],
            "health": {"type": "tcp", "port": 3000},
            "env": [{"name": "APPURL", "value": "http://web/"}],
            "shmMb": 2048,
            "resources": {"memoryMb": 2048, "cpuMhz": 500},
        },
    ]
    manifest["spec"]["networks"][0]["members"] = ["web", "bot"]
    manifest["spec"]["flags"] = [
        {
            "id": "flag",
            "type": "dynamic",
            "path": "/flag",
            "owner": "root:root",
            "mode": "0440",
            "service": "web",
        },
        {
            "id": "backup",
            "type": "dynamic",
            "path": "/app/extra.txt",
            "owner": "nginx:nginx",
            "mode": "0400",
            "service": "web",
        },
        {
            "id": "bot",
            "type": "dynamic",
            "path": "/opt/bot.txt",
            "owner": "ctf:ctf",
            "mode": "0400",
            "service": "bot",
        },
    ]
    _write_manifest(root, manifest)
    calls = []
    result = run_challenge(root, _runner(calls), contents=_fixed, host_port_for=_same_port)

    runs = [argv for argv in calls if argv[1] == "run"]
    web_flags = _flags_env(runs[0])
    bot_flags = _flags_env(runs[1])
    assert web_flags == {
        "flags": [
            {"path": "/flag", "content": "hg{flag}", "owner": "root:root", "mode": "0440"},
            {
                "path": "/app/extra.txt",
                "content": "hg{backup}",
                "owner": "nginx:nginx",
                "mode": "0400",
            },
        ]
    }
    assert bot_flags == {
        "flags": [
            {"path": "/opt/bot.txt", "content": "hg{bot}", "owner": "ctf:ctf", "mode": "0400"}
        ]
    }
    assert "APPURL=http://web/" in runs[1]
    assert "FLAGS=nope" not in runs[1]
    assert "--shm-size" in runs[1] and runs[1][runs[1].index("--shm-size") + 1] == "2048m"
    assert "-p" not in runs[1]
    assert "-p" in runs[0]
    assert [flag.id for flag in result.flags] == ["flag", "backup", "bot"]


def test_missing_image_stops_before_run(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []

    def status(argv):
        return 1 if argv[1:3] == ["image", "inspect"] else 0

    with pytest.raises(ToolError, match="hg challenge build"):
        run_challenge(root, _runner(calls, status), host_port_for=_same_port)
    assert not any(argv[1] == "run" for argv in calls)


def test_committed_flag_content_is_rejected(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = _manifest(root)
    manifest["spec"]["flags"][0]["content"] = "hg{hidden}"
    _write_manifest(root, manifest)
    calls = []
    with pytest.raises(ToolError, match="content"):
        run_challenge(root, _runner(calls), contents=_fixed, host_port_for=_same_port)
    assert calls == []


def test_scenario_connection_and_ready_probe(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    plan = plan_run(_manifest(root), contents=_fixed, host_port_for=_same_port)
    assert plan.connection == "ssh -p 22 player@127.0.0.1"
    assert plan.probe.kind == "ready-file"
    assert plan.probe.path == "/run/hg/ready"
    assert plan.probe.container == "hg-range-debian-01-box"
    assert plan.flags[0].path == "/flag"
    assert plan.flags[0].owner == "root:root"
    assert plan.flags[0].content == "hg{flag}"


def test_tcp_connection_template(tmp_path):
    root = create_challenge("python-socat", "py-socat-01", tmp_path / "py-socat-01")
    plan = plan_run(_manifest(root), contents=_fixed, host_port_for=_same_port)
    assert plan.connection == "nc 127.0.0.1 5000"


def test_wait_tcp_accepts_an_open_port():
    from hg.run import HealthProbe

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        wait_health(
            HealthProbe(kind="tcp", container="c", host="127.0.0.1", port=port),
            lambda _argv: 0,
            timeout=2,
        )
    finally:
        server.close()


def test_wait_ready_file_polls_docker_exec():
    from hg.run import HealthProbe

    calls = []

    def run(argv):
        calls.append(argv)
        return 0

    wait_health(
        HealthProbe(kind="ready-file", container="hg-box", path="/run/hg/ready"),
        run,
        timeout=1,
    )
    assert calls == [["docker", "exec", "hg-box", "test", "-f", "/run/hg/ready"]]


def test_wait_http_requires_the_status():
    from hg.run import HealthProbe

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _addr = server.accept()
        conn.recv(256)
        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n")
        conn.close()

    import threading

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        wait_health(
            HealthProbe(kind="http", container="c", host="127.0.0.1", port=port, path="/", status=200),
            lambda _argv: 0,
            timeout=2,
        )
    finally:
        thread.join(timeout=2)
        server.close()


def test_users_are_injected_on_the_host(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    plan = plan_run(
        _manifest(root),
        contents=_fixed,
        host_port_for=_same_port,
        password_for=lambda _user: "secretpw",
    )
    run = next(step for step in plan.steps if step.argv and step.argv[1] == "run")
    users = next(arg for arg in run.argv if arg.startswith("USERS="))
    assert json.loads(arg_value(users)) == {"users": [{"name": "player", "password": "secretpw"}]}
    assert plan.users[0].member == "box"
    assert plan.users[0].password == "secretpw"


def arg_value(item: str) -> str:
    return item.split("=", 1)[1]

def test_user_fields_are_forwarded_in_the_payload(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    manifest = _manifest(root)
    manifest["spec"]["hosts"][0]["users"] = [
        {
            "name": "player",
            "passwordFrom": "generated",
            "uid": 2000,
            "group": "staff",
            "groups": ["ctf"],
            "home": "/home/player",
            "shell": "/bin/bash",
            "gecos": "Player",
            "locked": False,
        }
    ]
    _write_manifest(root, manifest)
    plan = plan_run(
        _manifest(root),
        contents=_fixed,
        host_port_for=_same_port,
        password_for=lambda _user: "secretpw",
    )
    run = next(step for step in plan.steps if step.argv and step.argv[1] == "run")
    users = next(arg for arg in run.argv if arg.startswith("USERS="))
    assert json.loads(arg_value(users)) == {
        "users": [
            {
                "name": "player",
                "password": "secretpw",
                "uid": 2000,
                "group": "staff",
                "groups": ["ctf"],
                "home": "/home/player",
                "shell": "/bin/bash",
                "gecos": "Player",
                "locked": False,
            }
        ]
    }

def test_static_password_is_carried_through(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    manifest = _manifest(root)
    manifest["spec"]["hosts"][0]["users"] = [{"name": "svc", "password": "s3cret"}]
    _write_manifest(root, manifest)
    plan = plan_run(
        _manifest(root),
        contents=_fixed,
        host_port_for=_same_port,
        password_for=lambda _user: "generatedpw",
    )
    run = next(step for step in plan.steps if step.argv and step.argv[1] == "run")
    users = next(arg for arg in run.argv if arg.startswith("USERS="))
    assert json.loads(arg_value(users)) == {"users": [{"name": "svc", "password": "s3cret"}]}
    assert plan.users[0].password == "s3cret"

def test_passwordless_user_omits_the_password_key(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    manifest = _manifest(root)
    manifest["spec"]["hosts"][0]["users"] = [{"name": "lockme"}]
    _write_manifest(root, manifest)
    plan = plan_run(_manifest(root), contents=_fixed, host_port_for=_same_port)
    run = next(step for step in plan.steps if step.argv and step.argv[1] == "run")
    users = next(arg for arg in run.argv if arg.startswith("USERS="))
    assert json.loads(arg_value(users)) == {"users": [{"name": "lockme"}]}


def test_depends_on_starts_the_dependency_first(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    bot = root / "services" / "bot"
    bot.mkdir()
    (bot / "Dockerfile").write_text(
        (root / "services" / "web" / "Dockerfile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = _manifest(root)
    web = manifest["spec"]["services"][0]
    web["image"]["name"] = "challenges/web/web-xss-01-web"
    manifest["spec"]["services"] = [
        {
            "name": "bot",
            "build": {"context": "services/bot", "dockerfile": "Dockerfile"},
            "image": {"name": "challenges/web/web-xss-01-bot"},
            "ports": [{"name": "report", "container": 3000, "protocol": "tcp", "publish": False}],
            "health": {"type": "tcp", "port": 3000},
            "dependsOn": ["web"],
            "resources": {"memoryMb": 128, "cpuMhz": 200},
        },
        web,
    ]
    manifest["spec"]["networks"][0]["members"] = ["web", "bot"]
    _write_manifest(root, manifest)
    calls = []
    run_challenge(root, _runner(calls), contents=_fixed, host_port_for=_same_port)
    runs = [argv for argv in calls if argv[1] == "run"]
    assert runs[0][runs[0].index("--name") + 1] == "hg-web-xss-01-web"
    assert runs[1][runs[1].index("--name") + 1] == "hg-web-xss-01-bot"


def test_wait_tcp_times_out():
    from hg.run import HealthProbe

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.close()
    with pytest.raises(ToolError, match="timed out"):
        wait_health(
            HealthProbe(kind="tcp", container="c", host="127.0.0.1", port=port),
            lambda _argv: 1,
            timeout=0.3,
        )


def test_local_run_does_not_mark_the_network_internal(tmp_path):
    """An internal bridge makes Docker drop the published port, so don't use one."""
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    run_challenge(root, _runner(calls), contents=_fixed, host_port_for=_same_port)
    created = next(argv for argv in calls if argv[1:3] == ["network", "create"])
    assert "--internal" not in created


def test_default_run_lets_docker_choose_the_host_port(tmp_path):
    """Without an injected port map, the binding must leave the host port empty."""
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    seen = {}

    def read_port(container, container_port):
        seen["container"] = container
        seen["container_port"] = container_port
        return 49152

    result = run_challenge(
        root,
        _runner(calls),
        contents=_fixed,
        read_port=read_port,
    )
    run_argv = next(argv for argv in calls if argv[1] == "run")
    assert run_argv[run_argv.index("-p") + 1] == "127.0.0.1::80"
    assert seen == {"container": "hg-web-xss-01-web", "container_port": 80}
    assert result.port == 49152
    assert result.connection == "http://127.0.0.1:49152"


def test_unpublished_entry_port_is_a_named_failure(tmp_path):
    """A dropped mapping must not degrade into a 60 second health timeout."""
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    with pytest.raises(ToolError, match="did not publish container port 80"):
        run_challenge(
            root,
            _runner(calls),
            contents=_fixed,
            read_port=lambda _container, _port: None,
        )


def test_plan_defers_the_entry_port_when_docker_chooses(tmp_path):
    plan = plan_run(_manifest(create_challenge("web-nginx", "web-xss-01", tmp_path / "w")))
    assert plan.port is None
    assert plan.connection == ""
    assert plan.probe.host is None
    assert plan.probe.port is None
    assert [(slot.container, slot.container_port) for slot in plan.port_slots] == [
        ("hg-web-xss-01-web", 80)
    ]
