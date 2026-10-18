"""
    HunterGround Toolkit
    Test for removing base and builder images from the local machine
"""

from hg.catalog     import BASES, BUILDERS, hub_ref
from hg.errors      import ToolError
from hg.remove      import (
    ImageRemoveError,
    clean_bases,
    clean_builders,
    remove_bases,
    remove_builders,
)
import pytest


def _runner(calls, status=None):
    def run(argv):
        calls.append(argv)
        if status is not None:
            return status(argv)
        return 0

    return run


def _inspector(ids):
    def lookup(tag):
        return ids.get(tag)

    return lookup


def _pair(image, image_id):
    return {image: image_id, hub_ref(image): image_id}


def _present(*images, image_id="sha256:same"):
    ids = {}
    for image in images:
        ids.update(_pair(image, image_id))
    return ids


def test_one_name_removes_both_tags_when_ids_match():
    calls = []
    image = "base/web-nginx:latest"
    removed = remove_bases(["web-nginx"], _runner(calls), _inspector(_present(image)))

    assert removed == (image, hub_ref(image))
    assert calls == [["docker", "image", "rm", image, hub_ref(image)]]


def test_missing_tags_are_a_no_op():
    calls = []
    assert remove_bases(["web-nginx"], _runner(calls), _inspector({})) == ()
    assert calls == []


def test_one_present_tag_is_removed_alone():
    calls = []
    image = "base/web-nginx:latest"
    removed = remove_bases(
        ["web-nginx"],
        _runner(calls),
        _inspector({image: "sha256:local"}),
    )

    assert removed == (image,)
    assert calls == [["docker", "image", "rm", image]]


def test_mismatched_ids_remove_nothing_and_raise():
    calls = []
    image = "base/web-nginx:latest"
    ids = {image: "sha256:local", hub_ref(image): "sha256:hub"}

    with pytest.raises(ToolError, match=r"kept base/web-nginx:latest"):
        remove_bases(["web-nginx"], _runner(calls), _inspector(ids))

    assert calls == []


def test_image_rm_failure_is_reported_and_later_images_still_run():
    calls = []
    nginx  = "base/web-nginx:latest"
    python = "base/python:latest"

    def status(argv):
        return 1 if nginx in argv else 0

    with pytest.raises(ImageRemoveError, match="docker image rm failed for base/web-nginx:latest") as exc:
        remove_bases(
            ["web-nginx", "python"],
            _runner(calls, status),
            _inspector(_present(nginx, python)),
        )

    assert calls == [
        ["docker", "image", "rm", nginx, hub_ref(nginx)],
        ["docker", "image", "rm", python, hub_ref(python)],
    ]
    assert exc.value.removed == (python, hub_ref(python))


def test_remove_debian_leaves_the_pack():
    calls = []
    image = "range/debian:latest"
    pack  = "range/pack/sshd:latest"
    removed = remove_bases(
        ["debian"],
        _runner(calls),
        _inspector(_present(image, pack)),
    )

    assert removed == (image, hub_ref(image))
    assert calls == [["docker", "image", "rm", image, hub_ref(image)]]
    assert not any("sshd" in arg for argv in calls for arg in argv)


def test_clean_bases_includes_every_base_and_extra_image():
    calls = []
    images = []
    for base in BASES:
        images.append(base.image)
        images.extend(base.extra_inspect)
    ids = {}
    for index, image in enumerate(images):
        ids.update(_pair(image, f"sha256:{index}"))

    removed = clean_bases(_runner(calls), _inspector(ids))

    expected = []
    for image in images:
        expected.extend((image, hub_ref(image)))
    assert removed == tuple(expected)
    assert "range/pack/sshd:latest" in removed
    assert [argv[3] for argv in calls] == images


def test_clean_builders_includes_every_builder():
    calls = []
    images = [builder.image for builder in BUILDERS]
    ids = {}
    for index, image in enumerate(images):
        ids.update(_pair(image, f"sha256:{index}"))

    removed = clean_builders(_runner(calls), _inspector(ids))

    expected = []
    for image in images:
        expected.extend((image, hub_ref(image)))
    assert removed == tuple(expected)
    assert [argv[3] for argv in calls] == images


def test_a_builder_name_is_rejected_by_remove_bases():
    calls = []
    with pytest.raises(ToolError, match="unknown base"):
        remove_bases(["cpp"], _runner(calls), _inspector({}))
    assert calls == []


def test_a_base_name_is_rejected_by_remove_builders():
    calls = []
    with pytest.raises(ToolError, match="unknown builder"):
        remove_builders(["web-nginx"], _runner(calls), _inspector({}))
    assert calls == []


def test_remove_without_a_name_points_at_clean():
    with pytest.raises(ToolError, match="hg bases clean"):
        remove_bases([])
    with pytest.raises(ToolError, match="hg builders clean"):
        remove_builders(None)
