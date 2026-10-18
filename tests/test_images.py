"""
    HunterGround Toolkit
    Test for tagging and pushing challenge images to a public registry
"""

from hg.errors      import ToolError
from hg.images      import list_images, publish_name, push_images
from hg.scaffold    import create_challenge
import json
import pytest
import yaml


DIGEST = "sha256:" + ("cd" * 32)


def _docker(calls, *, present=True, push_code=0, repo_digests=None):
    def run(argv):
        calls.append(list(argv))
        if argv[1:3] == ["image", "inspect"] and "--format" not in argv:
            if present:
                return 0, "", ""
            return 1, "", "No such image"
        if argv[1] == "push":
            if push_code:
                return push_code, "", "denied: requested access to the resource is denied"
            return 0, "", ""
        if "--format" in argv:
            remote = argv[-1].rsplit(":", 1)[0]
            listed = repo_digests if repo_digests is not None else [f"docker.io/{remote}@{DIGEST}"]
            return 0, json.dumps(listed), ""
        return 0, "", ""

    return run


def _text(root):
    return (root / "challenge.yaml").read_text(encoding="utf-8")


## Main Tests ---------------------------------------------------------------------------
def test_publish_name_flattens_the_logical_image():
    assert publish_name("challenges/web/web-note-01", "ada") == "ada/challenges-web-web-note-01"


def test_push_tags_pushes_and_pins_without_rewriting_the_file(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    original = _text(root)
    note = "# author note\n"
    (root / "challenge.yaml").write_text(note + original, encoding="utf-8")
    before = _text(root)
    calls = []
    published = push_images(root, "ada", docker=_docker(calls), env={})

    assert published[0].member == "web"
    assert published[0].reference == f"ada/challenges-web-web-xss-01@{DIGEST}"
    assert ["docker", "image", "inspect", "challenges/web/web-xss-01:local"] in calls
    assert ["docker", "tag", "challenges/web/web-xss-01:local", "ada/challenges-web-web-xss-01:1"] in calls
    assert ["docker", "push", "ada/challenges-web-web-xss-01:1"] in calls
    assert any(argv[1:3] == ["image", "inspect"] and "--format" in argv for argv in calls)

    text = _text(root)
    assert text.startswith(note)
    digest_line = next(line for line in before.splitlines() if "fromDigest:" in line)
    assert digest_line in text.splitlines()
    image = yaml.safe_load(text)["spec"]["services"][0]["image"]
    assert image["name"] == "challenges/web/web-xss-01"
    assert image["registry"] == "ada/challenges-web-web-xss-01"
    assert image["digest"] == DIGEST
    assert text.count("registry:") == 1
    assert text.count(f"digest: {DIGEST}") == 1

    again = []
    push_images(root, None, docker=_docker(again), env={"HG_REGISTRY": "ada"})
    replaced = _text(root)
    assert replaced.count("registry:") == 1
    assert replaced.count(f"digest: {DIGEST}") == 1


def test_push_pins_every_built_member(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    bot = root / "services" / "bot"
    bot.mkdir()
    (bot / "Dockerfile").write_text(
        (root / "services" / "web" / "Dockerfile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = yaml.safe_load(_text(root))
    web = manifest["spec"]["services"][0]
    second = {
        "name": "bot",
        "build": {"context": "services/bot", "dockerfile": "Dockerfile"},
        "image": {"name": "challenges/web/web-xss-01-bot"},
        "ports": web["ports"],
        "health": web["health"],
        "resources": web["resources"],
    }
    web["image"]["name"] = "challenges/web/web-xss-01-web"
    manifest["spec"]["services"] = [web, second]
    manifest["spec"]["networks"][0]["members"] = ["web", "bot"]
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    calls = []
    published = push_images(root, "ada", docker=_docker(calls), env={})
    assert [item.member for item in published] == ["web", "bot"]
    pushed = [argv[-1] for argv in calls if argv[1] == "push"]
    assert pushed == [
        "ada/challenges-web-web-xss-01-web:1",
        "ada/challenges-web-web-xss-01-bot:1",
    ]
    services = yaml.safe_load(_text(root))["spec"]["services"]
    assert services[0]["image"]["registry"] == "ada/challenges-web-web-xss-01-web"
    assert services[1]["image"]["registry"] == "ada/challenges-web-web-xss-01-bot"
    assert services[0]["build"]["context"] == "services/web"


def test_missing_local_tag_leaves_the_manifest_unchanged(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    before = _text(root)
    calls = []
    with pytest.raises(ToolError, match="hg challenge build"):
        push_images(root, "ada", docker=_docker(calls, present=False), env={})
    assert _text(root) == before
    assert not any(argv[1] == "push" for argv in calls)


def test_failed_push_leaves_the_manifest_unchanged(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    before = _text(root)
    with pytest.raises(ToolError, match="denied"):
        push_images(root, "ada", docker=_docker([], push_code=1), env={})
    assert _text(root) == before


def test_namespace_is_required(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    with pytest.raises(ToolError, match="HG_REGISTRY"):
        push_images(root, None, docker=_docker([]), env={})
    with pytest.raises(ToolError, match="namespace"):
        push_images(root, "Ada/org", docker=_docker([]), env={})


def test_static_and_compose_are_refused(tmp_path):
    static = create_challenge("none", "static-note", tmp_path / "static-note", category="osint")
    with pytest.raises(ToolError, match="static"):
        push_images(static, "ada", docker=_docker([]), env={})

    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    text = _text(root).replace("spawn: image\n", "spawn: compose\n", 1)
    (root / "challenge.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ToolError, match="compose"):
        push_images(root, "ada", docker=_docker([]), env={})


def test_list_reports_the_local_tag_and_the_pin(tmp_path):
    root = create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")
    missing = list_images(root, _docker([], present=False))
    assert missing[0].local == "challenges/web/web-xss-01:local"
    assert missing[0].present is False
    assert missing[0].reference is None

    push_images(root, "ada", docker=_docker([]), env={})
    found = list_images(root, _docker([], present=True))
    assert found[0].present is True
    assert found[0].reference == f"ada/challenges-web-web-xss-01@{DIGEST}"
