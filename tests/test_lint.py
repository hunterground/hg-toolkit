"""
    HunterGround Toolkit
    Test for linting the challenge
"""

from hg.lint        import lint_challenge
from hg.scaffold    import create_challenge
import yaml


## Helper Functions ----------------------------------------------------------------------
def _scaffold(tmp_path):
    return create_challenge("web-nginx", "web-xss-01", tmp_path / "web-xss-01")


def _write_manifest(root, manifest):
    (root / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )


def _manifest(root):
    return yaml.safe_load((root / "challenge.yaml").read_text(encoding="utf-8"))


## Main Tests ---------------------------------------------------------------------------
def test_lint_rejects_a_second_from(tmp_path):
    root = _scaffold(tmp_path)
    dockerfile = root / "services" / "web" / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text(encoding="utf-8") + "\nFROM scratch\n", encoding="utf-8")
    errors = lint_challenge(root)
    assert any("allowlisted" in error for error in errors)


def test_lint_rejects_entrypoint(tmp_path):
    root = _scaffold(tmp_path)
    dockerfile = root / "services" / "web" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8") + '\nENTRYPOINT ["/init"]\n',
        encoding="utf-8",
    )
    errors = lint_challenge(root)
    assert any("ENTRYPOINT" in error for error in errors)


def test_lint_rejects_user(tmp_path):
    root = _scaffold(tmp_path)
    dockerfile = root / "services" / "web" / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text(encoding="utf-8") + "\nUSER ctf\n", encoding="utf-8")
    errors = lint_challenge(root)
    assert any("USER" in error for error in errors)


def test_lint_rejects_flag_content(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["flags"][0]["content"] = "hg{hidden}"
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("content" in error for error in errors)


def test_lint_accepts_a_static_password(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["users"] = [{"name": "player", "password": "secret"}]
    _write_manifest(root, manifest)
    assert lint_challenge(root) == []

def test_lint_rejects_password_with_generated(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["users"] = [
        {"name": "player", "password": "secret", "passwordFrom": "generated"}
    ]
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("password" in error for error in errors)

def test_lint_rejects_gid_without_group(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["users"] = [{"name": "player", "gid": 1001}]
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("gid requires a group" in error for error in errors)

def test_lint_rejects_duplicate_user_name(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["users"] = [
        {"name": "player", "passwordFrom": "generated"},
        {"name": "player", "passwordFrom": "generated"},
    ]
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("duplicated" in error for error in errors)

def test_lint_rejects_a_reserved_scenario_uid(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    manifest = _manifest(root)
    manifest["spec"]["hosts"][0]["users"] = [{"name": "player", "uid": 101, "passwordFrom": "generated"}]
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("reserved" in error for error in errors)


def test_lint_rejects_a_private_context(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["build"]["context"] = "private"
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("private" in error for error in errors)


def test_lint_rejects_a_missing_writeup(tmp_path):
    root = _scaffold(tmp_path)
    (root / "private" / "writeup.md").unlink()
    errors = lint_challenge(root)
    assert any("writeup.md" in error for error in errors)


def test_lint_rejects_a_mismatched_image_name(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["spec"]["services"][0]["image"]["name"] = "challenges/web-xss-01/web"
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("challenges/web/web-xss-01" in error for error in errors)


def test_lint_suffixes_the_member_when_several_images_are_built(tmp_path):
    root = _scaffold(tmp_path)
    bot = root / "services" / "bot"
    bot.mkdir()
    (bot / "Dockerfile").write_text(
        (root / "services" / "web" / "Dockerfile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = _manifest(root)
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
    manifest["spec"]["flags"][0]["service"] = "web"
    _write_manifest(root, manifest)
    assert lint_challenge(root) == []

    web["image"]["name"] = "challenges/web/web-xss-01"
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("challenges/web/web-xss-01-web" in error for error in errors)


def test_lint_rejects_scenario_health_that_is_not_ready(tmp_path):
    root = create_challenge("range/debian", "range-debian-01", tmp_path / "range-debian-01")
    manifest = _manifest(root)
    manifest["spec"]["hosts"][0]["health"] = {"type": "tcp", "port": 22}
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("ready-file" in error for error in errors)


def test_lint_accepts_a_numeric_difficulty(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["metadata"]["difficulty"] = 7
    _write_manifest(root, manifest)
    assert lint_challenge(root) == []


def test_lint_accepts_a_label_difficulty(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    manifest["metadata"]["difficulty"] = "hard"
    _write_manifest(root, manifest)
    assert lint_challenge(root) == []


def test_lint_accepts_a_published_image_pin(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    image = manifest["spec"]["services"][0]["image"]
    image["registry"] = "ada/challenges-web-web-xss-01"
    image["digest"] = "sha256:" + ("ab" * 32)
    _write_manifest(root, manifest)
    assert lint_challenge(root) == []


def test_lint_rejects_a_half_written_image_pin(tmp_path):
    root = _scaffold(tmp_path)
    manifest = _manifest(root)
    image = manifest["spec"]["services"][0]["image"]
    image["registry"] = "ada/challenges-web-web-xss-01"
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("image.registry and image.digest" in error for error in errors)

    manifest = _manifest(root)
    del manifest["spec"]["services"][0]["image"]["registry"]
    manifest["spec"]["services"][0]["image"]["digest"] = "sha256:" + ("ab" * 32)
    _write_manifest(root, manifest)
    errors = lint_challenge(root)
    assert any("image.registry and image.digest" in error for error in errors)


def test_lint_rejects_a_difficulty_out_of_range(tmp_path):
    root = _scaffold(tmp_path)
    for value in (0, 11):
        manifest = _manifest(root)
        manifest["metadata"]["difficulty"] = value
        _write_manifest(root, manifest)
        errors = lint_challenge(root)
        assert any("difficulty" in error for error in errors), value
