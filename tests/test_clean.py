"""
    HunterGround Toolkit
    Test for cleaning up the challenge images
"""

from hg.clean       import clean_challenge
from hg.errors      import ToolError
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


def _kinds(calls):
    kinds = []
    for argv in calls:
        if argv[1:3] == ["rm", "-f"]:
            kinds.append("rm")
        elif argv[1:3] == ["network", "rm"]:
            kinds.append("network-rm")
        elif argv[1:3] == ["image", "inspect"]:
            kinds.append("inspect")
        elif argv[1:3] == ["image", "rm"]:
            kinds.append("image-rm")
        else:
            kinds.append("other")
    return kinds


## Main Tests ---------------------------------------------------------------------------
def test_clean_removes_the_local_image_after_the_run_graph(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    removed = clean_challenge(root, _runner(calls))

    assert removed == ["challenges/web/web-xss-01:local"]
    assert calls == [
        ["docker", "rm", "-f", "hg-web-xss-01-web"],
        ["docker", "network", "rm", "hg-web-xss-01-default"],
        ["docker", "image", "inspect", "challenges/web/web-xss-01:local"],
        ["docker", "image", "rm", "challenges/web/web-xss-01:local"],
    ]
    assert not any("base/web-nginx" in arg for argv in calls for arg in argv)


def test_missing_image_is_left_alone(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []

    def status(argv):
        return 1 if argv[1:3] == ["image", "inspect"] else 0

    assert clean_challenge(root, _runner(calls, status)) == []
    assert _kinds(calls) == ["rm", "network-rm", "inspect"]


def test_image_rm_failure_is_reported(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []

    def status(argv):
        return 1 if argv[1:3] == ["image", "rm"] else 0

    with pytest.raises(ToolError, match="challenges/web/web-xss-01:local"):
        clean_challenge(root, _runner(calls, status))
    assert calls[-1][1:3] == ["image", "rm"]


def test_clean_removes_each_member_image(tmp_path):
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
            "name":     "bot",
            "build":    {"context": "services/bot", "dockerfile": "Dockerfile"},
            "image":    {"name": "challenges/web/web-xss-01-bot"},
            "ports":    web["ports"],
            "health":   web["health"],
            "resources": web["resources"],
        },
    ]
    manifest["spec"]["networks"][0]["members"] = ["web", "bot"]
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    calls = []
    removed = clean_challenge(root, _runner(calls))
    assert removed == [
        "challenges/web/web-xss-01-web:local",
        "challenges/web/web-xss-01-bot:local",
    ]
    assert ["docker", "rm", "-f", "hg-web-xss-01-web"] in calls
    assert ["docker", "rm", "-f", "hg-web-xss-01-bot"] in calls
    assert _kinds(calls) == [
        "rm",
        "rm",
        "network-rm",
        "inspect",
        "image-rm",
        "inspect",
        "image-rm",
    ]


def test_scenario_image_uses_the_host_name(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    calls = []
    removed = clean_challenge(root, _runner(calls))
    assert removed == ["challenges/range/range-debian-01:local"]
    assert ["docker", "rm", "-f", "hg-range-debian-01-box"] in calls


def test_lint_failure_does_not_call_docker(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    manifest = yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))
    manifest["spec"]["flags"][0]["content"] = "hg{hidden}"
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    calls = []
    with pytest.raises(ToolError, match="content"):
        clean_challenge(root, _runner(calls))
    assert calls == []
