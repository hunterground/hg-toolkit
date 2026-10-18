"""
    HunterGround Toolkit
    Test for scaffolding the challenge
"""

from hg.errors      import ToolError
from hg.lint        import lint_challenge, parse_dockerfile
from hg.scaffold    import base_digest, create_challenge, repo_root
import stat
import subprocess
import pytest
import yaml


# Same value tests/conftest.py returns from the stubbed digest lookup.
STUB_DIGEST = "sha256:" + ("ab" * 32)


TEMPLATES = [
    (
        "web-nginx",
        "web-nginx-01",
        "services/web",
        ["Dockerfile", "html/index.html"],
        "base/web-nginx:latest",
        "web.http",
        "nginx:nginx",
    ),
    (
        "web-php-nginx",
        "web-php-nginx-01",
        "services/web",
        ["Dockerfile", "index.php"],
        "base/web-php-nginx:latest",
        "web.http",
        "ctf:ctf",
    ),
    (
        "web-php-apache",
        "web-php-apache-01",
        "services/web",
        ["Dockerfile", "index.php"],
        "base/web-php-apache:latest",
        "web.http",
        "ctf:ctf",
    ),
    (
        "web-flask",
        "web-flask-01",
        "services/web",
        ["Dockerfile", "app.py"],
        "base/web-flask:latest",
        "web.http",
        "ctf:ctf",
    ),
    (
        "web-fastapi",
        "web-fastapi-01",
        "services/web",
        ["Dockerfile", "app.py"],
        "base/web-fastapi:latest",
        "web.http",
        "ctf:ctf",
    ),
    (
        "web-express",
        "web-express-01",
        "services/web",
        ["Dockerfile", "app.js"],
        "base/web-express:latest",
        "web.http",
        "ctf:ctf",
    ),
    (
        "web-sinatra",
        "web-sinatra-01",
        "services/web",
        ["Dockerfile", "app.rb"],
        "base/web-sinatra:latest",
        "web.http",
        "ctf:ctf",
    ),
    (
        "python-socat",
        "python-socat-01",
        "services/web",
        ["Dockerfile", "chall.py"],
        "base/python-socat:latest",
        "web.tcp",
        "ctf:ctf",
    ),
    (
        "nodejs-socat",
        "nodejs-socat-01",
        "services/web",
        ["Dockerfile", "chall.js"],
        "base/nodejs-socat:latest",
        "web.tcp",
        "ctf:ctf",
    ),
    (
        "range/debian",
        "range-debian-01",
        "hosts/box",
        ["Dockerfile"],
        "range/debian:latest",
        "box.ssh",
        "root:root",
    ),
]


## Main Tests ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("base", "challenge_id", "context", "files", "image", "entry", "owner"),
    TEMPLATES,
)
def test_scaffold_writes_a_tree_that_lints(tmp_path, base, challenge_id, context, files, image, entry, owner):
    dest = create_challenge(base, challenge_id, tmp_path / challenge_id)

    for name in files:
        assert (dest / context / name).is_file()
    assert (dest / "private" / "writeup.md").is_file()
    solve = dest / "private" / "solve.sh"
    assert "CHALLENGE_HOST" in solve.read_text(encoding="utf-8")
    assert "CHALLENGE_PORT" in solve.read_text(encoding="utf-8")
    assert "CHALLENGE_CONNECTION" in solve.read_text(encoding="utf-8")
    assert stat.S_IMODE(solve.stat().st_mode) & stat.S_IXUSR
    assert (dest / ".gitignore").read_text(encoding="utf-8") == "dist/\n.hg/\n"

    dockerfile = (dest / context / "Dockerfile").read_text(encoding="utf-8")
    parsed = parse_dockerfile(dockerfile)
    assert parsed.from_images == [image]
    assert "ENTRYPOINT" not in parsed.instructions
    assert "USER" not in parsed.instructions

    manifest = yaml.safe_load((dest / "challenge.yaml").read_text(encoding="utf-8"))
    assert manifest["metadata"]["id"] == challenge_id
    assert manifest["metadata"]["state"] == "hidden"
    assert manifest["metadata"]["version"] == "1"
    assert manifest["metadata"]["author"] == "author"
    flag = manifest["spec"]["flags"][0]
    assert flag["type"] == "dynamic"
    assert "content" not in flag
    assert flag["owner"] == owner
    assert flag["mode"] == "0400"
    assert manifest["spec"]["player"]["entry"] == entry
    members = manifest["spec"].get("services") or manifest["spec"].get("hosts")
    assert len(members) == 1
    assert members[0]["image"]["name"] == f"challenges/{manifest['metadata']['category']}/{challenge_id}"
    assert members[0]["build"]["fromDigest"] == STUB_DIGEST
    assert lint_challenge(dest) == []


def test_tcp_and_range_connection_text(tmp_path):
    tcp = create_challenge("python-socat", "python-socat-01", tmp_path / "tcp")
    tcp_manifest = yaml.safe_load((tcp / "challenge.yaml").read_text(encoding="utf-8"))
    assert tcp_manifest["spec"]["player"]["connection"] == "nc {host} {port}"

    host = create_challenge("debian", "range-debian-01", tmp_path / "host")
    host_manifest = yaml.safe_load((host / "challenge.yaml").read_text(encoding="utf-8"))
    assert host_manifest["spec"]["runtime"] == "scenario"
    assert host_manifest["spec"]["hosts"][0]["health"] == {
        "type": "ready-file",
        "path": "/run/hg/ready",
    }
    assert host_manifest["spec"]["hosts"][0]["users"] == [
        {"name": "player", "passwordFrom": "generated"}
    ]
    assert host_manifest["spec"]["player"]["connection"] == "ssh -p {port} player@{host}"
    dockerfile = (host / "hosts" / "box" / "Dockerfile").read_text(encoding="utf-8")
    assert "openssh-server" in dockerfile
    assert "hg-enable sshd && hg-seal" in dockerfile
    assert "RANGE_PACK_SSHD_IMAGE=range/pack/sshd:latest" in dockerfile


def test_base_digest_reads_the_index_digest(monkeypatch):
    seen = []

    def run(argv, **_kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=STUB_DIGEST + "\n", stderr="")

    monkeypatch.setattr("hg.scaffold.subprocess.run", run)
    assert base_digest("base/web-nginx:latest") == STUB_DIGEST
    assert seen == [[
        "docker", "buildx", "imagetools", "inspect",
        "--format", "{{.Manifest.Digest}}",
        "hunterground/base-web-nginx:latest",
    ]]


def test_base_digest_reports_a_failed_inspect(monkeypatch):
    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such manifest")

    monkeypatch.setattr("hg.scaffold.subprocess.run", run)
    with pytest.raises(ToolError, match="cannot read digest of hunterground/base-web-nginx:latest"):
        base_digest("base/web-nginx:latest")


def test_scaffold_refuses_when_the_digest_cannot_be_read(tmp_path, monkeypatch):
    def fail(_tag):
        raise ToolError("cannot read digest of hunterground/base-web-nginx:latest")

    monkeypatch.setattr("hg.scaffold.base_digest", fail)
    dest = tmp_path / "out"
    with pytest.raises(ToolError, match="digest"):
        create_challenge("web-nginx", "web-xss-01", dest)
    assert not dest.exists()


def test_scaffold_refuses_a_bad_id(tmp_path):
    with pytest.raises(ToolError, match="id must match"):
        create_challenge("web-nginx", "Not-Valid", tmp_path / "out")


def test_scaffold_refuses_an_unknown_base(tmp_path):
    with pytest.raises(ToolError, match="unknown base"):
        create_challenge("no-such", "web-xss-01", tmp_path / "out")


def test_scaffold_refuses_a_base_without_a_template(tmp_path):
    with pytest.raises(ToolError, match="no template in this cut"):
        create_challenge("web-spring", "web-spring-01", tmp_path / "out")
    with pytest.raises(ToolError, match="no template in this cut"):
        create_challenge("base/pwn-qemu", "pwn-qemu-01", tmp_path / "out")


def test_scaffold_refuses_a_non_empty_dest(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "already").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError, match="not empty"):
        create_challenge("web-nginx", "web-xss-01", dest)


def test_scaffold_static_omits_the_image_tree(tmp_path, monkeypatch):
    def fail(_tag):
        raise ToolError("cannot read digest")

    monkeypatch.setattr("hg.scaffold.base_digest", fail)
    omitted = create_challenge(
        challenge_id = "forensic-note-01",
        dest         = tmp_path / "omitted",
        category     = "forensic",
    )
    explicit = create_challenge("none", "note-explicit-01", tmp_path / "explicit", category="crypto")

    for dest, category in ((omitted, "forensic"), (explicit, "crypto")):
        assert not (dest / "services").exists()
        assert not (dest / "hosts").exists()
        assert lint_challenge(dest) == []
        manifest = yaml.safe_load((dest / "challenge.yaml").read_text(encoding="utf-8"))
        assert manifest["metadata"]["category"] == category
        assert manifest["metadata"]["state"] == "hidden"
        assert manifest["spec"]["runtime"] == "static"
        assert "spawn" not in manifest["spec"]
        assert manifest["spec"]["flags"] == [
            {"id": "flag", "type": "static", "content": "hg{replace-me}"},
        ]
        assert manifest["spec"]["files"] == [{"name": "handout.txt", "source": "build"}]
        solve = (dest / "private" / "solve.sh").read_text(encoding="utf-8")
        assert "CHALLENGE_HOST" not in solve
        assert "CHALLENGE_PORT" not in solve
        assert "CHALLENGE_CONNECTION" not in solve
        build = dest / "private" / "build.sh"
        assert stat.S_IMODE(build.stat().st_mode) & stat.S_IXUSR
        assert "hg{replace-me}" in build.read_text(encoding="utf-8")
        errors = lint_challenge(dest, strict=True)
        assert any("writeup.md" in error for error in errors)
        assert any("solve.sh" in error for error in errors)


def test_scaffold_static_requires_a_category(tmp_path):
    dest = tmp_path / "out"
    with pytest.raises(ToolError, match="category is required"):
        create_challenge(challenge_id="forensic-note-01", dest=dest)
    with pytest.raises(ToolError, match="category is required"):
        create_challenge("none", "forensic-note-01", dest)
    assert not dest.exists()


def test_scaffold_static_refuses_an_unknown_category(tmp_path):
    dest = tmp_path / "out"
    with pytest.raises(ToolError, match="not on the platform"):
        create_challenge("none", "forensic-note-01", dest, category="misc")
    assert not dest.exists()


def test_scaffold_refuses_a_category_on_an_image_base(tmp_path):
    dest = tmp_path / "out"
    with pytest.raises(ToolError, match="set by the base"):
        create_challenge("web-nginx", "web-xss-01", dest, category="web")
    assert not dest.exists()


def test_scaffold_refuses_a_dest_inside_the_repo():
    root = repo_root()
    if root is None:
        pytest.skip("not running from the base-images checkout")
    dest = root / "containers" / "not-a-challenge"
    with pytest.raises(ToolError, match="inside this repo"):
        create_challenge("web-nginx", "web-xss-01", dest)
    assert not dest.exists()
