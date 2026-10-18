"""
    HunterGround Toolkit
    Test for building the challenge images
"""

from hg.build       import build_challenge, host_platform
from hg.errors      import ToolError
from hg.scaffold    import create_challenge
import pytest
import yaml


## Helper Functions ----------------------------------------------------------------------
def _runner(calls, inspect_status):
    def run(argv):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return inspect_status(argv)
        return 0

    return run


def _runner_pull(calls, inspect_status, pull_status=None):
    def run(argv):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return inspect_status(argv)
        if argv[0:2] == ["docker", "pull"]:
            return pull_status(argv) if pull_status is not None else 0
        return 0

    return run


## Main Tests ---------------------------------------------------------------------------
def test_build_argv_loads_the_host_platform_and_catalog_tag(tmp_path):
    root  = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    tags  = build_challenge(root, _runner(calls, lambda _argv: 0))

    inspects = [argv for argv in calls if argv[1:3] == ["image", "inspect"]]
    builds   = [argv for argv in calls if "buildx" in argv]
    assert inspects == [["docker", "image", "inspect", "base/web-nginx:latest"]]
    assert len(builds) == 1

    argv = builds[0]
    digest = yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))
    digest = digest["spec"]["services"][0]["build"]["fromDigest"]
    assert "--load" in argv
    assert argv[argv.index("--platform") + 1] == host_platform()
    assert argv[argv.index("--tag") + 1] == "challenges/web/web-xss-01:local"
    assert f"BASE_WEB_NGINX_IMAGE=hunterground/base-web-nginx@{digest}" in argv
    assert tags == ["challenges/web/web-xss-01:local"]


def test_range_build_inspects_the_pack_and_passes_both_args(tmp_path):
    root  = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    calls = []
    tags  = build_challenge(root, _runner(calls, lambda _argv: 0))

    inspected = [argv[-1] for argv in calls if argv[1:3] == ["image", "inspect"]]
    assert inspected == ["range/debian:latest", "range/pack/sshd:latest"]

    builds = [argv for argv in calls if "buildx" in argv]
    digest = yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))
    digest = digest["spec"]["hosts"][0]["build"]["fromDigest"]
    assert len(builds) == 1
    assert f"RANGE_DEBIAN_IMAGE=hunterground/range-debian@{digest}" in builds[0]
    assert "RANGE_PACK_SSHD_IMAGE=range/pack/sshd:latest" in builds[0]
    assert builds[0][builds[0].index("--tag") + 1] == "challenges/range/range-debian-01:local"
    assert tags == ["challenges/range/range-debian-01:local"]


def test_missing_base_pulls_from_hub_and_retags(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    tags  = build_challenge(root, _runner_pull(calls, lambda _argv: 1))

    pulls = [argv for argv in calls if argv[0:2] == ["docker", "pull"]]
    assert pulls == [["docker", "pull", "--platform", host_platform(), "hunterground/base-web-nginx:latest"]]

    tags_ = [argv for argv in calls if argv[0:2] == ["docker", "tag"]]
    assert tags_ == [["docker", "tag", "hunterground/base-web-nginx:latest", "base/web-nginx:latest"]]
    assert tags == ["challenges/web/web-xss-01:local"]

def test_missing_base_names_hub_ref_on_pull_failure_and_skips_buildx(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    calls = []
    with pytest.raises(ToolError, match=r"cannot fetch base image base/web-nginx:latest from hunterground/base-web-nginx:latest"):
        build_challenge(root, _runner_pull(calls, lambda _argv: 1, lambda _argv: 1))
    assert not any("buildx" in argv for argv in calls)


def test_missing_pack_pulls_from_hub_and_retags(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    calls = []

    def status(argv):
        return 1 if argv[-1] == "range/pack/sshd:latest" else 0

    tags  = build_challenge(root, _runner_pull(calls, status))
    pulls = [argv for argv in calls if argv[0:2] == ["docker", "pull"]]
    assert ["docker", "pull", "--platform", host_platform(), "hunterground/range-pack-sshd:latest"] in pulls

    tags_ = [argv for argv in calls if argv[0:2] == ["docker", "tag"]]
    assert ["docker", "tag", "hunterground/range-pack-sshd:latest", "range/pack/sshd:latest"] in tags_
    assert tags == ["challenges/range/range-debian-01:local"]


def test_missing_builder_stage_pulls_from_hub_and_retags(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    dockerfile = root / "services" / "web" / "Dockerfile"
    dockerfile.write_text(
        "FROM scratch AS prep\n"
        "FROM builder/cpp:latest AS build\n"
        + dockerfile.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    calls = []
    tags = build_challenge(root, _runner_pull(calls, lambda _argv: 1))

    pulls = [argv for argv in calls if argv[0:2] == ["docker", "pull"]]
    assert ["docker", "pull", "--platform", host_platform(), "hunterground/builder-cpp:latest"] in pulls
    assert not any("scratch" in argv[-1] for argv in pulls)

    tagged = [argv for argv in calls if argv[0:2] == ["docker", "tag"]]
    assert ["docker", "tag", "hunterground/builder-cpp:latest", "builder/cpp:latest"] in tagged
    assert tags == ["challenges/web/web-xss-01:local"]
