"""
    HunterGround Toolkit
    Test for the remote API client, credential storage, and challenge push mapping
"""

from http.server    import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib        import Path
from hg             import api as hg_api
from hg.api         import APIError, Client, Session, urllib_transport
from hg.cli         import main
from hg.config      import DEFAULT_API_ORIGIN, Profile, load_profile, normalize_api_url, resolve_origin, save_profile
from hg.credentials import SERVICE, CredentialStore, Secret
from hg.errors      import ToolError
from hg.remote      import (
    FLAG_TEMPLATE,
    add_challenge,
    challenge_body,
    create_contest,
    handout_bytes,
    push_challenge,
    push_material,
)
from hg.scaffold    import WRITEUP
import io
import json
import ssl
import stat
import threading
import time
import tomllib
import urllib.error
import zipfile
import pytest
import yaml


DIGEST = "sha256:" + ("ab" * 32)
ORIGIN = "https://api.example"


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HG_API_URL",      raising=False)
    monkeypatch.delenv("HG_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("hg.credentials.keyring_available", lambda: False)


class FakeRing:
    def __init__(self):
        self.data = {}

    def get_password(self, service, account):
        return self.data.get((service, account))

    def set_password(self, service, account, password):
        self.data[(service, account)] = password

    def delete_password(self, service, account):
        self.data.pop((service, account), None)


class Script:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


def ok(data, status=200):
    return status, {"success": True, "data": data}


def err(status, code, message):
    return status, {"success": False, "error": {"code": code, "message": message}}


def _mode(path):
    return path.stat().st_mode & 0o777


def _write_manifest(root, spec, **metadata):
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": "web-xss-01",
        "title": "XSS",
        "category": "web",
        "description": "find it",
        "difficulty": "easy",
        "author": "author",
        "value": 100,
        "state": "hidden",
        "version": "1",
    }
    meta.update(metadata)
    (root / "challenge.yaml").write_text(
        yaml.safe_dump({"apiVersion": "hg.challenge/v1", "kind": "Challenge", "metadata": meta, "spec": spec}),
        encoding="utf-8",
    )


def _service_spec(**overrides):
    spec = {
        "runtime": "container",
        "spawn": "image",
        "flags": [
            {
                "id": "flag",
                "type": "dynamic",
                "path": "/app/flag.txt",
                "owner": "nginx:nginx",
                "mode": "0644",
                "service": "web",
            }
        ],
        "services": [
            {
                "name": "web",
                "image": {
                    "name": "registry.example/challenges/web/web-xss-01:1",
                    "digest": DIGEST,
                },
                "ports": [{"name": "http", "container": 80, "protocol": "tcp", "publish": True}],
                "health": {"type": "http", "path": "/"},
            }
        ],
        "player": {"entry": "web.http", "connection": "{url}"},
    }
    spec.update(overrides)
    return spec


def _client(script: Script) -> Client:
    return Client(ORIGIN, "token", transport=script)


def _part(content_type: str, body: bytes) -> tuple[str, bytes]:
    boundary = content_type.split("boundary=", 1)[1].encode()
    chunk = body.split(b"--" + boundary)[1]
    header, _, data = chunk.partition(b"\r\n\r\n")
    if data.endswith(b"\r\n"):
        data = data[:-2]
    filename = header.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
    return filename, data


def test_file_fallback_is_private_and_omits_the_password(tmp_path, capsys):
    script = Script(
        [
            ok(
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 900,
                    "user": {"email": "staff@example", "username": "staff", "role": "admin"},
                }
            )
        ]
    )
    store = CredentialStore(tmp_path, force_file=True)
    user = Session.blank(directory=tmp_path, store=store, transport=script).login(
        "https://API.Example", "staff@example", "s3cret"
    )
    assert user["email"] == "staff@example"
    secret_path = tmp_path / "credentials.json"
    text = secret_path.read_text(encoding="utf-8")
    assert "s3cret" not in text
    assert "refresh" in text
    assert _mode(secret_path) == stat.S_IRUSR | stat.S_IWUSR
    assert _mode(tmp_path) == stat.S_IRWXU
    assert _mode(tmp_path / "config.json") == stat.S_IRUSR | stat.S_IWUSR
    assert "no OS keychain" in capsys.readouterr().err
    sent = json.loads(script.calls[0][3])
    assert sent == {"email": "staff@example", "password": "s3cret"}
    assert "Authorization" not in script.calls[0][2]


def test_login_defaults_to_the_platform_origin(tmp_path):
    script = Script(
        [
            ok(
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 900,
                    "user": {"email": "staff@example", "username": "staff", "role": "admin"},
                }
            )
        ]
    )
    store = CredentialStore(tmp_path, force_file=True)
    user = Session.blank(directory=tmp_path, store=store, transport=script).login(
        None, "staff@example", "s3cret"
    )
    assert user["email"] == "staff@example"
    assert script.calls[0][1] == DEFAULT_API_ORIGIN + "/api/v1/internal/auth/login"
    saved = load_profile(tmp_path)
    assert saved is not None
    assert saved.url == DEFAULT_API_ORIGIN

def test_open_defaults_to_the_platform_origin_for_ci_token(tmp_path):
    script = Script([ok({"email": "a@b.c", "username": "ada", "role": "admin"})])
    store = CredentialStore(tmp_path, force_file=True)
    session = Session.open(
        directory=tmp_path,
        store=store,
        transport=script,
        env={"HG_ACCESS_TOKEN": "ci-token"},
    )
    assert session.url == DEFAULT_API_ORIGIN
    assert session.whoami()["username"] == "ada"
    assert script.calls[0][1].startswith(DEFAULT_API_ORIGIN)
    assert script.calls[0][2]["Authorization"] == "Bearer ci-token"

def test_env_url_overrides_the_default_origin():
    assert resolve_origin({"HG_API_URL": ORIGIN}) == ORIGIN
    assert resolve_origin({}) == DEFAULT_API_ORIGIN
    assert resolve_origin({}, ORIGIN) == ORIGIN

def test_keyring_store_does_not_write_a_file(tmp_path):
    ring = FakeRing()
    store = CredentialStore(tmp_path, keyring_module=ring)
    store.save(ORIGIN, Secret("refresh", "access", time.time() + 1000))
    assert not (tmp_path / "credentials.json").exists()
    assert ring.data[(SERVICE, ORIGIN)]
    loaded = store.load(ORIGIN)
    assert loaded is not None
    assert loaded.access_token == "access"
    store.delete(ORIGIN)
    assert store.load(ORIGIN) is None


def test_https_required_except_loopback():
    with pytest.raises(ToolError, match="https"):
        normalize_api_url("http://example.com")
    assert normalize_api_url("http://localhost:8081") == "http://localhost:8081"
    assert normalize_api_url("http://127.0.0.1:8081") == "http://127.0.0.1:8081"
    assert normalize_api_url("https://API.Example") == "https://api.example"
    assert normalize_api_url("https://api.example:443") == "https://api.example"


def test_refresh_before_expiry_keeps_the_refresh_token(tmp_path):
    script = Script(
        [
            ok({"access_token": "new", "expires_in": 900}),
            ok({"email": "a@b.c", "username": "ada", "role": "admin"}),
        ]
    )
    store = CredentialStore(tmp_path, force_file=True)
    store.save(ORIGIN, Secret("refresh-1", "old", time.time() - 5))
    save_profile(tmp_path, Profile(ORIGIN, "a@b.c"))
    session = Session(url=ORIGIN, email="a@b.c", directory=tmp_path, store=store, transport=script)
    assert session.whoami()["username"] == "ada"
    assert script.calls[0][1].endswith("/api/v1/internal/auth/refresh")
    assert json.loads(script.calls[0][3])["refresh_token"] == "refresh-1"
    assert script.calls[1][2]["Authorization"] == "Bearer new"
    saved = store.load(ORIGIN)
    assert saved is not None
    assert saved.refresh_token == "refresh-1"
    assert saved.access_token == "new"
    assert saved.fresh()


def test_unauthorized_retries_refresh_once(tmp_path):
    script = Script(
        [
            err(401, "UNAUTHORIZED", "expired"),
            ok({"access_token": "new", "expires_in": 900}),
            err(401, "UNAUTHORIZED", "still expired"),
        ]
    )
    store = CredentialStore(tmp_path, force_file=True)
    store.save(ORIGIN, Secret("refresh-1", "old", time.time() + 3600))
    session = Session(url=ORIGIN, email="a@b.c", directory=tmp_path, store=store, transport=script)
    with pytest.raises(APIError):
        session.whoami()
    refresh_calls = [call for call in script.calls if call[1].endswith("/auth/refresh")]
    assert len(refresh_calls) == 1


def test_env_token_is_not_saved_or_refreshed(tmp_path):
    script = Script([err(401, "UNAUTHORIZED", "nope")])
    store = CredentialStore(tmp_path, force_file=True)
    session = Session.open(
        directory=tmp_path,
        store=store,
        transport=script,
        env={"HG_API_URL": ORIGIN, "HG_ACCESS_TOKEN": "ci-token"},
    )
    with pytest.raises(APIError):
        session.whoami()
    assert script.calls[0][2]["Authorization"] == "Bearer ci-token"
    assert not any(call[1].endswith("/auth/refresh") for call in script.calls)
    assert not (tmp_path / "credentials.json").exists()


def test_logout_deletes_the_secret_when_the_api_fails(tmp_path):
    script = Script([err(500, "INTERNAL", "nope")])
    store = CredentialStore(tmp_path, force_file=True)
    store.save(ORIGIN, Secret("refresh-1", "access", time.time() + 3600))
    session = Session(url=ORIGIN, email="a@b.c", directory=tmp_path, store=store, transport=script)
    session.logout()
    assert store.load(ORIGIN) is None
    with pytest.raises(ToolError, match="not logged in"):
        session.logout()


def test_contest_create_body(tmp_path):
    script = Script([ok({"slug": "spring-2026", "published": False}, status=201)])
    data = create_contest(
        _client(script),
        slug="spring-2026",
        title="Spring 2026",
        starts_at="2026-04-01T00:00:00Z",
        ends_at="2026-04-03T00:00:00Z",
        description="notes",
    )
    assert data["slug"] == "spring-2026"
    assert script.calls[0][0] == "POST"
    assert script.calls[0][1].endswith("/api/v1/admin/contests")
    body = json.loads(script.calls[0][3])
    assert body == {
        "slug": "spring-2026",
        "title": "Spring 2026",
        "description": "notes",
        "rules": "",
        "mode": "jeopardy",
        "kind": "open",
        "starts_at": "2026-04-01T00:00:00Z",
        "ends_at": "2026-04-03T00:00:00Z",
        "published": False,
        "participation": "individual",
    }
    with pytest.raises(ToolError, match="ends_at"):
        create_contest(
            _client(Script([])),
            slug="spring-2026",
            title="Spring 2026",
            starts_at="2026-04-03T00:00:00Z",
            ends_at="2026-04-01T00:00:00Z",
        )


def test_push_creates_then_updates(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(root, _service_spec(), state="visible")
    (root / "private").mkdir()
    (root / "private" / "writeup.md").write_text(WRITEUP, encoding="utf-8")
    created = Script(
        [
            err(404, "NOT_FOUND", "challenge not found"),
            ok({"id": "uuid", "slug": "web-xss-01", "status": "draft", "title": "XSS"}, status=201),
        ]
    )
    action, data = push_challenge(_client(created), root)
    assert action == "created"
    assert data["status"] == "draft"
    body = json.loads(created.calls[1][3])
    assert body["slug"] == "web-xss-01"
    assert body["difficulty"] == 3
    assert "status" not in body
    assert body["flag_mode"] == "dynamic"
    assert body["flag_template"] == FLAG_TEMPLATE
    assert body["requires_instance"] is True
    assert body["container_port"] == 80
    assert body["protocol"] == "http"
    assert body["docker_image"] == f"registry.example/challenges/web/web-xss-01@{DIGEST}"
    assert "writeup" not in body
    assert "flag" not in body
    assert "review_factor" not in body

    updated = Script(
        [
            ok({"id": "uuid", "slug": "web-xss-01", "review_factor": 1.2, "is_legacy": True}),
            ok({"id": "uuid", "slug": "web-xss-01", "status": "published", "title": "XSS"}),
        ]
    )
    action, data = push_challenge(_client(updated), root, difficulty=7, publish=True)
    assert action == "updated"
    assert updated.calls[1][0] == "PUT"
    body = json.loads(updated.calls[1][3])
    assert body["difficulty"] == 7
    assert body["status"] == "published"
    assert body["review_factor"] == 1.2
    assert body["is_legacy"] is True
    assert data["status"] == "published"


def test_push_mapping_fails_closed(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(root, _service_spec(), category="misc")
    with pytest.raises(ToolError, match="category"):
        challenge_body(root)

    _write_manifest(root, _service_spec())
    with pytest.raises(ToolError, match="1..10"):
        challenge_body(root, difficulty=11)

    spec = _service_spec()
    spec["flags"] = [
        {"id": "a", "type": "static", "content": "flag{a}"},
        {"id": "b", "type": "static", "content": "flag{b}"},
    ]
    _write_manifest(root, spec)
    with pytest.raises(ToolError, match="one flag"):
        challenge_body(root)

    spec = _service_spec()
    spec["flags"] = [{"id": "flag", "type": "static", "content": "flag{fixed}"}]
    _write_manifest(root, spec)
    with pytest.raises(ToolError, match="static challenge"):
        challenge_body(root)

    spec = _service_spec()
    spec["runtime"] = "static"
    spec.pop("services")
    spec.pop("spawn")
    spec["flags"] = [{"id": "flag", "type": "regex", "content": "flag\\{.*\\}"}]
    spec["files"] = [{"name": "handout.txt", "source": "build"}]
    _write_manifest(root, spec)
    with pytest.raises(ToolError, match="regex"):
        challenge_body(root)

    spec["flags"] = [{"id": "flag", "type": "static"}]
    _write_manifest(root, spec)
    with pytest.raises(ToolError, match="content"):
        challenge_body(root)

    spec = _service_spec()
    spec["services"][0]["image"] = {"name": "registry.example/challenges/web/web-xss-01:local", "digest": DIGEST}
    _write_manifest(root, spec)
    with pytest.raises(ToolError, match=":local"):
        challenge_body(root)
    with pytest.raises(ToolError, match=":local"):
        challenge_body(root, image="registry.example/challenges/web/web-xss-01:local")
    with pytest.raises(ToolError, match="registry reference"):
        challenge_body(root, image="web-xss-01")

    spec = _service_spec()
    spec["services"][0]["ports"][0]["protocol"] = "udp"
    _write_manifest(root, spec)
    with pytest.raises(ToolError, match="udp"):
        challenge_body(root)

    spec = _service_spec()
    spec["services"][0]["health"] = {"type": "tcp"}
    spec["player"] = {"entry": "web.http", "connection": "nc {host} {port}"}
    _write_manifest(root, spec)
    assert challenge_body(root)["protocol"] == "tcp"

    spec["player"] = {"entry": "web.http", "connection": "{url}"}
    _write_manifest(root, spec)
    assert challenge_body(root)["protocol"] == "http"

    (root / "private").mkdir(exist_ok=True)
    (root / "private" / "writeup.md").write_text("# Real\n", encoding="utf-8")
    spec = _service_spec()
    _write_manifest(root, spec)
    assert challenge_body(root)["writeup"] == "# Real\n"


def test_static_challenge_omits_the_image(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(
        root,
        {
            "runtime": "static",
            "flags": [{"id": "flag", "type": "static", "content": "flag{demo}"}],
            "files": [{"name": "handout.txt", "source": "build"}],
        },
        difficulty="hard",
    )
    body = challenge_body(root)
    assert body["requires_instance"] is False
    assert body["flag"] == "flag{demo}"
    assert body["flag_mode"] == "static"
    assert body["difficulty"] == 9
    assert "docker_image" not in body


def test_numeric_difficulty_is_sent_as_is(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(root, _service_spec(), difficulty=7)
    assert challenge_body(root)["difficulty"] == 7

    _write_manifest(root, _service_spec(), difficulty=1)
    assert challenge_body(root)["difficulty"] == 1


def test_numeric_difficulty_out_of_range_fails_closed(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(root, _service_spec(), difficulty=0)
    with pytest.raises(ToolError, match="1..10"):
        challenge_body(root)

    _write_manifest(root, _service_spec(), difficulty=11)
    with pytest.raises(ToolError, match="1..10"):
        challenge_body(root)


def test_difficulty_override_wins_over_the_manifest(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(root, _service_spec(), difficulty=8)
    assert challenge_body(root, difficulty=2)["difficulty"] == 2


def test_push_uses_the_public_registry_pin(tmp_path):
    root = tmp_path / "chall"
    spec = _service_spec()
    spec["services"][0]["image"] = {
        "name": "challenges/web/web-xss-01",
        "registry": "ada/challenges-web-web-xss-01",
        "digest": DIGEST,
    }
    _write_manifest(root, spec)
    assert challenge_body(root)["docker_image"] == f"ada/challenges-web-web-xss-01@{DIGEST}"
    override = f"other/challenges-web-web-xss-01@{DIGEST}"
    assert challenge_body(root, image=override)["docker_image"] == override


def test_compose_entry_image(tmp_path):
    root = tmp_path / "chall"
    spec = {
        "runtime": "container",
        "spawn": "compose",
        "compose": {"file": "compose.yaml"},
        "flags": [
            {
                "id": "flag",
                "type": "dynamic",
                "path": "/flag",
                "owner": "ctf:ctf",
                "mode": "0644",
                "service": "web",
            }
        ],
        "player": {"entry": "web.http", "connection": "{url}"},
    }
    _write_manifest(root, spec)
    (root / "compose.yaml").write_text(
        "services:\n"
        "  web:\n"
        "    image: challenges/web/web-xss-01:local\n"
        "    ports: ['80']\n"
        "    labels:\n"
        "      hg.port.http: '80'\n"
        "  db:\n"
        f"    image: registry.example/mysql@{DIGEST}\n",
        encoding="utf-8",
    )
    with pytest.raises(ToolError, match=":local"):
        challenge_body(root)
    body = challenge_body(root, image=f"registry.example/challenges/web/web-xss-01@{DIGEST}")
    assert body["docker_image"] == f"registry.example/challenges/web/web-xss-01@{DIGEST}"
    assert body["container_port"] == 80

    (root / "compose.yaml").write_text(
        "services:\n"
        "  web:\n"
        f"    image: registry.example/side@{DIGEST}\n"
        "    ports: ['80']\n"
        "    labels:\n"
        "      hg.port.http: '80'\n",
        encoding="utf-8",
    )
    assert challenge_body(root)["docker_image"] == f"registry.example/side@{DIGEST}"


def test_add_challenge_uses_metadata_value(tmp_path):
    root = tmp_path / "chall"
    _write_manifest(root, _service_spec(), value=250)
    script = Script(
        [
            ok({"id": "challenge-uuid", "slug": "web-xss-01"}),
            ok({"ok": True}),
        ]
    )
    linked = add_challenge(_client(script), "spring-2026", "web-xss-01", root=root, sort_order=2)
    assert linked == {
        "contest": "spring-2026",
        "challenge": "web-xss-01",
        "points": 250,
        "sort_order": 2,
    }
    assert script.calls[1][0] == "PUT"
    assert script.calls[1][1].endswith("/api/v1/admin/contests/spring-2026/challenges")
    assert json.loads(script.calls[1][3]) == {
        "challenge_id": "challenge-uuid",
        "points": 250,
        "sort_order": 2,
    }


def test_push_keeps_status_tags_and_writeup(tmp_path):
    root = tmp_path / "chall"
    spec = _service_spec()
    spec["topics"] = ["xss"]
    spec["hints"] = [{"content": "Look at the query.", "cost": 10}]
    _write_manifest(root, spec)
    (root / "private").mkdir()
    (root / "private" / "writeup.md").write_text(WRITEUP, encoding="utf-8")
    updated = Script(
        [
            ok({
                "id": "uuid",
                "slug": "web-xss-01",
                "status": "published",
                "tags": ["kept"],
                "writeup": "Already written.",
                "review_factor": 1.2,
                "hints": [{"id": "hint-1", "position": 1, "content": "old"}],
            }),
            ok({"id": "uuid", "slug": "web-xss-01", "status": "published"}),
            ok({"deleted": True}),
            ok({"id": "hint-2", "position": 1, "content": "Look at the query."}),
        ]
    )
    action, _data = push_challenge(_client(updated), root)
    assert action == "updated"
    body = json.loads(updated.calls[1][3])
    assert "status" not in body
    assert body["tags"] == ["xss"]
    assert body["writeup"] == "Already written."
    assert updated.calls[2][0] == "DELETE"
    assert updated.calls[2][1].endswith("/hints/hint-1")
    assert json.loads(updated.calls[3][3]) == {"position": 1, "content": "Look at the query."}


def test_add_challenge_requires_points_without_a_tree():
    script = Script([ok({"id": "challenge-uuid"}), ok({"ok": True})])
    with pytest.raises(ToolError, match="points are required"):
        add_challenge(_client(script), "acme-2026", "web-xss-01")
    linked = add_challenge(_client(script), "acme-2026", "web-xss-01", points=250)
    assert linked["points"] == 250
    assert linked["challenge"] == "web-xss-01"


def test_keyring_save_drops_the_file_copy(tmp_path):
    store = CredentialStore(tmp_path, force_file=True)
    store.save(ORIGIN, Secret("old", "old-access", time.time() + 1000))
    assert (tmp_path / "credentials.json").is_file()
    ring = FakeRing()
    store = CredentialStore(tmp_path, keyring_module=ring)
    store.save(ORIGIN, Secret("new", "new-access", time.time() + 1000))
    assert not (tmp_path / "credentials.json").exists()
    loaded = store.load(ORIGIN)
    assert loaded is not None
    assert loaded.access_token == "new-access"


def test_material_single_file_and_zip(tmp_path, monkeypatch):
    root = tmp_path / "chall"
    _write_manifest(
        root,
        {
            "runtime": "static",
            "flags": [{"id": "flag", "type": "static", "content": "flag{demo}"}],
            "files": [{"name": "handout.txt", "source": "build"}],
        },
    )
    dist = root / "dist"
    dist.mkdir()
    (dist / "handout.txt").write_text("hello", encoding="utf-8")
    script = Script(
        [
            ok({"id": "uuid", "slug": "web-xss-01"}),
            ok({"filename": "handout.txt", "size": 5, "content_type": "application/octet-stream"}),
        ]
    )
    name, size = push_material(_client(script), root)
    assert (name, size) == ("handout.txt", 5)
    assert script.calls[1][0] == "POST"
    assert script.calls[1][1].endswith("/api/v1/admin/challenges/web-xss-01/attachment")
    filename, data = _part(script.calls[1][2]["Content-Type"], script.calls[1][3])
    assert filename == "handout.txt"
    assert data == b"hello"

    _write_manifest(
        root,
        {
            "runtime": "static",
            "flags": [{"id": "flag", "type": "static", "content": "flag{demo}"}],
            "files": [
                {"name": "a.txt", "source": "build"},
                {"name": "b.txt", "source": "build"},
            ],
        },
    )
    (dist / "a.txt").write_bytes(b"A")
    (dist / "b.txt").write_bytes(b"B")
    zip_name, zip_bytes, content_type = handout_bytes(root, None)
    assert zip_name == "web-xss-01-handout.zip"
    assert content_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.read("a.txt") == b"A"
        assert archive.read("b.txt") == b"B"

    explicit = tmp_path / "bundle.zip"
    explicit.write_bytes(zip_bytes)
    filename, data, content_type = handout_bytes(root, explicit)
    assert filename == "bundle.zip"
    assert content_type == "application/zip"
    assert data == zip_bytes


def test_material_refuses_staging_missing_and_oversize(tmp_path, monkeypatch):
    root = tmp_path / "chall"
    _write_manifest(
        root,
        {
            "runtime": "static",
            "flags": [{"id": "flag", "type": "static", "content": "flag{demo}"}],
            "files": [{"name": "handout.txt", "source": "staging"}],
        },
    )
    with pytest.raises(ToolError, match="player bucket"):
        handout_bytes(root, None)

    _write_manifest(
        root,
        {
            "runtime": "static",
            "flags": [{"id": "flag", "type": "static", "content": "flag{demo}"}],
            "files": [{"name": "handout.txt", "source": "build"}],
        },
    )
    with pytest.raises(ToolError, match="missing dist/handout.txt"):
        handout_bytes(root, None)

    (root / "dist").mkdir()
    (root / "dist" / "handout.txt").write_bytes(b"12345")
    monkeypatch.setattr("hg.remote.MAX_UPLOAD_BYTES", 4)
    with pytest.raises(ToolError, match="platform limit"):
        handout_bytes(root, None)

    script = Script([err(404, "NOT_FOUND", "challenge not found")])
    monkeypatch.setattr("hg.remote.MAX_UPLOAD_BYTES", 25 << 20)
    with pytest.raises(ToolError, match="hg challenge push"):
        push_material(_client(script), root)


def test_cli_login_whoami_and_logout_over_http(tmp_path, monkeypatch, capsys):
    seen = []

    def route(method, path, headers, body):
        seen.append((method, path, headers, body))
        if path == "/api/v1/internal/auth/login":
            return 200, {
                "success": True,
                "data": {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 900,
                    "user": {"email": "staff@example", "username": "staff", "role": "admin"},
                },
            }
        if path == "/api/v1/internal/auth/refresh":
            return 200, {"success": True, "data": {"access_token": "refreshed", "expires_in": 900}}
        if path == "/api/v1/internal/me":
            token = headers.get("Authorization")
            assert token == "Bearer refreshed"
            return 200, {
                "success": True,
                "data": {"email": "staff@example", "username": "staff", "role": "admin"},
            }
        if path == "/api/v1/internal/auth/logout":
            return 500, {"success": False, "error": {"code": "INTERNAL", "message": "nope"}}
        return 404, {"success": False, "error": {"code": "NOT_FOUND", "message": path}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._respond()

        def do_POST(self):
            self._respond()

        def _respond(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            status, payload = route(self.command, self.path, dict(self.headers), body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        monkeypatch.setattr("hg.cli.getpass.getpass", lambda prompt="": "s3cret")
        main(["login", "--url", f"http://{host}:{port}", "--email", "staff@example"])
        secret = json.loads((tmp_path / "hg" / "credentials.json").read_text(encoding="utf-8"))
        account = next(iter(secret))
        secret[account]["expires_at"] = time.time() - 10
        (tmp_path / "hg" / "credentials.json").write_text(json.dumps(secret), encoding="utf-8")
        main(["whoami"])
        main(["logout"])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    out = capsys.readouterr().out
    assert "logged in as staff@example" in out
    assert "email     staff@example" in out
    assert "username  staff" in out
    assert "role      admin" in out
    assert "logged out" in out
    assert not (tmp_path / "hg" / "credentials.json").exists()
    assert seen[0][0] == "POST"
    assert seen[0][1] == "/api/v1/internal/auth/login"
    assert b"s3cret" not in (tmp_path / "hg" / "config.json").read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["whoami"])
    assert exc.value.code == 1

class _Timeout(Exception):
    """Raised by the fake urlopen so the test never waits on a network stack."""

def _capture_context(monkeypatch):
    captured = {}

    def fake_urlopen(request, *, timeout, context=None):
        captured["request"] = request
        captured["timeout"] = timeout
        captured["context"] = context
        raise urllib.error.URLError(_Timeout("offline test"))

    monkeypatch.setattr("hg.api.urllib.request.urlopen", fake_urlopen)
    return captured

def test_https_requests_verify_against_the_bundled_ca_store(monkeypatch):
    """
    A python.org interpreter on macOS has no default CA bundle, so urllib
    fails https with "unable to get local issuer certificate". The transport
    must opt into the certifi roots and load them, which is the regression
    that broke `hg login`.
    """
    captured = _capture_context(monkeypatch)

    with pytest.raises(ToolError, match="could not reach"):
        urllib_transport("POST", "https://hntr-backend.archonlabs.id/x", {}, b"{}")

    context = captured["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # Building the context reads the bundle, so a missing certifi would have
    # raised above instead of reaching this assertion.
    assert context.cert_store_stats()["x509_ca"] > 0

def test_the_tls_context_is_built_once_and_reused(monkeypatch):
    captured = _capture_context(monkeypatch)
    monkeypatch.setattr("hg.api._SSL_CONTEXT", None)

    for _ in range(3):
        with pytest.raises(ToolError):
            urllib_transport("GET", "https://api.example/me", {}, None)

    assert captured["context"] is not None
    assert captured["context"] is hg_api._tls_context()

def test_plain_http_does_not_load_a_ca_bundle(monkeypatch):
    """
    localhost is allowed over http. There is no TLS there, so the transport
    must not build a context, which also keeps the http unit tests honest.
    """
    captured = _capture_context(monkeypatch)

    with pytest.raises(ToolError, match="could not reach"):
        urllib_transport("GET", "http://127.0.0.1:9/me", {}, None)

    assert captured["context"] is None

def test_certifi_is_a_runtime_dependency():
    """
    The fix only works where certifi is installed. It is imported by
    hg.api at load time, so it must be a hard dependency of the package,
    not an optional or development extra.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(dep.startswith("certifi") for dep in deps), deps

    optional = data["project"].get("optional-dependencies", {})
    assert not any(dep.startswith("certifi") for deps in optional.values() for dep in deps), optional
