"""
    HunterGround Toolkit
    Test for the contest manifest, discovery, lint, and the platform deploy flow
"""

from pathlib        import Path
from hg.api         import Client
from hg.contest     import (
    contest_body,
    contest_root,
    create_contest_tree,
    digest_contest,
    discover_challenges,
    lint_contest,
    resolve_participation,
)
from hg.errors      import ToolError
from hg.remote      import (
    add_manifest_challenges,
    deploy_contest,
    link_challenge,
    upsert_contest,
)
from hg.scaffold    import create_challenge
import json
import pytest
import yaml


DIGEST = "sha256:" + ("ab" * 32)

## Helpers -----------------------------------------------------------------------------------------

class Script:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, method, url, headers, body):
        path = url.split("/api/v1", 1)[1]
        payload = None
        if body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = body
        self.calls.append((method, path, payload))
        status, data = self.handler(method, path, payload)
        return status, json.dumps(data).encode()

def _client(script):
    return Client("https://api.example", "token", transport=script)

def _ok(data):
    return 200, {"success": True, "data": data}

def _missing():
    return 404, {"success": False, "error": {"code": "NOT_FOUND", "message": "no"}}

def _routes(script_handler):
    """Wrap a per-path handler with the default not-found and ok responses."""
    def handler(method, path, payload):
        return script_handler(method, path, payload)
    return handler

def _manifest(
    root:           Path,
    contest_id:          str           = "spring-2026",
    *,
    kind:                str           = "open",
    participation:       str    | None = None,
    challenges:     list[dict]  | None = None,
    visibility:          str           = "draft",
    starts:              str           = "2026-04-01T00:00:00Z",
    ends:                str           = "2026-04-03T00:00:00Z",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "id":       contest_id,
        "title":    "Spring 2026",
        "description": "A weekend CTF.",
        "type":     kind,
    }
    if participation is not None:
        metadata["participation"] = participation

    spec: dict = {"startsAt": starts, "endsAt": ends, "visibility": visibility}
    if challenges is not None:
        spec["challenges"] = challenges
    (root / "contest.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion":   "hg.contest/v1",
                "kind":         "Contest",
                "metadata":     metadata,
                "spec":         spec,
            },
            sort_keys = False,
        ),
        encoding="utf-8",
    )
    return root

def _challenge(
    root:       Path,
    name:       str,
    *,
    value:      int  = 100,
    category:   str  = "web",
    digest:     bool = False,
) -> Path:
    child = root / name
    create_challenge("web-nginx", name, child)
    manifest = yaml.safe_load((child / "challenge.yaml").read_text(encoding="utf-8"))
    manifest["metadata"]["value"]    = value
    manifest["metadata"]["category"] = category
    # A built member carries build.fromDigest, not image.digest, so lint stays
    # happy. The push tests need a registry ref, so they ask for the digest.
    if digest:
        del manifest["spec"]["services"][0]["build"]
        manifest["spec"]["services"][0]["image"]["digest"] = DIGEST
    (child / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return child

## Scaffold ----------------------------------------------------------------------------------------

def test_new_writes_a_contest_tree(tmp_path):
    dest = create_contest_tree(
        "spring-2026",
        tmp_path / "spring-2026",
        title         = "Spring 2026",
        kind          = "public-competition",
        starts_at     = "2026-04-01T00:00:00Z",
        ends_at       = "2026-04-03T00:00:00Z",
        participation = "team",
    )
    manifest = yaml.safe_load((dest / "contest.yaml").read_text(encoding="utf-8"))
    assert manifest["apiVersion"] == "hg.contest/v1"
    assert manifest["kind"] == "Contest"
    assert manifest["metadata"]["type"] == "public-competition"
    assert manifest["metadata"]["participation"] == "team"
    assert manifest["spec"]["visibility"] == "draft"
    assert (dest / ".gitignore").is_file()

def test_new_omits_participation_when_not_given(tmp_path):
    dest = create_contest_tree(
        "spring-2026",
        tmp_path / "spring-2026",
        kind      = "open",
        starts_at = "2026-04-01T00:00:00Z",
        ends_at   = "2026-04-03T00:00:00Z",
    )
    manifest = yaml.safe_load((dest / "contest.yaml").read_text(encoding="utf-8"))
    assert "participation" not in manifest["metadata"]

def test_new_refuses_team_on_open(tmp_path):
    with pytest.raises(ToolError, match="does not allow"):
        create_contest_tree(
            "spring-2026",
            tmp_path / "spring-2026",
            kind          = "open",
            starts_at     = "2026-04-01T00:00:00Z",
            ends_at       = "2026-04-03T00:00:00Z",
            participation = "team",
        )

def test_new_refuses_a_bad_id(tmp_path):
    with pytest.raises(ToolError, match="id must match"):
        create_contest_tree(
            "Spring-2026",
            tmp_path / "out",
            kind      = "open",
            starts_at = "2026-04-01T00:00:00Z",
            ends_at   = "2026-04-03T00:00:00Z",
        )

def test_new_refuses_a_reversed_window(tmp_path):
    with pytest.raises(ToolError, match="endsAt"):
        create_contest_tree(
            "spring-2026",
            tmp_path / "out",
            kind      = "open",
            starts_at = "2026-04-03T00:00:00Z",
            ends_at   = "2026-04-01T00:00:00Z",
        )

def test_new_refuses_a_non_empty_dest(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "already").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError, match="not empty"):
        create_contest_tree(
            "spring-2026",
            dest,
            kind      = "open",
            starts_at = "2026-04-01T00:00:00Z",
            ends_at   = "2026-04-03T00:00:00Z",
        )

## Discovery ---------------------------------------------------------------------------------------

def test_discovery_is_name_sorted_and_uses_value(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01", value=250)
    _challenge(root, "crypto-rsa-01", value=150, category="crypto")

    entries = discover_challenges(root)
    assert [entry.path for entry in entries] == ["crypto-rsa-01", "web-xss-01"]
    assert [entry.points for entry in entries] == [150, 250]
    assert [entry.sort_order for entry in entries] == [0, 1]
    assert all(entry.state == "hidden" for entry in entries)

def test_listed_entries_lead_and_override(tmp_path):
    root = _manifest(
        tmp_path / "spring-2026",
        challenges=[
            {"path": "web-xss-01", "points": 500, "sortOrder": 7, "state": "visible"},
        ],
    )
    _challenge(root, "crypto-rsa-01", value=150, category="crypto")
    _challenge(root, "web-xss-01", value=250)

    entries = discover_challenges(root)
    assert [entry.path for entry in entries] == ["web-xss-01", "crypto-rsa-01"]

    first = entries[0]
    assert (first.points, first.sort_order, first.state) == (500, 7, "visible")

    second = entries[1]
    assert (second.points, second.sort_order) == (150, 1)

def test_discovery_rejects_a_missing_path(tmp_path):
    root = _manifest(tmp_path / "spring-2026", challenges=[{"path": "nope"}])
    _challenge(root, "web-xss-01")
    with pytest.raises(ToolError, match="no challenge.yaml"):
        discover_challenges(root)

def test_discovery_rejects_a_duplicate_path(tmp_path):
    root = _manifest(
        tmp_path / "spring-2026",
        challenges=[{"path": "web-xss-01"}, {"path": "web-xss-01"}],
    )
    _challenge(root, "web-xss-01")
    with pytest.raises(ToolError, match="more than once"):
        discover_challenges(root)

def test_discovery_ignores_directories_without_a_manifest(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01")
    (root / "notes").mkdir()
    (root / "notes" / "todo.md").write_text("later\n", encoding="utf-8")
    assert [entry.path for entry in discover_challenges(root)] == ["web-xss-01"]

## Type table --------------------------------------------------------------------------------------

def test_type_table_expands_kind_mode_and_participation(tmp_path):
    root = _manifest(tmp_path / "spring-2026", kind="mini-open")
    _challenge(root, "web-xss-01")
    body = contest_body(yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8")))
    assert body["kind"] == "mini_open"
    assert body["mode"] == "jeopardy"
    assert body["participation"] == "individual"

def test_competition_defaults_to_individual(tmp_path):
    root = _manifest(tmp_path / "spring-2026", kind="public-competition")
    _challenge(root, "web-xss-01")
    body = contest_body(yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8")))
    assert body["participation"] == "individual"

def test_competition_accepts_team(tmp_path):
    root = _manifest(tmp_path / "spring-2026", kind="private-competition", participation="team")
    _challenge(root, "web-xss-01")
    body = contest_body(yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8")))
    assert body["participation"] == "team"

def test_hiring_test_accepts_team(tmp_path):
    root = _manifest(tmp_path / "spring-2026", kind="hiring-test", participation="team")
    _challenge(root, "web-xss-01")
    body = contest_body(yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8")))
    assert body["kind"] == "hiring_test"
    assert body["participation"] == "team"

def test_open_rejects_team(tmp_path):
    root = _manifest(tmp_path / "spring-2026", kind="open", participation="team")
    with pytest.raises(ToolError, match="does not allow"):
        resolve_participation(yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8")))

def test_body_publishes_when_visibility_is_published(tmp_path):
    root = _manifest(tmp_path / "spring-2026", visibility="published")
    _challenge(root, "web-xss-01")
    body = contest_body(yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8")))
    assert body["published"] is True

## Lint and digest ---------------------------------------------------------------------------------

def test_lint_passes_for_a_valid_contest(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01")
    assert lint_contest(root) == []

def test_lint_strict_requires_real_child_content(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01")
    errors = lint_contest(root, strict=True)
    # Strict lint runs on each child, so the scaffold stubs are reported.
    assert any("writeup.md" in message for message in errors)

def test_lint_reports_unknown_keys(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01")
    data = yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8"))
    data["metadata"]["typo"] = "x"
    (root / "contest.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    errors = lint_contest(root)
    assert any("typo" in message for message in errors)

def test_lint_surfaces_a_child_challenge_error(tmp_path):
    root  = _manifest(tmp_path / "spring-2026")
    child = _challenge(root, "web-xss-01")
    manifest = yaml.safe_load((child / "challenge.yaml").read_text(encoding="utf-8"))
    manifest["spec"]["flags"][0]["content"] = "hg{literal}"
    (child / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    errors = lint_contest(root)
    assert any(message.startswith("web-xss-01:") for message in errors)

def test_lint_requires_at_least_one_challenge(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    errors = lint_contest(root)
    assert any("no challenge directories" in message for message in errors)

def test_digest_is_stable_and_changes_with_content(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01")
    first = digest_contest(root)
    assert first == digest_contest(root)

    data = yaml.safe_load((root / "contest.yaml").read_text(encoding="utf-8"))
    data["metadata"]["title"] = "Renamed"
    (root / "contest.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert digest_contest(root) != first

def test_contest_root_defaults_to_cwd(tmp_path, monkeypatch):
    root = _manifest(tmp_path / "spring-2026")
    monkeypatch.chdir(root)
    assert contest_root(None) == root.resolve()

## Remote flow -------------------------------------------------------------------------------------

def test_upsert_contest_creates_when_missing(tmp_path):
    script = Script(lambda method, path, payload: _missing() if method == "GET" else _ok({"slug": "spring-2026"}))
    action, data = upsert_contest(_client(script), {"slug": "spring-2026", "title": "Spring"})
    assert action == "created"
    assert data["slug"] == "spring-2026"
    assert script.calls[0][0] == "GET"
    assert script.calls[1][0] == "POST"
    assert script.calls[1][1] == "/admin/contests"

def test_upsert_contest_updates_when_present(tmp_path):
    script = Script(lambda method, path, payload: _ok({
        "slug": "spring-2026",
        "freeze_minutes": 30,
        "register_by": "2026-03-28T00:00:00Z",
        "requires_approval": True,
        "team_size_max": 2,
        "invite_ttl_minutes": 120,
        "allow_late_join": True,
    }))
    action, _data = upsert_contest(
        _client(script),
        {"slug": "spring-2026", "title": "Spring", "published": False},
    )
    assert action == "updated"
    assert script.calls[0][1] == "/contests/spring-2026"
    assert script.calls[1][0] == "PUT"
    assert script.calls[1][1] == "/admin/contests/spring-2026"
    assert script.calls[1][2]["freeze_minutes"] == 30
    assert script.calls[1][2]["register_by"] == "2026-03-28T00:00:00Z"
    assert script.calls[1][2]["requires_approval"] is True
    assert script.calls[1][2]["team_size_max"] == 2
    assert script.calls[1][2]["invite_ttl_minutes"] == 120
    assert script.calls[1][2]["allow_late_join"] is True
    assert script.calls[1][2]["title"] == "Spring"

def test_link_challenge_resolves_slug_then_links():
    def handler(method, path, payload):
        if method == "GET":
            return _ok({"id": "challenge-uuid"})
        return _ok({})
    script = Script(handler)
    linked = link_challenge(_client(script), "spring-2026", "web-xss-01", points=250, sort_order=2)
    assert linked == {
        "contest": "spring-2026",
        "challenge": "web-xss-01",
        "points": 250,
        "sort_order": 2,
    }
    assert script.calls[0][1] == "/admin/challenges/web-xss-01"
    assert script.calls[1][0] == "PUT"
    assert script.calls[1][1] == "/admin/contests/spring-2026/challenges"
    assert script.calls[1][2] == {
        "challenge_id": "challenge-uuid",
        "points": 250,
        "sort_order": 2,
    }

def test_link_challenge_needs_the_challenge_on_the_platform():
    script = Script(lambda method, path, payload: _missing())
    with pytest.raises(ToolError, match="not on the platform"):
        link_challenge(_client(script), "spring-2026", "web-xss-01", points=100)

def test_deploy_creates_contest_links_challenges_and_publishes(tmp_path):
    root = _manifest(tmp_path / "spring-2026", visibility="published")
    _challenge(root, "web-xss-01", value=250, digest=True)
    _challenge(root, "pwn-01", value=400, category="pwn", digest=True)
    handout = root / "web-xss-01" / "dist" / "note.txt"
    handout.parent.mkdir()
    handout.write_bytes(b"notes")
    manifest = yaml.safe_load((root / "web-xss-01" / "challenge.yaml").read_text(encoding="utf-8"))
    manifest["spec"]["files"] = [{"name": "note.txt", "source": "build"}]
    (root / "web-xss-01" / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    def handler(method, path, payload):
        if method == "GET" and path.startswith("/contests/"):
            return _missing()
        if method == "GET" and path.startswith("/admin/challenges/"):
            return _ok({"id": "id-" + path.rsplit("/", 1)[-1], "status": "draft"})
        return _ok({"slug": "spring-2026", "filename": "note.txt"})
    script = Script(handler)
    result = deploy_contest(_client(script), root)

    assert result["action"] == "created"
    assert result["published"] is True
    assert [item["challenge"] for item in result["linked"]] == ["pwn-01", "web-xss-01"]
    uploaded = [item for item in result["linked"] if item.get("handout")]
    assert uploaded[0]["handout"] == "note.txt"

    paths = [(method, path) for method, path, _payload in script.calls]
    assert ("POST", "/admin/contests") in paths
    assert ("POST", "/admin/contests/spring-2026/publish") in paths
    assert ("POST", "/admin/challenges/web-xss-01/attachment") in paths
    challenge_writes = [
        payload for method, path, payload in script.calls
        if method in ("POST", "PUT")
        and path.startswith("/admin/challenges/")
        and isinstance(payload, dict)
        and "slug" in payload
    ]
    assert challenge_writes
    assert all("status" not in payload for payload in challenge_writes)
    links = [payload for method, path, payload in script.calls if method == "PUT" and path.endswith("/challenges")]
    assert links == [
        {"challenge_id": "id-pwn-01", "points": 400, "sort_order": 0},
        {"challenge_id": "id-web-xss-01", "points": 250, "sort_order": 1},
    ]

def test_deploy_names_the_challenge_that_cannot_be_pushed(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    child = _challenge(root, "web-xss-01", digest=True)
    manifest = yaml.safe_load((child / "challenge.yaml").read_text(encoding="utf-8"))
    del manifest["spec"]["services"][0]["image"]["digest"]
    (child / "challenge.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    script = Script(lambda method, path, payload: _missing() if method == "GET" else _ok({"slug": "spring-2026"}))
    with pytest.raises(ToolError, match=r"web-xss-01: "):
        deploy_contest(_client(script), root)

def test_add_manifest_challenges_links_every_entry(tmp_path):
    root = _manifest(tmp_path / "spring-2026")
    _challenge(root, "web-xss-01", value=250, digest=True)
    _challenge(root, "crypto-rsa-01", value=150, category="crypto", digest=True)
    script = Script(lambda method, path, payload: _ok({"id": "id-" + path.rsplit("/", 1)[-1]}))
    linked = add_manifest_challenges(_client(script), "spring-2026", root)
    assert [item["challenge"] for item in linked] == ["crypto-rsa-01", "web-xss-01"]
