"""
    HunterGround Toolkit
    Test for the contract between the challenge and the verifier
"""

# Test for the contract between the challenge and the verifier

from hg.build       import build_challenge
from hg.clean       import clean_challenge
from hg.digest      import manifest_digest
from hg.errors      import ToolError
from hg.lint        import lint_challenge
from hg.run         import plan_run, run_challenge
from hg.scaffold    import WRITEUP, create_challenge
from hg.verify      import verify_challenge
import json
import pytest
import yaml

DIGEST = "sha256:" + ("ab" * 32)


def _write(root, manifest):
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _manifest(root):
    return yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))


def _same_port(port: int) -> int:
    return port


def _runner(calls, write_dist=None):
    def run(argv):
        calls.append(argv)
        if write_dist is not None and argv and argv[0] == "sh":
            write_dist()
        return 0

    return run


def test_digest_ignores_key_order():
    left  = manifest_digest({"b": 1, "a": {"d": 1, "c": 2}})
    right = manifest_digest({"a": {"c": 2, "d": 1}, "b": 1})
    assert left == right
    assert left.startswith("sha256:")
    assert len(left) == 7 + 64


def test_lint_rejects_an_unknown_key(tmp_path):
    root     = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = _manifest(root)
    manifest["metadata"]["extra"] = "nope"
    _write(root, manifest)
    errors = lint_challenge(root)
    assert any("extra" in error for error in errors)


def test_lint_rejects_a_flag_literal(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    page = root / "services" / "web" / "html" / "index.html"
    page.write_text(page.read_text(encoding="utf-8") + "\nhg{hidden}\n", encoding="utf-8")
    errors = lint_challenge(root)
    assert any("flag literal" in error for error in errors)


def test_lint_allows_a_builder_stage(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    dockerfile = root / "services" / "web" / "Dockerfile"
    dockerfile.write_text(
        'FROM scratch AS build\nENTRYPOINT ["/bin/build"]\n' + dockerfile.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert lint_challenge(root) == []


def test_strict_requires_real_private_files(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    assert _manifest(root)["spec"]["services"][0]["build"]["fromDigest"] == DIGEST
    errors = lint_challenge(root, strict=True)
    assert not any("fromDigest" in error for error in errors)
    assert any("writeup.md is still the scaffold stub" in error for error in errors)
    assert any("solve.sh is still the scaffold stub" in error for error in errors)


def test_spawn_image_rejects_a_compose_file(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    errors = lint_challenge(root)
    assert any("compose.yaml" in error for error in errors)


def test_url_template_requires_an_http_port(tmp_path):
    root = create_challenge("python-socat", "py-socat-01", tmp_path / "py-socat-01")
    manifest = _manifest(root)
    manifest["spec"]["player"]["connection"] = "{url}"
    _write(root, manifest)
    errors = lint_challenge(root)
    assert any("{url}" in error for error in errors)


def test_build_uses_from_digest(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["build"]["fromDigest"] = DIGEST
    _write(root, manifest)
    calls = []
    build_challenge(root, _runner(calls))
    builds = [argv for argv in calls if "buildx" in argv]
    assert f"BASE_WEB_NGINX_IMAGE=hunterground/base-web-nginx@{DIGEST}" in builds[0]


def test_sidecar_is_inspected_and_not_built(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = _manifest(root)
    web = manifest["spec"]["services"][0]
    digest = "sha256:" + ("cd" * 32)
    manifest["spec"]["services"] = [
        web,
        {
            "name": "db",
            "image": {"name": "mysql", "digest": digest},
            "ports": [{"name": "mysql", "container": 3306, "protocol": "tcp", "publish": False}],
            "health": {"type": "tcp", "port": 3306},
            "resources": {"memoryMb": 256, "cpuMhz": 200},
        },
    ]
    manifest["spec"]["networks"][0]["members"] = ["web", "db"]
    _write(root, manifest)
    assert lint_challenge(root) == []

    calls = []
    tags = build_challenge(root, _runner(calls))
    assert tags == ["challenges/web/web-xss-01:local"]
    assert ["docker", "image", "inspect", f"mysql@{digest}"] in calls
    assert not any("mysql" in arg for argv in calls if "buildx" in argv for arg in argv)


def _compose_root(tmp_path, service_extra=""):
    root = tmp_path / "web-xss-01"
    (root / "services" / "web").mkdir(parents=True)
    (root / "private").mkdir()
    (root / "private" / "writeup.md").write_text("The intended path.\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (root / ".gitignore").write_text("dist/\n.hg/\n", encoding="utf-8")
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
                service_extra,
            ]
        ),
        encoding="utf-8",
    )
    manifest = {
        "apiVersion": "hg.challenge/v1",
        "kind": "Challenge",
        "metadata": {
            "id": "web-xss-01",
            "title": "Reflected note",
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
    _write(root, manifest)
    return root


def test_compose_spawn_lints_and_plans_an_override(tmp_path):
    root = _compose_root(tmp_path)
    assert lint_challenge(root) == []
    plan = plan_run(_manifest(root), root=root, host_port_for=_same_port)
    override = yaml.safe_load(plan.override)
    web = override["services"]["web"]
    assert web["image"] == "challenges/web/web-xss-01:local"
    assert web["ports"] == ["127.0.0.1:80:80"]
    assert json.loads(web["environment"]["FLAGS"])["flags"][0]["path"] == "/app/flag.txt"
    assert plan.connection == "http://127.0.0.1:80"
    assert "__OVERRIDE__" in plan.steps[-1].argv
    calls = []
    removed = clean_challenge(root, _runner(calls))
    assert removed == ["challenges/web/web-xss-01:local"]
    assert calls[0][:3] == ["docker", "compose", "-f"]
    assert "down" in calls[0]


def test_compose_defers_the_host_port_to_docker(tmp_path):
    root = _compose_root(tmp_path)
    plan = plan_run(_manifest(root), root=root)
    web = yaml.safe_load(plan.override)["services"]["web"]
    assert web["ports"] == ["127.0.0.1::80"]
    assert plan.port is None
    assert [(slot.container, slot.container_port) for slot in plan.port_slots] == [
        ("hg-web-xss-01-web", 80)
    ]

def test_compose_injects_users(tmp_path):
    root = _compose_root(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["compose"]["users"] = {
        "web": [
            {"name": "player", "passwordFrom": "generated", "uid": 2000},
            {"name": "svc", "password": "s3cret"},
        ]
    }
    _write(root, manifest)
    assert lint_challenge(root) == []

    plan = plan_run(_manifest(root), 
            root          = root, 
            host_port_for = _same_port,
            password_for  = lambda _user: "secretpw"
    )
    web = yaml.safe_load(plan.override)["services"]["web"]
    assert json.loads(web["environment"]["USERS"]) == {
        "users": [
            {"name": "player", "password": "secretpw", "uid": 2000},
            {"name": "svc", "password": "s3cret"},
        ]
    }
    assert [(u.member, u.name) for u in plan.users] == [("web", "player"), ("web", "svc")]

def test_compose_users_rejects_unknown_service(tmp_path):
    root = _compose_root(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["compose"]["users"] = {"ghost": [{"name": "player", "passwordFrom": "generated"}]}
    _write(root, manifest)
    errors = lint_challenge(root)
    assert any("not a compose service" in error for error in errors)


def test_compose_run_reads_the_assigned_port(tmp_path):
    root = _compose_root(tmp_path)
    (root / "private" / "writeup.md").write_text("The intended path.\n", encoding="utf-8")
    calls = []
    result = run_challenge(
        root,
        _runner(calls),
        contents=lambda flag: "hg{" + flag["id"] + "}",
        read_port=lambda _container, _port: 32768,
    )
    assert result.port == 32768
    assert result.connection == "http://127.0.0.1:32768"


def test_compose_rejects_a_privileged_service(tmp_path):
    root = _compose_root(tmp_path, service_extra="    privileged: true")
    errors = lint_challenge(root)
    assert any("privileged" in error for error in errors)


def _static_root(tmp_path):
    root = tmp_path / "static-01"
    (root / "private").mkdir(parents=True)
    (root / "dist").mkdir()
    (root / ".gitignore").write_text("dist/\n.hg/\n", encoding="utf-8")
    (root / "private" / "writeup.md").write_text("Read the handout.\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\necho hg{static}\n", encoding="utf-8")
    (root / "private" / "build.sh").write_text("#!/bin/sh\nmkdir -p dist\necho hi > dist/handout.txt\n", encoding="utf-8")
    (root / "dist" / "handout.txt").write_text("hi\n", encoding="utf-8")
    manifest = {
        "apiVersion": "hg.challenge/v1",
        "kind": "Challenge",
        "metadata": {
            "id": "static-01",
            "title": "Handout",
            "category": "misc",
            "description": "A file.",
            "difficulty": "easy",
            "author": "ada",
            "value": 50,
            "state": "hidden",
            "version": "1",
        },
        "spec": {
            "runtime": "static",
            "flags": [{"id": "flag", "type": "static", "content": "hg{static}"}],
            "scripts": {"test": "private/solve.sh", "build": "private/build.sh"},
            "files": [{"name": "handout.txt", "source": "build"}],
        },
    }
    _write(root, manifest)
    return root


def test_static_build_runs_the_script_and_not_docker(tmp_path):
    root = _static_root(tmp_path)
    assert lint_challenge(root) == []
    calls = []
    assert build_challenge(root, _runner(calls)) == []
    assert calls == [["sh", str((root / "private" / "build.sh").resolve())]]
    assert clean_challenge(root, _runner(calls)) == []


def test_static_staging_file_requires_a_hash(tmp_path):
    root = _static_root(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["files"] = [{"name": "dump.pcap", "source": "staging"}]
    del manifest["spec"]["scripts"]["build"]
    _write(root, manifest)
    errors = lint_challenge(root)
    assert any("sha256" in error for error in errors)


def test_verify_static_accepts_the_flag(tmp_path):
    root = _static_root(tmp_path)

    def solve(_script, _env):
        return 0, "hg{static}\n"

    assert verify_challenge(root, _runner([]), solve=solve) is None


def test_verify_container_exports_the_connection(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["build"]["fromDigest"] = DIGEST
    _write(root, manifest)
    (root / "private" / "writeup.md").write_text("Click the page.\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    assert (root / "private" / "writeup.md").read_text(encoding="utf-8") != WRITEUP
    seen = {}

    def solve(_script, env):
        seen.update(env)
        return 0, "hg{flag}\n"

    result = verify_challenge(
        root,
        _runner([]),
        solve=solve,
        contents=lambda flag: "hg{" + flag["id"] + "}",
        host_port_for=_same_port,
    )
    assert result is not None
    assert seen["CHALLENGE_HOST"] == "127.0.0.1"
    assert seen["CHALLENGE_PORT"] == "80"
    assert seen["CHALLENGE_CONNECTION"] == "http://127.0.0.1:80"
    assert seen["CHALLENGE_FLAG_FLAG"] == "hg{flag}"
    assert "CHALLENGE_URL" not in seen


def test_verify_uses_the_port_docker_assigned(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["build"]["fromDigest"] = DIGEST
    _write(root, manifest)
    (root / "private" / "writeup.md").write_text("Click the page.\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    seen = {}

    def solve(_script, env):
        seen.update(env)
        return 0, "hg{flag}\n"

    result = verify_challenge(
        root,
        _runner([]),
        solve=solve,
        contents=lambda flag: "hg{" + flag["id"] + "}",
        read_port=lambda _container, _port: 32768,
    )
    assert result is not None
    assert seen["CHALLENGE_PORT"] == "32768"
    assert seen["CHALLENGE_CONNECTION"] == "http://127.0.0.1:32768"


def test_verify_rejects_a_solver_that_misses_the_flag(tmp_path):
    root = _static_root(tmp_path)
    with pytest.raises(ToolError, match="flag"):
        verify_challenge(root, _runner([]), solve=lambda _script, _env: (0, "nope\n"))

def test_verify_exports_generated_users(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    manifest = _manifest(root)
    manifest["spec"]["hosts"][0]["build"]["fromDigest"] = DIGEST
    _write(root, manifest)
    (root / "private" / "writeup.md").write_text("The intended path.\n", encoding="utf-8")
    (root / "private" / "solve.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    seen = {}

    def solve(_script, env):
        seen.update(env)
        return 0, "hg{flag}\n"

    result = verify_challenge(
        root,
        _runner([]),
        solve         = solve,
        contents      = lambda flag: "hg{" + flag["id"] + "}",
        password_for  = lambda _user: "secretpw",
        host_port_for = _same_port,
    )
    assert result is not None
    assert seen["CHALLENGE_USER_BOX_PLAYER"] == "secretpw"
