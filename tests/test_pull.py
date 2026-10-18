"""
    HunterGround Toolkit
    Test for pulling base and builder images from the public hub
"""

from hg.build   import host_platform
from hg.catalog import BASES, BUILDERS, hub_ref
from hg.errors  import ToolError
from hg.pull    import pull_bases, pull_builders
import pytest


def _tags():
    return [tag for base in BASES for tag in (base.image, *base.extra_inspect)]


def _runner(calls, pull_status=None, tag_status=None):
    def run(argv):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return 1
        if argv[0:2] == ["docker", "pull"] and pull_status is not None:
            return pull_status(argv)
        if argv[0:2] == ["docker", "tag"] and tag_status is not None:
            return tag_status(argv)
        return 0

    return run


def test_no_names_pulls_every_catalog_image_and_retags():
    calls    = []
    expected = _tags()
    result   = pull_bases(None, _runner(calls))

    assert result.pulled == tuple(expected)
    assert result.missing == ()
    assert [argv for argv in calls if argv[0:2] == ["docker", "pull"]] == [
        ["docker", "pull", "--platform", host_platform(), hub_ref(tag)]
        for tag in expected
    ]
    assert [argv for argv in calls if argv[0:2] == ["docker", "tag"]] == [
        ["docker", "tag", hub_ref(tag), tag]
        for tag in expected
    ]


def test_one_name_pulls_only_that_base():
    calls  = []
    result = pull_bases(["web-nginx"], _runner(calls))

    assert result.pulled == ("base/web-nginx:latest",)
    assert result.missing == ()
    assert [argv[-1] for argv in calls if argv[0:2] == ["docker", "pull"]] == [
        "hunterground/base-web-nginx:latest",
    ]


def test_missing_image_is_recorded_and_later_tags_still_pull():
    calls = []
    missed = "hunterground/base-web-nginx:latest"

    def pull_status(argv):
        return 1 if argv[-1] == missed else 0

    result = pull_bases(None, _runner(calls, pull_status))
    pulled_refs = [argv[-1] for argv in calls if argv[0:2] == ["docker", "pull"]]

    assert "base/web-nginx:latest" in result.missing
    assert "base/web-nginx:latest" not in result.pulled
    assert result.pulled[-1] == _tags()[-1]
    assert pulled_refs.index(missed) < len(pulled_refs) - 1
    assert not any(
        argv[0:2] == ["docker", "tag"] and argv[-1] == "base/web-nginx:latest"
        for argv in calls
    )


def test_tag_failure_stops_before_later_bases():
    calls = []

    def tag_status(_argv):
        return 1

    with pytest.raises(ToolError, match=r"cannot tag it as base/web-nginx:latest"):
        pull_bases(["web-nginx", "python"], _runner(calls, tag_status=tag_status))

    assert [argv[-1] for argv in calls if argv[0:2] == ["docker", "pull"]] == [
        "hunterground/base-web-nginx:latest",
    ]


def _builder_tags():
    return [builder.image for builder in BUILDERS]


def test_no_names_pulls_every_builder_and_retags():
    calls    = []
    expected = _builder_tags()
    result   = pull_builders(None, _runner(calls))

    assert result.pulled == tuple(expected)
    assert result.missing == ()
    assert [argv for argv in calls if argv[0:2] == ["docker", "pull"]] == [
        ["docker", "pull", "--platform", host_platform(), hub_ref(tag)]
        for tag in expected
    ]
    assert [argv for argv in calls if argv[0:2] == ["docker", "tag"]] == [
        ["docker", "tag", hub_ref(tag), tag]
        for tag in expected
    ]


def test_one_name_pulls_only_that_builder():
    calls  = []
    result = pull_builders(["cpp"], _runner(calls))

    assert result.pulled == ("builder/cpp:latest",)
    assert result.missing == ()
    assert [argv[-1] for argv in calls if argv[0:2] == ["docker", "pull"]] == [
        "hunterground/builder-cpp:latest",
    ]


def test_missing_builder_is_recorded_and_later_tags_still_pull():
    calls = []
    missed = "hunterground/builder-cpp:latest"

    def pull_status(argv):
        return 1 if argv[-1] == missed else 0

    result = pull_builders(None, _runner(calls, pull_status))
    pulled_refs = [argv[-1] for argv in calls if argv[0:2] == ["docker", "pull"]]

    assert "builder/cpp:latest" in result.missing
    assert "builder/cpp:latest" not in result.pulled
    assert result.pulled[-1] == _builder_tags()[-1]
    assert pulled_refs.index(missed) < len(pulled_refs) - 1
    assert not any(
        argv[0:2] == ["docker", "tag"] and argv[-1] == "builder/cpp:latest"
        for argv in calls
    )


def test_builder_tag_failure_stops_before_later_builders():
    calls = []

    def tag_status(_argv):
        return 1

    with pytest.raises(ToolError, match=r"cannot tag it as builder/cpp:latest"):
        pull_builders(["cpp", "java"], _runner(calls, tag_status=tag_status))

    assert [argv[-1] for argv in calls if argv[0:2] == ["docker", "pull"]] == [
        "hunterground/builder-cpp:latest",
    ]
