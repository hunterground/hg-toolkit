"""
    HunterGround Toolkit
    Test for reporting the local challenge graph
"""

from datetime       import datetime, timezone
from hg.errors      import ToolError
from hg.run         import HealthProbe, check_health
from hg.scaffold    import create_challenge
from hg.state       import write_state
from hg.status      import status_challenge
import json
import pytest
import yaml


NOW = datetime(2026, 10, 18, 3, 12, tzinfo=timezone.utc)


## Helper Functions ----------------------------------------------------------------------
def _docker(containers=None, networks=None, daemon=False):
    calls = []
    containers = containers or {}
    networks = networks or {}

    def run(argv):
        calls.append(argv)
        if daemon:
            return 1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
        if argv[1] == "network":
            name = argv[-1]
            if networks.get(name):
                return 0, "[]", ""
            return 1, "", f"Error: No such network: {name}"
        body = containers.get(argv[-1])
        if body is None:
            return 1, "", f"Error: No such object: {argv[-1]}"
        return 0, json.dumps(body), ""

    return run, calls


def _running(port="52341", health="healthy", image="challenges/web/web-xss-01:local", status="running"):
    return {
        "Status": status,
        "ExitCode": 0 if status == "running" else 1,
        "StartedAt": "2026-10-18T03:00:00Z",
        "FinishedAt": "0001-01-01T00:00:00Z" if status == "running" else "2026-10-18T03:09:00Z",
        "Health": health,
        "Image": image,
        "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]},
    }


def _record(root, port=52341):
    write_state(
        root,
        {
            "connection": f"http://127.0.0.1:{port}",
            "host": "127.0.0.1",
            "port": port,
            "flags": [{"id": "flag", "content": "hg{live}"}],
            "users": [{"member": "web", "name": "player", "password": "secret"}],
            "containers": [{"name": "hg-web-xss-01-web", "image": "challenges/web/web-xss-01:local"}],
        },
    )


## Main Tests ---------------------------------------------------------------------------
def test_status_reports_a_running_graph(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    _record(root)
    docker, calls = _docker(
        containers={"hg-web-xss-01-web": _running()},
        networks={"hg-web-xss-01-default": True},
    )
    seen = []

    def probe(health):
        seen.append(health)
        return True

    report = status_challenge(root, docker=docker, probe=probe, now=NOW)
    assert report.ok
    assert report.summary == "running"
    assert report.challenge_id == "web-xss-01"
    assert report.connection == "http://127.0.0.1:52341"
    assert report.flags == (("flag", "hg{live}"),)
    assert report.users == (("web player", "secret"),)
    assert report.health == "ok"
    assert report.warnings == ()
    container = report.containers[0]
    assert container.name == "hg-web-xss-01-web"
    assert container.state == "running"
    assert container.detail == "Up 12 minutes (healthy)"
    assert container.ports == "127.0.0.1:52341->80/tcp"
    assert container.image == "challenges/web/web-xss-01:local"
    assert report.networks == (("hg-web-xss-01-default", True),)
    assert seen[0].container == "hg-web-xss-01-web"
    assert seen[0].port == 52341
    assert seen[0].host == "127.0.0.1"
    assert any(argv[:3] == ["docker", "inspect", "--format"] for argv in calls)


def test_status_without_a_run_is_not_started(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    docker, _calls = _docker()
    probed = []
    report = status_challenge(root, docker=docker, probe=lambda health: probed.append(health) or True, now=NOW)
    assert not report.ok
    assert report.summary == "not started"
    assert report.containers[0].state == "missing"
    assert report.containers[0].image.endswith(":local")
    assert report.networks == (("hg-web-xss-01-default", False),)
    assert report.connection == ""
    assert probed == []


def test_status_with_a_recorded_run_and_no_container_is_stopped(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    _record(root)
    docker, _calls = _docker()
    report = status_challenge(root, docker=docker, probe=lambda _health: True, now=NOW)
    assert report.summary == "stopped"
    assert report.connection == "http://127.0.0.1:52341"
    assert report.flags == (("flag", "hg{live}"),)
    assert report.health == ""


def test_status_reports_an_exited_container(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    docker, _calls = _docker(containers={"hg-web-xss-01-web": _running(status="exited", health=None)})
    report = status_challenge(root, docker=docker, probe=lambda _health: True, now=NOW)
    assert report.summary == "stopped"
    assert report.containers[0].state == "exited"
    assert report.containers[0].detail == "Exited (1) 3 minutes ago"


def test_status_warns_when_the_published_port_drifted(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    _record(root, port=111)
    docker, _calls = _docker(
        containers={"hg-web-xss-01-web": _running(port="52341")},
        networks={"hg-web-xss-01-default": True},
    )
    report = status_challenge(root, docker=docker, probe=lambda _health: True, now=NOW)
    assert report.connection == "http://127.0.0.1:52341"
    assert report.warnings == (
        "recorded port 111 does not match the published port 52341",
    )


def test_status_keeps_running_when_the_health_check_fails(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    docker, _calls = _docker(containers={"hg-web-xss-01-web": _running(health=None)})
    report = status_challenge(root, docker=docker, probe=lambda _health: False, now=NOW)
    assert report.ok
    assert report.summary == "running"
    assert report.health == "failed"
    assert report.warnings == ("player entry health check failed",)
    assert report.containers[0].detail == "Up 12 minutes"


def test_status_is_partial_when_one_member_is_down(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    bot = root / "services" / "bot"
    bot.mkdir()
    (bot / "Dockerfile").write_text(
        (root / "services" / "web" / "Dockerfile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))
    web = manifest["spec"]["services"][0]
    web["image"]["name"] = "challenges/web/web-xss-01-web"
    manifest["spec"]["services"] = [
        web,
        {
            "name": "bot",
            "build": {"context": "services/bot", "dockerfile": "Dockerfile"},
            "image": {"name": "challenges/web/web-xss-01-bot"},
            "ports": web["ports"],
            "health": web["health"],
            "resources": web["resources"],
        },
    ]
    manifest["spec"]["networks"][0]["members"] = ["web", "bot"]
    (root / "challenge.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    docker, _calls = _docker(containers={"hg-web-xss-01-web": _running()})
    report = status_challenge(root, docker=docker, probe=lambda _health: True, now=NOW)
    assert report.summary == "partial"
    assert [item.state for item in report.containers] == ["running", "missing"]
    assert not report.ok


def test_status_names_a_scenario_host(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    docker, calls = _docker()
    report = status_challenge(root, docker=docker, probe=lambda _health: True, now=NOW)
    assert report.summary == "not started"
    assert report.containers[0].name == "hg-range-debian-01-box"
    assert report.networks == (("hg-range-debian-01-default", False),)
    assert calls[0][-1] == "hg-range-debian-01-box"


def test_status_names_compose_services_and_skips_networks(tmp_path):
    root = tmp_path / "web-xss-01"
    (root / "services" / "web").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "private" / "writeup.md").write_text("The intended path.\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (root / "services" / "web" / "Dockerfile").write_text(
        "ARG BASE_WEB_NGINX_IMAGE=base/web-nginx:latest\nFROM ${BASE_WEB_NGINX_IMAGE}\n",
        encoding="utf-8",
    )
    (root / "compose.yaml").write_text(
        "\n".join([
            "services:",
            "  web:",
            "    build: services/web",
            "    image: challenges/web/web-xss-01",
            "    labels:",
            '      hg.port.http: "80"',
            "    ports:",
            '      - "80:80"',
        ]),
        encoding="utf-8",
    )
    manifest = {
        "apiVersion": "hg.challenge/v1",
        "kind": "Challenge",
        "metadata": {
            "id": "web-xss-01",
            "title": "A note",
            "category": "web",
            "description": "A note.",
            "difficulty": "easy",
            "author": "ada",
            "value": 100,
            "state": "hidden",
            "version": "1",
        },
        "spec": {
            "runtime": "container",
            "spawn": "compose",
            "platform": ["linux/amd64", "linux/arm64"],
            "flags": [{
                "id": "flag",
                "type": "dynamic",
                "path": "/app/flag.txt",
                "owner": "nginx:nginx",
                "mode": "0400",
                "service": "web",
            }],
            "scripts": {"test": "private/solve.sh"},
            "compose": {"file": "compose.yaml"},
            "player": {"entry": "web.http", "connection": "{url}"},
        },
    }
    (root / "challenge.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    docker, calls = _docker(containers={"hg-web-xss-01-web": _running()})
    seen = []
    report = status_challenge(
        root,
        docker=docker,
        probe=lambda health: seen.append(health) or True,
        now=NOW,
    )
    assert report.summary == "running"
    assert report.containers[0].name == "hg-web-xss-01-web"
    assert report.containers[0].image == "challenges/web/web-xss-01:local"
    assert report.networks == ()
    assert seen[0].kind == "http"
    assert seen[0].port == 52341
    assert not any(argv[1] == "network" for argv in calls)


def test_status_static_does_not_call_docker(tmp_path):
    import importlib.util
    import pathlib as _pathlib

    helper = _pathlib.Path(__file__).with_name("test_contract.py")
    spec = importlib.util.spec_from_file_location("_contract_helper", helper)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    root = contract._static_root(tmp_path)
    docker, calls = _docker()
    report = status_challenge(root, docker=docker, now=NOW)
    assert report.ok
    assert report.summary == "static"
    assert report.challenge_id == "static-01"
    assert calls == []


def test_status_lint_failure_does_not_call_docker(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))
    manifest["spec"]["flags"][0]["content"] = "hg{hidden}"
    (root / "challenge.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    docker, calls = _docker()
    with pytest.raises(ToolError, match="content"):
        status_challenge(root, docker=docker, now=NOW)
    assert calls == []


def test_status_reports_a_missing_docker_daemon(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    docker, _calls = _docker(daemon=True)
    with pytest.raises(ToolError, match="docker is not available"):
        status_challenge(root, docker=docker, now=NOW)


def test_check_health_tries_once():
    calls = []

    def run(argv):
        calls.append(argv)
        return 1

    probe = HealthProbe(kind="exec", container="hg-web-xss-01-web", command="true")
    assert check_health(probe, run) is False
    assert len(calls) == 1
    assert calls[0][:3] == ["docker", "exec", "hg-web-xss-01-web"]


def test_cli_status_prints_the_report_and_exits_when_stopped(tmp_path, monkeypatch, capsys):
    from hg import cli

    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    docker, _calls = _docker()
    monkeypatch.setattr("hg.status._docker", docker)
    with pytest.raises(SystemExit) as exc:
        cli.main(["challenge", "status", "--path", str(root)])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "not started" in out
    assert "web-xss-01" in out
    assert "hg-web-xss-01-web" in out


def test_cli_status_exits_zero_when_running(tmp_path, monkeypatch, capsys):
    from hg import cli

    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    _record(root)
    docker, _calls = _docker(
        containers={"hg-web-xss-01-web": _running()},
        networks={"hg-web-xss-01-default": True},
    )
    monkeypatch.setattr("hg.status._docker", docker)
    monkeypatch.setattr("hg.status.check_health", lambda _probe: True)
    cli.main(["challenge", "status", "--path", str(root)])
    out = capsys.readouterr().out
    assert "running" in out
    assert "hg{live}" in out
    assert "127.0.0.1:52341->80/tcp" in out
    assert "present" in out
