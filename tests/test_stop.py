"""
    HunterGround Toolkit
    Test for stopping the challenge instance
"""

from hg.errors      import ToolError
from hg.run         import stop_challenge
from hg.scaffold    import create_challenge
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

## Main Tests ---------------------------------------------------------------------------
def test_stop_removes_the_run_graph_in_order(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    removed = stop_challenge(root, _runner(calls))
    assert removed == ["hg-web-xss-01-web", "hg-web-xss-01-default"]
    assert calls == [
        ["docker", "rm", "-f", "hg-web-xss-01-web"],
        ["docker", "network", "rm", "hg-web-xss-01-default"],
    ]

def test_stop_keeps_the_image(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    stop_challenge(root, _runner(calls))
    assert not any(argv[1] == "image" for argv in calls)
    assert not any("base/web-nginx" in arg for argv in calls for arg in argv)

def test_stop_removes_every_member_and_network(tmp_path):
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
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    calls = []
    removed = stop_challenge(root, _runner(calls))
    assert removed == [
        "hg-web-xss-01-web",
        "hg-web-xss-01-bot",
        "hg-web-xss-01-default",
    ]

def test_stop_scenario_uses_the_host_name(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    calls = []
    removed = stop_challenge(root, _runner(calls))
    assert removed == ["hg-range-debian-01-box", "hg-range-debian-01-default"]
    assert ["docker", "rm", "-f", "hg-range-debian-01-box"] in calls

def test_stop_tolerates_an_already_stopped_graph(tmp_path):
    """Every step is quiet, so a missing container or network is not an error."""
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    removed = stop_challenge(root, _runner(calls, status=lambda _argv: 1))
    assert removed == ["hg-web-xss-01-web", "hg-web-xss-01-default"]

def test_stop_compose_runs_compose_down(tmp_path):
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
        "\n".join(
            [
                "services:",
                "  web:",
                "    build: services/web",
                "    image: challenges/web/web-xss-01",
                "    labels:",
                '      hg.port.http: "80"',
                "    ports:",
                '      - "80:80"',
            ]
        ),
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
            "flags": [
                {
                    "id": "flag",
                    "type": "dynamic",
                    "path": "/app/flag.txt",
                    "owner": "nginx:nginx",
                    "mode": "0400",
                    "service": "web",
                }
            ],
            "scripts": {"test": "private/solve.sh"},
            "compose": {"file": "compose.yaml"},
            "player": {"entry": "web.http", "connection": "{url}"},
        },
    }
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    calls = []
    removed = stop_challenge(root, _runner(calls))
    assert removed == ["hg-web-xss-01"]
    assert calls[0][:3] == ["docker", "compose", "-f"]
    assert "down" in calls[0]
    assert not any(argv[1] == "image" for argv in calls)

def test_stop_static_does_nothing(tmp_path):
    """A static challenge has no Docker graph, so stop is a no-op."""
    import importlib.util
    import pathlib as _pathlib

    helper = _pathlib.Path(__file__).with_name("test_contract.py")
    spec = importlib.util.spec_from_file_location("_contract_helper", helper)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    root = contract._static_root(tmp_path)
    calls = []
    assert stop_challenge(root, _runner(calls)) == []
    assert calls == []

def test_stop_lint_failure_does_not_call_docker(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))
    manifest["spec"]["flags"][0]["content"] = "hg{hidden}"
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    calls = []
    with pytest.raises(ToolError, match="content"):
        stop_challenge(root, _runner(calls))
    assert calls == []
